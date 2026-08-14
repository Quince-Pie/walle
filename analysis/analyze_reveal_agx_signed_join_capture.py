#!/usr/bin/env python3.14
"""Authenticate and score the focused M1 AGX signed-join capture."""

from __future__ import annotations

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
import analyze_reveal_agx_join_preimage as preimage  # noqa: E402


type JsonObject = dict[str, object]

PLAN_ROOT: Final = ROOT / "build" / "analysis-agx-basis" / "signed-join-plan-v1"
CAPTURE_ROOT: Final = ROOT / "build" / "analysis-agx-basis" / "macos-signed-join-v1"
PLAN: Final = CAPTURE_ROOT / "reveal-agx-setup-accumulator-plan.json"
VERTICES: Final = CAPTURE_ROOT / "reveal-agx-setup-accumulator-vertices.bin"
RAW: Final = CAPTURE_ROOT / "capture" / "reveal-agx-setup-accumulator.raw"
CAPTURE_MANIFEST: Final = CAPTURE_ROOT / "capture" / "manifest.json"
STDERR: Final = CAPTURE_ROOT / "capture.stderr"
STDOUT: Final = CAPTURE_ROOT / "capture.stdout"
OUTPUT: Final = (
    ROOT / "build" / "analysis-agx-basis" / "signed-join-analysis" / "result.json"
)
RECORD_VECTOR_COUNT: Final = 101
RECORD_WORD_COUNT: Final = RECORD_VECTOR_COUNT * 4

EXPECTED: Final = {
    PLAN_ROOT
    / "manifest.json": "3258e85b45146de90cd4ee03dfc1088f51115ec0371925f61365b754b68c1035",
    PLAN: "a8e4aa06195ed15370370b1262e2090569227343481de7f1c8371ea830c986e5",
    VERTICES: "53a67cf9418f51c6801856d79f2bad4178e51c592eb81555bff5c2b4579140aa",
    RAW: "4a9d5683fca5de4e701323417f6b66f7e0efb1b0189c32b32ccfaae087d811c7",
    CAPTURE_MANIFEST: "fb8dff2a72e434595bae7e2f9331bcd9762106e465ae046a0e8e44afdd6e9bf4",
    STDERR: "6baee071d5e1b9416efff15be88d1506f4b89a4d190531757e3d5947f456bd35",
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
        or census
        != {
            "coefficientTripleCount": 79_200,
            "drawCount": 19_800,
            "patternCount": 825,
            "targetCount": 8,
        }
        or manifest.get("schema") != "walle-reveal-agx-setup-accumulator-capture-v1"
        or not isinstance(capture, dict)
        or capture.get("recordCount") != 19_800
        or capture.get("recordVectorCount") != RECORD_VECTOR_COUNT
        or capture.get("sha256") != EXPECTED[RAW]
        or STDOUT.stat().st_size != 0
    ):
        raise ValueError("focused capture closure differs")
    trace = STDERR.read_text(encoding="utf-8")
    if re.findall(
        r"^AGX_IO coefficient export patched handle=(\d+) shader=0x([0-9a-f]+)$",
        trace,
        flags=re.MULTILINE,
    ) != [("1", "28c0")]:
        raise ValueError("coefficient export patch target differs")
    if re.findall(
        r"^AGX_IO coefficient export matches=(\d+) applied=(\d+)$",
        trace,
        flags=re.MULTILINE,
    ) != [("1", "1")]:
        raise ValueError("coefficient export patch census differs")
    words = np.fromfile(RAW, dtype="<u4")
    if words.size != 19_800 * RECORD_WORD_COUNT:
        raise ValueError("focused capture word count differs")
    vertices = np.fromfile(VERTICES, dtype="<u4")
    if vertices.size != 19_800 * 3 * 8:
        raise ValueError("focused vertex word count differs")
    return (
        plan,
        words.reshape(19_800, RECORD_VECTOR_COUNT, 4),
        vertices.reshape(19_800, 3, 8),
    )


def _summary(values: list[int]) -> JsonObject:
    histogram = Counter(values)
    return {
        "count": len(values),
        "exactCount": histogram[0],
        "withinOneCount": sum(
            count for delta, count in histogram.items() if abs(delta) <= 1
        ),
        "minimum": min(values),
        "maximum": max(values),
        "smallDeltaHistogram": {
            str(delta): count
            for delta, count in sorted(histogram.items())
            if abs(delta) <= 64
        },
    }


