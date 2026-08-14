#!/usr/bin/env python3
"""Fine refinement of the sawtooth excess, reparametrised.

E = (q - ((dm - wrap) mod 2^13)) * 2^(cut-13) applied to wide products,
where `wrap` is the dm residue at which the ramp restarts and q the value
at the restart.  The coarse sweep put q near 1152 = (9/64) * 2^13.
"""

from __future__ import annotations

import sys

import numpy as np

sys.path[:0] = ["/tmp/walle/analysis"]

from wide_solver_np import ARR, NAMES, bitlen  # noqa: E402

K = 13
PERIOD = 1 << K


def score_scaled(law, name: str) -> int:
    dm, d_o, p, lo, hi = ARR[name]
    d = law(dm, d_o) - p * PERIOD
    return int(np.count_nonzero((d >= lo * PERIOD) & (d <= hi * PERIOD)))


def make(q: int, wrap: int, thresh: int = 6):
    def law(dm, d_o):
        p = dm * d_o
        cut = np.maximum(bitlen(p) - 24, 0)
        ramp = q - ((dm - wrap) & (PERIOD - 1))
        return p * PERIOD + np.where(cut > thresh, ramp * (1 << cut), 0)
    return law


def main() -> None:
    results = []
    for q in range(1024, 1281, 8):
        for wrap in range(0, PERIOD, 8):
            law = make(q, wrap)
            s = tuple(score_scaled(law, n) for n in NAMES)
            if s[1] != 18001:
                continue
            results.append((s[0] + s[2], s, q, wrap))
    results.sort(reverse=True)
    print("tt4+tt1   tt4   tt3   tt1     q   wrap")
    for tot, s, q, wrap in results[:15]:
        print(f"{tot:7d} {s[0]:5d} {s[1]:5d} {s[2]:5d} {q:5d} {wrap:6d}")


if __name__ == "__main__":
    main()
