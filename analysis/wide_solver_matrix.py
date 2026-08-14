#!/usr/bin/env python3
"""Deviation matrix: rows = d_o, columns = dm scan index.

Each cell shows (exported - narrow-law prediction) in output granules:
'.' agrees with the narrow law, '+'/'-' are one granule high/low, digits
for larger offsets.
"""

from __future__ import annotations

import sys

sys.path[:0] = ["/tmp/walle/analysis"]

from wide_solver_data import narrow  # noqa: E402
from wide_solver_xmap import observations  # noqa: E402

BASE = 1 << 23


def symbol(off: int) -> str:
    if off == 0:
        return "."
    if off == 1:
        return "+"
    if off == -1:
        return "-"
    if 2 <= off <= 9:
        return str(off)
    if -9 <= off <= -2:
        return "abcdefgh"[-off - 2]
    return "?"


def main() -> None:
    name = sys.argv[1] if len(sys.argv) > 1 else "tt4"
    block = sys.argv[2] if len(sys.argv) > 2 else "hi"
    grid: dict = {}
    for r in observations(name):
        t = r["dm"] - BASE
        if block == "lo":
            if not 0 <= t <= 255:
                continue
            col = t
        else:
            if t % 256 or t == 0:
                continue
            col = t >> 8
        mant, sh = narrow(r["P"])
        pred = mant << sh
        g = 1 << r["cut"] if r["cut"] > 0 else 1
        grid.setdefault(r["d_o"], {})[col] = (r["target"] - pred) // g
    cols = sorted({c for row in grid.values() for c in row})
    print(f"{name} block={block}: deviation from narrow law, in granules")
    print("      " + "".join(str(c // 10 % 10) if c % 10 == 0 else " "
                             for c in cols))
    print("      " + "".join(str(c % 10) for c in cols))
    for d_o in sorted(grid):
        line = "".join(symbol(grid[d_o].get(c, 0)) for c in cols)
        print(f"{d_o:5d} {line}")


if __name__ == "__main__":
    main()
