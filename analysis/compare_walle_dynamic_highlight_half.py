#!/usr/bin/env python3
"""Compare Walle's reconstructed highlight alpha with Apple's exact half map."""

import argparse
import json
import struct
import sys
from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import numpy as np


ROOT = Path(__file__).resolve().parent.parent
LG_ANALYSIS = ROOT / "lg-test" / "Analysis"
if str(LG_ANALYSIS) not in sys.path:
    sys.path.insert(0, str(LG_ANALYSIS))

import raster_tile_selector_model as raster_arithmetic  # noqa: E402

from apple_glass_reference_renderer import (  # noqa: E402
    AppleGlassReferenceRenderer,
    DrawGeometry,
)
from liquid_glass_shader_specialization import (  # noqa: E402
    load_amd_exact_circle_shader,
)
from liquid_glass_runtime_raster_coefficients import (  # noqa: E402
    axis_table,
    coefficient_table,
    load_near_square_selector_calibration,
    load_square_selector_calibration,
    runtime_quad_from_vertices,
    selector_table_for_calibrated_quad,
    selector_table_for_square_quad,
    slopes_bits,
)


type JsonObject = dict[str, Any]

WIDTH = 1024
HEIGHT = 1024
SMALL_SQUARE_WIDTH_FIXED_LOWER = 114_688
SMALL_SQUARE_WIDTH_FIXED_UPPER = 147_456
SMALL_SQUARE_SELECTOR_COMPRESSED_SHA256 = (
    "4a701a9868484ec6580026b6328ac99ec38d14d1d4747cd2066964e46498989e"
)
SMALL_SQUARE_SELECTOR_RAW_SHA256 = (
    "9cb148ec4996e77243c397c97f01163ea0a08502239adc8aeecd3e8e64fe6d10"
)
SMALL_NEAR_SQUARE_HEIGHT_FIXED_DELTAS = (
    -256,
    -128,
    -64,
    -32,
    -16,
    -8,
    -4,
    -2,
    -1,
    1,
    2,
    4,
    8,
    16,
    32,
    64,
    128,
    256,
)
SMALL_NEAR_SQUARE_SELECTOR_COMPRESSED_SHA256 = (
    "7d0f0743a894c47518139456d5e7d9d805526126f760650239babde35388bba6"
)
SMALL_NEAR_SQUARE_SELECTOR_RAW_SHA256 = (
    "424fd9e815520c1f6f77840a6b976bf41d2907aecb1d4c82d1ea43fbc152633f"
)
SAMPLES = (1, 4, 8, 12, 16, 20, 24, 28)
MODE_NAMES = frozenset(
    {
        "derivative",
        "coordinate",
        "alphaUlpBias",
        "floatDivision",
        "coverage",
        "mix",
        "band",
        "normalize",
        "normalizedCoordinate",
        "sdfArithmetic",
        "sdfNormal",
        "sdfSquaredUlpBias",
        "sdfDistanceUlpBias",
    }
)


def object_value(value: object, *, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} is not an object")
    return value


def geometry(
    directory: Path,
    *,
    vertex_name: str,
    index_name: str | None,
) -> DrawGeometry:
    vertices = np.fromfile(directory / vertex_name, dtype="<f4")
    if vertices.size % 8:
        raise ValueError(f"{vertex_name} has a partial vertex")
    indices = (
        np.fromfile(directory / index_name, dtype="<u2")
        if index_name is not None
        else None
    )
    return DrawGeometry(
        vertices=vertices.reshape(-1, 8),
        indices=indices,
    )


def source_levels(
    directory: Path,
    construction: Mapping[str, Any],
) -> dict[int, tuple[int, int, bytes]]:
    extent = construction.get("sourceExtent")
    count = construction.get("sourceMipCount")
    if (
        not isinstance(extent, list)
        or len(extent) != 2
        or not all(isinstance(value, int) for value in extent)
        or not isinstance(count, int)
    ):
        raise ValueError("fixture source pyramid metadata differs")
    width, height = extent
    result: dict[int, tuple[int, int, bytes]] = {}
    for level in range(count):
        payload = (directory / f"source-mip-{level}.rgba8").read_bytes()
        expected = width * height * 4
        if len(payload) != expected:
            raise ValueError(
                f"source mip {level} has {len(payload)} bytes; expected {expected}"
            )
        result[level] = (width, height, payload)
        width //= 2
        height //= 2
    return result


