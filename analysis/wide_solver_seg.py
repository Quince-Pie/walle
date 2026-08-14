#!/usr/bin/env python3
"""Family S: segmented multiplier.

One operand is split hi/lo at bit s; each partial product is quantised
through the narrow-law datapath (width W, mode) on its own, then the two
are re-aligned and summed and the sum goes to the narrow-law export.
Sweeps the split point, both partial widths/modes, and the operand that
gets split (dm, raw d_o, or d_o normalised to 24 bits).
"""

from __future__ import annotations

import sys

import numpy as np

sys.path[:0] = ["/tmp/walle/analysis"]

from wide_solver_np import ARR, bitlen, quant, score_all  # noqa: E402

MODES = ("rtz", "rne", "rna", "rup", "rodd")


def norm24(d_o: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """didx normalised to 24 significant bits, plus the shift applied."""
    sh = 24 - bitlen(d_o)
    return d_o << sh, sh


def make(split_on: str, s: int, wh: int, mh: str, wl: int, ml: str):
    def law(dm, d_o):
        if split_on == "dm":
            a, b = dm, d_o
        elif split_on == "do":
            a, b = d_o, dm
        else:                                   # normalised didx
            a, b = norm24(d_o)[0], dm
        hi = a >> s
        lo = a - (hi << s)
        ph = quant(hi * b, wh, mh)
        pl = quant(lo * b, wl, ml)
        v = (ph << s) + pl
        if split_on == "don":
            v = v >> norm24(d_o)[1]
        return v
    return law


def main() -> None:
    results = []
    for split_on in ("dm", "do", "don"):
        for s in range(8, 17):
            for wh in range(22, 31):
                for mh in MODES:
                    for wl in range(22, 31):
                        for ml in MODES:
                            if (wh, mh) > (wl, ml) and False:
                                continue
                            law = make(split_on, s, wh, mh, wl, ml)
                            t4, t3, t1 = score_all(law)
                            if t3 != 18001:
                                continue
                            results.append((t4 + t3 + t1, t4, t3, t1,
                                            split_on, s, wh, mh, wl, ml))
    results.sort(reverse=True)
    print("total   tt4   tt3   tt1  split  s  wh  mh    wl  ml")
    for r in results[:30]:
        print(f"{r[0]:6d} {r[1]:5d} {r[2]:5d} {r[3]:4d}  {r[4]:5s} {r[5]:2d} "
              f"{r[6]:3d} {r[7]:5s} {r[8]:3d} {r[9]:5s}")
    print(f"\n({len(results)} tt3-preserving combinations)")


if __name__ == "__main__":
    main()
