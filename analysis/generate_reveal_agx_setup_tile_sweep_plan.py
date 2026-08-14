#!/usr/bin/env python3
"""Generate wide-tile AGX setup-accumulator probe plans.

The existing accumulator capture observes at most three nearby coefficient
tiles per triangle.  A rounded float slope admits several possible hidden
27-bit setup values, so those samples leave most pairs ambiguous.  This
generator selects nine widely separated, strictly interior tiles from each of
the same eight public canonical triangles.  Three schema-compatible plans let
the already authenticated Metal probe measure all nine positions without any
rendered-output or reference-pixel feedback.
"""

import argparse
import hashlib
import json
import struct
import sys
from pathlib import Path
from typing import Final


ROOT: Final = Path(__file__).resolve().parent.parent
ANALYSIS: Final = ROOT / "analysis"
sys.path.insert(0, str(ANALYSIS))

import analyze_reveal_agx_basis_phase as phase  # noqa: E402
import analyze_reveal_agx_clip_setup_split as setup  # noqa: E402
import generate_reveal_agx_setup_accumulator_plan as accumulator  # noqa: E402


type JsonObject = dict[str, object]
type FixedPoint = tuple[int, int]
type Pixel = tuple[int, int]

CATALOG_DEFAULT: Final = accumulator.CATALOG_DEFAULT
OUTPUT_DEFAULT: Final = (
    ROOT / "build" / "analysis-agx-basis" / "setup-tile-sweep-plan-v1"
)
TARGET_WIDTH: Final = 2_048
TARGET_HEIGHT: Final = 2_048
TILE_SIZE: Final = 32
SUBPIXEL_SCALE: Final = 256
SELECTED_TILE_COUNT: Final = 9
PLAN_COUNT: Final = 3
VERTEX: Final = struct.Struct("<8I")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _cross(left: FixedPoint, right: FixedPoint) -> int:
    return left[0] * right[1] - left[1] * right[0]


def _subtract(left: FixedPoint, right: FixedPoint) -> FixedPoint:
    return left[0] - right[0], left[1] - right[1]


def _inside_margin(positions: tuple[FixedPoint, ...], pixel: Pixel) -> int:
    point = (
        pixel[0] * SUBPIXEL_SCALE + SUBPIXEL_SCALE // 2,
        pixel[1] * SUBPIXEL_SCALE + SUBPIXEL_SCALE // 2,
    )
    weights = tuple(
        _cross(
            _subtract(positions[(index + 1) % 3], point),
            _subtract(positions[(index + 2) % 3], point),
        )
        for index in range(3)
    )
    determinant = _cross(
        _subtract(positions[1], positions[0]),
        _subtract(positions[2], positions[0]),
    )
    if determinant == 0:
        raise ValueError("degenerate target triangle")
    signed = weights if determinant > 0 else tuple(-weight for weight in weights)
    return min(signed)


