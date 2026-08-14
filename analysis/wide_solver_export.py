#!/usr/bin/env python3
"""Does the wide path use a different export rounding than the narrow one?

The ceiling theorem assumes hw_word = encode(narrow(V)).  If the wide path
rounds differently, that preimage - and hence the ceiling - is wrong.  This
recomputes, for wide cells only (bl >= 31), the admissible interval for
V under each candidate export, then reports the interval-stabbing ceiling
of a bias law keyed on dm.  Narrow cells keep the proven law, so tt3 is
unaffected and stays 18001/18001 by construction.
"""

from __future__ import annotations

import sys
from collections import defaultdict
from fractions import Fraction as F

sys.path[:0] = ["/tmp/walle", "/tmp/walle/analysis"]

import wide_solver_data as W  # noqa: E402
import wide_solver_xmap as XM  # noqa: E402


def make_export(inner: str | None, outer: str):
    def export(v: int) -> int:
        if inner is not None:
            mant, sh = W.MODES[inner](v, 27)
            v = mant << sh
        mant, sh = W.MODES[outer](v, 24)
        return mant << sh
    return export


EXPORTS = {"narrow RNE24(rna27)": make_export("rna", "rne")}
for _outer in ("rne", "rna", "rtz", "rup"):
    EXPORTS[f"{_outer}24 direct"] = make_export(None, _outer)
    for _inner in ("rna", "rne", "rtz", "rodd", "rup"):
        EXPORTS[f"{_outer}24({_inner}27)"] = make_export(_inner, _outer)


def preimage(export, target: int, p: int) -> tuple[int, int]:
    """Inclusive [lo, hi] of V with export(V) == target (export monotone)."""
    lo_b, hi_b = 1, 1 << 52
    a, b = lo_b, hi_b
    while a < b:
        mid = (a + b) // 2
        if export(mid) >= target:
            b = mid
        else:
            a = mid + 1
    lo = a
    a, b = lo_b, hi_b
    while a < b:
        mid = (a + b + 1) // 2
        if export(mid) <= target:
            a = mid
        else:
            b = mid - 1
    hi = a
    if export(lo) != target or export(hi) != target:
        return 1, 0
    return lo, hi


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
    wide = {}
    for ds in ("tt4", "tt1"):
        wide[ds] = [r for r in XM.observations(ds) if r["bl"] >= 31]
    narrow_tt1 = 2610 - len(wide["tt1"])
    print(f"wide cells: tt4 {len(wide['tt4'])}, tt1 {len(wide['tt1'])} "
          f"(tt1 also has {narrow_tt1} narrow cells)")
    print("\n  tt4    tt1  | export stage (bias keyed on dm, scale d_o)")
    out = []
    for name, export in EXPORTS.items():
        cache: dict = {}
        totals = []
        for ds in ("tt4", "tt1"):
            groups = defaultdict(list)
            ok = True
            for r in wide[ds]:
                key = (r["target"], r["bl"])
                if key not in cache:
                    cache[key] = preimage(export, r["target"], r["P"])
                lo, hi = cache[key]
                if lo > hi:
                    ok = False
                    continue
                s = F(r["d_o"])
                groups[r["dm"]].append((F(lo - r["P"]) / s,
                                        F(hi - r["P"]) / s))
            totals.append(sum(stab(v) for v in groups.values()) if ok
                          else sum(stab(v) for v in groups.values()))
        out.append((totals[0] + totals[1], totals, name))
    out.sort(reverse=True)
    for tot, t, name in out:
        print(f"{t[0]:6d} {t[1]:6d}  | {name}")


if __name__ == "__main__":
    main()
