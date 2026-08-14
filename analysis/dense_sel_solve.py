import sys, pickle
sys.path[:0] = ["/tmp/walle"]
from collections import defaultdict
import numpy as np
import _sweep_fused_join_lattice as m
import _joint_stage_sweep as js
f32 = np.float32
groups = defaultdict(list)
for name in ("_dls_rows", "_dls2_rows"):
    rows = pickle.load(open(f"/tmp/walle/build/{name}.pkl","rb"))
    for row in rows:
        if name == "_dls_rows":
            det, sel, se, ds, e_pair, hw = row; off = False
        else:
            _, off, det, sel, se, ds, e_pair, hw, P = row
        if off: continue
        for e_int, w_hw in zip(e_pair, hw):
            if e_int == 0: continue
            groups[(det, sel, se, ds)].append((e_int, w_hw))
def rne_int(v, sh):
    if sh <= 0: return v
    q = v >> sh; r = v - (q << sh); h = 1 << (sh-1)
    return q + (1 if (r > h or (r == h and q & 1)) else 0)
def word(e_int, sel, se, ds):
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
solved = unsolved = clean = 0
offsets = defaultdict(int)
unsolved_ex = []
for (det, sel, se, ds), meas in groups.items():
    meas = sorted(set(meas))
    if all(word(e, sel, se, ds) == hw for e, hw in meas):
        clean += 1; continue
    found = None
    for delta in sorted(range(-600, 601), key=abs):
        if all(word(e, sel + delta, se, ds) == hw for e, hw in meas):
            found = delta; break
    if found is not None:
        solved += 1; offsets[found] += 1
    else:
        unsolved += 1
        if len(unsolved_ex) < 3: unsolved_ex.append((det, sel, [ (e, f"{hw:08x}") for e,hw in meas[:3]]))
print(f"geometries: clean {clean}, sel-offset-solved {solved}, unsolved {unsolved}")
print("offset histogram:", dict(sorted(offsets.items(), key=lambda kv:-kv[1])[:12]))
for u in unsolved_ex: print("  unsolved:", u)
