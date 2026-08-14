#!/usr/bin/env python3
"""Score a conventional Sutherland-Hodgman post-guard topology candidate."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
LG_ANALYSIS = ROOT / "lg-test" / "Analysis"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(LG_ANALYSIS))

import _analyze_reveal_captured_a2_geometry as raster_prototype  # noqa: E402
import score_reveal_v74_public_raster as public_raster  # noqa: E402


type Vertex = tuple[float, ...]


def canonical_clip_triangle(triangle: list[Vertex]) -> list[Vertex]:
    """Clip in L/R/B/T order without rotating on an all-inside plane."""

    polygon = triangle
    planes = (
        (0, raster_prototype.GUARD_LOW, True),
        (0, raster_prototype.GUARD_HIGH, False),
        (1, raster_prototype.GUARD_LOW, True),
        (1, raster_prototype.GUARD_HIGH, False),
    )
    for axis, edge, keep_greater in planes:
        if not polygon:
            break

        clipped: list[Vertex] = []
        previous = polygon[-1]
        previous_inside = (
            previous[axis] >= edge
            if keep_greater
            else previous[axis] <= edge
        )
        for current in polygon:
            current_inside = (
                current[axis] >= edge
                if keep_greater
                else current[axis] <= edge
            )
            if current_inside:
                if not previous_inside:
                    clipped.append(
                        raster_prototype._clip_intersection(
                            previous,
                            current,
                            axis=axis,
                            edge=edge,
                        )
                    )
                clipped.append(current)
            elif previous_inside:
                clipped.append(
                    raster_prototype._clip_intersection(
                        previous,
                        current,
                        axis=axis,
                        edge=edge,
                    )
                )
            previous = current
            previous_inside = current_inside

        polygon = []
        for vertex in clipped:
            if not polygon or vertex[:2] != polygon[-1][:2]:
                polygon.append(vertex)
        if len(polygon) > 1 and polygon[0][:2] == polygon[-1][:2]:
            polygon.pop()
    return polygon


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus", type=Path, default=public_raster.DEFAULT_CORPUS)
    args = parser.parse_args()

    raster_prototype._clip_triangle = canonical_clip_triangle
    result = public_raster.score_public_raster(args.corpus)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
