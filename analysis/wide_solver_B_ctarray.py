#!/usr/bin/env python3
"""Column-truncated array with a normaliser-side compensation.

This is the only shape consistent with all the measurements at once:

* the array omits partial-product bits below a FIXED column T of the raw
  subpixel frame dm * disp.  Since disp carries tz trailing zeros, every
  partial product with j < tz is absent, so the dropped sum -- and hence the
  effective compensation -- depends on tz.  That is B9: K is +1/-7/+9/-4 for
  tz = 4/5/6/9 at fixed cut.  It also gates tt3 automatically (tz=13 means
  nothing is dropped for any T <= 13).
* the compensation constant is added by the NORMALISER, after the result is
  aligned, so it is result-relative (`c * 2^(bl-30)`) and therefore constant
  as cut varies at fixed tz.  That is the other half of B9: K is flat along
  each row of the (tz, cut) table.

An absolute compensation is excluded (it would scale as 2^-cut along a row)
and a result-relative truncation is excluded (it would be tz-blind).

For each T the optimal c is SOLVED, not swept: every capture pins c to an
interval, and interval stabbing returns both the best c and the ceiling of
the family at that T.
"""

from __future__ import annotations

import sys
from fractions import Fraction as F

sys.path[:0] = ["/tmp/walle", "/tmp/walle/analysis"]

import wide_solver_data as W  # noqa: E402
import wide_solver_B_data as B  # noqa: E402
import wide_solver_xmap as XM  # noqa: E402
import _sweep_fused_join_lattice as m  # noqa: E402

SETS = [("tt3", 13), ("tt4", 6), ("tt1", 7),
        ("tz3", 3), ("tz4", 4), ("tz5", 5), ("tz8", 8), ("tz9", 9)]


def rows_for(name: str, tz: int):
    return W.load(name) if name in ("tt3", "tt4", "tt1") else B.load_tz(tz)


def dropped(dm: int, didx: int, T: int) -> int:
    """Sum of partial-product bits in columns < T (the omitted corner)."""
    total = 0
    j = 0
    d = didx
    while d and j < T:
        if d & 1:
            total += (dm & ((1 << (T - j)) - 1)) << j
        d >>= 1
        j += 1
    return total


def c_interval(dm, e, d_o, sign, c_word, tz, T):
    """Admissible interval for the normaliser compensation c, in ulp30."""
    P = dm * d_o
    frame = P << tz
    V = frame - dropped(dm, d_o << tz, T)
    if V <= 0:
        return None
    sign_c, mant_c, e_c = m.f32_parts(c_word)
    if sign_c != sign:
        return None
    target = mant_c << (e_c - e + tz)
    lo, hi = XM.preimage(target)
    if lo > hi:
        return None
    unit = F(1, 1) * 2 ** (V.bit_length() - 30)
    return (F(lo - V) / unit, F(hi - V) / unit)


def stab(intervals):
    ev = []
    for lo, hi in intervals:
        ev.append((lo, 0))
        ev.append((hi, 1))
    ev.sort()
    best = cur = 0
    pt = None
    for x, k in ev:
        if k == 0:
            cur += 1
            if cur > best:
                best, pt = cur, x
        else:
            cur -= 1
    return best, pt


def main() -> None:
    data = [(n, tz, rows_for(n, tz)) for n, tz in SETS]
    lo = int(sys.argv[1]) if len(sys.argv) > 1 else 7
    hi = int(sys.argv[2]) if len(sys.argv) > 2 else 17
    for T in range(lo, hi + 1):
        per = {}
        allint = []
        for name, tz, rows in data:
            ivs = []
            for dm, e, d_o, sign, c_word in rows:
                iv = c_interval(dm, e, d_o, sign, c_word, tz, T)
                if iv is not None:
                    ivs.append(iv)
            best, pt = stab(ivs)
            per[name] = (best, len(rows))
            allint += ivs
        joint, cpt = stab(allint)
        line = "  ".join(f"{n} {per[n][0]}/{per[n][1]}" for n, _ in SETS)
        total = sum(v[1] for v in per.values())
        print(f"T={T:2d}  joint-best c={float(cpt) if cpt is not None else 0:+8.3f}"
              f" -> {joint}/{total}   per-set ceilings: {line}")


if __name__ == "__main__":
    main()
