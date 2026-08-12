#!/usr/bin/env python3
"""Compare sample-16 custom-Metal arithmetic with the local exact renderer."""

import argparse
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from apple_glass_reference_renderer import (
    CAPTURE_HEIGHT,
    CAPTURE_WIDTH,
    AppleGlassReferenceRenderer,
    compare_images,
)
from liquid_glass_dynamic_capture import (
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
from liquid_glass_profile_matrix import GLASS_FRAGMENTS
from liquid_glass_runtime_raster_coefficients import (
    coefficient_table,
    runtime_quad_from_vertices,
    slopes_bits,
)
from liquid_glass_square_selector_calibration import SquareSelectorCalibration

import raster_tile_selector_model as arithmetic


type JsonObject = dict[str, Any]
type HalfImage = NDArray[np.uint16]
type UIntImage = NDArray[np.uint32]

SAMPLE_INDEX = 16
TRACE_LAYOUTS = {
    "sdf-float": (123, 16),
    "sdf-geometry": (123, 16),
    "sdf-oval": (123, 16),
    "sdf-normal": (123, 16),
    "sdf-coverage": (123, 16),
    "sdf": (115, 8),
    "color-stages-a": (123, 16),
    "color-stages-b": (123, 16),
    "final-color": (115, 8),
}
COMPARISON_ORDER = (
    "sdf",
    "sdf-coverage",
    "color-stages-a",
    "color-stages-b",
    "final-color",
)
GENERIC_EXACT_CONFIGURATION = {
    "SamplerSpatialQuantization": 0,
    "SamplerModel": 0,
    "InnerSamplerCoordinateModel": 3,
    "OuterSamplerCoordinateModel": 1,
    "EdgeSamplerCoordinateModel": 1,
    "ShadowSamplerCoordinateModel": 2,
    "RefractionMixModel": 0,
    "HoldingMixMode": 0,
    "UseAppleRefractionTrace": 0,
    "UseAppleInterpolantTrace": 0,
    "UseAppleSdfTrace": 0,
    "UseAppleSqrtTrace": 0,
    "UseAppleRsqrtTrace": 0,
    "UseAppleIntrinsicTable": 1,
    "UseAppleHalfIntrinsicTable": 0,
    "RecordAppleIntrinsicUsage": 0,
    "CoordinateMode": 5,
    "AnalyticCoordinateUlpBias": 0,
    "AppleFastSqrtBias": 0,
    "AppleFastReciprocalBias": 1,
    "ArithmeticBarrier": 0,
    "ProfileMode4Path": 0,
    "EmulateAppleBlend": 1,
}
FROZEN_FILES = {
    "analysisWrapperSha256": Path(__file__),
    "referenceRendererSha256": Path(__file__).with_name(
        "apple_glass_reference_renderer.py"
    ),
    "referenceShaderSha256": Path(__file__).with_name(
        "apple_glass_reference.frag.glsl"
    ),
    "dynamicRenderGateSha256": Path(__file__).with_name(
        "liquid_glass_dynamic_render_gate.py"
    ),
    "runtimeRasterCoefficientsSha256": Path(__file__).with_name(
        "liquid_glass_runtime_raster_coefficients.py"
    ),
    "squareSelectorCalibrationSha256": Path(__file__).with_name(
        "liquid_glass_square_selector_calibration.py"
    ),
}


def mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} is not an object")
    return value


