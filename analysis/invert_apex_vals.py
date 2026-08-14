import sys, json, itertools, pickle
sys.path[:0] = ["/tmp/walle"]
from pathlib import Path
from fractions import Fraction
import numpy as np
import _sweep_fused_join_lattice as m
import _solve_clip_varyings as sv
import _joint_stage_sweep as js
f32 = np.float32
exec(Path("/tmp/nix-shell.PFgUGF/claude-1000/-tmp-walle/4ccfbce8-33b2-4b5f-8e29-93486397c8a4/scratchpad/solve_tt.py").read_text().split("geo_hw = {}")[0])

D = Path("/tmp/walle/build/analysis-agx-basis/apex-x-sweep-plan-v1")
PLAN = json.load(open(D / "reveal-agx-setup-accumulator-plan.json"))
T = m.load_records(D / "capture.raw", len(PLAN["draws"]))
geo_hw = {}; geo_pos = {}
for exp, draw in zip(PLAN["experiments"], PLAN["draws"]):
    gi = exp["geometryIndex"]
    if gi in geo_hw: continue
    r = exp["recordIndex"]
    geo_hw[gi] = [(int(T[r][c][0]), int(T[r][c][1])) for c in range(3)]
    geo_pos[gi] = [tuple(p) for p in exp["positions"]]

STEP = Fraction(1, 1 << 27)
OUT = {}
count = 0
for gi, hws in sorted(geo_hw.items()):
    P = geo_pos[gi]
    if P[2][1] != -715.0: continue
    count += 1
    if count > 60: break
    V = [(Fraction(p[0]), Fraction(p[1])) for p in P]
    c = Fraction(-512)
    t0 = (c - V[0][1]) / (V[2][1] - V[0][1])
    t1 = (c - V[1][1]) / (V[2][1] - V[1][1])
    cut0 = (V[0][0] + t0*(V[2][0]-V[0][0]), c)
    cut1 = (V[1][0] + t1*(V[2][0]-V[1][0]), c)
    poly = [(V[0], 0), (V[1], 1), (cut1, "c1"), (cut0, "c0")]
    spts = [(snap(e[0][0]), snap(e[0][1])) for e in poly]
    hw = hws[2]
    def test(da, db):
        va = t0 + da*STEP; vb = t1 + db*STEP
        vals4 = [Fraction(0), Fraction(0), vb, va]
        for tri in itertools.combinations(range(4), 3):
            fx3 = [spts[j] for j in tri]
            if fused_AB(fx3, [vals4[j] for j in tri]) == hw:
                return True
        return False
    hits = []
    for da in range(-48, 49, 4):
        for db in range(-48, 49, 4):
            if test(da, db):
                hits.append((da, db))
    fine = set()
    for da0, db0 in hits:
        for da in range(da0-3, da0+4):
            for db in range(db0-3, db0+4):
                if test(da, db): fine.add((da, db))
    OUT[gi] = {"x2": float(P[2][0]), "sols": sorted(fine),
               "t0": t0, "t1": t1}
    das = sorted(set(d for d,_ in fine)); dbs = sorted(set(d for _,d in fine))
    print(f"g{gi} x2={float(P[2][0]):9.3f}: {len(fine)} sols "
          f"da {das[:1]}..{das[-1:]} db {dbs[:1]}..{dbs[-1:]}" if fine else
          f"g{gi} x2={float(P[2][0]):9.3f}: none", flush=True)
pickle.dump(OUT, open("/tmp/walle/build/_apex_val_sols.pkl","wb"))
