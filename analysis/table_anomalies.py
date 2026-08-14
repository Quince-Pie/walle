import sys, pickle
sys.path[:0] = ["/tmp/walle"]
from pathlib import Path
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
def parts(w):
    if w == 0 or (w & 0x7fffffff) == 0: return (0,0,0)
    return m.f32_parts(w)

obs = pickle.load(open("/tmp/walle/build/_rcb_obs.pkl","rb"))
def sel_oa(mand, me, sel, se):
    prod = mand*sel; bits = prod.bit_length(); shift = bits-27
    if bits <= 32:
        if shift <= 0: return prod, me+se
        idx = (prod + (1 << (shift-1))) >> shift
        if idx.bit_length() > 27: idx >>= 1; shift += 1
        return idx, me+se+shift
    T = max(0, mand.bit_length() - 8)
    return (js.pps(mand, sel, T) + (20 << T)) >> shift, me+se+shift

print("state ord basis | e_x e_y | hwA hwB | modelA modelB | note")
for (st, od, ctx), d in sorted(obs.items()):
    if ctx == 3: continue
    verts = load_sdf(st)[od]
    fixed = [(round(f32v(v[0])*256), round(f32v(v[1])*256)) for v in verts]
    det = ((fixed[1][0]-fixed[0][0])*(fixed[2][1]-fixed[0][1])
           - (fixed[1][1]-fixed[0][1])*(fixed[2][0]-fixed[0][0]))
    sel, se = sv.selector_for(abs(det))
    ds = -1 if det < 0 else 1
    v = ctx
    a2, b2 = (v+1)%3, (v+2)%3
    e = [fixed[a2][1]-fixed[b2][1], fixed[b2][0]-fixed[a2][0]]
    model = []
    for axis in range(2):
        if e[axis] == 0: model.append(0); continue
        edge = float(np.float32(e[axis]/256.0))
        _, mev, ee = m.f32_parts(m.f32_bits(abs(edge)))
        gm, ge = sel_oa(mev, ee, sel, se)
        sh = gm.bit_length()-24
        if sh > 0:
            gm = m.rne_int(gm, sh); ge += sh
            if gm.bit_length() > 24: gm >>= 1; ge += 1
        sgn = (1 if edge > 0 else -1) * ds
        try: model.append(m.dyadic_to_f32(sgn, gm, ge))
        except Exception: model.append(None)
    hw = d["AB"]
    if (model[0], model[1]) == hw: continue
    notes = []
    for axis, (h, mo) in enumerate(zip(hw, model)):
        if h == mo: continue
        hs, hm, he = parts(h)
        other = hw[1-axis]
        os_, om, oe = parts(other)
        if mo == 0 and h != 0:
            rel = (he - oe) if om else None
            same_mant = (hm == om)
            notes.append(f"axis{axis}: ZERO->tiny expsdelta={rel} same_mant={same_mant} sign={hs}{os_}")
        elif mo not in (None, 0):
            ms, mm, me_ = parts(mo)
            notes.append(f"axis{axis}: main {'+' if hm*2**he > mm*2**me_ else '-'}1ulp-ish hm-mm={hm-mm if he==me_ else 'expdiff'}")
    fmt=lambda w: "None" if w is None else format(w,'08x')
    print(f"s{st} o{od} b{v} | {e[0]} {e[1]} | {fmt(hw[0])} {fmt(hw[1])} | {fmt(model[0])} {fmt(model[1])} | {'; '.join(notes)}")
