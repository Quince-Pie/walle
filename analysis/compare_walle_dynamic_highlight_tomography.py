#!/usr/bin/env python3
"""Select highlight arithmetic with current-system uniform tomography."""

import argparse
import json
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import numpy as np


type JsonObject = dict[str, Any]

ROOT = Path(__file__).resolve().parent.parent
LG_ANALYSIS = ROOT / "lg-test" / "Analysis"
if str(LG_ANALYSIS) not in sys.path:
    sys.path.insert(0, str(LG_ANALYSIS))

import raster_tile_selector_model as raster_arithmetic  # noqa: E402

from apple_glass_reference_renderer import (  # noqa: E402
    AppleGlassReferenceRenderer,
)
from compare_walle_dynamic_highlight_half import (  # noqa: E402
    HEIGHT,
    SMALL_NEAR_SQUARE_HEIGHT_FIXED_DELTAS,
    SMALL_NEAR_SQUARE_SELECTOR_COMPRESSED_SHA256,
    SMALL_NEAR_SQUARE_SELECTOR_RAW_SHA256,
    SMALL_SQUARE_SELECTOR_COMPRESSED_SHA256,
    SMALL_SQUARE_SELECTOR_RAW_SHA256,
    SMALL_SQUARE_WIDTH_FIXED_LOWER,
    SMALL_SQUARE_WIDTH_FIXED_UPPER,
    WIDTH,
    geometry,
    highlight_quad,
    interpolant_configuration,
    object_value,
    source_levels,
)
from liquid_glass_runtime_raster_coefficients import (  # noqa: E402
    axis_table,
    load_near_square_selector_calibration,
    load_square_selector_calibration,
    selector_table_for_calibrated_quad,
)
from liquid_glass_shader_specialization import (  # noqa: E402
    load_amd_exact_circle_shader,
)


SAMPLES = (1, 8, 12)
DIVISION_MODES = (0, 1, 2, 3)


def trace_records(timeline: Mapping[str, Any]) -> dict[int, Mapping[str, Any]]:
    dynamic = object_value(
        timeline.get("dynamicBackgroundUniforms"),
        name="dynamic background uniforms",
    )
    records = dynamic.get("records")
    if not isinstance(records, list):
        raise ValueError("dynamic background records are absent")
    result: dict[int, Mapping[str, Any]] = {}
    for value in records:
        record = object_value(value, name="dynamic background record")
        sample = record.get("sampleIndex")
        if sample not in SAMPLES:
            continue
        render = object_value(record.get("render"), name="dynamic render")
        replay = object_value(render.get("exactPassReplay"), name="exact replay")
        trace = object_value(
            replay.get("finalHighlightAlphaTrace"),
            name="full final-highlight trace",
        )
        comparison = object_value(
            trace.get("capturedVsRebuiltBGRA8"),
            name="captured/system comparison",
        )
        tomography = object_value(
            trace.get("stageTomography"),
            name="highlight stage tomography",
        )
        cases = tomography.get("cases")
        if (
            not isinstance(sample, int)
            or trace.get("executed") is not True
            or trace.get("systemSpecializationExact") is not True
            or comparison.get("exactByteMatch") is not True
            or comparison.get("mismatchedByteCount") != 0
            or tomography.get("executed") is not True
            or tomography.get("caseCount") != 10
            or not isinstance(cases, list)
            or len(cases) != 10
        ):
            raise ValueError(f"sample {sample} tomography did not pass exactly")
        result[sample] = trace
    if set(result) != set(SAMPLES):
        raise ValueError(f"tomography samples differ: {sorted(result)}")
    return result


