#!/usr/bin/env python3
"""Excess intervals down one dm column: how does E vary with d_o?"""

from __future__ import annotations

import sys
from fractions import Fraction

sys.path[:0] = ["/tmp/walle/analysis"]

from wide_solver_xmap import observations  # noqa: E402


def main() -> None:
    name = sys.argv[1]
    dm = int(sys.argv[2], 0)
    rows = [r for r in observations(name) if r["dm"] == dm and r["cut"] > 0]
    rows.sort(key=lambda r: r["d_o"])
    print(f"{name} dm={dm:08x} (dm-2^23={dm - (1 << 23)}, "
          f"dm mod 2^13={dm & 8191}): {len(rows)} rows")
    print("   d_o  ty  bl cut  drop/g    E interval        E/g interval")
    for r in rows:
        g = Fraction(1 << r["cut"])
        ty = None
        if name == "tt4":
            ty = (r["d_o"] + 2047) // 128
        elif name == "tt1":
            ty = (r["d_o"] + 1229) // 64
        print(f"{r['d_o']:6d} {ty if ty else 0:3d} {r['bl']:3d} {r['cut']:3d} "
              f"{float(r['dropped'] / g):7.4f}  "
              f"[{r['xlo']:+7d},{r['xhi']:+7d}]  "
              f"[{float(r['xlo'] / g):+7.4f},{float(r['xhi'] / g):+7.4f}]")


if __name__ == "__main__":
    main()
