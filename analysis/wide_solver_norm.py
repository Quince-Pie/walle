#!/usr/bin/env python3
"""Which normalisation makes the excess a function of a single operand?

If the hardware perturbs the slope operand, E = eps(dm) * d_o, so E/d_o
is constant across a dm column.  If it perturbs the displacement,
E/dm is constant across a d_o row.  Tests those and several relatives by
interval intersection.
"""

from __future__ import annotations

import sys
from fractions import Fraction

sys.path[:0] = ["/tmp/walle/analysis"]

from wide_solver_xmap import observations  # noqa: E402

NEG, POS = Fraction(-10 ** 20), Fraction(10 ** 20)


def norm24(v: int) -> int:
    return v << (24 - v.bit_length())


def models():
    return {
        "eps(dm) with E = eps*d_o":
            (lambda r: r["dm"], lambda r: Fraction(r["d_o"])),
        "eps(dm) with E = eps*didx24":
            (lambda r: r["dm"], lambda r: Fraction(norm24(r["d_o"]))),
        "eps(dm) with E = eps*2^bl(d_o)":
            (lambda r: r["dm"],
             lambda r: Fraction(1 << r["d_o"].bit_length())),
        "eps(dm) with E = eps*P":
            (lambda r: r["dm"], lambda r: Fraction(r["P"])),
        "eps(dm) with E = eps*2^cut":
            (lambda r: r["dm"], lambda r: Fraction(1 << r["cut"])),
        "eta(d_o) with E = eta*dm":
            (lambda r: r["d_o"], lambda r: Fraction(r["dm"])),
        "eta(d_o) with E = eta*P":
            (lambda r: r["d_o"], lambda r: Fraction(r["P"])),
        "eta(d_o) with E = eta*2^cut":
            (lambda r: r["d_o"], lambda r: Fraction(1 << r["cut"])),
        "eps(dm mod 2^13) with E = eps*d_o":
            (lambda r: r["dm"] & 8191, lambda r: Fraction(r["d_o"])),
        "eps(dm mod 2^13) with E = eps*2^cut":
            (lambda r: r["dm"] & 8191, lambda r: Fraction(1 << r["cut"])),
    }


def main() -> None:
    names = sys.argv[1:] or ["tt4"]
    rows = []
    for name in names:
        rows += [r for r in observations(name) if r["cut"] > 0]
    print(f"{len(rows)} wide cells from {names}")
    for tag, (key, scale) in models().items():
        groups: dict = {}
        for r in rows:
            s = scale(r)
            k = key(r)
            lo, hi = groups.get(k, (NEG, POS))
            groups[k] = (max(lo, Fraction(r["xlo"]) / s),
                         min(hi, Fraction(r["xhi"]) / s))
        bad = sum(1 for lo, hi in groups.values() if lo > hi)
        print(f"  {len(groups):5d} groups  {bad:5d} infeasible   {tag}")


if __name__ == "__main__":
    main()
