#!/usr/bin/env python3
"""Bit-gate generated runtime raster coefficients through the exact shader."""

import argparse
import hashlib
import json
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
from liquid_glass_runtime_raster_coefficients import (
    coefficient_table,
    runtime_quad,
    sha256_file,
    slopes_bits,
)
from liquid_glass_shader_specialization import (
    load_specialized_exact_final_shader,
)

import raster_tile_selector_model as arithmetic


type JsonObject = dict[str, Any]

REFERENCE_NAME = "carenderer-live-tree-glass-prefix-reference-bgra8.raw"


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def evaluate_capture(
    capture: Path,
    *,
    intrinsic_table: Path,
    shader_source: str,
    selector_table: tuple[int, ...],
) -> JsonObject:
    quad = runtime_quad(capture)
    tile_start, table = coefficient_table(
        quad,
        selector_table=selector_table,
    )
    slopes = slopes_bits(quad, selector_table)
    reference_path = capture / REFERENCE_NAME
    reference = bgra_raw(
        reference_path,
        width=CAPTURE_WIDTH,
        height=CAPTURE_HEIGHT,
    )
    with AppleGlassReferenceRenderer(
        capture,
        fragment_shader_source=shader_source,
        intrinsic_table=intrinsic_table,
        interpolant_coefficient_data=table,
        interpolant_tile_start=tile_start,
        interpolant_slope_bits=slopes,
        load_interpolant_trace=False,
        load_interpolant_axis_trace=False,
        load_diagnostic_traces=False,
    ) as renderer:
        comparison = compare_images(reference, renderer.render()).as_json()
        implementation = renderer.implementation
    encoded_table = np.ascontiguousarray(table, dtype="<u4").tobytes()
    return {
        "capture": str(capture),
        "runtimeJsonSha256": sha256_file(capture / "runtime.json"),
        "reference": {
            "path": str(reference_path),
            "sha256": sha256_file(reference_path),
        },
        "runtimeRasterInput": {
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
            "tileCount": int(table.shape[1]),
            "tableBytes": len(encoded_table),
            "tableSha256": sha256_bytes(encoded_table),
            "slopeBits": [f"0x{value:08x}" for value in slopes],
            "capturedCoordinateTableLoaded": False,
            "capturedCoefficientTableLoaded": False,
        },
        "comparison": comparison,
        "implementation": implementation,
    }


def run_gate(
    captures: list[Path],
    *,
    intrinsic_table: Path,
) -> JsonObject:
    shader_source = load_specialized_exact_final_shader()
    selector_table = arithmetic.load_selector_table()
    measurements = [
        evaluate_capture(
            capture,
            intrinsic_table=intrinsic_table,
            shader_source=shader_source,
            selector_table=selector_table,
        )
        for capture in captures
    ]
    exact = all(
        bool(measurement["comparison"]["exact"])
        for measurement in measurements
    )
    return {
        "liquidGlassRuntimeGeometryImageGateSchemaVersion": 1,
        "implementation": {
            "fragmentShader": "analysis/apple_glass_reference.frag.glsl",
            "specializedShaderSha256": sha256_bytes(
                shader_source.encode("utf-8")
            ),
            "runtimeCoefficientGenerator": (
                "analysis/liquid_glass_runtime_raster_coefficients.py"
            ),
        },
        "intrinsicTable": {
            "path": str(intrinsic_table),
            "bytes": intrinsic_table.stat().st_size,
            "sha256": sha256_file(intrinsic_table),
        },
        "captures": measurements,
        "gate": {
            "captureCount": len(measurements),
            "exact": exact,
            "mismatchedBytes": sum(
                int(measurement["comparison"]["mismatchedBytes"])
                for measurement in measurements
            ),
            "mismatchedPixels": sum(
                int(measurement["comparison"]["mismatchedPixels"])
                for measurement in measurements
            ),
            "generatedRuntimeRasterCoefficients": True,
            "capturedCoordinateOrCoefficientTableLoaded": False,
            "productionShaderAuthorized": False,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("captures", type=Path, nargs="+")
    parser.add_argument(
        "--intrinsic-table",
        type=Path,
        default=Path("artifacts/apple-float-intrinsics-r8-30556057571.bin"),
    )
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    report = run_gate(
        arguments.captures,
        intrinsic_table=arguments.intrinsic_table,
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
