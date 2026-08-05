#!/usr/bin/env python3
"""Bit-gate captured Apple Liquid Glass transition renders locally."""

import argparse
import json
import platform
import time
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from apple_glass_reference_renderer import (
    CAPTURE_HEIGHT,
    CAPTURE_WIDTH,
    AppleGlassReferenceRenderer,
    bgra_raw,
    compare_images,
)
from liquid_glass_complete_static_gate import HIGHLIGHT_CONFIGURATION
from liquid_glass_dynamic_capture import (
    EXPECTED_SAMPLE_INDICES,
    _background_geometry,
    _background_mvp,
    _fragment,
    _highlight_geometry,
    _report_paths,
    _source_texture,
    _uniform_payloads,
)
from liquid_glass_pack_intrinsic_tables import circle_scale_reciprocal_bits
from liquid_glass_profile_matrix import GLASS_FRAGMENTS, decode_profile
from liquid_glass_post_glass_gate import sha256_file
from liquid_glass_runtime_raster_coefficients import (
    axis_table,
    coefficient_table,
    runtime_quad_from_vertices,
    slopes_bits,
)
from liquid_glass_shader_specialization import (
    load_amd_packed_exact_circle_shader,
    load_specialized_exact_final_shader,
)
from liquid_glass_square_selector_calibration import (
    SquareSelectorCalibration,
    base_selector_use,
)

import raster_tile_selector_model as arithmetic


type JsonObject = dict[str, Any]
type CodeImage = NDArray[np.uint8]
type ProfileKey = tuple[str, str]

PROFILE_KEYS = {
    ("clear", "light"),
    ("clear", "dark"),
    ("regular", "light"),
    ("regular", "dark"),
}

# The settled fixture is centered exactly at (512, 512), so its recovered
# screen-coordinate path and the geometry-carried SDF coordinates coincide.
# Transition geometry has a fractional moving center; Apple's final highlight
# follows that interpolant instead of a fixed screen center.
DYNAMIC_HIGHLIGHT_CONFIGURATION = {
    **HIGHLIGHT_CONFIGURATION,
    "HighlightCoordinateMode": 1,
    "HighlightNormalizedCoordinateMode": 1,
    "HoldingMixMode": 0,
}


def _mismatch_details(reference: CodeImage, candidate: CodeImage) -> JsonObject:
    if reference.shape != candidate.shape:
        raise ValueError(
            f"image dimensions differ: {reference.shape} != {candidate.shape}"
        )
    signed_delta = candidate.astype(np.int16) - reference.astype(np.int16)
    changed = np.any(signed_delta != 0, axis=2)
    coordinates = np.argwhere(changed)
    return {
        "mismatchedPixels": int(coordinates.shape[0]),
        "firstMismatches": [
            {
                "x": int(x),
                "y": int(y),
                "appleRGBA": [int(value) for value in reference[y, x]],
                "candidateRGBA": [int(value) for value in candidate[y, x]],
                "candidateMinusAppleRGBA": [
                    int(value) for value in signed_delta[y, x]
                ],
            }
            for y, x in coordinates[:32]
        ],
    }


def _parse_static_capture(value: str) -> tuple[ProfileKey, Path]:
    profile, separator, path_text = value.partition("=")
    material, profile_separator, appearance = profile.partition("-")
    if (
        separator != "="
        or profile_separator != "-"
        or (material, appearance) not in PROFILE_KEYS
        or not path_text
    ):
        raise argparse.ArgumentTypeError(
            "static capture must be PROFILE=PATH, where PROFILE is "
            "clear-light, clear-dark, regular-light, or regular-dark"
        )
    return (material, appearance), Path(path_text)