def apple_case(
    capture: Path,
    case: Mapping[str, Any],
) -> tuple[str, np.ndarray, list[Mapping[str, Any]]]:
    name = case.get("name")
    edits = case.get("edits")
    replay = object_value(case.get("replay"), name="tomography replay")
    output = object_value(replay.get("output"), name="tomography output")
    raw_name = output.get("rawFile")
    if (
        not isinstance(name, str)
        or not isinstance(edits, list)
        or not all(isinstance(value, Mapping) for value in edits)
        or replay.get("executed") is not True
        or not isinstance(raw_name, str)
        or output.get("width") != WIDTH
        or output.get("height") != HEIGHT
        or output.get("rawBytes") != WIDTH * HEIGHT * 8
    ):
        raise ValueError("tomography case metadata differs")
    words = np.fromfile(capture / raw_name, dtype="<u2")
    if words.size != WIDTH * HEIGHT * 4:
        raise ValueError("tomography output is truncated")
    pixels = words.reshape(HEIGHT, WIDTH, 4)
    if not np.all(pixels[..., :3] == pixels[..., :1]):
        raise ValueError("tomography RGB channels differ")
    if not np.all(pixels[..., 3] == 0x3C00):
        raise ValueError("tomography output alpha is not one")
    return name, pixels[..., 0].copy(), edits


def edited_uniform(
    original: bytes,
    edits: list[Mapping[str, Any]],
) -> bytes:
    result = bytearray(original)
    for edit in edits:
        offset = edit.get("recordOffset")
        encoded = edit.get("hex")
        if not isinstance(offset, int) or not isinstance(encoded, str):
            raise ValueError("tomography uniform edit metadata differs")
        payload = bytes.fromhex(encoded)
        if offset < 0 or offset + len(payload) > len(result):
            raise ValueError("tomography uniform edit is out of bounds")
        result[offset : offset + len(payload)] = payload
    return bytes(result)


