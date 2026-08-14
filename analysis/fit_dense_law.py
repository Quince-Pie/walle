import sys, pickle
sys.path[:0] = ["/tmp/walle"]
import numpy as np
import _sweep_fused_join_lattice as m
import _joint_stage_sweep as js
f32 = np.float32

cases = []
for name in ("_dls_rows", "_dls2_rows"):
    rows = pickle.load(open(f"/tmp/walle/build/{name}.pkl","rb"))
    for row in rows:
        if name == "_dls_rows":
            det, sel, se, ds, e_pair, hw = row
            off = False
        else:
            _, off, det, sel, se, ds, e_pair, hw, P = row
        if off: continue          # on-screen only: pure product law
        for e_int, w_hw in zip(e_pair, hw):
            if e_int == 0: continue
            cases.append((e_int, sel, se, ds, w_hw))
# dedupe
cases = sorted(set(cases))
print(len(cases), "distinct (edge, sel) measurements")

def rne_int(v, sh):
    if sh <= 0: return v
    q = v >> sh; r = v - (q << sh); h = 1 << (sh-1)
    return q + (1 if (r > h or (r == h and q & 1)) else 0)

def word(mev, ee, sel, se, sgn, stage):
    idx, e = stage(mev, ee, sel, se)
    sh = idx.bit_length() - 24
    i24 = rne_int(idx, sh); e24 = e + max(sh, 0)
    if i24.bit_length() > 24: i24 >>= 1; e24 += 1
    try: return m.dyadic_to_f32(sgn, i24, e24)
    except Exception: return None

def make_stage(T_of, bias, use_carry):
    def stage(mev, ee, sel, se):
        prod = mev * sel
        bits = prod.bit_length(); shift = bits - 27
        Tc = T_of(mev, sel, bits)
        c = js.carry_top(mev, sel, Tc) if use_carry else 0
        val = js.pps(mev, sel, Tc) + ((bias + c) << Tc)
        idx = val >> shift if shift > 0 else val
        return idx, ee + se + max(shift, 0)
    return stage

def test(name, stage):
    ok = bad = 0
    worst = 0
    for e_int, sel, se, ds, hw in cases:
        edge = float(f32(e_int/256.0))
        _, mev, ee = m.f32_parts(m.f32_bits(abs(edge)))
        sgn = (1 if e_int > 0 else -1) * ds
        w = word(mev, ee, sel, se, sgn, stage)
        if w == hw: ok += 1
        else:
            bad += 1
            if w is not None:
                d = abs((hw & 0x7fffffff) - (w & 0x7fffffff))
                worst = max(worst, d)
    print(f"{name}: {ok}/{ok+bad} exact (worst miss {worst} ulps)")

test("banked (T=mand-8=16, +20)", make_stage(lambda mv,s,b: max(0, mv.bit_length()-8), 20, False))
test("T=16 +20 +carry", make_stage(lambda mv,s,b: 16, 20, True))
test("T=prod-33", make_stage(lambda mv,s,b: max(0, b-33), 20, False))
test("T=prod-35", make_stage(lambda mv,s,b: max(0, b-35), 20, False))
test("T=sel-8", make_stage(lambda mv,s,b: max(0, s.bit_length()-8), 20, False))
# swap operand order in pps (array indexed by the selector instead of edge)
def make_stage_swap(T_of, bias, use_carry):
    def stage(mev, ee, sel, se):
        prod = mev * sel
        bits = prod.bit_length(); shift = bits - 27
        Tc = T_of(mev, sel, bits)
        c = js.carry_top(sel, mev, Tc) if use_carry else 0
        val = js.pps(sel, mev, Tc) + ((bias + c) << Tc)
        idx = val >> shift if shift > 0 else val
        return idx, ee + se + max(shift, 0)
    return stage
test("SWAP pps(sel,mev) T=16 +20", make_stage_swap(lambda mv,s,b: 16, 20, False))
test("SWAP T=sel-8 +20", make_stage_swap(lambda mv,s,b: max(0, s.bit_length()-8), 20, False))
test("SWAP T=17 +20", make_stage_swap(lambda mv,s,b: 17, 20, False))
test("SWAP T=16 +20 +carry", make_stage_swap(lambda mv,s,b: 16, 20, True))
