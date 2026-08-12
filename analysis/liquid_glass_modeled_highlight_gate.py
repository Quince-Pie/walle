#!/usr/bin/env python3
"""Bit-gate the recovered Apple highlight model without oracle injection."""

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from apple_glass_reference_renderer import (
    CAPTURE_HEIGHT,
    CAPTURE_WIDTH,
    AppleGlassReferenceRenderer,
    bgra_raw,
    compare_images,
)
from liquid_glass_exact_highlight_alpha_gate import (
    highlight_fixture_fingerprint,
    mapping,
    runtime_json,
)
from liquid_glass_highlight_tomography_gate import (
    compare_half_words,
    final_highlight_trace,
    load_half_rgb,
)
from liquid_glass_post_glass_gate import load_sweep, raw_path, sha256_file


type JsonObject = dict[str, Any]


@dataclass(frozen=True, slots=True)
class AlphaCase:
    name: str
    trace_key: str
    uniform_edits: dict[int, bytes]


ALPHA_CASES = (
    AlphaCase("combined", "exactHalfAlpha", {}),
    AlphaCase("key", "exactKeyHalfAlpha", {0xF0: bytes(8)}),
    AlphaCase("fill", "exactFillHalfAlpha", {0xE8: bytes(8)}),
)


def configure_recovered_model(renderer: AppleGlassReferenceRenderer) -> None:
    renderer.program["UseAppleIntrinsicTable"].value = 1
    renderer.program["UseAppleHalfIntrinsicTable"].value = 1
    renderer.program["HighlightCoordinateMode"].value = 0
    renderer.program["HighlightDerivativeMode"].value = 1
    renderer.program["HighlightCoverageArithmeticMode"].value = 1
    renderer.program["HighlightNormalizeMode"].value = 1
    renderer.program["HighlightVibrantArithmeticMode"].value = 9
    renderer.program["HighlightSourceDivisionMode"].value = 0


def validate_alpha_trace(alpha_capture: Path) -> JsonObject:
    trace = final_highlight_trace(runtime_json(alpha_capture))
    if (
        trace.get("schemaVersion") != 1
        or trace.get("executed") is not True
        or trace.get("capturedAppleFunctionUnmodified") is not True
        or trace.get("selectedLastA2XghfcDraw") is not True
    ):
        raise ValueError("final highlight alpha trace contract differs")
    comparison = mapping(
        trace.get("capturedVsRebuiltBGRA8"),
        "same-format Apple pipeline comparison",
    )
    if (
        comparison.get("compared") is not True
        or comparison.get("mismatchedByteCount") != 0
        or comparison.get("maximumChannelDelta") != 0
    ):
        raise ValueError("captured Apple pipeline rebuild is not exact")
    return trace


def alpha_reference_path(
    alpha_capture: Path,
    trace: JsonObject,
    key: str,
) -> Path:
    replay = mapping(trace.get(key), key)
    output = mapping(replay.get("output"), f"{key} output")
    filename = output.get("rawFile")
    if (
        replay.get("executed") is not True
        or output.get("pixelFormat") != 115
        or not isinstance(filename, str)
    ):
        raise ValueError(f"{key} exact-half replay contract differs")
    path = alpha_capture / filename
    expected_bytes = CAPTURE_WIDTH * CAPTURE_HEIGHT * 8
    if path.stat().st_size != expected_bytes:
        raise ValueError(
            f"{path} has {path.stat().st_size} bytes; expected {expected_bytes}"
        )
    return path


def aggregate_image_comparisons(comparisons: list[JsonObject]) -> JsonObject:
    return {
        "exact": all(bool(comparison["exact"]) for comparison in comparisons),
        "mismatchedBytes": sum(
            int(comparison["mismatchedBytes"]) for comparison in comparisons
        ),
        "mismatchedPixels": sum(
            int(comparison["mismatchedPixels"]) for comparison in comparisons
        ),
        "maximumChannelDelta": max(
            int(comparison["maximumChannelDelta"]) for comparison in comparisons
        ),
    }