def _static_captures(
    values: list[tuple[ProfileKey, Path]],
) -> dict[ProfileKey, Path]:
    result: dict[ProfileKey, Path] = {}
    for profile, path in values:
        if profile in result:
            raise ValueError(f"duplicate static capture for {profile}")
        runtime = path / "runtime.json"
        half_intrinsics = path / "half-intrinsics.bin"
        if not runtime.is_file() or not half_intrinsics.is_file():
            raise ValueError(f"static capture is incomplete: {path}")
        result[profile] = path
    return result


def _raw_mip_levels(
    root: Path, source: JsonObject
) -> dict[int, tuple[int, int, bytes]]:
    snapshots = source.get("mipSnapshots")
    if not isinstance(snapshots, list):
        raise ValueError("captured backdrop has no mip snapshots")
    result: dict[int, tuple[int, int, bytes]] = {}
    for snapshot in snapshots:
        if not isinstance(snapshot, dict) or snapshot.get("rawCapture") is not True:
            raise ValueError("captured backdrop mip has no raw bytes")
        level = snapshot.get("level")
        width = snapshot.get("width")
        height = snapshot.get("height")
        filename = snapshot.get("rawFile")
        byte_count = snapshot.get("rawBytes")
        if (
            type(level) is not int
            or type(width) is not int
            or type(height) is not int
            or not isinstance(filename, str)
            or type(byte_count) is not int
        ):
            raise ValueError("captured backdrop mip metadata is incomplete")
        path = root / filename
        raw = path.read_bytes()
        if len(raw) != byte_count or len(raw) != width * height * 4:
            raise ValueError(f"captured backdrop mip layout differs: {path}")
        result[level] = (width, height, raw)
    if sorted(result) != list(range(len(result))):
        raise ValueError("captured backdrop mip levels are not consecutive")
    return result


def _reference_output(root: Path, render: JsonObject) -> tuple[Path, Any]:
    output = render.get("output")
    if not isinstance(output, dict) or output.get("rawCapture") is not True:
        raise ValueError("dynamic CARenderer output has no raw capture")
    width = output.get("width")
    height = output.get("height")
    filename = output.get("rawFile")
    if (
        width != CAPTURE_WIDTH
        or height != CAPTURE_HEIGHT
        or output.get("pixelFormat") != 80
        or not isinstance(filename, str)
    ):
        raise ValueError("dynamic CARenderer output layout differs")
    path = root / filename
    if path.stat().st_size != output.get("rawBytes"):
        raise ValueError(f"dynamic CARenderer output bytes differ: {path}")
    return path, bgra_raw(path, width=width, height=height)


def _pre_final_input(root: Path, render: JsonObject) -> Path:
    replay = render.get("exactPassReplay")
    if not isinstance(replay, dict) or replay.get("exactByteMatch") is not True:
        raise ValueError("dynamic CARenderer final-pass replay is not exact")
    snapshot = replay.get("preFinalPass")
    if not isinstance(snapshot, dict) or snapshot.get("rawCapture") is not True:
        raise ValueError("dynamic CARenderer render has no pre-final input")
    width = snapshot.get("width")
    height = snapshot.get("height")
    filename = snapshot.get("rawFile")
    if (
        width != CAPTURE_WIDTH
        or height != CAPTURE_HEIGHT
        or snapshot.get("pixelFormat") != 80
        or snapshot.get("rawBytes") != CAPTURE_WIDTH * CAPTURE_HEIGHT * 4
        or not isinstance(filename, str)
    ):
        raise ValueError("dynamic CARenderer pre-final input layout differs")
    path = root / filename
    if not path.is_file() or path.stat().st_size != snapshot["rawBytes"]:
        raise ValueError(f"dynamic CARenderer pre-final input bytes differ: {path}")
    return path


