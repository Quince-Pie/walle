#!/usr/bin/env python3
"""Vectorised exact scoring: same contract as wide_solver_fast, numpy speed.

A candidate is a function of numpy int64 arrays (dm, d_o) returning the
value V fed to the narrow-law export.  All arithmetic stays inside int64
(products are < 2^40), so results are exact.
"""

from __future__ import annotations

import sys

import numpy as np

sys.path[:0] = ["/tmp/walle/analysis"]

from wide_solver_fast import DATA, NAMES  # noqa: E402

ARR = {}
for _n in NAMES:
    cols = list(zip(*DATA[_n]))
    ARR[_n] = tuple(np.array(c, dtype=np.int64) for c in cols)


def bitlen(a: np.ndarray) -> np.ndarray:
    """Exact bit length of non-negative int64 values."""
    out = np.zeros(a.shape, dtype=np.int64)
    x = a.copy()
    for k in (32, 16, 8, 4, 2, 1):
        mask = x >= (np.int64(1) << k)
        out[mask] += k
        x[mask] >>= k
    return out + (a > 0)


def quant(v: np.ndarray, width: int, mode: str) -> np.ndarray:
    """Round v to `width` significant bits; returns the rounded VALUE."""
    sh = np.maximum(bitlen(v) - width, 0)
    base = v >> sh
    rem = v - (base << sh)
    if mode == "rtz":
        return base << sh
    if mode == "rodd":
        return (base | (rem != 0)) << sh
    half = np.where(sh > 0, np.int64(1) << np.maximum(sh - 1, 0), 0)
    if mode == "rna":
        inc = rem >= half
    elif mode == "rne":
        inc = (rem > half) | ((rem == half) & ((base & 1) == 1))
    elif mode == "rup":
        inc = rem > 0
    else:
        raise ValueError(mode)
    inc &= sh > 0
    return (base + inc) << sh


def score(law, name: str) -> int:
    dm, d_o, p, lo, hi = ARR[name]
    v = law(dm, d_o)
    d = v - p
    return int(np.count_nonzero((d >= lo) & (d <= hi)))


def score_all(law) -> tuple[int, int, int]:
    return tuple(score(law, n) for n in NAMES)


def report(tag: str, law) -> tuple[int, int, int]:
    s = score_all(law)
    print(f"{tag:56s} tt4 {s[0]:5d}/18001  tt3 {s[1]:5d}/18001  "
          f"tt1 {s[2]:4d}/2610")
    return s


if __name__ == "__main__":
    report("narrow law (baseline)", lambda dm, d_o: dm * d_o)
