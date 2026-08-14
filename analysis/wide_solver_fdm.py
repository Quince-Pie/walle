#!/usr/bin/env python3
"""Solve for the best per-dm operand perturbation, jointly over tt4+tt1.

Model: for wide products the effective slope operand is dm + f(dm), i.e.
V = P + f(dm) * d_o.  For each dm the admissible f values form a union of
intervals; the optimal f is the interval-stabbing point.  Prints the
resulting table so its shape can be read off.
"""

from __future__ import annotations

import sys
from collections import defaultdict
from fractions import Fraction as F

sys.path[:0] = ["/tmp/walle", "/tmp/walle/analysis"]

import wide_solver_xmap as XM  # noqa: E402


def stab_point(intervals):
    """(best count, a point achieving it) over closed rational intervals."""
    events = []
    for lo, hi in intervals:
        if lo <= hi:
            events.append((lo, 0))
            events.append((hi, 1))
    events.sort()
    best = cur = 0
    point = F(0)
    for value, kind in events:
        if kind == 0:
            cur += 1
            if cur > best:
                best, point = cur, value
        else:
            cur -= 1
    return best, point


def main() -> None:
    groups = defaultdict(list)
    per_ds = defaultdict(lambda: defaultdict(list))
    for ds in ("tt4", "tt1"):
        for r in XM.observations(ds):
            if r["bl"] < 31:
                continue
            s = F(r["d_o"])
            iv = (F(r["xlo"]) / s, F(r["xhi"]) / s)
            groups[r["dm"]].append(iv)
            per_ds[ds][r["dm"]].append(iv)
    total = hits = 0
    table = {}
    for dm, ivs in sorted(groups.items()):
        best, point = stab_point(ivs)
        table[dm] = point
        hits += best
        total += len(ivs)
    print(f"joint optimum over wide cells: {hits}/{total}")
    for ds in ("tt4", "tt1"):
        got = sum(1 for dm, ivs in per_ds[ds].items()
                  for lo, hi in ivs if lo <= table[dm] <= hi)
        n = sum(len(v) for v in per_ds[ds].values())
        print(f"  {ds}: {got}/{n} wide cells")
    print("\n dm        dm-2^23   dm mod 2^13   f(dm)          f*2^13")
    for dm in sorted(table):
        f = table[dm]
        print(f" {dm:08x} {dm - (1 << 23):9d} {dm & 8191:11d}   "
              f"{float(f):+12.8f}  {float(f * 8192):+10.3f}")


if __name__ == "__main__":
    main()
