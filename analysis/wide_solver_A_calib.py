#!/usr/bin/env python3
"""Track A calibration: reproduce the banked cross-track rules in the frame.

Before sweeping the segmented family, check that the 48-bit frame harness
reproduces the rules the other tracks already banked, and find which
fixed-frame injection form actually keeps tt3 at 18001 (the lead reports
T=17 K=9 with tt3 "derived", which is worth verifying rather than
assuming).
"""

from __future__ import annotations

import sys

sys.path[:0] = ["/tmp/walle/analysis"]

from wide_solver_A_frame import passes_killer, report, score_all  # noqa: E402


def inject_then_trunc(t: int, k: int):
    """V48 = ((P48 + k*2^t) >> t) << t  -- constant at the cut."""
    return lambda a: ((a["P48"] + (k << t)) >> t) << t


def trunc_then_inject(t: int, k: int):
    """V48 = ((P48 >> t) + k) << t."""
    return lambda a: ((a["P48"] >> t) + k) << t


def subcut_constant(t: int, c: int):
    """V48 = ((P48 + c) >> t) << t with c < 2^t.

    A rounding constant strictly below the cut is invisible whenever no
    column is dropped, so tt3 (>= 18 trailing zeros) stays exact for any
    t <= 18 with no product-width side condition.
    """
    return lambda a: ((a["P48"] + c) >> t) << t


def main() -> None:
    report("identity (narrow law)", lambda a: a["P48"])
    print()
    for t in (16, 17, 18):
        law = inject_then_trunc(t, 9)
        report(f"inject-then-trunc  T={t} K=9", law)
        print(f"{'':52s}   killer cell: {passes_killer(law)}")
        report(f"trunc-then-inject  T={t} K=9", trunc_then_inject(t, 9))
    print()
    best = []
    for t in range(12, 22):
        step = max(1, (1 << t) // 128)
        for c in range(0, 1 << t, step):
            s = score_all(subcut_constant(t, c))
            best.append((s[0] + s[2], s, t, c))
    best.sort(reverse=True)
    print("sub-cut rounding constant C < 2^T (tt3-safe by construction):")
    print("   tt4   tt3   tt1    T        C      C/2^17")
    for tot, s, t, c in best[:14]:
        print(f"{s[0]:6d} {s[1]:5d} {s[2]:5d} {t:4d} {c:8d} "
              f"{c / (1 << 17):9.3f}")


if __name__ == "__main__":
    main()
