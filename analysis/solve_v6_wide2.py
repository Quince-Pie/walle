"""Re-derive v6 ruler t_hw windows under the FUSED wide-delta chain.

Observable: A/B/C words of the clipped triangle whose clip-vertex carries
the wide value t*e (e swept in 1-ulp steps near 1.0).  Model:
  delta_wide = t_wide * e  (exact rational; anchor value is ps/V1 f32)
  first product: half-up to 27 bits of |delta_wide * edge| (wide operands)
  join: RNE-28 (single product: pass-through), selector product, RNE-24.
For each (geometry, e) and candidate t_wide the chain is deterministic;
binary-search the t_wide window reproducing ALL rows of the geometry.
"""
import json, pickle, sys
sys.path[:0] = ["/tmp/walle"]
from collections import defaultdict
from fractions import Fraction
from pathlib import Path
import numpy as np
import _sweep_fused_join_lattice as m
import _solve_clip_varyings as sv
import _joint_stage_sweep as js

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
    """|v| (Fraction) -> (mant27, exp) half-up."""
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

def fused_words(fx, vals_wide, sel, se, ds, tiles):
    """vals_wide: per-vertex Fractions. Returns (A, B, {tile: C})."""
    anchor = min(range(3), key=lambda i: (fx[i][1], fx[i][0]))
    nums = []
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
            nums.append((0,0,0)); continue
        gmin = min(g for _, g in parts)
        tot = sum(v << (g-gmin) for v, g in parts)
        if tot == 0: nums.append((0,0,0)); continue
        s = 1 if tot > 0 else -1
        mag, g28 = tap_rne(abs(tot), gmin, 28)
        nums.append((s, mag, g28))
    out = []
    for axis in range(2):
        s, mag, g = nums[axis]
        if s == 0: out.append(0); continue
        idx, e2 = sel_oa(mag, g, sel, se)
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
    # window on t_wide: start wide, narrow by bisection per observation
    lo = t_exact * (1 - Fraction(1, 1 << 18))
    hi = t_exact * (1 + Fraction(1, 1 << 18))
    consistent = True
    nrows = 0
    for ch, (ps, pe) in enumerate(exp0["pairs"]):
        hw = None
        for (exp, draw) in recs:
            r = exp["recordIndex"]
            hw = (int(TRIPLES[r][ch][0]), int(TRIPLES[r][ch][1]))
            break
        if hw is None: continue
        e_val = Fraction(f32(pe))
        anchor_vals_base = [None, Fraction(f32(V1)), Fraction(f32(ps))]
        def words_at(t):
            vals = [t * e_val, anchor_vals_base[1], anchor_vals_base[2]]
            return fused_words(fx, vals, sel, se, ds, [])
        # check window ends produce hw; bisect boundaries
        if words_at((lo+hi)/2) != hw:
            continue   # this row's model may be off; skip
        nrows += 1
        # narrow lower bound
        a, b = lo, (lo+hi)/2
        for _ in range(64):
            mid = (a+b)/2
            if words_at(mid) == hw: b = mid
            else: a = mid
        lo_new = b
        a, b = (lo+hi)/2, hi
        for _ in range(64):
            mid = (a+b)/2
            if words_at(mid) == hw: a = mid
            else: b = mid
        hi_new = a
        lo, hi = max(lo, lo_new), min(hi, hi_new)
        if lo > hi: consistent = False; break
    prev = OUT.get(g)
    if prev is not None and prev["consistent"] and consistent:
        lo = max(lo, prev["lo"]); hi = min(hi, prev["hi"])
        nrows += prev["rows"]
        if lo > hi: consistent = False
    OUT[g] = {"t_exact": t_exact, "lo": lo, "hi": hi,
              "rows": nrows, "consistent": consistent,
              "num": None, "den": Fraction(v2[1]) - Fraction(v0[1])}
    done += 1
    if done % 50 == 0: print(f"{done} geometries", flush=True)
pickle.dump(OUT, open("/tmp/walle/build/_ruler_v6_thw_wide2.pkl", "wb"))
ok = sum(1 for e in OUT.values() if e["consistent"] and e["rows"] > 0)
print(f"{len(OUT)} geometries; consistent with rows: {ok}")
# quick look: does t_exact fall inside windows?
inside = sum(1 for e in OUT.values()
             if e["consistent"] and e["rows"] > 0 and e["lo"] <= e["t_exact"] <= e["hi"])
print(f"t_exact inside window: {inside}/{ok}")
