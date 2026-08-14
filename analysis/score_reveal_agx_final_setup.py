#!/usr/bin/env python3.14
"""Retained rejected scorer for the disproven lane-phase setup hypothesis.

This experiment combined four candidate stages:

* direct AGX guard-endpoint materialization;
* tile-local two-dimensional shared-P25 setup;
* a supposed X-lane setup phase; and
* source-plane lineage for children crossing both positive guard planes.

The focused-capture correction proves that the apparent phase split was a
clipped-child ownership split: coefficients are constant per primitive and a
same-child opposite-parity counterexample exports identical A/B/C.  Keep this
file only to reproduce the rejected hypothesis; do not cite or integrate it.
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Final

import numpy as np
from numpy.typing import NDArray


type Vertex = tuple[float, ...]
type F32Plane = NDArray[np.float32]
type I64Plane = NDArray[np.int64]

ROOT: Final = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "analysis"), str(ROOT / "lg-test" / "Analysis")]

import analyze_reveal_agx_setup_tile_sweep as tile_setup  # noqa: E402
import raster_tile_iterator_model as iterator  # noqa: E402
import recover_reveal_postguard_plane_setup as plane_setup  # noqa: E402
import score_reveal_agx_direct_endpoint_setup as direct  # noqa: E402
import score_reveal_agx_shared_setup as shared  # noqa: E402


TILE_SIZE: Final = 32


def _phase_constant_bits(
    triangle: tuple[Vertex, Vertex, Vertex],
    component: int,
    tile: tuple[int, int],
    phase: int,
    p25_bitmap: bytes,
) -> int:
    """Return the rejected phase-dependent candidate tile constant."""
    original = tile_setup.accumulator.composite.quantize_composite_constant_bits
    if phase == 0:
        tile_setup.accumulator.composite.quantize_composite_constant_bits = (
            iterator.toward_zero_float32_bits
        )
    try:
        return tile_setup._shared_reciprocal_constant_bits(  # noqa: SLF001
            triangle,
            component,
            tile,
            p25_bitmap,
            join_precision=28,
            reciprocal_truncation=22 + phase,
        )
    finally:
        tile_setup.accumulator.composite.quantize_composite_constant_bits = original


def _tile_plane(
    triangle: tuple[Vertex, Vertex, Vertex],
    *,
    component: int,
    x: I64Plane,
    y: I64Plane,
    p25_bitmap: bytes,
) -> F32Plane:
    coefficient_triangle = shared.SOURCE_PLANES.get(triangle, triangle)
    _old_x_bits, anchor, positions = shared.scorer._slope_bits(  # noqa: SLF001
        coefficient_triangle,
        component=component,
        axis=0,
        p25_bitmap=p25_bitmap,
    )
    _old_y_bits, anchor_y, positions_y = shared.scorer._slope_bits(  # noqa: SLF001
        coefficient_triangle,
        component=component,
        axis=1,
        p25_bitmap=p25_bitmap,
    )
    if anchor_y != anchor or positions_y != positions:
        raise AssertionError("triangle setup anchor changed between axes")
    slope_x_bits = plane_setup._plane_slope_bits(  # noqa: SLF001
        coefficient_triangle, axis=0, component=component
    )
    slope_y_bits = plane_setup._plane_slope_bits(  # noqa: SLF001
        coefficient_triangle, axis=1, component=component
    )
    slope_x = float(tile_setup.accumulator.export._fraction(slope_x_bits))  # noqa: SLF001
    slope_y = float(tile_setup.accumulator.export._fraction(slope_y_bits))  # noqa: SLF001

    result = np.empty(x.shape, dtype=np.float32)
    tile_x = np.floor_divide(x, TILE_SIZE)
    tile_y = np.floor_divide(y, TILE_SIZE)
    phase = x & np.int64(1)
    keys = np.unique(np.stack((tile_x, tile_y, phase), axis=-1).reshape(-1, 3), axis=0)
    for x_tile_raw, y_tile_raw, phase_raw in keys:
        x_tile = int(x_tile_raw)
        y_tile = int(y_tile_raw)
        lane_phase = int(phase_raw)
        selected = (tile_x == x_tile) & (tile_y == y_tile) & (phase == lane_phase)
        constant_bits = _phase_constant_bits(
            coefficient_triangle,
            component - 2,
            (x_tile, y_tile),
            lane_phase,
            p25_bitmap,
        )
        step_exponent = shared._p36_step_exponent(  # noqa: SLF001
            constant_bits, coefficient_triangle, component
        )
        step = float(tile_setup.accumulator._power_of_two(step_exponent))  # noqa: SLF001
        constant = float(
            tile_setup.accumulator.export._fraction(constant_bits)  # noqa: SLF001
        )
        local_x = x[selected] - x_tile * TILE_SIZE
        local_y = y[selected] - y_tile * TILE_SIZE
        quad_x = local_x & ~np.int64(1)
        quad_y = local_y & ~np.int64(1)
        exact_base = (
            constant
            + (quad_x.astype(np.float64) + 0.5) * slope_x
            + (quad_y.astype(np.float64) + 0.5) * slope_y
        )
        base = np.floor(exact_base / step) * step
        exact = (
            base
            + (local_x & 1).astype(np.float64) * slope_x
            + (local_y & 1).astype(np.float64) * slope_y
        )
        result[selected] = shared._toward_zero_f32(exact)  # noqa: SLF001
    return result


def _retains_source_plane(source: tuple[Vertex, Vertex, Vertex]) -> bool:
    """Apply the recovered one-plane rule and multi-plane discriminator."""
    raster = shared.scorer.public.raster_prototype
    low = raster.GUARD_LOW
    high = raster.GUARD_HIGH
    crossings = (
        min(vertex[0] for vertex in source) < low,
        max(vertex[0] for vertex in source) > high,
        min(vertex[1] for vertex in source) < low,
        max(vertex[1] for vertex in source) > high,
    )
    return sum(crossings) == 1 or (crossings[1] and crossings[3])


def _source_plane_map() -> dict[
    tuple[Vertex, Vertex, Vertex], tuple[Vertex, Vertex, Vertex]
]:
    result: dict[tuple[Vertex, Vertex, Vertex], tuple[Vertex, Vertex, Vertex]] = {}
    public = shared.scorer.public
    for state in range(public.public_geometry.DEFAULT_STATE_COUNT):
        geometry = public.public_geometry.construct_state_geometry(state)
        if geometry is None or geometry.family != "border-grid":
            continue
        vertices = [tuple(vertex) for vertex in geometry.vertices]
        indices = list(geometry.indices)
        for offset in range(0, len(indices), 3):
            source = tuple(
                vertices[int(index)] for index in indices[offset : offset + 3]
            )
            if len(source) != 3 or not _retains_source_plane(source):
                continue
            polygon = public._clip_triangle_preserving_start(list(source))  # noqa: SLF001
            children = tuple(
                tuple((polygon[0], polygon[index], polygon[index + 1]))
                for index in range(1, len(polygon) - 1)
            )
            result.update((child, source) for child in children)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", type=int)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()

    direct._install_direct_endpoint_model()  # noqa: SLF001
    shared.SOURCE_PLANES.clear()
    shared.SOURCE_PLANES.update(_source_plane_map())
    shared._tile_plane = _tile_plane  # noqa: SLF001
    shared.scorer._overlay_arbitrary_triangle = (  # noqa: SLF001
        shared._overlay_arbitrary_triangle  # noqa: SLF001
    )
    result = shared.scorer.score(state_only=arguments.state)
    result["schema"] = "walle-reveal-agx-rejected-lane-phase-score-v2"
    result["authority"] = {
        "productionAuthorized": False,
        "hypothesisRejected": True,
        "reason": "X parity was confounded with clipped-child ownership",
    }
    result["model"] = (
        "direct AGX endpoints, p28 shared-P25 two-dimensional setup, "
        "rejected X-lane phase candidate and positive-guard source-plane lineage"
    )
    result["sourcePlaneChildCount"] = len(shared.SOURCE_PLANES)
    encoded = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if arguments.output is None:
        print(encoded, end="")
    else:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(encoded, encoding="utf-8")


if __name__ == "__main__":
    main()
