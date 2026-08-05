#!/usr/bin/env python3
"""Bit-gate the complete recovered static Apple Liquid Glass renderer."""

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

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
    slopes_bits,
)
from liquid_glass_shader_specialization import (
    load_specialized_exact_final_shader,
)

import raster_tile_selector_model as arithmetic


type JsonObject = dict[str, Any]

PREFIX_REFERENCE = "carenderer-live-tree-glass-prefix-reference-bgra8.raw"
COMPLETE_REFERENCE = "carenderer-live-tree-bgra8.raw"
HIGHLIGHT_CONFIGURATION = {
    "UseAppleHalfIntrinsicTable": 1,
    "HighlightCoordinateMode": 0,
    "HighlightDerivativeMode": 1,
    "HighlightCoverageArithmeticMode": 1,
    "HighlightFloatDivisionMode": 3,
    "HighlightNormalizeMode": 1,
    "HighlightVibrantArithmeticMode": 9,
    "HighlightSourceDivisionMode": 0,
    "HighlightSourceConstructionMode": 1,
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def run_gate(
    capture: Path,
    *,
    float_intrinsic_table: Path,
) -> JsonObject:
    half_intrinsic_table = capture / "half-intrinsics.bin"
    for path in (float_intrinsic_table, half_intrinsic_table):
        if not path.is_file():
            raise ValueError(f"intrinsic evidence is missing: {path}")

    selector_table = arithmetic.load_selector_table()
    quad = runtime_quad(capture)
    tile_start, coefficients = coefficient_table(
        quad,
        selector_table=selector_table,
    )
    slopes = slopes_bits(quad, selector_table)
    shader_source = load_specialized_exact_final_shader()

    prefix_path = capture / PREFIX_REFERENCE
    complete_path = capture / COMPLETE_REFERENCE
    prefix_reference = bgra_raw(
        prefix_path,
        width=CAPTURE_WIDTH,
        height=CAPTURE_HEIGHT,
    )
    complete_reference = bgra_raw(
        complete_path,
        width=CAPTURE_WIDTH,
        height=CAPTURE_HEIGHT,
    )
    with AppleGlassReferenceRenderer(
        capture,
        fragment_shader_source=shader_source,
        intrinsic_table=float_intrinsic_table,
        half_intrinsic_table=half_intrinsic_table,
        interpolant_coefficient_data=coefficients,
        interpolant_tile_start=tile_start,
        interpolant_slope_bits=slopes,
        load_interpolant_trace=False,
        load_interpolant_axis_trace=False,
        load_diagnostic_traces=False,
    ) as renderer:
        for name, value in HIGHLIGHT_CONFIGURATION.items():
            renderer.program[name].value = value
        prefix_comparison = compare_images(
            prefix_reference,
            renderer.render(),
        ).as_json()
        complete_comparison = compare_images(
            complete_reference,
            renderer.render_complete(),
        ).as_json()
        implementation = renderer.implementation

    exact = bool(prefix_comparison["exact"]) and bool(complete_comparison["exact"])
    return {
        "liquidGlassCompleteStaticGateSchemaVersion": 1,
        "capture": {
            "path": str(capture),
            "runtimeSHA256": sha256_file(capture / "runtime.json"),
            "prefixReference": {
                "path": str(prefix_path),
                "sha256": sha256_file(prefix_path),
            },
            "completeReference": {
                "path": str(complete_path),
                "sha256": sha256_file(complete_path),
            },
        },
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
        "runtimeRaster": {
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
            "capturedCoordinateOrCoefficientTableLoaded": False,
        },
        "candidateConfiguration": HIGHLIGHT_CONFIGURATION,
        "implementation": implementation,
        "comparisons": {
            "glassPrefix": prefix_comparison,
            "completeBackgroundAndHighlight": complete_comparison,
        },
        "gate": {
            "exact": exact,
            "oracleInjectionUsed": False,
            "generatedRuntimeRasterCoefficients": True,
            "productionShaderAuthorized": False,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture", type=Path)
    parser.add_argument(
        "--float-intrinsic-table",
        type=Path,
        required=True,
    )
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    report = run_gate(
        arguments.capture,
        float_intrinsic_table=arguments.float_intrinsic_table,
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
