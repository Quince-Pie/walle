#!/usr/bin/env python3
"""Bit-gate the recovered Apple Liquid Glass pass on desktop GLSL."""

import argparse
import hashlib
import json
import platform
import resource
import time
from pathlib import Path
from typing import Any

import moderngl
import numpy as np
from numpy.typing import NDArray

from apple_glass_reference_renderer import (
    CAPTURE_HEIGHT,
    CAPTURE_WIDTH,
    AppleGlassReferenceRenderer,
    bgra_raw,
    compare_images,
)


type JsonObject = dict[str, Any]
type HalfTrace = NDArray[np.uint16]

ACTIVE_START = 112
ACTIVE_SIZE = 800


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def load_half_trace(path: Path) -> HalfTrace:
    values = np.fromfile(path, dtype="<u2")
    expected = CAPTURE_WIDTH * CAPTURE_HEIGHT * 4
    if values.size != expected:
        raise ValueError(
            f"{path} has {values.size} half values; expected {expected}"
        )
    return values.reshape(CAPTURE_HEIGHT, CAPTURE_WIDTH, 4)


def unpack_stage_trace(
    path: Path,
    *,
    upper_pair: bool,
) -> HalfTrace:
    values = np.fromfile(path, dtype="<u4")
    expected = CAPTURE_WIDTH * CAPTURE_HEIGHT * 4
    if values.size != expected:
        raise ValueError(
            f"{path} has {values.size} uint values; expected {expected}"
        )
    packed = values.reshape(CAPTURE_HEIGHT, CAPTURE_WIDTH, 4)
    offset = 2 if upper_pair else 0
    first = packed[..., offset]
    second = packed[..., offset + 1]
    return np.stack(
        (
            (first & np.uint32(0xFFFF)).astype(np.uint16),
            (first >> np.uint32(16)).astype(np.uint16),
            (second & np.uint32(0xFFFF)).astype(np.uint16),
            (second >> np.uint32(16)).astype(np.uint16),
        ),
        axis=-1,
    )


def compare_half_trace(
    reference: HalfTrace,
    candidate: HalfTrace,
) -> JsonObject:
    active = np.s_[
        ACTIVE_START : ACTIVE_START + ACTIVE_SIZE,
        ACTIVE_START : ACTIVE_START + ACTIVE_SIZE,
        :,
    ]
    delta = (
        candidate[active].astype(np.int32)
        - reference[active].astype(np.int32)
    )
    changed = delta != 0
    return {
        "exact": not bool(np.any(changed)),
        "observedHalfValues": int(delta.size),
        "mismatchedHalfValues": int(np.count_nonzero(changed)),
        "mismatchedPixels": int(
            np.count_nonzero(np.any(changed, axis=2))
        ),
        "maximumEncodingDistance": int(
            np.abs(delta).max(initial=0)
        ),
        "mismatchedChannels": [
            int(np.count_nonzero(changed[..., channel]))
            for channel in range(4)
        ],
    }


def stage_references(capture: Path) -> dict[int, HalfTrace]:
    stage_a = capture / (
        "carenderer-live-tree-glass-color-stages-a-"
        "numeric-trace-rgba32ui.raw"
    )
    stage_b = capture / (
        "carenderer-live-tree-glass-color-stages-b-"
        "numeric-trace-rgba32ui.raw"
    )
    return {
        10: unpack_stage_trace(stage_a, upper_pair=False),
        11: unpack_stage_trace(stage_a, upper_pair=True),
        12: unpack_stage_trace(stage_b, upper_pair=False),
        13: unpack_stage_trace(stage_b, upper_pair=True),
        9: load_half_trace(
            capture
            / "carenderer-live-tree-glass-final-color-"
            "numeric-trace-rgba16f.raw"
        ),
        14: load_half_trace(
            capture
            / "carenderer-live-tree-glass-bleed-"
            "numeric-trace-rgba16f.raw"
        ),
    }


