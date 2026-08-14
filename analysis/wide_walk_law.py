#!/usr/bin/env python3
"""THE WALK HYPOTHESIS: C(tile row) is produced by a sequential walk
from the anchor row, quantizing after each step:

    c(ty0)   = dm * d_o(ty0)          (first covered row, exact if narrow)
    c(ty+1)  = Q( c(ty) + dm * dstep )

with dstep = d_o row pitch (tt4: 128, tt1: 64, tt3: 1) and Q a 24/27-bit
quantizer.  Narrow datasets stay exact because no partial sum ever
rounds - reproducing the proven narrow law for free.  Score every
(dm, ty) cell of tt1/tt3/tt4 against the walk prediction.
"""
from __future__ import annotations

import sys

sys.path[:0] = ["/tmp/walle", "/tmp/walle/analysis"]

import wide_solver_data as W  # noqa: E402

# name -> (row pitch of d_o per tile row, first d_o)
FRames = {
    "tt4": 128,
    "tt3": 1,
    "tt1": 64,
}


def q_bits_int(x: int, nbits: int, mode: str) -> int:
    bl = x.bit_length()
    sh = bl - nbits
    if sh <= 0:
        return x
    low = x & ((1 << sh) - 1)
    half = 1 << (sh - 1)
    y = x >> sh
    if mode == "rtz":
        pass
    elif mode == "rna":
        if low >= half:
            y += 1
    elif mode == "rne":
        if low > half or (low == half and y & 1):
            y += 1
    elif mode == "jam":
        y = y | 1
    if y.bit_length() > nbits:
        y >>= 1
        sh += 1
    return y << sh


def chain_q(x: int) -> int:
    return q_bits_int(q_bits_int(x, 27, "rna"), 24, "rne")


QUANTS = {
    "rne24": lambda x: q_bits_int(x, 24, "rne"),
    "rna24": lambda x: q_bits_int(x, 24, "rna"),
    "rtz24": lambda x: q_bits_int(x, 24, "rtz"),
    "jam24": lambda x: q_bits_int(x, 24, "jam"),
    "rne27": lambda x: q_bits_int(x, 27, "rne"),
    "rna27": lambda x: q_bits_int(x, 27, "rna"),
    "rtz27": lambda x: q_bits_int(x, 27, "rtz"),
    "jam27": lambda x: q_bits_int(x, 27, "jam"),
    "chain": chain_q,
    "rne28": lambda x: q_bits_int(x, 28, "rne"),
    "rna28": lambda x: q_bits_int(x, 28, "rna"),
}


def main() -> None:
    names = sys.argv[1:] or ["tt4", "tt3", "tt1"]
    data = {}
    for n in names:
        obs = W.load(n)
        cells = {}
        for dm, e, d_o, sign, c_word in obs:
            cells.setdefault(dm, {})[d_o] = (sign, e, c_word)
        data[n] = cells
    for qname, Q in QUANTS.items():
        line = f"{qname:6s}"
        for n in names:
            pitch = FRames[n]
            ok = tot = 0
            for dm, rows in data[n].items():
                d_sorted = sorted(rows)
                c = dm * d_sorted[0]
                prev = d_sorted[0]
                for d_o in d_sorted:
                    c = c + dm * (d_o - prev)
                    prev = d_o
                    # quantize the accumulator AFTER each row step
                    cq = Q(c)
                    sign, e, c_word = rows[d_o]
                    try:
                        mant24 = q_bits_int(cq, 24, "rne")
                        pred = W.f32_from_int(sign, mant24 >> max(
                            0, mant24.bit_length() - 24), e + max(
                            0, mant24.bit_length() - 24))
                    except (ValueError, ZeroDivisionError):
                        pred = None
                    tot += 1
                    ok += pred == c_word
                    c = cq
            line += f"  {n}={ok}/{tot}"
        print(line)


if __name__ == "__main__":
    main()
