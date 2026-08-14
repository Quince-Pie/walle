import sys, pickle, json, itertools
sys.path[:0] = ["/tmp/walle"]
from pathlib import Path
import numpy as np
import _sweep_fused_join_lattice as m
import _solve_clip_varyings as sv
import _joint_stage_sweep as js
f32 = np.float32

D = Path("/tmp/walle/build/analysis-agx-basis/dual-lane-sweep-plan-v2")
PLAN = json.loads((D / "reveal-agx-setup-accumulator-plan.json").read_text())
T = m.load_records(D / "capture.raw", len(PLAN["draws"]))

def snap(v):
    return int(np.floor(f32(v) * 256.0 + 0.5))

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
            if axis == 0: e = fx[a2][1]-fx[b2][1]
            else: e = fx[b2][0]-fx[a2][0]
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
        if not parts:
            out.append(0); continue
        gmin = min(g for _,g in parts)
        tot = sum(v << (g-gmin) for v,g in parts)
        if tot == 0: out.append(0); continue
        s = 1 if tot>0 else -1
        mag, g28 = tap_rne(abs(tot), gmin, 28)
        idx, e = sel_oa(mag, g28, sel, se)
        sh = idx.bit_length()-24
        if sh > 0:
            idx = rne_int(idx, sh); e += sh
            if idx.bit_length() > 24: idx >>= 1; e += 1
        try: out.append(m.dyadic_to_f32(s*ds, idx, e))
        except Exception: out.append(None)
    return tuple(out)

def rne_frac(v):
    from fractions import Fraction
    if v == 0: return 0.0
    neg = v < 0; av = abs(v); e = 0
    while av >= 2: av/=2; e+=1
    while av < 1: av*=2; e-=1
    scaled = av * 2**23
    mant = int(scaled); frac = scaled - mant
    if frac > Fraction(1,2) or (frac == Fraction(1,2) and mant & 1): mant += 1
    if mant >= 2**24: mant //= 2; e += 1
    return float((-1 if neg else 1) * mant * 2.0**(e-23))

def snap_pair(pt):
    return (snap(pt[0]), snap(pt[1]))

def clip_poly(pts, vals_list, lerp_mode):
    """Sutherland-Hodgman in f32 pixel space against x>=0, y>=0, x<=2048, y<=2048."""
    def inside(p, plane):
        a, c, s = plane
        return (p[a] - c) * s >= 0
    def intersect(p, q, vp, vq, plane):
        a, c, s = plane
        from fractions import Fraction
        if lerp_mode == "rational":
            # exact rational t and lerp, single RNE to f32 per output
            fp = [Fraction(float(f32(p[0]))), Fraction(float(f32(p[1])))]
            fq = [Fraction(float(f32(q[0]))), Fraction(float(f32(q[1])))]
            t = (Fraction(c) - fp[a]) / (fq[a] - fp[a])
            def rne(v):
                return float(np.float32(np.nextafter(float(v), float(v))) ) if False else rne_frac(v)
            nx = rne_frac(fp[0] + t * (fq[0] - fp[0]))
            ny = rne_frac(fp[1] + t * (fq[1] - fp[1]))
            nvals = [rne_frac(Fraction(float(f32(a0)))
                              + t * (Fraction(float(f32(b0))) - Fraction(float(f32(a0)))))
                     for a0, b0 in zip(vp, vq)]
        else:
            t = f32((f32(c) - f32(p[a])) / f32(f32(q[a]) - f32(p[a])))
            if lerp_mode == "fma":
                nx = float(f32(f32(p[0]) + t * f32(f32(q[0]) - f32(p[0]))))
                ny = float(f32(f32(p[1]) + t * f32(f32(q[1]) - f32(p[1]))))
                nvals = [float(f32(f32(a0) + t * f32(f32(b0) - f32(a0))))
                         for a0, b0 in zip(vp, vq)]
            else:
                omt = f32(f32(1) - t)
                nx = float(f32(omt * f32(p[0]) + t * f32(q[0])))
                ny = float(f32(omt * f32(p[1]) + t * f32(q[1])))
                nvals = [float(f32(omt * f32(a0) + t * f32(b0)))
                         for a0, b0 in zip(vp, vq)]
        if a == 0: nx = float(c)
        else: ny = float(c)
        return (nx, ny), nvals
    poly = list(zip(pts, vals_list))
    for plane in ((0, 0.0, 1), (1, 0.0, 1), (0, 2048.0, -1), (1, 2048.0, -1)):
        if not poly: break
        out = []
        for i in range(len(poly)):
            p, vp = poly[i]
            q, vq = poly[(i+1) % len(poly)]
            pin, qin = inside(p, plane), inside(q, plane)
            if pin:
                out.append((p, vp))
                if not qin:
                    out.append(intersect(p, q, vp, vq, plane))
            elif qin:
                out.append(intersect(p, q, vp, vq, plane))
        poly = out
    return poly

def contains(fx3, px, py):
    det = ((fx3[1][0]-fx3[0][0])*(fx3[2][1]-fx3[0][1])
           - (fx3[1][1]-fx3[0][1])*(fx3[2][0]-fx3[0][0]))
    if det == 0: return False
    exp = -1 if det < 0 else 1
    cx, cy = 256*px+128, 256*py+128
    for e in range(3):
        nx = (e+1)%3
        ex = fx3[nx][0]-fx3[e][0]; ey = fx3[nx][1]-fx3[e][1]
        cr = ex*(cy-fx3[e][1]) - ey*(cx-fx3[e][0])
        if cr == 0: continue
        if (1 if cr > 0 else -1) != exp: return False
    return True

results = {}
for lerp_mode in ("rational",):
    ok = tot = 0; ex = []
    for exp, draw in zip(PLAN["experiments"], PLAN["draws"]):
        P = [tuple(p) for p in exp["positions"]]
        if not any(p[0] < 0 or p[1] < 0 or p[0] >= 2048 or p[1] >= 2048 for p in P):
            continue
        r = exp["recordIndex"]
        px, py = draw["x"], draw["y"]
        for ctx in range(3):
            vals = [1.0 if v == ctx else 0.0 for v in range(3)]
            hw = (int(T[r][ctx][0]), int(T[r][ctx][1]))
            poly = clip_poly(P, [[v] for v in vals], lerp_mode)
            if len(poly) < 3: continue
            pts = [snap_pair(p) for p, _ in poly]
            tot += 1
            matches = []
            import itertools as it
            for tri in it.permutations(range(len(poly)), 3):
                if tri[0] > tri[1] or tri[1] > tri[2]:
                    continue   # combinations with fixed order first
                fx3 = [pts[j] for j in tri]
                got = slope_words(fx3, [poly[j][1][0] for j in tri])
                if got == hw:
                    matches.append(tri)
            if matches: ok += 1
            if len(ex) < 12:
                ex.append((tuple(P), ctx, len(poly), matches))
    results[lerp_mode] = (ok, tot)
    print(f"clip lerp={lerp_mode}: some-triple matches {ok}/{tot}")
    for e in ex[:12]: print("   ", e)