def _interior_tiles(positions: tuple[FixedPoint, ...]) -> tuple[Pixel, ...]:
    minimum_x = max(0, min(point[0] for point in positions) // SUBPIXEL_SCALE)
    maximum_x = min(
        TARGET_WIDTH - 1,
        max(point[0] for point in positions) // SUBPIXEL_SCALE,
    )
    minimum_y = max(0, min(point[1] for point in positions) // SUBPIXEL_SCALE)
    maximum_y = min(
        TARGET_HEIGHT - 1,
        max(point[1] for point in positions) // SUBPIXEL_SCALE,
    )
    result: list[Pixel] = []
    for tile_y in range(minimum_y // TILE_SIZE, maximum_y // TILE_SIZE + 1):
        for tile_x in range(minimum_x // TILE_SIZE, maximum_x // TILE_SIZE + 1):
            best: tuple[int, Pixel] | None = None
            start_x = tile_x * TILE_SIZE
            start_y = tile_y * TILE_SIZE
            for y in range(start_y, min(start_y + TILE_SIZE, TARGET_HEIGHT)):
                for x in range(start_x, min(start_x + TILE_SIZE, TARGET_WIDTH)):
                    margin = _inside_margin(positions, (x, y))
                    if margin > 0 and (
                        best is None
                        or (margin, -y, -x)
                        > (
                            best[0],
                            -best[1][1],
                            -best[1][0],
                        )
                    ):
                        best = margin, (x, y)
            if best is not None:
                result.append(best[1])
    return tuple(result)


def _tile(pixel: Pixel) -> tuple[int, int]:
    return pixel[0] // TILE_SIZE, pixel[1] // TILE_SIZE


def _select_wide_tiles(candidates: tuple[Pixel, ...]) -> tuple[Pixel, ...]:
    if len(candidates) < SELECTED_TILE_COUNT:
        raise ValueError("target has fewer than nine interior tiles")
    tile_points = tuple((_tile(pixel), pixel) for pixel in candidates)
    center_x = sum(tile[0] for tile, _pixel in tile_points) / len(tile_points)
    center_y = sum(tile[1] for tile, _pixel in tile_points) / len(tile_points)

    selected: list[tuple[tuple[int, int], Pixel]] = []

    def add(entry: tuple[tuple[int, int], Pixel]) -> None:
        if entry not in selected:
            selected.append(entry)

    add(
        min(
            tile_points,
            key=lambda entry: (
                (entry[0][0] - center_x) ** 2 + (entry[0][1] - center_y) ** 2,
                entry[0],
            ),
        )
    )
    directions = (
        (1, 0),
        (-1, 0),
        (0, 1),
        (0, -1),
        (1, 1),
        (-1, -1),
        (1, -1),
        (-1, 1),
    )
    for dx, dy in directions:
        add(
            max(
                tile_points,
                key=lambda entry: (
                    entry[0][0] * dx + entry[0][1] * dy,
                    -entry[0][1],
                    -entry[0][0],
                ),
            )
        )

    while len(selected) < SELECTED_TILE_COUNT:
        add(
            max(
                (entry for entry in tile_points if entry not in selected),
                key=lambda entry: (
                    min(
                        (entry[0][0] - chosen[0][0]) ** 2
                        + (entry[0][1] - chosen[0][1]) ** 2
                        for chosen in selected
                    ),
                    -entry[0][1],
                    -entry[0][0],
                ),
            )
        )
    return tuple(pixel for _tile_position, pixel in selected[:SELECTED_TILE_COUNT])


def _write_plan(
    output_directory: Path,
    *,
    batch_index: int,
    catalog_path: Path,
    targets: tuple[
        tuple[phase.Sample, tuple[tuple[float, ...], ...], tuple[Pixel, ...]], ...
    ],
    patterns: tuple[JsonObject, ...],
) -> JsonObject:
    batch_directory = output_directory / f"batch-{batch_index}"
    batch_directory.mkdir()
    vertices = bytearray()
    draws: list[JsonObject] = []
    target_objects: list[JsonObject] = []
    for target_index, (target, child, selected) in enumerate(targets):
        pixels = selected[batch_index::PLAN_COUNT]
        if len(pixels) != 3:
            raise ValueError("tile batch does not contain three positions")
        target_objects.append(
            {
                "targetRecordIndex": target.record_index,
                "caseIndex": target.case_index,
                "state": target.state,
                "sourcePrimitive": target.source_primitive,
                "childOrdinal": target.child_ordinal,
                "childOrdinalWithinSource": target.child_ordinal_within_source,
                "pixels": [list(pixel) for pixel in pixels],
                "tiles": [list(_tile(pixel)) for pixel in pixels],
            }
        )
        for sample_ordinal, pixel in enumerate(pixels):
            for pattern_index, pattern in enumerate(patterns):
                values = pattern["values"]
                if not isinstance(values, tuple) or len(values) != 4:
                    raise ValueError("pattern shape differs")
                record_index = len(draws)
                for local_vertex, vertex in enumerate(child):
                    lane_values = tuple(values[lane][local_vertex] for lane in range(4))
                    vertices.extend(
                        VERTEX.pack(
                            phase._bits(vertex[0]),  # noqa: SLF001
                            phase._bits(vertex[1]),  # noqa: SLF001
                            0,
                            0,
                            *(phase._bits(value) for value in lane_values),  # noqa: SLF001
                        )
                    )
                tile_x, tile_y = _tile(pixel)
                draws.append(
                    {
                        "recordIndex": record_index,
                        "targetIndex": target_index,
                        "targetRecordIndex": target.record_index,
                        "sampleRecordIndex": target.record_index,
                        "sampleOrdinal": sample_ordinal,
                        "patternIndex": pattern_index,
                        "x": pixel[0],
                        "y": pixel[1],
                        "tileX": tile_x,
                        "tileY": tile_y,
                    }
                )

    vertex_path = batch_directory / "reveal-agx-setup-accumulator-vertices.bin"
    vertex_path.write_bytes(vertices)
    plan_path = batch_directory / "reveal-agx-setup-accumulator-plan.json"
    plan: JsonObject = {
        "schema": "walle-reveal-agx-setup-accumulator-plan-v1",
        "authority": {
            "usesPublicRevealGeometryOnly": True,
            "opensReferencePixels": False,
            "usesOutputFeedback": False,
            "establishesAGXAccumulatorLaw": False,
        },
        "target": {"width": TARGET_WIDTH, "height": TARGET_HEIGHT},
        "catalog": {
            "path": str(catalog_path),
            "bytes": catalog_path.stat().st_size,
            "sha256": _sha256(catalog_path),
        },
        "tileSweep": {
            "batchIndex": batch_index,
            "batchCount": PLAN_COUNT,
            "selectedTileCountPerTarget": SELECTED_TILE_COUNT,
            "selection": (
                "strictly interior public-geometry tiles; centroid and eight "
                "deterministic directional extremes"
            ),
        },
        "vertexData": {
            "file": vertex_path.name,
            "bytes": len(vertices),
            "sha256": _sha256(vertex_path),
            "recordCount": len(draws),
            "verticesPerRecord": 3,
            "wordsPerVertex": 8,
            "layout": "positionXY,pad2,varyingRGBA; little-endian uint32",
        },
        "targets": target_objects,
        "patterns": [
            {key: value for key, value in pattern.items() if key != "values"}
            for pattern in patterns
        ],
        "draws": draws,
        "census": {
            "targetCount": len(targets),
            "patternCount": len(patterns),
            "drawCount": len(draws),
            "coefficientTripleCount": len(draws) * 4,
        },
    }
    plan_path.write_text(
        json.dumps(plan, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {
        "batchIndex": batch_index,
        "plan": {
            "file": plan_path.relative_to(output_directory).as_posix(),
            "bytes": plan_path.stat().st_size,
            "sha256": _sha256(plan_path),
        },
        "vertexData": {
            "file": vertex_path.relative_to(output_directory).as_posix(),
            "bytes": vertex_path.stat().st_size,
            "sha256": _sha256(vertex_path),
        },
    }


def generate(catalog_path: Path, output_directory: Path) -> JsonObject:
    if output_directory.exists():
        raise FileExistsError(output_directory)
    _catalog, samples = phase._load_catalog(catalog_path)  # noqa: SLF001
    sample_by_record = {sample.record_index: sample for sample in samples}
    patterns = accumulator._patterns()  # noqa: SLF001
    targets: list[
        tuple[phase.Sample, tuple[tuple[float, ...], ...], tuple[Pixel, ...]]
    ] = []
    for record_index in accumulator.TARGET_RECORDS:
        target = sample_by_record[record_index]
        child = phase._canonical_children(target)[  # noqa: SLF001
            target.child_ordinal_within_source
        ]
        positions = setup._fixed_positions(child)  # noqa: SLF001
        selected = _select_wide_tiles(_interior_tiles(positions))
        targets.append((target, child, selected))

    output_directory.mkdir(parents=False)
    batches = tuple(
        _write_plan(
            output_directory,
            batch_index=batch_index,
            catalog_path=catalog_path,
            targets=tuple(targets),
            patterns=patterns,
        )
        for batch_index in range(PLAN_COUNT)
    )
    result: JsonObject = {
        "schema": "walle-reveal-agx-setup-tile-sweep-plans-v1",
        "authority": {
            "usesPublicRevealGeometryOnly": True,
            "opensReferencePixels": False,
            "usesOutputFeedback": False,
            "establishesAGXAccumulatorLaw": False,
        },
        "generator": {
            "path": str(Path(__file__).resolve()),
            "sha256": _sha256(Path(__file__).resolve()),
        },
        "catalog": {
            "path": str(catalog_path),
            "sha256": _sha256(catalog_path),
        },
        "targetCount": len(targets),
        "patternCount": len(patterns),
        "selectedTileCountPerTarget": SELECTED_TILE_COUNT,
        "batches": list(batches),
        "selectedTiles": [
            {
                "targetRecordIndex": target.record_index,
                "pixels": [list(pixel) for pixel in selected],
                "tiles": [list(_tile(pixel)) for pixel in selected],
            }
            for target, _child, selected in targets
        ],
    }
    manifest_path = output_directory / "manifest.json"
    manifest_path.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, default=CATALOG_DEFAULT)
    parser.add_argument("--output", type=Path, default=OUTPUT_DEFAULT)
    arguments = parser.parse_args()
    print(json.dumps(generate(arguments.catalog, arguments.output), indent=2))


if __name__ == "__main__":
    main()
