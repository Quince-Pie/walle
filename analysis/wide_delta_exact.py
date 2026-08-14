#!/usr/bin/env python3
"""Exact preimage intervals: for each wide cell find [lo, hi) in P-units
such that chain(P + d) == captured word for d in [lo, hi).  Binary
search on the monotone chain, range +-8 granules.  Emit:
name d_o dm bl parity lo hi (P-units).
"""
from __future__ import annotations

import sys

sys.path[:0] = ["/tmp/walle", "/tmp/walle/analysis"]

import wide_solver_data as W  # noqa: E402


def word_of(Pp: int, sign: int, e: int):
    if Pp <= 0:
        return None
    try:
        mant, k = W.narrow(Pp)
        return W.f32_from_int(sign, mant, e + k)
    except (ValueError, ZeroDivisionError):
        return None


def main() -> None:
    name = sys.argv[1]
    out = open(sys.argv[2], "w")
    for dm, e, d_o, sign, c_word in W.load(name):
        P = dm * d_o
        bl = P.bit_length()
        if bl < 31:
            continue
        G = 1 << (bl - 24)               # one 24-bit granule
        span = 8 * G
        # find any matching d by coarse scan at granule/64 steps
        d0 = None
        step = max(1, G >> 6)
        for d in range(-span, span, step):
            if word_of(P + d, sign, e) == c_word:
                d0 = d
                break
        if d0 is None:
            out.write(f"{name} {d_o} {dm:#x} {bl} ? none none\n")
            continue
        # binary search lower edge in (d0-span, d0]
        a, b = d0 - span, d0
        while a + 1 < b:
            mid = (a + b) // 2
            if word_of(P + mid, sign, e) == c_word:
                b = mid
            else:
                a = mid
        lo = b
        a, b = d0, d0 + span
        while a + 1 < b:
            mid = (a + b) // 2
            if word_of(P + mid, sign, e) == c_word:
                a = mid
            else:
                b = mid
        hi = a + 1
        M27, _ = W.narrow(P)
        out.write(f"{name} {d_o} {dm:#x} {bl} {M27 & 1} {lo} {hi}\n")
    out.close()


if __name__ == "__main__":
    main()