def highlight_quad(directory: Path) -> Any:
    vertices = np.fromfile(
        directory / "highlight-vertices.f32",
        dtype="<f4",
    ).reshape(-1, 8)
    indices = np.fromfile(
        directory / "highlight-indices.u16",
        dtype="<u2",
    )
    if indices.size == 0 or indices.size % 6 != 0:
        raise ValueError("final-highlight indices do not contain complete quads")
    expanded = vertices[indices[:6]].copy()
    expanded[:, 6:8] = expanded[:, 4:6]
    return runtime_quad_from_vertices(
        expanded,
        name=directory.name,
    )


def final_highlight_is_back_facing(directory: Path) -> bool:
    """Classify Apple's retained final-highlight winding before the Y-flip MVP."""

    vertices = np.fromfile(
        directory / "highlight-vertices.f32",
        dtype="<f4",
    ).reshape(-1, 8)
    indices = np.fromfile(
        directory / "highlight-indices.u16",
        dtype="<u2",
    )
    if (
        indices.size == 0
        or indices.size % 3 != 0
        or int(indices.max()) >= len(vertices)
    ):
        raise ValueError("final-highlight indices are invalid")
    triangles = vertices[indices].reshape(-1, 3, 8)[..., :2]
    left = triangles[:, 1] - triangles[:, 0]
    right = triangles[:, 2] - triangles[:, 0]
    signed_areas = left[:, 0] * right[:, 1] - left[:, 1] * right[:, 0]
    if np.any(signed_areas == 0.0):
        raise ValueError("final-highlight geometry contains a degenerate triangle")
    if np.all(signed_areas > 0.0):
        return False
    if np.all(signed_areas < 0.0):
        return True
    raise ValueError("final-highlight geometry mixes front and back winding")


def interpolant_configuration(
    directory: Path,
    *,
    source: str,
    anchor_policy: tuple[tuple[bool, bool], tuple[bool, bool]] | None,
    square_selector_calibration: tuple[int, ...] | None,
    near_square_selector_calibration: tuple[int, ...] | None,
    back_facing: bool,
) -> tuple[np.ndarray, int, tuple[int, int, int, int]]:
    if source in {"highlight", "axis-highlight"}:
        quad = highlight_quad(directory)
        base_selector_table = raster_arithmetic.load_selector_table()
        if back_facing or square_selector_calibration is None:
            selector_table = base_selector_table
        elif near_square_selector_calibration is None:
            selector_table = selector_table_for_square_quad(
                quad,
                base_selector_table,
                square_selector_calibration,
                width_fixed_lower=SMALL_SQUARE_WIDTH_FIXED_LOWER,
            )
        else:
            selector_table = selector_table_for_calibrated_quad(
                quad,
                base_selector_table,
                square_selector_calibration,
                near_square_selector_calibration,
                width_fixed_lower=SMALL_SQUARE_WIDTH_FIXED_LOWER,
                height_fixed_deltas=SMALL_NEAR_SQUARE_HEIGHT_FIXED_DELTAS,
            )
        tile_start, coefficients = coefficient_table(
            quad,
            selector_table=selector_table,
            anchor_high_by_primitive_axis=anchor_policy,
        )
        return (
            coefficients,
            tile_start,
            slopes_bits(quad, selector_table),
        )
    if source != "main":
        raise ValueError(f"unsupported interpolant coefficient source: {source}")
    config = (directory / "config.bin").read_bytes()
    if len(config) != 164:
        raise ValueError("dynamic fixture config byte count differs")
    values = struct.unpack_from("<8s11I", config)
    if values[0] != b"WALLELG3":
        raise ValueError("dynamic fixture config magic differs")
    tile_start = values[6]
    width = values[7]
    slopes = tuple(values[8:12])
    raw = np.fromfile(
        directory / "interpolant-coefficients.rgba32ui",
        dtype="<u4",
    )
    if raw.size != 2 * width * 4:
        raise ValueError("interpolant coefficient table byte count differs")
    return raw.reshape(2, width, 4), tile_start, slopes


