import sys, json, itertools
sys.path[:0] = ["/tmp/walle"]
from pathlib import Path
from fractions import Fraction
import numpy as np
import _sweep_fused_join_lattice as m
import _solve_clip_varyings as sv
import _joint_stage_sweep as js
f32 = np.float32

D = Path("/tmp/walle/build/analysis-agx-basis/dual-lane-sweep-plan-v2")
PLAN = json.loads((D / "reveal-agx-setup-accumulator-plan.json").read_text())
T = m.load_records(D / "capture.raw", len(PLAN["draws"]))

def snap(v): return int(np.floor(f32(v) * 256.0 + 0.5))
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

def fused_AB(fx, vals_wide):
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

def clip_poly(P, guard):
    poly = [((Fraction(float(f32(p[0]))), Fraction(float(f32(p[1])))), i) for i, p in enumerate(P)]
    planes = ((0, Fraction(-guard), 1), (1, Fraction(-guard), 1),
              (0, Fraction(2048+guard), -1), (1, Fraction(2048+guard), -1))
    for a, c, s in planes:
        if not poly: break
        out = []
        for i in range(len(poly)):
            p = poly[i]; q = poly[(i+1) % len(poly)]
            pin = (p[0][a] - c) * s >= 0
            qin = (q[0][a] - c) * s >= 0
            def cut(u, w):
                t = (c - u[0][a]) / (w[0][a] - u[0][a])
                np_ = (u[0][0] + t*(w[0][0]-u[0][0]), u[0][1] + t*(w[0][1]-u[0][1]))
                return (np_, ("clip", u[1], w[1], t))
            if pin:
                out.append(p)
                if not qin: out.append(cut(p, q))
            elif qin:
                out.append(cut(p, q))
        poly = out
    return poly

def value_of(tag, ctx):
    if isinstance(tag, int):
        return Fraction(1) if tag == ctx else Fraction(0)
    _, ui, wi, t = tag
    vu = value_of(ui, ctx); vw = value_of(wi, ctx)
    return vu + t * (vw - vu)      # exact wide

def contains(fx3, px, py):
    det = ((fx3[1][0]-fx3[0][0])*(fx3[2][1]-fx3[0][1])
           - (fx3[1][1]-fx3[0][1])*(fx3[2][0]-fx3[0][0]))
    if det == 0: return False
    exp_ = -1 if det < 0 else 1
    cx, cy = 256*px+128, 256*py+128
    for e in range(3):
        nx = (e+1)%3
        ex = fx3[nx][0]-fx3[e][0]; ey = fx3[nx][1]-fx3[e][1]
        cr = ex*(cy-fx3[e][1]) - ey*(cx-fx3[e][0])
        if cr == 0: continue
        if (1 if cr > 0 else -1) != exp_: return False
    return True

from collections import Counter
guard = 512
match_pat = Counter(); miss = []
fan_ok = fan_tot = 0
for exp, draw in zip(PLAN["experiments"], PLAN["draws"]):
    P = [tuple(p) for p in exp["positions"]]
    if not any(p[0] < 0 or p[1] < 0 or p[0] >= 2048 or p[1] >= 2048 for p in P):
        continue
    r = exp["recordIndex"]
    px, py = draw["x"], draw["y"]
    poly = clip_poly(P, guard)
    if len(poly) < 3: continue
    pts = [(snap(float(e[0][0])), snap(float(e[0][1]))) for e in poly]
    n = len(poly)
    for ctx in range(3):
        hw = (int(T[r][ctx][0]), int(T[r][ctx][1]))
        matches = []
        for tri in itertools.combinations(range(n), 3):
            fx3 = [pts[j] for j in tri]
            vals = [value_of(poly[j][1], ctx) for j in tri]
            if fused_AB(fx3, vals) == hw:
                matches.append(tri)
        # candidate rule: fan (0, i, i+1) whose triangle contains the pixel
        rule = None
        for i in range(1, n-1):
            tri = (0, i, i+1)
            if contains([pts[j] for j in tri], px, py):
                rule = tri; break
        fan_tot += 1
        if rule is not None and rule in matches: fan_ok += 1
        if matches:
            match_pat[tuple(matches)] += 1
        else:
            miss.append((tuple(P), ctx, n))
print(f"fan-rule (0,i,i+1 containing pixel) correct: {fan_ok}/{fan_tot}")
print("top match patterns:")
for pat, c in match_pat.most_common(8): print("  ", pat, c)
print(f"complete misses: {len(miss)}")
for r_ in miss[:6]: print("   ", r_)
