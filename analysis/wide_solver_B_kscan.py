#!/usr/bin/env python3
"""Best global compensation constant per tz class.

Robust companion to the threshold brackets: for each capture, sweep the
compensation K in `narrow(P + K * 2^(bl(P)-30))` and report the K that
maximises exact word agreement.  All the tz-class captures share tt4's dm
scan (dm pinned near 2^23), so K's dependence on tz is isolated.
"""

from __future__ import annotations

import sys

sys.path[:0] = ["/tmp/walle", "/tmp/walle/analysis"]

import wide_solver_data as W  # noqa: E402
import wide_solver_B_data as B  # noqa: E402
import wide_solver_B_bias as BB  # noqa: E402


def law(K: int):
    def f(dm, d_o, z):
        P = dm * d_o
        bl = P.bit_length()
        k = max(0, 30 - bl)
        v = (P << k) + (K << (bl - 30 + k))
        if v <= 0:
            return W.narrow(P)
        mant, sh = W.narrow(v)
        return mant, sh - k
    return f


def main() -> None:
    names = ["tt3", "tt4", "tt1"] + [
        f"tz{t}" for t in sorted(B.TZ_SETS)
        if (B.ROOT / B.TZ_SETS[t][0] / "capture.raw").exists()]
    print(f"{'set':6s} {'tz':>3s} {'n':>6s} {'K=0':>7s} {'bestK':>6s} "
          f"{'best':>7s}  {'gain':>6s}")
    for name in names:
        if name in ("tt3", "tt4", "tt1"):
            rows = W.load(name)
            tz = {"tt3": 13, "tt4": 6, "tt1": 7}[name]
        else:
            tz = int(name[2:])
            rows = B.load_tz(tz)
        base = B.score(rows, law(0), tz)[0]
        best = max(((B.score(rows, law(K), tz)[0], K)
                    for K in range(-40, 41)))
        print(f"{name:6s} {tz:3d} {len(rows):6d} {base:7d} {best[1]:6d} "
              f"{best[0]:7d}  {best[0]-base:+6d}")


if __name__ == "__main__":
    main()
