#!/usr/bin/env python3
"""Score recovered AGX guard endpoints with ordinary triangle setup.

This experiment changes only post-guard endpoint materialization.  Geometry,
fan order, coverage, helper lanes, circle arithmetic, binary16, and R8 encoding
remain those of the public-input scorer.  The endpoint pipeline was recovered
independently from raw ``LDCF`` captures and is evaluated before any retained
reference frame is opened.
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Final

import numpy as np
from numpy.typing import NDArray


ROOT: Final = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "analysis"), str(ROOT / "lg-test" / "Analysis")]

import analyze_reveal_agx_endpoint_isolation as endpoint  # noqa: E402
import analyze_reveal_agx_guard_fan_diagonal as fan  # noqa: E402
import score_reveal_agx_top_left_setup as scorer  # noqa: E402


type Vertex = tuple[float, ...]


def _direct_clip_triangle(
    triangle: list[Vertex], reciprocal_table: NDArray[np.uint32]
) -> list[Vertex]:
    polygon = tuple(triangle)
    for axis, edge, keep_greater in fan.GUARD_PLANES:
        if not polygon:
            break
        polygon = fan._clip_one_plane(  # noqa: SLF001
            polygon,
            axis=axis,
            edge=edge,
            keep_greater=keep_greater,
            table=reciprocal_table,
        )
        deduplicated: list[Vertex] = []
        for vertex in polygon:
            if not deduplicated or vertex[:2] != deduplicated[-1][:2]:
                deduplicated.append(vertex)
        if (
            len(deduplicated) > 1
            and deduplicated[0][:2] == deduplicated[-1][:2]
        ):
            deduplicated.pop()
        polygon = tuple(deduplicated)
    return list(polygon)


def _install_direct_endpoint_model() -> None:
    reciprocal_table = np.fromfile(endpoint.RECIPROCAL_TABLE, dtype="<u4")
    if reciprocal_table.size != endpoint.RECIPROCAL_ENTRY_COUNT:
        raise ValueError("direct reciprocal table extent differs")

    scorer.public._clip_triangle_preserving_start = (  # noqa: SLF001
        lambda triangle: _direct_clip_triangle(triangle, reciprocal_table)
    )

    ordinary_overlay = scorer.public.raster_prototype._overlay_triangle  # noqa: SLF001

    def overlay_with_arbitrary_fallback(*args: object, **kwargs: object) -> object:
        try:
            return ordinary_overlay(*args, **kwargs)
        except ValueError as error:
            if "not axis-separable at its edges" not in str(error):
                raise
            raise ValueError(
                "compact-boundary channel 3 is not axis-separable at its edges"
            ) from error

    scorer.public.raster_prototype._overlay_triangle = (  # noqa: SLF001
        overlay_with_arbitrary_fallback
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", type=int)
    arguments = parser.parse_args()

    _install_direct_endpoint_model()
    result = scorer.score(state_only=arguments.state)
    result["schema"] = "walle-reveal-agx-direct-endpoint-setup-score-v1"
    result["model"] = (
        "recovered AGX direct endpoint materialization followed by the "
        "top-left ordinary triangle setup model"
    )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
