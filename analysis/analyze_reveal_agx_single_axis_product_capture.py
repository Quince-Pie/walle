#!/usr/bin/env python3.14
"""Authenticate the M1 capture that isolates each AGX setup-axis product."""

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

PLAN_ROOT: Final = ROOT / "build" / "analysis-agx-basis" / "single-axis-product-plan-v1"
CAPTURE_ROOT: Final = (
    ROOT / "build" / "analysis-agx-basis" / "macos-single-axis-product-v1"
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
    / "single-axis-product-analysis"
    / "result.json"
)
DRAW_COUNT: Final = 1_872
COEFFICIENT_TRIPLE_COUNT: Final = 7_488
RECORD_VECTOR_COUNT: Final = 101
RECORD_WORD_COUNT: Final = RECORD_VECTOR_COUNT * 4

EXPECTED: Final = {
    PLAN_ROOT
    / "manifest.json": "53ae94f3d9a6b7615053813c84cc445c9f74ca51d51d8bce1feea9de9437b15f",
    PLAN: "f899053762c9c4b18c6cce87c3eaedf4ad1a5668b24f59783ed4987caf326272",
    VERTICES: "30e26e930024dbcf7400253ba4d15489b3a663d732f8845716c46256e8cab4dd",
    RAW: "0773f0f24207ab2fb8a51857159637be0643df6708aea4dfbc864ffeb2f129d9",
    CAPTURE_MANIFEST: "2ed1595e2c717f3d6bf55d95f2b34b8dd1b41ea4f360ebec2f0c3383578c762c",
    STDERR: "e039575cca917bde60e19d52e5d5c6c08e8a601b365bda4e1e0e9a2e8601933a",
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
            "coefficientTripleCount": COEFFICIENT_TRIPLE_COUNT,
            "drawCount": DRAW_COUNT,
            "patternCount": DRAW_COUNT,
            "targetCount": 8,
            "targetTileCount": 16,
        }
        or manifest.get("schema") != "walle-reveal-agx-setup-accumulator-capture-v1"
        or not isinstance(capture, dict)
        or capture.get("recordCount") != DRAW_COUNT
        or capture.get("recordVectorCount") != RECORD_VECTOR_COUNT
        or capture.get("sha256") != EXPECTED[RAW]
        or STDOUT.stat().st_size != 0
    ):
        raise ValueError("single-axis capture closure differs")

    trace = STDERR.read_text(encoding="utf-8")
    patches = re.findall(
        r"^AGX_IO coefficient export patched handle=(\d+) shader=0x([0-9a-f]+)$",
        trace,
        flags=re.MULTILINE,
    )
    counts = re.findall(
        r"^AGX_IO coefficient export matches=(\d+) applied=(\d+)$",
        trace,
        flags=re.MULTILINE,
    )
    if patches != [("1", "28c0")] or counts != [("1", "1")]:
        raise ValueError("coefficient-export patch census differs")

    words = np.fromfile(RAW, dtype="<u4")
    vertices = np.fromfile(VERTICES, dtype="<u4")
    if words.size != DRAW_COUNT * RECORD_WORD_COUNT:
        raise ValueError("single-axis capture word count differs")
    if vertices.size != DRAW_COUNT * 3 * 8:
        raise ValueError("single-axis vertex word count differs")
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
        "histogram": {str(delta): count for delta, count in sorted(histogram.items())},
    }


