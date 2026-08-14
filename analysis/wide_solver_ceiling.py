#!/usr/bin/env python3
"""Exact ceiling of the "bias is a function of dm" family.

Any rule of the form  C = narrow(P + c(key) * 2^(bl(P)-30))  -- a sawtooth,
a lookup table, anything -- is pinned per key by an interval-stabbing
problem: choose the c that lies inside the most admissible intervals.
Solving that optimally gives the maximum score ANY member of the family can
reach, without sweeping a single candidate.
"""

from __future__ import annotations

import sys
from collections import defaultdict
from fractions import Fraction as F

sys.path[:0] = ["/tmp/walle", "/tmp/walle/analysis"]

import wide_solver_xmap as XM  # noqa: E402

KEYS = {
    "dm": lambda r: r["dm"],
    "dm mod 2^13": lambda r: r["dm"] & 8191,
    "dm mod 2^14": lambda r: r["dm"] & 16383,
    "(dm, bl)": lambda r: (r["dm"], r["bl"]),
    "(dm mod 2^13, bl)": lambda r: (r["dm"] & 8191, r["bl"]),
}

SCALES = {
    "granule 2^(bl-30)": lambda r: F(1, 1) * 2 ** (r["bl"] - 30),
    "d_o": lambda r: F(r["d_o"]),
}


def stab(intervals) -> int:
    """Max number of intervals containing a common point (exact rationals)."""
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
    for kname, kf in KEYS.items():
        for sname, sf in SCALES.items():
            line = []
            for ds in ("tt4", "tt3", "tt1"):
                groups = defaultdict(list)
                for r in XM.observations(ds):
                    s = sf(r)
                    groups[kf(r)].append((F(r["xlo"]) / s, F(r["xhi"]) / s))
                total = sum(stab(v) for v in groups.values())
                n = sum(len(v) for v in groups.values())
                line.append(f"{ds} {total}/{n}")
            print(f"  key={kname:20s} scale={sname:18s} ceiling: "
                  + "  ".join(line))


if __name__ == "__main__":
    main()