def _verify_preregistration(
    path: Path,
    *,
    run_id: int,
    static_capture: Path,
    float_intrinsic_table: Path,
    square_selector_archive: Path,
) -> tuple[JsonObject, JsonObject]:
    registration = mapping(
        json.loads(path.read_text(encoding="utf-8")),
        "background arithmetic preregistration",
    )
    capture = mapping(registration.get("capture"), "capture")
    frozen = mapping(registration.get("frozenAnalysis"), "frozen analysis")
    forbidden_run_ids = capture.get("forbiddenOpenedRunIds")
    if (
        registration.get("backgroundArithmeticTomographyPreregistrationSchemaVersion")
        != 1
        or registration.get("classification")
        != "prospective diagnostic stage isolation"
        or capture.get("material") != "clear"
        or capture.get("appearance") != "light"
        or capture.get("direction") != "materialize"
        or capture.get("sampleIndex") != SAMPLE_INDEX
        or capture.get("capturedAppleFunctionUnmodified") is not False
        or capture.get("customStageInVertex") is not True
        or capture.get("traceNames") != list(TRACE_LAYOUTS)
        or not isinstance(forbidden_run_ids, list)
        or any(type(value) is not int for value in forbidden_run_ids)
        or run_id in forbidden_run_ids
    ):
        raise ValueError("background arithmetic preregistration differs")

    verified: JsonObject = {}
    for name, frozen_path in FROZEN_FILES.items():
        observed = sha256_file(frozen_path)
        if frozen.get(name) != observed:
            raise ValueError(
                f"frozen analysis hash differs for {frozen_path}: "
                f"{observed} != {frozen.get(name)}"
            )
        verified[name] = {"path": str(frozen_path), "sha256": observed}

    runtime = static_capture / "runtime.json"
    half_intrinsics = static_capture / "half-intrinsics.bin"
    external_files = {
        "staticRuntimeSha256": runtime,
        "halfIntrinsicTableSha256": half_intrinsics,
        "floatIntrinsicTableSha256": float_intrinsic_table,
        "squareSelectorArchiveSha256": square_selector_archive,
    }
    for name, frozen_path in external_files.items():
        if not frozen_path.is_file():
            raise ValueError(f"frozen analysis input is absent: {frozen_path}")
        observed = sha256_file(frozen_path)
        if frozen.get(name) != observed:
            raise ValueError(
                f"frozen input hash differs for {frozen_path}: "
                f"{observed} != {frozen.get(name)}"
            )
        verified[name] = {"path": str(frozen_path), "sha256": observed}
    return dict(registration), verified


def _sample_record(report: Mapping[str, Any]) -> Mapping[str, Any]:
    uniforms = mapping(report.get("dynamicBackgroundUniforms"), "dynamic uniforms")
    records = uniforms.get("records")
    if not isinstance(records, list):
        raise ValueError("dynamic background records are absent")
    matches = [
        mapping(record, "dynamic record")
        for record in records
        if isinstance(record, Mapping) and record.get("sampleIndex") == SAMPLE_INDEX
    ]
    if len(matches) != 1:
        raise ValueError(f"sample {SAMPLE_INDEX} is not unique")
    return matches[0]


def _trace_paths(root: Path, render: Mapping[str, Any]) -> dict[str, Path]:
    exact = mapping(render.get("exactPassReplay"), "exact pass replay")
    bundle = mapping(
        exact.get("backgroundArithmeticTrace"),
        "background arithmetic trace",
    )
    replays = bundle.get("replays")
    if (
        bundle.get("schemaVersion") != 1
        or bundle.get("executed") is not True
        or bundle.get("scope") != "sample-16-custom-metal-main-only"
        or bundle.get("capturedAppleFunctionUnmodified") is not False
        or bundle.get("customStageInVertex") is not True
        or bundle.get("classification")
        != "diagnostic custom-Metal arithmetic replay"
        or not isinstance(replays, list)
    ):
        raise ValueError("background arithmetic trace metadata differs")

    observed = {
        item.get("name"): mapping(item, "arithmetic trace wrapper")
        for item in replays
        if isinstance(item, Mapping)
    }
    if set(observed) != set(TRACE_LAYOUTS):
        raise ValueError("background arithmetic trace set differs")
    paths: dict[str, Path] = {}
    for name, (pixel_format, bytes_per_pixel) in TRACE_LAYOUTS.items():
        wrapper = observed[name]
        replay = mapping(wrapper.get("replay"), f"{name} replay")
        output = mapping(replay.get("output"), f"{name} output")
        filename = output.get("rawFile")
        expected_bytes = CAPTURE_WIDTH * CAPTURE_HEIGHT * bytes_per_pixel
        if (
            wrapper.get("pixelFormat") != pixel_format
            or replay.get("executed") is not True
            or replay.get("glassDrawCount") != 1
            or output.get("rawCapture") is not True
            or output.get("pixelFormat") != pixel_format
            or output.get("width") != CAPTURE_WIDTH
            or output.get("height") != CAPTURE_HEIGHT
            or output.get("rawBytes") != expected_bytes
            or not isinstance(filename, str)
        ):
            raise ValueError(f"background arithmetic trace layout differs: {name}")
        trace_path = root / filename
        if not trace_path.is_file() or trace_path.stat().st_size != expected_bytes:
            raise ValueError(f"background arithmetic trace file differs: {trace_path}")
        paths[name] = trace_path
    return paths


def _load_half(path: Path) -> HalfImage:
    values = np.fromfile(path, dtype="<u2")
    expected = CAPTURE_WIDTH * CAPTURE_HEIGHT * 4
    if values.size != expected:
        raise ValueError(f"{path} has {values.size} half words; expected {expected}")
    return values.reshape(CAPTURE_HEIGHT, CAPTURE_WIDTH, 4)


