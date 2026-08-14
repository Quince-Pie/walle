#!/usr/bin/env python3.14
"""Generate a focused output-blind AGX signed-join probe plan.

The prior wide-tile coefficient capture leaves 24 setup-constant residuals.
This plan perturbs only their pattern/component vertex words and samples three
strictly interior tiles per public triangle.  It never inspects rendered output.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import struct
import sys
from pathlib import Path
from typing import Final


ROOT: Final = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "analysis"))

import analyze_reveal_agx_basis_phase as phase  # noqa: E402
import generate_reveal_agx_setup_accumulator_plan as accumulator  # noqa: E402
import generate_reveal_agx_setup_tile_sweep_plan as tile_sweep  # noqa: E402


type JsonObject = dict[str, object]
type Pixel = tuple[int, int]

OUTPUT_DEFAULT: Final = ROOT / "build" / "analysis-agx-basis" / "signed-join-plan-v1"
JOIN_RESULT: Final = (
    ROOT / "build" / "analysis-agx-basis" / "join-preimage" / "result.json"
)
PERTURBATIONS: Final = (
    -256,
    -128,
    -64,
    -32,
    -16,
    -8,
    -4,
    -2,
    -1,
    1,
    2,
    4,
    8,
    16,
    32,
    64,
    128,
    256,
)
VERTEX: Final = struct.Struct("<8I")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _ordered_key(bits: int) -> int:
    return (~bits & 0xFFFF_FFFF) if bits & 0x8000_0000 else bits | 0x8000_0000


def _bits_from_ordered_key(key: int) -> int:
    if not 0 <= key <= 0xFFFF_FFFF:
        raise ValueError("perturbed binary32 key escaped uint32")
    return (~key & 0xFFFF_FFFF) if key < 0x8000_0000 else key & 0x7FFF_FFFF


def _perturb(value: float, offset: int) -> float:
    bits = phase._bits(value)  # noqa: SLF001
    perturbed = _bits_from_ordered_key(_ordered_key(bits) + offset)
    result = phase._float(perturbed)  # noqa: SLF001
    if not math.isfinite(result):
        raise ValueError("perturbation generated a non-finite value")
    return result


def _load_join_result() -> tuple[JsonObject, list[JsonObject]]:
    result = json.loads(JOIN_RESULT.read_text(encoding="utf-8"))
    records = result.get("records")
    census = result.get("census")
    if (
        result.get("schema") != "walle-reveal-agx-join-preimage-analysis-v1"
        or not isinstance(records, list)
        or not all(isinstance(record, dict) for record in records)
        or not isinstance(census, dict)
        or census.get("residualCount") != 24
    ):
        raise ValueError("join-preimage evidence differs")
    return result, records  # type: ignore[return-value]


def _selected_pixels(
    child: tuple[tuple[float, ...], ...], required_tiles: set[tuple[int, int]]
) -> tuple[Pixel, Pixel, Pixel]:
    positions = tile_sweep.setup._fixed_positions(child)  # noqa: SLF001
    candidates = tile_sweep._interior_tiles(positions)  # noqa: SLF001
    by_tile = {tile_sweep._tile(pixel): pixel for pixel in candidates}  # noqa: SLF001
    selected: list[Pixel] = []
    for tile in sorted(required_tiles):
        if tile not in by_tile:
            raise ValueError(f"residual tile is not strictly interior: {tile}")
        selected.append(by_tile[tile])
    for pixel in tile_sweep._select_wide_tiles(candidates):  # noqa: SLF001
        if len(selected) == 3:
            break
        if pixel not in selected:
            selected.append(pixel)
    if len(selected) != 3:
        raise ValueError("target does not provide three selected pixels")
    return selected[0], selected[1], selected[2]


def _patterns(records: list[JsonObject]) -> tuple[JsonObject, ...]:
    base_patterns = accumulator._patterns()  # noqa: SLF001
    pairs = sorted(
        {(int(record["patternIndex"]), int(record["component"])) for record in records}
    )
    patterns: list[JsonObject] = []
    for base_index, component in pairs:
        base = base_patterns[base_index]
        values = base.get("values")
        if not isinstance(values, tuple) or len(values) != 4:
            raise ValueError("base pattern value shape differs")
        patterns.append(
            {
                "basePatternIndex": base_index,
                "component": component,
                "vertex": 0,
                "ulpOffset": 0,
                "values": values,
            }
        )
        for vertex in range(3):
            for offset in PERTURBATIONS:
                changed = [list(lane) for lane in values]
                changed[component][vertex] = _perturb(
                    changed[component][vertex], offset
                )
                patterns.append(
                    {
                        "basePatternIndex": base_index,
                        "component": component,
                        "vertex": vertex,
                        "ulpOffset": offset,
                        "values": tuple(tuple(lane) for lane in changed),
                    }
                )
    expected_count = len(pairs) * (1 + 3 * len(PERTURBATIONS))
    if len(patterns) != expected_count:
        raise AssertionError("focused pattern census differs")
    return tuple(patterns)


def generate(catalog_path: Path, output_directory: Path) -> JsonObject:
    if output_directory.exists():
        raise FileExistsError(output_directory)
    _join, residuals = _load_join_result()
    catalog, samples = phase._load_catalog(catalog_path)  # noqa: SLF001
    sample_by_record = {sample.record_index: sample for sample in samples}
    base_patterns = _patterns(residuals)

    residual_tiles: dict[int, dict[tuple[int, int], int]] = {
        target: {} for target in range(len(accumulator.TARGET_RECORDS))
    }
    for record in residuals:
        tile = record["tile"]
        interval = record["compatibleJoinOffset"]
        if not isinstance(tile, list) or len(tile) != 2:
            raise ValueError("residual tile shape differs")
        if not isinstance(interval, dict) or not isinstance(interval.get("count"), int):
            raise ValueError("residual preimage interval shape differs")
        key = (int(tile[0]), int(tile[1]))
        target_tiles = residual_tiles[int(record["targetIndex"])]
        target_tiles[key] = min(target_tiles.get(key, 1 << 30), interval["count"])

    vertices = bytearray()
    draws: list[JsonObject] = []
    targets: list[JsonObject] = []
    for target_index, target_record in enumerate(accumulator.TARGET_RECORDS):
        target = sample_by_record[target_record]
        child = phase._canonical_children(target)[  # noqa: SLF001
            target.child_ordinal_within_source
        ]
        prioritized = sorted(
            residual_tiles[target_index],
            key=lambda tile: (residual_tiles[target_index][tile], tile),
        )
        pixels = _selected_pixels(child, set(prioritized[:3]))
        targets.append(
            {
                "targetRecordIndex": target_record,
                "caseIndex": target.case_index,
                "state": target.state,
                "sourcePrimitive": target.source_primitive,
                "childOrdinal": target.child_ordinal,
                "childOrdinalWithinSource": target.child_ordinal_within_source,
                "pixels": [list(pixel) for pixel in pixels],
                "tiles": [
                    list(tile_sweep._tile(pixel))
                    for pixel in pixels  # noqa: SLF001
                ],
            }
        )
        for sample_ordinal, pixel in enumerate(pixels):
            tile_x, tile_y = tile_sweep._tile(pixel)  # noqa: SLF001
            for pattern_index, pattern in enumerate(base_patterns):
                values = pattern["values"]
                if not isinstance(values, tuple):
                    raise ValueError("focused values are not a tuple")
                record_index = len(draws)
                for local_vertex, vertex in enumerate(child):
                    vertices.extend(
                        VERTEX.pack(
                            phase._bits(vertex[0]),  # noqa: SLF001
                            phase._bits(vertex[1]),  # noqa: SLF001
                            0,
                            0,
                            *(
                                phase._bits(values[lane][local_vertex])  # noqa: SLF001
                                for lane in range(4)
                            ),
                        )
                    )
                draws.append(
                    {
                        "recordIndex": record_index,
                        "targetIndex": target_index,
                        "targetRecordIndex": target_record,
                        "sampleRecordIndex": target_record,
                        "sampleOrdinal": sample_ordinal,
                        "patternIndex": pattern_index,
                        "x": pixel[0],
                        "y": pixel[1],
                        "tileX": tile_x,
                        "tileY": tile_y,
                    }
                )

    output_directory.mkdir(parents=True)
    vertex_path = output_directory / "reveal-agx-setup-accumulator-vertices.bin"
    vertex_path.write_bytes(vertices)
    plan_path = output_directory / "reveal-agx-setup-accumulator-plan.json"
    plan: JsonObject = {
        "schema": "walle-reveal-agx-setup-accumulator-plan-v1",
        "authority": {
            "usesPublicRevealGeometryOnly": True,
            "opensReferencePixels": False,
            "usesOutputFeedback": False,
            "establishesAGXAccumulatorLaw": False,
        },
        "target": {"width": 2_048, "height": 2_048},
        "catalog": {
            "path": str(catalog_path),
            "bytes": catalog_path.stat().st_size,
            "sha256": _sha256(catalog_path),
        },
        "joinPreimageEvidence": {
            "path": JOIN_RESULT.relative_to(ROOT).as_posix(),
            "bytes": JOIN_RESULT.stat().st_size,
            "sha256": _sha256(JOIN_RESULT),
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
        "targets": targets,
        "patterns": [
            {key: value for key, value in pattern.items() if key != "values"}
            for pattern in base_patterns
        ],
        "draws": draws,
        "census": {
            "targetCount": len(targets),
            "patternCount": len(base_patterns),
            "drawCount": len(draws),
            "coefficientTripleCount": len(draws) * 4,
        },
    }
    plan_path.write_text(json.dumps(plan, indent=2, sort_keys=True) + "\n")
    manifest: JsonObject = {
        "schema": "walle-reveal-agx-signed-join-plan-manifest-v1",
        "generator": {
            "path": Path(__file__).relative_to(ROOT).as_posix(),
            "bytes": Path(__file__).stat().st_size,
            "sha256": _sha256(Path(__file__)),
        },
        "plan": {
            "file": plan_path.name,
            "bytes": plan_path.stat().st_size,
            "sha256": _sha256(plan_path),
        },
        "vertexData": {
            "file": vertex_path.name,
            "bytes": vertex_path.stat().st_size,
            "sha256": _sha256(vertex_path),
        },
        "sourceEvidence": {
            "joinPreimageSha256": _sha256(JOIN_RESULT),
            "catalogSha256": _sha256(catalog_path),
        },
        "census": plan["census"],
    }
    manifest_path = output_directory / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, default=accumulator.CATALOG_DEFAULT)
    parser.add_argument("--output", type=Path, default=OUTPUT_DEFAULT)
    arguments = parser.parse_args()
    manifest = generate(arguments.catalog, arguments.output)
    print(json.dumps(manifest["census"], indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
