import sys, json, pickle
sys.path[:0] = ["/tmp/walle"]
from collections import defaultdict
from pathlib import Path
import numpy as np
import _sweep_fused_join_lattice as m
import _solve_clip_varyings as sv
import _joint_stage_sweep as js

D = Path("/tmp/walle/build/analysis-agx-basis/residual-children-basis-plan-v1")
PLAN = json.loads((D / "reveal-agx-setup-accumulator-plan.json").read_text())
T = m.load_records(D / "capture.raw", len(PLAN["draws"]))
obs = defaultdict(lambda: defaultdict(dict))
for exp, draw in zip(PLAN["experiments"], PLAN["draws"]):
    r = exp["recordIndex"]
    for ctx in range(4):
        d = obs[(exp["state"], exp["drawOrdinal"], ctx)]
        d["AB"] = (int(T[r][ctx][0]), int(T[r][ctx][1]))
        d.setdefault("C", {})[(draw["tileX"], draw["tileY"])] = int(T[r][ctx][2])
pickle.dump(dict(obs), open("/tmp/walle/build/_rcb_obs.pkl","wb"))

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

def sel_oa(mand, me, sel, se):
    prod = mand*sel; bits = prod.bit_length(); shift = bits-27
    if bits <= 32:
        if shift <= 0: return prod, me+se
        idx = (prod + (1 << (shift-1))) >> shift
        if idx.bit_length() > 27: idx >>= 1; shift += 1
        return idx, me+se+shift
    T = max(0, mand.bit_length() - 8)
    return (js.pps(mand, sel, T) + (20 << T)) >> shift, me+se+shift

# For basis vertex i: slope_x = gradient from edge coefficient; compare hw AB
match = mismatch = 0
ex = []
for (st, od, ctx), d in sorted(obs.items()):
    if ctx == 3: continue     # sum context, skip here
    verts = load_sdf(st)[od]
    fixed = [(round(f32v(v[0])*256), round(f32v(v[1])*256)) for v in verts]
    det = ((fixed[1][0]-fixed[0][0])*(fixed[2][1]-fixed[0][1])
           - (fixed[1][1]-fixed[0][1])*(fixed[2][0]-fixed[0][0]))
    sel, se = sv.selector_for(abs(det))
    ds = -1 if det < 0 else 1
    v = ctx  # basis vertex index
    a2, b2 = (v+1)%3, (v+2)%3
    words = []
    for axis in range(2):
        if axis == 0: e_int = fixed[a2][1]-fixed[b2][1]
        else: e_int = fixed[b2][0]-fixed[a2][0]
        if e_int == 0: words.append(0); continue
        edge = float(np.float32(e_int/256.0))
        _, mev, ee = m.f32_parts(m.f32_bits(abs(edge)))
        gm, ge = sel_oa(mev, ee, sel, se)
        sh = gm.bit_length()-24
        if sh > 0:
            gm2 = m.rne_int(gm, sh); ge2 = ge + sh
            if gm2.bit_length() > 24: gm2 >>= 1; ge2 += 1
        else: gm2, ge2 = gm, ge
        sgn = (1 if edge > 0 else -1) * ds
        try: words.append(m.dyadic_to_f32(sgn*gm2, 1, 0) if False else m.dyadic_to_f32(sgn, gm2, ge2))
        except Exception: words.append(None)
    hw = d["AB"]
    if tuple(words) == hw: match += 1
    else:
        mismatch += 1
        if len(ex) < 10:
            fmt=lambda w: "None" if w is None else format(w,'08x')
            ex.append(f"s{st} o{od} basis{v}: hw=({fmt(hw[0])},{fmt(hw[1])}) model=({fmt(words[0])},{fmt(words[1])})")
print(f"basis gradient words: {match} match, {mismatch} mismatch")
for e in ex: print("  ", e)
