import sys, pickle, itertools
sys.path[:0] = ["/tmp/walle"]
from pathlib import Path
from fractions import Fraction
import numpy as np
import _sweep_fused_join_lattice as m
import _solve_clip_varyings as sv
import _joint_stage_sweep as js

SC = ("/tmp/nix-shell.PFgUGF/claude-1000/-tmp-walle/"
      "4ccfbce8-33b2-4b5f-8e29-93486397c8a4/scratchpad")
def load_sdf(state):
    out = {}
    for line in Path(f"{SC}/childgeo_all_residual_states.txt").read_text().splitlines():
        if "CHILDSDF" not in line: continue
        t = line[line.index("CHILDSDF"):].split()
        if int(t[1]) != state: continue
        out[int(t[2])] = [[int(x,16) for x in t[3+4*v:7+4*v]] for v in range(3)]
    return out
def f32v(w): return m.bits_f32(w)
def perturb(w, k):
    if w == 0: return w
    return m.key_to_bits(m.ordered_key(w) + k)

def fr_bits(word):
    if word & 0x7fffffff == 0: return Fraction(0)
    s, mant, e = m.f32_parts(word)
    return Fraction(s*mant) * Fraction(2)**e

def rne_f32_frac(v):
    if v == 0: return 0
    neg = v < 0; av = abs(v); e = 0
    while av >= 2: av/=2; e+=1
    while av < 1: av*=2; e-=1
    scaled = av * 2**23
    mant = int(scaled); frac = scaled - mant
    if frac > Fraction(1,2) or (frac == Fraction(1,2) and mant & 1): mant += 1
    if mant >= 2**24: mant //= 2; e += 1
    return ((e+127)<<23) | (mant & 0x7fffff) | (0x80000000 if neg else 0)

def sel_oa(mand, me, sel, se):
    prod = mand*sel; bits = prod.bit_length(); shift = bits-27
    if bits <= 32:
        if shift <= 0: return prod, me+se
        idx = (prod + (1 << (shift-1))) >> shift
        if idx.bit_length() > 27: idx >>= 1; shift += 1
        return idx, me+se+shift
    T = max(0, mand.bit_length() - 8)
    return (js.pps(mand, sel, T) + (20 << T)) >> shift, me+se+shift

def gradient(edge_int, sel, se, det_sign, g24=True):
    """barycentric gradient word for one edge coefficient (edge/256 in f32)."""
    if edge_int == 0: return Fraction(0)
    edge = float(np.float32(edge_int/256.0))
    _, mev, ee = m.f32_parts(m.f32_bits(abs(edge)))
    idx, e = sel_oa(mev, ee, sel, se)
    if g24:
        sh = idx.bit_length()-24
        if sh > 0:
            idx = m.rne_int(idx, sh); e += sh
            if idx.bit_length() > 24: idx >>= 1; e += 1
    sgn = (1 if edge > 0 else -1) * det_sign
    return Fraction(sgn*idx) * Fraction(2)**e

obs = pickle.load(open("/tmp/walle/build/_rcd_obs.pkl","rb"))
def edge_coeff(fixed, v, axis):
    a2, b2 = (v+1)%3, (v+2)%3
    if axis == 0: return fixed[a2][1]-fixed[b2][1]
    return fixed[b2][0]-fixed[a2][0]

def slope_model(verts, vwords, axis, order, form, g24):
    fixed = [(round(f32v(v[0])*256), round(f32v(v[1])*256)) for v in verts]
    det = ((fixed[1][0]-fixed[0][0])*(fixed[2][1]-fixed[0][1])
           - (fixed[1][1]-fixed[0][1])*(fixed[2][0]-fixed[0][0]))
    sel, se = sv.selector_for(abs(det))
    ds = -1 if det < 0 else 1
    anchor = min(range(3), key=lambda i:(fixed[i][1], fixed[i][0]))
    terms = []
    if form == "delta":
        for v in range(3):
            if v == anchor: continue
            dv = float(np.float32(np.float32(f32v(vwords[v]))
                                  - np.float32(f32v(vwords[anchor]))))
            g = gradient(edge_coeff(fixed, v, axis), sel, se, ds, g24)
            terms.append((Fraction(dv), g))
    else:  # raw 3-vertex
        for v in range(3):
            val = Fraction(np.float32(f32v(vwords[v])).item())
            g = gradient(edge_coeff(fixed, v, axis), sel, se, ds, g24)
            terms.append((val, g))
    if order == "rev": terms = terms[::-1]
    # FMA chain: first product rounded f32, then fma-accumulate (round each fma)
    acc = None
    for val, g in terms:
        p = val * g
        if acc is None:
            acc = fr_bits(rne_f32_frac(p))
        else:
            acc = fr_bits(rne_f32_frac(p + acc))
    if acc is None or acc == 0: return 0
    return rne_f32_frac(acc)

AN = {}
best = {}
for order in ("fwd", "rev"):
    for form in ("delta", "raw"):
        for g24 in (True, False):
            ok = tot = 0
            bad = []
            for (st, od, ctx), d in sorted(obs.items()):
                verts = load_sdf(st)[od]
                ch = ctx % 2
                fixed = [(round(f32v(v[0])*256), round(f32v(v[1])*256)) for v in verts]
                an = min(range(3), key=lambda i:(fixed[i][1], fixed[i][0]))
                vwords = []
                for vi in range(3):
                    w = verts[vi][2+ch]
                    if ctx >= 2 and vi == an: w = perturb(w, 1)
                    vwords.append(w)
                a = slope_model(verts, vwords, 0, order, form, g24)
                b = slope_model(verts, vwords, 1, order, form, g24)
                tot += 1
                if (a, b) == d["AB"]: ok += 1
                elif len(bad) < 2:
                    fmt=lambda w: format(w,'08x')
                    bad.append(f"s{st}o{od}c{ctx} hw=({fmt(d['AB'][0])},{fmt(d['AB'][1])}) law=({fmt(a)},{fmt(b)})")
            print(f"order={order} form={form} g24={g24}: {ok}/{tot}")
            for b_ in bad: print("   ", b_)
