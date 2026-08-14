"""Invert t windows for failing clipped sub-children on production geometry.

For each (state, ordinal) crossing the guard band: unknowns = t per
crossing edge (usually 2).  Sweep t offsets around exact; values =
RTZ24(lerp of production endpoint values); positions from probe's clip
(snap of double lerp).  Constraint: BOTH channels' A/B words of every
sub-child must match hw.  Report per-geometry offset windows."""
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

def load_sdf(state):
    out = {}
    for line in Path("/tmp/walle/build/_childgeo_all_residual_states.txt").read_text().splitlines():
        if "CHILDSDF" not in line: continue
        t = line[line.index("CHILDSDF"):].split()
        if int(t[1]) != state: continue
        out[int(t[2])] = [[int(x,16) for x in t[3+4*v:7+4*v]] for v in range(3)]
    return out
def f32v(w): return m.bits_f32(w)

def rtz24_frac(v):
    if v == 0: return v
    neg = v < 0; av = abs(v)
    e = av.numerator.bit_length() - av.denominator.bit_length()
    if av < Fraction(2)**e: e -= 1
    step = Fraction(2)**(e - 23)
    mant = (av / step).numerator // (av / step).denominator
    r = mant * step
    return -r if neg else r

# geometry clip in exact rationals (positions), guard +-512
def clip3(V, vals_pairs, toffs):
    """V: [(x,y) Fractions x3]; vals_pairs: [(s0,s1) Fractions x3];
    toffs: dict edge->Fraction offset added to exact t."""
    poly = [(V[i], vals_pairs[i], ("orig", i)) for i in range(3)]
    planes = ((0, Fraction(-512), 1), (1, Fraction(-512), 1),
              (0, Fraction(2560), -1), (1, Fraction(2560), -1))
    eidx = 0
    for a, c, s in planes:
        out = []
        n = len(poly)
        for i in range(n):
            p = poly[i]; q = poly[(i+1) % n]
            pin = (p[0][a] - c) * s >= 0
            qin = (q[0][a] - c) * s >= 0
            if pin:
                out.append(p)
            if pin != qin:
                t = (c - p[0][a]) / (q[0][a] - p[0][a])
                key = (p[2], q[2], a, str(c))
                t = t + toffs.get(key, Fraction(0))
                pos = [None, None]
                pos[a] = c
                pos[1-a] = p[0][1-a] + t*(q[0][1-a] - p[0][1-a])
                nv = tuple(rtz24_frac(p[1][ch] + t*(q[1][ch]-p[1][ch]))
                           for ch in range(2))
                out.append(((pos[0], pos[1]), nv, ("cut", key)))
        poly = out
        if not poly: break
    return poly

def crossing_keys(V):
    keys = []
    poly = [(V[i], None, ("orig", i)) for i in range(3)]
    planes = ((0, Fraction(-512), 1), (1, Fraction(-512), 1),
              (0, Fraction(2560), -1), (1, Fraction(2560), -1))
    for a, c, s in planes:
        out = []
        n = len(poly)
        for i in range(n):
            p = poly[i]; q = poly[(i+1) % n]
            pin = (p[0][a] - c) * s >= 0
            qin = (q[0][a] - c) * s >= 0
            if pin: out.append(p)
            if pin != qin:
                t = (c - p[0][a]) / (q[0][a] - p[0][a])
                key = (p[2], q[2], a, str(c))
                keys.append(key)
                pos = [None, None]; pos[a] = c
                pos[1-a] = p[0][1-a] + t*(q[0][1-a] - p[0][1-a])
                out.append(((pos[0], pos[1]), None, ("cut", key)))
        poly = out
    return keys

D = Path("/tmp/walle/build/analysis-agx-basis/residual-children-dense-plan-v1")
PLAN = json.load(open(D / "reveal-agx-setup-accumulator-plan.json"))
T = m.load_records(D / "capture.raw", len(PLAN["draws"]))
geo_hw = {}
for exp, draw in zip(PLAN["experiments"], PLAN["draws"]):
    key = (exp["state"], exp["drawOrdinal"])
    if key in geo_hw: continue
    r = exp["recordIndex"]
    geo_hw[key] = [(int(T[r][c][0]), int(T[r][c][1])) for c in range(2)]

TARGETS = [(31,6),(33,6),(34,2),(34,6),(35,2),(39,6),(40,2),(42,2),(42,6),
           (44,6),(45,2),(47,2),(58,4),(58,5),(60,4)]
STEP = Fraction(1, 1 << 27)
RES = {}
for st, ordn in TARGETS:
    sdf = load_sdf(st)
    if ordn not in sdf: continue
    verts = sdf[ordn]
    V = [(Fraction(f32v(v[0])), Fraction(f32v(v[1]))) for v in verts]
    beyond = any(p[0] < -512 or p[0] > 2560 or p[1] < -512 or p[1] > 2560 for p in V)
    if not beyond: continue
    vals = [(Fraction(f32v(v[2])), Fraction(f32v(v[3]))) for v in verts]
    keys = crossing_keys(V)
    hw = geo_hw.get((st, ordn))
    if hw is None: continue
    def test(toffs):
        poly = clip3(V, vals, toffs)
        if len(poly) < 3: return False
        spts = [(int(np.floor(f32(float(e[0][0]))*256.0+0.5)),
                 int(np.floor(f32(float(e[0][1]))*256.0+0.5))) for e in poly]
        for ctx in range(2):
            found = False
            for tri in itertools.combinations(range(len(poly)), 3):
                fx3 = [spts[j] for j in tri]
                vals3 = [poly[j][1][ctx] for j in tri]
                if fused_AB(fx3, vals3) == hw[ctx]:
                    found = True; break
            if not found: return False
        return True
    R = 160
    if len(keys) == 2:
        sols = []
        for a in range(-R, R+1, 4):
            for b in range(-R, R+1, 4):
                if test({keys[0]: a*STEP, keys[1]: b*STEP}):
                    sols.append((a, b))
        fine = set()
        for a0, b0 in sols:
            for a in range(a0-3, a0+4):
                for b in range(b0-3, b0+4):
                    if test({keys[0]: a*STEP, keys[1]: b*STEP}):
                        fine.add((a, b))
        AL = sorted(set(a for a,_ in fine)); BL = sorted(set(b for _,b in fine))
        print(f"s{st} o{ordn}: {len(fine)} sols a[{AL[:1]}..{AL[-1:]}] b[{BL[:1]}..{BL[-1:]}]"
              if fine else f"s{st} o{ordn}: none", flush=True)
        RES[(st, ordn)] = {"keys": keys, "sols": sorted(fine), "V": V, "t_exact": [
            (Fraction(-512) - V[0][1]) / (V[2][1] - V[0][1]) if False else None]}


pickle.dump(RES, open("/tmp/walle/build/_prod_t_windows.pkl","wb"))
print("saved")
