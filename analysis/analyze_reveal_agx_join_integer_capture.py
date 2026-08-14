#!/usr/bin/env python3.14
"""Intersect M1 coefficient preimages across common-anchor join probes."""

import argparse
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Final

import numpy as np


ROOT: Final = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "analysis"), str(ROOT / "lg-test" / "Analysis")]

import analyze_reveal_agx_join_preimage as preimage  # noqa: E402
import analyze_reveal_agx_setup_accumulator as accumulator  # noqa: E402
import analyze_reveal_agx_setup_tile_sweep as sweep  # noqa: E402


type JsonObject = dict[str, object]

PLAN_ROOT: Final = ROOT / "build" / "analysis-agx-basis" / "join-integer-plan-v1"
CAPTURE_ROOT: Final = ROOT / "build" / "analysis-agx-basis" / "macos-join-integer-v1"
PLAN: Final = CAPTURE_ROOT / "reveal-agx-setup-accumulator-plan.json"
VERTICES: Final = CAPTURE_ROOT / "reveal-agx-setup-accumulator-vertices.bin"
RAW: Final = CAPTURE_ROOT / "capture" / "reveal-agx-setup-accumulator.raw"
CAPTURE_MANIFEST: Final = CAPTURE_ROOT / "capture" / "manifest.json"
STDERR: Final = CAPTURE_ROOT / "capture.stderr"
STDOUT: Final = CAPTURE_ROOT / "capture.stdout"
OUTPUT: Final = (
    ROOT / "build" / "analysis-agx-basis" / "join-integer-analysis" / "result.json"
)
DRAW_COUNT: Final = 96
CASE_COUNT: Final = 24
ANCHOR_COUNT_PER_CASE: Final = 16
COEFFICIENT_TRIPLE_COUNT: Final = 384
RECORD_VECTOR_COUNT: Final = 101
RECORD_WORD_COUNT: Final = RECORD_VECTOR_COUNT * 4

