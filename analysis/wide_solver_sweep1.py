#!/usr/bin/env python3
"""Family A: W-bit datapath with truncation + compensation constant.

Hypothesis: the setup multiplier holds W significant bits.  Products that
fit (bl <= W) go through the proven narrow law untouched.  Wider products
are cut to W bits and a fixed compensation constant K is added at the
W-bit lsb (a truncated-multiplier correction), after which the same
narrow-law datapath (rna27 then RNE24) exports the word.

For W = 30 a constant K = 13 reproduces the measured +13/64-output-ulp
bias exactly, and the reduction to the narrow law is automatic.
"""

from __future__ import annotations

import sys

sys.path[:0] = ["/tmp/walle/analysis"]

from wide_solver_data import MODES, load, narrow, score  # noqa: E402


def make_law(width: int, mode: str, const: int):
    cut = MODES[mode]

    def law(dm: int, d_o: int) -> tuple[int, int]:
        p = dm * d_o
        if p.bit_length() <= width:
            return narrow(p)
        t, sh = cut(p, width)
        t += const
        m, k = narrow(t)
        return m, k + sh

    return law


def main() -> None:
    data = {n: load(n) for n in ("tt4", "tt3", "tt1")}
    results = []
    for width in range(27, 35):
        for mode in ("rtz", "rne", "rna", "rup", "rodd"):
            for const in range(0, 65):
                law = make_law(width, mode, const)
                s = [score(data[n], law)[0] for n in ("tt4", "tt3", "tt1")]
                results.append((s[0] + s[1] + s[2], s, width, mode, const))
    results.sort(reverse=True)
    print("total  tt4    tt3    tt1    W  mode  K")
    for total, s, width, mode, const in results[:25]:
        print(f"{total:6d} {s[0]:6d} {s[1]:6d} {s[2]:6d} {width:3d}  "
              f"{mode:5s} {const}")


if __name__ == "__main__":
    main()
