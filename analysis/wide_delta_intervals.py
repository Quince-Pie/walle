#!/usr/bin/env python3
"""Per-cell required-bias intervals for the wide C path.

For each wide (bl>=31) cell: find the interval [lo, hi) of delta (in
units of 2^(bl-31), i.e. v/2 with v = 2^(cut-6)) such that
chain(P + delta) == captured word.  The chain is monotone, so the
preimage is one interval; scan delta in half-v steps over +-64v.
Dump one line per cell: dataset, d_o, dm, bl, parity(M), lo, hi.
"""
from __future__ import annotations

import sys

sys.path[:0] = ["/tmp/walle", "/tmp/walle/analysis"]

import wide_solver_data as W  # noqa: E402


def chain_word(P2: int, sign: int, e: int) -> int | None:
    # P2 in doubled units (delta granularity v/2): exponent e-1
    if P2 <= 0:
        return None
    try:
        mant, k = W.narrow(P2)
        return W.f32_from_int(sign, mant, e + k - 1)
    except (ValueError, ZeroDivisionError):
        return None


def main() -> None:
    out = open(sys.argv[2] if len(sys.argv) > 2 else
               "/dev/stdout", "w")
    for name in (sys.argv[1:2] or ["tt4"]):
        for dm, e, d_o, sign, c_word in W.load(name):
            P = dm * d_o
            bl = P.bit_length()
            if bl < 31:
                continue
            v2 = 1 << (bl - 31)          # v/2 in P units
            P2 = P << 1
            lo = None
            hi = None
            for step in range(-128, 129):
                w = chain_word(P2 + step * v2, sign, e)
                if w == c_word:
                    if lo is None:
                        lo = step
                    hi = step
                elif lo is not None:
                    break
            if lo is None:
                out.write(f"{name} {d_o} {dm:#x} {bl} ? none\n")
                continue
            M27, _ = W.narrow(P)
            out.write(f"{name} {d_o} {dm:#x} {bl} {M27 & 1} "
                      f"{lo} {hi}\n")
    out.close()


if __name__ == "__main__":
    main()
