#!/usr/bin/env python3
"""Locate Walle's natural background residual in captured arithmetic stages."""

import argparse
import hashlib
import json
import struct
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from apple_glass_reference_renderer import (
    CAPTURE_HEIGHT,
    CAPTURE_WIDTH,
    AppleGlassReferenceRenderer,
    DrawGeometry,
)


type JsonObject = dict[str, Any]
type HalfImage = NDArray[np.uint16]
type UIntImage = NDArray[np.uint32]

ROOT = Path(__file__).resolve().parent.parent
PIXEL_COUNT = CAPTURE_WIDTH * CAPTURE_HEIGHT
CONFIG = struct.Struct("<8s39I")
TRACE_FILES = {
    "sdf": "transition-background-uniform-16-glass-dynamic-main-sdf-numeric-trace-rgba16f.raw",
    "sdf-coverage": "transition-background-uniform-16-glass-dynamic-main-sdf-coverage-numeric-trace-rgba32ui.raw",
    "color-stages-a": "transition-background-uniform-16-glass-dynamic-main-color-stages-a-numeric-trace-rgba32ui.raw",
    "color-stages-b": "transition-background-uniform-16-glass-dynamic-main-color-stages-b-numeric-trace-rgba32ui.raw",
    "final-color": "transition-background-uniform-16-glass-dynamic-main-final-color-numeric-trace-rgba16f.raw",
}
APPLE_REFERENCE_FILE = (
    "transition-background-uniform-16-pre-final-highlight-reference-bgra8.raw"
)
EXACT_CONFIGURATION = {
    "SamplerSpatialQuantization": 0,
    "SamplerModel": 0,
    "InnerSamplerCoordinateModel": 3,
    "OuterSamplerCoordinateModel": 1,
    "EdgeSamplerCoordinateModel": 1,
    "ShadowSamplerCoordinateModel": 2,
    "RefractionMixModel": 0,
    "HoldingMixMode": 0,
    "HoldingDivideMode": 0,
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


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1 << 20):
            digest.update(block)
    return digest.hexdigest()


def load_config(path: Path) -> JsonObject:
    payload = path.read_bytes()
    if len(payload) != CONFIG.size:
        raise ValueError(
            f"fixture config has {len(payload)} bytes; expected {CONFIG.size}"
        )
    magic, *words = CONFIG.unpack(payload)
    if magic != b"WALLELG3":
        raise ValueError(f"fixture config magic differs: {magic!r}")
    if words[0:2] != [CAPTURE_WIDTH, CAPTURE_HEIGHT]:
        raise ValueError(f"fixture dimensions differ: {words[0:2]}")
    return {
        "width": words[0],
        "height": words[1],
        "mipCount": words[4],
        "tileStart": words[5],
        "coefficientWidth": words[6],
        "slopeBits": tuple(words[7:11]),
        "sourceWidth": words[11],
        "sourceHeight": words[12],
        "mainVertexCount": words[13],
        "shadowVertexCount": words[14],
        "shadowIndexCount": words[15],
        "backgroundScissorGL": tuple(words[19:23]),
    }


def load_geometry(
    fixture: Path,
    config: JsonObject,
) -> tuple[DrawGeometry, DrawGeometry]:
    main_vertices = np.fromfile(fixture / "main-vertices.f32", dtype="<f4").reshape(
        int(config["mainVertexCount"]), 8
    )
    shadow_vertices = np.fromfile(fixture / "shadow-vertices.f32", dtype="<f4").reshape(
        int(config["shadowVertexCount"]), 8
    )
    shadow_indices = np.fromfile(fixture / "shadow-indices.u16", dtype="<u2")
    if shadow_indices.size != config["shadowIndexCount"]:
        raise ValueError("shadow index count differs from config")
    return (
        DrawGeometry(vertices=main_vertices.copy(), indices=None),
        DrawGeometry(vertices=shadow_vertices.copy(), indices=shadow_indices.copy()),
    )


