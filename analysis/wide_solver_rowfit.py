#!/usr/bin/env python3
"""Per-row feasibility of simple X models.

For each dataset row (fixed d_o) intersect the exact X intervals over all
cells and report whether a single constant X, or a constant X/granule,
explains the whole row.
"""

from __future__ import annotations

import sys
from collections import defaultdict
from fractions import Fraction

sys.path[:0] = ["/tmp/walle", "/tmp/walle/analysis"]

import wide_solver_xmap as X  # noqa: E402


def main() -> None:
    for name in ("tt4", "tt1", "tt3"):
        rows = defaultdict(list)
        for r in X.observations(name):
            rows[r["d_o"]].append(r)
        print(f"### {name}")
        for d_o in sorted(rows):
            cells = rows[d_o]
            lo = max(c["xlo"] for c in cells)
            hi = min(c["xhi"] for c in cells)
            # constant X / granule (as Fractions)
            flo = max(Fraction(c["xlo"], 1 << c["cut"]) for c in cells)
            fhi = min(Fraction(c["xhi"], 1 << c["cut"]) for c in cells)
            # constant relative X / P
            rlo = max(Fraction(c["xlo"], c["P"]) for c in cells)
            rhi = min(Fraction(c["xhi"], c["P"]) for c in cells)
            cuts = sorted({c["cut"] for c in cells})
            abs_ok = "OK" if lo <= hi else "--"
            rel_ok = "OK" if flo <= fhi else "--"
            prel_ok = "OK" if rlo <= rhi else "--"
            print(f" d_o={d_o:6d} n={len(cells):4d} cuts={cuts} "
                  f"absX[{lo},{hi}] {abs_ok} | X/g[{float(flo):+.4f},"
                  f"{float(fhi):+.4f}] {rel_ok} | X/P*2^29["
                  f"{float(rlo)*2**29:+.3f},{float(rhi)*2**29:+.3f}] {prel_ok}")


if __name__ == "__main__":
    main()