EXPECTED: Final = {
    PLAN_ROOT
    / "manifest.json": "12b461dab7a21f306dabfd49da2d5383712b92312f8567e62d9da4be5d0c0b9b",
    PLAN: "cbcc4af6e60dc576225db827103b2a67c118943ea334e8d7b4c45d027eb3fef1",
    VERTICES: "2117168a59536f2f1152cd6239e1f0e5ca7c23d512067136fadbcdf888b01ad3",
    RAW: "f443ecedec0ed10575ebf99a216e48b6e7557f5770a1e75f9d5e51d4b15afbbe",
    CAPTURE_MANIFEST: "b6c3ee91b093166ded86fa5093d716b24bf93e7c8582080ad253534f54e9b823",
    STDERR: "6b76f08cede7750bcc97ffc33d5ba3a0e83368daeaf50b2a43b917e0454356fb",
    STDOUT: "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(4 * 1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _identity(path: Path) -> JsonObject:
    return {
        "path": path.relative_to(ROOT).as_posix(),
        "bytes": path.stat().st_size,
        "sha256": _sha256(path),
    }


def _load() -> tuple[JsonObject, np.ndarray, np.ndarray]:
    for path, expected in EXPECTED.items():
        if _sha256(path) != expected:
            raise ValueError(f"SHA-256 differs: {path.relative_to(ROOT)}")
    plan = json.loads(PLAN.read_text(encoding="utf-8"))
    manifest = json.loads(CAPTURE_MANIFEST.read_text(encoding="utf-8"))
    capture = manifest.get("capture")
    if (
        plan.get("schema") != "walle-reveal-agx-setup-accumulator-plan-v1"
        or plan.get("census")
        != {
            "anchorCountPerCase": ANCHOR_COUNT_PER_CASE,
            "coefficientTripleCount": COEFFICIENT_TRIPLE_COUNT,
            "drawCount": DRAW_COUNT,
            "patternCount": DRAW_COUNT,
            "residualCaseCount": CASE_COUNT,
            "targetCount": 8,
        }
        or manifest.get("schema") != "walle-reveal-agx-setup-accumulator-capture-v1"
        or not isinstance(capture, dict)
        or capture.get("recordCount") != DRAW_COUNT
        or capture.get("recordVectorCount") != RECORD_VECTOR_COUNT
        or capture.get("sha256") != EXPECTED[RAW]
        or STDOUT.stat().st_size != 0
    ):
        raise ValueError("join-integer capture closure differs")
    trace = STDERR.read_text(encoding="utf-8")
    if re.findall(
        r"^AGX_IO coefficient export patched handle=(\d+) shader=0x([0-9a-f]+)$",
        trace,
        flags=re.MULTILINE,
    ) != [("1", "28c0")] or re.findall(
        r"^AGX_IO coefficient export matches=(\d+) applied=(\d+)$",
        trace,
        flags=re.MULTILINE,
    ) != [("1", "1")]:
        raise ValueError("coefficient-export patch census differs")
    words = np.fromfile(RAW, dtype="<u4")
    vertices = np.fromfile(VERTICES, dtype="<u4")
    if words.size != DRAW_COUNT * RECORD_WORD_COUNT:
        raise ValueError("join-integer capture word count differs")
    if vertices.size != DRAW_COUNT * 3 * 8:
        raise ValueError("join-integer vertex word count differs")
    return (
        plan,
        words.reshape(DRAW_COUNT, RECORD_VECTOR_COUNT, 4),
        vertices.reshape(DRAW_COUNT, 3, 8),
    )


def analyze() -> JsonObject:
    plan, words, vertex_words = _load()
    cases = sweep._require_list(plan.get("cases"), "cases")  # noqa: SLF001
    experiments = sweep._require_list(plan.get("experiments"), "experiments")  # noqa: SLF001
    draws = sweep._require_list(plan.get("draws"), "draws")  # noqa: SLF001
    if len(cases) != CASE_COUNT or len(experiments) != DRAW_COUNT:
        raise ValueError("join-integer plan census differs")
    bitmap = accumulator.setup.P25_PATH.read_bytes()

    offsets_by_case: dict[int, list[set[int]]] = defaultdict(list)
    anchor_offsets_by_case: dict[int, list[int]] = defaultdict(list)
    slope_mismatch_count = 0
    observed_constant_count = 0

    for experiment_value, draw_value in zip(experiments, draws, strict=True):
        experiment = sweep._require_dict(experiment_value, "experiment")  # noqa: SLF001
        draw = sweep._require_dict(draw_value, "draw")  # noqa: SLF001
        record = sweep._require_int(draw.get("recordIndex"), "record")  # noqa: SLF001
        case_index = sweep._require_int(experiment.get("caseIndex"), "case")  # noqa: SLF001
        if experiment.get("recordIndex") != record or not 0 <= case_index < CASE_COUNT:
            raise ValueError("join-integer experiment join differs")
        anchors = sweep._require_list(experiment.get("anchors"), "anchors")  # noqa: SLF001
        if len(anchors) != 4:
            raise ValueError("join-integer experiment does not fill four lanes")
        vertices = sweep._vertices(vertex_words, record)  # noqa: SLF001
        positions = accumulator.setup._fixed_positions(vertices)  # noqa: SLF001
        top_left = accumulator.top_left._top_left(positions)  # noqa: SLF001
        tile = (
            sweep._require_int(draw.get("tileX"), "tile x"),  # noqa: SLF001
            sweep._require_int(draw.get("tileY"), "tile y"),  # noqa: SLF001
        )
        case = sweep._require_dict(cases[case_index], "case")  # noqa: SLF001
        predicted_join = sweep._require_dict(  # noqa: SLF001
            case.get("predictedJoin"), "predicted join"
        )

        for component, (anchor_value, triple) in enumerate(
            zip(anchors, accumulator._triples(words[record]), strict=True)  # noqa: SLF001
        ):
            anchor = sweep._require_dict(anchor_value, "anchor")  # noqa: SLF001
            anchor_bits, determinant, terms = preimage._middle_terms(  # noqa: SLF001
                vertices, component, tile
            )
            sign, index, exponent = preimage._joined_index(terms)  # noqa: SLF001
            if (
                len(terms) != 2
                or terms[0][0] == terms[1][0]
                or f"0x{anchor_bits:08x}" != anchor.get("anchorBits")
                or sign != predicted_join.get("sign")
                or index != predicted_join.get("index")
                or exponent != predicted_join.get("exponent")
            ):
                raise ValueError("join-integer preimage differs")
            for axis, actual in enumerate(triple[:2]):
                predicted_slope = accumulator.top_left._anchor_slope(  # noqa: SLF001
                    vertices, component, axis, bitmap, top_left
                )
                slope_mismatch_count += predicted_slope != actual
            selector, selector_exponent = accumulator.setup._p25_selector(  # noqa: SLF001
                determinant, bitmap
            )
            compatible = preimage._compatible_offsets(  # noqa: SLF001
                triple[2],
                anchor_bits,
                sign,
                index,
                exponent,
                selector,
                selector_exponent,
            )
            if not compatible:
                raise ValueError("join integer escaped bounded preimage search")
            offsets_by_case[case_index].append(set(compatible))
            anchor_offsets_by_case[case_index].append(
                sweep._require_int(anchor.get("anchorUlpOffset"), "anchor offset")  # noqa: SLF001
            )
            observed_constant_count += 1

    records: list[JsonObject] = []
    width_histogram = Counter[int]()
    unique_offset_histogram = Counter[int]()
    for case_index, case_value in enumerate(cases):
        case = sweep._require_dict(case_value, "case")  # noqa: SLF001
        preimages = offsets_by_case[case_index]
        if len(preimages) != ANCHOR_COUNT_PER_CASE:
            raise ValueError("join-integer anchor census differs")
        intersection = set.intersection(*preimages)
        if not intersection:
            raise ValueError("common-anchor preimage intersection is empty")
        offsets = sorted(intersection)
        if offsets != list(range(offsets[0], offsets[-1] + 1)):
            raise ValueError("common-anchor preimage is not contiguous")
        width_histogram[len(offsets)] += 1
        if len(offsets) == 1:
            unique_offset_histogram[offsets[0]] += 1
        anchor_offsets = anchor_offsets_by_case[case_index]
        records.append(
            {
                "caseIndex": case_index,
                "targetIndex": case["targetIndex"],
                "patternIndex": case["patternIndex"],
                "component": case["component"],
                "tile": case["tile"],
                "sourceActualMinusPredictedFloatUlps": case[
                    "sourceActualMinusPredictedFloatUlps"
                ],
                "anchorCandidateCount": case["anchorCandidateCount"],
                "capturedAnchorUlpOffset": {
                    "minimum": min(anchor_offsets),
                    "maximum": max(anchor_offsets),
                    "count": len(anchor_offsets),
                },
                "compatibleJoinOffsetIntersection": {
                    "minimum": offsets[0],
                    "maximum": offsets[-1],
                    "count": len(offsets),
                    "values": offsets,
                },
            }
        )

    if slope_mismatch_count:
        raise ValueError("join-integer capture contains an inexact slope")
    return {
        "schema": "walle-reveal-agx-join-integer-analysis-v1",
        "classification": "output-blind common-anchor p28 join preimage intersection",
        "authority": {
            "readsReferencePixels": False,
            "usesM1CoefficientExports": True,
            "usesKnownWideTileResidualInputs": True,
            "establishesAllJoinIntegersUniquely": False,
            "authorizesProductionMutation": False,
        },
        "inputs": {
            "analyzer": _identity(Path(__file__).resolve()),
            "closure": [_identity(path) for path in EXPECTED],
        },
        "census": {
            "caseCount": CASE_COUNT,
            "capturedAnchorCount": observed_constant_count,
            "slopeWordCount": observed_constant_count * 2,
            "slopeMismatchCount": slope_mismatch_count,
            "intersectionWidthHistogram": {
                str(width): count for width, count in sorted(width_histogram.items())
            },
            "uniqueCaseCount": width_histogram[1],
            "uniqueOffsetHistogram": {
                str(offset): count
                for offset, count in sorted(unique_offset_histogram.items())
            },
        },
        "records": records,
        "conclusion": (
            "Varying the common anchor without changing either middle product narrows "
            "each of the 24 wide-tile residuals to a contiguous one-to-three-unit "
            "p28 join interval. One case uniquely requires offset +2; the other 23 "
            "remain quantization intervals. The probe exposes the interaction result "
            "more sharply but does not yet identify the generating bit law."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=OUTPUT)
    arguments = parser.parse_args()
    result = analyze()
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(result["census"], indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