def run_gate(
    reference_capture: Path,
    alpha_capture: Path,
    float_intrinsic_table: Path,
) -> JsonObject:
    reference_runtime = runtime_json(reference_capture)
    alpha_runtime = runtime_json(alpha_capture)
    reference_fingerprint = highlight_fixture_fingerprint(reference_runtime)
    alpha_fingerprint = highlight_fixture_fingerprint(alpha_runtime)
    if reference_fingerprint != alpha_fingerprint:
        raise ValueError(
            "reference and alpha captures use different highlight fixtures"
        )

    half_intrinsic_table = reference_capture / "half-intrinsics.bin"
    for evidence in (float_intrinsic_table, half_intrinsic_table):
        if not evidence.is_file():
            raise ValueError(f"intrinsic evidence is missing: {evidence}")

    trace = validate_alpha_trace(alpha_capture)
    alpha_results: JsonObject = {}
    with AppleGlassReferenceRenderer(
        reference_capture,
        intrinsic_table=float_intrinsic_table,
        half_intrinsic_table=half_intrinsic_table,
        load_interpolant_trace=False,
        load_interpolant_axis_trace=False,
        load_diagnostic_traces=False,
    ) as renderer:
        configure_recovered_model(renderer)
        for case in ALPHA_CASES:
            reference_path = alpha_reference_path(
                alpha_capture,
                trace,
                case.trace_key,
            )
            reference = load_half_rgb(reference_path)
            candidate_rgba = renderer.render_final_highlight_half(
                uniform_edits=case.uniform_edits
            )
            candidate = np.ascontiguousarray(candidate_rgba[..., 0])
            if not (
                np.array_equal(candidate, candidate_rgba[..., 1])
                and np.array_equal(candidate, candidate_rgba[..., 2])
            ):
                raise ValueError(f"candidate {case.name} RGB half words differ")
            alpha_results[case.name] = {
                "applePath": str(reference_path),
                "appleSHA256": sha256_file(reference_path),
                "uniformEdits": {
                    f"0x{offset:02x}": payload.hex()
                    for offset, payload in sorted(case.uniform_edits.items())
                },
                "comparison": compare_half_words(reference, candidate),
            }
        implementation = renderer.implementation

    sweep = load_sweep(reference_capture)
    post_glass_cases: list[JsonObject] = []
    for untyped_case in sweep["cases"]:
        case = mapping(untyped_case, "post-glass case")
        output = mapping(case.get("output"), "post-glass output")
        name = case.get("name")
        if case.get("executed") is not True or not isinstance(name, str):
            raise ValueError(f"post-glass case failed: {case}")
        input_path = raw_path(case, reference_capture, "inputFile")
        output_path = raw_path(output, reference_capture, "rawFile")
        reference = bgra_raw(
            output_path,
            width=CAPTURE_WIDTH,
            height=CAPTURE_HEIGHT,
        )
        with AppleGlassReferenceRenderer(
            reference_capture,
            destination_bgra_path=input_path,
            intrinsic_table=float_intrinsic_table,
            half_intrinsic_table=half_intrinsic_table,
            load_interpolant_trace=False,
            load_interpolant_axis_trace=False,
            load_diagnostic_traces=False,
        ) as renderer:
            configure_recovered_model(renderer)
            comparison = compare_images(
                reference,
                renderer.render_final_highlight(),
            ).as_json()
        post_glass_cases.append(
            {
                "name": name,
                "input": {
                    "path": str(input_path),
                    "sha256": sha256_file(input_path),
                },
                "appleOutput": {
                    "path": str(output_path),
                    "sha256": sha256_file(output_path),
                },
                "comparison": comparison,
            }
        )

    alpha_exact = all(
        bool(mapping(record, name)["comparison"]["exact"])
        for name, record in alpha_results.items()
    )
    post_glass_gate = aggregate_image_comparisons(
        [mapping(case["comparison"], "comparison") for case in post_glass_cases]
    )
    exact = alpha_exact and bool(post_glass_gate["exact"])
    return {
        "liquidGlassModeledHighlightGateSchemaVersion": 1,
        "referenceCapture": str(reference_capture),
        "alphaCapture": str(alpha_capture),
        "referenceRuntimeSHA256": sha256_file(reference_capture / "runtime.json"),
        "alphaRuntimeSHA256": sha256_file(alpha_capture / "runtime.json"),
        "highlightFixtureFingerprint": reference_fingerprint,
        "intrinsicEvidence": {
            "float": {
                "path": str(float_intrinsic_table),
                "sha256": sha256_file(float_intrinsic_table),
            },
            "half": {
                "path": str(half_intrinsic_table),
                "sha256": sha256_file(half_intrinsic_table),
            },
        },
        "candidateConfiguration": {
            "useAppleFloatIntrinsicTable": True,
            "useAppleHalfIntrinsicTable": True,
            "highlightCoordinateMode": 0,
            "highlightDerivativeMode": 1,
            "highlightCoverageArithmeticMode": 1,
            "highlightNormalizeMode": 1,
            "highlightVibrantArithmeticMode": 9,
            "highlightSourceDivisionMode": 0,
        },
        "implementation": implementation,
        "modeledAlpha": alpha_results,
        "postGlassCases": post_glass_cases,
        "gate": {
            "exact": exact,
            "modeledAlphaExact": alpha_exact,
            "modeledPostGlass": post_glass_gate,
            "oracleInjectionUsed": False,
            "capturedAppleFunctionUnmodified": True,
            "productionShaderAuthorized": False,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reference_capture", type=Path)
    parser.add_argument("alpha_capture", type=Path)
    parser.add_argument("--float-intrinsic-table", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    report = run_gate(
        arguments.reference_capture,
        arguments.alpha_capture,
        arguments.float_intrinsic_table,
    )
    encoded = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if arguments.output is None:
        print(encoded, end="")
    else:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(encoded, encoding="utf-8")
        print(arguments.output)
    return 0 if report["gate"]["exact"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
