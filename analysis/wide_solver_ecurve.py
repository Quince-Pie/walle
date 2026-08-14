#!/usr/bin/env python3
"""Test whether E/granule depends only on dm's low bits, across all rows.

Intersects the normalised admissible interval X/2^cut over every d_o row
for each value of z = dm mod 2^k.  A feasible intersection everywhere
means the excess is a pure function of the multiplicand's low bits,
measured in output granules.
"""

from __future__ import annotations

import sys
from fractions import Fraction

sys.path[:0] = ["/tmp/walle/analysis"]

from wide_solver_xmap import observations  # noqa: E402

NEG, POS = Fraction(-10 ** 9), Fraction(10 ** 9)


def main() -> None:
    name = sys.argv[1] if len(sys.argv) > 1 else "tt4"
    k = int(sys.argv[2]) if len(sys.argv) > 2 else 13
    rows = [r for r in observations(name) if r["cut"] > 0]
    per: dict = {}
    count: dict = {}
    for r in rows:
        g = Fraction(1 << r["cut"])
        z = r["dm"] & ((1 << k) - 1)
        lo, hi = per.get(z, (NEG, POS))
        per[z] = (max(lo, Fraction(r["xlo"]) / g),
                  min(hi, Fraction(r["xhi"]) / g))
        count[z] = count.get(z, 0) + 1
    bad = [z for z in per if per[z][0] > per[z][1]]
    print(f"{name}: E/granule as a function of dm mod 2^{k} over "
          f"{len(rows)} wide cells")
    print(f"  {len(per)} distinct z values, {len(bad)} infeasible")
    width = {z: per[z][1] - per[z][0] for z in per if per[z][0] <= per[z][1]}
    if width:
        tight = sorted(width, key=lambda z: width[z])[:40]
        print("  tightest pins (z, count, E/g interval):")
        for z in sorted(tight):
            lo, hi = per[z]
            print(f"    z={z:6d} n={count[z]:4d}  "
                  f"[{float(lo):+8.5f},{float(hi):+8.5f}]  width "
                  f"{float(width[z]):.5f}")
    if bad:
        print(f"  infeasible z (first 20): {sorted(bad)[:20]}")


if __name__ == "__main__":
    main()