def _load_uint(path: Path) -> UIntImage:
    values = np.fromfile(path, dtype="<u4")
    expected = CAPTURE_WIDTH * CAPTURE_HEIGHT * 4
    if values.size != expected:
        raise ValueError(f"{path} has {values.size} words; expected {expected}")
    return values.reshape(CAPTURE_HEIGHT, CAPTURE_WIDTH, 4)


def _unpack_half_pairs(values: UIntImage) -> HalfImage:
    unpacked = np.empty((*values.shape[:2], values.shape[2] * 2), dtype=np.uint16)
    unpacked[..., 0::2] = values & np.uint32(0xFFFF)
    unpacked[..., 1::2] = values >> np.uint32(16)
    return unpacked


def _apply_scissor(values: HalfImage, scissor: tuple[int, int, int, int]) -> HalfImage:
    x, y, width, height = scissor
    if (
        x < 0
        or y < 0
        or width < 0
        or height < 0
        or x + width > CAPTURE_WIDTH
        or y + height > CAPTURE_HEIGHT
    ):
        raise ValueError(f"background scissor is invalid: {scissor}")
    result = np.zeros_like(values)
    result[y : y + height, x : x + width] = values[
        y : y + height,
        x : x + width,
    ]
    return result


def _compare_words(reference: HalfImage, candidate: HalfImage) -> JsonObject:
    if reference.shape != candidate.shape:
        raise ValueError(
            f"trace shapes differ: {reference.shape} != {candidate.shape}"
        )
    differing = reference != candidate
    differing_pixels = np.any(differing, axis=2)
    coordinates = np.argwhere(differing_pixels)
    examples = []
    for y, x in coordinates[:16]:
        channels = np.flatnonzero(differing[y, x])
        examples.append(
            {
                "x": int(x),
                "y": int(y),
                "channels": channels.astype(int).tolist(),
                "referenceHex": [
                    f"0x{int(value):04x}" for value in reference[y, x]
                ],
                "candidateHex": [
                    f"0x{int(value):04x}" for value in candidate[y, x]
                ],
            }
        )
    return {
        "comparedWords": int(reference.size),
        "mismatchedWords": int(np.count_nonzero(differing)),
        "mismatchedPixels": int(np.count_nonzero(differing_pixels)),
        "exact": not np.any(differing),
        "examples": examples,
    }


