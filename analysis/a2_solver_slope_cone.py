#!/usr/bin/env python3
"""Per-tile constant + shared slope: solve the deficit gradient direction.

AGX setup exports one slope pair per primitive and one constant per tile.  If
the transfer alpha follows that shape, then inside every tile the selection is
a threshold on t = A*x + B*y with A, B shared across the whole primitive, so
every LOW pixel must out-rank every HIGH pixel of its own tile under t.

Each same-tile (low, high) pair is therefore one open half-plane on (A, B):
A*(xl - xh) + B*(yl - yh) > 0.  The feasible set is an open angular cone;
this script decides it exactly with integer dot products, sweeping the tile
size so the granularity of the exported constant is a measured quantity
rather than an assumption.
"""

from __future__ import annotations

import sys
from math import degrees, atan2
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "analysis"))

import a2_solver_constraints as constraints  # noqa: E402
import a2_solver_primary as primary  # noqa: E402


def solve_cone(vectors: list[tuple[int, int]]) -> tuple[int, int] | None:
    """Return an integer direction u with <u, d> > 0 for all d, or None.

    The feasible set is an open convex cone whose boundary normals are the
    quarter turns of the constraint vectors, so if it is nonempty it contains
    the sum of two such quarter turns.
    """
    if not vectors:
        return (1, 0)
    turns = [(-dy, dx) for dx, dy in vectors] + [(dy, -dx) for dx, dy in vectors]
    candidates = list(turns) + list(vectors)
    candidates += [
        (left[0] + right[0], left[1] + right[1])
        for index, left in enumerate(turns)
        for right in turns[index + 1 :]
    ]
    for candidate in candidates:
        if candidate == (0, 0):
            continue
        if all(candidate[0] * dx + candidate[1] * dy > 0 for dx, dy in vectors):
            return candidate
    return None


def pairs_for(state_constraints, triangle: int, tile: int) -> list[tuple[int, int]]:
    ys, xs = np.nonzero(state_constraints.labels != constraints.LABEL_NONE)
    chosen = state_constraints.triangles[ys, xs] == triangle
    ys, xs = ys[chosen], xs[chosen]
    low = state_constraints.labels[ys, xs] == constraints.LABEL_LOW
    keys = (ys // tile).astype(np.int64) * 4096 + (xs // tile)
    vectors: list[tuple[int, int]] = []
    for key in np.unique(keys):
        here = keys == key
        low_index = np.nonzero(here & low)[0]
        high_index = np.nonzero(here & ~low)[0]
        for l in low_index:
            for h in high_index:
                vectors.append((int(xs[l]) - int(xs[h]), int(ys[l]) - int(ys[h])))
    return vectors


def main() -> int:
    base, bitmap = primary.load_tables()
    states = [int(value) for value in sys.argv[1:]] or [40, 41, 42, 58, 60]
    built = {
        state: constraints.build(state, base=base, bitmap=bitmap)
        for state in states
    }
    for tile in (64, 32, 16, 8, 4, 2, 1):
        print(f"tile {tile}:")
        for state in states:
            state_constraints = built[state]
            labels = state_constraints.labels
            for triangle in sorted(
                set(state_constraints.triangles[labels == constraints.LABEL_LOW].tolist())
            ):
                vectors = pairs_for(state_constraints, triangle, tile)
                solution = solve_cone(vectors)
                text = (
                    f"u=({solution[0]},{solution[1]}) "
                    f"{degrees(atan2(solution[1], solution[0])):.2f}deg"
                    if solution
                    else "INFEASIBLE"
                )
                print(
                    f"  state {state} tri {triangle}: pairs={len(vectors)} {text}"
                )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
