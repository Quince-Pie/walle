#!/usr/bin/env python3.14
"""Authenticate and invert the dense M1 AGX two-product tomography capture."""

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

PLAN_ROOT: Final = (
    ROOT / "build" / "analysis-agx-basis" / "two-product-tomography-plan-v1"
)
CAPTURE_ROOT: Final = (
    ROOT / "build" / "analysis-agx-basis" / "macos-two-product-tomography-v1"
)
PLAN: Final = CAPTURE_ROOT / "reveal-agx-setup-accumulator-plan.json"
VERTICES: Final = CAPTURE_ROOT / "reveal-agx-setup-accumulator-vertices.bin"
RAW: Final = CAPTURE_ROOT / "capture" / "reveal-agx-setup-accumulator.raw"
CAPTURE_MANIFEST: Final = CAPTURE_ROOT / "capture" / "manifest.json"
STDERR: Final = CAPTURE_ROOT / "capture.stderr"
STDOUT: Final = CAPTURE_ROOT / "capture.stdout"
OUTPUT: Final = (
    ROOT
    / "build"
    / "analysis-agx-basis"
    / "two-product-tomography-analysis"
    / "result.json"
)
DRAW_COUNT: Final = 17_916
RECORD_VECTOR_COUNT: Final = 101
RECORD_WORD_COUNT: Final = RECORD_VECTOR_COUNT * 4
SEARCH_RADIUS: Final = 4_096

