#!/usr/bin/env python3
"""Retrospectively isolate dynamic SDF float arithmetic by exact words."""

import argparse
import json
import struct
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import moderngl
import numpy as np
from numpy.typing import NDArray

from apple_glass_reference_renderer import (
    CAPTURE_HEIGHT,
    CAPTURE_WIDTH,
    AppleGlassReferenceRenderer,
)
from liquid_glass_dynamic_background_arithmetic import (
    GENERIC_EXACT_CONFIGURATION,
    _load_uint,
    _sample_record,
    _trace_paths,
)
from liquid_glass_dynamic_background_interpolant_gate import _trace_outputs
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
    _pre_final_input,
    _raw_mip_levels,
)
from liquid_glass_post_glass_gate import sha256_file
from liquid_glass_profile_matrix import GLASS_FRAGMENTS, decode_profile
from liquid_glass_runtime_raster_coefficients import (
    axis_table,
    coefficient_table,
    runtime_quad_from_vertices,
    slopes_bits,
)
from liquid_glass_square_selector_calibration import SquareSelectorCalibration

import raster_tile_selector_model as arithmetic


type JsonObject = dict[str, Any]
type FloatImage = NDArray[np.float32]
type UIntImage = NDArray[np.uint32]

SAMPLE_INDEX = 16
GL_RGBA32F = 0x8814
TRACE_NUMBERS = {
    "geometry": 26,
    "oval": 27,
    "float": 28,
    "normal": 29,
    "half-pair": 30,
    "setup": 31,
}

