"""Fit the incremental C tile-walk model against the dense capture.

Model: C(seed) = hw-matching direct value; steps quantized once:
  sx = Q(A * 32), sy = Q(B * 32) at width Wq (RNE);
walk column-major from seed: C(tx, ty) = Q24(seed + (tx-sx0)*sx + (ty-sy0)*sy)
with the ACCUMULATION itself quantized at width Wa per step (the drift
source).  Sweep (Wq, Wa, seed strategy, walk order) x children.
"""
import sys, json
sys.path[:0] = ["/tmp/walle"]
from pathlib import Path
from fractions import Fraction
from collections import defaultdict, Counter
import _sweep_fused_join_lattice as m
def frac_of(w):
    if w == 0 or (w & 0x7fffffff) == 0: return Fraction(0)
    s, mant, e = m.f32_parts(w)
    return Fraction(s*mant) * Fraction(2)**e
def quantW(v, W, mode="rne"):
    if v == 0 or W is None: return v
    if v < 0: return -quantW(-v, W, mode)
    e = v.numerator.bit_length() - v.denominator.bit_length()
    if v < Fraction(2)**e: e -= 1
    step = Fraction(2)**(e - W + 1)
    q = v / step
    fl = q.numerator // q.denominator; fr = q - fl
    if mode == "rtz": mant = fl
    else: mant = fl + (1 if (fr > Fraction(1,2) or (fr == Fraction(1,2) and fl % 2)) else 0)
    return mant * step
def rne24_word(v):
    if v == 0: return 0
    neg = v < 0; av = abs(v); e = 0
    while av >= 2: av /= 2; e += 1
    while av < 1: av *= 2; e -= 1
    scaled = av * (1 << 23)
    mant = int(scaled); fr = scaled - mant
    if fr > Fraction(1,2) or (fr == Fraction(1,2) and mant & 1): mant += 1
    if mant >= 1 << 24: mant //= 2; e += 1
    return ((e+127) << 23) | (mant & 0x7fffff) | (0x80000000 if neg else 0)

D = Path("/tmp/walle/build/analysis-agx-basis/residual-children-dense-plan-v1")
PLAN = json.load(open(D / "reveal-agx-setup-accumulator-plan.json"))
T = m.load_records(D / "capture.raw", len(PLAN["draws"]))
hw = {}
AB = {}
for exp, draw in zip(PLAN["experiments"], PLAN["draws"]):
    key = (exp["state"], exp["drawOrdinal"])
    r = exp["recordIndex"]
    if key not in AB:
        AB[key] = [(int(T[r][c][0]), int(T[r][c][1])) for c in range(2)]
    for c in range(2):
        hw.setdefault(key + (c,), {})[(draw["tileX"], draw["tileY"])] = int(T[r][c][2])

# focus child: s40 o2 ch0
for key in [(40, 2, 0), (40, 2, 1), (58, 4, 0)]:
    tiles = hw[key]
    A, B = AB[(key[0], key[1])][key[2]]
    Af, Bf = frac_of(A), frac_of(B)
    txs = sorted(set(t[0] for t in tiles)); tys = sorted(set(t[1] for t in tiles))
    best = None
    seeds = set()
    for sx0 in (txs[0], txs[-1], txs[len(txs)//2]):
        for sy0 in (tys[0], tys[-1], tys[len(tys)//2]):
            cand = [t for t in tiles if t[1] == sy0]
            if not cand: continue
            seeds.add(min(cand, key=lambda t: abs(t[0]-sx0)))
    for seed in seeds:
        seedv = frac_of(tiles[seed])
        for order in ("yx", "xy"):
            for Wq in (24, 26, 28, None):
                sx = quantW(Af * 32, Wq); sy = quantW(Bf * 32, Wq)
                for Wa in (24, 26, 28, None):
                    ok = 0
                    for (tx, ty), hwC in tiles.items():
                        v = seedv
                        moves = [("y", ty), ("x", tx)] if order == "yx" else [("x", tx), ("y", ty)]
                        for ax, tgt in moves:
                            cur = seed[1] if ax == "y" else seed[0]
                            st = sy if ax == "y" else sx
                            d = 1 if tgt >= cur else -1
                            for _ in range(abs(tgt - cur)):
                                v = quantW(v + d*st, Wa)
                        if rne24_word(v) == hwC: ok += 1
                    if best is None or ok > best[0]:
                        best = (ok, seed, order, Wq, Wa)
    print(f"{key}: best {best[0]}/{len(tiles)} seed={best[1]} order={best[2]} Wq={best[3]} Wa={best[4]}")
