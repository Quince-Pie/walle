#!/usr/bin/env python3
"""Exact cone of transfer-alpha planes consistent with the corpus labels.

The selection is  secondary = 0x3BFF  iff  the interpolated alpha rounds below
1.0, i.e. iff the deficit  D(x, y) = 1 - w(x, y)  exceeds the binary32 rounding
boundary 2^-25.  Writing  g(P) = D(P) - 2^-25  the corpus says

    g(P) >  0   at every LOW pixel
    g(P) <= 0   at every HIGH pixel

which is scale invariant, so what the corpus determines is the CROSSING LINE
and its orientation, not the deficit's magnitude.  g is affine, so with pixel
centres doubled to integers P = (2x+1, 2y+1, 2) the feasible set is the
polyhedral cone { n : n.P > 0 on LOW, n.P <= 0 on HIGH } whose extreme rays are
cross products of pairs of constraint normals.  Everything here is integer.
"""

from __future__ import annotations

import sys
from fractions import Fraction
from math import gcd
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "analysis"))

import a2_solver_constraints as constraints  # noqa: E402
import a2_solver_primary as primary  # noqa: E402

# Isolated LOW pixels inside otherwise-HIGH tiles are raster-precision
# residuals, not transfer-plane pixels; see a2_solver_log entry 4.
RASTER_EXCLUSIONS = {
    (42, 1837, 103),
}


def _reduce(vector: tuple[int, int, int]) -> tuple[int, int, int]:
    divisor = gcd(gcd(abs(vector[0]), abs(vector[1])), abs(vector[2]))
    return vector if divisor == 0 else tuple(value // divisor for value in vector)


def cone(low: list[tuple[int, int, int]], high: list[tuple[int, int, int]]):
    """Extreme rays of { n : n.p >= 0 on low, n.p <= 0 on high } and strictness."""
    points = low + high
    candidates: set[tuple[int, int, int]] = set()
    for index, left in enumerate(points):
        for right in points[index + 1 :]:
            cross = (
                left[1] * right[2] - left[2] * right[1],
                left[2] * right[0] - left[0] * right[2],
                left[0] * right[1] - left[1] * right[0],
            )
            if cross == (0, 0, 0):
                continue
            candidates.add(_reduce(cross))
            candidates.add(_reduce((-cross[0], -cross[1], -cross[2])))

    rays = []
    for normal in sorted(candidates):
        if all(
            normal[0] * p[0] + normal[1] * p[1] + normal[2] * p[2] >= 0 for p in low
        ) and all(
            normal[0] * p[0] + normal[1] * p[1] + normal[2] * p[2] <= 0 for p in high
        ):
            rays.append(normal)
    if not rays:
        return [], None
    interior = (
        sum(ray[0] for ray in rays),
        sum(ray[1] for ray in rays),
        sum(ray[2] for ray in rays),
    )
    strict = all(
        interior[0] * p[0] + interior[1] * p[1] + interior[2] * p[2] > 0 for p in low
    ) and all(
        interior[0] * p[0] + interior[1] * p[1] + interior[2] * p[2] <= 0 for p in high
    )
    return rays, (_reduce(interior) if strict else None)


def solve_state(state: int, *, base, bitmap) -> None:
    built = constraints.build(state, base=base, bitmap=bitmap)
    mesh = constraints.load_mesh(state)
    ys, xs = np.nonzero(built.labels != constraints.LABEL_NONE)
    for triangle in sorted(set(built.triangles[ys, xs].tolist())):
        chosen = built.triangles[ys, xs] == triangle
        low, high = [], []
        excluded = 0
        for y, x in zip(ys[chosen], xs[chosen]):
            point = (2 * int(x) + 1, 2 * int(y) + 1, 2)
            if built.labels[y, x] == constraints.LABEL_LOW:
                if (state, int(x), int(y)) in RASTER_EXCLUSIONS:
                    excluded += 1
                    continue
                low.append(point)
            else:
                high.append(point)
        rays, interior = cone(low, high)
        head = (
            f"state {state} tri {triangle}: low={len(low)} high={len(high)} "
            f"excluded={excluded}"
        )
        if not rays:
            print(head + "  INFEASIBLE")
            continue
        print(head + f"  rays={len(rays)} strictly-feasible={interior is not None}")
        if low:
            for ray in rays:
                print(f"    ray (A,B,C) = {ray}")
            if interior is not None:
                corners = mesh.triangle(triangle)
                values = [
                    interior[0] * round(2 * vx)
                    + interior[1] * round(2 * vy)
                    + interior[2] * 2
                    for vx, vy in corners
                ]
                print(f"    interior normal {interior}")
                print(f"    g at triangle vertices {corners} = {values}")
                print(
                    "    vertex deficit ratios "
                    f"{[str(Fraction(value, values[0])) for value in values]}"
                )


def main() -> int:
    base, bitmap = primary.load_tables()
    states = [int(value) for value in sys.argv[1:]] or [40, 41, 42, 58, 60]
    for state in states:
        solve_state(state, base=base, bitmap=bitmap)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