def trace_records(timeline: Mapping[str, Any]) -> dict[int, Mapping[str, Any]]:
    dynamic = object_value(
        timeline.get("dynamicBackgroundUniforms"),
        name="dynamic background uniforms",
    )
    values = dynamic.get("records")
    if not isinstance(values, list):
        raise ValueError("dynamic background records are absent")
    result: dict[int, Mapping[str, Any]] = {}
    for value in values:
        record = object_value(value, name="dynamic background record")
        sample = record.get("sampleIndex")
        render = object_value(record.get("render"), name="dynamic render")
        replay = object_value(render.get("exactPassReplay"), name="exact replay")
        trace = object_value(
            replay.get("currentFinalHighlightAlphaTrace"),
            name="current-system final-highlight alpha trace",
        )
        comparison = object_value(
            trace.get("capturedVsRebuiltBGRA8"),
            name="captured/system comparison",
        )
        if (
            not isinstance(sample, int)
            or trace.get("executed") is not True
            or trace.get("systemSpecializationExact") is not True
            or comparison.get("exactByteMatch") is not True
            or comparison.get("mismatchedByteCount") != 0
        ):
            raise ValueError("current-system alpha trace did not pass exactly")
        result[sample] = trace
    if set(result) != set(SAMPLES):
        raise ValueError(f"alpha-trace samples differ: {sorted(result)}")
    return result


def apple_alpha(
    capture: Path,
    trace: Mapping[str, Any],
) -> tuple[np.ndarray, JsonObject]:
    exact = object_value(trace.get("exactHalfAlpha"), name="exact half alpha")
    output = object_value(exact.get("output"), name="exact half output")
    raw_name = output.get("rawFile")
    if (
        not isinstance(raw_name, str)
        or output.get("width") != WIDTH
        or output.get("height") != HEIGHT
        or output.get("rawBytes") != WIDTH * HEIGHT * 8
    ):
        raise ValueError("exact half-alpha output metadata differs")
    words = np.fromfile(capture / raw_name, dtype="<u2")
    if words.size != WIDTH * HEIGHT * 4:
        raise ValueError("exact half-alpha output is truncated")
    pixels = words.reshape(HEIGHT, WIDTH, 4)
    rgb_equal = np.all(pixels[..., :3] == pixels[..., :1], axis=2)
    active = pixels[..., 0] != 0
    alpha_valid = np.all(pixels[..., 3] == 0x3C00)
    clear_valid = np.all(pixels[..., :3][~active] == 0)
    if not np.all(rgb_equal) or not alpha_valid or not clear_valid:
        raise ValueError("Apple alpha-oracle matrix output semantics differ")
    return pixels[..., 0].copy(), {
        "rawFile": raw_name,
        "activePixels": int(np.count_nonzero(active)),
        "rgbChannelsEqual": True,
        "outputAlphaIsOne": True,
        "inactiveRgbIsZero": True,
    }


def apple_interpolant(
    capture: Path,
    trace: Mapping[str, Any],
) -> tuple[np.ndarray, JsonObject]:
    exact = object_value(trace.get("exactInterpolant"), name="exact interpolant")
    output = object_value(exact.get("output"), name="exact interpolant output")
    raw_name = output.get("rawFile")
    if (
        exact.get("executed") is not True
        or not isinstance(raw_name, str)
        or output.get("width") != WIDTH
        or output.get("height") != HEIGHT
        or output.get("rawBytes") != WIDTH * HEIGHT * 16
    ):
        raise ValueError("exact interpolant output metadata differs")
    words = np.fromfile(capture / raw_name, dtype="<u4")
    if words.size != WIDTH * HEIGHT * 4:
        raise ValueError("exact interpolant output is truncated")
    pixels = words.reshape(HEIGHT, WIDTH, 4)
    return pixels, {
        "rawFile": raw_name,
        "rasterizedPixels": int(np.count_nonzero(np.any(pixels != 0, axis=2))),
        "components": ["sdf-x", "sdf-y", "source-x", "source-y"],
    }


