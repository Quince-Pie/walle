#!/usr/bin/env python3
"""Compare Apple's retained A2 ``circle_image`` output with the CPU model.

The input oracle is produced by ``LG_REVEAL_MASK_A2_CIRCLE_TRACE``.  That
intervention preserves Apple's private fragment function and changes only the
specialization record so ``circle_image`` is written directly to an RGBA16F
attachment.  The companion interpolant trace replays the same retained vertex
buffer through the decoded generic vertex transform and writes the exact
binary32 SDF coordinates at each covered sample.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import _analyze_reveal_raster_trace as reveal
import _analyze_reveal_second_stage as second_stage
import _search_reveal_tile_phase as phase
from analysis import liquid_glass_runtime_raster_coefficients as raster


type UInt8Array = NDArray[np.uint8]
type UInt16Array = NDArray[np.uint16]
type UInt32Array = NDArray[np.uint32]
type BoolArray = NDArray[np.bool_]

WIDTH = 2_048
HEIGHT = 2_048


def _model_sdf_bits(
    vertices: list[tuple[float, ...]],
    indices: tuple[int, ...],
    *,
    scissor: tuple[int, int, int, int],
    base: tuple[int, ...],
    bitmap: bytes,
) -> tuple[UInt32Array, UInt32Array, UInt8Array, UInt8Array]:
    crop_left, crop_top, crop_width, crop_height = scissor
    x_bits = np.zeros((crop_height, crop_width), dtype=np.uint32)
    y_bits = np.zeros_like(x_bits)
    quadrants = np.full(x_bits.shape, 255, dtype=np.uint8)
    primitive_ids = np.full(x_bits.shape, 255, dtype=np.uint8)
    covered = np.zeros(x_bits.shape, dtype=np.bool_)

    for draw in range(9):
        draw_indices = indices[draw * 6 : draw * 6 + 6]
        draw_vertices = [vertices[index] for index in draw_indices]
        try:
            quad = raster.runtime_quad_from_vertices(
                draw_vertices,
                name=f"circle-draw-{draw}",
            )
        except ValueError:
            continue

        table = reveal.selector_table_for_quad(quad, base, bitmap)
        left, top, right, bottom = raster.visible_pixel_bounds(quad.case)
        target_left = max(left, crop_left)
        target_top = max(top, crop_top)
        target_right = min(right, crop_left + crop_width)
        target_bottom = min(bottom, crop_top + crop_height)
        if target_left >= target_right or target_top >= target_bottom:
            continue

        xs = np.arange(target_left, target_right, dtype=np.uint32)
        ys = np.arange(target_top, target_bottom, dtype=np.uint32)
        yy, xx = np.meshgrid(ys, xs, indexing="ij")
        primitives = raster.primitive_ids(quad, xx, yy)
        draw_x = np.empty(xx.shape, dtype=np.uint32)
        draw_y = np.empty(xx.shape, dtype=np.uint32)
        for primitive in (0, 1):
            selected = primitives == primitive
            if not np.any(selected):
                continue
            x_values = raster.coordinate_axis_bits(
                quad,
                channel=2,
                primitive=primitive,
                coordinates=range(target_left, target_right),
                selector_table=table,
            )
            y_values = raster.coordinate_axis_bits(
                quad,
                channel=3,
                primitive=primitive,
                coordinates=range(target_top, target_bottom),
                selector_table=table,
            )
            draw_x[selected] = x_values[xx[selected] - target_left]
            draw_y[selected] = y_values[yy[selected] - target_top]

        destination = np.s_[
            target_top - crop_top : target_bottom - crop_top,
            target_left - crop_left : target_right - crop_left,
        ]
        x_bits[destination] = draw_x
        y_bits[destination] = draw_y
        quadrants[destination] = draw
        primitive_ids[destination] = primitives
        covered[destination] = True

    if not np.all(covered):
        examples = np.argwhere(~covered)[:16].tolist()
        raise ValueError(f"circle mesh left uncovered pixels: {examples}")
    return x_bits, y_bits, quadrants, primitive_ids


def _count_by(values: NDArray[Any], selected: BoolArray) -> dict[str, int]:
    counts = Counter(int(value) for value in values[selected].tolist())
    return {str(key): value for key, value in sorted(counts.items())}


def analyze(arguments: argparse.Namespace) -> dict[str, Any]:
    generated = reveal.generated_native_mesh(arguments.state)
    if generated is None:
        raise ValueError("selected reveal state has no circle mesh")
    vertices, (left, top, right, bottom) = generated
    if arguments.geometry_mode == "unit":
        center_x = np.float32((left + right) / 2)
        center_y = np.float32((top + bottom) / 2)
        radius = np.float32(min(right - left, bottom - top) / 2)
        positions_x = (
            np.float32(center_x - radius),
            center_x,
            center_x,
            np.float32(center_x + radius),
        )
        positions_y = (
            np.float32(center_y - radius),
            center_y,
            center_y,
            np.float32(center_y + radius),
        )
        coordinates = (
            np.float32(-1),
            np.float32(0),
            np.float32(0),
            np.float32(-1),
        )
        vertices = [
            (
                x,
                y,
                np.float32(0),
                np.float32(1),
                np.float32((column > 0) + (column > 2)),
                np.float32((row > 0) + (row > 2)),
                coordinates[column],
                coordinates[row],
            )
            for row, y in enumerate(positions_y)
            for column, x in enumerate(positions_x)
        ]
    clip_left = max(0, left)
    clip_top = max(0, top)
    clip_right = min(WIDTH, right)
    clip_bottom = min(HEIGHT, bottom)
    scissor = (
        clip_left,
        clip_top,
        clip_right - clip_left,
        clip_bottom - clip_top,
    )

    indices = phase._load_indices()
    base = reveal.raster_arithmetic.load_selector_table()
    bitmap = reveal.P25_BITMAP.read_bytes()
    model_half_crop, quadrants_crop, primitives_crop = (
        second_stage.render_primary_half(
            vertices,
            indices,
            scissor=scissor,
            base=base,
            bitmap=bitmap,
        )
    )
    model_x_crop, model_y_crop, coordinate_quadrants, coordinate_primitives = (
        _model_sdf_bits(
            vertices,
            indices,
            scissor=scissor,
            base=base,
            bitmap=bitmap,
        )
    )
    if not np.array_equal(quadrants_crop, coordinate_quadrants):
        raise ValueError("coordinate and alpha quadrant maps differ")
    if not np.array_equal(primitives_crop, coordinate_primitives):
        raise ValueError("coordinate and alpha primitive maps differ")

    model_half = np.zeros((HEIGHT, WIDTH), dtype=np.uint16)
    model_x = np.zeros((HEIGHT, WIDTH), dtype=np.uint32)
    model_y = np.zeros((HEIGHT, WIDTH), dtype=np.uint32)
    quadrants = np.full((HEIGHT, WIDTH), 255, dtype=np.uint8)
    primitives = np.full((HEIGHT, WIDTH), 255, dtype=np.uint8)
    destination = np.s_[clip_top:clip_bottom, clip_left:clip_right]
    model_half[destination] = model_half_crop
    model_x[destination] = model_x_crop
    model_y[destination] = model_y_crop
    quadrants[destination] = quadrants_crop
    primitives[destination] = primitives_crop

    oracle = np.fromfile(arguments.oracle, dtype="<u2").reshape(
        HEIGHT,
        WIDTH,
        4,
    )
    interpolants = np.fromfile(arguments.interpolants, dtype="<u4").reshape(
        HEIGHT,
        WIDTH,
        4,
    )
    colors = np.fromfile(arguments.colors, dtype="<u4").reshape(
        HEIGHT,
        WIDTH,
        4,
    )
    active = (colors[..., 3] & np.uint32(0xFFFF)) != 0
    oracle_half = oracle[..., 0]
    half_mismatch = active & (model_half != oracle_half)
    x_mismatch = active & (model_x != interpolants[..., 0])
    y_mismatch = active & (model_y != interpolants[..., 1])
    coordinate_mismatch = x_mismatch | y_mismatch
    model_abs_x = model_x & np.uint32(0x7FFF_FFFF)
    model_abs_y = model_y & np.uint32(0x7FFF_FFFF)
    captured_abs_x = interpolants[..., 0] & np.uint32(0x7FFF_FFFF)
    captured_abs_y = interpolants[..., 1] & np.uint32(0x7FFF_FFFF)
    abs_x_mismatch = active & (model_abs_x != captured_abs_x)
    abs_y_mismatch = active & (model_abs_y != captured_abs_y)
    abs_coordinate_mismatch = abs_x_mismatch | abs_y_mismatch

    yy, xx = np.indices((HEIGHT, WIDTH), dtype=np.int32)
    partner_x = xx ^ 1
    partner_y = yy ^ 1
    same_x_invocation = (
        active
        & active[yy, partner_x]
        & (quadrants == quadrants[yy, partner_x])
        & (primitives == primitives[yy, partner_x])
    )
    same_y_invocation = (
        active
        & active[partner_y, xx]
        & (quadrants == quadrants[partner_y, xx])
        & (primitives == primitives[partner_y, xx])
    )
    captured_replay_valid = same_x_invocation & same_y_invocation
    captured_float = interpolants[..., :2].view(np.float32)
    captured_distance = reveal.circle_distance(
        captured_float[..., 0],
        captured_float[..., 1],
    )
    captured_distance_x = captured_distance[yy, partner_x]
    captured_distance_y = captured_distance[partner_y, xx]
    captured_feather = np.maximum(
        np.asarray(
            np.abs(captured_distance_x - captured_distance)
            + np.abs(captured_distance_y - captured_distance),
            dtype=np.float32,
        ),
        np.float32(1e-4),
    )
    captured_alpha = np.clip(
        np.asarray(
            (np.float32(1) - captured_distance) / captured_feather
            + np.float32(0.5),
            dtype=np.float32,
        ),
        0,
        1,
    )
    captured_half = captured_alpha.astype(np.float16).view(np.uint16)
    captured_half_mismatch = captured_replay_valid & (
        captured_half != oracle_half
    )

    model_code = np.rint(
        model_half.view(np.float16).astype(np.float32) * np.float32(255)
    ).astype(np.uint8)
    oracle_code = np.rint(
        oracle_half.view(np.float16).astype(np.float32) * np.float32(255)
    ).astype(np.uint8)
    code_mismatch = active & (model_code != oracle_code)

    examples: list[dict[str, Any]] = []
    for y, x in np.argwhere(half_mismatch)[:64]:
        model_bits = int(model_half[y, x])
        oracle_bits = int(oracle_half[y, x])
        examples.append(
            {
                "x": int(x),
                "y": int(y),
                "tileX": int(x) // raster.TILE_SIZE,
                "tileY": int(y) // raster.TILE_SIZE,
                "pixelParity": [int(x) & 1, int(y) & 1],
                "quadrant": int(quadrants[y, x]),
                "primitive": int(primitives[y, x]),
                "modelHalfBits": f"0x{model_bits:04x}",
                "oracleHalfBits": f"0x{oracle_bits:04x}",
                "halfUlpDelta": oracle_bits - model_bits,
                "modelCode": int(model_code[y, x]),
                "oracleCode": int(oracle_code[y, x]),
                "modelSDFBits": [
                    f"0x{int(model_x[y, x]):08x}",
                    f"0x{int(model_y[y, x]):08x}",
                ],
                "capturedSDFBits": [
                    f"0x{int(interpolants[y, x, 0]):08x}",
                    f"0x{int(interpolants[y, x, 1]):08x}",
                ],
                "sdfExact": bool(not coordinate_mismatch[y, x]),
            }
        )

    delta = oracle_half.astype(np.int32) - model_half.astype(np.int32)
    group_counts: list[dict[str, int]] = []
    for quadrant, primitive in sorted(
        set(
            zip(
                quadrants[active].tolist(),
                primitives[active].tolist(),
                strict=True,
            )
        )
    ):
        selected = (
            half_mismatch
            & (quadrants == quadrant)
            & (primitives == primitive)
        )
        active_group = (
            active
            & (quadrants == quadrant)
            & (primitives == primitive)
        )
        group_counts.append(
            {
                "quadrant": int(quadrant),
                "primitive": int(primitive),
                "activePixels": int(np.count_nonzero(active_group)),
                "halfMismatches": int(np.count_nonzero(selected)),
                "codeMismatches": int(np.count_nonzero(selected & code_mismatch)),
                "coordinateMismatches": int(np.count_nonzero(
                    active_group & coordinate_mismatch
                )),
                "xMismatches": int(np.count_nonzero(
                    active_group & x_mismatch
                )),
                "yMismatches": int(np.count_nonzero(
                    active_group & y_mismatch
                )),
            }
        )

    return {
        "schemaVersion": 1,
        "state": arguments.state,
        "geometryMode": arguments.geometry_mode,
        "generatedBounds": [left, top, right, bottom],
        "clippedScissor": list(scissor),
        "activePixels": int(np.count_nonzero(active)),
        "modelVsOracle": {
            "halfMismatchedPixels": int(np.count_nonzero(half_mismatch)),
            "codeMismatchedPixels": int(np.count_nonzero(code_mismatch)),
            "maximumCodeDelta": int(
                np.abs(
                    model_code.astype(np.int16) - oracle_code.astype(np.int16)
                )[active].max(initial=0)
            ),
            "halfUlpDeltaCounts": _count_by(delta, half_mismatch),
        },
        "modelVsCapturedInterpolants": {
            "coordinateMismatchedPixels": int(
                np.count_nonzero(coordinate_mismatch)
            ),
            "xMismatchedPixels": int(np.count_nonzero(x_mismatch)),
            "yMismatchedPixels": int(np.count_nonzero(y_mismatch)),
            "halfMismatchWithExactCoordinates": int(
                np.count_nonzero(half_mismatch & ~coordinate_mismatch)
            ),
            "halfMismatchWithDifferentCoordinates": int(
                np.count_nonzero(half_mismatch & coordinate_mismatch)
            ),
            "absoluteCoordinateMismatchedPixels": int(
                np.count_nonzero(abs_coordinate_mismatch)
            ),
            "absoluteXMismatchedPixels": int(np.count_nonzero(abs_x_mismatch)),
            "absoluteYMismatchedPixels": int(np.count_nonzero(abs_y_mismatch)),
            "halfMismatchWithExactAbsoluteCoordinates": int(
                np.count_nonzero(half_mismatch & ~abs_coordinate_mismatch)
            ),
            "halfMismatchWithDifferentAbsoluteCoordinates": int(
                np.count_nonzero(half_mismatch & abs_coordinate_mismatch)
            ),
        },
        "capturedCoordinateArithmeticReplay": {
            "validPixels": int(np.count_nonzero(captured_replay_valid)),
            "halfMismatchedPixels": int(
                np.count_nonzero(captured_half_mismatch)
            ),
            "exact": bool(not np.any(captured_half_mismatch)),
        },
        "halfMismatchPixelParity": {
            f"{x},{y}": int(
                np.count_nonzero(
                    half_mismatch
                    & ((np.arange(WIDTH)[None, :] & 1) == x)
                    & ((np.arange(HEIGHT)[:, None] & 1) == y)
                )
            )
            for y in range(2)
            for x in range(2)
        },
        "groups": group_counts,
        "examples": examples,
    }


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", type=int, default=43)
    parser.add_argument(
        "--geometry-mode",
        choices=("expanded", "unit"),
        default="expanded",
    )
    parser.add_argument("--oracle", type=Path, required=True)
    parser.add_argument("--interpolants", type=Path, required=True)
    parser.add_argument("--colors", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def main() -> None:
    arguments = parse_arguments()
    result = analyze(arguments)
    serialized = json.dumps(result, indent=2) + "\n"
    if arguments.output is None:
        print(serialized, end="")
    else:
        arguments.output.write_text(serialized, encoding="utf-8")


if __name__ == "__main__":
    main()
