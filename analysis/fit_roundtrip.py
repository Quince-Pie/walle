import sys, pickle, json
sys.path[:0] = ["/tmp/walle"]
from pathlib import Path
import numpy as np
import _sweep_fused_join_lattice as m
import _solve_clip_varyings as sv
import _joint_stage_sweep as js
f32 = np.float32

# rebuild rows from plans WITH positions (dls_rows lacks positions; re-extract v1)
def extract(plandir, capname="capture.raw"):
    D = Path(f"/tmp/walle/build/analysis-agx-basis/{plandir}")
    PLAN = json.loads((D / "reveal-agx-setup-accumulator-plan.json").read_text())
    T = m.load_records(D / capname, len(PLAN["draws"]))
    out = []
    for exp, draw in zip(PLAN["experiments"], PLAN["draws"]):
        r = exp["recordIndex"]
        P = [tuple(p) for p in exp["positions"]]
        out.append((P, [(int(T[r][c][0]), int(T[r][c][1])) for c in range(3)]))
    return out

def snap_rne(v):
    s = v * 256.0
    f = np.floor(s); r = s - f
    if r > 0.5 or (r == 0.5 and (int(f) & 1)): f += 1
    return int(f)
def snap_half_up(v):
    return int(np.floor(v * 256.0 + 0.5))

def roundtrip(p, mode):
    x, y = f32(p[0]), f32(p[1])
    if mode == "direct":
        return float(x), float(y)
    xc = f32(x * f32(2/2048) + f32(-1))
    yc = f32(y * f32(-2/2048) + f32(1))
    if mode == "rt":
        xb = f32(xc + f32(1)); yb = f32(f32(1) - yc)
        return float(f32(xb * 1024)), float(f32(yb * 1024))
    if mode == "rt_fma":  # viewport via fma: px = xc*1024 + 1024
        return float(f32(xc * f32(1024) + f32(1024))), float(f32(yc * f32(-1024) + f32(1024)))
    raise ValueError

def rne_int(v, sh):
    if sh <= 0: return v
    q = v >> sh; r = v - (q << sh); h = 1 << (sh-1)
    return q + (1 if (r > h or (r == h and q & 1)) else 0)
def per_edge_word(e_int, sel, se, ds):
    if e_int == 0: return 0
    edge = float(f32(e_int/256.0))
    _, mev, ee = m.f32_parts(m.f32_bits(abs(edge)))
    prod = mev * sel
    bits = prod.bit_length(); shift = bits - 27
    if bits <= 32:
        idx = (prod + (1 << (shift-1))) >> shift if shift > 0 else prod
        if idx.bit_length() > 27: idx >>= 1; shift += 1
        e = ee + se + max(shift, 0)
    else:
        Tc = max(0, mev.bit_length() - 8)
        idx = (js.pps(mev, sel, Tc) + (20 << Tc)) >> shift
        e = ee + se + shift
    sh = idx.bit_length() - 24
    i24 = rne_int(idx, sh); e24 = e + max(sh, 0)
    if i24.bit_length() > 24: i24 >>= 1; e24 += 1
    sgn = (1 if e_int > 0 else -1) * ds
    try: return m.dyadic_to_f32(sgn, i24, e24)
    except Exception: return None

data = extract("dual-lane-sweep-plan-v1") + extract("dual-lane-sweep-plan-v2")
for mode in ("direct", "rt", "rt_fma"):
    for snap in (snap_rne, snap_half_up):
        ok = bad = 0
        for P, hws in data:
            if any(p[0] < 0 or p[1] < 0 or p[0] >= 2048 or p[1] >= 2048 for p in P):
                continue
            pts = [roundtrip(p, mode) for p in P]
            fx = [(snap(px), snap(py)) for px, py in pts]
            det = ((fx[1][0]-fx[0][0])*(fx[2][1]-fx[0][1])
                   - (fx[1][1]-fx[0][1])*(fx[2][0]-fx[0][0]))
            if det == 0: continue
            sel, se = sv.selector_for(abs(det))
            ds = -1 if det < 0 else 1
            for ctx in range(3):
                a2, b2 = (ctx+1)%3, (ctx+2)%3
                e_pair = (fx[a2][1]-fx[b2][1], fx[b2][0]-fx[a2][0])
                for e_int, hw in zip(e_pair, hws[ctx]):
                    if e_int == 0 and hw == 0: ok += 1; continue
                    w = per_edge_word(e_int, sel, se, ds)
                    if w == hw: ok += 1
                    else: bad += 1
        print(f"{mode}/{snap.__name__}: {ok} exact, {bad} miss")