def decode_trace_words(pixels: np.ndarray) -> np.ndarray:
    values = pixels.view("<f2").astype(np.float32)
    scaled = values * 255.0
    encoded = np.rint(scaled).astype(np.uint32)
    if np.any(np.abs(scaled - encoded.astype(np.float32)) >= 0.5):
        raise ValueError("binary16 trace byte encoding does not round-trip")
    return (
        encoded[..., 0]
        | (encoded[..., 1] << 8)
        | (encoded[..., 2] << 16)
        | (encoded[..., 3] << 24)
    )


def compare_sample(
    *,
    fixture: Path,
    apple: np.ndarray,
    apple_interpolants: np.ndarray,
    shader: str,
    intrinsic_table: Path,
    device_index: int,
    mode_overrides: Mapping[str, int],
    interpolant_geometry: str,
    anchor_policy: tuple[tuple[bool, bool], tuple[bool, bool]] | None,
    square_selector_calibration: tuple[int, ...] | None,
    near_square_selector_calibration: tuple[int, ...] | None,
) -> JsonObject:
    manifest = json.loads((fixture / "manifest.json").read_text(encoding="utf-8"))
    construction = object_value(manifest.get("construction"), name="construction")
    captured_modes = object_value(
        construction.get("highlightArithmeticModes"),
        name="highlight arithmetic modes",
    )
    modes = {**captured_modes, **mode_overrides}
    back_facing = final_highlight_is_back_facing(fixture)
    coefficients, tile_start, slopes = interpolant_configuration(
        fixture,
        source=interpolant_geometry,
        anchor_policy=anchor_policy,
        square_selector_calibration=square_selector_calibration,
        near_square_selector_calibration=near_square_selector_calibration,
        back_facing=back_facing,
    )
    renderer_arguments: dict[str, Any] = {
        "fragment_shader_source": shader,
        "intrinsic_table": intrinsic_table,
        "interpolant_coefficient_data": coefficients,
        "interpolant_tile_start": tile_start,
        "interpolant_slope_bits": slopes,
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
        "load_interpolant_axis_trace": False,
        "load_diagnostic_traces": False,
        "context_arguments": {"device_index": device_index},
    }
    if interpolant_geometry == "axis-highlight":
        quad = highlight_quad(fixture)
        base_selector_table = raster_arithmetic.load_selector_table()
        if back_facing or square_selector_calibration is None:
            selector_table = base_selector_table
        elif near_square_selector_calibration is None:
            selector_table = selector_table_for_square_quad(
                quad,
                base_selector_table,
                square_selector_calibration,
                width_fixed_lower=SMALL_SQUARE_WIDTH_FIXED_LOWER,
            )
        else:
            selector_table = selector_table_for_calibrated_quad(
                quad,
                base_selector_table,
                square_selector_calibration,
                near_square_selector_calibration,
                width_fixed_lower=SMALL_SQUARE_WIDTH_FIXED_LOWER,
                height_fixed_deltas=SMALL_NEAR_SQUARE_HEIGHT_FIXED_DELTAS,
            )
        axis_start, axis_data = axis_table(
            quad,
            selector_table=selector_table,
            anchor_high_by_primitive_axis=anchor_policy,
        )
        renderer_arguments.update(
            {
                "interpolant_axis_data": axis_data,
                "interpolant_axis_start": axis_start,
                "load_interpolant_axis_trace": True,
            }
        )
    with AppleGlassReferenceRenderer(fixture, **renderer_arguments) as renderer:
        uniforms = {
            "HighlightDerivativeMode": modes["derivative"],
            "HighlightCoordinateMode": modes["coordinate"],
            "HighlightAlphaUlpBias": modes["alphaUlpBias"],
            "HighlightFloatDivisionMode": modes["floatDivision"],
            "HighlightCoverageArithmeticMode": modes["coverage"],
            "HighlightMixMode": modes["mix"],
            "HighlightBandMode": modes["band"],
            "HighlightNormalizeMode": modes["normalize"],
            "HighlightNormalizedCoordinateMode": modes["normalizedCoordinate"],
            "HighlightSdfArithmeticMode": modes["sdfArithmetic"],
            "HighlightSdfNormalMode": modes.get("sdfNormal", 0),
            "HighlightSdfSquaredUlpBias": modes["sdfSquaredUlpBias"],
            "HighlightSdfDistanceUlpBias": modes["sdfDistanceUlpBias"],
        }
        for name, value in uniforms.items():
            renderer.program[name].value = value
        candidate_pixels = renderer.render_final_highlight_half(
            uniform_payload=(fixture / "highlight-uniform.bin").read_bytes(),
            trace_mode=2,
        )
        candidate_sdf_x = decode_trace_words(
            renderer.render_final_highlight_half(
                uniform_payload=(fixture / "highlight-uniform.bin").read_bytes(),
                trace_mode=40,
            )
        )
        candidate_sdf_y = decode_trace_words(
            renderer.render_final_highlight_half(
                uniform_payload=(fixture / "highlight-uniform.bin").read_bytes(),
                trace_mode=41,
            )
        )
        implementation = renderer.implementation

    if not np.all(candidate_pixels == candidate_pixels[..., :1]):
        raise ValueError("Walle alpha trace channels differ")
    candidate = candidate_pixels[..., 0]
    mismatch = apple != candidate
    mismatch_coordinates = np.argwhere(mismatch)
    pairs = Counter(
        (int(apple[y, x]), int(candidate[y, x])) for y, x in mismatch_coordinates
    )
    bit_distance = np.abs(apple.astype(np.int32) - candidate.astype(np.int32))
    candidate_interpolants = np.stack(
        (candidate_sdf_x, candidate_sdf_y),
        axis=2,
    )
    interpolant_mismatch = apple_interpolants[..., :2] != candidate_interpolants
    interpolant_pixel_mismatch = np.any(interpolant_mismatch, axis=2)
    interpolant_coordinates = np.argwhere(interpolant_pixel_mismatch)
    return {
        "sampleIndex": manifest["sampleIndex"],
        "remainingFloat32Bits": manifest["remainingFloat32Bits"],
        "finalHighlightBackFacing": back_facing,
        "highlightArithmeticModes": modes,
        "implementation": implementation,
        "checkedHalfWords": WIDTH * HEIGHT,
        "mismatchedHalfWords": int(np.count_nonzero(mismatch)),
        "maximumPositiveHalfBitDistance": int(bit_distance.max(initial=0)),
        "appleActivePixels": int(np.count_nonzero(apple)),
        "candidateActivePixels": int(np.count_nonzero(candidate)),
        "firstMismatches": [
            {
                "x": int(x),
                "y": int(y),
                "appleBits": f"0x{int(apple[y, x]):04x}",
                "candidateBits": f"0x{int(candidate[y, x]):04x}",
            }
            for y, x in mismatch_coordinates[:32]
        ],
        "mismatchPairs": [
            {
                "appleBits": f"0x{left:04x}",
                "candidateBits": f"0x{right:04x}",
                "count": count,
            }
            for (left, right), count in pairs.most_common()
        ],
        "sdfInterpolants": {
            "checkedWords": WIDTH * HEIGHT * 2,
            "mismatchedWords": int(np.count_nonzero(interpolant_mismatch)),
            "mismatchedPixels": int(np.count_nonzero(interpolant_pixel_mismatch)),
            "appleRasterizedPixels": int(
                np.count_nonzero(np.any(apple_interpolants != 0, axis=2))
            ),
            "candidateRasterizedPixels": int(
                np.count_nonzero(np.any(candidate_interpolants != 0, axis=2))
            ),
            "firstMismatches": [
                {
                    "x": int(x),
                    "y": int(y),
                    "appleXBits": f"0x{int(apple_interpolants[y, x, 0]):08x}",
                    "candidateXBits": f"0x{int(candidate_interpolants[y, x, 0]):08x}",
                    "appleYBits": f"0x{int(apple_interpolants[y, x, 1]):08x}",
                    "candidateYBits": f"0x{int(candidate_interpolants[y, x, 1]):08x}",
                }
                for y, x in interpolant_coordinates[:32]
            ],
        },
    }


