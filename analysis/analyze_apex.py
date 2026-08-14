import sys, json, itertools
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

print("x2 | cut0x_frac | per-ctx match (exact-t any-triple)")
from collections import Counter
phase_hit = Counter(); phase_tot = Counter()
for gi, hws in sorted(geo_hw.items()):
    P = geo_pos[gi]
    if P[2][1] != -715.0: continue
    V = [(Fraction(p[0]), Fraction(p[1])) for p in P]
    c = Fraction(-512)
    t0 = (c - V[0][1]) / (V[2][1] - V[0][1])
    t1 = (c - V[1][1]) / (V[2][1] - V[1][1])
    cut0 = (V[0][0] + t0*(V[2][0]-V[0][0]), c)
    cut1 = (V[1][0] + t1*(V[2][0]-V[1][0]), c)
    poly = [(V[0], 0), (V[1], 1), (cut1, ("c1",)), (cut0, ("c0",))]
    spts = [(snap(e[0][0]), snap(e[0][1])) for e in poly]
    matches = []
    for ctx in range(3):
        hw = hws[ctx]
        def val(tag):
            if isinstance(tag, int):
                return Fraction(1) if tag == ctx else Fraction(0)
            if tag[0] == "c0":
                return (1-t0) if ctx == 0 else (t0 if ctx == 2 else Fraction(0))
            return (1-t1) if ctx == 1 else (t1 if ctx == 2 else Fraction(0))
        found = False
        for tri in itertools.combinations(range(4), 3):
            fx3 = [spts[j] for j in tri]
            vals = [val(poly[j][1]) for j in tri]
            if fused_AB(fx3, vals) == hw:
                found = True; break
        matches.append(found)
    cut0x_frac = float(cut0[0]*256 - int(cut0[0]*256))
    subph = round(cut0x_frac, 2)
    phase_tot[subph] += 1
    if matches[2]: phase_hit[subph] += 1
    x2 = float(P[2][0])
    if gi % 8 == 0:
        print(f"{x2:9.3f} | {cut0x_frac:.3f} | {matches}")
print("\nctx2 hit-rate by cut0 x subpixel fraction:")
for ph in sorted(phase_tot):
    print(f"  frac~{ph}: {phase_hit[ph]}/{phase_tot[ph]}")
