#!/usr/bin/env python3
"""Rescore the corpus with the solved transfer-alpha plane applied.

The plane is expressed the way the hardware exports it: a per-transfer-tile
binary32 constant plus (negligible) slopes, so the secondary is
0x3BFF wherever the tile constant is 1 - 2^-24.  The solved polytope
(a2_solver_log entry 5) pins exactly one such tile in the whole corpus.

This is a validation of the SOLVED plane, not yet of a generation rule: the
open item is the setup law that decides which tiles export 1 - 2^-24.
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

# Solved transfer tiles whose exported alpha constant is 1 - 2^-24.
# Key: state -> ((transfer triangle, tile x, tile y), ...), tiles are 32x32.
DEFICIT_TILES: dict[int, tuple[tuple[int, int, int], ...]] = {
    42: ((2, 56, 0),),
}
TILE = 32


def apply_plane(state: int, half: np.ndarray, triangles: np.ndarray) -> np.ndarray:
    """Corpus bytes with the solved per-tile secondary applied."""
    high = primary.packed_bytes(half, 0x3C00)
    low = primary.packed_bytes(half, 0x3BFF)
    select = np.zeros(half.shape, dtype=np.bool_)
    for triangle, tile_x, tile_y in DEFICIT_TILES.get(state, ()):
        window = np.s_[
            tile_y * TILE : (tile_y + 1) * TILE,
            tile_x * TILE : (tile_x + 1) * TILE,
        ]
        select[window] = triangles[window] == triangle
    return np.where(select, low, high)


def main() -> int:
    base, bitmap = primary.load_tables()
    before = after = 0
    for state in range(65):
        if state in COMPACT_STATES:
            continue
        try:
            mesh = constraints.load_mesh(state)
            half, covered, _, _, _ = primary.render_state_half(
                state, base=base, bitmap=bitmap
            )
        except (NotImplementedError, FileNotFoundError):
            continue
        observed = primary.observed_frame(state)
        baseline = np.where(covered, primary.packed_bytes(half, 0x3C00), np.uint8(0))
        candidate = np.where(
            covered, apply_plane(state, half, constraints.triangle_map(mesh)), np.uint8(0)
        )
        state_before = int(np.count_nonzero(baseline != observed))
        state_after = int(np.count_nonzero(candidate != observed))
        before += state_before
        after += state_after
        if state_before or state_after:
            print(
                f"state {state}: residuals {state_before} -> {state_after}"
                + ("  REGRESSION" if state_after > state_before else "")
            )
    print(f"TOTAL residuals {before} -> {after}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