def run(arguments: argparse.Namespace) -> JsonObject:
    timeline = json.loads(
        (arguments.capture / "transition-timeline.json").read_text(encoding="utf-8")
    )
    records = trace_records(timeline)
    square_selector_calibration = (
        load_square_selector_calibration(
            arguments.square_selector_calibration,
            width_fixed_lower=SMALL_SQUARE_WIDTH_FIXED_LOWER,
            width_fixed_upper=SMALL_SQUARE_WIDTH_FIXED_UPPER,
            expected_compressed_sha256=(SMALL_SQUARE_SELECTOR_COMPRESSED_SHA256),
            expected_raw_sha256=SMALL_SQUARE_SELECTOR_RAW_SHA256,
        )
        if arguments.square_selector_calibration is not None
        else None
    )
    near_square_selector_calibration = (
        load_near_square_selector_calibration(
            arguments.near_square_selector_calibration,
            width_fixed_lower=SMALL_SQUARE_WIDTH_FIXED_LOWER,
            width_fixed_upper=SMALL_SQUARE_WIDTH_FIXED_UPPER,
            height_fixed_deltas=SMALL_NEAR_SQUARE_HEIGHT_FIXED_DELTAS,
            expected_compressed_sha256=(SMALL_NEAR_SQUARE_SELECTOR_COMPRESSED_SHA256),
            expected_raw_sha256=SMALL_NEAR_SQUARE_SELECTOR_RAW_SHA256,
        )
        if arguments.near_square_selector_calibration is not None
        else None
    )
    shader = (
        arguments.fragment_shader.read_text(encoding="utf-8")
        if arguments.coordinate_mode is None
        else load_amd_exact_circle_shader(
            "regular",
            ROOT / "analysis/apple_glass_reference.frag.glsl",
            coordinate_mode=arguments.coordinate_mode,
        )
    )
    cases: list[JsonObject] = []
    oracles: dict[str, JsonObject] = {}
    samples = arguments.sample_index or SAMPLES
    for sample in samples:
        alpha, alpha_oracle = apple_alpha(arguments.capture, records[sample])
        interpolants, interpolant_oracle = apple_interpolant(
            arguments.capture,
            records[sample],
        )
        oracles[str(sample)] = {
            "alpha": alpha_oracle,
            "interpolant": interpolant_oracle,
        }
        cases.append(
            compare_sample(
                fixture=(
                    arguments.fixtures / f"regular-dark-dematerialize-{sample:02d}"
                ),
                apple=alpha,
                apple_interpolants=interpolants,
                shader=shader,
                intrinsic_table=arguments.intrinsic_table,
                device_index=arguments.device_index,
                mode_overrides=arguments.mode,
                interpolant_geometry=arguments.interpolant_geometry,
                anchor_policy=arguments.anchor_policy,
                square_selector_calibration=square_selector_calibration,
                near_square_selector_calibration=(near_square_selector_calibration),
            )
        )
    return {
        "schemaVersion": 1,
        "scope": "natural current-Iscd internal binary16 highlight alpha",
        "capture": str(arguments.capture),
        "fixtures": str(arguments.fixtures),
        "coordinateMode": arguments.coordinate_mode,
        "modeOverrides": arguments.mode,
        "interpolantGeometry": arguments.interpolant_geometry,
        "anchorPolicy": arguments.anchor_policy_text,
        "squareSelectorCalibration": (
            str(arguments.square_selector_calibration)
            if arguments.square_selector_calibration is not None
            else None
        ),
        "nearSquareSelectorCalibration": (
            str(arguments.near_square_selector_calibration)
            if arguments.near_square_selector_calibration is not None
            else None
        ),
        "appleOracles": oracles,
        "cases": cases,
        "totals": {
            "checkedHalfWords": sum(case["checkedHalfWords"] for case in cases),
            "mismatchedHalfWords": sum(case["mismatchedHalfWords"] for case in cases),
            "maximumPositiveHalfBitDistance": max(
                case["maximumPositiveHalfBitDistance"] for case in cases
            ),
            "checkedSdfInterpolantWords": sum(
                case["sdfInterpolants"]["checkedWords"] for case in cases
            ),
            "mismatchedSdfInterpolantWords": sum(
                case["sdfInterpolants"]["mismatchedWords"] for case in cases
            ),
            "mismatchedSdfInterpolantPixels": sum(
                case["sdfInterpolants"]["mismatchedPixels"] for case in cases
            ),
        },
    }


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--capture",
        type=Path,
        default=(ROOT / "artifacts/local-natural-walle-current-alpha-interpolant-02"),
    )
    parser.add_argument(
        "--fixtures",
        type=Path,
        default=(
            ROOT
            / "build/generated/liquid-glass"
            / "dynamic-current-alpha-interpolant-fixtures"
        ),
    )
    parser.add_argument(
        "--fragment-shader",
        type=Path,
        default=(
            ROOT
            / "build/generated/liquid-glass/desktop"
            / "apple_glass_exact_regular.frag.glsl"
        ),
    )
    parser.add_argument(
        "--intrinsic-table",
        type=Path,
        default=(ROOT / "artifacts/apple-float-intrinsics-r8-30556057571.bin"),
    )
    parser.add_argument("--device-index", type=int, default=0)
    parser.add_argument("--coordinate-mode", type=int, choices=(0, 4, 5))
    parser.add_argument(
        "--interpolant-geometry",
        choices=("main", "highlight", "axis-highlight"),
        default="main",
    )
    parser.add_argument("--anchor-policy")
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
    parser.add_argument(
        "--sample-index",
        type=int,
        choices=SAMPLES,
        action="append",
    )
    parser.add_argument("--mode", action="append", default=[])
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    overrides: dict[str, int] = {}
    for value in arguments.mode:
        name, separator, raw = value.partition("=")
        if separator != "=" or name not in MODE_NAMES:
            parser.error(f"unsupported highlight mode override: {value}")
        try:
            parsed = int(raw)
        except ValueError:
            parser.error(f"highlight mode override is not an integer: {value}")
        if name in overrides:
            parser.error(f"duplicate highlight mode override: {name}")
        overrides[name] = parsed
    arguments.mode = overrides
    arguments.anchor_policy_text = arguments.anchor_policy
    if arguments.anchor_policy is None:
        arguments.anchor_policy = None
    elif len(arguments.anchor_policy) == 4 and set(arguments.anchor_policy) <= {
        "0",
        "1",
    }:
        bits = tuple(value == "1" for value in arguments.anchor_policy)
        arguments.anchor_policy = (
            (bits[0], bits[1]),
            (bits[2], bits[3]),
        )
    else:
        parser.error("anchor policy must be four bits: p0x,p0y,p1x,p1y")
    if arguments.sample_index is not None:
        if len(set(arguments.sample_index)) != len(arguments.sample_index):
            parser.error("sample indices must be unique")
        arguments.sample_index = tuple(arguments.sample_index)
    return arguments


def main() -> int:
    arguments = parse_arguments()
    result = run(arguments)
    encoded = json.dumps(result, indent=2, sort_keys=True) + "\n"
    print(encoded, end="")
    if arguments.output is not None:
        arguments.output.write_text(encoded, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
