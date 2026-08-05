#!/usr/bin/env python3
"""Bit-gate dynamic Apple highlight raster and translucent source stages."""

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from apple_glass_reference_renderer import (
    CAPTURE_HEIGHT,
    CAPTURE_WIDTH,
    AppleGlassReferenceRenderer,
)
from liquid_glass_dynamic_capture import (
    EXPECTED_SAMPLE_INDICES,
    _highlight_geometry,
    _report_paths,
    _uniform_payloads,
)
from liquid_glass_dynamic_render_gate import (
    DYNAMIC_HIGHLIGHT_CONFIGURATION,
    _draw_scissors,
    _final_highlight_input,
    _highlight_quad,
)
from liquid_glass_post_glass_gate import sha256_file
from liquid_glass_profile_matrix import GLASS_FRAGMENTS
from liquid_glass_runtime_raster_coefficients import (
    axis_table,
    coefficient_table,
    slopes_bits,
)
from liquid_glass_shader_specialization import (
    load_specialized_exact_final_shader,
)
from liquid_glass_square_selector_calibration import (
    SELECTOR_COUNT,
    WIDTH_FIXED_LOWER,
    SquareSelectorCalibration,
    base_selector_use,
)

import raster_tile_selector_model as arithmetic


type JsonObject = dict[str, Any]
type HalfImage = NDArray[np.uint16]
type HalfPlane = NDArray[np.uint16]
type UIntImage = NDArray[np.uint32]

TRACE_SAMPLE_INDICES = (1, 12, 32)
EXPECTED_TOMOGRAPHY_CASES = {
    "asymmetric-constant-unit-alpha",
    "identity-rgb-destination-alpha",
    "identity-rgb-unit-alpha",
    "natural-rgb-unit-alpha",
    "permuted-rgb-unit-alpha",
    "unit-rgb-unit-alpha",
    "zero-rgb-unit-alpha",
}
EXPECTED_ALPHA_STAGE_CASES = {
    "positive-normal-x",
    "negative-normal-x",
    "positive-normal-y",
    "negative-normal-y",
    "normalized-normal-x",
    "normalized-normal-y",
    "original-directional",
    "shifted-scaled-distance",
    "leading-coverage",
    "original-coverage",
}


def mapping(value: object, name: str) -> JsonObject:
    if not isinstance(value, dict):
        raise ValueError(f"{name} is not an object")
    return value


def _raw_half_output(
    root: Path, record: JsonObject, name: str
) -> tuple[Path, HalfImage]:
    output = mapping(record.get("output"), f"{name} output")
    filename = output.get("rawFile")
    expected_words = CAPTURE_WIDTH * CAPTURE_HEIGHT * 4
    if (
        record.get("executed") is not True
        or output.get("rawCapture") is not True
        or output.get("pixelFormat") != 115
        or output.get("width") != CAPTURE_WIDTH
        or output.get("height") != CAPTURE_HEIGHT
        or output.get("rawBytes") != expected_words * 2
        or not isinstance(filename, str)
    ):
        raise ValueError(f"{name} RGBA16Float layout differs")
    path = root / filename
    words = np.fromfile(path, dtype="<u2")
    if words.size != expected_words:
        raise ValueError(f"{path} has {words.size} words; expected {expected_words}")
    return path, words.reshape(CAPTURE_HEIGHT, CAPTURE_WIDTH, 4)


def _highlight_trace(render: JsonObject) -> JsonObject:
    replay = mapping(render.get("exactPassReplay"), "exact pass replay")
    trace = mapping(replay.get("finalHighlightAlphaTrace"), "highlight trace")
    comparison = mapping(
        trace.get("capturedVsRebuiltBGRA8"),
        "same-format highlight comparison",
    )
    if (
        trace.get("schemaVersion") != 2
        or trace.get("capturedAppleFunctionUnmodified") is not True
        or trace.get("selectedLastA2XghfcDraw") is not True
        or comparison.get("compared") is not True
        or comparison.get("mismatchedByteCount") != 0
    ):
        raise ValueError("highlight trace contract differs")
    return trace


def _alpha_output(
    root: Path,
    record: JsonObject,
    name: str,
) -> tuple[Path, HalfPlane]:
    path, rgba = _raw_half_output(root, record, name)
    alpha = rgba[..., 0]
    if not (
        np.array_equal(alpha, rgba[..., 1]) and np.array_equal(alpha, rgba[..., 2])
    ):
        raise ValueError(f"Apple {name} does not contain three equal RGB channels")
    return path, alpha


def _alpha_reference(root: Path, trace: JsonObject) -> tuple[Path, HalfPlane]:
    exact = mapping(trace.get("exactHalfAlpha"), "exact half alpha")
    return _alpha_output(root, exact, "exact half alpha")


def _interpolant_reference(root: Path, trace: JsonObject) -> UIntImage:
    exact = mapping(trace.get("exactInterpolant"), "exact interpolant")
    output = mapping(exact.get("output"), "exact interpolant output")
    filename = output.get("rawFile")
    expected_words = CAPTURE_WIDTH * CAPTURE_HEIGHT * 4
    if (
        exact.get("executed") is not True
        or output.get("rawCapture") is not True
        or output.get("pixelFormat") != 123
        or output.get("width") != CAPTURE_WIDTH
        or output.get("height") != CAPTURE_HEIGHT
        or output.get("rawBytes") != expected_words * 4
        or not isinstance(filename, str)
    ):
        raise ValueError("exact interpolant RGBA32Uint layout differs")
    words = np.fromfile(root / filename, dtype="<u4")
    if words.size != expected_words:
        raise ValueError(
            f"{root / filename} has {words.size} words; expected {expected_words}"
        )
    return words.reshape(CAPTURE_HEIGHT, CAPTURE_WIDTH, 4)


