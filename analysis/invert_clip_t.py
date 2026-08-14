import sys, json, itertools
sys.path[:0] = ["/tmp/walle"]
from pathlib import Path
from fractions import Fraction
import numpy as np
import _sweep_fused_join_lattice as m
import _solve_clip_varyings as sv
import _joint_stage_sweep as js
f32 = np.float32
_lib = Path("/tmp/nix-shell.PFgUGF/claude-1000/-tmp-walle/4ccfbce8-33b2-4b5f-8e29-93486397c8a4/scratchpad/fit_clip_values.py").read_text()
exec(_lib.split("results = {}")[0])

D = Path("/tmp/walle/build/analysis-agx-basis/dual-lane-sweep-plan-v2")
PLAN = json.loads((D / "reveal-agx-setup-accumulator-plan.json").read_text())
T = m.load_records(D / "capture.raw", len(PLAN["draws"]))

def ulp_shift(x, k):
    if x == 0.0: return 0.0
    b = m.f32_bits(x)
    return m.bits_f32(m.key_to_bits(m.ordered_key(b) + k))

target = ((-560.0, 640.0), (464.0, 640.0), (463.98828125, 1151.99609375))
for exp, draw in zip(PLAN["experiments"], PLAN["draws"]):
    P = tuple(tuple(p) for p in exp["positions"])
    if P != target: continue
    r = exp["recordIndex"]
    hws = [(int(T[r][c][0]), int(T[r][c][1])) for c in range(3)]
    poly = clip_poly(P, "rational")
    pts = [(snap(float(e[0][0])), snap(float(e[0][1]))) for e in poly]
    print("polygon:", [(float(e[0][0]), float(e[0][1])) for e in poly])
    print("snapped:", pts)
    print("hw:", [(f"{a:08x}", f"{b:08x}") for a, b in hws])
    # clip-vertex entries and their rational t
    clipverts = [i for i, e in enumerate(poly) if e[1] is None]
    tvals = {}
    for i in clipverts:
        u, w, t = poly[i][2]
        tvals[i] = (u[1], w[1], t)   # from orig u to orig w
        print(f"clip vertex {i}: from v{u[1]} to v{w[1]} t={float(t):.9f} t_rne={rne_frac(t)!r}")
    # solve: sweep t word offsets for the two clip vertices
    base_t = {i: rne_frac(tvals[i][2]) for i in clipverts}
    sols = []
    for offs in itertools.product(range(-64, 65), repeat=len(clipverts)):
        tw = {i: ulp_shift(base_t[i], o) for i, o in zip(clipverts, offs)}
        # values per ctx at each poly vertex
        def val(i, ctx):
            e = poly[i]
            if e[1] is not None: return 1.0 if e[1] == ctx else 0.0
            u, w, t = e[2]
            vu = 1.0 if u[1] == ctx else 0.0
            vw = 1.0 if w[1] == ctx else 0.0
            tt = tw[i]
            return float(f32(vu + tt * (vw - vu)))  # exact for one-hot: t or 1-t
        for tri in itertools.combinations(range(len(poly)), 3):
            fx3 = [pts[j] for j in tri]
            good = all(
                slope_words(fx3, [val(j, ctx) for j in tri]) == hws[ctx]
                for ctx in range(3))
            if good:
                sols.append((offs, tri))
    print("solutions (t-offsets, triple):", sols[:10], f"({len(sols)} total)")
    break