def configure_recovered_material(
    renderer: AppleGlassReferenceRenderer,
) -> None:
    renderer.program["UseAppleRefractionTrace"].value = 0
    renderer.program["UseAppleSdfTrace"].value = 0
    renderer.program["UseAppleIntrinsicTable"].value = 1
    renderer.program["InnerSamplerCoordinateModel"].value = 3
    renderer.program["OuterSamplerCoordinateModel"].value = 1
    renderer.program["EdgeSamplerCoordinateModel"].value = 1
    renderer.program["ShadowSamplerCoordinateModel"].value = 2
    renderer.program["RefractionMixModel"].value = 0


def analytic_coordinate_mode(
    capture: Path,
    source_slope_bits: int | None,
) -> int:
    modes_by_slope = {
        0x3A92_4924: 1,
        0x3A2A_AAAB: 2,
    }
    if source_slope_bits in modes_by_slope:
        return modes_by_slope[source_slope_bits]

    name = capture.name.lower()
    if "clear" in name:
        return 1
    if "regular" in name:
        return 2
    raise ValueError(
        "analytic coordinate geometry is not identifiable from the capture "
        "name or source slope"
    )


def coordinate_stage_gates(
    renderer: AppleGlassReferenceRenderer,
    references: dict[int, HalfTrace],
) -> list[JsonObject]:
    return [
        {
            "name": name,
            **compare_half_trace(
                references[trace],
                renderer.render_numeric_trace(trace),
            ),
        }
        for trace, name in (
            (10, "source"),
            (11, "face"),
            (14, "edge-bleed-face"),
            (12, "pre-holding"),
            (13, "post-holding"),
            (9, "final-pre-blend"),
        )
    ]