def _custom_highlight_sdf_reference(
    root: Path,
    trace: JsonObject,
) -> tuple[Path, HalfImage] | None:
    untyped_diagnostics = trace.get("customHighlightSDFDiagnostics")
    if untyped_diagnostics is None:
        return None
    diagnostics = mapping(
        untyped_diagnostics,
        "custom highlight SDF diagnostics",
    )
    untyped_replays = diagnostics.get("replays")
    if (
        diagnostics.get("schemaVersion") != 1
        or diagnostics.get("executed") is not True
        or diagnostics.get("classification")
        != "diagnostic custom-Metal SDF replay"
        or diagnostics.get("capturedAppleFunctionUnmodified") is not False
        or diagnostics.get("customStageInVertex") is not True
        or not isinstance(untyped_replays, list)
    ):
        raise ValueError("custom highlight SDF diagnostic contract differs")
    replays = [
        mapping(record, "custom highlight SDF replay")
        for record in untyped_replays
    ]
    matches = [record for record in replays if record.get("name") == "sdf"]
    if len(matches) != 1 or matches[0].get("executed") is not True:
        raise ValueError("custom highlight SDF replay is absent")
    replay = mapping(matches[0].get("replay"), "custom highlight SDF replay")
    return _raw_half_output(root, replay, "custom highlight SDF")


def _alpha_stage_records(
    trace: JsonObject,
) -> tuple[list[tuple[str, list[object], JsonObject]], JsonObject]:
    zero_half4 = bytes(8).hex()
    diagnostics: list[tuple[str, list[object], JsonObject]] = [
        (
            "key-only",
            [{"recordOffset": 0xF0, "hex": zero_half4}],
            mapping(trace.get("exactKeyHalfAlpha"), "exact key half alpha"),
        ),
        (
            "fill-only",
            [{"recordOffset": 0xE8, "hex": zero_half4}],
            mapping(trace.get("exactFillHalfAlpha"), "exact fill half alpha"),
        ),
    ]
    tomography = mapping(trace.get("stageTomography"), "highlight tomography")
    untyped_cases = tomography.get("cases")
    if (
        tomography.get("schemaVersion") != 1
        or tomography.get("capturedAppleFunctionUnmodified") is not True
        or not isinstance(untyped_cases, list)
        or tomography.get("caseCount") != len(untyped_cases)
    ):
        raise ValueError("highlight tomography contract differs")
    cases = [mapping(case, "highlight tomography case") for case in untyped_cases]
    names = {case.get("name") for case in cases}
    if names != EXPECTED_ALPHA_STAGE_CASES:
        raise ValueError(f"highlight tomography cases differ: {sorted(names)}")
    for case in cases:
        name = case.get("name")
        edits = case.get("edits")
        if not isinstance(name, str) or not isinstance(edits, list) or not edits:
            raise ValueError("highlight tomography case metadata differs")
        diagnostics.append(
            (
                name,
                edits,
                mapping(case.get("replay"), f"{name} replay"),
            )
        )
    interpolant = mapping(trace.get("exactInterpolant"), "exact interpolant")
    interpolant_probe = {
        "executed": interpolant.get("executed") is True,
        "commandBufferStatus": interpolant.get("commandBufferStatus"),
        "reason": interpolant.get("reason"),
    }
    return diagnostics, interpolant_probe


def _packed_alpha_oracle(reference: HalfPlane) -> UIntImage:
    if reference.shape != (CAPTURE_HEIGHT, CAPTURE_WIDTH):
        raise ValueError(f"alpha oracle dimensions differ: {reference.shape}")
    packed = np.zeros((CAPTURE_HEIGHT, CAPTURE_WIDTH, 4), dtype=np.uint32)
    packed[..., 1] = reference.astype(np.uint32)
    return packed


def _alpha_comparison(reference: HalfPlane, candidate: HalfPlane) -> JsonObject:
    if reference.shape != candidate.shape:
        raise ValueError(
            f"alpha dimensions differ: {reference.shape} != {candidate.shape}"
        )
    mismatch = reference != candidate
    signed_delta = candidate.astype(np.int32) - reference.astype(np.int32)
    coordinates = np.argwhere(mismatch)
    bounds = (
        None
        if coordinates.size == 0
        else {
            "minimumX": int(coordinates[:, 1].min()),
            "minimumY": int(coordinates[:, 0].min()),
            "maximumX": int(coordinates[:, 1].max()),
            "maximumY": int(coordinates[:, 0].max()),
        }
    )
    nonzero = signed_delta[mismatch]
    first_mismatches = [
        {
            "x": int(x),
            "y": int(y),
            "appleHalfCode": int(reference[y, x]),
            "candidateHalfCode": int(candidate[y, x]),
            "candidateMinusAppleHalfCode": int(signed_delta[y, x]),
        }
        for y, x in coordinates[:32]
    ]
    histogram: list[JsonObject] = []
    if nonzero.size:
        values, counts = np.unique(nonzero, return_counts=True)
        order = np.argsort(counts, stable=True)[::-1][:12]
        histogram = [
            {
                "candidateMinusAppleHalfCode": int(values[index]),
                "count": int(counts[index]),
            }
            for index in order
        ]
    return {
        "exact": not bool(np.any(mismatch)),
        "wordCount": int(reference.size),
        "mismatchedWords": int(np.count_nonzero(mismatch)),
        "matchingWordFraction": float(1.0 - np.mean(mismatch)),
        "activeApplePixels": int(np.count_nonzero(reference)),
        "activeCandidatePixels": int(np.count_nonzero(candidate)),
        "activeMaskMismatchedPixels": int(
            np.count_nonzero((reference != 0) != (candidate != 0))
        ),
        "maximumHalfCodeDelta": int(np.abs(signed_delta).max(initial=0)),
        "mismatchBounds": bounds,
        "firstMismatches": first_mismatches,
        "mostCommonNonzeroDeltas": histogram,
    }


def _comparison(reference: HalfImage, candidate: HalfImage) -> JsonObject:
    if reference.shape != candidate.shape:
        raise ValueError(
            f"half image dimensions differ: {reference.shape} != {candidate.shape}"
        )
    mismatch = reference != candidate
    signed_delta = candidate.astype(np.int32) - reference.astype(np.int32)
    changed_pixels = np.any(mismatch, axis=2)
    coordinates = np.argwhere(changed_pixels)
    bounds = (
        None
        if coordinates.size == 0
        else {
            "minimumX": int(coordinates[:, 1].min()),
            "minimumY": int(coordinates[:, 0].min()),
            "maximumX": int(coordinates[:, 1].max()),
            "maximumY": int(coordinates[:, 0].max()),
        }
    )
    nonzero = signed_delta[mismatch]
    histogram: list[JsonObject] = []
    if nonzero.size:
        values, counts = np.unique(nonzero, return_counts=True)
        order = np.argsort(counts, stable=True)[::-1][:12]
        histogram = [
            {
                "candidateMinusAppleHalfCode": int(values[index]),
                "count": int(counts[index]),
            }
            for index in order
        ]
    channel_names = ("red", "green", "blue", "alpha")
    return {
        "exact": not bool(np.any(mismatch)),
        "wordCount": int(reference.size),
        "mismatchedWords": int(np.count_nonzero(mismatch)),
        "mismatchedPixels": int(np.count_nonzero(changed_pixels)),
        "matchingWordFraction": float(1.0 - np.mean(mismatch)),
        "maximumHalfCodeDelta": int(np.abs(signed_delta).max(initial=0)),
        "mismatchBounds": bounds,
        "mismatchedWordsByChannel": {
            name: int(np.count_nonzero(mismatch[..., channel]))
            for channel, name in enumerate(channel_names)
        },
        "mostCommonNonzeroDeltas": histogram,
    }


