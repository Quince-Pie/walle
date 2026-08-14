#!/usr/bin/env python3
"""Cells whose product is exactly representable in 24 bits.

For these the narrow law returns P untouched, so the exported word minus
P is the pure arithmetic deviation of the wide datapath, free of any
rounding ambiguity.  Tabulates that deviation against the operands.
"""

from __future__ import annotations

import sys
from collections import Counter

sys.path[:0] = ["/tmp/walle/analysis"]

from wide_solver_xmap import observations  # noqa: E402


def main() -> None:
    for name in ("tt4", "tt1"):
        rows = [r for r in observations(name)
                if r["cut"] > 0 and r["dropped"] == 0]
        print(f"\n=== {name}: {len(rows)} exactly-representable wide cells")
        tally = Counter()
        for r in rows:
            g = 1 << r["cut"]
            tally[(r["target"] - r["P"]) // g] += 1
        print("   offset in granules:", dict(sorted(tally.items())))
        shown = [r for r in rows if r["target"] != r["P"]]
        shown.sort(key=lambda r: (r["d_o"], r["dm"]))
        print(f"   {len(shown)} deviating cells "
              f"(dm, d_o, bl, cut, offset, dm low12, dm low13):")
        for r in shown[:60]:
            g = 1 << r["cut"]
            print(f"   dm={r['dm']:08x} d_o={r['d_o']:6d} bl={r['bl']:2d} "
                  f"cut={r['cut']:2d} off={(r['target'] - r['P']) // g:+2d} "
                  f"dm&0xfff={r['dm'] & 0xfff:5d} "
                  f"dm&0x1fff={r['dm'] & 0x1fff:5d}")
        if len(shown) > 60:
            print(f"   ... {len(shown) - 60} more")


if __name__ == "__main__":
    main()