def analyze() -> JsonObject:
    plan, words, vertex_words = _load()
    patterns = sweep._require_list(plan.get("patterns"), "patterns")  # noqa: SLF001
    draws = sweep._require_list(plan.get("draws"), "draws")  # noqa: SLF001
    bitmap = accumulator.setup.P25_PATH.read_bytes()
    deltas: list[int] = []
    changed_component_deltas: list[int] = []
    control_component_deltas: list[int] = []
    by_base_pattern: dict[int, list[int]] = defaultdict(list)
    by_vertex: dict[int, list[int]] = defaultdict(list)
    by_offset: dict[int, list[int]] = defaultdict(list)
    first_mismatches: list[JsonObject] = []
    perturbed_preimages: list[JsonObject] = []
    slope_word_count = 0
    slope_mismatch_count = 0
    perturbed_constant_residuals_with_exact_slopes = 0
    perturbed_constant_residuals_with_inexact_slopes = 0

    for draw_value in draws:
        draw = sweep._require_dict(draw_value, "draw")  # noqa: SLF001
        record = sweep._require_int(draw.get("recordIndex"), "record")  # noqa: SLF001
        pattern_index = sweep._require_int(  # noqa: SLF001
            draw.get("patternIndex"), "pattern"
        )
        metadata = sweep._require_dict(patterns[pattern_index], "pattern metadata")  # noqa: SLF001
        changed_component = sweep._require_int(  # noqa: SLF001
            metadata.get("component"), "changed component"
        )
        base_pattern = sweep._require_int(  # noqa: SLF001
            metadata.get("basePatternIndex"), "base pattern"
        )
        changed_vertex = sweep._require_int(  # noqa: SLF001
            metadata.get("vertex"), "changed vertex"
        )
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
            predicted_slopes = tuple(
                accumulator.top_left._anchor_slope(  # noqa: SLF001
                    vertices,
                    component,
                    axis,
                    bitmap,
                    anchor_index,
                )
                for axis in range(2)
            )
            slopes_exact = predicted_slopes == triple[:2]
            slope_word_count += 2
            slope_mismatch_count += sum(
                predicted != actual
                for predicted, actual in zip(predicted_slopes, triple[:2], strict=True)
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
            deltas.append(delta)
            if component == changed_component:
                changed_component_deltas.append(delta)
                by_base_pattern[base_pattern].append(delta)
                by_vertex[changed_vertex].append(delta)
                by_offset[offset].append(delta)
                if delta:
                    if slopes_exact:
                        perturbed_constant_residuals_with_exact_slopes += 1
                    else:
                        perturbed_constant_residuals_with_inexact_slopes += 1
                    anchor, determinant, terms = preimage._middle_terms(  # noqa: SLF001
                        vertices, component, tile
                    )
                    sign, join_index, join_exponent = preimage._joined_index(  # noqa: SLF001
                        terms
                    )
                    selector, selector_exponent = accumulator.setup._p25_selector(  # noqa: SLF001
                        determinant, bitmap
                    )
                    offsets = preimage._compatible_offsets(  # noqa: SLF001
                        triple[2],
                        anchor,
                        sign,
                        join_index,
                        join_exponent,
                        selector,
                        selector_exponent,
                    )
                    if not offsets:
                        raise ValueError("focused p28 preimage escaped search radius")
                    perturbed_preimages.append(
                        {
                            "recordIndex": record,
                            "targetIndex": draw["targetIndex"],
                            "tile": list(tile),
                            "basePatternIndex": base_pattern,
                            "component": component,
                            "changedVertex": changed_vertex,
                            "ulpOffset": offset,
                            "termSigns": [term[0] for term in terms],
                            "predictedJoin": {
                                "sign": sign,
                                "index": join_index,
                                "exponent": join_exponent,
                            },
                            "actualMinusPredictedFloatUlps": delta,
                            "slopesExact": slopes_exact,
                            "compatibleJoinOffset": {
                                "minimum": offsets[0],
                                "maximum": offsets[-1],
                                "count": len(offsets),
                                "contiguous": offsets
                                == tuple(range(offsets[0], offsets[-1] + 1)),
                            },
                        }
                    )
            else:
                control_component_deltas.append(delta)
            if delta and len(first_mismatches) < 128:
                first_mismatches.append(
                    {
                        "recordIndex": record,
                        "targetIndex": draw["targetIndex"],
                        "tile": list(tile),
                        "focusedPatternIndex": pattern_index,
                        "basePatternIndex": base_pattern,
                        "changedComponent": changed_component,
                        "component": component,
                        "changedVertex": changed_vertex,
                        "ulpOffset": offset,
                        "actualBits": f"0x{triple[2]:08x}",
                        "predictedBits": f"0x{predicted:08x}",
                        "actualMinusPredictedFloatUlps": delta,
                    }
                )

    return {
        "schema": "walle-reveal-agx-signed-join-analysis-v1",
        "classification": "output-blind focused M1 signed-join coefficient sweep",
        "authority": {
            "readsReferencePixels": False,
            "usesM1CoefficientExports": True,
            "exactSignedJoinRecovered": False,
            "productionMutationAuthorized": False,
        },
        "inputs": [_identity(path) for path in EXPECTED],
        "capture": {
            "drawCount": 19_800,
            "coefficientWordCount": 79_200,
            "patternCount": 825,
            "matchingPatchedShaderCount": 1,
            "slopeWordCount": slope_word_count,
            "slopeMismatchCount": slope_mismatch_count,
            "perturbedConstantResidualsWithExactSlopes": (
                perturbed_constant_residuals_with_exact_slopes
            ),
            "perturbedConstantResidualsWithInexactSlopes": (
                perturbed_constant_residuals_with_inexact_slopes
            ),
        },
        "baseline": {
            "overall": _summary(deltas),
            "perturbedComponent": _summary(changed_component_deltas),
            "unchangedControlComponents": _summary(control_component_deltas),
            "byBasePattern": {
                str(key): _summary(values)
                for key, values in sorted(by_base_pattern.items())
            },
            "byChangedVertex": {
                str(key): _summary(values) for key, values in sorted(by_vertex.items())
            },
            "byUlpOffset": {
                str(key): _summary(values) for key, values in sorted(by_offset.items())
            },
            "firstMismatches": first_mismatches,
            "perturbedPreimages": perturbed_preimages,
        },
        "conclusion": (
            "The focused capture authenticates how the established p28 model behaves "
            "under independent vertex-ULP perturbations. Candidate signed-join laws "
            "must improve the perturbed component while preserving all unchanged "
            "control components."
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
    print(json.dumps(result["baseline"]["overall"], indent=2, sort_keys=True))  # type: ignore[index]


if __name__ == "__main__":
    main()
