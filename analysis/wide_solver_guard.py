#!/usr/bin/env python3
"""Family G: array truncation in the 48-bit frame + rounder constant.

Solver-2 falsified a column-truncated array with an ABSOLUTE compensation,
and separately measured that the compensation is granule-relative.  This
combines them: the multiplier array drops every column below T of the
normalised 48-bit product (so only a few guard bits survive below the
result granule), and the rounder then adds a granule-relative constant.

In P units, dropping columns below T of `dm * (d_o << (24-bl(d_o)))` means
dropping `P mod 2^u` with `u = T - 24 + bl(d_o)`.  For tt3 every d_o is
small enough that `u <= 0` for T <= 18, so the narrow law is preserved
structurally rather than by fiat.
"""

from __future__ import annotations

import sys

import numpy as np

sys.path[:0] = ["/tmp/walle/analysis"]

from wide_solver_np import ARR, NAMES, bitlen, quant  # noqa: E402


def score_scaled(law, name: str, scale: int) -> int:
    dm, d_o, p, lo, hi = ARR[name]
    d = law(dm, d_o) - p * scale
    return int(np.count_nonzero((d >= lo * scale) & (d <= hi * scale)))


SCALE = 64


def make(t: int, const: int, mode: str, thresh: int = 30):
    def law(dm, d_o):
        p = dm * d_o
        bl = bitlen(p)
        u = np.maximum(t - 24 + bitlen(d_o), 0)
        if mode == "rtz":
            base = p - (p & ((np.int64(1) << u) - 1))
        else:
            base = quant_at(p, u, mode)
        wide = bl > thresh
        v = np.where(wide, base * SCALE + const * (np.int64(1) << (bl - 30)),
                     p * SCALE)
        if mode != "rtz":
            v = np.where(wide, v, p * SCALE)
        return v
    return law


def quant_at(p, u, mode):
    """Round p at bit position u (vectorised, exact)."""
    step = np.int64(1) << u
    base = (p >> u) << u
    rem = p - base
    half = step >> 1
    if mode == "rne":
        inc = (rem > half) | ((rem == half) & (((p >> u) & 1) == 1))
    elif mode == "rna":
        inc = rem >= half
    else:
        raise ValueError(mode)
    return base + np.where(inc & (u > 0), step, 0)


def main() -> None:
    results = []
    for t in range(8, 24):
        for mode in ("rtz", "rne", "rna"):
            for const in range(0, SCALE + 1):
                law = make(t, const, mode)
                s = tuple(score_scaled(law, n, SCALE) for n in NAMES)
                results.append((s[0] + s[1] + s[2], s, t, mode, const))
    results.sort(reverse=True)
    print("total   tt4   tt3   tt1   T  mode  const(/64 granule)")
    for tot, s, t, mode, const in results[:20]:
        print(f"{tot:6d} {s[0]:5d} {s[1]:5d} {s[2]:5d} {t:3d}  {mode:4s} "
              f"{const:3d}")


if __name__ == "__main__":
    main()
