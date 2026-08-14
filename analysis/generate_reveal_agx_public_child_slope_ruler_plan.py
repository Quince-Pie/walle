#!/usr/bin/env python3.14
"""Generate a determinant-amplified real-child ruler for exact slope inversion."""

import argparse
import hashlib
import json
import struct
from fractions import Fraction
from pathlib import Path
from typing import Final

import analyze_reveal_agx_setup_accumulator as accumulator
import generate_reveal_agx_two_product_amplification_plan as amplification
import generate_reveal_agx_two_product_tomography_plan as tomography


type JsonObject = dict[str, object]

ROOT: Final = Path(__file__).resolve().parent.parent
OUTPUT_DEFAULT: Final = (
    ROOT / "build" / "analysis-agx-basis" / "public-child-slope-ruler-plan-v2"
)
SOURCE_CASE_INDEX: Final = 5
DETERMINANT_RATIO: Final = Fraction(1, 10_000)
VARIABLE_ULP_OFFSETS: Final = tuple(range(-4096, 4096))
VERTEX: Final = struct.Struct("<8I")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(4 * 1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def generate(output_directory: Path) -> JsonObject:
    if output_directory.exists():
        raise FileExistsError(output_directory)
    case = tomography._base_cases()[SOURCE_CASE_INDEX]  # noqa: SLF001
    geometry = amplification._amplified_geometry(case, DETERMINANT_RATIO)  # noqa: SLF001
    values = tuple(accumulator.setup._float32(float(value)) for value in case["values"])  # type: ignore[index]  # noqa: SLF001
    anchor = int(case["anchorVertex"])
    differences = tuple(
        accumulator.setup._float32(value - values[anchor])  # noqa: SLF001
        for value in values
    )
    pixel = tuple(int(value) for value in case["pixel"])  # type: ignore[arg-type]
    tile = tuple(int(value) for value in case["tile"])  # type: ignore[arg-type]
    vertices = bytearray()
    draws: list[JsonObject] = []
    experiments: list[JsonObject] = []
    split_counts = {"discovery": 0, "holdout": 0}

    nonanchors = [index for index in range(3) if index != anchor]
    for variable_offset in VARIABLE_ULP_OFFSETS:
        submitted_values = list(differences)
        submitted_values[nonanchors[1]] = tomography._perturb(  # noqa: SLF001
            submitted_values[nonanchors[1]], variable_offset
        )
        semantic = struct.pack("<i", variable_offset)
        split = "holdout" if hashlib.sha256(semantic).digest()[0] < 64 else "discovery"
        record = len(draws)
        for vertex_index, vertex in enumerate(geometry):
            varying_bits = accumulator.setup._float_bits(  # noqa: SLF001
                submitted_values[vertex_index]
            )
            vertices.extend(
                VERTEX.pack(
                    accumulator.setup._float_bits(vertex[0]),  # noqa: SLF001
                    accumulator.setup._float_bits(vertex[1]),  # noqa: SLF001
                    0,
                    0,
                    varying_bits,
                    varying_bits,
                    varying_bits,
                    varying_bits,
                )
            )
        experiments.append(
            {
                "recordIndex": record,
                "split": split,
                "variableUlpOffset": variable_offset,
            }
        )
        draws.append(
            {
                "recordIndex": record,
                "targetIndex": int(case["targetIndex"]),
                "targetRecordIndex": int(case["targetRecordIndex"]),
                "sampleRecordIndex": int(case["sourceRecordIndex"]),
                "sampleOrdinal": 0,
                "patternIndex": record,
                "x": pixel[0],
                "y": pixel[1],
                "tileX": tile[0],
                "tileY": tile[1],
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
        "skippedCount": 0,
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
            "sourceCaseIndex": SOURCE_CASE_INDEX,
            "sourceRecordIndex": case["sourceRecordIndex"],
            "determinantRatio": str(DETERMINANT_RATIO),
            "geometry": [list(vertex[:2]) for vertex in geometry],
            "baseDifferenceBits": [
                f"0x{accumulator.setup._float_bits(value):08x}"  # noqa: SLF001
                for value in differences
            ],
            "fixedFirstNonanchorUlpOffset": 0,
            "variableOffsetMinimum": VARIABLE_ULP_OFFSETS[0],
            "variableOffsetMaximum": VARIABLE_ULP_OFFSETS[-1],
            "lanePolicy": "zero-anchor duplicate-rgba slope oracle",
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
        "schema": "walle-reveal-agx-public-child-slope-ruler-plan-manifest-v2",
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
    }
    (output_directory / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT_DEFAULT)
    arguments = parser.parse_args()
    print(json.dumps(generate(arguments.output)["census"], indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