def compare_sample(
    *,
    fixture: Path,
    capture: Path,
    trace: Mapping[str, Any],
    shader: str,
    intrinsic_table: Path,
    square_selector_calibration: tuple[int, ...],
    near_square_selector_calibration: tuple[int, ...],
    device_index: int,
) -> JsonObject:
    manifest = json.loads((fixture / "manifest.json").read_text(encoding="utf-8"))
    construction = object_value(manifest.get("construction"), name="construction")
    modes = object_value(
        construction.get("highlightArithmeticModes"),
        name="highlight arithmetic modes",
    )
    coefficients, tile_start, slopes = interpolant_configuration(
        fixture,
        source="axis-highlight",
        anchor_policy=None,
        square_selector_calibration=square_selector_calibration,
        near_square_selector_calibration=near_square_selector_calibration,
    )
    quad = highlight_quad(fixture)
    base_selectors = raster_arithmetic.load_selector_table()
    selectors = selector_table_for_calibrated_quad(
        quad,
        base_selectors,
        square_selector_calibration,
        near_square_selector_calibration,
        width_fixed_lower=SMALL_SQUARE_WIDTH_FIXED_LOWER,
        height_fixed_deltas=SMALL_NEAR_SQUARE_HEIGHT_FIXED_DELTAS,
    )
    axis_start, axis_data = axis_table(quad, selector_table=selectors)
    renderer_arguments: dict[str, Any] = {
        "fragment_shader_source": shader,
        "intrinsic_table": intrinsic_table,
        "interpolant_coefficient_data": coefficients,
        "interpolant_tile_start": tile_start,
        "interpolant_slope_bits": slopes,
        "interpolant_axis_data": axis_data,
        "interpolant_axis_start": axis_start,
        "source_mip_bgra_levels": source_levels(fixture, construction),
        "destination_bgra_data": (fixture / "destination.rgba8").read_bytes(),
        "main_geometry": geometry(
            fixture,
            vertex_name="main-vertices.f32",
            index_name=None,
        ),
        "shadow_geometry": geometry(
            fixture,
            vertex_name="shadow-vertices.f32",
            index_name="shadow-indices.u16",
        ),
        "final_highlight_geometry": geometry(
            fixture,
            vertex_name="highlight-vertices.f32",
            index_name="highlight-indices.u16",
        ),
        "profile_payload": (fixture / "profile.bin").read_bytes(),
        "runtime_data": {},
        "load_interpolant_trace": False,
        "load_interpolant_axis_trace": True,
        "load_diagnostic_traces": False,
        "context_arguments": {"device_index": device_index},
    }
    original_uniform = (fixture / "highlight-uniform.bin").read_bytes()
    cases = object_value(
        trace.get("stageTomography"),
        name="highlight stage tomography",
    )["cases"]
    mode_results: dict[int, list[JsonObject]] = {mode: [] for mode in DIVISION_MODES}
    with AppleGlassReferenceRenderer(fixture, **renderer_arguments) as renderer:
        uniforms = {
            "HighlightDerivativeMode": modes["derivative"],
            "HighlightCoordinateMode": modes["coordinate"],
            "HighlightAlphaUlpBias": modes["alphaUlpBias"],
            "HighlightCoverageArithmeticMode": modes["coverage"],
            "HighlightMixMode": modes["mix"],
            "HighlightBandMode": modes["band"],
            "HighlightNormalizeMode": modes["normalize"],
            "HighlightNormalizedCoordinateMode": modes["normalizedCoordinate"],
            "HighlightSdfArithmeticMode": modes["sdfArithmetic"],
            "HighlightSdfSquaredUlpBias": modes["sdfSquaredUlpBias"],
            "HighlightSdfDistanceUlpBias": modes["sdfDistanceUlpBias"],
        }
        for uniform_name, value in uniforms.items():
            renderer.program[uniform_name].value = value
        for raw_case in cases:
            case = object_value(raw_case, name="tomography case")
            name, apple, edits = apple_case(capture, case)
            payload = edited_uniform(original_uniform, edits)
            for mode in DIVISION_MODES:
                renderer.program["HighlightFloatDivisionMode"].value = mode
                pixels = renderer.render_final_highlight_half(
                    uniform_payload=payload,
                    trace_mode=2,
                )
                if not np.all(pixels == pixels[..., :1]):
                    raise ValueError("candidate tomography channels differ")
                candidate = pixels[..., 0]
                changed = candidate != apple
                coordinates = np.argwhere(changed)
                mode_results[mode].append(
                    {
                        "name": name,
                        "checkedHalfWords": WIDTH * HEIGHT,
                        "mismatchedHalfWords": int(np.count_nonzero(changed)),
                        "firstMismatches": [
                            {
                                "x": int(x),
                                "y": int(y),
                                "appleBits": f"0x{int(apple[y, x]):04x}",
                                "candidateBits": f"0x{int(candidate[y, x]):04x}",
                            }
                            for y, x in coordinates[:16]
                        ],
                    }
                )
        implementation = renderer.implementation
    return {
        "sampleIndex": manifest["sampleIndex"],
        "remainingFloat32Bits": manifest["remainingFloat32Bits"],
        "implementation": implementation,
        "divisionModes": {
            str(mode): {
                "checkedHalfWords": sum(
                    case["checkedHalfWords"] for case in mode_results[mode]
                ),
                "mismatchedHalfWords": sum(
                    case["mismatchedHalfWords"] for case in mode_results[mode]
                ),
                "exactCaseCount": sum(
                    case["mismatchedHalfWords"] == 0 for case in mode_results[mode]
                ),
                "cases": mode_results[mode],
            }
            for mode in DIVISION_MODES
        },
    }


