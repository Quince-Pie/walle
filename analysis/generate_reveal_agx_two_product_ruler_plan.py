#!/usr/bin/env python3.14
"""Generate an M1 AGX two-product probe with one exact power-of-two ruler."""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
import sys
from pathlib import Path
from typing import Final


ROOT: Final = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "analysis"), str(ROOT / "lg-test" / "Analysis")]

import analyze_reveal_agx_join_preimage as preimage  # noqa: E402
import analyze_reveal_agx_setup_accumulator as accumulator  # noqa: E402
import generate_reveal_agx_two_product_tomography_plan as tomography  # noqa: E402


type JsonObject = dict[str, object]
type Vertex = tuple[float, ...]

OUTPUT_DEFAULT: Final = (
    ROOT / "build" / "analysis-agx-basis" / "two-product-ruler-plan-v2"
)
GEOMETRY: Final = ((0.0, 0.0), (1024.0, 0.0), (0.0, 1024.0))
PIXEL: Final = (272, 240)
TILE: Final = (8, 7)
RULER_DIFFERENCE: Final = -1.0
EXPECTED_FIXED_MIDDLE_TERM: Final = (-1, 1 << 26, -8)
VARIABLE_HIGH_BITS: Final = 0x3F924000
LOW_BIT_COUNT: Final = 13
LOW_BIT_CENSUS: Final = 1 << LOW_BIT_COUNT
ANCHOR_PROBE_OFFSETS: Final = (
    -4096,
    -2048,
    -1024,
    -512,
    -256,
    -128,
    -64,
    -32,
    -16,
    -8,
    0,
    8,
    16,
    32,
    64,
    128,
    256,
    512,
    1024,
    2048,
    4096,
)
VERTEX: Final = struct.Struct("<8I")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(4 * 1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _float(bits: int) -> float:
    return struct.unpack("<f", struct.pack("<I", bits))[0]


def _lane_values(
    variable_bits: int, bitmap: bytes
) -> tuple[list[tuple[float, ...]], JsonObject] | None:
    variable = _float(variable_bits)
    differences = (0.0, RULER_DIFFERENCE, variable)
    zero_vertices = tuple(
        (position[0], position[1], differences[index], 0.0, 0.0, 0.0)
        for index, position in enumerate(GEOMETRY)
    )
    zero_anchor, determinant, terms = preimage._middle_terms(  # noqa: SLF001
        zero_vertices, 0, TILE
    )
    sign, index, exponent = preimage._joined_index(terms)  # noqa: SLF001
    if (
        zero_anchor != 0
        or len(terms) != 2
        or terms[0][0] == terms[1][0]
        or sign == 0
        or terms[0] != EXPECTED_FIXED_MIDDLE_TERM
    ):
        return None
    selector, selector_exponent = accumulator.setup._p25_selector(  # noqa: SLF001
        determinant, bitmap
    )
    zero_coefficient = preimage._constant_from_join(  # noqa: SLF001
        0,
        sign,
        index,
        exponent,
        selector,
        selector_exponent,
    )
    if zero_coefficient is None:
        return None
    cancellation_anchor = accumulator.setup._float32(  # noqa: SLF001
        float(-accumulator.export._fraction(zero_coefficient))  # noqa: SLF001
    )
    center_key = tomography._ordered_key(  # noqa: SLF001
        accumulator.setup._float_bits(cancellation_anchor)  # noqa: SLF001
    )
    candidates: list[tuple[int, int, tuple[float, ...]]] = []
    for anchor_offset in ANCHOR_PROBE_OFFSETS:
        key = center_key + anchor_offset
        if not 0 <= key <= 0xFFFF_FFFF:
            continue
        common = accumulator.setup._float32(  # noqa: SLF001
            _float(tomography._bits_from_ordered_key(key))  # noqa: SLF001
        )
        submitted_values = tuple(
            accumulator.setup._float32(common + difference)  # noqa: SLF001
            for difference in differences
        )
        submitted = tuple(
            (position[0], position[1], submitted_values[vertex], 0.0, 0.0, 0.0)
            for vertex, position in enumerate(GEOMETRY)
        )
        actual_anchor, actual_determinant, actual_terms = preimage._middle_terms(  # noqa: SLF001
            submitted, 0, TILE
        )
        if actual_determinant == determinant and actual_terms == terms:
            candidates.append((anchor_offset, actual_anchor, submitted_values))
    if not candidates:
        return None
    selected = (
        tomography._evenly_spaced(candidates, 4)  # noqa: SLF001
        if len(candidates) >= 4
        else [candidates[index * len(candidates) // 4] for index in range(4)]
    )
    return [candidate[2] for candidate in selected], {
        "variableBits": f"0x{variable_bits:08x}",
        "variableMantissaLowBits": variable_bits & (LOW_BIT_CENSUS - 1),
        "determinant": determinant,
        "middleTerms": [
            {"sign": term_sign, "index": term_index, "exponent": term_exponent}
            for term_sign, term_index, term_exponent in terms
        ],
        "predictedJoin": {"sign": sign, "index": index, "exponent": exponent},
        "anchors": [
            {"anchorUlpOffset": offset, "anchorBits": f"0x{anchor_bits:08x}"}
            for offset, anchor_bits, _values in selected
        ],
    }


def generate(output_directory: Path) -> JsonObject:
    if output_directory.exists():
        raise FileExistsError(output_directory)
    bitmap = accumulator.setup.P25_PATH.read_bytes()
    vertices = bytearray()
    draws: list[JsonObject] = []
    experiments: list[JsonObject] = []
    split_counts = {"discovery": 0, "holdout": 0}
    skipped: list[int] = []

    for low_bits in range(LOW_BIT_CENSUS):
        variable_bits = VARIABLE_HIGH_BITS | low_bits
        result = _lane_values(variable_bits, bitmap)
        if result is None:
            skipped.append(low_bits)
            continue
        lane_values, metadata = result
        semantic = struct.pack("<I", variable_bits)
        split = "holdout" if hashlib.sha256(semantic).digest()[0] < 64 else "discovery"
        record = len(draws)
        for vertex_index, position in enumerate(GEOMETRY):
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
                **metadata,
            }
        )
        draws.append(
            {
                "recordIndex": record,
                "targetIndex": 0,
                "targetRecordIndex": 0,
                "sampleRecordIndex": 0,
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
        "candidateCount": LOW_BIT_CENSUS,
        "skippedCount": len(skipped),
        "patternCount": len(draws),
        "drawCount": len(draws),
        "coefficientTripleCount": len(draws) * 4,
        "discoveryPatternCount": split_counts["discovery"],
        "holdoutPatternCount": split_counts["holdout"],
        "variableMantissaLowBitCount": LOW_BIT_COUNT,
    }
    plan: JsonObject = {
        "schema": "walle-reveal-agx-setup-accumulator-plan-v1",
        "authority": {
            "opensReferencePixels": False,
            "usesOutputFeedback": False,
            "usesSyntheticAxisAlignedGeometry": True,
            "establishesTwoProductInteractionLaw": False,
        },
        "target": {"width": 2_048, "height": 2_048},
        "ruler": {
            "geometry": [list(position) for position in GEOMETRY],
            "pixel": list(PIXEL),
            "tile": list(TILE),
            "fixedDifferenceBits": f"0x{accumulator.setup._float_bits(RULER_DIFFERENCE):08x}",  # noqa: SLF001
            "expectedFixedMiddleTerm": {
                "sign": EXPECTED_FIXED_MIDDLE_TERM[0],
                "index": EXPECTED_FIXED_MIDDLE_TERM[1],
                "exponent": EXPECTED_FIXED_MIDDLE_TERM[2],
            },
            "variableHighBits": f"0x{VARIABLE_HIGH_BITS:08x}",
            "variableLowBitCount": LOW_BIT_COUNT,
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
        "schema": "walle-reveal-agx-two-product-ruler-plan-manifest-v1",
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
        "skippedLowBits": skipped,
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
