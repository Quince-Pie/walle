#!/usr/bin/env python3
"""Is apple's secondary constant per raster tile?

later-44 killed the per-tile-constant model using (1837,103) vs (1838,106),
but (1838,106) is a PRIMARY residual (see a2_solver_log entry 2), so the model
deserves a direct test against every sensitive pixel.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "analysis"))

import a2_solver_constraints as constraints  # noqa: E402
import a2_solver_primary as primary  # noqa: E402


def main() -> int:
    base, bitmap = primary.load_tables()
    states = [int(value) for value in sys.argv[1:]] or [40, 41, 42, 58, 60]
    for size in (8, 16, 32, 64, 128):
        print(f"tile {size}x{size}:")
        for state in states:
            state_constraints = constraints.build(state, base=base, bitmap=bitmap)
            labels = state_constraints.labels
            ys, xs = np.nonzero(labels != constraints.LABEL_NONE)
            keys = (ys // size).astype(np.int64) * 4096 + (xs // size)
            low = labels[ys, xs] == constraints.LABEL_LOW
            mixed = []
            low_tiles = []
            for key in np.unique(keys):
                selected = keys == key
                if low[selected].all():
                    low_tiles.append(key)
                elif low[selected].any():
                    mixed.append(
                        (
                            int(key % 4096),
                            int(key // 4096),
                            int(low[selected].sum()),
                            int((~low[selected]).sum()),
                        )
                    )
            print(
                f"  state {state}: tiles={len(np.unique(keys))} "
                f"all-low={len(low_tiles)} mixed={len(mixed)} {mixed[:6]}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