def _glass_prefix_output(root: Path, render: JsonObject) -> tuple[Path, Any]:
    replay = render.get("exactPassReplay")
    independent = (
        replay.get("independentGlassReplay") if isinstance(replay, dict) else None
    )
    reference = independent.get("reference") if isinstance(independent, dict) else None
    snapshot = reference.get("output") if isinstance(reference, dict) else None
    if (
        not isinstance(snapshot, dict)
        or snapshot.get("rawCapture") is not True
        or snapshot.get("width") != CAPTURE_WIDTH
        or snapshot.get("height") != CAPTURE_HEIGHT
        or snapshot.get("pixelFormat") != 80
        or not isinstance(snapshot.get("rawFile"), str)
    ):
        raise ValueError("dynamic CARenderer glass-prefix output layout differs")
    path = root / snapshot["rawFile"]
    if not path.is_file() or path.stat().st_size != snapshot.get("rawBytes"):
        raise ValueError(f"dynamic CARenderer glass-prefix bytes differ: {path}")
    return path, bgra_raw(path, width=CAPTURE_WIDTH, height=CAPTURE_HEIGHT)


def _final_highlight_input(root: Path, render: JsonObject) -> tuple[Path, Any]:
    replay = render.get("exactPassReplay")
    reference = (
        replay.get("finalHighlightInputReference") if isinstance(replay, dict) else None
    )
    snapshot = reference.get("output") if isinstance(reference, dict) else None
    if (
        not isinstance(snapshot, dict)
        or reference.get("executed") is not True
        or snapshot.get("rawCapture") is not True
        or snapshot.get("width") != CAPTURE_WIDTH
        or snapshot.get("height") != CAPTURE_HEIGHT
        or snapshot.get("pixelFormat") != 80
        or snapshot.get("rawBytes") != CAPTURE_WIDTH * CAPTURE_HEIGHT * 4
        or not isinstance(snapshot.get("rawFile"), str)
    ):
        raise ValueError("dynamic CARenderer final-highlight input layout differs")
    path = root / snapshot["rawFile"]
    if not path.is_file() or path.stat().st_size != snapshot["rawBytes"]:
        raise ValueError(f"dynamic final-highlight input bytes differ: {path}")
    return path, bgra_raw(path, width=CAPTURE_WIDTH, height=CAPTURE_HEIGHT)


def _draw_scissors(
    render: JsonObject,
    fragment: str,
) -> tuple[tuple[int, int, int, int], tuple[int, int, int, int]]:
    probe = render.get("metalUniformProbe")
    records = probe.get("records") if isinstance(probe, dict) else None
    if not isinstance(records, list):
        raise ValueError("dynamic CARenderer command records are incomplete")

    def scissor_value(record: JsonObject, name: str) -> tuple[int, int, int, int]:
        values = tuple(record.get(key) for key in ("x", "y", "width", "height"))
        if len(values) != 4 or any(type(value) is not int for value in values):
            raise ValueError(f"dynamic CARenderer {name} scissor layout differs")
        return values  # type: ignore[return-value]

    background_matches = [
        record
        for record in records
        if isinstance(record, dict)
        and record.get("kind") == "scissorRect"
        and _fragment(record) == fragment
    ]
    if len(background_matches) != 1:
        raise ValueError(
            f"dynamic CARenderer has {len(background_matches)} {fragment} scissors"
        )
    background_record = background_matches[0]
    encoder = background_record.get("encoder")
    highlight_draws = [
        record
        for record in records
        if isinstance(record, dict)
        and record.get("encoder") == encoder
        and str(record.get("kind", "")).startswith("draw")
        and _fragment(record) == "A2Xghfc"
    ]
    if len(highlight_draws) != 1:
        raise ValueError(
            "dynamic CARenderer final pass has "
            f"{len(highlight_draws)} final-highlight draws"
        )
    highlight_sequence = int(highlight_draws[0]["sequence"])
    active_scissors = [
        record
        for record in records
        if isinstance(record, dict)
        and record.get("encoder") == encoder
        and record.get("kind") == "scissorRect"
        and int(record["sequence"]) < highlight_sequence
    ]
    if not active_scissors:
        raise ValueError("dynamic CARenderer final highlight has no active scissor")
    highlight_record = max(
        active_scissors,
        key=lambda value: int(value["sequence"]),
    )
    return (
        scissor_value(background_record, fragment),
        scissor_value(highlight_record, "A2Xghfc"),
    )


