#!/usr/bin/env python3
"""Group cells by a key and intersect their admissible X intervals.

If a grouping is the right one (X constant inside every group) every
intersection is non-empty.  Reports infeasible-group counts, and prints
the pinned X table for the (d_o, dm mod 2) grouping.
"""

from __future__ import annotations

import sys

sys.path[:0] = ["/tmp/walle/analysis"]

from wide_solver_xmap import observations  # noqa: E402

BASE = 1 << 23
NEG, POS = -(1 << 60), 1 << 60


def intersect(rows, key):
    groups: dict = {}
    for r in rows:
        k = key(r)
        lo, hi = groups.get(k, (NEG, POS))
        groups[k] = (max(lo, r["xlo"]), min(hi, r["xhi"]))
    return groups


def main() -> None:
    name = sys.argv[1] if len(sys.argv) > 1 else "tt4"
    rows = [r for r in observations(name) if r["xlo"] <= r["xhi"]]
    for k in range(0, 6):
        mask = (1 << k) - 1
        groups = intersect(rows, lambda r, mask=mask: (r["d_o"], r["dm"] & mask))
        bad = sum(1 for lo, hi in groups.values() if lo > hi)
        print(f"group by (d_o, dm mod 2^{k}): {len(groups):5d} groups, "
              f"{bad:5d} infeasible")
    # also: does X depend on dm at all beyond low bits?
    groups = intersect(rows, lambda r: (r["d_o"], r["dm"]))
    print(f"group by (d_o, dm) [singletons]: {len(groups)} groups")

    print("\nPinned X per (d_o, dm parity), low-block dm only "
          "(granule = 2^cut):")
    print("  d_o cut  even-dm X            odd-dm X             "
          "X_even/2^cut  X_odd/2^cut")
    lo_rows = [r for r in rows if 0 <= r["dm"] - BASE <= 255]
    per = intersect(lo_rows, lambda r: (r["d_o"], r["dm"] & 1))
    for d_o in sorted({d for d, _ in per}):
        cut = next(r["cut"] for r in lo_rows if r["d_o"] == d_o)
        out = []
        for par in (0, 1):
            a, b = per[(d_o, par)]
            out.append(f"[{a:+7d},{b:+7d}]" + ("" if a <= b else "XX"))
        ea, eb = per[(d_o, 0)]
        oa, ob = per[(d_o, 1)]
        g = float(1 << cut)
        print(f"{d_o:5d} {cut:3d}  {out[0]:20s} {out[1]:20s} "
              f"{ea / g:+.4f}..{eb / g:+.4f}  {oa / g:+.4f}..{ob / g:+.4f}")


if __name__ == "__main__":
    main()
