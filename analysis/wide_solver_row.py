#!/usr/bin/env python3
"""Dump per-cell admissible X intervals for one d_o row of a dataset."""

from __future__ import annotations

import sys

sys.path[:0] = ["/tmp/walle/analysis"]

from wide_solver_xmap import observations  # noqa: E402

BASE = 1 << 23


def main() -> None:
    name = sys.argv[1]
    d_o = int(sys.argv[2])
    block = sys.argv[3] if len(sys.argv) > 3 else "lo"
    rows = [r for r in observations(name) if r["d_o"] == d_o]
    rows.sort(key=lambda r: r["dm"])
    sel = []
    for r in rows:
        t = r["dm"] - BASE
        if block == "lo" and 0 <= t <= 255:
            sel.append((t, r))
        elif block == "hi" and t and t % 256 == 0:
            sel.append((t >> 8, r))
        elif block == "all":
            sel.append((t, r))
    print(f"{name} d_o={d_o} block={block}  n={len(sel)}")
    if not sel:
        return
    cut = sel[0][1]["cut"]
    print(f"cut={cut} granule=2^{cut}={1 << cut}")
    print("   t   dm        dropped  drop/g    X interval        X/g range")
    for t, r in sel:
        g = float(1 << cut) if cut > 0 else 1.0
        print(f"{t:4d} {r['dm']:08x} {r['dropped']:8d} {r['dropped'] / g:7.4f}"
              f"  [{r['xlo']:+8d},{r['xhi']:+8d}]"
              f"  [{r['xlo'] / g:+7.4f},{r['xhi'] / g:+7.4f}]")


if __name__ == "__main__":
    main()
