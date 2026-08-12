#!/usr/bin/env python3
"""Calibrate dynamic circle-scale reciprocal codes on opened Apple frames."""

import argparse
import json
import struct
from pathlib import Path
from typing import Any

import numpy as np

from apple_glass_reference_renderer import (
    AppleGlassReferenceRenderer,
    compare_images,
)
from liquid_glass_dynamic_background_arithmetic import (
    GENERIC_EXACT_CONFIGURATION,
)
from liquid_glass_dynamic_capture import (
    EXPECTED_SAMPLE_INDICES,
    _background_geometry,
    _background_mvp,
    _report_paths,
    _source_texture,
    _uniform_payloads,
)
from liquid_glass_dynamic_render_gate import (
    DYNAMIC_HIGHLIGHT_CONFIGURATION,
    _draw_scissors,
    _glass_prefix_output,
    _pre_final_input,
    _raw_mip_levels,
)
from liquid_glass_post_glass_gate import sha256_file
from liquid_glass_profile_matrix import GLASS_FRAGMENTS, decode_profile
from liquid_glass_runtime_raster_coefficients import (
    coefficient_table,
    runtime_quad_from_vertices,
    slopes_bits,
)
from liquid_glass_square_selector_calibration import SquareSelectorCalibration

import raster_tile_selector_model as arithmetic


type JsonObject = dict[str, Any]

CIRCLE_CONSTANT_BITS = 0x3FC3_AB4B


def _float32_from_bits(bits: int) -> float:
    return struct.unpack("<f", struct.pack("<I", bits))[0]


def _circle_scale_bits(profile_payload: bytes) -> tuple[float, int]:
    fields = decode_profile(profile_payload)["fields"]
    radius = np.float32(fields["sdf_arg2"]["values"][2])
    circle_constant = np.float32(_float32_from_bits(CIRCLE_CONSTANT_BITS))
    circle_scale = np.float32(radius * circle_constant)
    bits = int(circle_scale.view(np.uint32))
    return float(radius), bits


def _profile(
    report_path: Path,
    *,
    static_capture: Path,
    float_intrinsic_table: Path,
    square_calibration: SquareSelectorCalibration,
    selector_table: tuple[int, ...],
) -> JsonObject:
    report = json.loads(report_path.read_text(encoding="utf-8"))
    material = str(report.get("material"))
    appearance = str(report.get("appearance"))
    if (material, appearance) != ("clear", "light"):
        raise ValueError(f"unexpected dynamic profile: {material}/{appearance}")
    fragment = GLASS_FRAGMENTS[material]
    uniforms = report.get("dynamicBackgroundUniforms")
    records = uniforms.get("records") if isinstance(uniforms, dict) else None
    if not isinstance(records, list) or [
        record.get("sampleIndex") for record in records
    ] != list(EXPECTED_SAMPLE_INDICES):
        raise ValueError(f"dynamic records are incomplete: {report_path}")

    captured_codes = bytearray(float_intrinsic_table.read_bytes())
    states: list[JsonObject] = []
    with AppleGlassReferenceRenderer(
        static_capture,
        intrinsic_table=float_intrinsic_table,
        half_intrinsic_table=static_capture / "half-intrinsics.bin",
        load_interpolant_trace=False,
        load_interpolant_axis_trace=False,
        load_diagnostic_traces=False,
    ) as renderer:
        for name, value in GENERIC_EXACT_CONFIGURATION.items():
            renderer.program[name].value = value
        for name, value in DYNAMIC_HIGHLIGHT_CONFIGURATION.items():
            renderer.program[name].value = value

        for record in records:
            if not isinstance(record, dict):
                raise ValueError("dynamic record is not an object")
            render = record.get("render")
            if not isinstance(render, dict) or render.get("executed") is not True:
                raise ValueError("dynamic render did not execute")
            sample_index = int(record["sampleIndex"])
            main, shadow = _background_geometry(render, fragment)
            profile_payload, _ = _uniform_payloads(render, fragment)
            background_scissor, highlight_scissor = _draw_scissors(
                render,
                fragment,
            )
            _, reference = _glass_prefix_output(report_path.parent, render)
            source = _source_texture(render, fragment)
            quad = runtime_quad_from_vertices(
                main.vertices,
                name=f"reciprocal-sample-{sample_index}",
            )
            selector_use = square_calibration.use_for(quad.case, selector_table)
            selectors = list(selector_table)
            selectors[selector_use.table_index] = selector_use.selected
            tile_start, coefficients = coefficient_table(
                quad,
                selector_table=selectors,
            )
            radius, circle_scale_bits = _circle_scale_bits(profile_payload)
            mantissa = circle_scale_bits & 0x007F_FFFF
            captured_code = captured_codes[mantissa]

            renderer.set_draw_geometries(main=main, shadow=shadow)
            renderer.set_mvp_payload(_background_mvp(render, fragment))
            renderer.set_profile_payload(profile_payload)
            renderer.set_source_mip_bgra(
                _raw_mip_levels(report_path.parent, source)
            )
            renderer.set_destination_bgra_path(
                _pre_final_input(report_path.parent, render)
            )
            renderer.set_draw_scissors(
                background=background_scissor,
                final_highlight=highlight_scissor,
            )
            renderer.set_interpolant_coefficients(
                coefficients,
                tile_start=tile_start,
                slope_bits=slopes_bits(quad, selectors),
            )

            candidates: list[JsonObject] = []
            for encoded_delta in range(4):
                codes = bytearray(captured_codes)
                code = (captured_code & 0x3F) | (encoded_delta << 6)
                codes[mantissa] = code
                renderer.intrinsic_table_texture.write(codes)
                comparison = compare_images(reference, renderer.render())
                candidates.append(
                    {
                        "encodedDelta": encoded_delta,
                        "netReciprocalUlpAdjustment": encoded_delta - 1,
                        "code": code,
                        "comparison": comparison.as_json(),
                    }
                )
            exact_deltas = [
                candidate["encodedDelta"]
                for candidate in candidates
                if candidate["comparison"]["exact"] is True
            ]
            states.append(
                {
                    "sampleIndex": sample_index,
                    "remaining": record["remaining"],
                    "radius": radius,
                    "circleScaleBits": f"0x{circle_scale_bits:08x}",
                    "mantissa": mantissa,
                    "capturedCode": captured_code,
                    "capturedEncodedDelta": captured_code >> 6,
                    "selector": {
                        "base": selector_use.base,
                        "selected": selector_use.selected,
                        "offset": selector_use.offset,
                    },
                    "candidates": candidates,
                    "exactEncodedDeltas": exact_deltas,
                    "uniqueExactEncodedDelta": (
                        exact_deltas[0] if len(exact_deltas) == 1 else None
                    ),
                }
            )
        implementation = renderer.implementation

    return {
        "artifact": str(report_path.parent),
        "report": str(report_path),
        "material": material,
        "appearance": appearance,
        "implementation": implementation,
        "states": states,
        "summary": {
            "stateCount": len(states),
            "statesWithAtLeastOneExactCode": sum(
                bool(state["exactEncodedDeltas"]) for state in states
            ),
            "statesWithUniqueExactCode": sum(
                state["uniqueExactEncodedDelta"] is not None for state in states
            ),
            "capturedCodeExactStateCount": sum(
                state["capturedEncodedDelta"] in state["exactEncodedDeltas"]
                for state in states
            ),
        },
    }