def analyze(
    dynamic_root: Path,
    *,
    run_id: int,
    static_capture: Path,
    float_intrinsic_table: Path,
    square_selector_archive: Path,
    preregistration: Path,
) -> JsonObject:
    registration, verified = _verify_preregistration(
        preregistration,
        run_id=run_id,
        static_capture=static_capture,
        float_intrinsic_table=float_intrinsic_table,
        square_selector_archive=square_selector_archive,
    )
    report_paths = _report_paths(dynamic_root)
    if len(report_paths) != 1:
        raise ValueError(f"expected one dynamic report, found {len(report_paths)}")
    report_path = report_paths[0]
    report = mapping(
        json.loads(report_path.read_text(encoding="utf-8")),
        "dynamic report",
    )
    material = str(report.get("material"))
    appearance = str(report.get("appearance"))
    if (material, appearance) != ("clear", "light"):
        raise ValueError(f"unexpected dynamic profile: {material}/{appearance}")
    fragment = GLASS_FRAGMENTS[material]
    record = _sample_record(report)
    render = mapping(record.get("render"), "dynamic render")
    if render.get("executed") is not True:
        raise ValueError("dynamic sample did not execute")

    main, shadow = _background_geometry(dict(render), fragment)
    mvp = _background_mvp(dict(render), fragment)
    profile_payload, _ = _uniform_payloads(dict(render), fragment)
    source = _source_texture(dict(render), fragment)
    background_scissor, highlight_scissor = _draw_scissors(
        dict(render),
        fragment,
    )
    prefix_path, prefix_reference = _glass_prefix_output(
        report_path.parent,
        dict(render),
    )
    trace_paths = _trace_paths(report_path.parent, render)

    selector_table = arithmetic.load_selector_table()
    square_calibration = SquareSelectorCalibration.load(square_selector_archive)
    quad = runtime_quad_from_vertices(
        main.vertices,
        name=f"{material}-{appearance}-sample-{SAMPLE_INDEX}",
    )
    selector_use = square_calibration.use_for(quad.case, selector_table)
    selectors = list(selector_table)
    selectors[selector_use.table_index] = selector_use.selected
    tile_start, coefficients = coefficient_table(quad, selector_table=selectors)
    slopes = slopes_bits(quad, selectors)

    comparisons: JsonObject = {}
    raw_diagnostics: JsonObject = {}
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
        renderer.set_draw_geometries(main=main, shadow=shadow)
        renderer.set_mvp_payload(mvp)
        renderer.set_profile_payload(profile_payload)
        renderer.set_source_mip_bgra(_raw_mip_levels(report_path.parent, source))
        renderer.set_destination_bgra_path(
            _pre_final_input(report_path.parent, dict(render))
        )
        renderer.set_draw_scissors(
            background=background_scissor,
            final_highlight=highlight_scissor,
        )
        renderer.set_interpolant_coefficients(
            coefficients,
            tile_start=tile_start,
            slope_bits=slopes,
        )

        local_sdf = _apply_scissor(
            renderer.render_numeric_trace(1),
            background_scissor,
        )
        comparisons["sdf"] = _compare_words(
            _load_half(trace_paths["sdf"]),
            local_sdf,
        )

        local_coverage = _apply_scissor(
            renderer.render_numeric_trace(8),
            background_scissor,
        )
        metal_coverage = (
            _load_uint(trace_paths["sdf-coverage"]) & np.uint32(0xFFFF)
        ).astype(np.uint16)
        comparisons["sdf-coverage"] = _compare_words(
            metal_coverage,
            local_coverage,
        )

        for trace_name, local_trace_numbers in (
            ("color-stages-a", (10, 11)),
            ("color-stages-b", (12, 13)),
        ):
            local_stages = np.concatenate(
                [
                    _apply_scissor(
                        renderer.render_numeric_trace(trace_number),
                        background_scissor,
                    )
                    for trace_number in local_trace_numbers
                ],
                axis=2,
            )
            comparisons[trace_name] = _compare_words(
                _unpack_half_pairs(_load_uint(trace_paths[trace_name])),
                local_stages,
            )

        local_final = _apply_scissor(
            renderer.render_numeric_trace(9),
            background_scissor,
        )
        comparisons["final-color"] = _compare_words(
            _load_half(trace_paths["final-color"]),
            local_final,
        )
        prefix_candidate = renderer.render()
        prefix_comparison = compare_images(prefix_reference, prefix_candidate).as_json()
        implementation = renderer.implementation

    for name in ("sdf-geometry", "sdf-oval", "sdf-float", "sdf-normal"):
        raw_diagnostics[name] = {
            "path": str(trace_paths[name]),
            "sha256": sha256_file(trace_paths[name]),
            "classification": "custom-Metal diagnostic with no local gate oracle",
        }
    first_divergent_stage = next(
        (
            name
            for name in COMPARISON_ORDER
            if not comparisons[name]["exact"]
        ),
        None,
    )
    return {
        "liquidGlassDynamicBackgroundArithmeticSchemaVersion": 1,
        "classification": "prospective diagnostic stage isolation",
        "runId": run_id,
        "dynamicArtifact": str(dynamic_root),
        "report": str(report_path),
        "sampleIndex": SAMPLE_INDEX,
        "profile": {"material": material, "appearance": appearance},
        "capturedAppleFunctionUnmodified": False,
        "customStageInVertex": True,
        "preregistration": {
            "path": str(preregistration),
            "contents": registration,
            "verifiedFiles": verified,
        },
        "implementation": implementation,
        "runtimeRaster": {
            "backgroundScissor": list(background_scissor),
            "tileStart": tile_start,
            "tileCount": int(coefficients.shape[1]),
            "slopeBits": [f"0x{value:08x}" for value in slopes],
            "selector": {
                "base": selector_use.base,
                "selected": selector_use.selected,
                "offset": selector_use.offset,
            },
        },
        "prefix": {
            "reference": str(prefix_path),
            "comparison": prefix_comparison,
        },
        "stageComparisons": comparisons,
        "firstDivergentStage": first_divergent_stage,
        "rawFloatDiagnostics": raw_diagnostics,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dynamic_root", type=Path)
    parser.add_argument("--run-id", type=int, required=True)
    parser.add_argument("--static-capture", type=Path, required=True)
    parser.add_argument("--float-intrinsic-table", type=Path, required=True)
    parser.add_argument("--square-selector-archive", type=Path, required=True)
    parser.add_argument("--preregistration", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    result = analyze(
        arguments.dynamic_root,
        run_id=arguments.run_id,
        static_capture=arguments.static_capture,
        float_intrinsic_table=arguments.float_intrinsic_table,
        square_selector_archive=arguments.square_selector_archive,
        preregistration=arguments.preregistration,
    )
    encoded = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if arguments.output is None:
        print(encoded, end="")
    else:
        arguments.output.write_text(encoded, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
