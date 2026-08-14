#!/usr/bin/env python3
"""E/granule versus dm's low bits, intersected within one cut class.

Rows sharing a cut share an output granule, so if the excess is a pure
function of dm's low bits (in granule units) their constraints can be
intersected to pin the curve tightly.
"""

from __future__ import annotations

import sys
from fractions import Fraction

sys.path[:0] = ["/tmp/walle/analysis"]

from wide_solver_xmap import observations  # noqa: E402

NEG, POS = Fraction(-10 ** 9), Fraction(10 ** 9)


def main() -> None:
    name = sys.argv[1] if len(sys.argv) > 1 else "tt4"
    cut_sel = int(sys.argv[2]) if len(sys.argv) > 2 else 12
    k = int(sys.argv[3]) if len(sys.argv) > 3 else 13
    step = int(sys.argv[4]) if len(sys.argv) > 4 else 256
    per: dict = {}
    count: dict = {}
    for r in observations(name):
        if r["cut"] != cut_sel:
            continue
        z = r["dm"] & ((1 << k) - 1)
        if z % step:
            continue
        g = Fraction(1 << r["cut"])
        lo, hi = per.get(z, (NEG, POS))
        per[z] = (max(lo, Fraction(r["xlo"]) / g),
                  min(hi, Fraction(r["xhi"]) / g))
        count[z] = count.get(z, 0) + 1
    print(f"{name} cut={cut_sel}: E/granule vs z = dm mod 2^{k} "
          f"(step {step})")
    print("     z   n     E/g interval        width")
    for z in sorted(per):
        lo, hi = per[z]
        flag = "  INFEASIBLE" if lo > hi else ""
        print(f"{z:6d} {count[z]:3d}  [{float(lo):+8.4f},{float(hi):+8.4f}]"
              f"  {float(hi - lo):7.4f}{flag}")


if __name__ == "__main__":
    main()