def run_gate(
    capture: Path,
    intrinsic_table: Path,
    interpolant_axis_table: Path | None = None,
    interpolant_coefficient_table: Path | None = None,
    source_slope_bits: int | None = None,
    interpolant_correction_surface: Path | None = None,
    source_low_bits: int | None = None,
) -> JsonObject:
    started = time.perf_counter()
    analytic_mode = analytic_coordinate_mode(capture, source_slope_bits)
    shader_path = Path("analysis/apple_glass_reference.frag.glsl")
    references = stage_references(capture)
    image_reference = bgra_raw(
        capture
        / "carenderer-live-tree-glass-prefix-reference-bgra8.raw",
        width=CAPTURE_WIDTH,
        height=CAPTURE_HEIGHT,
    )
    stages: list[JsonObject] = []
    with AppleGlassReferenceRenderer(
        capture,
        intrinsic_table=intrinsic_table,
        load_interpolant_axis_trace=False,
    ) as renderer:
        configure_recovered_material(renderer)
        renderer.program["UseAppleInterpolantTrace"].value = 1
        renderer.program["UseAppleRefractionTrace"].value = 1
        renderer.program["UseAppleSdfTrace"].value = 1
        for trace, name in (
            (10, "source"),
            (11, "face"),
            (14, "edge-bleed-face"),
            (12, "pre-holding"),
            (13, "post-holding"),
            (9, "final-pre-blend"),
        ):
            stages.append({
                "name": name,
                **compare_half_trace(
                    references[trace],
                    renderer.render_numeric_trace(trace),
                ),
            })

        renderer.program["UseAppleRefractionTrace"].value = 0
        renderer.program["UseAppleSdfTrace"].value = 0
        recovered_image = compare_images(
            image_reference,
            renderer.render(),
        ).as_json()
        implementation = renderer.implementation

    with AppleGlassReferenceRenderer(
        capture,
        intrinsic_table=intrinsic_table,
        interpolant_axis_table=interpolant_axis_table,
        load_interpolant_trace=False,
    ) as renderer:
        configure_recovered_material(renderer)
        renderer.program["CoordinateMode"].value = 4
        axis_trace_stages = coordinate_stage_gates(
            renderer,
            references,
        )
        axis_trace_image = compare_images(
            image_reference,
            renderer.render(),
        ).as_json()

        renderer.program["CoordinateMode"].value = analytic_mode
        analytic_coordinate_diagnostic = compare_images(
            image_reference,
            renderer.render(),
        ).as_json()

    coefficient_gate: JsonObject | None = None
    if interpolant_coefficient_table is not None:
        if source_slope_bits is None:
            raise ValueError(
                "source slope bits are required with a coefficient table"
            )
        with AppleGlassReferenceRenderer(
            capture,
            intrinsic_table=intrinsic_table,
            interpolant_coefficient_table=(
                interpolant_coefficient_table
            ),
            interpolant_source_slope_bits=source_slope_bits,
            load_interpolant_trace=False,
            load_interpolant_axis_trace=False,
        ) as renderer:
            configure_recovered_material(renderer)
            renderer.program["CoordinateMode"].value = 5
            coefficient_stages = coordinate_stage_gates(
                renderer,
                references,
            )
            coefficient_image = compare_images(
                image_reference,
                renderer.render(),
            ).as_json()
        coefficient_gate = {
            "coefficientTable": {
                "path": str(interpolant_coefficient_table),
                "sha256": sha256_file(
                    interpolant_coefficient_table
                ),
                "bytes": interpolant_coefficient_table.stat().st_size,
                "sourceSlopeBits": f"0x{source_slope_bits:08x}",
                "loadedWithoutFullOrAxisTraceTexture": True,
            },
            "stageGates": coefficient_stages,
            "imageGate": coefficient_image,
            "fullTraceBytes": (
                CAPTURE_WIDTH * CAPTURE_HEIGHT * 4 * 4
            ),
            "compressionRatio": (
                CAPTURE_WIDTH
                * CAPTURE_HEIGHT
                * 4
                * 4
                / interpolant_coefficient_table.stat().st_size
            ),
            "residentTextureByteReductionPercent": (
                100.0
                * (
                    CAPTURE_WIDTH * CAPTURE_HEIGHT * 4 * 4
                    - interpolant_coefficient_table.stat().st_size
                )
                / (CAPTURE_WIDTH * CAPTURE_HEIGHT * 4 * 4)
            ),
            "exact": (
                all(
                    bool(stage["exact"])
                    for stage in coefficient_stages
                )
                and bool(coefficient_image["exact"])
            ),
        }

    correction_gate: JsonObject | None = None
    if interpolant_correction_surface is not None:
        if source_slope_bits is None or source_low_bits is None:
            raise ValueError(
                "source low and slope bits are required with a "
                "correction surface"
            )
        with AppleGlassReferenceRenderer(
            capture,
            intrinsic_table=intrinsic_table,
            interpolant_correction_surface=(
                interpolant_correction_surface
            ),
            interpolant_source_low_bits=source_low_bits,
            interpolant_source_slope_bits=source_slope_bits,
            load_interpolant_trace=False,
            load_interpolant_axis_trace=False,
        ) as renderer:
            configure_recovered_material(renderer)
            renderer.program["CoordinateMode"].value = 6
            correction_stages = coordinate_stage_gates(
                renderer,
                references,
            )
            correction_image = compare_images(
                image_reference,
                renderer.render(),
            ).as_json()
        correction_gate = {
            "correctionSurface": {
                "path": str(interpolant_correction_surface),
                "sha256": sha256_file(
                    interpolant_correction_surface
                ),
                "bytes": interpolant_correction_surface.stat().st_size,
                "sourceLowBits": f"0x{source_low_bits:08x}",
                "sourceSlopeBits": f"0x{source_slope_bits:08x}",
                "loadedWithoutOtherCoordinateTextures": True,
            },
            "stageGates": correction_stages,
            "imageGate": correction_image,
            "exact": (
                all(
                    bool(stage["exact"])
                    for stage in correction_stages
                )
                and bool(correction_image["exact"])
            ),
        }

    exact = (
        all(bool(stage["exact"]) for stage in stages)
        and bool(recovered_image["exact"])
        and all(bool(stage["exact"]) for stage in axis_trace_stages)
        and bool(axis_trace_image["exact"])
        and (
            coefficient_gate is None
            or bool(coefficient_gate["exact"])
        )
        and (
            correction_gate is None
            or bool(correction_gate["exact"])
        )
    )
    report = {
        "liquidGlassGlslEndToEndGateSchemaVersion": 1,
        "capture": {
            "path": str(capture),
            "runtimeJsonSha256": sha256_file(capture / "runtime.json"),
        },
        "intrinsicTable": {
            "path": str(intrinsic_table),
            "sha256": sha256_file(intrinsic_table),
            "bytes": intrinsic_table.stat().st_size,
        },
        "implementation": {
            **implementation,
            "fragmentShader": str(shader_path),
            "fragmentShaderSha256": sha256_file(shader_path),
            "moderngl": moderngl.__version__,
            "numpy": np.__version__,
            "python": platform.python_version(),
        },
        "stageGates": stages,
        "recoveredMaterialImageGate": recovered_image,
        "separableRasterCoordinateGate": {
            "axisTable": (
                {
                    "path": str(interpolant_axis_table),
                    "sha256": sha256_file(interpolant_axis_table),
                    "loadedWithoutFullTraceTexture": True,
                }
                if interpolant_axis_table is not None
                else {
                    "path": None,
                    "derivedFromFullTraceOnCpu": True,
                    "loadedWithoutFullTraceTexture": True,
                }
            ),
            "stageGates": axis_trace_stages,
            "imageGate": axis_trace_image,
            "fullTraceBytes": (
                CAPTURE_WIDTH * CAPTURE_HEIGHT * 4 * 4
            ),
            "axisTraceBytes": ACTIVE_SIZE * 8 * 4,
            "compressionRatio": (
                CAPTURE_WIDTH
                * CAPTURE_HEIGHT
                * 4
                * 4
                / (ACTIVE_SIZE * 8 * 4)
            ),
            "residentTextureByteReductionPercent": (
                100.0
                * (
                    CAPTURE_WIDTH * CAPTURE_HEIGHT * 4 * 4
                    - ACTIVE_SIZE * 8 * 4
                )
                / (CAPTURE_WIDTH * CAPTURE_HEIGHT * 4 * 4)
            ),
            "exact": (
                all(
                    bool(stage["exact"])
                    for stage in axis_trace_stages
                )
                and bool(axis_trace_image["exact"])
            ),
        },
        "analyticCoordinateDiagnostic": {
            **analytic_coordinate_diagnostic,
            "coordinateMode": analytic_mode,
            "gate": False,
            "reason": (
                "Apple's measured fixed-function raster interpolant is an "
                "input fixture, not material shader arithmetic"
            ),
        },
        "gate": {
            "exact": bool(exact),
            "remainingMeasuredInput": (
                "8 MiB Apple fast-intrinsic table + 832-byte "
                "Apple raster-coefficient table"
                if coefficient_gate is not None
                else (
                    "8 MiB Apple fast-intrinsic table + 25 KiB "
                    "separable Apple raster-coordinate table"
                )
            ),
            "productionShaderAuthorized": False,
        },
        "resourceMeasurements": {
            "analysisSeconds": time.perf_counter() - started,
            "maximumResidentSetKiB":
                resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        },
    }
    if coefficient_gate is not None:
        report["coefficientRasterCoordinateGate"] = coefficient_gate
    if correction_gate is not None:
        report["correctionRasterCoordinateGate"] = correction_gate
    return report


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the recovered desktop GLSL Liquid Glass bit gate."
    )
    parser.add_argument("capture", type=Path)
    parser.add_argument("intrinsic_table", type=Path)
    parser.add_argument("--axis-table", type=Path)
    parser.add_argument("--coefficient-table", type=Path)
    parser.add_argument("--correction-surface", type=Path)
    parser.add_argument(
        "--source-low-bits",
        type=lambda value: int(value, 0),
    )
    parser.add_argument(
        "--source-slope-bits",
        type=lambda value: int(value, 0),
    )
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    report = run_gate(
        arguments.capture,
        arguments.intrinsic_table,
        arguments.axis_table,
        arguments.coefficient_table,
        arguments.source_slope_bits,
        arguments.correction_surface,
        arguments.source_low_bits,
    )
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if arguments.output is None:
        print(rendered, end="")
    else:
        arguments.output.write_text(rendered, encoding="utf-8")
    return 0 if report["gate"]["exact"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
