"""Pin the C tile-walk SEED rule.

Per (state, ordinal, ctx) in the dense capture, group tiles by their
(A, B) slope words (one plane per general child).  For each group:
  1. recover the EXACT wide walk accumulator by intersecting RNE24
     preimage intervals under step = RTZ(slope_word * 32) on the
     2^(e-27) grid of the C binade e (banked walk law);
  2. compute my banked-law direct chain (28-bit wide value) per tile;
  3. report the tiles where chain == accumulator exactly, plus the
     anchor tile, bounds, and diff profile -> the seed rule.
"""
import sys, json
sys.path[:0] = ["/tmp/walle", "/tmp/walle/analysis"]
from pathlib import Path
from fractions import Fraction
from collections import defaultdict
import numpy as np
import _sweep_fused_join_lattice as m
import _solve_clip_varyings as sv
import _fit_child_tiles as ft
import _joint_stage_sweep as js

F2 = Fraction(2)

def f32v(w): return m.bits_f32(w)

def frac_of(w):
    if w == 0 or (w & 0x7fffffff) == 0: return Fraction(0)
    s, mant, e = m.f32_parts(w)
    return Fraction(s * mant) * F2**e

def sel_oa(mand, me, sel, se):
    prod = mand * sel; bits = prod.bit_length(); shift = bits - 27
    if bits <= 32:
        if shift <= 0: return prod, me + se
        idx = (prod + (1 << (shift - 1))) >> shift
        if idx.bit_length() > 27: idx >>= 1; shift += 1
        return idx, me + se + shift
    T = max(0, mand.bit_length() - 8)
    return (js.pps(mand, sel, T) + (20 << T)) >> shift, me + se + shift

def tap_rne(mag, gm, W):
    sh = mag.bit_length() - W
    if sh > 0:
        low = mag & ((1 << sh) - 1); half = 1 << (sh - 1)
        base = mag >> sh
        up = low > half or (low == half and base & 1)
        mag = base + (1 if up else 0)
        gm += sh
        if mag.bit_length() > W: mag >>= 1; gm += 1
    return mag, gm

def build28(verts, vwords):
    fixed = [(round(f32v(v[0]) * 256), round(f32v(v[1]) * 256)) for v in verts]
    det = ((fixed[1][0] - fixed[0][0]) * (fixed[2][1] - fixed[0][1])
           - (fixed[1][1] - fixed[0][1]) * (fixed[2][0] - fixed[0][0]))
    anchor = min(range(3), key=lambda i: (fixed[i][1], fixed[i][0]))
    sel, se = sv.selector_for(abs(det))
    det_sign = -1 if det < 0 else 1
    n28 = []
    for axis in range(2):
        parts = []
        for v in range(3):
            if v == anchor: continue
            a2, b2 = (v + 1) % 3, (v + 2) % 3
            if axis == 0: e = fixed[a2][1] - fixed[b2][1]
            else: e = fixed[b2][0] - fixed[a2][0]
            delta = float(np.float32(np.float32(f32v(vwords[v]))
                                     - np.float32(f32v(vwords[anchor]))))
            edge = float(np.float32(e / 256.0))
            if delta == 0.0 or edge == 0.0: continue
            _, md, ed = m.f32_parts(m.f32_bits(abs(delta)))
            _, mev, ee = m.f32_parts(m.f32_bits(abs(edge)))
            sign = -1 if (delta < 0) != (edge < 0) else 1
            prod = md * mev; bits = prod.bit_length(); shift = bits - 27
            if bits <= 32: t, g = prod, ed + ee
            else: t, g = (js.pps(md, mev, 16) + (15 << 16)) >> shift, ed + ee + shift
            parts.append((sign * t, g))
        if not parts:
            n28.append((0, 0, 0)); continue
        gmin = min(g for _, g in parts)
        tot = sum(v << (g - gmin) for v, g in parts)
        s = 1 if tot > 0 else (-1 if tot < 0 else 0)
        m28, g28 = tap_rne(abs(tot), gmin, 28)
        n28.append((s, m28, g28))
    return n28, sel, se, det_sign, anchor, fixed