DEBUG_FUNCTION = r"""
float replay_debug_apply_rsqrt_delta(float value, float base)
{
    uint source_bits = floatBitsToUint(value);
    uint mantissa = source_bits & 0x007fffffu;
    uint exponent_parity = (source_bits >> 23u) & 1u;
    uint code = apple_intrinsic_code(value, 1u);
    int delta = int((code >> (4u + exponent_parity)) & 1u);
    if (mantissa == 651320u || mantissa == 8380416u) {
        delta = -1;
    }
    return uintBitsToFloat(uint(int(floatBitsToUint(base)) + delta));
}

void replay_debug_float_fraction(
    uint bits,
    out uint significand,
    out int exponent
)
{
    uint exponent_field = (bits >> 23u) & 255u;
    if (exponent_field == 0u) {
        significand = bits & 0x007fffffu;
        exponent = -149;
    } else {
        significand = (bits & 0x007fffffu) | 0x00800000u;
        exponent = int(exponent_field) - 127 - 23;
    }
}

void replay_debug_midpoint_fraction(
    uint left_bits,
    uint right_bits,
    out uint significand,
    out int exponent
)
{
    uint left_significand;
    uint right_significand;
    int left_exponent;
    int right_exponent;
    replay_debug_float_fraction(
        left_bits,
        left_significand,
        left_exponent
    );
    replay_debug_float_fraction(
        right_bits,
        right_significand,
        right_exponent
    );
    int common_exponent = min(left_exponent, right_exponent);
    uint aligned_left = left_significand
        << uint(left_exponent - common_exponent);
    uint aligned_right = right_significand
        << uint(right_exponent - common_exponent);
    significand = aligned_left + aligned_right;
    exponent = common_exponent - 1;
}

int replay_debug_rsqrt_midpoint_product_compare(
    uint value_bits,
    uint midpoint_significand,
    int midpoint_exponent
)
{
    uint value_significand;
    int value_exponent;
    replay_debug_float_fraction(
        value_bits,
        value_significand,
        value_exponent
    );

    uint square_high;
    uint square_low;
    umulExtended(
        midpoint_significand,
        midpoint_significand,
        square_high,
        square_low
    );
    uint low_product_high;
    uint low_product_low;
    umulExtended(
        square_low,
        value_significand,
        low_product_high,
        low_product_low
    );
    uint high_product_high;
    uint high_product_low;
    umulExtended(
        square_high,
        value_significand,
        high_product_high,
        high_product_low
    );
    uint middle = low_product_high + high_product_low;
    uint high = high_product_high + uint(middle < low_product_high);
    uvec3 product = uvec3(low_product_low, middle, high);

    int target_shift = -(
        value_exponent + 2 * midpoint_exponent
    );
    if (target_shift < 0) {
        return 1;
    }
    if (target_shift >= 96) {
        return -1;
    }
    uvec3 target = uvec3(0u);
    if (target_shift < 32) {
        target.x = 1u << uint(target_shift);
    } else if (target_shift < 64) {
        target.y = 1u << uint(target_shift - 32);
    } else {
        target.z = 1u << uint(target_shift - 64);
    }
    if (product.z != target.z) {
        return product.z < target.z ? -1 : 1;
    }
    if (product.y != target.y) {
        return product.y < target.y ? -1 : 1;
    }
    if (product.x != target.x) {
        return product.x < target.x ? -1 : 1;
    }
    return 0;
}

float replay_debug_ieee_rsqrt(float value)
{
    if (!(value > 0.0) || isinf(value)) {
        return inversesqrt(value);
    }
    uint value_bits = floatBitsToUint(value);
    uint candidate_bits = floatBitsToUint(
        float(1.0 / sqrt(double(value)))
    );
    for (int iteration = 0; iteration < 2; ++iteration) {
        uint midpoint_significand;
        int midpoint_exponent;
        replay_debug_midpoint_fraction(
            candidate_bits - 1u,
            candidate_bits,
            midpoint_significand,
            midpoint_exponent
        );
        int lower_compare = replay_debug_rsqrt_midpoint_product_compare(
            value_bits,
            midpoint_significand,
            midpoint_exponent
        );
        if (
            lower_compare > 0
            || (lower_compare == 0 && (candidate_bits & 1u) != 0u)
        ) {
            candidate_bits -= 1u;
            continue;
        }

        replay_debug_midpoint_fraction(
            candidate_bits,
            candidate_bits + 1u,
            midpoint_significand,
            midpoint_exponent
        );
        int upper_compare = replay_debug_rsqrt_midpoint_product_compare(
            value_bits,
            midpoint_significand,
            midpoint_exponent
        );
        if (
            upper_compare < 0
            || (upper_compare == 0 && (candidate_bits & 1u) != 0u)
        ) {
            candidate_bits += 1u;
            continue;
        }
        break;
    }
    return uintBitsToFloat(candidate_bits);
}

vec4 replay_dynamic_sdf_float_debug(vec2 source_point, int trace)
{
    vec2 point = abs(source_point);
    float circle_constant = uintBitsToFloat(0x3fc3ab4bu);
    float circle_scale = float_barrier(SdfArg2.z * circle_constant);
    float inverse_circle_scale = apple_fast_reciprocal(circle_scale);
__NUMERATOR_BODY__
    float normalized_x = max(
        0.0,
        float_barrier(numerator_x * inverse_circle_scale)
    );
    float normalized_y = max(
        0.0,
        float_barrier(numerator_y * inverse_circle_scale)
    );
    if (trace == 31) {
        return vec4(
            point,
            circle_scale,
            inverse_circle_scale
        );
    }
    if (trace == 26) {
        return vec4(
            numerator_x,
            numerator_y,
            normalized_x,
            normalized_y
        );
    }

    float oval_x = max(0.0, float_barrier(
        normalized_x * circle_constant
        + uintBitsToFloat(0xbf075697u)
    ));
    float oval_y = max(0.0, float_barrier(
        normalized_y * circle_constant
        + uintBitsToFloat(0xbf075697u)
    ));
    float oval_squared = float_barrier(
        oval_y * oval_y + float_barrier(oval_x * oval_x)
    );
    float oval_length = apple_fast_sqrt(oval_squared);
    if (trace == 27) {
        return vec4(oval_x, oval_y, oval_squared, oval_length);
    }

    float oval_distance = float_barrier(
        oval_length * uintBitsToFloat(0x3f277765u)
        + uintBitsToFloat(0x3eb11136u)
    );
__CURVED_BODY__
    float distance = half_value(
        float_barrier(circle_scale * curved_distance)
    );
    if (trace == 28) {
        return vec4(
            oval_squared,
            oval_length,
            oval_distance,
            curved_distance
        );
    }
    if (trace == 30) {
        return vec4(curved_distance, distance, 0.0, 0.0);
    }

    float point_squared = float_barrier(
        point.y * point.y + float_barrier(point.x * point.x)
    );
__INVERSE_LENGTH_BODY__
    float normal_x = float_barrier(point.x * inverse_length);
    float normal_y = float_barrier(point.y * inverse_length);
    return vec4(
        point_squared,
        inverse_length,
        normal_x,
        normal_y
    );
}
"""


def mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} is not an object")
    return value


