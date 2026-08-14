#!/usr/bin/env python3
"""Track A: segmented multiplier in the raw subpixel frame.

    R  = dm * disp                       (disp = subpixel displacement)
    H  = dm >> s,  L = dm - (H << s)     (optionally a SIGNED low segment,
                                          L in [-2^(s-1), 2^(s-1)) with the
                                          borrow carried into H)
    V  = Q(H * disp) << s  +  Q(L * disp)

with each partial independently quantised to a significant width.  tt3 is
exact by construction, not by a product-width gate: tt3's displacements
have odd parts of at most 6 bits, so `H * disp` carries at most `30 - s`
significant bits and `L * disp` at most `s + 6`; any width >= 20 is a
no-op on tt3 for every split in 10..14.  tt4 and tt1 reach 25-27
significant bits in the same partials, so widths 20..24 bite there only.

An ABSOLUTE column cut cannot work here and is not swept: tt3 forces the
cut to bit <= 13 (its displacements carry only 13 trailing zeros), which
caps the achievable deviation at 2^13 raw units, while the killer cell
needs ~2^16.  Significant-width quantisation has the dynamic range.
"""

from __future__ import annotations

import sys

import numpy as np

sys.path[:0] = ["/tmp/walle/analysis"]

from wide_solver_A_frame import (ARR, NAMES, killer_index,  # noqa: E402
                                 report, score_all)
from wide_solver_np import bitlen, quant  # noqa: E402

MODES = ("rtz", "rne", "rna", "rup", "rodd")


def quant_signed(v: np.ndarray, width: int, mode: str) -> np.ndarray:
    """Quantise |v| to `width` significant bits, restoring the sign."""
    if mode == "floor":                       # round toward -infinity
        sh = np.maximum(bitlen(np.abs(v)) - width, 0)
        return (v >> sh) << sh
    neg = v < 0
    q = quant(np.abs(v), width, mode)
    return np.where(neg, -q, q)


def make(s: int, signed: bool, wh: int, mh: str, wl: int, ml: str):
    def law(a):
        dm, disp = a["dm"], a["disp"]
        hi = dm >> s
        lo = dm - (hi << s)
        if signed:
            borrow = (lo >= (1 << (s - 1))).astype(np.int64)
            lo = lo - (borrow << s)
            hi = hi + borrow
        return ((quant_signed(hi * disp, wh, mh) << s)
                + quant_signed(lo * disp, wl, ml))
    return law


def main() -> None:
    report("identity (narrow law, raw frame)", lambda a: a["R"], "raw")
    a4 = ARR["tt4"]
    ki = killer_index()
    print(f"killer cell needs V-R in "
          f"[{int(a4['rlo'][ki] - a4['R'][ki])}, "
          f"{int(a4['rhi'][ki] - a4['R'][ki])}] raw units "
          f"(one granule = {1 << (int(a4['bl'][ki]) - 24 + 6)})\n")

    survivors = []
    tested = 0
    for s in range(10, 15):
        for signed in (False, True):
            for wh in range(18, 28):
                for mh in MODES + ("floor",):
                    for wl in range(18, 28):
                        for ml in MODES + ("floor",):
                            tested += 1
                            law = make(s, signed, wh, mh, wl, ml)
                            v = law(a4)[ki]
                            if not (a4["rlo"][ki] <= v <= a4["rhi"][ki]):
                                continue
                            survivors.append((s, signed, wh, mh, wl, ml))
    print(f"{tested} combinations, {len(survivors)} pass the killer cell")

    scored = []
    for combo in survivors:
        law = make(*combo)
        sc = score_all(law, "raw")
        scored.append((sc[0] + sc[1] + sc[2], sc, combo))
    scored.sort(reverse=True)
    print("\ntotal   tt4   tt3   tt1   s  signed  wh mh     wl ml")
    for tot, sc, combo in scored[:25]:
        s, signed, wh, mh, wl, ml = combo
        print(f"{tot:6d} {sc[0]:5d} {sc[1]:5d} {sc[2]:5d} {s:3d} {signed!s:6s} "
              f"{wh:3d} {mh:5s} {wl:3d} {ml}")
    if scored:
        print(f"\nbest tt3-preserving: "
              f"{max((x for x in scored if x[1][1] == 18001), default=None)}")


if __name__ == "__main__":
    main()
