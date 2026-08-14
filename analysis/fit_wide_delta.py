import sys
sys.path[:0] = ["/tmp/walle"]
from fractions import Fraction
import numpy as np
import _sweep_fused_join_lattice as m
import _solve_clip_varyings as sv
import _joint_stage_sweep as js
f32 = np.float32

# geometry triple (cv0, v1, cv3)
fx3 = [(0, 163840), (118784, 163840), (0, 235520)]
det = (fx3[1][0]-fx3[0][0])*(fx3[2][1]-fx3[0][1]) - (fx3[1][1]-fx3[0][1])*(fx3[2][0]-fx3[0][0])
sel, se = sv.selector_for(abs(det))
ds = 1
# exact rational t values
t0 = Fraction(560, 1024)                       # v0->v1 crossing x=0: (0-(-560))/(464-(-560))
x2, y2 = Fraction(46399, 100), None
# v2->v0 crossing x=0: t = (0 - 463.98828125)/(-560 - 463.98828125)
xa = Fraction(46398828125, 10**8)   # 463.98828125 hmm ensure exact: 463.98828125 = 463 + 253/256
xa = Fraction(463) + Fraction(253, 256)
t3 = (0 - xa) / (Fraction(-560) - xa)
# channel values (exact rational)
vals_exact = [
    (1-t0, Fraction(0), t3),
    (t0,   Fraction(1), Fraction(0)),
    (Fraction(0), Fraction(0), 1-t3),
]
hws = [(0xba800000, 0xb2c00060), (0x3a800002, 0xbaffffc0), (0xaf4971bc, 0x3b000040)]

def rne_to_width(v, W):
    """round |v| to a W-bit significand; return (sign, mant, exp)."""
    if v == 0: return (0, 0, 0)
    s = -1 if v < 0 else 1
    av = abs(v); e = 0
    while av >= 2: av /= 2; e += 1
    while av < 1: av *= 2; e -= 1
    scaled = av * 2**(W-1)
    mant = int(scaled); fr = scaled - mant
    if fr > Fraction(1,2) or (fr == Fraction(1,2) and mant & 1): mant += 1
    if mant >= 2**W: mant //= 2; e += 1
    return (s, mant, e - (W-1))

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

def slope_from_delta(delta_exact, e_int, W, prodW):
    if delta_exact == 0 or e_int == 0: return 0
    s, md, de = rne_to_width(delta_exact, W)
    edge = float(f32(e_int/256.0))
    _, mev, ee = m.f32_parts(m.f32_bits(abs(edge)))
    sign = -1 if (s < 0) != (e_int < 0) else 1
    prod = md * mev
    bits = prod.bit_length(); shift = bits - prodW
    if shift > 0:
        t2 = (prod + (1 << (shift-1))) >> shift
        if t2.bit_length() > prodW: t2 >>= 1; shift += 1
        g = de + ee + shift
    else:
        t2, g = prod, de + ee
    mag, g28 = tap_rne(t2, g, prodW+1)
    idx, e2 = sel_oa(mag, g28, sel, se)
    sh = idx.bit_length() - 24
    if sh > 0:
        idx = rne_int(idx, sh); e2 += sh
        if idx.bit_length() > 24: idx >>= 1; e2 += 1
    try: return m.dyadic_to_f32(sign*ds, idx, e2)
    except Exception: return None

for W in (24, 25, 26, 27, 28, 30, 32, 36, 40):
    for prodW in (27,):
        good = 0; detail = []
        for ctx in range(3):
            av, v1, v3 = vals_exact[ctx]
            d1, d3 = v1 - av, v3 - av
            A = slope_from_delta(d1, 71680, W, prodW)   # axis0: edge for v1
            B = slope_from_delta(d3, 118784, W, prodW)  # axis1: edge for cv3
            hw = hws[ctx]
            ok = (A == hw[0] and B == hw[1])
            good += ok
            detail.append((ctx, ok, None if A is None else f"{A:08x}", None if B is None else f"{B:08x}"))
        print(f"W={W}: {good}/3", detail)
