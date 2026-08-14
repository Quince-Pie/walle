#!/usr/bin/env python3
"""Family T: sawtooth excess in the multiplicand's low bits.

The deviation matrix is periodic with period 2^13 in dm and behaves like
a descending ramp with one wrap per period, scaled by the output granule.
Model:  V = P + (A - ((dm + phase) mod 2^k)) * 2^cut / 2^k, applied only
to wide products so the narrow law is preserved exactly.
"""

from __future__ import annotations

import sys

import numpy as np

sys.path[:0] = ["/tmp/walle/analysis"]

from wide_solver_np import ARR, NAMES, bitlen  # noqa: E402


def score_scaled(law, name: str, scale: int) -> int:
    """law returns V * scale; compare against intervals scaled likewise."""
    dm, d_o, p, lo, hi = ARR[name]
    d = law(dm, d_o) - p * scale
    return int(np.count_nonzero((d >= lo * scale) & (d <= hi * scale)))


def make(k: int, amp: int, phase: int):
    period = 1 << k

    def law(dm, d_o):
        p = dm * d_o
        cut = np.maximum(bitlen(p) - 24, 0)
        wide = cut > 6
        ramp = amp - ((dm + phase) & (period - 1))
        return p * period + np.where(wide, ramp * (1 << cut), 0)

    return law


def main() -> None:
    results = []
    for k in (12, 13, 14):
        period = 1 << k
        for amp in range(-period, period + 1, 128):
            for phase in range(0, period, 128):
                law = make(k, amp, phase)
                s = tuple(score_scaled(law, n, period) for n in NAMES)
                if s[1] != 18001:
                    continue
                results.append((s[0] + s[2], s, k, amp, phase))
    results.sort(reverse=True)
    print("tt4+tt1   tt4   tt3   tt1   k   amp  phase")
    for tot, s, k, amp, phase in results[:20]:
        print(f"{tot:7d} {s[0]:5d} {s[1]:5d} {s[2]:5d} {k:3d} {amp:6d} "
              f"{phase:6d}")


if __name__ == "__main__":
    main()