def source_levels(
    fixture: Path, config: JsonObject
) -> dict[int, tuple[int, int, bytes]]:
    levels: dict[int, tuple[int, int, bytes]] = {}
    width = int(config["sourceWidth"])
    height = int(config["sourceHeight"])
    for level in range(int(config["mipCount"])):
        path = fixture / f"source-mip-{level}.rgba8"
        payload = path.read_bytes()
        if len(payload) != width * height * 4:
            raise ValueError(f"source mip {level} byte count differs")
        levels[level] = (width, height, payload)
        width = max(1, width // 2)
        height = max(1, height // 2)
    return levels


def coefficients(fixture: Path, width: int) -> UIntImage:
    values = np.fromfile(
        fixture / "interpolant-coefficients.rgba32ui",
        dtype="<u4",
    )
    expected = 2 * width * 4
    if values.size != expected:
        raise ValueError(
            f"coefficient table has {values.size} words; expected {expected}"
        )
    return values.reshape(2, width, 4)


def load_half(path: Path) -> HalfImage:
    values = np.fromfile(path, dtype="<u2")
    if values.size != PIXEL_COUNT * 4:
        raise ValueError(f"{path} has an invalid RGBA16Float byte count")
    return values.reshape(CAPTURE_HEIGHT, CAPTURE_WIDTH, 4)


def load_uint(path: Path) -> UIntImage:
    values = np.fromfile(path, dtype="<u4")
    if values.size != PIXEL_COUNT * 4:
        raise ValueError(f"{path} has an invalid RGBA32Uint byte count")
    return values.reshape(CAPTURE_HEIGHT, CAPTURE_WIDTH, 4)


def unpack_half_pairs(values: UIntImage) -> HalfImage:
    result = np.empty((*values.shape[:2], 8), dtype=np.uint16)
    result[..., 0::2] = values & np.uint32(0xFFFF)
    result[..., 1::2] = values >> np.uint32(16)
    return result


def apply_top_left_scissor(
    values: HalfImage,
    scissor: tuple[int, int, int, int],
) -> HalfImage:
    x, y, width, height = scissor
    result = np.zeros_like(values)
    result[y : y + height, x : x + width] = values[y : y + height, x : x + width]
    return result


def compare_words(reference: HalfImage, candidate: HalfImage) -> JsonObject:
    if reference.shape != candidate.shape:
        raise ValueError(f"trace shapes differ: {reference.shape} != {candidate.shape}")
    changed = reference != candidate
    changed_pixels = np.any(changed, axis=2)
    coordinates = np.argwhere(changed_pixels)
    return {
        "comparedWords": int(reference.size),
        "mismatchedWords": int(np.count_nonzero(changed)),
        "mismatchedPixels": int(np.count_nonzero(changed_pixels)),
        "exact": not bool(np.any(changed)),
        "firstMismatches": [
            {
                "x": int(x),
                "yTopLeft": int(y),
                "channels": np.flatnonzero(changed[y, x]).astype(int).tolist(),
                "appleHex": [f"0x{int(value):04x}" for value in reference[y, x]],
                "walleHex": [f"0x{int(value):04x}" for value in candidate[y, x]],
            }
            for y, x in coordinates[:32]
        ],
    }


def compare_pixels(
    reference: NDArray[np.uint8], candidate: NDArray[np.uint8]
) -> JsonObject:
    delta = candidate.astype(np.int16) - reference.astype(np.int16)
    changed = delta != 0
    changed_pixels = np.any(changed, axis=2)
    coordinates = np.argwhere(changed_pixels)
    return {
        "checkedBytes": int(reference.size),
        "mismatchedBytes": int(np.count_nonzero(changed)),
        "mismatchedPixels": int(np.count_nonzero(changed_pixels)),
        "maximumChannelDelta": int(np.abs(delta).max(initial=0)),
        "firstMismatches": [
            {
                "x": int(x),
                "yTopLeft": int(y),
                "apple": reference[y, x].astype(int).tolist(),
                "walle": candidate[y, x].astype(int).tolist(),
            }
            for y, x in coordinates[:32]
        ],
        "coordinates": [(int(y), int(x)) for y, x in coordinates],
    }


def trace_values_at(
    coordinates: list[tuple[int, int]],
    traces: dict[str, tuple[HalfImage, HalfImage]],
) -> list[JsonObject]:
    records: list[JsonObject] = []
    for y, x in coordinates:
        records.append(
            {
                "x": x,
                "yTopLeft": y,
                "yBottomLeft": CAPTURE_HEIGHT - 1 - y,
                "stages": {
                    name: {
                        "appleHex": [f"0x{int(value):04x}" for value in apple[y, x]],
                        "walleHex": [f"0x{int(value):04x}" for value in walle[y, x]],
                        "exact": bool(np.array_equal(apple[y, x], walle[y, x])),
                    }
                    for name, (apple, walle) in traces.items()
                },
            }
        )
    return records


def analyze(arguments: argparse.Namespace) -> JsonObject:
    config = load_config(arguments.fixture / "config.bin")
    main, shadow = load_geometry(arguments.fixture, config)
    source = source_levels(arguments.fixture, config)
    coefficient_data = coefficients(arguments.fixture, int(config["coefficientWidth"]))
    interpolant_trace_path = arguments.fixture / "interpolant-trace.rgba32ui"
    interpolant_trace = (
        load_uint(interpolant_trace_path) if interpolant_trace_path.is_file() else None
    )
    scissor_gl = tuple(int(value) for value in config["backgroundScissorGL"])
    x, y, width, height = scissor_gl
    scissor_top_left = (x, CAPTURE_HEIGHT - y - height, width, height)

    trace_paths = {
        name: arguments.capture / filename for name, filename in TRACE_FILES.items()
    }
    for path in trace_paths.values():
        if not path.is_file():
            raise ValueError(f"captured trace is absent: {path}")

    with AppleGlassReferenceRenderer(
        arguments.fixture,
        vertex_shader=arguments.vertex_shader,
        fragment_shader=arguments.fragment_shader,
        intrinsic_table=arguments.intrinsic_table,
        interpolant_coefficient_data=coefficient_data,
        interpolant_trace_data=interpolant_trace,
        interpolant_tile_start=int(config["tileStart"]),
        interpolant_slope_bits=tuple(int(value) for value in config["slopeBits"]),
        load_interpolant_trace=interpolant_trace is not None,
        load_interpolant_axis_trace=False,
        load_diagnostic_traces=False,
        source_mip_bgra_levels=source,
        destination_bgra_data=(arguments.fixture / "destination.rgba8").read_bytes(),
        main_geometry=main,
        shadow_geometry=shadow,
        profile_payload=(arguments.fixture / "profile.bin").read_bytes(),
        runtime_data={},
    ) as renderer:
        configuration = {
            **EXACT_CONFIGURATION,
            "UseAppleInterpolantTrace": int(interpolant_trace is not None),
        }
        for name, value in configuration.items():
            renderer.program[name].value = value
        renderer.set_draw_scissors(
            background=scissor_top_left,
            final_highlight=(0, 0, CAPTURE_WIDTH, CAPTURE_HEIGHT),
        )

        apple_bgra_top_left = np.fromfile(
            arguments.capture / APPLE_REFERENCE_FILE,
            dtype=np.uint8,
        ).reshape(CAPTURE_HEIGHT, CAPTURE_WIDTH, 4)
        reference_top_left = np.ascontiguousarray(
            apple_bgra_top_left[..., [2, 1, 0, 3]]
        )
        endpoint = compare_pixels(reference_top_left, renderer.render())

        local_sdf = apply_top_left_scissor(
            renderer.render_numeric_trace(1), scissor_top_left
        )
        local_coverage = apply_top_left_scissor(
            renderer.render_numeric_trace(8), scissor_top_left
        )
        local_source = apply_top_left_scissor(
            renderer.render_numeric_trace(10), scissor_top_left
        )
        local_face = apply_top_left_scissor(
            renderer.render_numeric_trace(11), scissor_top_left
        )
        local_composite = apply_top_left_scissor(
            renderer.render_numeric_trace(12), scissor_top_left
        )
        local_holding = apply_top_left_scissor(
            renderer.render_numeric_trace(13), scissor_top_left
        )
        local_final = apply_top_left_scissor(
            renderer.render_numeric_trace(9), scissor_top_left
        )
        implementation = renderer.implementation

    apple_coverage = (
        load_uint(trace_paths["sdf-coverage"]) & np.uint32(0xFFFF)
    ).astype(np.uint16)
    apple_color_stages_a = unpack_half_pairs(load_uint(trace_paths["color-stages-a"]))
    apple_color_stages_b = unpack_half_pairs(load_uint(trace_paths["color-stages-b"]))
    traces = {
        "sdf": (load_half(trace_paths["sdf"]), local_sdf),
        "sdf-coverage": (apple_coverage, local_coverage),
        "source": (apple_color_stages_a[..., :4], local_source),
        "face": (apple_color_stages_a[..., 4:], local_face),
        "composite": (apple_color_stages_b[..., :4], local_composite),
        "holding": (apple_color_stages_b[..., 4:], local_holding),
        "final-color": (load_half(trace_paths["final-color"]), local_final),
    }
    coordinates = list(endpoint.pop("coordinates"))
    stage_comparisons = {
        name: compare_words(apple, walle) for name, (apple, walle) in traces.items()
    }
    first_divergent_stage = next(
        (
            name
            for name, comparison in stage_comparisons.items()
            if not comparison["exact"]
        ),
        None,
    )
    return {
        "schemaVersion": 1,
        "classification": "natural background arithmetic stage isolation",
        "fixture": str(arguments.fixture),
        "capture": str(arguments.capture),
        "inputs": {
            "fixtureManifestSHA256": sha256_file(arguments.fixture / "manifest.json"),
            "fragmentShaderSHA256": sha256_file(arguments.fragment_shader),
            "floatIntrinsicTableSHA256": sha256_file(arguments.intrinsic_table),
            "traceSHA256": {
                name: sha256_file(path) for name, path in trace_paths.items()
            },
        },
        "implementation": implementation,
        "backgroundScissor": {
            "glBottomLeft": list(scissor_gl),
            "metalTopLeft": list(scissor_top_left),
        },
        "endpoint": endpoint,
        "stageComparisons": stage_comparisons,
        "firstDivergentStage": first_divergent_stage,
        "endpointMismatchStageValues": trace_values_at(coordinates, traces),
    }


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fixture",
        type=Path,
        default=ROOT
        / "build/generated/liquid-glass/dynamic-current-alpha-interpolant-fixtures/regular-dark-dematerialize-16",
    )
    parser.add_argument(
        "--capture",
        type=Path,
        default=ROOT / "artifacts/local-natural-walle-current-alpha-interpolant-02",
    )
    parser.add_argument(
        "--vertex-shader",
        type=Path,
        default=ROOT / "analysis/apple_glass_reference.vert.glsl",
    )
    parser.add_argument(
        "--fragment-shader",
        type=Path,
        default=ROOT / "analysis/apple_glass_reference.frag.glsl",
    )
    parser.add_argument(
        "--intrinsic-table",
        type=Path,
        default=ROOT / "artifacts/apple-float-intrinsics-r8-30556057571.bin",
    )
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def main() -> int:
    arguments = parse_arguments()
    result = analyze(arguments)
    encoded = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if arguments.output is None:
        print(encoded, end="")
    else:
        arguments.output.write_text(encoded, encoding="utf-8")
        print(arguments.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
