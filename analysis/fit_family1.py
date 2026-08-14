import sys, pickle
sys.path[:0] = ["/tmp/walle"]
from pathlib import Path
import numpy as np
import _sweep_fused_join_lattice as m
import _solve_clip_varyings as sv
import _joint_stage_sweep as js

def load_sdf(state):
    out = {}
    for line in Path("/tmp/walle/build/_childgeo_all_residual_states.txt").read_text().splitlines():
        if "CHILDSDF" not in line: continue
        t = line[line.index("CHILDSDF"):].split()
        if int(t[1]) != state: continue
        out[int(t[2])] = [[int(x,16) for x in t[3+4*v:7+4*v]] for v in range(3)]
    return out
def f32v(w): return m.bits_f32(w)

basis = pickle.load(open("/tmp/walle/build/_rcb_obs.pkl","rb"))
# collect (mev, ee, sel, se, sign, hw_word) for every nonzero-edge gradient
cases = []
for (st, od, ctx), d in sorted(basis.items()):
    if ctx == 3: continue
    verts = load_sdf(st)[od]
    fixed = [(round(f32v(v[0])*256), round(f32v(v[1])*256)) for v in verts]
    det = ((fixed[1][0]-fixed[0][0])*(fixed[2][1]-fixed[0][1])
           - (fixed[1][1]-fixed[0][1])*(fixed[2][0]-fixed[0][0]))
    sel, se = sv.selector_for(abs(det))
    ds = -1 if det < 0 else 1
    v = ctx
    a2, b2 = (v+1)%3, (v+2)%3
    for axis, e_int in enumerate((fixed[a2][1]-fixed[b2][1], fixed[b2][0]-fixed[a2][0])):
        if e_int == 0: continue
        edge = float(np.float32(e_int/256.0))
        _, mev, ee = m.f32_parts(m.f32_bits(abs(edge)))
        sgn = (1 if edge > 0 else -1) * ds
        cases.append((st, od, v, axis, mev, ee, sel, se, sgn, d["AB"][axis]))
print(f"{len(cases)} nonzero-edge gradient measurements")

def rne_int(v, sh):
    if sh <= 0: return v
    q = v >> sh; r = v - (q << sh); h = 1 << (sh-1)
    return q + (1 if (r > h or (r == h and q & 1)) else 0)
def hup_int(v, sh):
    if sh <= 0: return v
    return (v + (1 << (sh-1))) >> sh
def flo_int(v, sh):
    return v >> sh if sh > 0 else v

best = []
for T in range(12, 24):
    for bias in range(0, 33):
        for rname, r24 in (("rne", rne_int), ("hup", hup_int), ("flo", flo_int)):
            ok = 0
            for st, od, v, axis, mev, ee, sel, se, sgn, hw in cases:
                prod = mev * sel
                bits = prod.bit_length(); shift = bits - 27
                val = js.pps(mev, sel, T) + (bias << T)
                idx = val >> shift if shift > 0 else val
                e = ee + se + max(shift, 0)
                sh = idx.bit_length() - 24
                i24 = r24(idx, sh); e24 = e + max(sh, 0)
                if i24.bit_length() > 24: i24 >>= 1; e24 += 1
                try: w = m.dyadic_to_f32(sgn, i24, e24)
                except Exception: w = None
                ok += (w == hw)
            best.append((ok, T, bias, rname))
best.sort(reverse=True)
for row in best[:8]: print(row)
