#!/usr/bin/env python3
"""Measure the effective compensation per tz class from the new captures.

Same method that pinned tt4's compensation at exactly +9 ulp30: on the
low-dm block (dm = 0x800000+t, t=0..255) bracket the dropped-fraction at
which the hardware starts rounding up, per (cut, mantissa parity), and
compare with the narrow law's own threshold.  With v = 2^(bl(P)-30) the
narrow law rounds up at 36v (M even) / 28v (M odd); the shift is the
compensation in ulp30.

All five new captures use tt4's dm scan, so dm is pinned near 2^23 in every
one of them.  Any difference between classes is therefore attributable to
tz alone -- which is exactly what tt1 could not tell us, since tt1 differs
from tt4 in BOTH tz and dm distribution.
"""

from __future__ import annotations

import sys
from collections import defaultdict

sys.path[:0] = ["/tmp/walle", "/tmp/walle/analysis"]

import wide_solver_data as W  # noqa: E402
import wide_solver_B_data as B  # noqa: E402
import wide_solver_thresh as TH  # noqa: E402
import _sweep_fused_join_lattice as m  # noqa: E402


def rows_for(name: str):
    if name == "tt4":
        return W.load("tt4")
    return B.load_tz(int(name[2:]))


def deviations(name: str):
    for dm, e, d_o, sign, c_word in rows_for(name):
        P = dm * d_o
        mant_n, sh = W.narrow(P)
        _, mant_c, e_c = m.f32_parts(c_word)
        g = 1 << sh
        yield dict(dm=dm, d_o=d_o, P=P, bl=P.bit_length(), cut=sh,
                   drop=P & (g - 1), D=((mant_c << (e_c - e)) - (mant_n << sh))
                   // g)


def main() -> None:
    names = ["tt4"] + [f"tz{t}" for t in sorted(B.TZ_SETS)
                       if (B.ROOT / B.TZ_SETS[t][0] / "capture.raw").exists()]
    for name in names:
        buckets = defaultdict(list)
        for x in deviations(name):
            if x["dm"] - 0x800000 >= 256:
                continue                      # low-dm block only
            M = x["P"] >> x["cut"]
            up = x["D"] + (1 if x["drop"] >= TH.narrow_threshold(
                x["cut"], M & 1) else 0)
            buckets[(x["cut"], M & 1)].append((x["drop"], up))
        shifts = []
        detail = []
        for (cut, p), pts in sorted(buckets.items()):
            v = 2 ** (cut - 6)
            ups = [d for d, u in pts if u]
            dns = [d for d, u in pts if not u]
            if not ups or not dns:
                continue
            lo, hi = max(dns), min(ups)
            if lo >= hi:
                detail.append(f"cut={cut} p{p}: NOT separable")
                continue
            shift = (TH.narrow_threshold(cut, p) - hi) / v
            shifts.append(shift)
            detail.append(f"cut={cut} p{p}: shift {shift:+.3f}")
        med = sorted(shifts)[len(shifts) // 2] if shifts else float("nan")
        print(f"{name:6s} clean brackets {len(shifts):2d}  "
              f"median compensation {med:+.3f} ulp30   "
              f"range [{min(shifts):+.2f}, {max(shifts):+.2f}]"
              if shifts else f"{name}: no clean brackets")
        for line in detail[:8]:
            print("        " + line)


if __name__ == "__main__":
    main()
