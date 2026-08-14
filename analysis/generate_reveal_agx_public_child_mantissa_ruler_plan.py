#!/usr/bin/env python3.14
"""Generate a dense varying sweep on a correction-bearing public AGX child."""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
from pathlib import Path
from typing import Final

import analyze_reveal_agx_setup_accumulator as accumulator
import generate_reveal_agx_two_product_amplification_plan as amplification


type JsonObject = dict[str, object]
type Vertex = tuple[float, ...]

ROOT: Final = Path(__file__).resolve().parent.parent
OUTPUT_DEFAULT: Final = (
    ROOT / "build" / "analysis-agx-basis" / "public-child-mantissa-ruler-plan-v1"
)
GEOMETRY: Final = (
    (-11.2734375, 1666.921875),
    (512.0, 614.5),
    (66.765625, 1678.484375),
)
BASE_VALUES: Final = (-0.9999999403953552, 0.0, 1.0)
VARIABLE_ULP_OFFSETS: Final = tuple(range(-4096, 4096))
PIXEL: Final = (31, 1664)
TILE: Final = (0, 52)
VERTEX: Final = struct.Struct("<8I")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(4 * 1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def generate(
    output_directory: Path,
    *,
    geometry: tuple[Vertex, ...] = GEOMETRY,
    base_values: tuple[float, ...] = BASE_VALUES,
    source_determinant_ratio_index: int = 6,
    source_determinant_ratio: str = "1/22",
) -> JsonObject:
    if output_directory.exists():
        raise FileExistsError(output_directory)
    bitmap = accumulator.setup.P25_PATH.read_bytes()
    case: JsonObject = {
        "values": list(base_values),
        "tile": list(TILE),
    }
    submitted_geometry: tuple[Vertex, ...] = tuple(
        (vertex[0], vertex[1], 0.0, 0.0, 0.0, 0.0) for vertex in geometry
    )
    vertices = bytearray()
    draws: list[JsonObject] = []
    experiments: list[JsonObject] = []
    split_counts = {"discovery": 0, "holdout": 0}
    skipped: list[int] = []

    for variable_offset in VARIABLE_ULP_OFFSETS:
        result = amplification._lane_values(  # noqa: SLF001
            case,
            submitted_geometry,
            0,
            variable_offset,
            bitmap,
        )
        if result is None:
            skipped.append(variable_offset)
            continue
        lane_values, metadata = result
        semantic = struct.pack("<i", variable_offset)
        split = "holdout" if hashlib.sha256(semantic).digest()[0] < 64 else "discovery"
        record = len(draws)
        for vertex_index, position in enumerate(submitted_geometry):
            vertices.extend(
                VERTEX.pack(
                    accumulator.setup._float_bits(position[0]),  # noqa: SLF001
                    accumulator.setup._float_bits(position[1]),  # noqa: SLF001
                    0,
                    0,
                    *(
                        accumulator.setup._float_bits(values[vertex_index])  # noqa: SLF001
                        for values in lane_values
                    ),
                )
            )
        experiments.append(
            {
                "recordIndex": record,
                "split": split,
                "variableUlpOffset": variable_offset,
                **metadata,
            }
        )
        draws.append(
            {
                "recordIndex": record,
                "targetIndex": 7,
                "targetRecordIndex": 484,
                "sampleRecordIndex": 2528,
                "sampleOrdinal": 0,
                "patternIndex": record,
                "x": PIXEL[0],
                "y": PIXEL[1],
                "tileX": TILE[0],
                "tileY": TILE[1],
            }
        )
        split_counts[split] += 1

    output_directory.mkdir(parents=True)
    vertex_path = output_directory / "reveal-agx-setup-accumulator-vertices.bin"
    vertex_path.write_bytes(vertices)
    plan_path = output_directory / "reveal-agx-setup-accumulator-plan.json"
    census = {
        "targetCount": 8,
        "candidateCount": len(VARIABLE_ULP_OFFSETS),
        "skippedCount": len(skipped),
        "patternCount": len(draws),
        "drawCount": len(draws),
        "coefficientTripleCount": len(draws) * 4,
        "discoveryPatternCount": split_counts["discovery"],
        "holdoutPatternCount": split_counts["holdout"],
    }
    plan: JsonObject = {
        "schema": "walle-reveal-agx-setup-accumulator-plan-v1",
        "authority": {
            "opensReferencePixels": False,
            "usesOutputFeedback": False,
            "usesCorrectionBearingPublicChildGeometry": True,
            "establishesTwoProductInteractionLaw": False,
        },
        "target": {"width": 2_048, "height": 2_048},
        "ruler": {
            "sourceAmplificationCaseIndex": 5,
            "sourceDeterminantRatioIndex": source_determinant_ratio_index,
            "sourceDeterminantRatio": source_determinant_ratio,
            "geometry": [list(position[:2]) for position in submitted_geometry],
            "baseDifferenceBits": [
                f"0x{accumulator.setup._float_bits(value):08x}"  # noqa: SLF001
                for value in base_values
            ],
            "fixedFirstNonanchorUlpOffset": 0,
            "variableOffsetMinimum": VARIABLE_ULP_OFFSETS[0],
            "variableOffsetMaximum": VARIABLE_ULP_OFFSETS[-1],
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
        "experiments": experiments,
        "draws": draws,
        "census": census,
    }
    plan_path.write_text(
        json.dumps(plan, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    manifest: JsonObject = {
        "schema": "walle-reveal-agx-public-child-mantissa-ruler-plan-manifest-v1",
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
        "census": census,
        "skippedVariableUlpOffsets": skipped,
    }
    (output_directory / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT_DEFAULT)
    arguments = parser.parse_args()
    result = generate(arguments.output)
    print(json.dumps(result["census"], indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
