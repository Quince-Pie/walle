#!/usr/bin/env python3
"""Sweep tile size AND phase for a per-tile-constant secondary.

The zero-phase test (a2_solver_tile_test.py) rejects per-tile constants, but
the AGX tile grid need not be aligned to pixel 0 of the target: the transfer
draw has its own origin.  This sweeps both offsets over every tile size and
reports any (size, phase) under which no tile mixes LOW with HIGH.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "analysis"))

import a2_solver_constraints as constraints  # noqa: E402
import a2_solver_primary as primary  # noqa: E402


def mixed_count(xs, ys, low, width, height, x_phase, y_phase) -> int:
    keys = ((ys + y_phase) // height).astype(np.int64) * 8192 + (
        (xs + x_phase) // width
    )
    total = 0
    for key in np.unique(keys):
        here = keys == key
        if low[here].any() and not low[here].all():
            total += 1
    return total


def main() -> int:
    base, bitmap = primary.load_tables()
    states = [int(value) for value in sys.argv[1:]] or [40, 41, 42, 58, 60]
    data = {}
    for state in states:
        built = constraints.build(state, base=base, bitmap=bitmap)
        ys, xs = np.nonzero(built.labels != constraints.LABEL_NONE)
        data[state] = (
            xs.astype(np.int64),
            ys.astype(np.int64),
            built.labels[ys, xs] == constraints.LABEL_LOW,
        )

    best: list[tuple[int, tuple[int, int, int, int]]] = []
    for width, height in ((16, 16), (32, 32), (64, 64), (128, 128),
                          (32, 16), (16, 32), (64, 32), (32, 64),
                          (2048, 16), (2048, 32), (16, 2048), (32, 2048)):
        for x_phase in range(width if width <= 128 else 1):
            for y_phase in range(height if height <= 128 else 1):
                total = 0
                for state in states:
                    xs, ys, low = data[state]
                    total += mixed_count(xs, ys, low, width, height, x_phase, y_phase)
                best.append((total, (width, height, x_phase, y_phase)))
    best.sort()
    for total, key in best[:12]:
        print(
            f"mixed tiles across all states = {total:3d} "
            f"for tile {key[0]}x{key[1]} phase ({key[2]},{key[3]})"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
