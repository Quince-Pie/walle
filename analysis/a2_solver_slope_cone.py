#!/usr/bin/env python3
"""Per-tile constant + shared slope: solve the deficit gradient direction.

AGX setup exports one slope pair per primitive and one constant per tile.  If
the transfer alpha follows that shape, then inside every tile the selection is
a threshold on t = A*x + B*y with A, B shared across the whole primitive, so
every LOW pixel must out-rank every HIGH pixel of its own tile under t.

Each same-tile (low, high) pair is therefore one open half-plane on (A, B):
A*(xl - xh) + B*(yl - yh) > 0.  The feasible set is an angular cone; this
script computes it exactly with integer cross products, per state and per
transfer triangle.
"""

from __future__ import annotations

import sys
from math import atan2, degrees, gcd
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "analysis"))

import a2_solver_constraints as constraints  # noqa: E402
import a2_solver_primary as primary  # noqa: E402

TILE = 32


def _primitive(vector: tuple[int, int]) -> tuple[int, int]:
    divisor = gcd(abs(vector[0]), abs(vector[1]))
    return (vector[0] // divisor, vector[1] // divisor)


def cone_from_vectors(vectors: list[tuple[int, int]]):
    """Directions u with <u, d> > 0 for every d, as an open angular interval."""
    if not vectors:
        return (-180.0, 180.0), True
    unique = sorted({_primitive(vector) for vector in vectors},
                    key=lambda v: atan2(v[1], v[0]))
    count = len(unique)
    best = None
    for index in range(count):
        current = unique[index]
        following = unique[(index + 1) % count]
        cross = current[0] * following[1] - current[1] * following[0]
        dot = current[0] * following[0] + current[1] * following[1]
        gap = atan2(cross, dot)
        if count == 1:
            gap = 0.0
        if cross < 0 or (cross == 0 and dot < 0):
            # reflex gap between consecutive directions
            gap = 2 * 3.141592653589793 + gap if gap < 0 else gap
        if best is None or gap > best[0]:
            best = (gap, current, following)
    # Feasible iff every vector lies in an open half plane: the widest gap
    # between consecutive directions must exceed pi.
    gap, current, following = best
    lower = degrees(atan2(current[1], current[0]))
    upper = degrees(atan2(following[1], following[0]))
    feasible = degrees(gap) > 180.0 + 1e-12
    # solution directions are those strictly inside the gap, rotated by -90/+90
    return (lower, upper), feasible


def analyse(state: int, *, base: tuple[int, ...], bitmap: bytes) -> None:
    built = constraints.build(state, base=base, bitmap=bitmap)
    ys, xs = np.nonzero(built.labels != constraints.LABEL_NONE)
    low = built.labels[ys, xs] == constraints.LABEL_LOW
    triangles = built.triangles[ys, xs]
    for triangle in sorted(set(triangles.tolist())):
        chosen = triangles == triangle
        if not low[chosen].any():
            continue
        tx, ty = xs[chosen] // TILE, ys[chosen] // TILE
        keys = ty.astype(np.int64) * 4096 + tx
        vectors: list[tuple[int, int]] = []
        mixed = 0
        for key in np.unique(keys):
            here = keys == key
            lows = np.nonzero(here & low[chosen])[0]
            highs = np.nonzero(here & ~low[chosen])[0]
            if not len(lows) or not len(highs):
                continue
            mixed += 1
            for l in lows:
                for h in highs:
                    vectors.append(
                        (
                            int(xs[chosen][l]) - int(xs[chosen][h]),
                            int(ys[chosen][l]) - int(ys[chosen][h]),
                        )
                    )
        interval, feasible = cone_from_vectors(vectors)
        print(
            f"state {state} tri {triangle}: mixed tiles={mixed} pairs={len(vectors)} "
            f"feasible={feasible} blocking-gap=({interval[0]:.3f}, {interval[1]:.3f}) deg"
        )


def main() -> int:
    base, bitmap = primary.load_tables()
    states = [int(value) for value in sys.argv[1:]] or [40, 41, 42, 58, 60]
    for state in states:
        analyse(state, base=base, bitmap=bitmap)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