def chain_wide(n28, sel, se, ds, an, fx, vwords, tiles):
    """Direct chain per tile -> dict tile -> (wide28 Fraction, word)."""
    out = {}
    asign, amant, aexp = m.f32_parts(vwords[an])
    for (tx, ty) in tiles:
        parts = []
        for axis, tile in ((0, tx), (1, ty)):
            s, mag, g = n28[axis]
            disp = tile * 32 * 256 - fx[an][axis]
            if s == 0 or disp == 0: continue
            didx = abs(disp); de = -8
            while didx.bit_length() < 24: didx <<= 1; de -= 1
            while didx.bit_length() > 24: didx >>= 1; de += 1
            prod = mag * didx; bits = prod.bit_length(); shift = bits - 27
            if bits <= 32:
                if shift <= 0: t2, g2 = prod, g + de
                else:
                    t2 = (prod + (1 << (shift - 1))) >> shift
                    if t2.bit_length() > 27: t2 >>= 1; shift += 1
                    g2 = g + de + shift
            else:
                Tc = max(0, mag.bit_length() - 8)
                c = js.carry_top(mag, didx, Tc)
                t2, g2 = (js.pps(mag, didx, Tc) + ((c + 10) << Tc)) >> shift, g + de + shift
            parts.append((s * (1 if disp > 0 else -1) * t2, g2))
        if not parts:
            value = (asign * amant, aexp)
        else:
            gmin = min(g2 for _, g2 in parts)
            raw = sum(v << (g2 - gmin) for v, g2 in parts)
            jsign, jidx, je = ft.norm(raw, gmin, 28, "rne")
            if jsign == 0: value = (asign * amant, aexp)
            else:
                cidx, ce = sel_oa(jidx, je, sel, se)
                csign = jsign * ds
                mine = min(aexp, ce)
                value = ((asign * amant << (aexp - mine)) + (csign * cidx << (ce - mine)),
                         mine)
        s28, m28, e28 = ft.norm(value[0], value[1], 28, "rne")
        wide = Fraction(s28 * m28) * F2**e28
        s24, m24, e24 = ft.norm(s28 * m28, e28, 24, "rne")
        word = 0 if s24 == 0 else m.dyadic_to_f32(s24, m24, e24)
        out[(tx, ty)] = (wide, word)
    return out

def rne24_word_frac(v):
    if v == 0: return 0
    neg = v < 0; av = abs(v); e = 0
    while av >= 2: av /= 2; e += 1
    while av < 1: av *= 2; e -= 1
    scaled = av * (1 << 23)
    mant = scaled.numerator // scaled.denominator
    fr = scaled - mant
    if fr > Fraction(1, 2) or (fr == Fraction(1, 2) and mant & 1): mant += 1
    if mant >= 1 << 24: mant //= 2; e += 1
    return ((e + 127) << 23) | (mant & 0x7fffff) | (0x80000000 if neg else 0)

def load_sdf(state):
    out = {}
    for line in Path("/tmp/walle/build/_childgeo_all_residual_states.txt").read_text().splitlines():
        t = line.split()
        if t[0] != "CHILDSDF" or int(t[1]) != state: continue
        out[int(t[2])] = [[int(x, 16) for x in t[3 + 4 * v:7 + 4 * v]] for v in range(3)]
    return out

# ---- load dense capture, group tiles by (A,B) words -------------------
D = Path("/tmp/walle/build/analysis-agx-basis/residual-children-dense-plan-v1")
PLAN = json.load(open(D / "reveal-agx-setup-accumulator-plan.json"))
T = m.load_records(D / "capture.raw", len(PLAN["draws"]))
groups = defaultdict(dict)   # (state, ord, ctx, A, B) -> {(tx,ty): Cword}
for exp, draw in zip(PLAN["experiments"], PLAN["draws"]):
    key = (exp["state"], exp["drawOrdinal"])
    r = exp["recordIndex"]
    for ctx in range(2):
        A, B, C = (int(T[r][ctx][i]) for i in range(3))
        groups[key + (ctx, A, B)][(draw["tileX"], draw["tileY"])] = C