def _decode_trace_words(encoded: HalfImage) -> UIntImage:
    values = encoded.view(np.float16).astype(np.float32)
    octets = np.rint(values * 255.0).astype(np.uint32)
    if np.any(octets > 255):
        raise ValueError("encoded trace contains an out-of-range byte")
    return (
        octets[..., 0]
        | (octets[..., 1] << 8)
        | (octets[..., 2] << 16)
        | (octets[..., 3] << 24)
    )


def _patched_payload(payload: bytes, edits: list[object], name: str) -> bytes:
    result = bytearray(payload)
    occupied: set[int] = set()
    for untyped_edit in edits:
        edit = mapping(untyped_edit, f"{name} edit")
        offset = edit.get("recordOffset")
        hexadecimal = edit.get("hex")
        if not isinstance(offset, int) or not isinstance(hexadecimal, str):
            raise ValueError(f"malformed {name} edit")
        patch = bytes.fromhex(hexadecimal)
        offsets = set(range(offset, offset + len(patch)))
        if not patch or offset < 0 or offset + len(patch) > len(result):
            raise ValueError(f"{name} edit exceeds the highlight payload")
        if occupied & offsets:
            raise ValueError(f"{name} edits overlap")
        occupied |= offsets
        result[offset : offset + len(patch)] = patch
    return bytes(result)


def _tomography_records(trace: JsonObject) -> tuple[JsonObject, list[JsonObject]]:
    compositor = mapping(trace.get("exactCompositorTrace"), "compositor trace")
    tomography = mapping(compositor.get("stageTomography"), "stage tomography")
    untyped_records = tomography.get("cases")
    if (
        compositor.get("schemaVersion") != 2
        or compositor.get("executed") is not True
        or compositor.get("capturedAppleFunctionUnmodified") is not True
        or tomography.get("schemaVersion") != 1
        or tomography.get("executed") is not True
        or tomography.get("capturedAppleFunctionUnmodified") is not True
        or not isinstance(untyped_records, list)
        or tomography.get("caseCount") != len(untyped_records)
    ):
        raise ValueError("translucent compositor tomography contract differs")
    records = [mapping(record, "tomography case") for record in untyped_records]
    names = {record.get("name") for record in records}
    if names != EXPECTED_TOMOGRAPHY_CASES:
        raise ValueError(f"tomography cases differ: {sorted(names)}")
    return compositor, records


