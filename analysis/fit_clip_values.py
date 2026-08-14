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
def rne_frac(v):
    if v == 0: return 0.0
    neg = v < 0; av = abs(v); e = 0
    while av >= 2: av/=2; e+=1
    while av < 1: av*=2; e-=1
    scaled = av * 2**23
    mant = int(scaled); frac = scaled - mant
    if frac > Fraction(1,2) or (frac == Fraction(1,2) and mant & 1): mant += 1
    if mant >= 2**24: mant //= 2; e += 1
    return float((-1 if neg else 1) * mant * 2.0**(e-23))
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
def sel_oa(mand, me, sel, se):
    prod = mand*sel; bits = prod.bit_length(); shift = bits-27
    if bits <= 32:
        if shift <= 0: return prod, me+se
        idx = (prod + (1 << (shift-1))) >> shift
        if idx.bit_length() > 27: idx >>= 1; shift += 1
        return idx, me+se+shift
    Tc = max(0, mand.bit_length() - 8)
    return (js.pps(mand, sel, Tc) + (20 << Tc)) >> shift, me+se+shift

def slope_words(fx, vals):
    det = ((fx[1][0]-fx[0][0])*(fx[2][1]-fx[0][1])
           - (fx[1][1]-fx[0][1])*(fx[2][0]-fx[0][0]))
    if det == 0: return None
    sel, se = sv.selector_for(abs(det))
    ds = -1 if det < 0 else 1
    anchor = min(range(3), key=lambda i:(fx[i][1], fx[i][0]))
    out = []
    for axis in range(2):
        parts = []
        for v in range(3):
            if v == anchor: continue
            a2, b2 = (v+1)%3, (v+2)%3
            e = fx[a2][1]-fx[b2][1] if axis == 0 else fx[b2][0]-fx[a2][0]
            delta = float(f32(f32(vals[v]) - f32(vals[anchor])))
            edge = float(f32(e/256.0))
            if delta == 0.0 or edge == 0.0: continue
            _, md, ed = m.f32_parts(m.f32_bits(abs(delta)))
            _, mev, ee = m.f32_parts(m.f32_bits(abs(edge)))
            sign = -1 if (delta<0)!=(edge<0) else 1
            prod = md*mev; bits = prod.bit_length(); shift = bits-27
            if bits <= 32:
                if shift <= 0: t2, g = prod, ed+ee
                else:
                    t2 = (prod + (1 << (shift-1))) >> shift
                    if t2.bit_length() > 27: t2 >>= 1; shift += 1
                    g = ed+ee+shift
            else:
                t2, g = (js.pps(md,mev,16)+(15<<16))>>shift, ed+ee+shift
            parts.append((sign*t2, g))
        if not parts: out.append(0); continue
        gmin = min(g for _,g in parts)
        tot = sum(v << (g-gmin) for v,g in parts)
        if tot == 0: out.append(0); continue
        s = 1 if tot>0 else -1
        mag, g28 = tap_rne(abs(tot), gmin, 28)
        idx, e2 = sel_oa(mag, g28, sel, se)
        sh = idx.bit_length()-24
        if sh > 0:
            idx = rne_int(idx, sh); e2 += sh
            if idx.bit_length() > 24: idx >>= 1; e2 += 1
        try: out.append(m.dyadic_to_f32(s*ds, idx, e2))
        except Exception: out.append(None)
    return tuple(out)

def clip_poly(P, vmode):
    """SH clip; each vertex: (pos_float_pair, {orig_frac_bary for value calc})"""
    poly = [((Fraction(float(f32(p[0]))), Fraction(float(f32(p[1])))), i, None)
            for i, p in enumerate(P)]
    # vertex entries: (pos(Fractions), orig_index or None, edge_info (i,j,t) for clip verts)
    for a, c, s in ((0, 0, 1), (1, 0, 1), (0, 2048, -1), (1, 2048, -1)):
        if not poly: break
        out = []
        for i in range(len(poly)):
            p = poly[i]; q = poly[(i+1) % len(poly)]
            pin = (p[0][a] - c) * s >= 0
            qin = (q[0][a] - c) * s >= 0
            def cut(u, w):
                t = (Fraction(c) - u[0][a]) / (w[0][a] - u[0][a])
                np_ = (u[0][0] + t*(w[0][0]-u[0][0]), u[0][1] + t*(w[0][1]-u[0][1]))
                return (np_, None, (u, w, t))
            if pin:
                out.append(p)
                if not qin: out.append(cut(p, q))
            elif qin:
                out.append(cut(p, q))
        poly = out
    return poly

def vertex_value(entry, vals, vmode, snapped):
    pos, oi, edge = entry
    if oi is not None:
        return float(vals[oi])
    u, w, t = edge
    def val_of(e2):
        return Fraction(float(vals[e2[1]])) if e2[1] is not None else None
    vu, vw = val_of(u), val_of(w)
    assert vu is not None and vw is not None
    if vmode == "rational":
        return rne_frac(vu + t*(vw - vu))
    if vmode == "snapt":
        # recompute t from SNAPPED positions of u, w and the snapped clip pos
        su = (snap(float(u[0][0])), snap(float(u[0][1])))
        sw = (snap(float(w[0][0])), snap(float(w[0][1])))
        sp = snapped
        # use the axis with larger span
        ax = 0 if abs(sw[0]-su[0]) >= abs(sw[1]-su[1]) else 1
        den = sw[ax] - su[ax]
        if den == 0: return rne_frac(vu + t*(vw - vu))
        t2 = Fraction(sp[ax] - su[ax], den)
        return rne_frac(vu + t2*(vw - vu))
    raise ValueError

results = {}
for vmode in ("rational", "snapt"):
    ok = tot = 0
    for exp, draw in zip(PLAN["experiments"], PLAN["draws"]):
        P = [tuple(p) for p in exp["positions"]]
        if not any(p[0] < 0 or p[1] < 0 or p[0] >= 2048 or p[1] >= 2048 for p in P):
            continue
        r = exp["recordIndex"]
        poly = clip_poly(P, vmode)
        if len(poly) < 3: continue
        pts = [(snap(float(e[0][0])), snap(float(e[0][1]))) for e in poly]
        for ctx in range(3):
            vals = [1.0 if v == ctx else 0.0 for v in range(3)]
            hw = (int(T[r][ctx][0]), int(T[r][ctx][1]))
            vvals = [vertex_value(e, vals, vmode, pts[i]) for i, e in enumerate(poly)]
            tot += 1
            found = False
            for tri in itertools.combinations(range(len(poly)), 3):
                fx3 = [pts[j] for j in tri]
                if slope_words(fx3, [vvals[j] for j in tri]) == hw:
                    found = True; break
            ok += found
    results[vmode] = (ok, tot)
    print(f"value-mode {vmode}: any-triple {ok}/{tot}")
