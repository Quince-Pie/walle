#!/usr/bin/env python3
"""Measure full-surface versus lossless-axis Apple coordinate replay."""

import argparse
import json
import platform
import statistics
import time
from pathlib import Path
from typing import Callable

import moderngl

from apple_glass_reference_renderer import AppleGlassReferenceRenderer


def distribution(values: list[float]) -> dict[str, float]:
    ordered = sorted(values)

    def percentile(fraction: float) -> float:
        index = round(fraction * (len(ordered) - 1))
        return ordered[index]

    return {
        "minimum": ordered[0],
        "median": statistics.median(ordered),
        "mean": statistics.fmean(ordered),
        "maximum": ordered[-1],
        "p05": percentile(0.05),
        "p95": percentile(0.95),
        "standardDeviation": (
            statistics.stdev(ordered) if len(ordered) > 1 else 0.0
        ),
    }


def benchmark_mode(
    capture: Path,
    intrinsic_table: Path,
    axis_table: Path,
    coefficient_table: Path,
    correction_surface: Path,
    *,
    mode: str,
    target: str,
    samples: int,
    frames_per_sample: int,
    warmup_frames: int,
    device_index: int | None,
    source_slope_bits: int,
    source_low_bits: int,
) -> dict[str, object]:
    context_arguments: dict[str, object] = {}
    if device_index is not None:
        context_arguments["device_index"] = device_index
    with AppleGlassReferenceRenderer(
        capture,
        intrinsic_table=intrinsic_table,
        interpolant_axis_table=axis_table if mode == "axis" else None,
        interpolant_coefficient_table=(
            coefficient_table if mode == "coefficient" else None
        ),
        interpolant_source_slope_bits=source_slope_bits,
        interpolant_correction_surface=(
            correction_surface if mode == "correction" else None
        ),
        interpolant_source_low_bits=source_low_bits,
        load_interpolant_trace=mode == "full",
        load_interpolant_axis_trace=mode == "axis",
        context_arguments=context_arguments,
    ) as renderer:
        renderer.program["UseAppleInterpolantTrace"].value = (
            1 if mode == "full" else 0
        )
        renderer.program["CoordinateMode"].value = (
            {
                "full": 0,
                "axis": 4,
                "coefficient": 5,
                "correction": 6,
            }[mode]
        )
        renderer.program["UseAppleRefractionTrace"].value = 0
        renderer.program["UseAppleSdfTrace"].value = 0
        renderer.program["UseAppleIntrinsicTable"].value = 1
        renderer.program["InnerSamplerCoordinateModel"].value = 3
        renderer.program["OuterSamplerCoordinateModel"].value = 1
        renderer.program["EdgeSamplerCoordinateModel"].value = 1
        renderer.program["ShadowSamplerCoordinateModel"].value = 2
        renderer.program["RefractionMixModel"].value = 0
        renderer.prepare_render()
        draw: Callable[[], None] = (
            renderer.draw_main_layer
            if target == "main"
            else renderer.draw_layers
        )
        for _ in range(warmup_frames):
            draw()
        renderer.context.finish()

        gpu_microseconds: list[float] = []
        submission_microseconds: list[float] = []
        for _ in range(samples):
            query = renderer.context.query(time=True)
            started = time.perf_counter_ns()
            with query:
                for _ in range(frames_per_sample):
                    draw()
            renderer.context.finish()
            elapsed = time.perf_counter_ns() - started
            gpu_microseconds.append(
                query.elapsed / frames_per_sample / 1000.0
            )
            submission_microseconds.append(
                elapsed / frames_per_sample / 1000.0
            )
        return {
            "mode": mode,
            "target": target,
            "logicalCoordinateTextureBytes": (
                1024 * 1024 * 4 * 4
                if mode == "full"
                else (
                    800 * 2 * 4 * 4
                    if mode == "axis"
                    else (
                        26 * 2 * 4 * 4
                        if mode == "coefficient"
                        else 800 * 800 * 4
                    )
                )
            ),
            "implementation": renderer.implementation,
            "gpuMicrosecondsPerFrame": distribution(gpu_microseconds),
            "synchronizedCpuMicrosecondsPerFrame": distribution(
                submission_microseconds
            ),
            "gpuMegapixelsPerSecondAt800x800": {
                key: 640000.0 / value
                for key, value in distribution(gpu_microseconds).items()
                if key != "standardDeviation"
            },
        }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("capture", type=Path)
    parser.add_argument("intrinsic_table", type=Path)
    parser.add_argument("axis_table", type=Path)
    parser.add_argument("coefficient_table", type=Path)
    parser.add_argument("correction_surface", type=Path)
    parser.add_argument(
        "--source-slope-bits",
        required=True,
        type=lambda value: int(value, 0),
    )
    parser.add_argument(
        "--source-low-bits",
        required=True,
        type=lambda value: int(value, 0),
    )
    parser.add_argument("--samples", type=int, default=15)
    parser.add_argument("--frames-per-sample", type=int, default=10)
    parser.add_argument("--warmup-frames", type=int, default=20)
    parser.add_argument("--device-index", type=int)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    if arguments.samples < 2:
        parser.error("--samples must be at least two")
    if arguments.frames_per_sample < 1:
        parser.error("--frames-per-sample must be positive")

    results = [
        benchmark_mode(
            arguments.capture,
            arguments.intrinsic_table,
            arguments.axis_table,
            arguments.coefficient_table,
            arguments.correction_surface,
            mode=mode,
            target=target,
            samples=arguments.samples,
            frames_per_sample=arguments.frames_per_sample,
            warmup_frames=arguments.warmup_frames,
            device_index=arguments.device_index,
            source_slope_bits=arguments.source_slope_bits,
            source_low_bits=arguments.source_low_bits,
        )
        for target in ("main", "full")
        for mode in ("full", "axis", "coefficient", "correction")
    ]
    by_target = {
        target: {
            result["mode"]: result
            for result in results
            if result["target"] == target
        }
        for target in ("main", "full")
    }
    comparisons: dict[str, object] = {}
    for target, modes in by_target.items():
        full_median = modes["full"]["gpuMicrosecondsPerFrame"]["median"]
        comparisons[target] = {
            candidate: {
                "fullMedianGpuMicroseconds": full_median,
                "candidateMedianGpuMicroseconds": (
                    modes[candidate]["gpuMicrosecondsPerFrame"]["median"]
                ),
                "latencyChangePercent": (
                    100.0
                    * (
                        modes[candidate][
                            "gpuMicrosecondsPerFrame"
                        ]["median"]
                        - full_median
                    )
                    / full_median
                ),
                "throughputSpeedup": (
                    full_median
                    / modes[candidate][
                        "gpuMicrosecondsPerFrame"
                    ]["median"]
                ),
            }
            for candidate in ("axis", "coefficient", "correction")
        }
    report = {
        "liquidGlassRasterCoordinateBenchmarkSchemaVersion": 1,
        "capture": str(arguments.capture),
        "intrinsicTable": str(arguments.intrinsic_table),
        "axisTable": str(arguments.axis_table),
        "coefficientTable": str(arguments.coefficient_table),
        "correctionSurface": str(arguments.correction_surface),
        "method": {
            "timer": "OpenGL GL_TIME_ELAPSED",
            "samples": arguments.samples,
            "framesPerSample": arguments.frames_per_sample,
            "warmupFrames": arguments.warmup_frames,
            "deviceIndex": arguments.device_index,
        },
        "results": results,
        "comparison": comparisons,
        "python": platform.python_version(),
        "moderngl": moderngl.__version__,
    }
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if arguments.output is None:
        print(rendered, end="")
    else:
        arguments.output.write_text(rendered, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
