#!/usr/bin/env python3.14
"""Invert the determinant-amplified real-child M1 slope ruler."""

import argparse
import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from typing import Final

import numpy as np

import analyze_reveal_agx_setup_accumulator as accumulator
import analyze_reveal_agx_setup_tile_sweep as sweep
import analyze_reveal_agx_two_product_ruler as ruler


type JsonObject = dict[str, object]

ROOT: Final = Path(__file__).resolve().parent.parent
PLAN_ROOT: Final = (
    ROOT / "build" / "analysis-agx-basis" / "public-child-slope-ruler-plan-v2"
)
CAPTURE_ROOT: Final = (
    ROOT / "build" / "analysis-agx-basis" / "macos-public-child-slope-ruler-v1"
)
PLAN: Final = CAPTURE_ROOT / "reveal-agx-setup-accumulator-plan.json"
VERTICES: Final = CAPTURE_ROOT / "reveal-agx-setup-accumulator-vertices.bin"
RAW: Final = CAPTURE_ROOT / "capture" / "reveal-agx-setup-accumulator.raw"
MANIFEST: Final = CAPTURE_ROOT / "capture" / "manifest.json"
STDERR: Final = CAPTURE_ROOT / "capture.stderr"
STDOUT: Final = CAPTURE_ROOT / "capture.stdout"
INTERPOSER: Final = CAPTURE_ROOT / "libwalle-agx-ldcf-export.dylib"
EXECUTABLE: Final = CAPTURE_ROOT / "reveal-agx-setup-accumulator-probe"
OUTPUT: Final = (
    ROOT
    / "build"
    / "analysis-agx-basis"
    / "public-child-slope-ruler-analysis"
    / "result.json"
)
DRAW_COUNT: Final = 8_192
RECORD_VECTOR_COUNT: Final = 101
RECORD_WORD_COUNT: Final = RECORD_VECTOR_COUNT * 4
EXPECTED: Final = {
    PLAN_ROOT
    / "manifest.json": "0e72f271a13ee44dbe5c993fd4a5a29dfe2e4b5771abf516059b0c8b69de7254",
    PLAN: "40e7d1b11c7e118d44418cadcd5eace05b817ba1d410688c5cefab50ab5afdb1",
    VERTICES: "21d8b179ad89902b3ea9dfdb7c6529d0e463b03826d43efa095f754a3a4b2b07",
    RAW: "5b12c2ab5e0a29291b18d26f45642e74b80755da5c8642ae2308da0cc279ce4d",
    MANIFEST: "8fab5de601e05f59fff04e358050307166618e4ab489bc3969f9e845238cf88f",
    STDERR: "487843a0e135e08da017a2d16b0dfaa44ac776ad68e4e4b4dfb24055b36f8eba",
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


def _compatible_numerator_offsets(
    actual_bits: int,
    baseline: tuple[int, int, int],
    determinant: int,
    bitmap: bytes,
    *,
    radius: int = 512,
) -> range:
    sign, index, exponent = baseline
    if sign == 0:
        return range(0, 1) if actual_bits == 0 else range(0)
    lower = max(-radius, 1 - index)
    upper = radius + 1
    output_sign = sign * (-1 if determinant < 0 else 1)

    def ordered_float(bits: int) -> int:
        return (~bits & 0xFFFF_FFFF) if bits & 0x8000_0000 else bits | 0x8000_0000

    target = ordered_float(actual_bits) * output_sign

    def value(offset: int) -> int:
        bits = accumulator.setup._reciprocal_product(  # noqa: SLF001
            (sign, index + offset, exponent), determinant, bitmap
        )
        return ordered_float(bits) * output_sign

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

    return range(boundary(strict=False), boundary(strict=True))


def analyze() -> JsonObject:
    for path, expected in EXPECTED.items():
        actual = _sha256(path)
        if actual != expected:
            raise ValueError(f"SHA-256 differs: {path.relative_to(ROOT)}: {actual}")
    plan = json.loads(PLAN.read_text(encoding="utf-8"))
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    census = plan.get("census")
    capture = manifest.get("capture")
    if (
        plan.get("schema") != "walle-reveal-agx-setup-accumulator-plan-v1"
        or not isinstance(census, dict)
        or census.get("drawCount") != DRAW_COUNT
        or census.get("coefficientTripleCount") != DRAW_COUNT * 4
        or census.get("skippedCount") != 0
        or not isinstance(capture, dict)
        or capture.get("recordCount") != DRAW_COUNT
        or capture.get("sha256") != EXPECTED[RAW]
        or STDOUT.stat().st_size != 0
    ):
        raise ValueError("amplified slope ruler closure differs")
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
        raise ValueError("coefficient export census differs")
    words = np.fromfile(RAW, dtype="<u4")
    vertex_words = np.fromfile(VERTICES, dtype="<u4")
    if (
        words.size != DRAW_COUNT * RECORD_WORD_COUNT
        or vertex_words.size != DRAW_COUNT * 24
    ):
        raise ValueError("amplified slope ruler shape differs")
    records = words.reshape(DRAW_COUNT, RECORD_VECTOR_COUNT, 4)
    vertices_array = vertex_words.reshape(DRAW_COUNT, 3, 8)
    experiments = sweep._require_list(plan.get("experiments"), "experiments")  # noqa: SLF001
    draws = sweep._require_list(plan.get("draws"), "draws")  # noqa: SLF001
    bitmap = accumulator.setup.P25_PATH.read_bytes()

    width_histogram = Counter[int]()
    offset_histogram = Counter[int]()
    split_offsets = {"discovery": Counter[int](), "holdout": Counter[int]()}
    predicted_deltas = Counter[int]()
    empty = 0
    ambiguous = 0
    lane_mismatch = 0
    outputs: list[JsonObject] = []
    for experiment_value, draw_value in zip(experiments, draws, strict=True):
        experiment = sweep._require_dict(experiment_value, "experiment")  # noqa: SLF001
        draw = sweep._require_dict(draw_value, "draw")  # noqa: SLF001
        record_index = sweep._require_int(draw.get("recordIndex"), "record")  # noqa: SLF001
        vertices = sweep._vertices(vertices_array, record_index)  # noqa: SLF001
        positions = accumulator.setup._fixed_positions(vertices)  # noqa: SLF001
        determinant = accumulator.setup._determinant(positions)  # noqa: SLF001
        anchor = accumulator.top_left._top_left(positions)  # noqa: SLF001
        triples = accumulator._triples(records[record_index])  # noqa: SLF001
        split = experiment.get("split")
        if split not in {"discovery", "holdout"}:
            raise ValueError("slope ruler split differs")
        axes: list[JsonObject] = []
        for axis in range(2):
            baseline, products = ruler._slope_numerator(  # noqa: SLF001
                vertices, 0, axis, anchor
            )
            actual_words = tuple(int(triple[axis]) for triple in triples)
            if len(set(actual_words)) != 1:
                lane_mismatch += 1
            predicted = accumulator.setup._reciprocal_product(  # noqa: SLF001
                baseline, determinant, bitmap
            )
            delta = accumulator.export._float_ulp_delta(  # noqa: SLF001
                actual_words[0], predicted
            )
            predicted_deltas[delta] += 1
            compatible = set.intersection(
                *(
                    set(
                        _compatible_numerator_offsets(
                            actual,
                            baseline,
                            determinant,
                            bitmap,
                        )
                    )
                    for actual in actual_words
                )
            )
            offsets = sorted(compatible)
            width_histogram[len(offsets)] += 1
            if not offsets:
                empty += 1
            elif len(offsets) == 1:
                offset_histogram[offsets[0]] += 1
                split_offsets[split][offsets[0]] += 1
            else:
                ambiguous += 1
            axes.append(
                {
                    "axis": axis,
                    "baselineNumerator": {
                        "sign": baseline[0],
                        "index": baseline[1],
                        "exponent": baseline[2],
                    },
                    "sourceProducts": list(products),
                    "actualSlopeBits": f"0x{actual_words[0]:08x}",
                    "predictedSlopeBits": f"0x{predicted:08x}",
                    "actualMinusPredictedFloatUlps": delta,
                    "compatibleNumeratorOffsets": offsets,
                }
            )
        outputs.append(
            {
                "recordIndex": record_index,
                "variableUlpOffset": experiment["variableUlpOffset"],
                "split": split,
                "axes": axes,
            }
        )

    return {
        "schema": "walle-reveal-agx-public-child-slope-ruler-analysis-v1",
        "classification": "output-blind determinant-amplified M1 slope inversion",
        "authority": {
            "readsReferencePixels": False,
            "usesM1CoefficientExports": True,
            "hasFrozenDiscoveryHoldoutSplit": True,
            "establishesTwoSourceProductLaw": False,
            "authorizesProductionMutation": False,
        },
        "inputs": {
            "analyzer": _identity(Path(__file__).resolve()),
            "closure": [_identity(path) for path in EXPECTED],
        },
        "census": {
            "drawCount": DRAW_COUNT,
            "axisCount": DRAW_COUNT * 2,
            "laneMismatchCount": lane_mismatch,
            "emptyIntersectionCount": empty,
            "ambiguousIntersectionCount": ambiguous,
            "widthHistogram": {
                str(key): value for key, value in sorted(width_histogram.items())
            },
            "uniqueOffsetHistogram": {
                str(key): value for key, value in sorted(offset_histogram.items())
            },
            "uniqueOffsetHistogramBySplit": {
                key: {str(offset): count for offset, count in sorted(value.items())}
                for key, value in split_offsets.items()
            },
            "slopeUlpDeltaHistogram": {
                str(key): value for key, value in sorted(predicted_deltas.items())
            },
        },
        "records": outputs,
        "conclusion": (
            "The reduced determinant amplifies p27 slope-numerator offsets before "
            "the measured P25 reciprocal. Compatible offsets are inferred solely "
            "from exported M1 A/B coefficients; no rendered output is opened."
        ),
    }


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


if __name__ == "__main__":
    main()
