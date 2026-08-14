import sys, pickle
sys.path[:0] = ["/tmp/walle"]
from fractions import Fraction
import numpy as np
f32 = np.float32
RES = pickle.load(open("/tmp/walle/build/_prod_t_windows.pkl","rb"))
STEP = Fraction(1, 1 << 27)
R = 160

def exact_t(V, key):
    (ptag, qtag, axis, cstr) = key
    c = Fraction(cstr)
    def pos(tag):
        kind, arg = tag
        if kind == "orig": return V[arg]
        raise ValueError("compound cut")
    p, q = pos(ptag), pos(qtag)
    return (c - p[axis]) / (q[axis] - p[axis]), p, q, axis, c

def models(p, q, axis, c):
    out = {}
    pn = float(p[axis]); qn = float(q[axis]); cf = float(c)
    n = float(f32(cf - pn)); d = float(f32(qn - pn))
    out["f32div"] = Fraction(float(f32(n / d)))
    d0 = float(f32(cf - pn)); d1 = float(f32(cf - qn))
    out["dist_f32"] = Fraction(float(f32(d0 / float(f32(d0 - d1)))))
    # ndc form (axis-dependent transform; x: (2/2048)x-1, y: -(2/2048)y+1; boundary +-1.5)
    if axis == 0:
        tp = float(f32(f32(pn) * f32(2/2048) + f32(-1)))
        tq = float(f32(qn * f32(2/2048) + f32(-1)))
        cb = -1.5 if c < 0 else 1.5
    else:
        tp = float(f32(f32(pn) * f32(-2/2048) + f32(1)))
        tq = float(f32(qn * f32(-2/2048) + f32(1)))
        cb = 1.5 if c < 0 else -1.5
    nn = float(f32(cb - tp)); dd = float(f32(tq - tp))
    out["ndc_f32"] = Fraction(float(f32(nn / dd)))
    out["ndc_wide"] = Fraction(nn) / Fraction(dd)
    return out

score = {}
detail = []
for (st, ordn), ent in sorted(RES.items()):
    sols = ent["sols"]
    if not sols: continue
    V = ent["V"]
    for ki, key in enumerate(ent["keys"]):
        axisvals = sorted(set(s[ki] for s in sols))
        lo, hi = axisvals[0], axisvals[-1]
        if lo <= -R+4 or hi >= R-4:   # unbounded => uninformative
            continue
        try: te, p, q, axis, c = exact_t(V, key)
        except ValueError: continue
        w_lo, w_hi = te + lo*STEP, te + hi*STEP
        row = [f"s{st} o{ordn} k{ki} window [{lo},{hi}]"]
        for name, tv in models(p, q, axis, c).items():
            inside = w_lo <= tv <= w_hi
            score[name] = score.get(name, [0,0])
            score[name][0] += inside; score[name][1] += 1
            row.append(f"{name}:{'IN' if inside else 'out'}")
        ex_in = w_lo <= te <= w_hi
        score.setdefault("exact", [0,0])
        score["exact"][0] += ex_in; score["exact"][1] += 1
        row.append(f"exact:{'IN' if ex_in else 'out'}")
        detail.append(" ".join(row))
for d in detail: print(d)
print("totals:", {k: tuple(v) for k, v in score.items()})
