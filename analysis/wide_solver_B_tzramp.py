#!/usr/bin/env python3
"""Track B: sawtooth whose modulus is set by a fixed window in the SUBPIXEL frame.

The dm-indexed ramp is provably under-parameterised (see B6: at dm=0x801000
tt4 needs c in [36.0, 91.5] and tt1 needs c in [-32.0, 32.0]).  The only
systematic difference at a shared dm is the displacement's trailing-zero
count z, so the ramp's argument must live in the raw frame dm*disp rather
than in dm:

    u = ((dm << z) - wrap) mod 2^Wf        # fixed Wf-bit window of the frame
      = (dm - wrap>>z) mod 2^(Wf - z)  scaled

With Wf = 19 the modulus is 2^13 for tt4 (z=6) -- reproducing the
period-2^13 structure seen there -- and 2^12 for tt1 (z=7), which is exactly
the halving that lets a single rule serve both.  Amplitude is normalised to
one output granule so it stays comparable across the regimes.

The bias stays granule-relative (`* 2^(bl(P)-30)`), which the tt4 threshold
measurements pin, and is gated off when the frame drops no columns.
"""

from __future__ import annotations

import sys

sys.path[:0] = ["/tmp/walle", "/tmp/walle/analysis"]

import wide_solver_data as W  # noqa: E402
import wide_solver_sweep as S  # noqa: E402


def make_law(Wf: int, wrap: int, q: int, gate_z: int = 13):
    def law(dm: int, d_o: int, z: int):
        P = dm * d_o
        if z >= gate_z:
            return W.narrow(P)
        mod = 1 << (Wf - z)
        u = (dm - (wrap >> z)) % mod
        num = (q >> z) - u                       # in units of mod
        bl = P.bit_length()
        # c = num * 64 / mod  (one granule of swing); bias = c * 2^(bl-30)
        k = max(0, 36 - bl + (Wf - z) - 6)
        bias = (num << (bl - 30 + k)) >> (Wf - z - 6) if Wf - z > 6 else \
               (num << (bl - 30 + k))
        v = (P << k) + bias
        if v <= 0:
            return W.narrow(P)
        mant, sh = W.narrow(v)
        return mant, sh - k
    return law


def main() -> None:
    base = S.score_all(lambda dm, d_o, z: W.narrow(dm * d_o))
    print("narrow baseline:", base)
    best = []
    for Wf in (18, 19, 20):
        step = 1 << (Wf - 6)
        for wrap in range(0, 1 << Wf, step * 4):
            for q in range(0, 1 << Wf, step * 4):
                s = S.score_all(make_law(Wf, wrap, q))
                if s[1] == 18001:
                    best.append((sum(s), Wf, wrap, q, s))
    best.sort(reverse=True)
    print("top tz-parameterised ramps (tt3 intact):")
    for tot, Wf, wrap, q, s in best[:14]:
        mark = "  *** EXACT ***" if s == (18001, 18001, 2610) else ""
        print(f"  Wf={Wf} wrap={wrap:7d} q={q:7d}  tt4 {s[0]:5d} tt3 {s[1]:5d}"
              f" tt1 {s[2]:4d}  total {tot}{mark}")


if __name__ == "__main__":
    main()
