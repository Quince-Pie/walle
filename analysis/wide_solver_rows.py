#!/usr/bin/env python3
"""Dump per-row X intervals for tt4 so the deviation's shape is visible."""

from __future__ import annotations

import sys
from collections import defaultdict

sys.path[:0] = ["/tmp/walle", "/tmp/walle/analysis"]

import wide_solver_xmap as X  # noqa: E402


def main() -> None:
    name = sys.argv[1] if len(sys.argv) > 1 else "tt4"
    want = [int(a) for a in sys.argv[2:]]
    rows = defaultdict(list)
    for r in X.observations(name):
        rows[r["d_o"]].append(r)
    for d_o in sorted(rows):
        if want and d_o not in want:
            continue
        cells = sorted(rows[d_o], key=lambda r: r["dm"])
        print(f"=== d_o={d_o} (bl {d_o.bit_length()})  {len(cells)} cells")
        for r in cells:
            g = 1 << r["cut"]
            print(f"  dm=0x{r['dm']:06X} bl={r['bl']} cut={r['cut']:2d} "
                  f"drop={r['dropped']:6d}/{g} "
                  f"X in [{r['xlo']:7d},{r['xhi']:7d}] "
                  f"X/g in [{r['xlo']/g:+.4f},{r['xhi']/g:+.4f}]")


if __name__ == "__main__":
    main()
