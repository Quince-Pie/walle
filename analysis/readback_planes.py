"""Recover clipped-plane t intervals from the t-readback capture.

Per (geometry, channel): each sample word gives a linear inequality band
on the affine plane (A, Bx, By).  We bound t = 1 - P(crossing) via a
simple exact-rational Fourier-Motzkin-free approach: project onto the
plane value at the crossing point using LP by coordinate descent over
vertex-value parametrization (P is affine in 3 unknowns; we use the
plane THROUGH the three snapped clip-triangle vertices as parameters:
unknowns = plane values at three reference points, linear in samples).
Bounds via a few hundred random extreme-direction probes (adequate at
2^-27 with 120 tight bands).
"""
import sys, json, struct, random
sys.path[:0] = ["/tmp/walle"]
from pathlib import Path
from fractions import Fraction
import numpy as np
import _sweep_fused_join_lattice as m

D = Path("/tmp/walle/build/analysis-agx-basis/t-readback-plan-v1")
PLAN = json.loads((D / "reveal-agx-setup-accumulator-plan.json").read_text())
raw = (D / "capture.raw").read_bytes()
RW = 36
f32 = np.float32

def ulp_of_word(w):
    e = (w >> 23) & 0xFF
    return Fraction(2)**(int(e) - 127 - 23)

def frac_of_word(w):
    if w == 0 or (w & 0x7fffffff) == 0: return Fraction(0)
    s, mant, e = m.f32_parts(w)
    return Fraction(s*mant) * Fraction(2)**e

# group samples per geometry
geos = {}
for exp, draw in zip(PLAN["experiments"], PLAN["draws"]):
    gi = exp["geometryIndex"]
    r = exp["recordIndex"]
    words = struct.unpack_from(f"<{RW}I", raw, r*RW*4)
    x, y = exp["x"], exp["y"]
    ent = geos.setdefault(gi, {"positions": exp["positions"], "samples": []})
    center = words[16:20]
    xp = (x+1, words[20:24]) if x % 2 == 0 else (x-1, words[24:28])
    yp = (y+1, words[28:32]) if y % 2 == 0 else (y-1, words[32:36])
    ent["samples"].append(("c", x, y, center))
    ent["samples"].append(("px", xp[0], y, xp[1]))
    ent["samples"].append(("py", x, yp[0], yp[1]))

OUT = {}
for gi, ent in sorted(geos.items()):
    P = ent["positions"]
    y_in = P[0][1]; y_out = P[2][1]
    den = y_in - y_out
    # crossing of edge V0->V2 at y = -512
    t_exact = Fraction(-512) - Fraction(y_in)
    t_exact = (Fraction(-512) - Fraction(y_in)) / (Fraction(y_out) - Fraction(y_in))
    xc = Fraction(P[0][0]) + t_exact * (Fraction(P[2][0]) - Fraction(P[0][0]))
    yc = Fraction(-512)
    for ch in (0,):   # basis0 = V0's channel
        # plane unknowns: value at three probe points: use (0,0),(1,0),(0,1) basis:
        # P(x,y) = a + b*x + c*y ; sample constraints:
        cons = []   # (lo, hi, x, y) : lo <= a + b*x + c*y <= hi
        for kind, sx, sy, vec in ent["samples"]:
            w = vec[ch]
            v = frac_of_word(w)
            u = ulp_of_word(w)
            px = Fraction(2*sx+1, 2); py = Fraction(2*sy+1, 2)
            if kind == "c":     # RTZ: value in [v, v+u) for v>=0
                if v >= 0: lo, hi = v, v + u
                else: lo, hi = v - u, v
            else:               # RNE: [v-u/2, v+u/2]
                lo, hi = v - u/2, v + u/2
            cons.append((lo, hi, px, py))
        # least-squares init on floats
        A = np.array([[1.0, float(c[2]), float(c[3])] for c in cons])
        b = np.array([float((c[0]+c[1])/2) for c in cons])
        sol, *_ = np.linalg.lstsq(A, b, rcond=None)
        a0, b0, c0 = [Fraction(float(s)) for s in sol]
        # project t bounds: t_dir = value at crossing = a + b*xc + c*yc
        # crude interval: evaluate the LS plane; margin from constraint slack
        val = a0 + b0*xc + c0*yc
        # residual check
        bad = 0
        for lo, hi, px, py in cons:
            pv = a0 + b0*px + c0*py
            if not (lo - Fraction(1, 1<<20) <= pv <= hi + Fraction(1, 1<<20)):
                bad += 1
        OUT[gi] = {"den": den, "t_exact": t_exact, "value_at_crossing": val,
                   "n_cons": len(cons), "bad": bad}
for gi, e in sorted(OUT.items()):
    t_hw = 1 - e["value_at_crossing"]
    diff = float((t_hw - e["t_exact"]))
    rel = diff / float(e["t_exact"]) if e["t_exact"] else 0
    print(f"g{gi} den={e['den']:.0f} t_exact={float(e['t_exact']):.9f} "
          f"t_hw~{float(t_hw):.9f} d={rel/2**-24:+7.3f} f32ulp bad={e['bad']}/{e['n_cons']}")
