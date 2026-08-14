#!/usr/bin/env python3
"""How much of the product does the wide datapath actually see?

If the hardware keeps only W bits of P, then any two captures whose
products agree in their top W bits (and share a bit length) must export
the same mantissa.  Counts violations of that for W = 24..40, under
truncation, round-to-nearest and round-to-odd reductions to W bits.
"""

from __future__ import annotations

import sys
from collections import defaultdict

sys.path[:0] = ["/tmp/walle", "/tmp/walle/analysis"]

import wide_solver_data as W  # noqa: E402
import wide_solver_xmap as XM  # noqa: E402

DS = ("tt4", "tt3", "tt1")


def reduce_bits(p: int, width: int, mode: str) -> int:
    sh = max(p.bit_length() - width, 0)
    if sh == 0:
        return p
    if mode == "rtz":
        return p >> sh
    if mode == "rne":
        return W.rne(p, width)[0]
    if mode == "rodd":
        return W.rodd(p, width)[0]
    raise ValueError(mode)


def main() -> None:
    cells = []
    for ds in DS:
        for r in XM.observations(ds):
            # mantissa exported, normalised to the product's own scale
            cells.append((r["P"], r["bl"], r["mant"], ds))
    print(f"{len(cells)} captures total")
    print("  W  mode   distinct keys   keys with conflicting exports  "
          "cells in conflict")
    for width in range(24, 41, 2):
        for mode in ("rtz", "rne", "rodd"):
            groups = defaultdict(set)
            counts = defaultdict(int)
            for p, bl, mant, ds in cells:
                k = (bl, reduce_bits(p, width, mode))
                groups[k].add(mant)
                counts[k] += 1
            bad = [k for k, v in groups.items() if len(v) > 1]
            ncells = sum(counts[k] for k in bad)
            print(f" {width:3d}  {mode:5s} {len(groups):13d} "
                  f"{len(bad):25d} {ncells:17d}")


if __name__ == "__main__":
    main()
