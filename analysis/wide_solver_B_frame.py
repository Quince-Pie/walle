#!/usr/bin/env python3
"""Track B: fixed-position injection in the 48-bit dm x didx24 frame.

Model.  The setup multiplier forms frame = dm * didx24, where didx24 is the
odd part of the displacement normalised to 24 bits.  The array omits every
column below a FIXED frame bit T and injects a constant K there.  Writing
`sh_f = 24 - bl(odd(d_o)) - tz(d_o)`, we have `frame = P << sh_f`, so:

    columns below T are all zero  <=>  sh_f > T   -> the truncation is a no-op
    otherwise the injected constant lands at P-bit (T - sh_f)

        C = narrow( P + K * 2^(T - sh_f) )      [bias 0 when sh_f > T]

Why this shape is right, and why it beats the result-relative form
`narrow(P + K*2^(bl(P)-30))`:

* The narrow-path gate is AUTOMATIC, not asserted.  Every tt3 displacement
  has `bl(odd) + tz <= 6`, hence `sh_f >= 18`; with T = 17 no tt3 column is
  ever dropped, so tt3 stays narrow-law exact by construction rather than by
  an ad-hoc `bl(P) <= 30` condition.
* The two forms coincide exactly when `bl(P) = 23 + bl(odd) + tz`, which
  holds throughout tt4 (dm is pinned near 2^23 there) but NOT in tt1, whose
  dm sweeps the whole binade.  When the mantissa product overflows, a fixed
  injection point weighs half as much relative to the result ulp.  That is
  precisely the regime where the result-relative law regressed tt1 below its
  own baseline, so tt1 is the discriminating dataset.
"""

from __future__ import annotations

import sys

sys.path[:0] = ["/tmp/walle", "/tmp/walle/analysis"]

import wide_solver_data as W  # noqa: E402
import wide_solver_sweep as S  # noqa: E402


def frame_shift(d_o: int) -> int:
    """sh_f such that dm*didx24 == (dm*d_o) << sh_f."""
    n, tz = d_o, 0
    while not n & 1:
        n >>= 1
        tz += 1
    return 24 - n.bit_length() - tz


def make_law(T: int, K: int, drop_low: bool = False):
    def law(dm: int, d_o: int, z: int):
        P = dm * d_o
        sh_f = frame_shift(d_o)
        if sh_f > T:
            return W.narrow(P)
        e = T - sh_f
        V = ((P >> e) + K) << e if drop_low else P + (K << e)
        return W.narrow(V)
    return law


def main() -> None:
    print("reference narrow law:",
          S.score_all(lambda dm, d_o, z: W.narrow(dm * d_o)))
    print("result-relative +9*2^(bl-30):",
          S.score_all(lambda dm, d_o, z: _rel(dm * d_o)))
    print()
    rows = []
    for T in range(13, 21):
        for K in range(0, 24):
            for drop in (False, True):
                s = S.score_all(make_law(T, K, drop))
                rows.append((sum(s), T, K, drop, s))
    rows.sort(reverse=True)
    print("top (frame-fixed injection):")
    for tot, T, K, drop, s in rows[:16]:
        tag = " <== tt3 intact" if s[1] == 18001 else ""
        print(f"  T={T:2d} K={K:2d} drop_low={int(drop)}  "
              f"tt4 {s[0]:5d}  tt3 {s[1]:5d}  tt1 {s[2]:4d}  "
              f"total {tot}{tag}")


def _rel(P: int):
    sh = P.bit_length() - 30
    return W.narrow(P + (9 << sh) if sh > 0 else P)


if __name__ == "__main__":
    main()
