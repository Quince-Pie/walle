#!/usr/bin/env python3
"""Track B: tz-gated, granule-relative compensation.

The two halves of the datapath answer to different frames:

* WHETHER a compensation happens is an ABSOLUTE property of the subpixel
  frame -- the array drops columns below a fixed bit T of dm * disp, so it
  only bites when the displacement has fewer than T trailing zeros.
  tt3's displacements carry 13 trailing zeros, tt1's carry 7, tt4's carry 6.
* HOW MUCH it is worth is a RESULT-RELATIVE quantity, `K * 2^(bl(P)-30)`,
  because the constant is injected on the normaliser side.

That split is forced by the captures.  A purely absolute injection cannot
reproduce the measured bias, which scales with the output granule across
bl=31..36 (a factor of 32).  A purely result-relative rule gated on
bl(P) > 30 cannot be right either: tt1 has 8 distinct cells (16 with the
0.5x twins) at bl(P) = 27..30 that deviate from the narrow law, while tt3 is
exact at the same bl -- e.g. dm=0x800004,d_o=51 (bl=29, drop=12/32, D=+1)
and dm=0x80000F,d_o=115 (bl=30, drop=61/64, D=-1).  tt3 simply never probes
d_o > 47, so "narrow law holds for bl(P) <= 30" was an artefact of tt3's
displacement alignment, not a law about product width.

Note the bias is fractional when bl(P) < 30, so scoring is done in a scaled
integer domain (shift left by 30-bl) to stay exact.
"""

from __future__ import annotations

import sys

sys.path[:0] = ["/tmp/walle", "/tmp/walle/analysis"]

import wide_solver_data as W  # noqa: E402
import wide_solver_sweep as S  # noqa: E402


def make_law(T: int, K: int):
    """bias = K * 2^(bl(P)-30), applied iff the subpixel frame is truncated."""
    def law(dm: int, d_o: int, z: int):
        P = dm * d_o
        if z >= T:                     # disp has >= T trailing zeros: no drop
            return W.narrow(P)
        bl = P.bit_length()
        k = max(0, 30 - bl)            # scale up so the bias stays integral
        mant, sh = W.narrow((P << k) + (K << (bl - 30 + k)))
        return mant, sh - k
    return law


def main() -> None:
    print("reference narrow law:      ",
          S.score_all(lambda dm, d_o, z: W.narrow(dm * d_o)))
    print("bl-gated +9*2^(bl-30):     ",
          S.score_all(_blgated))
    print()
    rows = []
    for T in range(7, 14):
        for K in range(0, 26):
            s = S.score_all(make_law(T, K))
            rows.append((sum(s), T, K, s))
    rows.sort(reverse=True)
    print("tz-gated, granule-relative:")
    for tot, T, K, s in rows[:18]:
        mark = ""
        if s == (18001, 18001, 2610):
            mark = "   *** EXACT ***"
        elif s[1] == 18001 and s[0] > 14850 and s[2] > 2300:
            mark = "   <== beats both baselines"
        print(f"  T={T:2d} K={K:2d}  tt4 {s[0]:5d}  tt3 {s[1]:5d}  "
              f"tt1 {s[2]:4d}  total {tot}{mark}")


def _blgated(dm, d_o, z):
    P = dm * d_o
    sh = P.bit_length() - 30
    return W.narrow(P + (9 << sh) if sh > 0 else P)


if __name__ == "__main__":
    main()