sdf_cache = {}
report = []
for (st, ordn, ctx, A, B), tiles in sorted(groups.items()):
    if len(tiles) < 12: continue
    # C binade: majority exponent
    exps = defaultdict(int)
    for w in tiles.values():
        if w & 0x7fffffff: exps[m.f32_parts(w)[2] + 23] += 1
    if not exps: continue
    e = max(exps, key=exps.get)          # value in [2^e, 2^(e+1))
    grid = F2**(e - 27)
    Af, Bf = frac_of(A), frac_of(B)
    def rtz_units(v):
        q = (v * 32) / grid
        n = abs(q.numerator) // q.denominator
        return -n if q < 0 else n
    sx_u, sy_u = rtz_units(Af), rtz_units(Bf)
    tx0, ty0 = min(tiles)[0], min(t[1] for t in tiles)
    # intersect RNE24 preimage intervals for acc0 (in grid units)
    lo, hi = None, None
    usable = 0
    for (tx, ty), w in tiles.items():
        if not (w & 0x7fffffff): continue
        v = frac_of(w)
        ew = m.f32_parts(w)[2] + 23
        ulp = F2**(ew - 23)
        k = (tx - tx0) * sx_u + (ty - ty0) * sy_u
        a = (v - ulp / 2) / grid - k
        b = (v + ulp / 2) / grid - k
        lo = a if lo is None else max(lo, a)
        hi = b if hi is None else min(hi, b)
        usable += 1
    if lo is None or lo > hi:
        report.append((st, ordn, ctx, A, len(tiles), "NOFIT", None)); continue
    from math import ceil, floor
    cands = range(-(-lo.numerator // lo.denominator) if lo > 0 else lo.numerator // lo.denominator,
                  (hi.numerator // hi.denominator) + 1)
    cands = [n for n in cands if lo <= n <= hi]
    best = None
    for n0 in cands:
        ok = sum(1 for (tx, ty), w in tiles.items()
                 if rne24_word_frac((n0 + (tx - tx0) * sx_u + (ty - ty0) * sy_u) * grid) == w)
        if best is None or ok > best[1]: best = (n0, ok)
    if best is None or best[1] != len(tiles):
        report.append((st, ordn, ctx, A, len(tiles),
                       f"PROG {best[1] if best else 0}/{len(tiles)}", None))
        continue
    n0 = best[0]
    # my chain
    if st not in sdf_cache: sdf_cache[st] = load_sdf(st)
    verts = sdf_cache[st].get(ordn)
    if verts is None:
        report.append((st, ordn, ctx, A, len(tiles), "NOSDF", None)); continue
    vwords = [verts[v][2 + ctx] for v in range(3)]
    n28, sel, se, ds, an, fx = build28(verts, vwords)
    ch = chain_wide(n28, sel, se, ds, an, fx, vwords, sorted(tiles))
    # check slopes match this group's A word (else wrong child geometry)
    zero_tiles = []
    diffs = {}
    for t in sorted(tiles):
        acc = (n0 + (t[0] - tx0) * sx_u + (t[1] - ty0) * sy_u) * grid
        dv = (ch[t][0] - acc) / grid
        diffs[t] = float(dv)
        if dv == 0: zero_tiles.append(t)
    atile = (fx[an][0] // (32 * 256), fx[an][1] // (32 * 256))
    txs = sorted(set(t[0] for t in tiles)); tys = sorted(set(t[1] for t in tiles))
    print(f"s{st} o{ordn} ctx{ctx} A={A:08x} tiles={len(tiles)} "
          f"binade=2^{e} sx_u={sx_u} sy_u={sy_u} acc0={n0} "
          f"txr=[{txs[0]},{txs[-1]}] tyr=[{tys[0]},{tys[-1]}] anchor_tile={atile}")
    print(f"   zero_tiles={zero_tiles[:12]}{'...' if len(zero_tiles) > 12 else ''} "
          f"n_zero={len(zero_tiles)}")
    row = [t for t in sorted(tiles) if t[1] == tys[len(tys)//2]]
    print("   diff mid-row:", " ".join(f"{t[0]}:{diffs[t]:+.2f}" for t in row[:16]))