def _instrumented_shader(
    path: Path,
    *,
    numerator_mode: str,
    curved_distance_mode: str,
    normal_rsqrt_mode: str,
) -> str:
    source = path.read_text(encoding="utf-8")
    main_anchor = "\nvoid main()\n{"
    branch_anchor = """    if (NumericTrace == 3) {
        fragment_color = replay_profile_circle_debug(
            abs(replay_sdf_uv)
        );
        return;
    }
"""
    if source.count(main_anchor) != 1 or source.count(branch_anchor) != 1:
        raise ValueError("reference shader debug anchors differ")
    numerator_bodies = {
        "source-order": """    float numerator_x = float_barrier(
        float_barrier(point.x - SdfArg.x) + circle_scale
    );
    float numerator_y = float_barrier(
        float_barrier(point.y - SdfArg.y) + circle_scale
    );""",
        "uniform-offset": """    float offset_x = float_barrier(circle_scale - SdfArg.x);
    float offset_y = float_barrier(circle_scale - SdfArg.y);
    float numerator_x = float_barrier(point.x + offset_x);
    float numerator_y = float_barrier(point.y + offset_y);""",
    }
    try:
        debug_function = DEBUG_FUNCTION.replace(
            "__NUMERATOR_BODY__",
            numerator_bodies[numerator_mode],
        )
    except KeyError as error:
        raise ValueError(f"unsupported numerator mode: {numerator_mode}") from error
    curved_bodies = {
        "materialized-float": """    float curved_distance = half_value(
        float_barrier(oval_distance - 1.0)
    );""",
        "contracted-half-fma": """    precise float curved_float = fma(
        oval_length,
        uintBitsToFloat(0x3f277765u),
        uintBitsToFloat(0xbf277765u)
    );
    float curved_distance = half_value(curved_float);""",
    }
    try:
        debug_function = debug_function.replace(
            "__CURVED_BODY__",
            curved_bodies[curved_distance_mode],
        )
    except KeyError as error:
        raise ValueError(
            f"unsupported curved-distance mode: {curved_distance_mode}"
        ) from error
    normal_bodies = {
        "reference": """    float inverse_length = apple_fast_rsqrt(point_squared);""",
        "native": """    float inverse_length = replay_debug_apply_rsqrt_delta(
        point_squared,
        float_barrier(inversesqrt(point_squared))
    );""",
        "float-sqrt-divide": """    float inverse_length = replay_debug_apply_rsqrt_delta(
        point_squared,
        float_barrier(1.0 / sqrt(point_squared))
    );""",
        "ieee-sqrt-divide": """    float inverse_length = replay_debug_apply_rsqrt_delta(
        point_squared,
        float_barrier(1.0 / ieee_sqrt(point_squared))
    );""",
        "native-newton": """    precise float inverse_seed = inversesqrt(point_squared);
    precise float inverse_square = inverse_seed * inverse_seed;
    precise float inverse_factor = fma(
        -0.5 * point_squared,
        inverse_square,
        1.5
    );
    precise float inverse_base = inverse_seed * inverse_factor;
    float inverse_length = replay_debug_apply_rsqrt_delta(
        point_squared,
        inverse_base
    );""",
        "integer-corrected": """    float inverse_length = replay_debug_apply_rsqrt_delta(
        point_squared,
        replay_debug_ieee_rsqrt(point_squared)
    );""",
    }
    try:
        debug_function = debug_function.replace(
            "__INVERSE_LENGTH_BODY__",
            normal_bodies[normal_rsqrt_mode],
        )
    except KeyError as error:
        raise ValueError(
            f"unsupported normal-rsqrt mode: {normal_rsqrt_mode}"
        ) from error
    source = source.replace(
        main_anchor,
        "\n" + debug_function + main_anchor,
        1,
    )
    return source.replace(
        branch_anchor,
        branch_anchor
        + """    if (NumericTrace >= 26 && NumericTrace <= 31) {
        fragment_color = replay_dynamic_sdf_float_debug(
            replay_sdf_uv,
            NumericTrace
        );
        return;
    }
""",
        1,
    )


