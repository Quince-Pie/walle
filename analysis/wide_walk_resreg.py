#!/usr/bin/env python3
"""Residual-register walk fit (later-83 family).

    t          = c + dm*pitch + res
    c'         = R24(t)               (rne24 or the narrow chain)
    res'       = Qres(t - c')         (grid = ulp24(c')/2^r, lossy)

Sweep r, Qres mode, R24, seed handling; score tt4/tt3/tt1 exports.
"""
from __future__ import annotations

import sys

sys.path[:0] = ["/tmp/walle", "/tmp/walle/analysis"]

import wide_solver_data as W  # noqa: E402

PITCH = {"tt4": 128, "tt3": 1, "tt1": 64}


def rne24_int(x: int) -> int:
    bl = x.bit_length()
    sh = bl - 24
    if sh <= 0:
        return x
    low = x & ((1 << sh) - 1)
    half = 1 << (sh - 1)
    y = x >> sh
    if low > half or (low == half and y & 1):
        y += 1
    if y.bit_length() > 24:
        y >>= 1
        sh += 1
    return y << sh


def chain_int(x: int) -> int:
    bl = x.bit_length()
    sh = bl - 27
    if sh > 0:
        y = (x + (1 << (sh - 1))) >> sh
        if y.bit_length() > 27:
            y >>= 1
            sh += 1
        x = y << sh
    return rne24_int(x)


def qres(x: int, grid: int, mode: str) -> int:
    if grid <= 1:
        return x
    if mode == "floor":
        return (x // grid) * grid
    if mode == "rtz":
        return (abs(x) // grid) * grid * (1 if x >= 0 else -1)
    if mode == "rne":
        q, rem = divmod(x, grid)
        if 2 * rem > grid or (2 * rem == grid and q & 1):
            q += 1
        return q * grid
    raise ValueError(mode)


def main() -> None:
    names = sys.argv[1:] or ["tt4", "tt3", "tt1"]
    data = {}
    for n in names:
        cells = {}
        for dm, e, d_o, sign, c_word in W.load(n):
            cells.setdefault(dm, {})[d_o] = (sign, e, c_word)
        data[n] = cells
    results = []
    for r in (1, 2, 3, 4, 5, 6):
        for mode in ("floor", "rtz", "rne"):
            for rname, R in (("rne24", rne24_int), ("chain", chain_int)):
                scores = {}
                for n in names:
                    pitch = PITCH[n]
                    ok = tot = 0
                    for dm, rows in data[n].items():
                        ds = sorted(rows)
                        c = R(dm * ds[0])
                        res = dm * ds[0] - c
                        grid = max(1, (1 << max(0, c.bit_length() - 24)) >> r)
                        res = qres(res, grid, mode)
                        prev = ds[0]
                        for d_o in ds:
                            if d_o != prev:
                                steps = (d_o - prev) // pitch
                                for _ in range(steps):
                                    t = c + dm * pitch + res
                                    c = R(t)
                                    grid = max(1, (1 << max(
                                        0, c.bit_length() - 24)) >> r)
                                    res = qres(t - c, grid, mode)
                                prev = d_o
                            sign, e, c_word = rows[d_o]
                            bl = c.bit_length()
                            sh = max(0, bl - 24)
                            try:
                                pred = W.f32_from_int(sign, c >> sh, e + sh)
                            except (ValueError, ZeroDivisionError):
                                pred = None
                            tot += 1
                            ok += pred == c_word
                    scores[n] = (ok, tot)
                gain = sum(s[0] for s in scores.values())
                results.append((gain, r, mode, rname, scores))
    results.sort(reverse=True)
    for gain, r, mode, rname, scores in results[:12]:
        print(f"r={r} {mode:5s} {rname:5s} "
              + " ".join(f"{n}={v[0]}/{v[1]}" for n, v in scores.items()))


if __name__ == "__main__":
    main()
