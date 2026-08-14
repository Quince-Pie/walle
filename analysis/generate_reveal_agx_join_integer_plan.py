#!/usr/bin/env python3.14
"""Generate common-anchor probes that expose each residual AGX join integer."""

import argparse
import hashlib
import json
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

OUTPUT_DEFAULT: Final = ROOT / "build" / "analysis-agx-basis" / "join-integer-plan-v1"
ANCHOR_SEARCH_RADIUS: Final = 256
ANCHOR_COUNT_PER_CASE: Final = 16
VERTEX_WORD_COUNT: Final = 8


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


def _evenly_spaced[T](values: list[T], count: int) -> tuple[T, ...]:
    if len(values) < count:
        raise ValueError("not enough anchor-preserving candidates")
    indices = tuple(
        round(index * (len(values) - 1) / (count - 1)) for index in range(count)
    )
    if len(set(indices)) != count:
        raise ValueError("anchor candidate selection contains duplicates")
    return tuple(values[index] for index in indices)


def _residual_cases() -> list[JsonObject]:
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
                anchor_bits, determinant, terms = preimage._middle_terms(  # noqa: SLF001
                    vertices, component, tile
                )
                sign, join_index, join_exponent = preimage._joined_index(terms)  # noqa: SLF001
                selector, selector_exponent = accumulator.setup._p25_selector(  # noqa: SLF001
                    determinant, bitmap
                )
                zero_anchor_coefficient = preimage._constant_from_join(  # noqa: SLF001
                    0,
                    sign,
                    join_index,
                    join_exponent,
                    selector,
                    selector_exponent,
                )
                if zero_anchor_coefficient is None:
                    raise ValueError("baseline coefficient escaped the domain")
                cancellation_anchor = accumulator.setup._float32(  # noqa: SLF001
                    float(-accumulator.export._fraction(zero_anchor_coefficient))  # noqa: SLF001
                )
                anchor_vertex = accumulator.top_left._top_left(  # noqa: SLF001
                    accumulator.setup._fixed_positions(vertices)  # noqa: SLF001
                )
                original_values = [
                    accumulator.setup._float32(vertex[2 + component])  # noqa: SLF001
                    for vertex in vertices
                ]
                differences = [
                    accumulator.setup._float32(  # noqa: SLF001
                        value - original_values[anchor_vertex]
                    )
                    for value in original_values
                ]
                key = _ordered_key(accumulator.setup._float_bits(cancellation_anchor))  # noqa: SLF001
                candidates: list[JsonObject] = []
                for offset in range(-ANCHOR_SEARCH_RADIUS, ANCHOR_SEARCH_RADIUS + 1):
                    candidate_key = key + offset
                    if not 0 <= candidate_key <= 0xFFFF_FFFF:
                        continue
                    candidate_anchor = accumulator.setup._float32(  # noqa: SLF001
                        phase._float(_bits_from_ordered_key(candidate_key))  # noqa: SLF001
                    )
                    values = tuple(
                        accumulator.setup._float32(candidate_anchor + difference)  # noqa: SLF001
                        for difference in differences
                    )
                    submitted = tuple(
                        tuple(vertex[:2]) + (values[index], 0.0, 0.0, 0.0)
                        for index, vertex in enumerate(vertices)
                    )
                    submitted_anchor, submitted_determinant, submitted_terms = (
                        preimage._middle_terms(submitted, 0, tile)  # noqa: SLF001
                    )
                    if submitted_determinant != determinant or submitted_terms != terms:
                        continue
                    candidates.append(
                        {
                            "anchorUlpOffset": offset,
                            "anchorBits": f"0x{submitted_anchor:08x}",
                            "values": values,
                        }
                    )
                selected = _evenly_spaced(candidates, ANCHOR_COUNT_PER_CASE)
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
                        "predictedJoin": {
                            "sign": sign,
                            "index": join_index,
                            "exponent": join_exponent,
                        },
                        "sourceActualBits": f"0x{triple[2]:08x}",
                        "sourcePredictedBits": f"0x{predicted:08x}",
                        "sourceActualMinusPredictedFloatUlps": delta,
                        "anchorCandidateCount": len(candidates),
                        "anchors": selected,
                    }
                )
    if len(cases) != 24:
        raise ValueError("wide-tile residual census differs")
    return cases


def generate(output_directory: Path) -> JsonObject:
    if output_directory.exists():
        raise FileExistsError(output_directory)
    cases = _residual_cases()
    vertices = bytearray()
    draws: list[JsonObject] = []
    experiments: list[JsonObject] = []
    for case_index, case in enumerate(cases):
        anchors = case["anchors"]
        positions = case["positions"]
        if not isinstance(anchors, tuple) or not isinstance(positions, list):
            raise ValueError("case shape differs")
        for group_index in range(0, len(anchors), 4):
            group = anchors[group_index : group_index + 4]
            if len(group) != 4:
                raise ValueError("anchor group does not fill four lanes")
            record_index = len(draws)
            for vertex_index in range(3):
                position = positions[vertex_index]
                vertices.extend(
                    b"".join(
                        value.to_bytes(4, "little")
                        for value in (
                            accumulator.setup._float_bits(position[0]),  # noqa: SLF001
                            accumulator.setup._float_bits(position[1]),  # noqa: SLF001
                            0,
                            0,
                            *(
                                accumulator.setup._float_bits(
                                    anchor["values"][vertex_index]
                                )  # noqa: SLF001
                                for anchor in group
                            ),
                        )
                    )
                )
            pixel = case["pixel"]
            tile = case["tile"]
            experiments.append(
                {
                    "recordIndex": record_index,
                    "caseIndex": case_index,
                    "anchorGroupIndex": group_index // 4,
                    "anchors": [
                        {key: value for key, value in anchor.items() if key != "values"}
                        for anchor in group
                    ],
                }
            )
            draws.append(
                {
                    "recordIndex": record_index,
                    "targetIndex": int(case["targetIndex"]),
                    "targetRecordIndex": int(case["targetRecordIndex"]),
                    "sampleRecordIndex": int(case["sourceRecordIndex"]),
                    "sampleOrdinal": 0,
                    "patternIndex": record_index,
                    "x": pixel[0],
                    "y": pixel[1],
                    "tileX": tile[0],
                    "tileY": tile[1],
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
            "usesM1CoefficientResiduals": True,
            "establishesJoinInteger": False,
        },
        "target": {"width": 2_048, "height": 2_048},
        "vertexData": {
            "file": vertex_path.name,
            "bytes": len(vertices),
            "sha256": _sha256(vertex_path),
            "recordCount": len(draws),
            "verticesPerRecord": 3,
            "wordsPerVertex": VERTEX_WORD_COUNT,
            "layout": "positionXY,pad2,varyingRGBA; little-endian uint32",
        },
        "cases": [
            {
                key: value
                for key, value in case.items()
                if key not in {"anchors", "positions"}
            }
            for case in cases
        ],
        "experiments": experiments,
        "draws": draws,
        "census": {
            "targetCount": 8,
            "residualCaseCount": len(cases),
            "anchorCountPerCase": ANCHOR_COUNT_PER_CASE,
            "patternCount": len(draws),
            "drawCount": len(draws),
            "coefficientTripleCount": len(draws) * 4,
        },
    }
    plan_path.write_text(
        json.dumps(plan, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    manifest: JsonObject = {
        "schema": "walle-reveal-agx-join-integer-plan-manifest-v1",
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