def calibrate(
    dynamic_roots: list[Path],
    *,
    static_capture: Path,
    float_intrinsic_table: Path,
    square_selector_archive: Path,
) -> JsonObject:
    selector_table = arithmetic.load_selector_table()
    square_calibration = SquareSelectorCalibration.load(square_selector_archive)
    profiles: list[JsonObject] = []
    for dynamic_root in dynamic_roots:
        reports = _report_paths(dynamic_root)
        if len(reports) != 1:
            raise ValueError(
                f"expected one report under {dynamic_root}, found {len(reports)}"
            )
        profiles.append(
            _profile(
                reports[0],
                static_capture=static_capture,
                float_intrinsic_table=float_intrinsic_table,
                square_calibration=square_calibration,
                selector_table=selector_table,
            )
        )
    states = [state for profile in profiles for state in profile["states"]]
    return {
        "liquidGlassDynamicReciprocalCalibrationSchemaVersion": 1,
        "classification": "retrospective opened-frame calibration",
        "dynamicRoots": [str(path) for path in dynamic_roots],
        "staticCapture": str(static_capture),
        "floatIntrinsicTable": {
            "path": str(float_intrinsic_table),
            "sha256": sha256_file(float_intrinsic_table),
        },
        "squareSelectorArchive": {
            "path": str(square_selector_archive),
            "sha256": sha256_file(square_selector_archive),
        },
        "profiles": profiles,
        "summary": {
            "profileCount": len(profiles),
            "stateCount": len(states),
            "statesWithAtLeastOneExactCode": sum(
                bool(state["exactEncodedDeltas"]) for state in states
            ),
            "statesWithUniqueExactCode": sum(
                state["uniqueExactEncodedDelta"] is not None for state in states
            ),
            "capturedCodeExactStateCount": sum(
                state["capturedEncodedDelta"] in state["exactEncodedDeltas"]
                for state in states
            ),
        },
        "limitations": [
            "Every Apple output used here was opened before this search.",
            "An exact code may be non-unique when BGRA8 is insensitive to the reciprocal ULP.",
            "This calibration is not prospective parity evidence.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dynamic_roots", nargs="+", type=Path)
    parser.add_argument("--static-capture", type=Path, required=True)
    parser.add_argument("--float-intrinsic-table", type=Path, required=True)
    parser.add_argument("--square-selector-archive", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    result = calibrate(
        arguments.dynamic_roots,
        static_capture=arguments.static_capture,
        float_intrinsic_table=arguments.float_intrinsic_table,
        square_selector_archive=arguments.square_selector_archive,
    )
    encoded = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if arguments.output is None:
        print(encoded, end="")
    else:
        arguments.output.write_text(encoded, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
