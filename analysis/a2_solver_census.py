#!/usr/bin/env python3
"""Full-sweep census of secondary-selection evidence.

Scans every border-grid state, counts sensitive pixels and the ones apple
resolved with the 0x3BFF secondary, and reports the per-tile shape of each
LOW cluster.  A transfer-plane dip covers a whole tile's worth of the
antialiased arc; an isolated LOW inside an otherwise-HIGH tile cannot come
from any spatially monotone plane and is a raster-precision residual.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "analysis"))

import a2_solver_constraints as constraints  # noqa: E402
import a2_solver_primary as primary  # noqa: E402

COMPACT_STATES = (5, 11, 16, 21, 22, 27, 32, 38, 43, 48, 54, 59)
TILE = 32


def main() -> int:
    base, bitmap = primary.load_tables()
    total_sensitive = 0
    total_low = 0
    total_excluded = 0
    plane_pixels = 0
    raster_pixels = 0
    for state in range(65):
        if state in COMPACT_STATES:
            continue
        try:
            built = constraints.build(state, base=base, bitmap=bitmap)
        except (NotImplementedError, FileNotFoundError):
            continue
        ys, xs = np.nonzero(built.labels != constraints.LABEL_NONE)
        if not len(ys):
            continue
        low = built.labels[ys, xs] == constraints.LABEL_LOW
        excluded = int(
            np.count_nonzero(built.labels == constraints.LABEL_EXCLUDED)
        )
        total_sensitive += len(ys)
        total_low += int(low.sum())
        total_excluded += excluded
        if not low.any():
            continue
        keys = (ys // TILE).astype(np.int64) * 4096 + (xs // TILE)
        clusters = []
        for key in np.unique(keys):
            here = keys == key
            low_count = int(low[here].sum())
            if not low_count:
                continue
            high_count = int((~low[here]).sum())
            clusters.append((int(key % 4096), int(key // 4096), low_count, high_count))
            if high_count == 0 and low_count >= 2:
                plane_pixels += low_count
            else:
                raster_pixels += low_count
        print(
            f"state {state}: sensitive={len(ys)} low={int(low.sum())} "
            f"excluded={excluded} clusters="
            + " ".join(
                f"({tx},{ty}):{lo}L/{hi}H" for tx, ty, lo, hi in clusters
            )
        )
    print(
        f"TOTAL sensitive={total_sensitive} low={total_low} "
        f"excluded={total_excluded}"
    )
    print(
        f"tile-filling LOW clusters (transfer-plane class) = {plane_pixels} px; "
        f"isolated LOW (raster class) = {raster_pixels} px"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