def _highlight_quad(
    geometry: Any,
    *,
    name: str,
    mvp_payload: bytes,
) -> Any:
    """Build the raster inputs for Apple's separately submitted highlight quad."""
    if geometry.indices is None:
        raise ValueError("final-highlight geometry has no index buffer")
    vertices = geometry.vertices[geometry.indices].copy()
    # The final pass consumes only SDF.xy. Supplying the same affine fields for
    # the unused source channels lets the shared four-channel raster model build
    # the draw's coefficients without introducing captured data.
    vertices[:, 6:8] = vertices[:, 4:6]
    return runtime_quad_from_vertices(
        vertices,
        name=name,
        mvp_payload=mvp_payload,
    )


def _profile_report(
    report_path: Path,
    *,
    static_capture: Path,
    float_intrinsic_table: Path,
    shader_source: str,
    selector_table: tuple[int, ...],
    square_calibration: SquareSelectorCalibration | None,
    packed_exact_candidate: bool,
    axis_exact_candidate: bool,
    sqrt_intrinsic_table: Path | None,
    rsqrt_intrinsic_table: Path | None,
) -> JsonObject:
    report = json.loads(report_path.read_text(encoding="utf-8"))
    material = str(report.get("material"))
    appearance = str(report.get("appearance"))
    try:
        fragment = GLASS_FRAGMENTS[material]
    except KeyError as error:
        raise ValueError(f"unsupported dynamic material: {material}") from error
    uniforms = report.get("dynamicBackgroundUniforms")
    records = uniforms.get("records") if isinstance(uniforms, dict) else None
    if not isinstance(records, list) or [
        record.get("sampleIndex") for record in records
    ] != list(EXPECTED_SAMPLE_INDICES):
        raise ValueError(f"dynamic records are incomplete: {report_path}")

    states: list[JsonObject] = []
    with AppleGlassReferenceRenderer(
        static_capture,
        fragment_shader_source=shader_source,
        intrinsic_table=(
            None if packed_exact_candidate else float_intrinsic_table
        ),
        half_intrinsic_table=static_capture / "half-intrinsics.bin",
        sqrt_intrinsic_table=sqrt_intrinsic_table,
        rsqrt_intrinsic_table=rsqrt_intrinsic_table,
        load_interpolant_trace=False,
        load_interpolant_axis_trace=False,
        load_diagnostic_traces=False,
    ) as renderer:
        for name, value in DYNAMIC_HIGHLIGHT_CONFIGURATION.items():
            renderer.program[name].value = value
        implementation = renderer.implementation

        for record in records:
            if not isinstance(record, dict):
                raise ValueError("dynamic record is not an object")
            render = record.get("render")
            if not isinstance(render, dict) or render.get("executed") is not True:
                raise ValueError("dynamic render did not execute")
            main, shadow = _background_geometry(render, fragment)
            mvp = _background_mvp(render, fragment)
            highlight_geometry = _highlight_geometry(render)
            profile_payload, highlight_payload = _uniform_payloads(render, fragment)
            source = _source_texture(render, fragment)
            background_scissor, highlight_scissor = _draw_scissors(render, fragment)
            reference_path, reference = _reference_output(report_path.parent, render)
            pre_final_path = _pre_final_input(report_path.parent, render)
            prefix_path, prefix_reference = _glass_prefix_output(
                report_path.parent,
                render,
            )
            highlight_input_path, highlight_input_reference = _final_highlight_input(
                report_path.parent, render
            )

            state: JsonObject = {
                "sampleIndex": record["sampleIndex"],
                "requestedProgress": record["requestedProgress"],
                "remaining": record["remaining"],
                "reference": str(reference_path),
            }
            try:
                quad = runtime_quad_from_vertices(
                    main.vertices,
                    name=(f"{material}-{appearance}-sample-{record['sampleIndex']}"),
                    mvp_payload=mvp,
                )
                quad_selectors = list(selector_table)
                quad_selector_use = (
                    square_calibration.use_for(quad.case, selector_table)
                    if square_calibration is not None
                    else base_selector_use(quad.case, selector_table)
                )
                quad_selectors[quad_selector_use.table_index] = (
                    quad_selector_use.selected
                )
                tile_start, coefficients = coefficient_table(
                    quad,
                    selector_table=quad_selectors,
                )
                slopes = slopes_bits(quad, quad_selectors)
                axis_start, background_axes = axis_table(
                    quad,
                    selector_table=quad_selectors,
                    helper_lane_halo=2,
                )
                highlight_quad = _highlight_quad(
                    highlight_geometry,
                    name=(
                        f"{material}-{appearance}-sample-"
                        f"{record['sampleIndex']}-highlight"
                    ),
                    mvp_payload=mvp,
                )
                highlight_selectors = list(selector_table)
                highlight_selector_use = (
                    square_calibration.use_for(
                        highlight_quad.case,
                        selector_table,
                    )
                    if square_calibration is not None
                    else base_selector_use(
                        highlight_quad.case,
                        selector_table,
                    )
                )
                highlight_selectors[
                    highlight_selector_use.table_index
                ] = highlight_selector_use.selected
                highlight_tile_start, highlight_coefficients = coefficient_table(
                    highlight_quad,
                    selector_table=highlight_selectors,
                )
                highlight_slopes = slopes_bits(
                    highlight_quad,
                    highlight_selectors,
                )
                highlight_axis_start, highlight_axes = axis_table(
                    highlight_quad,
                    selector_table=highlight_selectors,
                    helper_lane_halo=2,
                )
            except ValueError as error:
                state["executed"] = False
                state["reason"] = str(error)
                state["requiresFractionalRasterSetup"] = True
                states.append(state)
                continue

            renderer.set_draw_geometries(main=main, shadow=shadow)
            renderer.set_mvp_payload(mvp)
            renderer.set_profile_payload(profile_payload)
            reciprocal_bits = None
            if packed_exact_candidate:
                profile_fields = decode_profile(profile_payload)["fields"]
                radius = float(profile_fields["sdf_arg2"]["values"][2])
                reciprocal_bits = circle_scale_reciprocal_bits(
                    radius,
                    float_intrinsic_table,
                )
                renderer.set_circle_scale_reciprocal_bits(reciprocal_bits)
            renderer.set_source_mip_bgra(_raw_mip_levels(report_path.parent, source))
            renderer.set_destination_bgra_path(pre_final_path)
            renderer.set_draw_scissors(
                background=background_scissor,
                final_highlight=highlight_scissor,
            )
            if axis_exact_candidate:
                renderer.set_interpolant_axis_table(
                    background_axes,
                    start=axis_start,
                )
            else:
                renderer.set_interpolant_coefficients(
                    coefficients,
                    tile_start=tile_start,
                    slope_bits=slopes,
                )
            renderer.set_final_highlight_geometry(highlight_geometry)
            started = time.perf_counter()
            prefix_candidate = renderer.render()
            prefix_comparison = compare_images(prefix_reference, prefix_candidate)
            stage_boundary_comparison = compare_images(
                prefix_reference,
                highlight_input_reference,
            )
            candidate_input_comparison = compare_images(
                highlight_input_reference,
                prefix_candidate,
            )
            if axis_exact_candidate:
                renderer.set_interpolant_axis_table(
                    highlight_axes,
                    start=highlight_axis_start,
                )
            else:
                renderer.set_interpolant_coefficients(
                    highlight_coefficients,
                    tile_start=highlight_tile_start,
                    slope_bits=highlight_slopes,
                )
            isolated_highlight_candidate = renderer.render_final_highlight_over(
                highlight_input_reference,
                final_highlight_payload=highlight_payload,
            )
            candidate = renderer.render_final_highlight_over(
                prefix_candidate,
                final_highlight_payload=highlight_payload,
            )
            render_seconds = time.perf_counter() - started
            state.update(
                {
                    "executed": True,
                    "requiresFractionalRasterSetup": False,
                    "fractionalRaster": any(
                        value % 256 != 0
                        for value in (
                            quad.case.originXFixed,
                            quad.case.originYFixed,
                            quad.case.widthFixed,
                            quad.case.heightFixed,
                        )
                    ),
                    "runtimeRaster": {
                        "coordinateMode": (
                            "axis-table"
                            if axis_exact_candidate
                            else "compact-coefficients"
                        ),
                        "axisStart": (
                            axis_start if axis_exact_candidate else None
                        ),
                        "axisBytes": (
                            int(background_axes.nbytes)
                            if axis_exact_candidate
                            else 0
                        ),
                        "circleScaleReciprocalBits": (
                            f"0x{reciprocal_bits:08x}"
                            if reciprocal_bits is not None
                            else None
                        ),
                        "diagonal": (
                            "ascending" if quad.ascendingDiagonal else "descending"
                        ),
                        "origin": [quad.case.originX, quad.case.originY],
                        "extent": [quad.case.width, quad.case.height],
                        "fixedBounds": [
                            quad.case.originXFixed,
                            quad.case.originYFixed,
                            quad.case.originXFixed + quad.case.widthFixed,
                            quad.case.originYFixed + quad.case.heightFixed,
                        ],
                        "fixedUnitsPerPixel": 256,
                        "tileStart": tile_start,
                        "tileCount": int(coefficients.shape[1]),
                        "slopeBits": [f"0x{value:08x}" for value in slopes],
                        "selector": {
                            "base": quad_selector_use.base,
                            "selected": quad_selector_use.selected,
                            "offset": quad_selector_use.offset,
                        },
                    },
                    "highlightRuntimeRaster": {
                        "coordinateMode": (
                            "axis-table"
                            if axis_exact_candidate
                            else "compact-coefficients"
                        ),
                        "axisStart": (
                            highlight_axis_start
                            if axis_exact_candidate
                            else None
                        ),
                        "axisBytes": (
                            int(highlight_axes.nbytes)
                            if axis_exact_candidate
                            else 0
                        ),
                        "diagonal": (
                            "ascending"
                            if highlight_quad.ascendingDiagonal
                            else "descending"
                        ),
                        "origin": [
                            highlight_quad.case.originX,
                            highlight_quad.case.originY,
                        ],
                        "extent": [
                            highlight_quad.case.width,
                            highlight_quad.case.height,
                        ],
                        "fixedBounds": [
                            highlight_quad.case.originXFixed,
                            highlight_quad.case.originYFixed,
                            highlight_quad.case.originXFixed
                            + highlight_quad.case.widthFixed,
                            highlight_quad.case.originYFixed
                            + highlight_quad.case.heightFixed,
                        ],
                        "fixedUnitsPerPixel": 256,
                        "tileStart": highlight_tile_start,
                        "tileCount": int(highlight_coefficients.shape[1]),
                        "slopeBits": [f"0x{value:08x}" for value in highlight_slopes],
                        "selector": {
                            "base": highlight_selector_use.base,
                            "selected": highlight_selector_use.selected,
                            "offset": highlight_selector_use.offset,
                        },
                    },
                    "localRenderSeconds": render_seconds,
                    "prefixReference": str(prefix_path),
                    "prefixComparison": prefix_comparison.as_json(),
                    "prefixMismatchDetails": _mismatch_details(
                        prefix_reference,
                        prefix_candidate,
                    ),
                    "finalHighlightInputReference": str(highlight_input_path),
                    "stageBoundaryComparison": (stage_boundary_comparison.as_json()),
                    "candidateInputComparison": (candidate_input_comparison.as_json()),
                    "isolatedFinalHighlightComparison": compare_images(
                        reference,
                        isolated_highlight_candidate,
                    ).as_json(),
                    "comparison": compare_images(reference, candidate).as_json(),
                }
            )
            states.append(state)

    executed = [state for state in states if state.get("executed") is True]
    exact = [
        state for state in executed if state.get("comparison", {}).get("exact") is True
    ]
    return {
        "material": material,
        "appearance": appearance,
        "artifact": str(report_path.parent),
        "staticCapture": str(static_capture),
        "implementation": implementation,
        "states": states,
        "summary": {
            "stateCount": len(states),
            "executedStateCount": len(executed),
            "exactStateCount": len(exact),
            "fractionalRasterStateCount": sum(
                state.get("fractionalRaster") is True for state in states
            ),
            "unsupportedRasterStateCount": sum(
                state.get("requiresFractionalRasterSetup") is True for state in states
            ),
            "allStatesExact": len(exact) == len(states),
        },
    }


