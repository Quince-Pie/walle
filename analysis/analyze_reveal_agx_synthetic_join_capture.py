#!/usr/bin/env python3.14
"""Authenticate and score the synthetic M1 AGX signed-join capture."""

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

import analyze_reveal_agx_setup_accumulator as accumulator  # noqa: E402
import analyze_reveal_agx_setup_tile_sweep as sweep  # noqa: E402


type JsonObject = dict[str, object]

PLAN_ROOT: Final = ROOT / "build" / "analysis-agx-basis" / "synthetic-join-plan-v1"
CAPTURE_ROOT: Final = ROOT / "build" / "analysis-agx-basis" / "macos-synthetic-join-v1"
PLAN: Final = CAPTURE_ROOT / "reveal-agx-setup-accumulator-plan.json"
VERTICES: Final = CAPTURE_ROOT / "reveal-agx-setup-accumulator-vertices.bin"
RAW: Final = CAPTURE_ROOT / "capture" / "reveal-agx-setup-accumulator.raw"
CAPTURE_MANIFEST: Final = CAPTURE_ROOT / "capture" / "manifest.json"
STDERR: Final = CAPTURE_ROOT / "capture.stderr"
STDOUT: Final = CAPTURE_ROOT / "capture.stdout"
OUTPUT: Final = (
    ROOT / "build" / "analysis-agx-basis" / "synthetic-join-analysis" / "result.json"
)
DRAW_COUNT: Final = 4_416
PATTERN_COUNT: Final = 184
COEFFICIENT_TRIPLE_COUNT: Final = 17_664
RECORD_VECTOR_COUNT: Final = 101
RECORD_WORD_COUNT: Final = RECORD_VECTOR_COUNT * 4

EXPECTED: Final = {
    PLAN_ROOT
    / "manifest.json": "e6de795a79d5bb501883ce53d4244f024af74128c6eceae1688b2f4aad0a1e92",
    PLAN: "cff8964042fa53c287218a1247c1e3db037e2523c72c394ff58b5255c85af3e8",
    VERTICES: "6b2b3cd6685c9c496b14e99a36de18d666471b2c7aa7dd8caad2385c166536a1",
    RAW: "0ee9db487d71992381a683d1a8fdb7bb0e5f00a8655367750c6054faf39fa6e8",
    CAPTURE_MANIFEST: "917df8a657e9aee72563ef5891fd39aeabbe1ad6e1ede9eee03f7c240c5453b5",
    STDERR: "aed74d257afb96d5359b1c5c28055d226050b8f1ffd85f4c1b273a5429ff9b05",
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
            "patternCount": PATTERN_COUNT,
            "targetCount": 8,
        }
        or manifest.get("schema") != "walle-reveal-agx-setup-accumulator-capture-v1"
        or not isinstance(capture, dict)
        or capture.get("recordCount") != DRAW_COUNT
        or capture.get("recordVectorCount") != RECORD_VECTOR_COUNT
        or capture.get("sha256") != EXPECTED[RAW]
        or STDOUT.stat().st_size != 0
    ):
        raise ValueError("synthetic signed-join capture closure differs")

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
        raise ValueError("synthetic capture word count differs")
    if vertices.size != DRAW_COUNT * 3 * 8:
        raise ValueError("synthetic vertex word count differs")
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
    patterns = sweep._require_list(plan.get("patterns"), "patterns")  # noqa: SLF001
    draws = sweep._require_list(plan.get("draws"), "draws")  # noqa: SLF001
    bitmap = accumulator.setup.P25_PATH.read_bytes()

    coefficient_deltas: list[int] = []
    slope_deltas: list[int] = []
    by_group: dict[int, list[int]] = defaultdict(list)
    by_endpoint: dict[str, list[int]] = defaultdict(list)
    by_offset: dict[int, list[int]] = defaultdict(list)

    for draw_value in draws:
        draw = sweep._require_dict(draw_value, "draw")  # noqa: SLF001
        record = sweep._require_int(draw.get("recordIndex"), "record")  # noqa: SLF001
        pattern_index = sweep._require_int(  # noqa: SLF001
            draw.get("patternIndex"), "pattern"
        )
        metadata = sweep._require_dict(patterns[pattern_index], "pattern")  # noqa: SLF001
        group = sweep._require_int(metadata.get("group"), "group")  # noqa: SLF001
        endpoint = metadata.get("perturbedEndpoint")
        if endpoint not in {"x", "y"}:
            raise ValueError("perturbed endpoint differs")
        offset = sweep._require_int(metadata.get("ulpOffset"), "ULP offset")  # noqa: SLF001
        vertices = sweep._vertices(vertex_words, record)  # noqa: SLF001
        positions = accumulator.setup._fixed_positions(vertices)  # noqa: SLF001
        anchor_index = accumulator.top_left._top_left(positions)  # noqa: SLF001
        tile = (
            sweep._require_int(draw.get("tileX"), "tile x"),  # noqa: SLF001
            sweep._require_int(draw.get("tileY"), "tile y"),  # noqa: SLF001
        )

        for component, triple in enumerate(
            accumulator._triples(words[record])  # noqa: SLF001
        ):
            for axis, actual in enumerate(triple[:2]):
                predicted = accumulator.top_left._anchor_slope(  # noqa: SLF001
                    vertices, component, axis, bitmap, anchor_index
                )
                slope_deltas.append(
                    accumulator.export._float_ulp_delta(actual, predicted)  # noqa: SLF001
                )
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
            coefficient_deltas.append(delta)
            if component == 0:
                by_group[group].append(delta)
                by_endpoint[endpoint].append(delta)
                by_offset[offset].append(delta)

    exact = not any(coefficient_deltas) and not any(slope_deltas)
    if not exact:
        raise ValueError("synthetic signed-join model is not exact")
    return {
        "schema": "walle-reveal-agx-synthetic-join-analysis-v1",
        "classification": "output-blind synthetic M1 signed-join coefficient sweep",
        "authority": {
            "readsReferencePixels": False,
            "usesM1CoefficientExports": True,
            "establishedModelExactOnSyntheticDomain": True,
            "establishesUniversalJoinLaw": False,
            "productionMutationAuthorized": False,
        },
        "inputs": [_identity(path) for path in EXPECTED],
        "capture": {
            "drawCount": DRAW_COUNT,
            "coefficientTripleCount": COEFFICIENT_TRIPLE_COUNT,
            "slopeWordCount": COEFFICIENT_TRIPLE_COUNT * 2,
            "patternCount": PATTERN_COUNT,
            "matchingPatchedShaderCount": 1,
        },
        "result": {
            "constantCoefficients": _summary(coefficient_deltas),
            "slopeCoefficients": _summary(slope_deltas),
            "componentZeroByGroup": {
                str(key): _summary(values) for key, values in sorted(by_group.items())
            },
            "componentZeroByPerturbedEndpoint": {
                key: _summary(values) for key, values in sorted(by_endpoint.items())
            },
            "componentZeroByUlpOffset": {
                str(key): _summary(values) for key, values in sorted(by_offset.items())
            },
        },
        "conclusion": (
            "The established p28 signed join and shared-P25 pipeline are exact for "
            "all zero-anchor power-of-two synthetic triangles, including both "
            "opposite-sign endpoint directions and every tested ULP perturbation. "
            "The arbitrary-child residual therefore requires hidden information in "
            "the upstream per-axis displacement-product inputs, not a different "
            "generic signed join."
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
