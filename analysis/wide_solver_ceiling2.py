#!/usr/bin/env python3
"""Extended ceiling search: which key can a bias law legitimately use?

Same interval-stabbing ceiling as wide_solver_ceiling.py, but over a wider
battery of keys and scales, and reporting the group count and the mean
cells-per-group so near-injective (and therefore meaningless) keys can be
told apart from real structure.
"""

from __future__ import annotations

import sys
from collections import defaultdict
from fractions import Fraction as F

sys.path[:0] = ["/tmp/walle", "/tmp/walle/analysis"]

import wide_solver_xmap as XM  # noqa: E402

DS = ("tt4", "tt3", "tt1")
TOTAL = {"tt4": 18001, "tt3": 18001, "tt1": 2610}


def phi(r, bits):
    """Top `bits` bits of P's dropped part below the 30-bit datapath."""
    c = max(r["bl"] - 30, 0)
    if c == 0:
        return 0
    return (r["P"] & ((1 << c) - 1)) >> max(c - bits, 0)


KEYS = {
    "dm": lambda r: r["dm"],
    "d_o": lambda r: r["d_o"],
    "(d_o, dm mod 2^4)": lambda r: (r["d_o"], r["dm"] & 15),
    "(d_o, dm mod 2^6)": lambda r: (r["d_o"], r["dm"] & 63),
    "(dm, bl)": lambda r: (r["dm"], r["bl"]),
    "(dm mod 2^13, bl)": lambda r: (r["dm"] & 8191, r["bl"]),
    "phi30 top4": lambda r: phi(r, 4),
    "phi30 top6": lambda r: phi(r, 6),
    "(dm mod 2^13, phi30 top4)": lambda r: (r["dm"] & 8191, phi(r, 4)),
    "(bl, phi30 top6)": lambda r: (r["bl"], phi(r, 6)),
    "P mod 2^(bl-24) i.e. dropped": lambda r: r["dropped"],
}

SCALES = {
    "2^(bl-30)": lambda r: F(2) ** (r["bl"] - 30),
    "d_o": lambda r: F(r["d_o"]),
    "dm": lambda r: F(r["dm"]),
    "P": lambda r: F(r["P"]),
}


def stab(intervals) -> int:
    events = []
    for lo, hi in intervals:
        if lo <= hi:
            events.append((lo, 0))
            events.append((hi, 1))
    events.sort()
    best = cur = 0
    for _, kind in events:
        if kind == 0:
            cur += 1
            best = max(best, cur)
        else:
            cur -= 1
    return best


def main() -> None:
    cache = {ds: list(XM.observations(ds)) for ds in DS}
    rows = []
    for kname, kf in KEYS.items():
        for sname, sf in SCALES.items():
            cells = []
            ngroups = []
            for ds in DS:
                groups = defaultdict(list)
                for r in cache[ds]:
                    s = sf(r)
                    groups[kf(r)].append((F(r["xlo"]) / s, F(r["xhi"]) / s))
                cells.append(sum(stab(v) for v in groups.values()))
                ngroups.append(len(groups))
            rows.append((sum(cells), cells, ngroups, kname, sname))
    rows.sort(reverse=True)
    print("  tt4    tt3    tt1  | groups(tt4/tt3/tt1) | key / scale")
    for tot, cells, ng, kname, sname in rows:
        print(f"{cells[0]:6d} {cells[1]:6d} {cells[2]:6d}  | "
              f"{ng[0]:5d}/{ng[1]:5d}/{ng[2]:4d} | {kname} / {sname}")


if __name__ == "__main__":
    main()