EXPECTED: Final = {
    PLAN_ROOT
    / "manifest.json": "0dcdbce7d0cc974e1c35aee597de6dcfee74ef14c79c7120cd47c0a7ac898a4a",
    PLAN: "d24594e51ff8d76236ae3b00500a04c82e30fcbd19a788f8cf73c4d7f2fd0e8e",
    VERTICES: "d0116bace4f34731cd9a37a555eab1e333090285fe224f207b4ce77f97e6216c",
    RAW: "c2dc11c37574d9e9e2052ca9d98821d480061130eaec209e9875b7349fb9f8e1",
    CAPTURE_MANIFEST: "82072bcced8a1a63dd57b364bf55118798c9e95a451518e3533a08cf7231d993",
    STDERR: "d824ab5649ad963168b07ad76e5041b4fcbd09bc2f7568709744aeedc07260a6",
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
    census = plan.get("census")
    capture = manifest.get("capture")
    if (
        plan.get("schema") != "walle-reveal-agx-setup-accumulator-plan-v1"
        or not isinstance(census, dict)
        or census.get("drawCount") != DRAW_COUNT
        or census.get("patternCount") != DRAW_COUNT
        or census.get("coefficientTripleCount") != DRAW_COUNT * 4
        or census.get("targetCount") != 8
        or census.get("discoveryPatternCount") != 13_471
        or census.get("holdoutPatternCount") != 4_445
        or manifest.get("schema") != "walle-reveal-agx-setup-accumulator-capture-v1"
        or not isinstance(capture, dict)
        or capture.get("recordCount") != DRAW_COUNT
        or capture.get("recordVectorCount") != RECORD_VECTOR_COUNT
        or capture.get("sha256") != EXPECTED[RAW]
        or STDOUT.stat().st_size != 0
    ):
        raise ValueError("two-product tomography closure differs")
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
        raise ValueError("two-product capture word count differs")
    if vertices.size != DRAW_COUNT * 3 * 8:
        raise ValueError("two-product vertex word count differs")
    return (
        plan,
        words.reshape(DRAW_COUNT, RECORD_VECTOR_COUNT, 4),
        vertices.reshape(DRAW_COUNT, 3, 8),
    )


def _summary(values: list[int]) -> JsonObject:
    histogram = Counter(values)
    return {
        "count": len(values),
        "exactCount": histogram[0],
        "residualCount": len(values) - histogram[0],
        "minimum": min(values),
        "maximum": max(values),
        "smallHistogram": {
            str(delta): count
            for delta, count in sorted(histogram.items())
            if abs(delta) <= 64
        },
    }


def _compatible_offsets(
    actual_bits: int,
    anchor_bits: int,
    sign: int,
    index: int,
    exponent: int,
    selector: int,
    selector_exponent: int,
) -> set[int]:
    lower = max(-SEARCH_RADIUS, 1 - index)
    upper = SEARCH_RADIUS + 1

    def ordered_float(bits: int) -> int:
        return (~bits & 0xFFFF_FFFF) if bits & 0x8000_0000 else bits | 0x8000_0000

    target = ordered_float(actual_bits) * sign

    def value(offset: int) -> int:
        bits = preimage._constant_from_join(  # noqa: SLF001
            anchor_bits,
            sign,
            index + offset,
            exponent,
            selector,
            selector_exponent,
        )
        if bits is None:
            raise ValueError("join neighborhood escaped final product domain")
        return ordered_float(bits) * sign

    def boundary(*, strict: bool) -> int:
        lo, hi = lower, upper
        while lo < hi:
            middle = (lo + hi) // 2
            observed = value(middle)
            crosses = observed > target if strict else observed >= target
            if crosses:
                hi = middle
            else:
                lo = middle + 1
        return lo

    first = boundary(strict=False)
    last = boundary(strict=True)
    return set(range(first, last))


def analyze() -> JsonObject:
    plan, words, vertex_words = _load()
    experiments = sweep._require_list(plan.get("experiments"), "experiments")  # noqa: SLF001
    draws = sweep._require_list(plan.get("draws"), "draws")  # noqa: SLF001
    if len(experiments) != DRAW_COUNT or len(draws) != DRAW_COUNT:
        raise ValueError("two-product experiment census differs")
    bitmap = accumulator.setup.P25_PATH.read_bytes()

    baseline_deltas: list[int] = []
    slope_deltas: list[int] = []
    interval_widths: list[int] = []
    actual_offsets: list[int] = []
    clean_interval_widths: list[int] = []
    clean_actual_offsets: list[int] = []
    by_split: dict[str, list[int]] = defaultdict(list)
    by_case: dict[int, list[int]] = defaultdict(list)
    by_sign_order: dict[str, list[int]] = defaultdict(list)
    records: list[JsonObject] = []

    for experiment_value, draw_value in zip(experiments, draws, strict=True):
        experiment = sweep._require_dict(experiment_value, "experiment")  # noqa: SLF001
        draw = sweep._require_dict(draw_value, "draw")  # noqa: SLF001
        record = sweep._require_int(draw.get("recordIndex"), "record")  # noqa: SLF001
        case = sweep._require_int(experiment.get("caseIndex"), "case")  # noqa: SLF001
        split = experiment.get("split")
        if experiment.get("recordIndex") != record or split not in {
            "discovery",
            "holdout",
        }:
            raise ValueError("two-product experiment join differs")
        anchors = sweep._require_list(experiment.get("anchors"), "anchors")  # noqa: SLF001
        if len(anchors) != 4:
            raise ValueError("two-product experiment does not have four anchors")
        vertices = sweep._vertices(vertex_words, record)  # noqa: SLF001
        positions = accumulator.setup._fixed_positions(vertices)  # noqa: SLF001
        top_left = accumulator.top_left._top_left(positions)  # noqa: SLF001
        tile = (
            sweep._require_int(draw.get("tileX"), "tile x"),  # noqa: SLF001
            sweep._require_int(draw.get("tileY"), "tile y"),  # noqa: SLF001
        )
        intersections: list[set[int]] = []
        lane_deltas: list[int] = []
        lane_slope_deltas: list[int] = []
        terms_for_record: tuple[tuple[int, int, int], ...] | None = None

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
            ):
                raise ValueError("two-product preimage differs")
            if terms_for_record is None:
                terms_for_record = terms
            elif terms_for_record != terms:
                raise ValueError("common anchors changed a middle product")
            for axis, actual in enumerate(triple[:2]):
                predicted_slope = accumulator.top_left._anchor_slope(  # noqa: SLF001
                    vertices, component, axis, bitmap, top_left
                )
                slope_delta = accumulator.export._float_ulp_delta(  # noqa: SLF001
                    actual, predicted_slope
                )
                slope_deltas.append(slope_delta)
                lane_slope_deltas.append(slope_delta)
            selector, selector_exponent = accumulator.setup._p25_selector(  # noqa: SLF001
                determinant, bitmap
            )
            predicted = preimage._constant_from_join(  # noqa: SLF001
                anchor_bits, sign, index, exponent, selector, selector_exponent
            )
            if predicted is None:
                raise ValueError("baseline join escaped final product domain")
            delta = accumulator.export._float_ulp_delta(triple[2], predicted)  # noqa: SLF001
            baseline_deltas.append(delta)
            lane_deltas.append(delta)
            compatible = _compatible_offsets(
                triple[2],
                anchor_bits,
                sign,
                index,
                exponent,
                selector,
                selector_exponent,
            )
            if not compatible:
                raise ValueError("two-product join escaped bounded inversion")
            intersections.append(compatible)

        intersection = set.intersection(*intersections)
        if not intersection:
            raise ValueError("four-anchor join intersection is empty")
        offsets = sorted(intersection)
        if offsets != list(range(offsets[0], offsets[-1] + 1)):
            raise ValueError("four-anchor join interval is not contiguous")
        interval_widths.append(len(offsets))
        if len(offsets) == 1:
            actual_offsets.append(offsets[0])
        slopes_exact = not any(lane_slope_deltas)
        if slopes_exact:
            clean_interval_widths.append(len(offsets))
            if len(offsets) == 1:
                clean_actual_offsets.append(offsets[0])
        by_split[str(split)].extend(lane_deltas)
        by_case[case].extend(lane_deltas)
        assert terms_for_record is not None
        sign_order = f"{terms_for_record[0][0]:+d},{terms_for_record[1][0]:+d}"
        by_sign_order[sign_order].extend(lane_deltas)
        records.append(
            {
                "recordIndex": record,
                "caseIndex": case,
                "split": split,
                "firstNonanchorUlpOffset": experiment["firstNonanchorUlpOffset"],
                "secondNonanchorUlpOffset": experiment["secondNonanchorUlpOffset"],
                "middleTerms": experiment["middleTerms"],
                "actualMinusPredictedFloatUlps": lane_deltas,
                "slopesExact": slopes_exact,
                "compatibleJoinOffsetIntersection": {
                    "minimum": offsets[0],
                    "maximum": offsets[-1],
                    "count": len(offsets),
                    "values": offsets,
                },
            }
        )

    width_histogram = Counter(interval_widths)
    clean_width_histogram = Counter(clean_interval_widths)
    return {
        "schema": "walle-reveal-agx-two-product-tomography-analysis-v1",
        "classification": "output-blind dense two-product M1 setup tomography",
        "authority": {
            "readsReferencePixels": False,
            "usesM1CoefficientExports": True,
            "hasFrozenDiscoveryHoldoutSplit": True,
            "establishesTwoProductInteractionLaw": False,
            "authorizesProductionMutation": False,
        },
        "inputs": {
            "analyzer": _identity(Path(__file__).resolve()),
            "closure": [_identity(path) for path in EXPECTED],
        },
        "census": {
            "drawCount": DRAW_COUNT,
            "coefficientConstantCount": len(baseline_deltas),
            "slopeWordCount": len(slope_deltas),
            "slopeMismatchCount": sum(delta != 0 for delta in slope_deltas),
            "discoveryDrawCount": 13_471,
            "holdoutDrawCount": 4_445,
            "uniqueJoinOffsetCount": len(actual_offsets),
            "cleanDrawCount": len(clean_interval_widths),
            "cleanUniqueJoinOffsetCount": len(clean_actual_offsets),
            "intervalWidthHistogram": {
                str(width): count for width, count in sorted(width_histogram.items())
            },
            "uniqueJoinOffsetHistogram": {
                str(offset): count
                for offset, count in sorted(Counter(actual_offsets).items())
            },
            "cleanIntervalWidthHistogram": {
                str(width): count
                for width, count in sorted(clean_width_histogram.items())
            },
            "cleanUniqueJoinOffsetHistogram": {
                str(offset): count
                for offset, count in sorted(Counter(clean_actual_offsets).items())
            },
        },
        "baseline": {
            "overall": _summary(baseline_deltas),
            "bySplit": {
                key: _summary(values) for key, values in sorted(by_split.items())
            },
            "byCase": {
                str(key): _summary(values) for key, values in sorted(by_case.items())
            },
            "bySignOrder": {
                key: _summary(values) for key, values in sorted(by_sign_order.items())
            },
        },
        "records": records,
        "conclusion": (
            "The capture holds two opposite-signed middle products fixed while four "
            "common anchors expose each hidden p28 interaction result. The reported "
            "intervals and frozen split provide the discovery/holdout corpus for a "
            "compact input-bit compensation law; this analyzer does not fit one."
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
    print(json.dumps(result["baseline"]["overall"], indent=2, sort_keys=True))  # type: ignore[index]


if __name__ == "__main__":
    main()
