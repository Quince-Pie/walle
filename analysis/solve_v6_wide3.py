"""v6 ruler: per-row t windows under the fused chain + max-coverage merge."""
import json, pickle, sys
sys.path[:0] = ["/tmp/walle"]
from collections import defaultdict
from fractions import Fraction
from pathlib import Path
import numpy as np
import _sweep_fused_join_lattice as m
import _solve_clip_varyings as sv
import _joint_stage_sweep as js

LIMIT = int(sys.argv[1]) if len(sys.argv) > 1 else 10**9
D = Path("/tmp/walle/build/analysis-agx-basis/single-clip-ruler-v6-plan-v1")
PLAN = json.loads((D / "reveal-agx-setup-accumulator-plan.json").read_text())
TRIPLES = m.load_records(D / "capture.raw", len(PLAN["draws"]))
f32 = lambda x: float(np.float32(x))

def rne_int(v, sh):
    if sh <= 0: return v
    q = v >> sh; r = v - (q << sh); h = 1 << (sh-1)
    return q + (1 if (r > h or (r == h and q & 1)) else 0)
def tap_rne(mag, gm, W):
    sh = mag.bit_length() - W
    if sh > 0:
        low = mag & ((1 << sh) - 1); half = 1 << (sh - 1)
        base = mag >> sh
        up = low > half or (low == half and base & 1)
        mag = base + (1 if up else 0); gm += sh
        if mag.bit_length() > W: mag >>= 1; gm += 1
    return mag, gm
def sel_oa(mand, me, sl, sle):
    prod = mand*sl; bits = prod.bit_length(); shift = bits-27
    if bits <= 32:
        if shift <= 0: return prod, me+sle
        idx = (prod + (1 << (shift-1))) >> shift
        if idx.bit_length() > 27: idx >>= 1; shift += 1
        return idx, me+sle+shift
    Tc = max(0, mand.bit_length() - 8)
    return (js.pps(mand, sl, Tc) + (20 << Tc)) >> shift, me+sle+shift
def q27_halfup_frac(v):
    if v == 0: return (0, 0)
    e = v.numerator.bit_length() - v.denominator.bit_length()
    if v < Fraction(2)**e: e -= 1
    step = Fraction(2)**(e - 26)
    q = v / step
    fl = q.numerator // q.denominator
    fr = q - fl
    mant = fl + (1 if fr >= Fraction(1,2) else 0)
    if mant >= 1 << 27: mant >>= 1; e += 1
    return (mant, e - 26)

def fused_AB(fx, vals_wide, sel, se, ds):
    anchor = min(range(3), key=lambda i: (fx[i][1], fx[i][0]))
    out = []
    for axis in range(2):
        parts = []
        for v in range(3):
            if v == anchor: continue
            a2, b2 = (v+1)%3, (v+2)%3
            e_int = fx[a2][1]-fx[b2][1] if axis == 0 else fx[b2][0]-fx[a2][0]
            delta = vals_wide[v] - vals_wide[anchor]
            edge = Fraction(f32(e_int/256.0))
            if delta == 0 or edge == 0: continue
            mant, ex = q27_halfup_frac(abs(delta * edge))
            sign = -1 if (delta < 0) != (edge < 0) else 1
            parts.append((sign*mant, ex))
        if not parts:
            out.append(0); continue
        gmin = min(g for _, g in parts)
        tot = sum(v << (g-gmin) for v, g in parts)
        if tot == 0: out.append(0); continue
        s = 1 if tot > 0 else -1
        mag, g28 = tap_rne(abs(tot), gmin, 28)
        idx, e2 = sel_oa(mag, g28, sel, se)
        sh = idx.bit_length()-24
        if sh > 0:
            idx = rne_int(idx, sh); e2 += sh
            if idx.bit_length() > 24: idx >>= 1; e2 += 1
        try: out.append(m.dyadic_to_f32(s*ds, idx, e2))
        except Exception: out.append(None)
    return tuple(out)

groups = defaultdict(list)
for exp, draw in zip(PLAN["experiments"], PLAN["draws"]):
    groups[(exp["geometryIndex"], exp["inputOrdinal"])].append((exp, draw))
V1 = PLAN["v1Value"]
OUT = {}
done = 0
for (g, quad), recs in sorted(groups.items()):
    if quad != 0: continue
    if done >= LIMIT: break
    exp0 = recs[0][0]
    (v0, v1, v2) = [tuple(v) for v in exp0["geometry"]]
    t_exact = (Fraction(-512) - Fraction(v2[1])) / (Fraction(v0[1]) - Fraction(v2[1]))
    qx = Fraction(v2[0]) + t_exact * (Fraction(v0[0]) - Fraction(v2[0]))
    tri_pos = [(f32(qx), -512.0), v1, v2]
    fx = [(int(np.floor(np.float32(p[0]) * 256.0 + 0.5)),
           int(np.floor(np.float32(p[1]) * 256.0 + 0.5))) for p in tri_pos]
    det = ((fx[1][0]-fx[0][0])*(fx[2][1]-fx[0][1])
           - (fx[1][1]-fx[0][1])*(fx[2][0]-fx[0][0]))
    if det == 0: continue
    sel, se = sv.selector_for(abs(det))
    ds = -1 if det < 0 else 1
    span = t_exact * Fraction(1, 1 << 21)
    ivals = []
    for ch, (ps, pe) in enumerate(exp0["pairs"]):
        hw = None
        for (exp, draw) in recs:
            r = exp["recordIndex"]
            hw = (int(TRIPLES[r][ch][0]), int(TRIPLES[r][ch][1]))
            break
        if hw is None: continue
        e_val = Fraction(f32(pe))
        base_vals = [None, Fraction(f32(V1)), Fraction(f32(ps))]
        def words_at(t):
            return fused_AB(fx, [t * e_val, base_vals[1], base_vals[2]],
                            sel, se, ds)
        # scan for an in-window point
        hit = None
        N = 96
        for k in range(-N, N+1):
            t = t_exact + span * k / N
            if words_at(t) == hw:
                hit = t; break
        if hit is None: continue
        a, b = hit - 2*span, hit
        for _ in range(60):
            mid = (a+b)/2
            if words_at(mid) == hw: b = mid
            else: a = mid
        lo = b
        a, b = hit, hit + 2*span
        for _ in range(60):
            mid = (a+b)/2
            if words_at(mid) == hw: a = mid
            else: b = mid
        hi = a
        ivals.append((lo, hi))
    # max coverage interval
    events = []
    for lo, hi in ivals:
        events.append((lo, 0)); events.append((hi, 1))
    events.sort()
    bestn = -1; n = 0; curlo = None; bestlo = besthi = None
    for x, kind in events:
        if kind == 0:
            n += 1
            if n > bestn: bestn, curlo, besthi = n, x, None
        else:
            if n == bestn and besthi is None: bestlo, besthi = curlo, x
            n -= 1
    OUT[g] = {"t_exact": t_exact, "lo": bestlo, "hi": besthi,
              "cover": (bestn, len(ivals)),
              "den": Fraction(v2[1]) - Fraction(v0[1])}
    done += 1
    if done % 25 == 0: print(f"{done}", flush=True)
pickle.dump(OUT, open("/tmp/walle/build/_ruler_v6_thw_wide3.pkl", "wb"))
full = sum(1 for e in OUT.values() if e["cover"][0] == e["cover"][1] and e["cover"][1] > 0)
inside = sum(1 for e in OUT.values()
             if e["lo"] is not None and e["lo"] <= e["t_exact"] <= e["hi"])
print(f"{len(OUT)} geoms; full-coverage {full}; t_exact inside {inside}")
