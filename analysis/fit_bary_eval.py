"""Clip values = original-triangle barycentric planes evaluated at the
snapped clip vertex position (exact rational).  Fit against A/B words."""
import sys, json, itertools
sys.path[:0] = ["/tmp/walle"]
from pathlib import Path
from fractions import Fraction
import numpy as np
import _sweep_fused_join_lattice as m
import _solve_clip_varyings as sv
import _joint_stage_sweep as js
f32 = np.float32

D = Path("/tmp/walle/build/analysis-agx-basis/t-readback-plan-v1")
PLAN = json.load(open(D / "reveal-agx-setup-accumulator-plan.json"))
T = m.load_records(D / "capture-words.raw", len(PLAN["draws"]))

def snap(v): return int(np.floor(f32(float(v)) * 256.0 + 0.5))
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
def fused_AB(fx, vals):
    det = ((fx[1][0]-fx[0][0])*(fx[2][1]-fx[0][1])
           - (fx[1][1]-fx[0][1])*(fx[2][0]-fx[0][0]))
    if det == 0: return None
    sel, se = sv.selector_for(abs(det))
    ds = -1 if det < 0 else 1
    anchor = min(range(3), key=lambda i: (fx[i][1], fx[i][0]))
    out = []
    for axis in range(2):
        parts = []
        for v in range(3):
            if v == anchor: continue
            a2, b2 = (v+1)%3, (v+2)%3
            e_int = fx[a2][1]-fx[b2][1] if axis == 0 else fx[b2][0]-fx[a2][0]
            delta = vals[v] - vals[anchor]
            edge = Fraction(float(f32(e_int/256.0)))
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

geo_hw = {}; geo_pos = {}
for exp, draw in zip(PLAN["experiments"], PLAN["draws"]):
    gi = exp["geometryIndex"]
    if gi in geo_hw: continue
    r = exp["recordIndex"]
    geo_hw[gi] = [(int(T[r][c][0]), int(T[r][c][1])) for c in range(3)]
    geo_pos[gi] = [tuple(p) for p in exp["positions"]]

def bary_value(V, sx, sy, ctx, space):
    """basis-ctx plane of the ORIGINAL triangle evaluated at subpixel point
    (sx, sy) [fixed units].  space: 'fixed' uses snapped vertex coords,
    'exact' uses exact rational vertex coords."""
    if space == "fixed":
        P = [(Fraction(snap(v[0])), Fraction(snap(v[1]))) for v in V]
        px, py = Fraction(sx), Fraction(sy)
    else:
        P = [(Fraction(v[0])*256, Fraction(v[1])*256) for v in V]
        px, py = Fraction(sx), Fraction(sy)
    d = ((P[1][0]-P[0][0])*(P[2][1]-P[0][1]) - (P[1][1]-P[0][1])*(P[2][0]-P[0][0]))
    i, j, k = ctx, (ctx+1)%3, (ctx+2)%3
    # bary_i at point = cross((P[j]->P[k]), (P[j]->pt)) / d with orientation
    num = ((P[k][0]-P[j][0])*(py-P[j][1]) - (P[k][1]-P[j][1])*(px-P[j][0]))
    return num / d

results = {}
for space in ("fixed", "exact"):
    ok = tot = 0
    per_ctx = {0:[0,0],1:[0,0],2:[0,0]}
    for gi, hws in sorted(geo_hw.items()):
        V = geo_pos[gi]
        Vr = [(Fraction(p[0]), Fraction(p[1])) for p in V]
        c = Fraction(-512)
        t0 = (c - Vr[0][1]) / (Vr[2][1] - Vr[0][1])
        t1 = (c - Vr[1][1]) / (Vr[2][1] - Vr[1][1])
        cut0 = (Vr[0][0] + t0*(Vr[2][0]-Vr[0][0]), c)
        cut1 = (Vr[1][0] + t1*(Vr[2][0]-Vr[1][0]), c)
        entries = [ (Vr[0], 0), (Vr[1], 1), (cut1, "c1"), (cut0, "c0") ]
        spts = [(snap(e[0][0]), snap(e[0][1])) for e in entries]
        for ctx in range(3):
            hw = hws[ctx]
            tot += 1
            # values: originals one-hot; clip verts = bary eval at SNAPPED pos
            def val(idx):
                tag = entries[idx][1]
                if isinstance(tag, int):
                    return Fraction(1) if tag == ctx else Fraction(0)
                sx, sy = spts[idx]
                return bary_value(V, Fraction(2*sx+1,2)*0 + Fraction(sx), Fraction(sy), ctx, space)
            found = False
            for tri in itertools.combinations(range(4), 3):
                fx3 = [spts[j] for j in tri]
                vals = [val(j) for j in tri]
                if fused_AB(fx3, vals) == hw:
                    found = True; break
            ok += found
            per_ctx[ctx][0] += found; per_ctx[ctx][1] += 1
    print(f"bary@snapped ({space}): {ok}/{tot}  " +
          " ".join(f"c{c2}={a}/{b}" for c2,(a,b) in per_ctx.items()))