def _bind_trace_textures(renderer: AppleGlassReferenceRenderer) -> None:
    renderer.source_texture.use(location=0)
    for texture, location in (
        (renderer.refraction_trace_texture, 1),
        (renderer.interpolant_trace_texture, 2),
        (renderer.sdf_trace_texture, 3),
        (renderer.sdf_float_trace_texture, 4),
        (renderer.sdf_normal_trace_texture, 5),
        (renderer.intrinsic_table_texture, 6),
        (renderer.interpolant_axis_trace_texture, 8),
        (renderer.interpolant_coefficient_texture, 9),
        (renderer.interpolant_correction_texture, 10),
        (renderer.sqrt_intrinsic_table_texture, 11),
        (renderer.rsqrt_intrinsic_table_texture, 12),
        (renderer.half_intrinsic_table_texture, 13),
    ):
        if texture is not None:
            texture.use(location=location)
    renderer.destination_texture.use(location=7)


def _render_float_trace(
    renderer: AppleGlassReferenceRenderer,
    trace: int,
) -> FloatImage:
    target = renderer.context.texture(
        (CAPTURE_WIDTH, CAPTURE_HEIGHT),
        4,
        dtype="f4",
        internal_format=GL_RGBA32F,
    )
    framebuffer = renderer.context.framebuffer([target])
    try:
        framebuffer.use()
        framebuffer.clear()
        renderer.context.viewport = (0, 0, CAPTURE_WIDTH, CAPTURE_HEIGHT)
        renderer.context.scissor = (0, 0, CAPTURE_WIDTH, CAPTURE_HEIGHT)
        renderer.context.disable(moderngl.BLEND)
        _bind_trace_textures(renderer)
        renderer.program["NumericTrace"].value = trace
        renderer.program["SdfMode"].value = 4
        renderer.main_array.render(mode=moderngl.TRIANGLES, vertices=6)
        renderer.context.finish()
        values = np.frombuffer(
            framebuffer.read(
                components=4,
                alignment=1,
                dtype="f4",
            ),
            dtype="<f4",
        ).reshape(CAPTURE_HEIGHT, CAPTURE_WIDTH, 4)
        return np.flipud(values).copy()
    finally:
        renderer.program["NumericTrace"].value = 0
        framebuffer.release()
        target.release()


def _apply_scissor(
    values: NDArray[np.generic],
    scissor: tuple[int, int, int, int],
) -> NDArray[np.generic]:
    x, y, width, height = scissor
    result = np.zeros_like(values)
    result[y : y + height, x : x + width] = values[
        y : y + height,
        x : x + width,
    ]
    return result


def _compare(reference: UIntImage, candidate: UIntImage) -> JsonObject:
    if reference.shape != candidate.shape:
        raise ValueError(
            f"float-trace shapes differ: {reference.shape} != {candidate.shape}"
        )
    changed = reference != candidate
    changed_pixels = np.any(changed, axis=2)
    locations = np.argwhere(changed_pixels)
    examples = []
    for y, x in locations[:16]:
        channels = np.flatnonzero(changed[y, x])
        examples.append(
            {
                "x": int(x),
                "y": int(y),
                "channels": channels.astype(int).tolist(),
                "referenceHex": [
                    f"0x{int(value):08x}" for value in reference[y, x]
                ],
                "candidateHex": [
                    f"0x{int(value):08x}" for value in candidate[y, x]
                ],
            }
        )
    bit_delta_histograms: list[JsonObject] = []
    for channel in range(reference.shape[2]):
        selected = changed[..., channel]
        deltas = (
            reference[..., channel][selected].astype(np.int64)
            - candidate[..., channel][selected].astype(np.int64)
        )
        values, counts = np.unique(deltas, return_counts=True)
        bit_delta_histograms.append(
            {
                str(int(value)): int(count)
                for value, count in zip(values, counts, strict=True)
            }
        )
    return {
        "comparedWords": int(reference.size),
        "mismatchedWords": int(np.count_nonzero(changed)),
        "mismatchedPixels": int(np.count_nonzero(changed_pixels)),
        "exact": not np.any(changed),
        "referenceMinusCandidateBitDeltaByChannel": bit_delta_histograms,
        "examples": examples,
    }


def _half_bits(values: NDArray[np.float32]) -> NDArray[np.uint16]:
    return values.astype(np.float16).view(np.uint16)


