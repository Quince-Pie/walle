#!/usr/bin/env python3
"""Track A, second half: partials through the full narrow-law datapath.

Instead of a single-width quantiser, each partial product is passed
through the proven export chain itself -- rna27 then RNE24 -- so the
segmented multiplier reuses the same rounder recursively.  Sweeps the
split point, signed/unsigned low segment, and which partials are routed
through the chain, plus a widened variant (rna at W1, RNE at W2) so the
chain's two stages can be moved together.
"""

from __future__ import annotations

import sys

import numpy as np

sys.path[:0] = ["/tmp/walle/analysis"]

from wide_solver_A_frame import (ARR, killer_index, report,  # noqa: E402
                                 score_all)
from wide_solver_np import quant  # noqa: E402


def chain(v: np.ndarray, w1: int, w2: int) -> np.ndarray:
    """|v| through rna at w1 bits then RNE at w2 bits; sign restored."""
    neg = v < 0
    q = quant(quant(np.abs(v), w1, "rna"), w2, "rne")
    return np.where(neg, -q, q)


def make(s: int, signed: bool, w1: int, w2: int, route: str):
    def law(a):
        dm, disp = a["dm"], a["disp"]
        hi = dm >> s
        lo = dm - (hi << s)
        if signed:
            borrow = (lo >= (1 << (s - 1))).astype(np.int64)
            lo = lo - (borrow << s)
            hi = hi + borrow
        ph, pl = hi * disp, lo * disp
        if route in ("both", "hi"):
            ph = chain(ph, w1, w2)
        if route in ("both", "lo"):
            pl = chain(pl, w1, w2)
        return (ph << s) + pl
    return law


def main() -> None:
    report("identity (narrow law, raw frame)", lambda a: a["R"], "raw")
    a4 = ARR["tt4"]
    ki = killer_index()
    rows = []
    for s in range(10, 15):
        for signed in (False, True):
            for route in ("both", "hi", "lo"):
                for w1 in range(21, 30):
                    for w2 in range(18, w1 + 1):
                        law = make(s, signed, w1, w2, route)
                        sc = score_all(law, "raw")
                        v = law(a4)[ki]
                        k = bool(a4["rlo"][ki] <= v <= a4["rhi"][ki])
                        rows.append((sc[0] + sc[1] + sc[2], sc, s, signed,
                                     route, w1, w2, k))
    rows.sort(reverse=True)
    print("\ntotal   tt4   tt3   tt1  killer   s signed route  w1  w2")
    for tot, sc, s, signed, route, w1, w2, k in rows[:15]:
        print(f"{tot:6d} {sc[0]:5d} {sc[1]:5d} {sc[2]:5d}  {k!s:5s}  {s:3d} "
              f"{signed!s:6s} {route:5s} {w1:3d} {w2:3d}")
    kill = [r for r in rows if r[7]]
    print(f"\n{len(kill)} of {len(rows)} reproduce the killer cell; best of "
          f"those:")
    for tot, sc, s, signed, route, w1, w2, k in kill[:5]:
        print(f"  tt4 {sc[0]:5d} tt3 {sc[1]:5d} tt1 {sc[2]:5d}  s={s} "
              f"signed={signed} route={route} w1={w1} w2={w2}")


if __name__ == "__main__":
    main()
