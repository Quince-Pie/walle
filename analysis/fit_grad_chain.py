import sys, pickle
sys.path[:0] = ["/tmp/walle"]
from pathlib import Path
from fractions import Fraction
import numpy as np
import _sweep_fused_join_lattice as m
import _solve_clip_varyings as sv
import _fit_child_tiles as ft
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

def sel_oa(mand, me, sel, se):
    prod = mand*sel; bits = prod.bit_length(); shift = bits-27
    if bits <= 32:
        if shift <= 0: return prod, me+se
        idx = (prod + (1 << (shift-1))) >> shift
        if idx.bit_length() > 27: idx >>= 1; shift += 1
        return idx, me+se+shift
    T = max(0, mand.bit_length() - 8)
    return (js.pps(mand, sel, T) + (20 << T)) >> shift, me+se+shift

def product27(vm, ve, gm, ge, mode):
    """value(<=24b) x gradient(<=27b) -> 27-bit product index."""
    prod = vm*gm; bits = prod.bit_length(); shift = bits-27
    if mode == "exact":
        if shift <= 0: return prod, ve+ge
        t = (prod + (1 << (shift-1))) >> shift
        if t.bit_length() > 27: t >>= 1; shift += 1
        return t, ve+ge+shift
    if mode == "Tval":
        T = max(0, vm.bit_length() - 8)
    elif mode == "Tgrad":
        T = max(0, gm.bit_length() - 8)
    elif mode == "Tprod":
        T = max(0, bits - 35)
    else: raise ValueError(mode)
    bias = 20
    v = js.pps(vm, gm, T) + (bias << T)
    if shift > 0: return v >> shift, ve+ge+shift
    return v, ve+ge

def edge_coeff(fixed, v, axis):
    a2, b2 = (v+1)%3, (v+2)%3
    if axis == 0: return fixed[a2][1]-fixed[b2][1]
    return fixed[b2][0]-fixed[a2][0]

def slope_model(verts, vwords, axis, pmode, form):
    fixed = [(round(f32v(v[0])*256), round(f32v(v[1])*256)) for v in verts]
    det = ((fixed[1][0]-fixed[0][0])*(fixed[2][1]-fixed[0][1])
           - (fixed[1][1]-fixed[0][1])*(fixed[2][0]-fixed[0][0]))
    sel, se = sv.selector_for(abs(det))
    ds = -1 if det < 0 else 1
    anchor = min(range(3), key=lambda i:(fixed[i][1], fixed[i][0]))
    parts = []
    for v in range(3):
        if form == "delta":
            if v == anchor: continue
            val = float(np.float32(np.float32(f32v(vwords[v]))
                                   - np.float32(f32v(vwords[anchor]))))
        else:
            val = float(np.float32(f32v(vwords[v])).item())
        e_int = edge_coeff(fixed, v, axis)
        if val == 0.0 or e_int == 0: continue
        edge = float(np.float32(e_int/256.0))
        _, mev, ee = m.f32_parts(m.f32_bits(abs(edge)))
        gm, ge = sel_oa(mev, ee, sel, se)   # 27-bit gradient magnitude
        gsign = (1 if edge > 0 else -1) * ds
        _, vm, ve = m.f32_parts(m.f32_bits(abs(val)))
        pm, pe = product27(vm, ve, gm, ge, pmode)
        sgn = (1 if val > 0 else -1) * gsign
        parts.append((sgn*pm, pe))
    if not parts: return 0
    gmin = min(g for _,g in parts)
    tot = sum(v << (g-gmin) for v,g in parts)
    if tot == 0: return 0
    s28, m28, e28 = ft.norm(tot, gmin, 28, "rne")
    s24, m24, e24 = ft.norm(s28*m28, e28, 24, "rne")
    try: return m.dyadic_to_f32(s24, m24, e24)
    except (ValueError, OverflowError): return None

obs = pickle.load(open("/tmp/walle/build/_rcd_obs.pkl","rb"))
for pmode in ("exact", "Tval", "Tgrad", "Tprod"):
    for form in ("delta", "raw"):
        ok = tot = 0; bad = []
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
            a = slope_model(verts, vwords, 0, pmode, form)
            b = slope_model(verts, vwords, 1, pmode, form)
            tot += 1
            if (a, b) == d["AB"]: ok += 1
            elif len(bad) < 2:
                fmt=lambda w: "None" if w is None else format(w,'08x')
                bad.append(f"s{st}o{od}c{ctx} hw=({fmt(d['AB'][0])},{fmt(d['AB'][1])}) law=({fmt(a)},{fmt(b)})")
        print(f"pmode={pmode} form={form}: {ok}/{tot}")
        for b_ in bad: print("   ", b_)
