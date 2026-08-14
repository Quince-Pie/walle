#!/usr/bin/env python3.14
"""Generate dense M1 probes for the rare AGX two-product setup interaction."""

import argparse
import hashlib
import json
import math
import struct
import sys
from pathlib import Path
from typing import Final

import numpy as np


ROOT: Final = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "analysis"), str(ROOT / "lg-test" / "Analysis")]

import analyze_reveal_agx_basis_phase as phase  # noqa: E402
import analyze_reveal_agx_join_preimage as preimage  # noqa: E402
import analyze_reveal_agx_setup_accumulator as accumulator  # noqa: E402
import analyze_reveal_agx_setup_tile_sweep as sweep  # noqa: E402


type JsonObject = dict[str, object]
type Vertex = tuple[float, ...]

OUTPUT_DEFAULT: Final = (
    ROOT / "build" / "analysis-agx-basis" / "two-product-tomography-plan-v1"
)
ULP_OFFSETS: Final = (
    -256,
    -192,
    -160,
    -128,
    -96,
    -64,
    -48,
    -32,
    -24,
    -16,
    -12,
    -8,
    -6,
    -4,
    -3,
    -2,
    -1,
    0,
    1,
    2,
    3,
    4,
    6,
    8,
    12,
    16,
    24,
    32,
    48,
    64,
    128,
    256,
)
ANCHOR_COUNT: Final = 4
ANCHOR_PROBE_OFFSETS: Final = (
    -512,
    -384,
    -256,
    -192,
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
    192,
    256,
    384,
    512,
)
VERTEX: Final = struct.Struct("<8I")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(4 * 1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _ordered_key(bits: int) -> int:
    return (~bits & 0xFFFF_FFFF) if bits & 0x8000_0000 else bits | 0x8000_0000


def _bits_from_ordered_key(key: int) -> int:
    if not 0 <= key <= 0xFFFF_FFFF:
        raise ValueError("perturbed binary32 key escaped uint32")
    return (~key & 0xFFFF_FFFF) if key < 0x8000_0000 else key & 0x7FFF_FFFF


def _perturb(value: float, offset: int) -> float:
    bits = accumulator.setup._float_bits(value)  # noqa: SLF001
    result = accumulator.setup._float32(  # noqa: SLF001
        phase._float(_bits_from_ordered_key(_ordered_key(bits) + offset))  # noqa: SLF001
    )
    if not math.isfinite(result):
        raise ValueError("ULP perturbation generated a non-finite value")
    return result


def _evenly_spaced[T](values: list[T], count: int) -> tuple[T, ...]:
    if len(values) < count:
        raise ValueError("not enough anchor-preserving candidates")
    indices = tuple(
        round(index * (len(values) - 1) / (count - 1)) for index in range(count)
    )
    if len(set(indices)) != count:
        raise ValueError("anchor candidate selection contains duplicates")
    return tuple(values[index] for index in indices)


def _with_lane_values(
    positions: tuple[tuple[float, float], ...], lane_values: list[tuple[float, ...]]
) -> tuple[Vertex, ...]:
    return tuple(
        position + tuple(values[vertex_index] for values in lane_values)
        for vertex_index, position in enumerate(positions)
    )


def _base_cases() -> list[JsonObject]:
    plans = sweep._load_plans()  # noqa: SLF001
    captures = sweep._load_captures(plans)  # noqa: SLF001
    bitmap = accumulator.setup.P25_PATH.read_bytes()
    cases: list[JsonObject] = []
    for batch, (plan, words) in enumerate(zip(plans, captures, strict=True)):
        vertex_path = (
            sweep.PLAN_ROOT
            / f"batch-{batch}"
            / "reveal-agx-setup-accumulator-vertices.bin"
        )
        vertex_words = np.fromfile(vertex_path, dtype="<u4").reshape(-1, 3, 8)
        for draw_value in sweep._require_list(plan.get("draws"), "draws"):  # noqa: SLF001
            draw = sweep._require_dict(draw_value, "draw")  # noqa: SLF001
            record = sweep._require_int(draw.get("recordIndex"), "record")  # noqa: SLF001
            vertices = sweep._vertices(vertex_words, record)  # noqa: SLF001
            tile = (
                sweep._require_int(draw.get("tileX"), "tile x"),  # noqa: SLF001
                sweep._require_int(draw.get("tileY"), "tile y"),  # noqa: SLF001
            )
            for component, triple in enumerate(
                accumulator._triples(words[record])  # noqa: SLF001
            ):
                predicted = sweep._shared_reciprocal_constant_bits(  # noqa: SLF001
                    vertices,
                    component,
                    tile,
                    bitmap,
                    join_precision=28,
                    reciprocal_truncation=20,
                )
                delta = accumulator.export._float_ulp_delta(  # noqa: SLF001
                    triple[2], predicted
                )
                if delta == 0:
                    continue
                positions = accumulator.setup._fixed_positions(vertices)  # noqa: SLF001
                anchor = accumulator.top_left._top_left(positions)  # noqa: SLF001
                cases.append(
                    {
                        "sourceBatch": batch,
                        "sourceRecordIndex": record,
                        "targetIndex": draw["targetIndex"],
                        "targetRecordIndex": draw["targetRecordIndex"],
                        "patternIndex": draw["patternIndex"],
                        "component": component,
                        "pixel": [draw["x"], draw["y"]],
                        "tile": list(tile),
                        "positions": [list(vertex[:2]) for vertex in vertices],
                        "values": [vertex[2 + component] for vertex in vertices],
                        "anchorVertex": anchor,
                        "sourceActualBits": f"0x{triple[2]:08x}",
                        "sourcePredictedBits": f"0x{predicted:08x}",
                        "sourceActualMinusPredictedFloatUlps": delta,
                    }
                )
    if len(cases) != 24:
        raise ValueError("wide-tile residual census differs")
    return cases


def _tomography_lanes(
    case: JsonObject,
    first_offset: int,
    second_offset: int,
    bitmap: bytes,
) -> tuple[list[tuple[float, ...]], JsonObject] | None:
    positions_value = case["positions"]
    values_value = case["values"]
    if not isinstance(positions_value, list) or not isinstance(values_value, list):
        raise ValueError("base-case geometry differs")
    positions = tuple(tuple(float(item) for item in row) for row in positions_value)
    values = [accumulator.setup._float32(float(value)) for value in values_value]  # noqa: SLF001
    anchor = int(case["anchorVertex"])
    nonanchors = [index for index in range(3) if index != anchor]
    values[nonanchors[0]] = _perturb(values[nonanchors[0]], first_offset)
    values[nonanchors[1]] = _perturb(values[nonanchors[1]], second_offset)
    differences = [
        accumulator.setup._float32(value - values[anchor])  # noqa: SLF001
        for value in values
    ]
    zero_vertices = tuple(
        position + (differences[index], 0.0, 0.0, 0.0)
        for index, position in enumerate(positions)
    )
    tile = tuple(int(value) for value in case["tile"])  # type: ignore[arg-type]
    try:
        zero_anchor, determinant, terms = preimage._middle_terms(  # noqa: SLF001
            zero_vertices, 0, tile
        )
        sign, index, exponent = preimage._joined_index(terms)  # noqa: SLF001
    except ValueError:
        return None
    if zero_anchor != 0 or len(terms) != 2 or terms[0][0] == terms[1][0] or sign == 0:
        return None
    selector, selector_exponent = accumulator.setup._p25_selector(  # noqa: SLF001
        determinant, bitmap
    )
    zero_coefficient = preimage._constant_from_join(  # noqa: SLF001
        0, sign, index, exponent, selector, selector_exponent
    )
    if zero_coefficient is None:
        return None
    cancellation_anchor = accumulator.setup._float32(  # noqa: SLF001
        float(-accumulator.export._fraction(zero_coefficient))  # noqa: SLF001
    )
    center_key = _ordered_key(accumulator.setup._float_bits(cancellation_anchor))  # noqa: SLF001
    candidates: list[tuple[int, int, tuple[float, ...]]] = []
    for anchor_offset in ANCHOR_PROBE_OFFSETS:
        key = center_key + anchor_offset
        if not 0 <= key <= 0xFFFF_FFFF:
            continue
        common_anchor = accumulator.setup._float32(  # noqa: SLF001
            phase._float(_bits_from_ordered_key(key))  # noqa: SLF001
        )
        submitted_values = tuple(
            accumulator.setup._float32(common_anchor + difference)  # noqa: SLF001
            for difference in differences
        )
        submitted = tuple(
            position + (submitted_values[vertex_index], 0.0, 0.0, 0.0)
            for vertex_index, position in enumerate(positions)
        )
        try:
            actual_anchor, actual_determinant, actual_terms = preimage._middle_terms(  # noqa: SLF001
                submitted, 0, tile
            )
        except ValueError:
            continue
        if actual_determinant == determinant and actual_terms == terms:
            candidates.append((anchor_offset, actual_anchor, submitted_values))
    if len(candidates) < ANCHOR_COUNT:
        return None
    selected = _evenly_spaced(candidates, ANCHOR_COUNT)
    lane_values = [candidate[2] for candidate in selected]
    semantic = json.dumps(
        {
            "case": case["sourceRecordIndex"],
            "component": case["component"],
            "tile": case["tile"],
            "first": first_offset,
            "second": second_offset,
            "terms": terms,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    holdout = hashlib.sha256(semantic).digest()[0] < 64
    return lane_values, {
        "firstNonanchorUlpOffset": first_offset,
        "secondNonanchorUlpOffset": second_offset,
        "middleTerms": [
            {"sign": term_sign, "index": term_index, "exponent": term_exponent}
            for term_sign, term_index, term_exponent in terms
        ],
        "predictedJoin": {"sign": sign, "index": index, "exponent": exponent},
        "anchorCandidateCount": len(candidates),
        "anchors": [
            {"anchorUlpOffset": offset, "anchorBits": f"0x{bits:08x}"}
            for offset, bits, _values in selected
        ],
        "split": "holdout" if holdout else "discovery",
    }


def generate(output_directory: Path) -> JsonObject:
    if output_directory.exists():
        raise FileExistsError(output_directory)
    bitmap = accumulator.setup.P25_PATH.read_bytes()
    cases = _base_cases()
    vertices = bytearray()
    draws: list[JsonObject] = []
    experiments: list[JsonObject] = []
    skipped = 0
    split_counts = {"discovery": 0, "holdout": 0}

    for case_index, case in enumerate(cases):
        positions = tuple(
            tuple(float(item) for item in row)
            for row in case["positions"]  # type: ignore[union-attr]
        )
        for first_offset in ULP_OFFSETS:
            for second_offset in ULP_OFFSETS:
                result = _tomography_lanes(case, first_offset, second_offset, bitmap)
                if result is None:
                    skipped += 1
                    continue
                lane_values, metadata = result
                record = len(draws)
                submitted = _with_lane_values(positions, lane_values)
                for vertex in submitted:
                    vertices.extend(
                        VERTEX.pack(
                            accumulator.setup._float_bits(vertex[0]),  # noqa: SLF001
                            accumulator.setup._float_bits(vertex[1]),  # noqa: SLF001
                            0,
                            0,
                            *(
                                accumulator.setup._float_bits(value)
                                for value in vertex[2:]
                            ),  # noqa: SLF001
                        )
                    )
                split = str(metadata["split"])
                split_counts[split] += 1
                experiments.append(
                    {
                        "recordIndex": record,
                        "caseIndex": case_index,
                        **metadata,
                    }
                )
                pixel = case["pixel"]
                tile = case["tile"]
                draws.append(
                    {
                        "recordIndex": record,
                        "targetIndex": int(case["targetIndex"]),
                        "targetRecordIndex": int(case["targetRecordIndex"]),
                        "sampleRecordIndex": int(case["sourceRecordIndex"]),
                        "sampleOrdinal": 0,
                        "patternIndex": record,
                        "x": pixel[0],  # type: ignore[index]
                        "y": pixel[1],  # type: ignore[index]
                        "tileX": tile[0],  # type: ignore[index]
                        "tileY": tile[1],  # type: ignore[index]
                    }
                )

    output_directory.mkdir(parents=True)
    vertex_path = output_directory / "reveal-agx-setup-accumulator-vertices.bin"
    vertex_path.write_bytes(vertices)
    plan_path = output_directory / "reveal-agx-setup-accumulator-plan.json"
    plan: JsonObject = {
        "schema": "walle-reveal-agx-setup-accumulator-plan-v1",
        "authority": {
            "opensReferencePixels": False,
            "usesOutputFeedback": False,
            "usesKnownWideTileResidualInputs": True,
            "establishesTwoProductInteractionLaw": False,
        },
        "target": {"width": 2_048, "height": 2_048},
        "vertexData": {
            "file": vertex_path.name,
            "bytes": len(vertices),
            "sha256": _sha256(vertex_path),
            "recordCount": len(draws),
            "verticesPerRecord": 3,
            "wordsPerVertex": 8,
            "layout": "positionXY,pad2,varyingRGBA; little-endian uint32",
        },
        "offsets": list(ULP_OFFSETS),
        "cases": [
            {
                key: value
                for key, value in case.items()
                if key not in {"positions", "values"}
            }
            for case in cases
        ],
        "experiments": experiments,
        "draws": draws,
        "census": {
            "targetCount": 8,
            "baseResidualCaseCount": len(cases),
            "offsetCountPerNonanchor": len(ULP_OFFSETS),
            "candidatePairCount": len(cases) * len(ULP_OFFSETS) ** 2,
            "skippedPairCount": skipped,
            "patternCount": len(draws),
            "drawCount": len(draws),
            "coefficientTripleCount": len(draws) * 4,
            "discoveryPatternCount": split_counts["discovery"],
            "holdoutPatternCount": split_counts["holdout"],
        },
    }
    plan_path.write_text(
        json.dumps(plan, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    manifest: JsonObject = {
        "schema": "walle-reveal-agx-two-product-tomography-plan-manifest-v1",
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
        "census": plan["census"],
    }
    (output_directory / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=OUTPUT_DEFAULT)
    arguments = parser.parse_args()
    manifest = generate(arguments.output)
    print(json.dumps(manifest["census"], indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