def analyze() -> JsonObject:
    plan, words, vertex_words = _load()
    experiments = sweep._require_list(plan.get("experiments"), "experiments")  # noqa: SLF001
    draws = sweep._require_list(plan.get("draws"), "draws")  # noqa: SLF001
    if len(experiments) != DRAW_COUNT or len(draws) != DRAW_COUNT:
        raise ValueError("single-axis experiment census differs")
    bitmap = accumulator.setup.P25_PATH.read_bytes()

    constant_deltas: list[int] = []
    slope_deltas: list[int] = []
    by_axis: dict[int, list[int]] = defaultdict(list)
    by_direction: dict[int, list[int]] = defaultdict(list)
    by_scale: dict[int, list[int]] = defaultdict(list)
    by_anchor_offset: dict[int, list[int]] = defaultdict(list)
    distinct_output_counts: list[int] = []

    for experiment_value, draw_value in zip(experiments, draws, strict=True):
        experiment = sweep._require_dict(experiment_value, "experiment")  # noqa: SLF001
        draw = sweep._require_dict(draw_value, "draw")  # noqa: SLF001
        record = sweep._require_int(draw.get("recordIndex"), "record")  # noqa: SLF001
        if experiment.get("recordIndex") != record:
            raise ValueError("experiment/draw record join differs")
        lanes = sweep._require_list(experiment.get("lanes"), "lanes")  # noqa: SLF001
        if len(lanes) != 4:
            raise ValueError("single-axis experiment does not fill four lanes")
        vertices = sweep._vertices(vertex_words, record)  # noqa: SLF001
        positions = accumulator.setup._fixed_positions(vertices)  # noqa: SLF001
        anchor = accumulator.top_left._top_left(positions)  # noqa: SLF001
        tile = (
            sweep._require_int(draw.get("tileX"), "tile x"),  # noqa: SLF001
            sweep._require_int(draw.get("tileY"), "tile y"),  # noqa: SLF001
        )
        triples = accumulator._triples(words[record])  # noqa: SLF001

        for component, (lane_value, triple) in enumerate(
            zip(lanes, triples, strict=True)
        ):
            lane = sweep._require_dict(lane_value, "lane")  # noqa: SLF001
            axis = sweep._require_int(lane.get("isolatedAxis"), "isolated axis")  # noqa: SLF001
            direction = sweep._require_int(lane.get("direction"), "direction")  # noqa: SLF001
            scale = sweep._require_int(lane.get("scaleExponent"), "scale")  # noqa: SLF001
            anchor_offset = sweep._require_int(  # noqa: SLF001
                lane.get("anchorUlpOffset"), "anchor ULP offset"
            )
            distinct = sweep._require_int(  # noqa: SLF001
                lane.get("distinctConstantsAcrossPlusMinus64MiddleUlps"),
                "distinct output count",
            )
            if axis not in {0, 1} or direction not in {-1, 1} or distinct < 35:
                raise ValueError("single-axis lane domain differs")
            actual_anchor, _determinant, terms = preimage._middle_terms(  # noqa: SLF001
                vertices, component, tile
            )
            if len(terms) != 1:
                raise ValueError("lane does not isolate exactly one product")
            sign, index, exponent = preimage._joined_index(terms)  # noqa: SLF001
            predicted_middle = sweep._require_dict(  # noqa: SLF001
                lane.get("predictedMiddleTerm"), "predicted middle term"
            )
            if (
                f"0x{actual_anchor:08x}" != lane.get("predictedAnchorBits")
                or sign != predicted_middle.get("sign")
                or index != predicted_middle.get("index")
                or exponent != predicted_middle.get("exponent")
            ):
                raise ValueError("single-axis preimage metadata differs")

            for slope_axis, actual in enumerate(triple[:2]):
                predicted_slope = accumulator.top_left._anchor_slope(  # noqa: SLF001
                    vertices, component, slope_axis, bitmap, anchor
                )
                slope_deltas.append(
                    accumulator.export._float_ulp_delta(actual, predicted_slope)  # noqa: SLF001
                )
            predicted = sweep._shared_reciprocal_constant_bits(  # noqa: SLF001
                vertices,
                component,
                tile,
                bitmap,
                join_precision=28,
                reciprocal_truncation=20,
            )
            if f"0x{predicted:08x}" != lane.get("predictedConstantBits"):
                raise ValueError("generated constant prediction differs")
            delta = accumulator.export._float_ulp_delta(triple[2], predicted)  # noqa: SLF001
            constant_deltas.append(delta)
            by_axis[axis].append(delta)
            by_direction[direction].append(delta)
            by_scale[scale].append(delta)
            by_anchor_offset[anchor_offset].append(delta)
            distinct_output_counts.append(distinct)

    if any(constant_deltas) or any(slope_deltas):
        raise ValueError("single-axis product pipeline is not exact")
    return {
        "schema": "walle-reveal-agx-single-axis-product-analysis-v1",
        "classification": "output-blind isolated-axis M1 setup-product tomography",
        "authority": {
            "readsReferencePixels": False,
            "usesM1CoefficientExports": True,
            "establishesEachIsolatedAxisProductOnCapturedDomain": True,
            "establishesTwoProductInteractionLaw": False,
            "authorizesProductionMutation": False,
        },
        "inputs": {
            "analyzer": _identity(Path(__file__).resolve()),
            "closure": [_identity(path) for path in EXPECTED],
        },
        "capture": {
            "drawCount": DRAW_COUNT,
            "coefficientTripleCount": COEFFICIENT_TRIPLE_COUNT,
            "slopeWordCount": COEFFICIENT_TRIPLE_COUNT * 2,
            "targetTileCount": 16,
            "scaleExponentCount": 13,
            "anchorOffsetCount": 9,
        },
        "result": {
            "constantCoefficients": _summary(constant_deltas),
            "slopeCoefficients": _summary(slope_deltas),
            "byIsolatedAxis": {
                str(key): _summary(values) for key, values in sorted(by_axis.items())
            },
            "byDirection": {
                str(key): _summary(values)
                for key, values in sorted(by_direction.items())
            },
            "byScaleExponent": {
                str(key): _summary(values) for key, values in sorted(by_scale.items())
            },
            "byAnchorUlpOffset": {
                str(key): _summary(values)
                for key, values in sorted(by_anchor_offset.items())
            },
            "distinctOutputCount": {
                "minimum": min(distinct_output_counts),
                "maximum": max(distinct_output_counts),
            },
        },
        "conclusion": (
            "Each isolated X or Y displacement-times-slope product, the shared-P25 "
            "reciprocal stage, and final anchor materialization are exact throughout "
            "the cancellation-sensitive capture. Combined with the zero-anchor "
            "synthetic join result, the arbitrary-child discrepancy requires an "
            "interaction between two simultaneously nonzero signed products."
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
    print(json.dumps(result["result"]["constantCoefficients"], indent=2))  # type: ignore[index]


if __name__ == "__main__":
    main()
