#!/usr/bin/env python3.14
"""Invert the M1 AGX two-product power-of-two-ruler coefficient capture."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from fractions import Fraction
from pathlib import Path
from typing import Final

import numpy as np

import analyze_reveal_agx_join_preimage as preimage
import analyze_reveal_agx_setup_accumulator as accumulator
import analyze_reveal_agx_setup_tile_sweep as sweep
import analyze_reveal_agx_signed_fused_accumulator as fused
import analyze_reveal_agx_two_product_tomography as tomography


type JsonObject = dict[str, object]
type Product = tuple[int, int, int, int]
type SignedValue = tuple[int, int, int]

ROOT: Final = Path(__file__).resolve().parent.parent
PLAN_ROOT: Final = ROOT / "build" / "analysis-agx-basis" / "two-product-ruler-plan-v2"
CAPTURE_ROOT: Final = (
    ROOT / "build" / "analysis-agx-basis" / "macos-two-product-ruler-v1"
)
PLAN: Final = CAPTURE_ROOT / "reveal-agx-setup-accumulator-plan.json"
VERTICES: Final = CAPTURE_ROOT / "reveal-agx-setup-accumulator-vertices.bin"
RAW: Final = CAPTURE_ROOT / "capture" / "reveal-agx-setup-accumulator.raw"
CAPTURE_MANIFEST: Final = CAPTURE_ROOT / "capture" / "manifest.json"
STDERR: Final = CAPTURE_ROOT / "capture.stderr"
STDOUT: Final = CAPTURE_ROOT / "capture.stdout"
INTERPOSER: Final = CAPTURE_ROOT / "libwalle-agx-ldcf-export.dylib"
EXECUTABLE: Final = CAPTURE_ROOT / "reveal-agx-setup-accumulator-probe"
OUTPUT: Final = (
    ROOT / "build" / "analysis-agx-basis" / "two-product-ruler-analysis" / "result.json"
)
DRAW_COUNT: Final = 8_009
VARIABLE_FIELD: Final = "variableMantissaLowBits"
EXPECTED_CENSUS: Final = {
    "candidateCount": 8_192,
    "skippedCount": 183,
    "discoveryPatternCount": 5_986,
    "holdoutPatternCount": 2_023,
}
RECORD_VECTOR_COUNT: Final = 101
RECORD_WORD_COUNT: Final = RECORD_VECTOR_COUNT * 4

EXPECTED: Final = {
    PLAN_ROOT
    / "manifest.json": "e4b6be3cc08428a4196e51279c48d1befd18b9fb89632bd405ec9faa19f9f97f",
    PLAN: "3c739f4f33cc0e161b2a94f737588287a141ac9dd2da23c6ee3a8e079d242d16",
    VERTICES: "57e46fa91cd5392a7254e8d856538d07449adc6a45d393785fbaab600a48eab9",
    RAW: "bff22002ad078e8a3c3e2fed877ed2e341284b0e69f71b193d59342370c56c8f",
    CAPTURE_MANIFEST: "d61b3e0b409316797fecd1f2cfcf15e4a187c491315bdd9fee10d3c2a87e7fe6",
    STDERR: "23cc715f35258dd2cf45070b1906202ea9a39e47116d029559c3d66ed4f5b83a",
    STDOUT: "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    INTERPOSER: "9ead667be857c2fa3ed8a9b110d6d33edb24cf6d7ddf575427d17740e0ff1e8f",
    EXECUTABLE: "0eba15db7f872a845398f91cacce7446f9e92cbd22276da3e42e5b9148be4ea9",
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
        actual = _sha256(path)
        if actual != expected:
            raise ValueError(f"SHA-256 differs: {path.relative_to(ROOT)}: {actual}")
    plan = json.loads(PLAN.read_text(encoding="utf-8"))
    manifest = json.loads(CAPTURE_MANIFEST.read_text(encoding="utf-8"))
    census = plan.get("census")
    capture = manifest.get("capture")
    executable = manifest.get("executable")
    if (
        plan.get("schema") != "walle-reveal-agx-setup-accumulator-plan-v1"
        or not isinstance(census, dict)
        or census.get("drawCount") != DRAW_COUNT
        or census.get("patternCount") != DRAW_COUNT
        or census.get("coefficientTripleCount") != DRAW_COUNT * 4
        or census.get("candidateCount") != EXPECTED_CENSUS["candidateCount"]
        or census.get("skippedCount") != EXPECTED_CENSUS["skippedCount"]
        or census.get("discoveryPatternCount")
        != EXPECTED_CENSUS["discoveryPatternCount"]
        or census.get("holdoutPatternCount") != EXPECTED_CENSUS["holdoutPatternCount"]
        or manifest.get("schema") != "walle-reveal-agx-setup-accumulator-capture-v1"
        or not isinstance(capture, dict)
        or capture.get("recordCount") != DRAW_COUNT
        or capture.get("recordVectorCount") != RECORD_VECTOR_COUNT
        or capture.get("sha256") != EXPECTED[RAW]
        or not isinstance(executable, dict)
        or executable.get("sha256") != EXPECTED[EXECUTABLE]
        or STDOUT.stat().st_size != 0
    ):
        raise ValueError("two-product ruler closure differs")
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
        raise ValueError("two-product ruler capture word count differs")
    if vertices.size != DRAW_COUNT * 3 * 8:
        raise ValueError("two-product ruler vertex word count differs")
    adapted = json.loads(json.dumps(plan))
    for experiment in adapted["experiments"]:
        experiment["caseIndex"] = 0
        experiment["firstNonanchorUlpOffset"] = experiment[VARIABLE_FIELD]
        experiment["secondNonanchorUlpOffset"] = 0
    return (
        adapted,
        words.reshape(DRAW_COUNT, RECORD_VECTOR_COUNT, 4),
        vertices.reshape(DRAW_COUNT, 3, 8),
    )


def _power(exponent: int) -> Fraction:
    return accumulator._power_of_two(exponent)  # noqa: SLF001


def _offset_from_terms(
    terms: tuple[tuple[int, int, int], ...],
    baseline: tuple[int, int, int],
) -> int | None:
    joined = sum(
        (sign * index * _power(exponent) for sign, index, exponent in terms),
        start=Fraction(),
    )
    if joined == 0:
        return None
    sign, index, exponent = accumulator.setup._normalize_signed(  # noqa: SLF001
        joined, precision_bits=28, rounding="nearest-even"
    )
    baseline_sign, baseline_index, baseline_exponent = baseline
    if sign != baseline_sign:
        return None
    difference = sign * index * _power(
        exponent
    ) - baseline_sign * baseline_index * _power(baseline_exponent)
    offset = difference / (baseline_sign * _power(baseline_exponent))
    return int(offset) if offset.denominator == 1 else None


def _correction_candidates(record: JsonObject) -> tuple[int, ...]:
    terms_value = record.get("middleTerms")
    interval_value = record.get("compatibleJoinOffsetIntersection")
    if not isinstance(terms_value, list) or not isinstance(interval_value, dict):
        raise ValueError("ruler record terms or interval are absent")
    terms = tuple(
        (
            int(term["sign"]),
            int(term["index"]),
            int(term["exponent"]),
        )
        for term in terms_value
        if isinstance(term, dict)
    )
    baseline = preimage._joined_index(terms)  # noqa: SLF001
    values = interval_value.get("values")
    if not isinstance(values, list):
        raise ValueError("ruler record interval is absent")
    compatible = set(int(value) for value in values)
    corrections: list[int] = []
    for correction in range(-4, 5):
        adjusted = (terms[0], (terms[1][0], terms[1][1] + correction, terms[1][2]))
        offset = _offset_from_terms(adjusted, baseline)
        if offset is not None and offset in compatible:
            corrections.append(correction)
    return tuple(corrections)


def _product_fingerprint(product: Product) -> JsonObject:
    _sign, multiplicand, multiplier, _exponent = product
    exact = multiplicand * multiplier
    partial = accumulator.tile.partial_product_sum(multiplicand, multiplier, 19)
    aggregate_carry = (exact >> 19) - (partial >> 19)
    top_carry = accumulator.coefficient.propagated_discarded_carry(
        multiplicand, multiplier, 19, 1
    )
    return {
        "multiplicandLow19": multiplicand & ((1 << 19) - 1),
        "multiplierLow19": multiplier & ((1 << 19) - 1),
        "exactProductLow19": exact & ((1 << 19) - 1),
        "partialProductLow19": partial & ((1 << 19) - 1),
        "aggregateDiscardedCarry": aggregate_carry,
        "topDiscardedCarry": top_carry,
        "column19Bit": (exact >> 19) & 1,
        "column20Bit": (exact >> 20) & 1,
    }


def _slope_numerator(
    vertices: tuple[tuple[float, ...], ...],
    component: int,
    axis: int,
    anchor: int,
) -> tuple[SignedValue, tuple[JsonObject, ...]]:
    positions = accumulator.setup._fixed_positions(vertices)  # noqa: SLF001
    values = tuple(
        accumulator.setup._float32(vertex[2 + component])  # noqa: SLF001
        for vertex in vertices
    )
    edge_fixed = (
        (
            positions[1][1] - positions[2][1],
            positions[2][1] - positions[0][1],
            positions[0][1] - positions[1][1],
        )
        if axis == 0
        else (
            positions[2][0] - positions[1][0],
            positions[0][0] - positions[2][0],
            positions[1][0] - positions[0][0],
        )
    )
    terms: list[JsonObject] = []
    total = Fraction()
    for index in range(3):
        if index == anchor:
            continue
        left = accumulator.setup._float32(  # noqa: SLF001
            values[index] - values[anchor]
        )
        right = accumulator.setup._float32(edge_fixed[index] / 256.0)  # noqa: SLF001
        value = accumulator.setup._first_product(  # noqa: SLF001
            left,
            right,
            bias_units=15,
        )
        total += value
        sign, product_index, exponent = accumulator.setup._normalize_signed(  # noqa: SLF001
            value,
            precision_bits=27,
            rounding="nearest-even",
        )
        terms.append(
            {
                "vertex": index,
                "leftBits": f"0x{accumulator.setup._float_bits(left):08x}",  # noqa: SLF001
                "rightBits": f"0x{accumulator.setup._float_bits(right):08x}",  # noqa: SLF001
                "sign": sign,
                "index": product_index,
                "exponent": exponent,
            }
        )
    return (
        accumulator.setup._normalize_signed(  # noqa: SLF001
            total,
            precision_bits=27,
            rounding="nearest-even",
        ),
        tuple(terms),
    )


def _compatible_numerator_offsets(
    actual_bits: int,
    baseline: SignedValue,
    determinant: int,
    bitmap: bytes,
    *,
    radius: int = 32,
) -> set[int]:
    sign, index, exponent = baseline
    if sign == 0:
        return {0} if actual_bits == 0 else set()
    return {
        offset
        for offset in range(max(-radius, 1 - index), radius + 1)
        if accumulator.setup._reciprocal_product(  # noqa: SLF001
            (sign, index + offset, exponent), determinant, bitmap
        )
        == actual_bits
    }


def analyze() -> JsonObject:
    plan, words, vertex_words = _load()
    original_load = tomography._load  # noqa: SLF001
    original_draw_count = tomography.DRAW_COUNT
    tomography._load = _load  # type: ignore[method-assign]  # noqa: SLF001
    tomography.DRAW_COUNT = DRAW_COUNT
    try:
        base = tomography.analyze()
    finally:
        tomography._load = original_load  # type: ignore[method-assign]  # noqa: SLF001
        tomography.DRAW_COUNT = original_draw_count

    records_value = base.get("records")
    experiments = sweep._require_list(plan.get("experiments"), "experiments")  # noqa: SLF001
    draws = sweep._require_list(plan.get("draws"), "draws")  # noqa: SLF001
    if not isinstance(records_value, list):
        raise ValueError("ruler records are absent")
    correction_histogram = Counter[tuple[int, ...]]()
    slope_offset_histogram = Counter[tuple[int, ...]]()
    slope_unique_by_split = {"discovery": Counter[int](), "holdout": Counter[int]()}
    slope_empty_count = 0
    slope_ambiguous_count = 0
    slope_axis_count = 0
    bitmap = accumulator.setup.P25_PATH.read_bytes()
    unique_by_split = {"discovery": Counter[int](), "holdout": Counter[int]()}
    transitions: list[JsonObject] = []
    previous: tuple[int, ...] | None = None
    for record_value, experiment_value, draw_value in zip(
        records_value, experiments, draws, strict=True
    ):
        record = sweep._require_dict(record_value, "record")  # noqa: SLF001
        experiment = sweep._require_dict(experiment_value, "experiment")  # noqa: SLF001
        draw = sweep._require_dict(draw_value, "draw")  # noqa: SLF001
        corrections = _correction_candidates(record)
        correction_histogram[corrections] += 1
        split = str(experiment["split"])
        if len(corrections) == 1:
            unique_by_split[split][corrections[0]] += 1
        record_index = int(record["recordIndex"])
        vertices = sweep._vertices(vertex_words, record_index)  # noqa: SLF001
        positions = accumulator.setup._fixed_positions(vertices)  # noqa: SLF001
        determinant = accumulator.setup._determinant(positions)  # noqa: SLF001
        top_left = accumulator.top_left._top_left(positions)  # noqa: SLF001
        tile = (int(draw["tileX"]), int(draw["tileY"]))
        _anchor, _determinant, products = fused._product_streams(  # noqa: SLF001
            vertices, 0, tile
        )
        if len(products) != 2 or products[0][1:] == products[1][1:]:
            raise ValueError("ruler product streams differ")
        fingerprint = _product_fingerprint(products[1])
        record[VARIABLE_FIELD] = experiment[VARIABLE_FIELD]
        record["variableProduct"] = {
            "sign": products[1][0],
            "multiplicand": products[1][1],
            "multiplier": products[1][2],
            "exponent": products[1][3],
            **fingerprint,
        }
        record["variableTermCorrectionCandidates"] = list(corrections)
        triples = accumulator._triples(words[record_index])  # noqa: SLF001
        slope_axes: list[JsonObject] = []
        for axis in range(2):
            lane_intervals: list[set[int]] = []
            common_baseline: SignedValue | None = None
            common_terms: tuple[JsonObject, ...] | None = None
            actual_words: list[str] = []
            for component, triple in enumerate(triples):
                baseline, numerator_terms = _slope_numerator(
                    vertices,
                    component,
                    axis,
                    top_left,
                )
                if common_baseline is None:
                    common_baseline = baseline
                    common_terms = numerator_terms
                elif baseline != common_baseline or numerator_terms != common_terms:
                    raise ValueError("common anchors changed a slope numerator")
                compatible = _compatible_numerator_offsets(
                    int(triple[axis]),
                    baseline,
                    determinant,
                    bitmap,
                )
                lane_intervals.append(compatible)
                actual_words.append(f"0x{int(triple[axis]):08x}")
            intersection = set.intersection(*lane_intervals)
            offsets = sorted(intersection)
            slope_axis_count += 1
            slope_offset_histogram[tuple(offsets)] += 1
            if not offsets:
                slope_empty_count += 1
            elif len(offsets) == 1:
                slope_unique_by_split[split][offsets[0]] += 1
            else:
                slope_ambiguous_count += 1
            assert common_baseline is not None
            assert common_terms is not None
            slope_axes.append(
                {
                    "axis": axis,
                    "baselineNumerator": {
                        "sign": common_baseline[0],
                        "index": common_baseline[1],
                        "exponent": common_baseline[2],
                    },
                    "firstProducts": list(common_terms),
                    "actualSlopeWords": actual_words,
                    "compatibleNumeratorOffsets": offsets,
                }
            )
        record["slopeNumeratorAxes"] = slope_axes
        if previous is not None and corrections != previous:
            transitions.append(
                {
                    VARIABLE_FIELD: experiment[VARIABLE_FIELD],
                    "before": list(previous),
                    "after": list(corrections),
                    "fingerprint": fingerprint,
                }
            )
        previous = corrections

    base["schema"] = "walle-reveal-agx-two-product-ruler-analysis-v1"
    base["classification"] = (
        "output-blind M1 two-product reduction with an exact power-of-two ruler"
    )
    base["inputs"] = {
        "analyzer": _identity(Path(__file__).resolve()),
        "closure": [_identity(path) for path in EXPECTED],
    }
    base["rulerAnalysis"] = {
        "correctionCandidateHistogram": {
            ",".join(str(value) for value in key): count
            for key, count in sorted(correction_histogram.items())
        },
        "uniqueCorrectionHistogramBySplit": {
            split: {str(value): count for value, count in sorted(counts.items())}
            for split, counts in unique_by_split.items()
        },
        "transitionCount": len(transitions),
        "transitions": transitions,
        "slopeNumerator": {
            "axisCount": slope_axis_count,
            "emptyIntersectionCount": slope_empty_count,
            "ambiguousIntersectionCount": slope_ambiguous_count,
            "compatibleOffsetHistogram": {
                ",".join(str(value) for value in key): count
                for key, count in sorted(slope_offset_histogram.items())
            },
            "uniqueOffsetHistogramBySplit": {
                split: {str(value): count for value, count in sorted(counts.items())}
                for split, counts in slope_unique_by_split.items()
            },
        },
    }
    base["authority"] = {
        "readsReferencePixels": False,
        "usesM1CoefficientExports": True,
        "hasFrozenDiscoveryHoldoutSplit": True,
        "establishesTwoProductInteractionLaw": False,
        "authorizesProductionMutation": False,
    }
    base["conclusion"] = (
        "One signed setup product is an exact fixed power of two; the other sweeps "
        "a complete 13-bit mantissa suffix. Compatible variable-term correction "
        "sets and discarded-column fingerprints are reported without rendered "
        "output feedback."
    )
    return base


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    arguments = parser.parse_args()
    result = analyze()
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(result["census"], indent=2, sort_keys=True))
    print(json.dumps(result["rulerAnalysis"], indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
