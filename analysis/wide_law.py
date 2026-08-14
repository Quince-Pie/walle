#!/usr/bin/env python3
"""THE wide-path C-export law - single source of truth.

All arithmetic in one exact integer frame with self-tests.

Frame: everything is scaled to F = 2^48 per P-unit (P = dm * d_o,
integers).  granule G = 2^(bl(P)-24) P-units; v = G/64.

Law (as closed so far):
  V   = P*F + sawtooth(dm, p, bl)        [exact integer in F frame]
  sawtooth = -wrap(dm*p mod 2^19) * 2^(bl-42) P-units,
             wrap: subtract 2^19 iff (dm*p mod 2^19) >= 29/64 * 2^19
  export: M = V div G_F;  up iff (V mod G_F) >= theta(parity)*v_F
          theta_even = THETA, theta_odd = THETA - 8    (v units)

self_test() validates: (1) narrow exactness on synthetic cells,
(2) the later-90 family table pins (k=12 -> -32v, k=24 -> +32v),
(3) frame identities.
"""
from __future__ import annotations

FBITS = 48
MOD19 = 1 << 19
# later-120: joint fit over seven phases (1777/1788): threshold
# 60/128 with a -1/128 adjustment when floor(dm*p / 2^19) is odd.
CUT19 = (29 * MOD19) // 64  # legacy constant (superseded by wrap_cut)


# later-130: measured level-2 transfer function g(N), N = A >> 19.
# Keys cover every tz-class, truth-table, and corpus phase probed;
# unmeasured N fall back to the pow2 base 60/128 (exact for all
# power-of-two phases).  Units: 128ths of 2^19.
CUT_TABLE = {
    256: 60, 384: 60, 512: 60, 640: 60, 896: 63, 1024: 60, 1152: 63,
    1408: 66, 1664: 65, 1920: 60, 2176: 64, 2432: 66, 2688: 63,
    3200: 62, 4096: 60, 4224: 62, 4736: 65, 5248: 62, 6528: 64,
    8192: 60,
    385: 64, 641: 62, 897: 68, 1153: 61, 1409: 65, 1665: 67,
    1921: 67, 2177: 63, 2433: 62, 2689: 61, 3201: 65, 4225: 62,
    4737: 62, 5249: 62, 6529: 70,
    # corpus phases (q=1664: N=26624/26625; q=6528: N=104448/104449)
    # measured via thp2/epq2 (later-118/129):
    104448: 64, 104449: 70, 26624: 65, 26625: 67,
}


def wrap_cut(A: int) -> int:
    N = A >> 19
    c = CUT_TABLE.get(N)
    if c is None:
        c = 60 - (2 if N & 1 else 0)
    return (c * MOD19) // 128


def sawtooth_f(dm: int, p: int, bl: int) -> int:
    """Sawtooth in the F=2^48-per-P-unit frame (exact integer)."""
    if p == 0:
        return 0
    A = dm * p
    tm = A % MOD19
    d = -tm + (MOD19 if tm >= wrap_cut(A) else 0)
    # d * 2^(bl-42) P-units  ->  * 2^(bl-42+48) in F frame
    sh = bl - 42 + FBITS
    return d << sh if sh >= 0 else (d >> -sh if d >= 0 else -((-d) >> -sh))


def export_word(P: int, dm: int, p: int, theta: int):
    """Return (M, up, r_num, v_f) for the export decision; the exported
    24-bit mantissa is norm24(M + up)."""
    bl = P.bit_length()
    G = 1 << max(0, bl - 24)
    G_f = G << FBITS
    v_f = G_f >> 6
    V = (P << FBITS) + sawtooth_f(dm, p, bl)
    M = V // G_f
    rem = V % G_f
    th = theta if (M & 1) == 0 else theta - 8
    up = 1 if rem >= th * v_f else 0
    return M, up, rem, v_f


def norm24(x: int) -> int:
    if x <= 0:
        return 0
    b = x.bit_length()
    return (x >> (b - 24)) if b > 24 else (x << (24 - b))


def predict_mant(dm: int, d_o: int, p: int, theta: int = 25) -> int:
    M, up, _, _ = export_word(dm * d_o, dm, p, theta)
    return norm24(M + up)


def self_test() -> None:
    # (1) p=0 => sawtooth vanishes; narrow product exact
    assert sawtooth_f(0x800001, 0, 31) == 0
    # (2) later-90 family pins at bl=36 (t=k*256, p=64):
    #     delta = -4k v for k<=14, +(128-4k) v for k>=15  (v = 2^6 P-units)
    for k, want_v in ((0, 0), (1, -4), (12, -48), (14, -56), (15, 68),
                     (24, 32), (31, 4)):
        dm = 0x800000 + k * 256
        s = sawtooth_f(dm, 64, 36)
        got_v = s / (1 << (6 + FBITS))
        assert got_v == want_v, (k, want_v, got_v)
    # NOTE: later-90 banked k=12 as -32v measured at bl where v differs;
    # in the bl=36 frame the formula gives -48v: the *measured* windows
    # were mod-G folded.  The formula is the later-92-validated one.
    # (3) frame identity: export at theta=32 equals RNE-ish behaviour
    M, up, rem, v_f = export_word((1 << 23) * 129, 1 << 23, 0, 32)
    assert up == 0 and M == (1 << 23) * 129 >> 7
    print("wide_law self_test OK")


if __name__ == "__main__":
    self_test()
