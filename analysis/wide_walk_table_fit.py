#!/usr/bin/env python3
"""Fit residual-register walk variants against the ladder deviation
tables (later-87).  Every shared (dm, d_o) cell across anchor classes
gives a crisp constraint: walk(dm, S, L) must reproduce the exported
word for each S in the ladder.

Variant space:
  R      : per-step state rounding - rna27 / rne27 / rne28 / rna28
  resw   : residual register width in ulp27-fraction bits (grid =
           ulp27 / 2^resw); resw=None disables the register
  qmode  : residual quantization - rne / rtz / floor
  sat    : residual saturation - none / half (|res| <= ulp27/2) /
           wrap (res mod ulp27, signed)
  export : rne24 of the state
Scored over ALL cells of tt3/tt4/tz4/tz5/tz8/tz9 (walk from d_o
= first probed row per dm as seed, exact seed product rounded by R).
"""
from __future__ import annotations

import sys

sys.path[:0] = ["/tmp/walle", "/tmp/walle/analysis"]

import wide_solver_data as W  # noqa: E402
import wide_solver_B_data as B  # noqa: E402


def q_at(x: int, nbits: int, mode: str) -> int:
    bl = x.bit_length()
    sh = bl - nbits
    if sh <= 0:
        return x
    low = x & ((1 << sh) - 1)
    half = 1 << (sh - 1)
    y = x >> sh
    if mode == "rna":
        if low >= half:
            y += 1
    elif mode == "rne":
        if low > half or (low == half and y & 1):
            y += 1
    elif mode == "rtz":
        pass
    if y.bit_length() > nbits:
        y >>= 1
        sh += 1
    return y << sh


def rne24_int(x: int) -> int:
    return q_at(x, 24, "rne")


def qres(x: int, grid: int, mode: str) -> int:
    if grid <= 1 or x == 0:
        return x
    if mode == "floor":
        return (x // grid) * grid
    if mode == "rtz":
        return (abs(x) // grid) * grid * (1 if x >= 0 else -1)
    q, rem = divmod(x, grid)
    if 2 * rem > grid or (2 * rem == grid and q & 1):
        q += 1
    return q * grid


def load_sets():
    sets = {}
    for n, S in (("tt4", 128), ("tt3", 1)):
        cells = {}
        for dm, e, d_o, sign, c_word in W.load(n):
            cells.setdefault(dm, {})[d_o] = (sign, e, c_word)
        sets[n] = (S, cells)
    for tz, S in ((4, 512), (5, 256), (8, 32), (9, 16)):
        cells = {}
        for dm, e, d_o, sign, c_word in B.load_tz(tz):
            cells.setdefault(dm, {})[d_o] = (sign, e, c_word)
        sets[f"tz{tz}"] = (S, cells)
    return sets


def run(sets, Rbits, Rmode, resw, qmode, sat):
    scores = {}
    for n, (S, cells) in sets.items():
        ok = tot = 0
        for dm, rows in cells.items():
            ds = sorted(rows)
            c = q_at(dm * ds[0], Rbits, Rmode)
            res = dm * ds[0] - c
            prev = ds[0]
            for d_o in ds:
                while prev < d_o:
                    t = c + dm * S + res
                    c = q_at(t, Rbits, Rmode)
                    res = t - c
                    if resw is None:
                        res = 0
                    else:
                        ulp = 1 << max(0, c.bit_length() - Rbits)
                        grid = max(1, ulp >> resw)
                        res = qres(res, grid, qmode)
                        if sat == "half":
                            lim = ulp >> 1
                            res = max(-lim, min(lim, res))
                        elif sat == "wrap":
                            res = ((res + (ulp >> 1)) % ulp) - (ulp >> 1)
                    prev += S
                sign, e, c_word = rows[d_o]
                ex = rne24_int(c)
                sh = max(0, ex.bit_length() - 24)
                try:
                    pred = W.f32_from_int(sign, ex >> sh, e + sh)
                except (ValueError, ZeroDivisionError):
                    pred = None
                tot += 1
                ok += pred == c_word
        scores[n] = (ok, tot)
    return scores


def main() -> None:
    sets = load_sets()
    results = []
    for Rbits, Rmode in ((27, "rna"), (27, "rne"), (28, "rne"), (28, "rna")):
        for resw in (None, 0, 1, 2, 3, 4):
            for qmode in (("rne", "rtz", "floor") if resw is not None
                          else ("rne",)):
                for sat in ("none", "half", "wrap"):
                    if resw is None and sat != "none":
                        continue
                    sc = run(sets, Rbits, Rmode, resw, qmode, sat)
                    total = sum(v[0] for v in sc.values())
                    results.append((total, Rbits, Rmode, resw, qmode, sat, sc))
    results.sort(key=lambda r: r[0], reverse=True)
    for total, Rbits, Rmode, resw, qmode, sat, sc in results[:10]:
        print(f"{total:6d} R={Rbits}{Rmode} resw={resw} {qmode:5s} "
              f"sat={sat:4s}: "
              + " ".join(f"{n}={v[0]}" for n, v in sorted(sc.items())))


if __name__ == "__main__":
    main()