def run(arguments: argparse.Namespace) -> JsonObject:
    timeline = json.loads(
        (arguments.capture / "transition-timeline.json").read_text(encoding="utf-8")
    )
    records = trace_records(timeline)
    square_selector_calibration = load_square_selector_calibration(
        arguments.square_selector_calibration,
        width_fixed_lower=SMALL_SQUARE_WIDTH_FIXED_LOWER,
        width_fixed_upper=SMALL_SQUARE_WIDTH_FIXED_UPPER,
        expected_compressed_sha256=SMALL_SQUARE_SELECTOR_COMPRESSED_SHA256,
        expected_raw_sha256=SMALL_SQUARE_SELECTOR_RAW_SHA256,
    )
    near_square_selector_calibration = load_near_square_selector_calibration(
        arguments.near_square_selector_calibration,
        width_fixed_lower=SMALL_SQUARE_WIDTH_FIXED_LOWER,
        width_fixed_upper=SMALL_SQUARE_WIDTH_FIXED_UPPER,
        height_fixed_deltas=SMALL_NEAR_SQUARE_HEIGHT_FIXED_DELTAS,
        expected_compressed_sha256=(SMALL_NEAR_SQUARE_SELECTOR_COMPRESSED_SHA256),
        expected_raw_sha256=SMALL_NEAR_SQUARE_SELECTOR_RAW_SHA256,
    )
    shader = load_amd_exact_circle_shader(
        "regular",
        ROOT / "analysis/apple_glass_reference.frag.glsl",
        coordinate_mode=4,
    )
    samples = [
        compare_sample(
            fixture=(arguments.fixtures / f"regular-dark-dematerialize-{sample:02d}"),
            capture=arguments.capture,
            trace=records[sample],
            shader=shader,
            intrinsic_table=arguments.intrinsic_table,
            square_selector_calibration=square_selector_calibration,
            near_square_selector_calibration=near_square_selector_calibration,
            device_index=arguments.device_index,
        )
        for sample in SAMPLES
    ]
    totals = {
        str(mode): {
            "checkedHalfWords": sum(
                sample["divisionModes"][str(mode)]["checkedHalfWords"]
                for sample in samples
            ),
            "mismatchedHalfWords": sum(
                sample["divisionModes"][str(mode)]["mismatchedHalfWords"]
                for sample in samples
            ),
            "exactCaseCount": sum(
                sample["divisionModes"][str(mode)]["exactCaseCount"]
                for sample in samples
            ),
        }
        for mode in DIVISION_MODES
    }
    return {
        "schemaVersion": 1,
        "scope": "current QuartzCore final-highlight uniform tomography",
        "capture": str(arguments.capture),
        "fixtures": str(arguments.fixtures),
        "squareSelectorCalibration": str(arguments.square_selector_calibration),
        "nearSquareSelectorCalibration": str(
            arguments.near_square_selector_calibration
        ),
        "samples": samples,
        "totals": totals,
        "exactDivisionModes": [
            mode
            for mode in DIVISION_MODES
            if totals[str(mode)]["mismatchedHalfWords"] == 0
        ],
    }


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--capture",
        type=Path,
        default=ROOT / "artifacts/local-highlight-tomography-current-01",
    )
    parser.add_argument(
        "--fixtures",
        type=Path,
        default=(
            ROOT
            / "build/generated/liquid-glass"
            / "dynamic-highlight-tomography-fixtures"
        ),
    )
    parser.add_argument(
        "--intrinsic-table",
        type=Path,
        default=ROOT / "artifacts/apple-float-intrinsics-r8-30556057571.bin",
    )
    parser.add_argument(
        "--square-selector-calibration",
        type=Path,
        default=(
            ROOT / "lg-test/Analysis" / "raster_small_square_selectors_u32le.zlib"
        ),
    )
    parser.add_argument(
        "--near-square-selector-calibration",
        type=Path,
        default=(
            ROOT / "lg-test/Analysis" / "raster_small_near_square_selectors_u32le.zlib"
        ),
    )
    parser.add_argument("--device-index", type=int, default=0)
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def main() -> int:
    arguments = parse_arguments()
    report = run(arguments)
    encoded = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if arguments.output is None:
        print(encoded, end="")
    else:
        arguments.output.write_text(encoded, encoding="utf-8")
        print(arguments.output)
    return 0 if len(report["exactDivisionModes"]) == 1 else 1


if __name__ == "__main__":
    raise SystemExit(main())
