#!/usr/bin/env python3
"""Which operand features does the excess depend on?

For a battery of candidate key functions, groups the wide cells by the
key and intersects their admissible eps = X/granule intervals.  A key
that is feasible everywhere is a key the excess can be a function of;
the coarsest such key names the real arguments of the law.
"""

from __future__ import annotations

import sys
from fractions import Fraction

sys.path[:0] = ["/tmp/walle/analysis"]

from wide_solver_xmap import observations  # noqa: E402

NEG, POS = Fraction(-10 ** 9), Fraction(10 ** 9)


def norm24(d_o: int) -> int:
    return d_o << (24 - d_o.bit_length())


def keys():
    out = {}
    for a in (8, 11, 12, 13, 14, 16, 24):
        out[f"dm%2^{a}"] = lambda r, a=a: r["dm"] & ((1 << a) - 1)
        for b in (0, 4, 6, 8, 11, 13):
            out[f"dm%2^{a},d_o%2^{b}"] = (
                lambda r, a=a, b=b: (r["dm"] & ((1 << a) - 1),
                                     r["d_o"] & ((1 << b) - 1)))
            out[f"dm%2^{a},didx24%2^{b}"] = (
                lambda r, a=a, b=b: (r["dm"] & ((1 << a) - 1),
                                     norm24(r["d_o"]) & ((1 << b) - 1)))
    for c in (8, 12, 16, 20, 24):
        out[f"P%2^{c}"] = lambda r, c=c: r["P"] & ((1 << c) - 1)
        out[f"P>>cut-24 low {c}"] = (
            lambda r, c=c: (r["P"] >> max(r["cut"] - c, 0)) & ((1 << c) - 1))
    out["dropped/granule"] = lambda r: Fraction(r["dropped"], 1 << r["cut"])
    out["dm,bl(d_o)"] = lambda r: (r["dm"], r["d_o"].bit_length())
    out["dm,d_o>>4"] = lambda r: (r["dm"], r["d_o"] >> 4)
    return out


def main() -> None:
    names = sys.argv[1:] or ["tt4"]
    rows = []
    for name in names:
        rows += [r for r in observations(name) if r["cut"] > 0]
    print(f"{len(rows)} wide cells from {names}")
    results = []
    for tag, fn in keys().items():
        groups: dict = {}
        for r in rows:
            g = Fraction(1 << r["cut"])
            k = fn(r)
            lo, hi = groups.get(k, (NEG, POS))
            groups[k] = (max(lo, Fraction(r["xlo"]) / g),
                         min(hi, Fraction(r["xhi"]) / g))
        bad = sum(1 for lo, hi in groups.values() if lo > hi)
        results.append((bad, len(groups), tag))
    results.sort()
    print(" infeasible  groups  key")
    for bad, n, tag in results:
        print(f"{bad:11d} {n:7d}  {tag}")


if __name__ == "__main__":
    main()