def run_gate(
    dynamic_root: Path,
    *,
    static_captures: dict[ProfileKey, Path],
    float_intrinsic_table: Path,
    square_selector_archive: Path | None = None,
    near_square_selector_archive: Path | None = None,
    packed_exact_candidate: bool = False,
    axis_exact_candidate: bool = False,
    sqrt_intrinsic_table: Path | None = None,
    rsqrt_intrinsic_table: Path | None = None,
) -> JsonObject:
    if not float_intrinsic_table.is_file():
        raise ValueError(f"float intrinsic table is absent: {float_intrinsic_table}")
    reports = _report_paths(dynamic_root)
    if not reports:
        raise ValueError(f"no dynamic reports found under {dynamic_root}")
    if packed_exact_candidate:
        for table in (sqrt_intrinsic_table, rsqrt_intrinsic_table):
            if table is None or not table.is_file():
                raise ValueError(f"packed intrinsic table is absent: {table}")
    use_axis_table = axis_exact_candidate or packed_exact_candidate
    generic_shader_source = (
        None
        if packed_exact_candidate
        else load_specialized_exact_final_shader(
            coordinate_mode=4 if use_axis_table else 5
        )
    )
    selector_table = arithmetic.load_selector_table()
    square_calibration = (
        SquareSelectorCalibration.load(
            square_selector_archive,
            near_square_path=near_square_selector_archive,
        )
        if square_selector_archive is not None
        else None
    )
    profiles: list[JsonObject] = []
    for report_path in reports:
        report = json.loads(report_path.read_text(encoding="utf-8"))
        key = (str(report.get("material")), str(report.get("appearance")))
        try:
            static_capture = static_captures[key]
        except KeyError as error:
            raise ValueError(f"no static capture supplied for {key}") from error
        profiles.append(
            _profile_report(
                report_path,
                static_capture=static_capture,
                float_intrinsic_table=float_intrinsic_table,
                shader_source=(
                    load_amd_packed_exact_circle_shader(
                        key[0],
                        coordinate_mode=4,
                    )
                    if packed_exact_candidate
                    else generic_shader_source
                ),
                selector_table=selector_table,
                square_calibration=square_calibration,
                packed_exact_candidate=packed_exact_candidate,
                axis_exact_candidate=use_axis_table,
                sqrt_intrinsic_table=sqrt_intrinsic_table,
                rsqrt_intrinsic_table=rsqrt_intrinsic_table,
            )
        )
    all_states = [state for profile in profiles for state in profile["states"]]
    all_exact = bool(all_states) and all(
        state.get("executed") is True
        and state.get("comparison", {}).get("exact") is True
        for state in all_states
    )
    return {
        "liquidGlassDynamicRenderGateSchemaVersion": 2,
        "implementation": {
            "file": "analysis/liquid_glass_dynamic_render_gate.py",
            "python": platform.python_version(),
            "capturedCoordinateOrCoefficientTableLoaded": False,
            "oracleInjectionUsed": False,
            "capturedStageInputUsedForGateCandidate": False,
            "capturedStageInputUsedForDiagnosticIsolation": True,
            "highlightConfiguration": DYNAMIC_HIGHLIGHT_CONFIGURATION,
            "packedExactCandidate": packed_exact_candidate,
            "axisExactCandidate": use_axis_table,
            "squareSelectorCalibration": (
                {
                    "path": str(square_selector_archive),
                    "sha256": sha256_file(square_selector_archive),
                    "classification": "retrospective finite-domain calibration",
                }
                if square_selector_archive is not None
                and square_calibration is not None
                else None
            ),
            "nearSquareSelectorCalibration": (
                {
                    "path": str(near_square_selector_archive),
                    "sha256": sha256_file(near_square_selector_archive),
                    "classification": (
                        "preregistered finite-domain calibration; "
                        "not a universal closed form"
                    ),
                }
                if near_square_selector_archive is not None
                and square_calibration is not None
                else None
            ),
        },
        "dynamicArtifact": str(dynamic_root),
        "floatIntrinsicTable": str(float_intrinsic_table),
        "profiles": profiles,
        "gate": {
            "profileCount": len(profiles),
            "stateCount": len(all_states),
            "executedStateCount": sum(
                state.get("executed") is True for state in all_states
            ),
            "exactStateCount": sum(
                state.get("comparison", {}).get("exact") is True for state in all_states
            ),
            "fractionalRasterStateCount": sum(
                state.get("fractionalRaster") is True for state in all_states
            ),
            "unsupportedRasterStateCount": sum(
                state.get("requiresFractionalRasterSetup") is True
                for state in all_states
            ),
            "exact": all_exact,
            "productionShaderAuthorized": False,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dynamic_root", type=Path)
    parser.add_argument(
        "--static-capture",
        action="append",
        type=_parse_static_capture,
        required=True,
        metavar="PROFILE=PATH",
    )
    parser.add_argument("--square-selector-archive", type=Path)
    parser.add_argument("--near-square-selector-archive", type=Path)
    parser.add_argument(
        "--float-intrinsic-table",
        type=Path,
        default=Path("artifacts/apple-float-intrinsics-r8-30556057571.bin"),
    )
    parser.add_argument("--packed-exact-candidate", action="store_true")
    parser.add_argument("--axis-exact-candidate", action="store_true")
    parser.add_argument(
        "--sqrt-intrinsic-table",
        type=Path,
        default=Path(
            "artifacts/apple-float-sqrt-intrinsics-r32ui-30556057571.bin"
        ),
    )
    parser.add_argument(
        "--rsqrt-intrinsic-table",
        type=Path,
        default=Path(
            "artifacts/apple-float-rsqrt-intrinsics-r32ui-30556057571.bin"
        ),
    )
    parser.add_argument("-o", "--output", type=Path)
    arguments = parser.parse_args()
    result = run_gate(
        arguments.dynamic_root,
        static_captures=_static_captures(arguments.static_capture),
        float_intrinsic_table=arguments.float_intrinsic_table,
        square_selector_archive=arguments.square_selector_archive,
        near_square_selector_archive=arguments.near_square_selector_archive,
        packed_exact_candidate=arguments.packed_exact_candidate,
        axis_exact_candidate=arguments.axis_exact_candidate,
        sqrt_intrinsic_table=(
            arguments.sqrt_intrinsic_table
            if arguments.packed_exact_candidate
            else None
        ),
        rsqrt_intrinsic_table=(
            arguments.rsqrt_intrinsic_table
            if arguments.packed_exact_candidate
            else None
        ),
    )
    encoded = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if arguments.output is None:
        print(encoded, end="")
    else:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(encoded, encoding="utf-8")
        print(arguments.output)
    return 0 if result["gate"]["exact"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
