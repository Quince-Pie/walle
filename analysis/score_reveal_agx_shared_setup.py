#!/usr/bin/env python3
"""Score arbitrary post-guard children with the recovered AGX setup pipeline.

The older top-left scorer evaluates every pixel directly from the vertex anchor.
This ablation keeps its public geometry, clipping, coverage, helper-lane, circle,
half, and R8 stages, but replaces the arbitrary-child plane evaluator with the
tile-local ``(A, B, C)`` architecture measured by the wide-tile coefficient
capture.  A candidate frame is complete before its retained PNG is opened.
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

import analyze_reveal_agx_setup_tile_sweep as tile_setup  # noqa: E402
import raster_tile_iterator_model as iterator  # noqa: E402
import score_reveal_agx_top_left_setup as scorer  # noqa: E402


type Vertex = tuple[float, ...]
type F32Plane = NDArray[np.float32]
type I64Plane = NDArray[np.int64]

TILE_SIZE: Final = 32
SOURCE_PLANES: dict[tuple[Vertex, Vertex, Vertex], tuple[Vertex, Vertex, Vertex]] = {}


def _toward_zero_f32(exact: NDArray[np.float64]) -> F32Plane:
    rounded = exact.astype(np.float32)
    rounded64 = rounded.astype(np.float64)
    overshot = np.abs(rounded64) > np.abs(exact)
    bits = rounded.view(np.uint32).copy()
    bits[overshot] -= np.uint32(1)
    return bits.view(np.float32)


def _p36_step_exponent(
    constant_bits: int,
    triangle: tuple[Vertex, Vertex, Vertex],
    component: int,
) -> int:
    constant = tile_setup.accumulator.export._fraction(constant_bits)  # noqa: SLF001
    if constant:
        return (
            tile_setup.accumulator.tile.floor_binary_exponent(abs(constant))
            - iterator.CENTER_PRECISION_BITS
            + 1
        )
    scale = max(
        abs(
            tile_setup.accumulator.export._fraction(  # noqa: SLF001
                tile_setup.accumulator.setup._float_bits(  # noqa: SLF001
                    tile_setup.accumulator.setup._float32(vertex[component])  # noqa: SLF001
                )
            )
        )
        for vertex in triangle
    )
    if not scale:
        return -149
    return (
        tile_setup.accumulator.tile.floor_binary_exponent(scale)
        - iterator.CENTER_PRECISION_BITS
        + 1
    )


def _tile_plane(
    triangle: tuple[Vertex, Vertex, Vertex],
    *,
    component: int,
    x: I64Plane,
    y: I64Plane,
    p25_bitmap: bytes,
) -> F32Plane:
    """Evaluate one varying with the tile-local two-dimensional p36 ITER path."""
    coefficient_triangle = SOURCE_PLANES.get(triangle, triangle)
    slope_x_bits, anchor, positions = scorer._slope_bits(  # noqa: SLF001
        coefficient_triangle,
        component=component,
        axis=0,
        p25_bitmap=p25_bitmap,
    )
    slope_y_bits, anchor_y, positions_y = scorer._slope_bits(  # noqa: SLF001
        coefficient_triangle,
        component=component,
        axis=1,
        p25_bitmap=p25_bitmap,
    )
    if anchor_y != anchor or positions_y != positions:
        raise AssertionError("triangle setup anchor changed between axes")
    slope_x = float(
        tile_setup.accumulator.export._fraction(slope_x_bits)  # noqa: SLF001
    )
    slope_y = float(
        tile_setup.accumulator.export._fraction(slope_y_bits)  # noqa: SLF001
    )
    result = np.empty(x.shape, dtype=np.float32)
    tile_x = np.floor_divide(x, TILE_SIZE)
    tile_y = np.floor_divide(y, TILE_SIZE)
    tile_keys = np.unique(np.stack((tile_x, tile_y), axis=-1).reshape(-1, 2), axis=0)
    for x_tile_raw, y_tile_raw in tile_keys:
        x_tile = int(x_tile_raw)
        y_tile = int(y_tile_raw)
        selected = (tile_x == x_tile) & (tile_y == y_tile)
        constant_bits = tile_setup._shared_reciprocal_constant_bits(  # noqa: SLF001
            coefficient_triangle,
            component - 2,
            (x_tile, y_tile),
            p25_bitmap,
            join_precision=28,
            reciprocal_truncation=20,
        )
        step_exponent = _p36_step_exponent(
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
        result[selected] = _toward_zero_f32(exact)
    return result


def _overlay_arbitrary_triangle(
    candidate: NDArray[np.uint8],
    triangle: tuple[Vertex, Vertex, Vertex],
    *,
    scissor: dict[str, int],
    p25_bitmap: bytes,
) -> None:
    fixed = np.asarray(
        [
            (
                scorer._subpixel_fixed(vertex[0]),  # noqa: SLF001
                scorer._subpixel_fixed(vertex[1]),  # noqa: SLF001
            )
            for vertex in triangle
        ],
        dtype=np.int64,
    )
    left = max(scissor["x"], int(int(fixed[:, 0].min()) // 256) - 1, 0)
    top = max(scissor["y"], int(int(fixed[:, 1].min()) // 256) - 1, 0)
    right = min(
        scissor["x"] + scissor["width"],
        int(-(-int(fixed[:, 0].max()) // 256)) + 1,
        scorer.public.WIDTH,
    )
    bottom = min(
        scissor["y"] + scissor["height"],
        int(-(-int(fixed[:, 1].max()) // 256)) + 1,
        scorer.public.HEIGHT,
    )
    if left >= right or top >= bottom:
        return

    yy, xx = np.meshgrid(
        np.arange(top, bottom, dtype=np.int64),
        np.arange(left, right, dtype=np.int64),
        indexing="ij",
    )
    selected = scorer._inside_triangle(  # noqa: SLF001
        fixed, xx * 256 + 128, yy * 256 + 128
    )
    if not bool(np.any(selected)):
        return

    partner_x = np.where((xx & 1) == 0, xx + 1, xx - 1)
    partner_y = np.where((yy & 1) == 0, yy + 1, yy - 1)
    center_components: list[F32Plane] = []
    x_components: list[F32Plane] = []
    y_components: list[F32Plane] = []
    for component in (6, 7):
        center_components.append(
            _tile_plane(
                triangle,
                component=component,
                x=xx,
                y=yy,
                p25_bitmap=p25_bitmap,
            )
        )
        x_components.append(
            _tile_plane(
                triangle,
                component=component,
                x=partner_x,
                y=yy,
                p25_bitmap=p25_bitmap,
            )
        )
        y_components.append(
            _tile_plane(
                triangle,
                component=component,
                x=xx,
                y=partner_y,
                p25_bitmap=p25_bitmap,
            )
        )

    distance = scorer.public.reveal.circle_distance(*center_components)
    # AGX evaluates the complete varying vector in every quad lane before the
    # native DFDX/DFDY instructions consume the resulting circle distance.
    # Post-clip setup can introduce a cross-gradient even when a source SDF
    # component was axis-aligned, so mixing one helper component with the
    # center lane is not equivalent.
    distance_x = scorer.public.reveal.circle_distance(*x_components)
    distance_y = scorer.public.reveal.circle_distance(*y_components)
    feather = np.maximum(
        np.asarray(
            np.abs(distance_x - distance) + np.abs(distance_y - distance),
            dtype=np.float32,
        ),
        np.float32(1e-4),
    )
    alpha = (
        np.clip(
            np.asarray(
                (np.float32(1) - distance) / feather + np.float32(0.5),
                dtype=np.float32,
            ),
            0,
            1,
        )
        .astype(np.float16)
        .astype(np.float32)
    )
    encoded = np.rint(alpha * np.float32(255)).astype(np.uint8)
    destination = candidate[top:bottom, left:right]
    destination[selected] = encoded[selected]


def _source_plane_map() -> dict[
    tuple[Vertex, Vertex, Vertex], tuple[Vertex, Vertex, Vertex]
]:
    result: dict[tuple[Vertex, Vertex, Vertex], tuple[Vertex, Vertex, Vertex]] = {}
    for state in range(scorer.public.public_geometry.DEFAULT_STATE_COUNT):
        geometry = scorer.public.public_geometry.construct_state_geometry(state)
        if geometry is None or geometry.family != "border-grid":
            continue
        vertices = [tuple(vertex) for vertex in geometry.vertices]
        indices = list(geometry.indices)
        for group in range(min(len(indices) // 6, 4)):
            for offset in (group * 6, group * 6 + 3):
                source = tuple(
                    vertices[int(index)] for index in indices[offset : offset + 3]
                )
                if all(
                    scorer.public.raster_prototype.GUARD_LOW
                    <= vertex[0]
                    <= scorer.public.raster_prototype.GUARD_HIGH
                    and scorer.public.raster_prototype.GUARD_LOW
                    <= vertex[1]
                    <= scorer.public.raster_prototype.GUARD_HIGH
                    for vertex in source
                ):
                    continue
                polygon = scorer.public._clip_triangle_preserving_start(  # noqa: SLF001
                    list(source)
                )
                children = (
                    (tuple(polygon),)
                    if len(polygon) == 3
                    else (
                        (tuple((polygon[0], polygon[1], polygon[2]))),
                        (tuple((polygon[0], polygon[2], polygon[3]))),
                    )
                    if len(polygon) == 4
                    else ()
                )
                for child in children:
                    result[child] = source
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", type=int)
    parser.add_argument("--source-plane", action="store_true")
    arguments = parser.parse_args()
    if arguments.source_plane:
        SOURCE_PLANES.update(_source_plane_map())
    scorer._overlay_arbitrary_triangle = _overlay_arbitrary_triangle  # noqa: SLF001
    result = scorer.score(state_only=arguments.state)
    result["schema"] = "walle-reveal-agx-shared-setup-score-v1"
    result["model"] = (
        "public post-guard geometry plus p28 shared-reciprocal tile-local AGX setup"
        + (" with source-plane reuse" if arguments.source_plane else "")
    )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
