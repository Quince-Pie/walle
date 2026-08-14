#!/usr/bin/env python3
"""Family B: radix-4 Booth array, per-partial truncation, floating window.

The residual under the best possible f(dm) concentrates on dm values with
runs of ones and isolated powers of two - radix-4 Booth recoding
boundaries.  This recodes one operand into signed digits, truncates each
partial product individually at a window that floats with the product
(so narrow products are untouched and tt3 stays exact by construction),
sums, and adds a granule-relative compensation before the narrow export.

Unlike the earlier campaign's Booth sweeps this uses the proven narrow
export and a granule-relative (not absolute) compensation.
"""

from __future__ import annotations

import sys

import numpy as np

sys.path[:0] = ["/tmp/walle/analysis"]

from wide_solver_np import ARR, NAMES, bitlen  # noqa: E402


def score(law, name: str) -> int:
    dm, d_o, p, lo, hi = ARR[name]
    d = law(dm, d_o) - p
    return int(np.count_nonzero((d >= lo) & (d <= hi)))


def booth_digits(a: np.ndarray, ndigits: int):
    """Radix-4 Booth digits b_k in {-2..2} with a = sum b_k * 4^k."""
    out = []
    for k in range(ndigits):
        b0 = (a >> max(2 * k - 1, 0)) & 1 if k else np.zeros_like(a)
        b1 = (a >> (2 * k)) & 1
        b2 = (a >> (2 * k + 1)) & 1
        out.append(b0 + b1 - 2 * b2)
    return out


def make(width: int, const: int, recode: str, ndigits: int = 13):
    def law(dm, d_o):
        p = dm * d_o
        bl = bitlen(p)
        t = np.maximum(bl - width, 0)
        a, b = (dm, d_o) if recode == "dm" else (d_o, dm)
        total = np.zeros_like(p)
        for k, dig in enumerate(booth_digits(a, ndigits)):
            partial = dig * b * (np.int64(1) << (2 * k))
            total += (partial >> t) << t        # arithmetic shift = floor
        return total + np.where(bl > 30,
                                const * (np.int64(1) << np.maximum(bl - 30,
                                                                   0)), 0)
    return law


def main() -> None:
    results = []
    for recode in ("dm", "d_o"):
        for width in range(28, 39):
            for const in range(-32, 65):
                law = make(width, const, recode)
                s = tuple(score(law, n) for n in NAMES)
                if s[1] != 18001:
                    continue
                results.append((s[0] + s[2], s, recode, width, const))
    results.sort(reverse=True)
    print("tt4+tt1   tt4   tt3   tt1  recode   W  const")
    for tot, s, recode, width, const in results[:20]:
        print(f"{tot:7d} {s[0]:5d} {s[1]:5d} {s[2]:5d}  {recode:5s} {width:3d} "
              f"{const:5d}")
    print(f"\n({len(results)} tt3-preserving combinations)")


if __name__ == "__main__":
    main()