def analyze(
    dynamic_root: Path,
    *,
    static_capture: Path,
    float_intrinsic_table: Path,
    square_selector_archive: Path,
    reference_shader: Path,
    reciprocal_encoded_delta: int | None,
    numerator_mode: str,
    coordinate_mode: str,
    curved_distance_mode: str,
    normal_rsqrt_mode: str,
) -> JsonObject:
    reports = _report_paths(dynamic_root)
    if len(reports) != 1:
        raise ValueError(f"expected one dynamic report, found {len(reports)}")
    report_path = reports[0]
    report = mapping(
        json.loads(report_path.read_text(encoding="utf-8")),
        "dynamic report",
    )
    if (report.get("material"), report.get("appearance")) != (
        "clear",
        "light",
    ):
        raise ValueError("unexpected dynamic profile")
    fragment = GLASS_FRAGMENTS["clear"]
    record = _sample_record(report)
    render = mapping(record.get("render"), "dynamic render")
    trace_paths = _trace_paths(report_path.parent, render)
    main, shadow = _background_geometry(dict(render), fragment)
    profile_payload, _ = _uniform_payloads(dict(render), fragment)
    background_scissor, highlight_scissor = _draw_scissors(
        dict(render),
        fragment,
    )

    selector_table = arithmetic.load_selector_table()
    square_calibration = SquareSelectorCalibration.load(square_selector_archive)
    quad = runtime_quad_from_vertices(main.vertices, name="dynamic-sdf-sample-16")
    selector_use = square_calibration.use_for(quad.case, selector_table)
    selectors = list(selector_table)
    selectors[selector_use.table_index] = selector_use.selected
    tile_start, coefficients = coefficient_table(quad, selector_table=selectors)
    axis_start, axes = axis_table(quad, selector_table=selectors)

    local: dict[str, FloatImage] = {}
    source = _instrumented_shader(
        reference_shader,
        numerator_mode=numerator_mode,
        curved_distance_mode=curved_distance_mode,
        normal_rsqrt_mode=normal_rsqrt_mode,
    )
    with AppleGlassReferenceRenderer(
        static_capture,
        fragment_shader_source=source,
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
        renderer.set_mvp_payload(_background_mvp(dict(render), fragment))
        renderer.set_profile_payload(profile_payload)
        renderer.set_source_mip_bgra(
            _raw_mip_levels(
                report_path.parent,
                _source_texture(dict(render), fragment),
            )
        )
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
            slope_bits=slopes_bits(quad, selectors),
        )
        if coordinate_mode == "axis-table":
            renderer.set_interpolant_axis_table(axes, start=axis_start)
            renderer.program["CoordinateMode"].value = 4
        elif coordinate_mode != "compact-coefficients":
            raise ValueError(f"unsupported coordinate mode: {coordinate_mode}")
        reciprocal_override: JsonObject | None = None
        if reciprocal_encoded_delta is not None:
            fields = mapping(
                decode_profile(profile_payload).get("fields"),
                "profile fields",
            )
            sdf_arg2 = mapping(fields.get("sdf_arg2"), "sdf_arg2")
            values = sdf_arg2.get("values")
            if not isinstance(values, list) or len(values) != 4:
                raise ValueError("sdf_arg2 values differ")
            radius = np.float32(values[2])
            circle_constant = np.float32(
                struct.unpack("<f", struct.pack("<I", 0x3FC3_AB4B))[0]
            )
            scale = np.float32(radius * circle_constant)
            scale_bits = int(scale.view(np.uint32))
            mantissa = scale_bits & 0x007F_FFFF
            codes = bytearray(float_intrinsic_table.read_bytes())
            original_code = codes[mantissa]
            corrected_code = (
                original_code & 0x3F
            ) | (reciprocal_encoded_delta << 6)
            codes[mantissa] = corrected_code
            renderer.intrinsic_table_texture.write(codes)
            reciprocal_override = {
                "circleScaleBits": f"0x{scale_bits:08x}",
                "mantissa": mantissa,
                "originalCode": original_code,
                "correctedCode": corrected_code,
                "encodedDelta": reciprocal_encoded_delta,
                "netReciprocalUlpAdjustment": reciprocal_encoded_delta - 1,
            }
        for name, trace in TRACE_NUMBERS.items():
            local[name] = _apply_scissor(
                _render_float_trace(renderer, trace),
                background_scissor,
            )
        implementation = renderer.implementation

    comparisons: JsonObject = {}
    interpolant_path, _ = _trace_outputs(
        report_path.parent,
        render,
        sample_index=SAMPLE_INDEX,
    )
    interpolant = _load_uint(interpolant_path)
    comparisons["point"] = _compare(
        interpolant[..., :2] & np.uint32(0x7FFF_FFFF),
        local["setup"][..., :2].view(np.uint32),
    )
    for name, metal_name in (
        ("geometry", "sdf-geometry"),
        ("oval", "sdf-oval"),
        ("normal", "sdf-normal"),
    ):
        comparisons[name] = _compare(
            _load_uint(trace_paths[metal_name]),
            local[name].view(np.uint32),
        )

    metal_float = _load_uint(trace_paths["sdf-float"])
    comparisons["float32"] = _compare(
        metal_float[..., :3],
        local["float"].view(np.uint32)[..., :3],
    )
    metal_packed = metal_float[..., 3]
    metal_half = np.stack(
        (
            (metal_packed & np.uint32(0xFFFF)).astype(np.uint16),
            (metal_packed >> np.uint32(16)).astype(np.uint16),
        ),
        axis=2,
    )
    local_half = np.stack(
        (
            _half_bits(local["half-pair"][..., 0]),
            _half_bits(local["half-pair"][..., 1]),
        ),
        axis=2,
    )
    comparisons["half-conversion"] = _compare(
        metal_half.astype(np.uint32),
        local_half.astype(np.uint32),
    )

    comparison_order = (
        "point",
        "geometry",
        "oval",
        "float32",
        "half-conversion",
        "normal",
    )
    first_divergent = next(
        (name for name in comparison_order if not comparisons[name]["exact"]),
        None,
    )
    return {
        "liquidGlassDynamicSdfFloatIsolationSchemaVersion": 1,
        "classification": "retrospective post-opening arithmetic isolation",
        "dynamicArtifact": str(dynamic_root),
        "sampleIndex": SAMPLE_INDEX,
        "capturedAppleFunctionUnmodified": False,
        "customMetalComparison": True,
        "numeratorMode": numerator_mode,
        "coordinateMode": coordinate_mode,
        "curvedDistanceMode": curved_distance_mode,
        "normalRsqrtMode": normal_rsqrt_mode,
        "referenceShader": {
            "path": str(reference_shader),
            "sha256BeforeInstrumentation": sha256_file(reference_shader),
            "runtimeInstrumentation": True,
        },
        "implementation": implementation,
        "reciprocalOverride": reciprocal_override,
        "runtimeRaster": {
            "backgroundScissor": list(background_scissor),
            "tileStart": tile_start,
            "selector": {
                "base": selector_use.base,
                "selected": selector_use.selected,
                "offset": selector_use.offset,
            },
        },
        "comparisons": comparisons,
        "localSetup": {
            "circleScaleBits": f"0x{int(local['setup'][..., 2].view(np.uint32).max()):08x}",
            "inverseCircleScaleBits": f"0x{int(local['setup'][..., 3].view(np.uint32).max()):08x}",
        },
        "firstDivergentStage": first_divergent,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dynamic_root", type=Path)
    parser.add_argument("--static-capture", type=Path, required=True)
    parser.add_argument("--float-intrinsic-table", type=Path, required=True)
    parser.add_argument("--square-selector-archive", type=Path, required=True)
    parser.add_argument(
        "--reciprocal-encoded-delta",
        type=int,
        choices=range(4),
    )
    parser.add_argument(
        "--numerator-mode",
        choices=("source-order", "uniform-offset"),
        default="source-order",
    )
    parser.add_argument(
        "--coordinate-mode",
        choices=("compact-coefficients", "axis-table"),
        default="compact-coefficients",
    )
    parser.add_argument(
        "--curved-distance-mode",
        choices=("materialized-float", "contracted-half-fma"),
        default="materialized-float",
    )
    parser.add_argument(
        "--normal-rsqrt-mode",
        choices=(
            "reference",
            "native",
            "float-sqrt-divide",
            "ieee-sqrt-divide",
            "native-newton",
            "integer-corrected",
        ),
        default="reference",
    )
    parser.add_argument(
        "--reference-shader",
        type=Path,
        default=Path("analysis/apple_glass_reference.frag.glsl"),
    )
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    result = analyze(
        arguments.dynamic_root,
        static_capture=arguments.static_capture,
        float_intrinsic_table=arguments.float_intrinsic_table,
        square_selector_archive=arguments.square_selector_archive,
        reference_shader=arguments.reference_shader,
        reciprocal_encoded_delta=arguments.reciprocal_encoded_delta,
        numerator_mode=arguments.numerator_mode,
        coordinate_mode=arguments.coordinate_mode,
        curved_distance_mode=arguments.curved_distance_mode,
        normal_rsqrt_mode=arguments.normal_rsqrt_mode,
    )
    encoded = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if arguments.output is None:
        print(encoded, end="")
    else:
        arguments.output.write_text(encoded, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
