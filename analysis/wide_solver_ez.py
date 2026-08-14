#!/usr/bin/env python3
"""Excess E as a function of dm's low bits, for one d_o row.

Groups the row's cells by z = dm mod 2^k and intersects their admissible
X intervals, giving the empirical E(z) curve in output-granule units.
"""

from __future__ import annotations

import sys

sys.path[:0] = ["/tmp/walle/analysis"]

from wide_solver_xmap import observations  # noqa: E402

NEG, POS = -(1 << 60), 1 << 60


def main() -> None:
    name = sys.argv[1]
    d_o = int(sys.argv[2])
    k = int(sys.argv[3]) if len(sys.argv) > 3 else 13
    rows = [r for r in observations(name) if r["d_o"] == d_o]
    if not rows:
        return
    cut = rows[0]["cut"]
    g = 1 << cut
    per: dict = {}
    for r in rows:
        z = r["dm"] & ((1 << k) - 1)
        lo, hi = per.get(z, (NEG, POS))
        per[z] = (max(lo, r["xlo"]), min(hi, r["xhi"]), )
    counts: dict = {}
    for r in rows:
        z = r["dm"] & ((1 << k) - 1)
        counts[z] = counts.get(z, 0) + 1
    print(f"{name} d_o={d_o} cut={cut} granule={g}  "
          f"grouped by dm mod 2^{k}")
    print("     z   n     E interval             E/g range")
    for z in sorted(per):
        lo, hi = per[z]
        flag = "" if lo <= hi else "  INFEASIBLE"
        print(f"{z:6d} {counts[z]:3d}  [{lo:+8d},{hi:+8d}]  "
              f"[{lo / g:+7.4f},{hi / g:+7.4f}]{flag}")


if __name__ == "__main__":
    main()