def run_gate(
    dynamic_root: Path,
    *,
    static_capture: Path,
    float_intrinsic_table: Path,
    coordinate_source: str = "generated-axis",
    square_selector_archive: Path | None = None,
) -> JsonObject:
    if coordinate_source not in {"generated-axis", "captured-interpolant"}:
        raise ValueError(f"unsupported coordinate source: {coordinate_source}")
    reports = _report_paths(dynamic_root)
    if len(reports) != 1:
        raise ValueError(f"expected one dynamic report under {dynamic_root}")
    report_path = reports[0]
    root = report_path.parent
    report = json.loads(report_path.read_text(encoding="utf-8"))
    material = str(report.get("material"))
    try:
        fragment = GLASS_FRAGMENTS[material]
    except KeyError as error:
        raise ValueError(f"unsupported dynamic material: {material}") from error
    uniforms = mapping(report.get("dynamicBackgroundUniforms"), "dynamic uniforms")
    untyped_records = uniforms.get("records")
    if not isinstance(untyped_records, list):
        raise ValueError("dynamic records are absent")
    records = [mapping(record, "dynamic record") for record in untyped_records]
    if [record.get("sampleIndex") for record in records] != list(
        EXPECTED_SAMPLE_INDICES
    ):
        raise ValueError("dynamic sample indices differ")
    trace_records = {
        int(record["sampleIndex"]): record
        for record in records
        if isinstance(
            mapping(record.get("render"), "dynamic render").get("exactPassReplay"), dict
        )
        and mapping(record["render"]["exactPassReplay"], "exact pass replay").get(
            "finalHighlightAlphaTrace"
        )
        is not None
    }
    if tuple(sorted(trace_records)) != TRACE_SAMPLE_INDICES:
        raise ValueError(f"highlight trace samples differ: {sorted(trace_records)}")

    endpoint_render = mapping(trace_records[32].get("render"), "endpoint render")
    endpoint_trace = _highlight_trace(endpoint_render)
    _, endpoint_alpha = _alpha_reference(root, endpoint_trace)
    selector_table = list(arithmetic.load_selector_table())
    square_calibration = (
        SquareSelectorCalibration.load(square_selector_archive)
        if square_selector_archive is not None
        else None
    )
    shader_source = load_specialized_exact_final_shader(
        coordinate_mode=4,
        use_apple_interpolant_trace=int(
            coordinate_source == "captured-interpolant"
        ),
        dynamic_uniforms=frozenset({"UseAppleSdfTrace"}),
    )
    alpha_results: JsonObject = {}
    alpha_anchor_sweep: JsonObject = {}
    alpha_stage_results: JsonObject = {}
    interpolant_probes: JsonObject = {}
    normalization_sweep: JsonObject = {}
    normalized_coordinate_sweep: JsonObject = {}
    division_sweep: JsonObject = {}
    tomography_results: JsonObject = {}
    arithmetic_sweep: JsonObject = {}
    source_construction_sweep: JsonObject = {}
    normal_field_diagnostics: JsonObject = {}
    custom_sdf_diagnostics: JsonObject = {}
    internal_stage_traces: JsonObject = {}
    sdf_arithmetic_sweep: JsonObject = {}
    derivative_sweep: JsonObject = {}
    coverage_sweep: JsonObject = {}
    mix_sweep: JsonObject = {}
    sdf_squared_ulp_sweep: JsonObject = {}
    sdf_distance_ulp_sweep: JsonObject = {}
    raster_selectors: JsonObject = {}

    with AppleGlassReferenceRenderer(
        static_capture,
        fragment_shader_source=shader_source,
        intrinsic_table=float_intrinsic_table,
        half_intrinsic_table=static_capture / "half-intrinsics.bin",
        highlight_half_stage_data=_packed_alpha_oracle(endpoint_alpha),
        load_interpolant_trace=False,
        load_interpolant_axis_trace=False,
        load_diagnostic_traces=False,
    ) as renderer:
        for uniform, value in DYNAMIC_HIGHLIGHT_CONFIGURATION.items():
            renderer.program[uniform].value = value

        endpoint_context: tuple[bytes, HalfImage, JsonObject] | None = None
        for sample_index in TRACE_SAMPLE_INDICES:
            record = trace_records[sample_index]
            render = mapping(record.get("render"), f"sample {sample_index} render")
            trace = _highlight_trace(render)
            alpha_path, alpha_reference = _alpha_reference(root, trace)
            geometry = _highlight_geometry(render)
            profile_payload, highlight_payload = _uniform_payloads(render, fragment)
            _, highlight_scissor = _draw_scissors(render, fragment)
            highlight_input_path, highlight_input = _final_highlight_input(root, render)
            quad = _highlight_quad(
                geometry,
                name=f"{material}-dynamic-sample-{sample_index}-highlight",
            )
            selector_use = (
                square_calibration.use_for(quad.case, selector_table)
                if square_calibration is not None
                else base_selector_use(quad.case, selector_table)
            )
            sample_selector_table = selector_table.copy()
            sample_selector_table[selector_use.table_index] = selector_use.selected
            tile_start, coefficients = coefficient_table(
                quad,
                selector_table=sample_selector_table,
            )
            axis_start, axes = axis_table(
                quad,
                selector_table=sample_selector_table,
                helper_lane_halo=2,
            )
            selected_slope_bits = slopes_bits(quad, sample_selector_table)
            raster_selectors[str(sample_index)] = {
                "widthFixed": quad.case.widthFixed,
                "fractionalTableIndex": selector_use.table_index,
                "base": selector_use.base,
                "selected": selector_use.selected,
                "offset": selector_use.offset,
                "squareCalibrationUsed": square_calibration is not None,
            }
            renderer.set_final_highlight_geometry(geometry)
            renderer.set_profile_payload(profile_payload)
            renderer.set_destination_bgra_path(highlight_input_path)
            renderer.set_draw_scissors(
                background=(0, 0, CAPTURE_WIDTH, CAPTURE_HEIGHT),
                final_highlight=highlight_scissor,
            )
            renderer.set_interpolant_coefficients(
                coefficients,
                tile_start=tile_start,
                slope_bits=selected_slope_bits,
            )
            if coordinate_source == "generated-axis":
                renderer.set_interpolant_axis_table(
                    axes,
                    start=axis_start,
                )
            else:
                renderer.set_interpolant_trace_data(
                    _interpolant_reference(root, trace)
                )
            renderer.program["UseAppleHighlightAlphaTrace"].value = 0
            candidate = renderer.render_final_highlight_half(
                uniform_payload=highlight_payload
            )
            diagnostics, interpolant_probe = _alpha_stage_records(trace)
            sample_stage_results: JsonObject = {}
            normal_stage_outputs: dict[str, tuple[HalfPlane, HalfPlane]] = {}
            normalization_inputs: dict[str, tuple[HalfPlane, bytes]] = {
                "natural": (alpha_reference, highlight_payload),
            }
            for name, edits, output_record in diagnostics:
                reference_path, reference = _alpha_output(
                    root,
                    output_record,
                    name,
                )
                diagnostic_payload = _patched_payload(
                    highlight_payload,
                    edits,
                    name,
                )
                normalization_inputs[name] = (reference, diagnostic_payload)
                diagnostic_candidate = renderer.render_final_highlight_half(
                    uniform_payload=diagnostic_payload
                )
                if name in {
                    "positive-normal-x",
                    "negative-normal-x",
                    "positive-normal-y",
                    "negative-normal-y",
                }:
                    normal_stage_outputs[name] = (
                        reference,
                        diagnostic_candidate[..., 0],
                    )
                sample_stage_results[name] = {
                    "applePath": str(reference_path),
                    "appleSHA256": sha256_file(reference_path),
                    "comparison": _alpha_comparison(
                        reference,
                        diagnostic_candidate[..., 0],
                    ),
                }
            alpha_stage_results[str(sample_index)] = sample_stage_results
            interpolant_probes[str(sample_index)] = interpolant_probe
            sample_normalization_sweep: JsonObject = {}
            for normalize_mode in range(6):
                renderer.program["HighlightNormalizeMode"].value = normalize_mode
                comparisons = {
                    name: _alpha_comparison(
                        reference,
                        renderer.render_final_highlight_half(
                            uniform_payload=payload
                        )[..., 0],
                    )
                    for name, (reference, payload) in normalization_inputs.items()
                }
                sample_normalization_sweep[str(normalize_mode)] = {
                    "normalizeMode": normalize_mode,
                    "mismatchedWords": sum(
                        int(comparison["mismatchedWords"])
                        for comparison in comparisons.values()
                    ),
                    "exactCases": sum(
                        bool(comparison["exact"])
                        for comparison in comparisons.values()
                    ),
                    "caseCount": len(comparisons),
                    "comparisons": comparisons,
                }
            normalization_sweep[str(sample_index)] = sample_normalization_sweep
            renderer.program["HighlightNormalizeMode"].value = (
                DYNAMIC_HIGHLIGHT_CONFIGURATION["HighlightNormalizeMode"]
            )
            sample_normalized_coordinate_sweep: JsonObject = {}
            for normalized_coordinate_mode in range(9):
                renderer.program["HighlightNormalizedCoordinateMode"].value = (
                    normalized_coordinate_mode
                )
                comparisons = {
                    name: _alpha_comparison(
                        reference,
                        renderer.render_final_highlight_half(
                            uniform_payload=payload
                        )[..., 0],
                    )
                    for name, (reference, payload) in normalization_inputs.items()
                }
                sample_normalized_coordinate_sweep[
                    str(normalized_coordinate_mode)
                ] = {
                    "normalizedCoordinateMode": normalized_coordinate_mode,
                    "mismatchedWords": sum(
                        int(comparison["mismatchedWords"])
                        for comparison in comparisons.values()
                    ),
                    "exactCases": sum(
                        bool(comparison["exact"])
                        for comparison in comparisons.values()
                    ),
                    "caseCount": len(comparisons),
                    "comparisons": comparisons,
                }
            normalized_coordinate_sweep[str(sample_index)] = (
                sample_normalized_coordinate_sweep
            )
            renderer.program["HighlightNormalizedCoordinateMode"].value = 0
            sample_division_sweep: JsonObject = {}
            for division_mode in range(6):
                renderer.program["HighlightFloatDivisionMode"].value = division_mode
                comparisons = {
                    name: _alpha_comparison(
                        reference,
                        renderer.render_final_highlight_half(
                            uniform_payload=payload
                        )[..., 0],
                    )
                    for name, (reference, payload) in normalization_inputs.items()
                }
                sample_division_sweep[str(division_mode)] = {
                    "divisionMode": division_mode,
                    "mismatchedWords": sum(
                        int(comparison["mismatchedWords"])
                        for comparison in comparisons.values()
                    ),
                    "exactCases": sum(
                        bool(comparison["exact"])
                        for comparison in comparisons.values()
                    ),
                    "caseCount": len(comparisons),
                    "comparisons": comparisons,
                }
            division_sweep[str(sample_index)] = sample_division_sweep
            renderer.program["HighlightFloatDivisionMode"].value = (
                DYNAMIC_HIGHLIGHT_CONFIGURATION["HighlightFloatDivisionMode"]
            )
            sample_sdf_arithmetic_sweep: JsonObject = {}
            for sdf_arithmetic_mode in range(4):
                renderer.program["HighlightSdfArithmeticMode"].value = (
                    sdf_arithmetic_mode
                )
                comparisons = {
                    name: _alpha_comparison(
                        reference,
                        renderer.render_final_highlight_half(
                            uniform_payload=payload
                        )[..., 0],
                    )
                    for name, (reference, payload) in normalization_inputs.items()
                }
                sample_sdf_arithmetic_sweep[str(sdf_arithmetic_mode)] = {
                    "sdfArithmeticMode": sdf_arithmetic_mode,
                    "mismatchedWords": sum(
                        int(comparison["mismatchedWords"])
                        for comparison in comparisons.values()
                    ),
                    "exactCases": sum(
                        bool(comparison["exact"])
                        for comparison in comparisons.values()
                    ),
                    "caseCount": len(comparisons),
                    "comparisons": comparisons,
                }
            sdf_arithmetic_sweep[str(sample_index)] = (
                sample_sdf_arithmetic_sweep
            )
            renderer.program["HighlightSdfArithmeticMode"].value = 0
            sample_derivative_sweep: JsonObject = {}
            for derivative_mode in range(5):
                renderer.program["HighlightDerivativeMode"].value = derivative_mode
                comparisons = {
                    name: _alpha_comparison(
                        reference,
                        renderer.render_final_highlight_half(
                            uniform_payload=payload
                        )[..., 0],
                    )
                    for name, (reference, payload) in normalization_inputs.items()
                }
                sample_derivative_sweep[str(derivative_mode)] = {
                    "derivativeMode": derivative_mode,
                    "mismatchedWords": sum(
                        int(comparison["mismatchedWords"])
                        for comparison in comparisons.values()
                    ),
                    "exactCases": sum(
                        bool(comparison["exact"])
                        for comparison in comparisons.values()
                    ),
                    "caseCount": len(comparisons),
                    "comparisons": comparisons,
                }
            derivative_sweep[str(sample_index)] = sample_derivative_sweep
            renderer.program["HighlightDerivativeMode"].value = (
                DYNAMIC_HIGHLIGHT_CONFIGURATION["HighlightDerivativeMode"]
            )
            sample_coverage_sweep: JsonObject = {}
            for coverage_mode in range(3):
                renderer.program["HighlightCoverageArithmeticMode"].value = (
                    coverage_mode
                )
                comparison = _alpha_comparison(
                    alpha_reference,
                    renderer.render_final_highlight_half(
                        uniform_payload=highlight_payload
                    )[..., 0],
                )
                sample_coverage_sweep[str(coverage_mode)] = {
                    "coverageArithmeticMode": coverage_mode,
                    "comparison": comparison,
                }
            coverage_sweep[str(sample_index)] = sample_coverage_sweep
            renderer.program["HighlightCoverageArithmeticMode"].value = (
                DYNAMIC_HIGHLIGHT_CONFIGURATION[
                    "HighlightCoverageArithmeticMode"
                ]
            )
            sample_mix_sweep: JsonObject = {}
            for mix_mode in range(3):
                renderer.program["HighlightMixMode"].value = mix_mode
                comparison = _alpha_comparison(
                    alpha_reference,
                    renderer.render_final_highlight_half(
                        uniform_payload=highlight_payload
                    )[..., 0],
                )
                sample_mix_sweep[str(mix_mode)] = {
                    "mixMode": mix_mode,
                    "comparison": comparison,
                }
            mix_sweep[str(sample_index)] = sample_mix_sweep
            renderer.program["HighlightMixMode"].value = 0
            sample_sdf_squared_ulp_sweep: JsonObject = {}
            for squared_ulp_bias in range(-3, 4):
                renderer.program["HighlightSdfSquaredUlpBias"].value = (
                    squared_ulp_bias
                )
                comparisons = {
                    name: _alpha_comparison(
                        reference,
                        renderer.render_final_highlight_half(
                            uniform_payload=payload
                        )[..., 0],
                    )
                    for name, (reference, payload) in normalization_inputs.items()
                }
                sample_sdf_squared_ulp_sweep[str(squared_ulp_bias)] = {
                    "squaredUlpBias": squared_ulp_bias,
                    "mismatchedWords": sum(
                        int(comparison["mismatchedWords"])
                        for comparison in comparisons.values()
                    ),
                    "exactCases": sum(
                        bool(comparison["exact"])
                        for comparison in comparisons.values()
                    ),
                    "caseCount": len(comparisons),
                    "comparisons": comparisons,
                }
            sdf_squared_ulp_sweep[str(sample_index)] = (
                sample_sdf_squared_ulp_sweep
            )
            renderer.program["HighlightSdfSquaredUlpBias"].value = 0
            sample_sdf_distance_ulp_sweep: JsonObject = {}
            for distance_ulp_bias in range(-3, 4):
                renderer.program["HighlightSdfDistanceUlpBias"].value = (
                    distance_ulp_bias
                )
                comparison = _alpha_comparison(
                    alpha_reference,
                    renderer.render_final_highlight_half(
                        uniform_payload=highlight_payload
                    )[..., 0],
                )
                sample_sdf_distance_ulp_sweep[str(distance_ulp_bias)] = {
                    "distanceUlpBias": distance_ulp_bias,
                    "comparison": comparison,
                }
            sdf_distance_ulp_sweep[str(sample_index)] = (
                sample_sdf_distance_ulp_sweep
            )
            renderer.program["HighlightSdfDistanceUlpBias"].value = 0
            required_normal_stages = {
                "positive-normal-x",
                "negative-normal-x",
                "positive-normal-y",
                "negative-normal-y",
            }
            if set(normal_stage_outputs) != required_normal_stages:
                raise RuntimeError("signed-normal stage outputs are incomplete")
            signed_normals: dict[str, tuple[HalfPlane, HalfPlane]] = {}
            for axis in ("x", "y"):
                positive = normal_stage_outputs[f"positive-normal-{axis}"]
                negative = normal_stage_outputs[f"negative-normal-{axis}"]
                signed_normals[axis] = tuple(
                    np.subtract(
                        positive[index].view(np.float16),
                        negative[index].view(np.float16),
                        dtype=np.float16,
                    ).view(np.uint16)
                    for index in range(2)
                )
            candidate_sdf = renderer.render_final_highlight_half(
                uniform_payload=highlight_payload,
                trace_mode=1,
            )
            custom_sdf = _custom_highlight_sdf_reference(root, trace)
            if custom_sdf is not None:
                custom_sdf_path, custom_sdf_words = custom_sdf
                renderer.set_sdf_trace_data(custom_sdf_words)
                renderer.program["UseAppleSdfTrace"].value = 1
                try:
                    custom_sdf_alpha_comparisons = {
                        name: _alpha_comparison(
                            reference,
                            renderer.render_final_highlight_half(
                                uniform_payload=payload
                            )[..., 0],
                        )
                        for name, (reference, payload) in (
                            normalization_inputs.items()
                        )
                    }
                finally:
                    renderer.program["UseAppleSdfTrace"].value = 0
                custom_sdf_diagnostics[str(sample_index)] = {
                    "classification": "diagnostic custom-Metal SDF replay",
                    "capturedAppleFunctionUnmodified": False,
                    "path": str(custom_sdf_path),
                    "sha256": sha256_file(custom_sdf_path),
                    "candidateSdfChannels": {
                        "distance": _alpha_comparison(
                            custom_sdf_words[..., 0],
                            candidate_sdf[..., 0],
                        ),
                        "normalX": _alpha_comparison(
                            custom_sdf_words[..., 1],
                            candidate_sdf[..., 1],
                        ),
                        "normalY": _alpha_comparison(
                            custom_sdf_words[..., 2],
                            candidate_sdf[..., 2],
                        ),
                    },
                    "alphaComparisons": custom_sdf_alpha_comparisons,
                    "alphaMismatchedWords": sum(
                        int(comparison["mismatchedWords"])
                        for comparison in custom_sdf_alpha_comparisons.values()
                    ),
                    "alphaExactCases": sum(
                        bool(comparison["exact"])
                        for comparison in custom_sdf_alpha_comparisons.values()
                    ),
                    "alphaCaseCount": len(custom_sdf_alpha_comparisons),
                }
            combined_mismatch = (
                signed_normals["x"][0] != signed_normals["x"][1]
            ) | (signed_normals["y"][0] != signed_normals["y"][1])
            first_coordinates = np.argwhere(combined_mismatch)[:64]
            normal_field_diagnostics[str(sample_index)] = {
                "x": _alpha_comparison(*signed_normals["x"]),
                "y": _alpha_comparison(*signed_normals["y"]),
                "appleVsCandidateSdfX": _alpha_comparison(
                    signed_normals["x"][0],
                    candidate_sdf[..., 1],
                ),
                "appleVsCandidateSdfY": _alpha_comparison(
                    signed_normals["y"][0],
                    candidate_sdf[..., 2],
                ),
                "candidateSdfVsPostNormalizeX": _alpha_comparison(
                    signed_normals["x"][1],
                    candidate_sdf[..., 1],
                ),
                "candidateSdfVsPostNormalizeY": _alpha_comparison(
                    signed_normals["y"][1],
                    candidate_sdf[..., 2],
                ),
                "combinedMismatchedPixels": int(
                    np.count_nonzero(combined_mismatch)
                ),
                "firstMismatches": [
                    {
                        "x": int(x),
                        "y": int(y),
                        "appleNormalXHalfCode": int(signed_normals["x"][0][y, x]),
                        "candidateNormalXHalfCode": int(
                            signed_normals["x"][1][y, x]
                        ),
                        "appleNormalYHalfCode": int(signed_normals["y"][0][y, x]),
                        "candidateNormalYHalfCode": int(
                            signed_normals["y"][1][y, x]
                        ),
                        "candidateSdfDistanceHalfCode": int(candidate_sdf[y, x, 0]),
                        "candidateSdfNormalXHalfCode": int(candidate_sdf[y, x, 1]),
                        "candidateSdfNormalYHalfCode": int(candidate_sdf[y, x, 2]),
                        "horizontalReflection": {
                            "x": CAPTURE_WIDTH - 1 - int(x),
                            "appleNormalXHalfCode": int(
                                signed_normals["x"][0][
                                    y, CAPTURE_WIDTH - 1 - x
                                ]
                            ),
                            "candidateNormalXHalfCode": int(
                                signed_normals["x"][1][
                                    y, CAPTURE_WIDTH - 1 - x
                                ]
                            ),
                            "candidateSdfNormalXHalfCode": int(
                                candidate_sdf[y, CAPTURE_WIDTH - 1 - x, 1]
                            ),
                        },
                        "verticalReflection": {
                            "y": CAPTURE_HEIGHT - 1 - int(y),
                            "appleNormalYHalfCode": int(
                                signed_normals["y"][0][
                                    CAPTURE_HEIGHT - 1 - y, x
                                ]
                            ),
                            "candidateNormalYHalfCode": int(
                                signed_normals["y"][1][
                                    CAPTURE_HEIGHT - 1 - y, x
                                ]
                            ),
                            "candidateSdfNormalYHalfCode": int(
                                candidate_sdf[CAPTURE_HEIGHT - 1 - y, x, 2]
                            ),
                        },
                    }
                    for y, x in first_coordinates
                ],
            }
            mismatch_coordinates = np.argwhere(
                alpha_reference != candidate[..., 0]
            )
            if mismatch_coordinates.size:
                trace_words = {
                    mode: _decode_trace_words(
                        renderer.render_final_highlight_half(
                            uniform_payload=highlight_payload,
                            trace_mode=mode,
                        )
                    )
                    for mode in range(4, 19)
                }
                internal_stage_traces[str(sample_index)] = [
                    {
                        "x": int(x),
                        "y": int(y),
                        "appleAlphaHalfCode": int(alpha_reference[y, x]),
                        "candidateAlphaHalfCode": int(candidate[y, x, 0]),
                        "candidateSdfHalfCodes": [
                            int(value) for value in candidate_sdf[y, x, :3]
                        ],
                        "candidateStages": {
                            str(mode): {
                                "wordHex": f"0x{int(words[y, x]):08x}",
                                "float": float(
                                    np.asarray(
                                        [words[y, x]], dtype=np.uint32
                                    ).view(np.float32)[0]
                                ),
                            }
                            for mode, words in trace_words.items()
                        },
                        "appleTomographyHalfCodes": {
                            name: int(reference[y, x])
                            for name, (reference, _) in (
                                normalization_inputs.items()
                            )
                            if name != "natural"
                        },
                    }
                    for y, x in mismatch_coordinates
                ]
            else:
                internal_stage_traces[str(sample_index)] = []
            if sample_index == 1:
                for encoded_policy in range(16):
                    flat_policy = tuple(
                        bool(encoded_policy & (1 << index)) for index in range(4)
                    )
                    anchor_policy = (
                        (flat_policy[0], flat_policy[1]),
                        (flat_policy[2], flat_policy[3]),
                    )
                    policy_name = "".join("H" if high else "L" for high in flat_policy)
                    alternate_start, alternate_coefficients = coefficient_table(
                        quad,
                        selector_table=sample_selector_table,
                        anchor_high_by_primitive_axis=anchor_policy,
                    )
                    renderer.set_interpolant_coefficients(
                        alternate_coefficients,
                        tile_start=alternate_start,
                        slope_bits=slopes_bits(quad, sample_selector_table),
                    )
                    alternate = renderer.render_final_highlight_half(
                        uniform_payload=highlight_payload
                    )
                    alpha_anchor_sweep[policy_name] = {
                        "primitive0": {
                            "x": "high" if flat_policy[0] else "low",
                            "y": "high" if flat_policy[1] else "low",
                        },
                        "primitive1": {
                            "x": "high" if flat_policy[2] else "low",
                            "y": "high" if flat_policy[3] else "low",
                        },
                        "comparison": _alpha_comparison(
                            alpha_reference,
                            alternate[..., 0],
                        ),
                    }
                renderer.set_interpolant_coefficients(
                    coefficients,
                    tile_start=tile_start,
                    slope_bits=slopes_bits(quad, sample_selector_table),
                )
            orientation_comparisons = {
                "capturedRows": _alpha_comparison(
                    alpha_reference,
                    candidate[..., 0],
                ),
                "flippedRows": _alpha_comparison(
                    np.flipud(alpha_reference),
                    candidate[..., 0],
                ),
                "flippedColumns": _alpha_comparison(
                    np.fliplr(alpha_reference),
                    candidate[..., 0],
                ),
                "rotated180": _alpha_comparison(
                    np.flip(alpha_reference, axis=(0, 1)),
                    candidate[..., 0],
                ),
                "rotated90Counterclockwise": _alpha_comparison(
                    np.rot90(alpha_reference, 1),
                    candidate[..., 0],
                ),
                "rotated90Clockwise": _alpha_comparison(
                    np.rot90(alpha_reference, -1),
                    candidate[..., 0],
                ),
                "transposed": _alpha_comparison(
                    np.transpose(alpha_reference),
                    candidate[..., 0],
                ),
                "transverse": _alpha_comparison(
                    np.flip(np.transpose(alpha_reference), axis=(0, 1)),
                    candidate[..., 0],
                ),
            }
            alpha_results[str(sample_index)] = {
                "remaining": record.get("remaining"),
                "diagonal": "ascending" if quad.ascendingDiagonal else "descending",
                "applePath": str(alpha_path),
                "appleSHA256": sha256_file(alpha_path),
                "comparison": orientation_comparisons["capturedRows"],
                "orientationDiagnostics": orientation_comparisons,
            }
            if sample_index == 32:
                endpoint_context = (highlight_payload, highlight_input, trace)

        if endpoint_context is None:
            raise RuntimeError("endpoint highlight context was not retained")
        endpoint_payload, endpoint_input, trace = endpoint_context
        compositor, tomography = _tomography_records(trace)
        exact_composite_path, exact_composite = _raw_half_output(
            root,
            mapping(compositor.get("exactHalfComposite"), "exact half composite"),
            "exact half composite",
        )
        renderer.program["UseAppleHighlightAlphaTrace"].value = 1
        natural_candidate = renderer.render_final_highlight_composite_half_over(
            endpoint_input,
            final_highlight_payload=endpoint_payload,
        )
        tomography_results["natural"] = {
            "applePath": str(exact_composite_path),
            "appleSHA256": sha256_file(exact_composite_path),
            "comparison": _comparison(exact_composite, natural_candidate),
        }
        sweep_inputs: dict[str, tuple[HalfImage, bytes]] = {
            "natural": (exact_composite, endpoint_payload),
        }
        for case in tomography:
            name = case.get("name")
            edits = case.get("edits")
            replay = mapping(case.get("replay"), f"{name} replay")
            if not isinstance(name, str) or not isinstance(edits, list):
                raise ValueError("tomography case metadata differs")
            reference_path, reference = _raw_half_output(root, replay, name)
            candidate = renderer.render_final_highlight_composite_half_over(
                endpoint_input,
                final_highlight_payload=(
                    patched_payload := _patched_payload(
                        endpoint_payload,
                        edits,
                        name,
                    )
                ),
            )
            if name == "identity-rgb-destination-alpha":
                sweep_inputs[name] = (reference, patched_payload)
            tomography_results[name] = {
                "applePath": str(reference_path),
                "appleSHA256": sha256_file(reference_path),
                "comparison": _comparison(reference, candidate),
            }

        if set(sweep_inputs) != {"natural", "identity-rgb-destination-alpha"}:
            raise RuntimeError("arithmetic sweep inputs are incomplete")
        for destination_mode in range(7):
            renderer.program[
                "HighlightDestinationDivisionMode"
            ].value = destination_mode
            for source_mode in range(5):
                renderer.program["HighlightSourceDivisionMode"].value = source_mode
                comparisons: JsonObject = {}
                for name, (reference, payload) in sweep_inputs.items():
                    candidate = renderer.render_final_highlight_composite_half_over(
                        endpoint_input,
                        final_highlight_payload=payload,
                    )
                    comparisons[name] = _comparison(reference, candidate)
                arithmetic_sweep[
                    f"destination-{destination_mode}-source-{source_mode}"
                ] = {
                    "destinationDivisionMode": destination_mode,
                    "sourceDivisionMode": source_mode,
                    "comparisons": comparisons,
                }
        renderer.program["HighlightDestinationDivisionMode"].value = 0
        renderer.program[
            "HighlightSourceDivisionMode"
        ].value = DYNAMIC_HIGHLIGHT_CONFIGURATION["HighlightSourceDivisionMode"]
        for construction_mode in range(7):
            renderer.program[
                "HighlightSourceConstructionMode"
            ].value = construction_mode
            comparisons = {}
            for name, (reference, payload) in sweep_inputs.items():
                candidate = renderer.render_final_highlight_composite_half_over(
                    endpoint_input,
                    final_highlight_payload=payload,
                )
                comparisons[name] = _comparison(reference, candidate)
            source_construction_sweep[str(construction_mode)] = {
                "sourceConstructionMode": construction_mode,
                "comparisons": comparisons,
            }
        renderer.program[
            "HighlightSourceConstructionMode"
        ].value = DYNAMIC_HIGHLIGHT_CONFIGURATION["HighlightSourceConstructionMode"]
        implementation = renderer.implementation

    alpha_exact = all(
        mapping(record, name)["comparison"]["exact"]
        for name, record in alpha_results.items()
    )
    tomography_exact = all(
        mapping(record, name)["comparison"]["exact"]
        for name, record in tomography_results.items()
    )
    alpha_stages_exact = all(
        mapping(record, name)["comparison"]["exact"]
        for sample, sample_records in alpha_stage_results.items()
        for name, record in mapping(sample_records, sample).items()
    )
    return {
        "liquidGlassDynamicHighlightStageGateSchemaVersion": 2,
        "dynamicArtifact": str(dynamic_root),
        "staticCapture": str(static_capture),
        "floatIntrinsicTable": {
            "path": str(float_intrinsic_table),
            "sha256": sha256_file(float_intrinsic_table),
        },
        "candidateConfiguration": DYNAMIC_HIGHLIGHT_CONFIGURATION,
        "coordinateSource": coordinate_source,
        "squareSelectorCalibration": (
            {
                "path": str(square_selector_archive),
                "sha256": sha256_file(square_selector_archive),
                "widthFixedLower": WIDTH_FIXED_LOWER,
                "selectorCount": SELECTOR_COUNT,
                "classification": "retrospective finite-domain calibration",
            }
            if square_selector_archive is not None
            and square_calibration is not None
            else None
        ),
        "rasterSelectors": raster_selectors,
        "implementation": implementation,
        "alphaRaster": alpha_results,
        "alphaAnchorSweep": alpha_anchor_sweep,
        "alphaStageDiagnostics": alpha_stage_results,
        "interpolantProbes": interpolant_probes,
        "normalizationSweep": normalization_sweep,
        "normalizedCoordinateSweep": normalized_coordinate_sweep,
        "divisionSweep": division_sweep,
        "sdfArithmeticSweep": sdf_arithmetic_sweep,
        "derivativeSweep": derivative_sweep,
        "coverageArithmeticSweep": coverage_sweep,
        "mixSweep": mix_sweep,
        "sdfSquaredUlpSweep": sdf_squared_ulp_sweep,
        "sdfDistanceUlpSweep": sdf_distance_ulp_sweep,
        "normalFieldDiagnostics": normal_field_diagnostics,
        "customSdfDiagnostics": custom_sdf_diagnostics,
        "internalStageTraces": internal_stage_traces,
        "translucentCompositor": tomography_results,
        "arithmeticSweep": arithmetic_sweep,
        "sourceConstructionSweep": source_construction_sweep,
        "gate": {
            "allDynamicAlphaTracesExact": alpha_exact,
            "allAlphaStageDiagnosticsExact": alpha_stages_exact,
            "allTranslucentCompositorStagesExact": tomography_exact,
            "exact": alpha_exact and alpha_stages_exact and tomography_exact,
            "calibrationBacked": square_calibration is not None,
            "productionShaderAuthorized": False,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dynamic_root", type=Path)
    parser.add_argument("--static-capture", type=Path, required=True)
    parser.add_argument(
        "--float-intrinsic-table",
        type=Path,
        default=Path("artifacts/apple-float-intrinsics-r8-30556057571.bin"),
    )
    parser.add_argument(
        "--coordinate-source",
        choices=("generated-axis", "captured-interpolant"),
        default="generated-axis",
    )
    parser.add_argument("--square-selector-archive", type=Path)
    parser.add_argument("-o", "--output", type=Path)
    arguments = parser.parse_args()
    report = run_gate(
        arguments.dynamic_root,
        static_capture=arguments.static_capture,
        float_intrinsic_table=arguments.float_intrinsic_table,
        coordinate_source=arguments.coordinate_source,
        square_selector_archive=arguments.square_selector_archive,
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
