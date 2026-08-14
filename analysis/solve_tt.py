"""Per-geometry joint (t, t') solve from all 6 A/B words (3 channels)."""
import sys, json, itertools, pickle
sys.path[:0] = ["/tmp/walle"]
from pathlib import Path
from fractions import Fraction
import numpy as np
import _sweep_fused_join_lattice as m
import _solve_clip_varyings as sv
import _joint_stage_sweep as js
f32 = np.float32

D = Path("/tmp/walle/build/analysis-agx-basis/t-readback-plan-v1")
PLAN = json.loads((D / "reveal-agx-setup-accumulator-plan.json").read_text())
T = m.load_records(D / "capture-words.raw", len(PLAN["draws"]))

def snap(v): return int(np.floor(f32(float(v)) * 256.0 + 0.5))
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
def sel_oa(mand, me, sl, sle):
    prod = mand*sl; bits = prod.bit_length(); shift = bits-27
    if bits <= 32:
        if shift <= 0: return prod, me+sle
        idx = (prod + (1 << (shift-1))) >> shift
        if idx.bit_length() > 27: idx >>= 1; shift += 1
        return idx, me+sle+shift
    Tc = max(0, mand.bit_length() - 8)
    return (js.pps(mand, sl, Tc) + (20 << Tc)) >> shift, me+sle+shift
def q27_halfup_frac(v):
    if v == 0: return (0, 0)
    e = v.numerator.bit_length() - v.denominator.bit_length()
    if v < Fraction(2)**e: e -= 1
    step = Fraction(2)**(e - 26)
    q = v / step
    fl = q.numerator // q.denominator
    fr = q - fl
    mant = fl + (1 if fr >= Fraction(1,2) else 0)
    if mant >= 1 << 27: mant >>= 1; e += 1
    return (mant, e - 26)
def fused_AB(fx, vals):
    det = ((fx[1][0]-fx[0][0])*(fx[2][1]-fx[0][1])
           - (fx[1][1]-fx[0][1])*(fx[2][0]-fx[0][0]))
    if det == 0: return None
    sel, se = sv.selector_for(abs(det))
    ds = -1 if det < 0 else 1
    anchor = min(range(3), key=lambda i: (fx[i][1], fx[i][0]))
    out = []
    for axis in range(2):
        parts = []
        for v in range(3):
            if v == anchor: continue
            a2, b2 = (v+1)%3, (v+2)%3
            e_int = fx[a2][1]-fx[b2][1] if axis == 0 else fx[b2][0]-fx[a2][0]
            delta = vals[v] - vals[anchor]
            edge = Fraction(float(f32(e_int/256.0)))
            if delta == 0 or edge == 0: continue
            mant, ex = q27_halfup_frac(abs(delta * edge))
            sign = -1 if (delta < 0) != (edge < 0) else 1
            parts.append((sign*mant, ex))
        if not parts:
            out.append(0); continue
        gmin = min(g for _, g in parts)
        tot = sum(v << (g-gmin) for v, g in parts)
        if tot == 0: out.append(0); continue
        s = 1 if tot > 0 else -1
        mag, g28 = tap_rne(abs(tot), gmin, 28)
        idx, e2 = sel_oa(mag, g28, sel, se)
        sh = idx.bit_length()-24
        if sh > 0:
            idx = rne_int(idx, sh); e2 += sh
            if idx.bit_length() > 24: idx >>= 1; e2 += 1
        try: out.append(m.dyadic_to_f32(s*ds, idx, e2))
        except Exception: out.append(None)
    return tuple(out)

geo_hw = {}; geo_pos = {}
for exp, draw in zip(PLAN["experiments"], PLAN["draws"]):
    gi = exp["geometryIndex"]
    if gi in geo_hw: continue
    r = exp["recordIndex"]
    geo_hw[gi] = [(int(T[r][c][0]), int(T[r][c][1])) for c in range(3)]
    geo_pos[gi] = [tuple(p) for p in exp["positions"]]

LIM = int(sys.argv[1]) if len(sys.argv) > 1 else 12
STEP = Fraction(1, 1 << 28)
R = 20
OUT = {}
count = 0
for gi in sorted(geo_hw):
    if count >= LIM: break
    count += 1
    P = geo_pos[gi]
    V = [(Fraction(p[0]), Fraction(p[1])) for p in P]
    c = Fraction(-512)
    # crossing edges: V0->V2 (t) and V1->V2 (t')
    t0 = (c - V[0][1]) / (V[2][1] - V[0][1])
    t1 = (c - V[1][1]) / (V[2][1] - V[1][1])
    cut0 = (V[0][0] + t0*(V[2][0]-V[0][0]), c)
    cut1 = (V[1][0] + t1*(V[2][0]-V[1][0]), c)
    poly = [(V[0], 0), (V[1], 1), (cut1, ("c1",)), (cut0, ("c0",))]
    spts = [(snap(e[0][0]), snap(e[0][1])) for e in poly]
    hws = geo_hw[gi]
    sols = []
    for da in range(-R, R+1):
        ta = t0 + da*STEP
        for db in range(-R, R+1):
            tb = t1 + db*STEP
            def val(tag, ctx):
                if isinstance(tag, int):
                    return Fraction(1) if tag == ctx else Fraction(0)
                if tag[0] == "c0":
                    return (Fraction(1)-ta) if ctx == 0 else (ta if ctx == 2 else Fraction(0))
                return (Fraction(1)-tb) if ctx == 1 else (tb if ctx == 2 else Fraction(0))
            good = True
            for ctx in range(3):
                hw = hws[ctx]
                found = False
                for tri in itertools.combinations(range(4), 3):
                    fx3 = [spts[j] for j in tri]
                    vals = [val(poly[j][1], ctx) for j in tri]
                    if fused_AB(fx3, vals) == hw:
                        found = True; break
                if not found: good = False; break
            if good: sols.append((da, db))
    OUT[gi] = {"t0": t0, "t1": t1, "sols": sols,
               "den0": float(V[2][1]-V[0][1]), "den1": float(V[2][1]-V[1][1])}
    print(f"g{gi}: {len(sols)} joint solutions; sample {sols[:4]}", flush=True)
pickle.dump(OUT, open("/tmp/walle/build/_tt_solutions.pkl","wb"))
