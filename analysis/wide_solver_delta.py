#!/usr/bin/env python3
"""Print the exported-minus-exact offset (in output granules) per cell.

delta = (hw_value - P) / 2^cut.  A narrow-law-exact cell has |delta| <= 1/2;
anything outside that is the wide-path deviation, shown as a signed
number of granules.
"""

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
    if not sel:
        return
    cut = sel[0][1]["cut"]
    g = 1 << cut
    print(f"{name} d_o={d_o} block={block} cut={cut} granule={g}  "
          f"n={len(sel)}")
    print("   t   dm        drop/g   delta/g   n=(hw-floor)/g")
    for t, r in sel:
        hw = r["target"]
        p = r["P"]
        floor = (p >> cut) << cut
        print(f"{t:4d} {r['dm']:08x}  {r['dropped'] / g:7.4f} "
              f"{(hw - p) / g:+8.4f}  {(hw - floor) // g:+3d}")


if __name__ == "__main__":
    main()
