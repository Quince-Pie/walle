"""Fit the fused clip+setup model against exact A/B words per geometry.

For each t-readback geometry (clip at y=-512 with two crossing edges),
model: clip polygon (rational), candidate t per crossing, snapped clip
positions, wide values; per covering sub-triangle: fused setup -> A/B.
Candidate t models are parametrized; count exact word matches over all
geometries x channels x sub-triangles (any-triple)."""
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
PLAN = json.loads((D / "reveal-agx-setup-accumulator-plan.json").read_text())
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

# hw AB per geometry (all draws share the record's triple set; take first)
geo_hw = {}
geo_pos = {}
for exp, draw in zip(PLAN["experiments"], PLAN["draws"]):
    gi = exp["geometryIndex"]
    if gi in geo_hw: continue
    r = exp["recordIndex"]
    geo_hw[gi] = [(int(T[r][c][0]), int(T[r][c][1])) for c in range(3)]
    geo_pos[gi] = [tuple(p) for p in exp["positions"]]

def clip_and_test(P, tmodel):
    """clip V0,V1,V2 triangle at y=-512 (keep y >= -512); returns polygon
    with (pos_rational, tag) where tag = orig index or ('clip', u, w, t)."""
    pts = [ (Fraction(p[0]), Fraction(p[1])) for p in P ]
    poly = [(pts[i], i) for i in range(3)]
    out = []
    c = Fraction(-512)
    for i in range(len(poly)):
        p, ti = poly[i]; q, tj = poly[(i+1) % 3]
        pin = p[1] >= c; qin = q[1] >= c
        def cut(u, ui, w, wi):
            t = tmodel(u, w)
            np_ = (u[0] + t*(w[0]-u[0]), c)
            return (np_, ("clip", ui, wi, t))
        if pin:
            out.append((p, ti))
            if not qin: out.append(cut(p, ti, q, tj))
        elif qin:
            out.append(cut(p, ti, q, tj))
    return out

def value_of(tag, ctx):
    if isinstance(tag, int):
        return Fraction(1) if tag == ctx else Fraction(0)
    _, ui, wi, t = tag
    return value_of(ui, ctx) + t * (value_of(wi, ctx) - value_of(ui, ctx))

def t_exact_model(u, w):
    return (Fraction(-512) - u[1]) / (w[1] - u[1])
def t_f32div(u, w):
    un = float(f32(-512.0 - float(u[1]))); dn = float(f32(float(w[1]) - float(u[1])))
    return Fraction(float(f32(un / dn)))
def t_ndc(u, w):
    uy = float(f32(f32(float(u[1])) * f32(-2/2048) + f32(1)))
    wy = float(f32(f32(float(w[1])) * f32(-2/2048) + f32(1)))
    num = float(f32(1.5 - uy)); den = float(f32(wy - uy))
    return Fraction(float(f32(num / den)))
def t_ndc_wide(u, w):
    uy = float(f32(f32(float(u[1])) * f32(-2/2048) + f32(1)))
    wy = float(f32(f32(float(w[1])) * f32(-2/2048) + f32(1)))
    return (Fraction(1.5) - Fraction(uy)) / (Fraction(wy) - Fraction(uy))

VQ = [None]
VMODE = ["rne"]
def quantW(v, W, mode=None):
    if v == 0 or W is None: return v
    mode = VMODE[0] if mode is None else mode
    from fractions import Fraction as F
    if v < 0: return -quantW(-v, W, mode)
    e = v.numerator.bit_length() - v.denominator.bit_length()
    if v < F(2)**e: e -= 1
    step = F(2)**(e - W + 1)
    q = v / step
    fl = q.numerator // q.denominator; fr = q - fl
    if mode == "rtz": mant = fl
    elif mode == "up": mant = fl + (1 if fr > 0 else 0)
    elif mode == "hup": mant = fl + (1 if fr >= F(1,2) else 0)
    else: mant = fl + (1 if (fr > F(1,2) or (fr == F(1,2) and fl % 2)) else 0)
    return mant * step
_value_of = value_of
def value_of(tag, ctx):
    v = _value_of(tag, ctx)
    if isinstance(tag, int): return v
    return quantW(v, VQ[0])
SNAPF32 = [False]
_snap0 = snap
def snap(v):
    if SNAPF32[0]:
        import numpy as _np
        v = float(_np.float32(float(v)))
    return _snap0(v)
CANDS = []
for md in ("rne", "rtz", "hup", "up"):
    CANDS.append((f"vq24-{md}", t_exact_model, 24, md, False))
CANDS.append(("vq24-rne-snapf32", t_exact_model, 24, "rne", True))
for name, tm, W, md, sf in CANDS:
    VMODE[0] = md; SNAPF32[0] = sf
    VQ[0] = W
    per_ctx = {0: [0,0], 1: [0,0], 2: [0,0]}
    ok = tot = 0
    for gi, hws in sorted(geo_hw.items()):
        P = geo_pos[gi]
        poly = clip_and_test(P, tm)
        if len(poly) < 3: continue
        spts = [(snap(e[0][0]), snap(e[0][1])) for e in poly]
        n = len(poly)
        for ctx in range(3):
            hw = hws[ctx]
            tot += 1
            found = False
            for tri in itertools.combinations(range(n), 3):
                fx3 = [spts[j] for j in tri]
                vals = [value_of(poly[j][1], ctx) for j in tri]
                if fused_AB(fx3, vals) == hw:
                    found = True; break
            ok += found
            per_ctx[ctx][0] += found; per_ctx[ctx][1] += 1
    print(f"{name}: {ok}/{tot}  " +
          " ".join(f"c{c2}={a}/{b}" for c2,(a,b) in per_ctx.items()))
