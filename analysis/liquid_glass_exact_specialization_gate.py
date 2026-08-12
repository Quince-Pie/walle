#!/usr/bin/env python3
"""Pixel-gate and time the AMD exact-circle Liquid Glass specialization."""

import argparse
import hashlib
import json
import platform
import statistics
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from apple_glass_reference_renderer import (
    CAPTURE_HEIGHT,
    CAPTURE_WIDTH,
    AppleGlassReferenceRenderer,
    bgra_raw,
    compare_images,
)
from liquid_glass_shader_specialization import (
    GlassMaterial,
    load_amd_packed_exact_circle_shader,
    load_specialized_exact_final_shader,
)
from liquid_glass_pack_intrinsic_tables import (
    circle_scale_reciprocal_bits,
)
from liquid_glass_profile_matrix import analyze_artifact


type JsonObject = dict[str, Any]
type ShaderKind = Literal["genericExact", "amdPackedExactCircle"]

ACTIVE_PIXEL_COUNT = 800 * 800
MINIMUM_REGULAR_MEDIAN_REDUCTION_PERCENT = 20.0
MAXIMUM_CLEAR_MEDIAN_REGRESSION_PERCENT = 2.0


@dataclass(frozen=True, slots=True)
class Fixture:
    name: str
    material: GlassMaterial
    capture: Path
    coefficient_table: Path
    source_slope_bits: int


def default_fixtures() -> tuple[Fixture, ...]:
    clear_root = Path("artifacts/liquid-glass-introspection-30575220842")
    regular_root = Path("artifacts/liquid-glass-introspection-30581698599")
    return (
        Fixture(
            name="clear-light",
            material="clear",
            capture=(
                clear_root
                / "liquid-glass-introspection-clear-light-30575220842"
            ),
            coefficient_table=Path(
                "artifacts/apple-raster-coefficients-clear-light-"
                "30575220842.rgba32ui.raw"
            ),
            source_slope_bits=0x3A924924,
        ),
        Fixture(
            name="clear-dark",
            material="clear",
            capture=(
                clear_root
                / "liquid-glass-introspection-clear-dark-30575220842"
            ),
            coefficient_table=Path(
                "artifacts/apple-raster-coefficients-clear-light-"
                "30575220842.rgba32ui.raw"
            ),
            source_slope_bits=0x3A924924,
        ),
        Fixture(
            name="regular-light",
            material="regular",
            capture=(
                regular_root
                / "liquid-glass-introspection-regular-light-30581698599"
            ),
            coefficient_table=Path(
                "artifacts/apple-raster-coefficients-regular-light-"
                "30581698599.rgba32ui.raw"
            ),
            source_slope_bits=0x3A2AAAAB,
        ),
        Fixture(
            name="regular-dark",
            material="regular",
            capture=(
                regular_root
                / "liquid-glass-introspection-regular-dark-30581698599"
            ),
            coefficient_table=Path(
                "artifacts/apple-raster-coefficients-regular-light-"
                "30581698599.rgba32ui.raw"
            ),
            source_slope_bits=0x3A2AAAAB,
        ),
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def distribution(values: list[float]) -> JsonObject:
    if not values:
        raise ValueError("a timing distribution cannot be empty")
    ordered = sorted(values)

    def percentile(fraction: float) -> float:
        return ordered[round(fraction * (len(ordered) - 1))]

    return {
        "sampleCount": len(ordered),
        "minimum": ordered[0],
        "p05": percentile(0.05),
        "median": statistics.median(ordered),
        "mean": statistics.fmean(ordered),
        "p95": percentile(0.95),
        "maximum": ordered[-1],
        "standardDeviation": (
            statistics.stdev(ordered) if len(ordered) > 1 else 0.0
        ),
    }


def shader_source(kind: ShaderKind, material: GlassMaterial) -> str:
    if kind == "genericExact":
        return load_specialized_exact_final_shader()
    return load_amd_packed_exact_circle_shader(material)


def performance_requirement(material: GlassMaterial) -> JsonObject:
    if material == "regular":
        return {
            "metric": "medianGpuTimeReductionPercent",
            "operator": ">=",
            "thresholdPercent": (
                MINIMUM_REGULAR_MEDIAN_REDUCTION_PERCENT
            ),
        }
    return {
        "metric": "medianGpuTimeReductionPercent",
        "operator": ">=",
        "thresholdPercent": -MAXIMUM_CLEAR_MEDIAN_REGRESSION_PERCENT,
    }


def performance_gate_passed(
    material: GlassMaterial,
    reduction_percent: float,
) -> bool:
    threshold = float(
        performance_requirement(material)["thresholdPercent"]
    )
    return reduction_percent >= threshold


def fixture_radius(fixture: Fixture) -> float:
    profile = analyze_artifact(fixture.capture)["profile"]
    return float(profile["fields"]["sdf_arg2"]["values"][2])


def measure_once(
    fixture: Fixture,
    *,
    kind: ShaderKind,
    intrinsic_table: Path,
    sqrt_intrinsic_table: Path,
    rsqrt_intrinsic_table: Path,
    reciprocal_bits: int,
    samples: int,
    frames_per_sample: int,
    warmup_frames: int,
    device_index: int | None,
) -> JsonObject:
    context_arguments: dict[str, object] = {}
    if device_index is not None:
        context_arguments["device_index"] = device_index
    with AppleGlassReferenceRenderer(
        fixture.capture,
        fragment_shader_source=shader_source(kind, fixture.material),
        intrinsic_table=(
            intrinsic_table if kind == "genericExact" else None
        ),
        sqrt_intrinsic_table=(
            sqrt_intrinsic_table
            if kind == "amdPackedExactCircle"
            else None
        ),
        rsqrt_intrinsic_table=(
            rsqrt_intrinsic_table
            if kind == "amdPackedExactCircle"
            else None
        ),
        circle_scale_reciprocal_bits=(
            reciprocal_bits
            if kind == "amdPackedExactCircle"
            else None
        ),
        interpolant_coefficient_table=fixture.coefficient_table,
        interpolant_source_slope_bits=fixture.source_slope_bits,
        load_interpolant_trace=False,
        load_interpolant_axis_trace=False,
        load_diagnostic_traces=False,
        context_arguments=context_arguments,
    ) as renderer:
        reference_path = fixture.capture / (
            "carenderer-live-tree-glass-prefix-reference-bgra8.raw"
        )
        reference = bgra_raw(
            reference_path,
            width=CAPTURE_WIDTH,
            height=CAPTURE_HEIGHT,
        )
        comparison = compare_images(reference, renderer.render()).as_json()

        renderer.prepare_render()
        for _ in range(warmup_frames):
            renderer.draw_layers()
        renderer.context.finish()

        gpu_milliseconds: list[float] = []
        for _ in range(samples):
            query = renderer.context.query(time=True)
            with query:
                for _ in range(frames_per_sample):
                    renderer.draw_layers()
            renderer.context.finish()
            gpu_milliseconds.append(
                query.elapsed / frames_per_sample / 1_000_000.0
            )
        return {
            "comparison": comparison,
            "gpuMillisecondsPerFrame": gpu_milliseconds,
            "implementation": renderer.implementation,
            "reference": {
                "path": str(reference_path),
                "sha256": sha256_file(reference_path),
            },
        }


def run_fixture(
    fixture: Fixture,
    *,
    intrinsic_table: Path,
    sqrt_intrinsic_table: Path,
    rsqrt_intrinsic_table: Path,
    samples: int,
    frames_per_sample: int,
    warmup_frames: int,
    rounds: int,
    device_index: int | None,
) -> JsonObject:
    measurements: dict[ShaderKind, list[JsonObject]] = {
        "genericExact": [],
        "amdPackedExactCircle": [],
    }
    radius = fixture_radius(fixture)
    reciprocal_bits = circle_scale_reciprocal_bits(
        radius,
        intrinsic_table,
    )
    for round_index in range(rounds):
        order: tuple[ShaderKind, ShaderKind] = (
            ("genericExact", "amdPackedExactCircle")
            if round_index % 2 == 0
            else ("amdPackedExactCircle", "genericExact")
        )
        for kind in order:
            measurements[kind].append(
                measure_once(
                    fixture,
                    kind=kind,
                    intrinsic_table=intrinsic_table,
                    sqrt_intrinsic_table=sqrt_intrinsic_table,
                    rsqrt_intrinsic_table=rsqrt_intrinsic_table,
                    reciprocal_bits=reciprocal_bits,
                    samples=samples,
                    frames_per_sample=frames_per_sample,
                    warmup_frames=warmup_frames,
                    device_index=device_index,
                )
            )

    variants: JsonObject = {}
    for kind, runs in measurements.items():
        timings = [
            value
            for run in runs
            for value in run["gpuMillisecondsPerFrame"]
        ]
        timing_distribution = distribution(timings)
        exact_repeatedly = all(
            bool(run["comparison"]["exact"]) for run in runs
        )
        variants[kind] = {
            "comparison": runs[0]["comparison"],
            "exactOnEveryRound": exact_repeatedly,
            "gpuMillisecondsPerFrame": timing_distribution,
            "activeMegapixelsPerSecond": {
                key: ACTIVE_PIXEL_COUNT / float(value) / 1000.0
                for key, value in timing_distribution.items()
                if key
                not in {
                    "sampleCount",
                    "standardDeviation",
                }
            },
            "implementation": runs[0]["implementation"],
            "reference": runs[0]["reference"],
        }

    baseline_median = float(
        variants["genericExact"]["gpuMillisecondsPerFrame"]["median"]
    )
    candidate_median = float(
        variants["amdPackedExactCircle"][
            "gpuMillisecondsPerFrame"
        ]["median"]
    )
    reduction = 100.0 * (baseline_median - candidate_median) / baseline_median
    requirement = performance_requirement(fixture.material)
    return {
        "name": fixture.name,
        "material": fixture.material,
        "capture": {
            "path": str(fixture.capture),
            "runtimeJsonSha256": sha256_file(
                fixture.capture / "runtime.json"
            ),
        },
        "rasterCoefficientTable": {
            "path": str(fixture.coefficient_table),
            "sha256": sha256_file(fixture.coefficient_table),
            "bytes": fixture.coefficient_table.stat().st_size,
            "sourceSlopeBits": f"0x{fixture.source_slope_bits:08x}",
        },
        "circleScaleReciprocal": {
            "radius": radius,
            "bits": f"0x{reciprocal_bits:08x}",
            "computedOncePerDrawOnCpu": True,
        },
        "variants": variants,
        "medianGpuTimeReductionPercent": reduction,
        "performanceRequirement": requirement,
        "localGatePassed": (
            bool(variants["genericExact"]["exactOnEveryRound"])
            and bool(
                variants["amdPackedExactCircle"]["exactOnEveryRound"]
            )
            and performance_gate_passed(fixture.material, reduction)
        ),
    }


def run_gate(
    *,
    intrinsic_table: Path,
    sqrt_intrinsic_table: Path,
    rsqrt_intrinsic_table: Path,
    samples: int,
    frames_per_sample: int,
    warmup_frames: int,
    rounds: int,
    device_index: int | None,
) -> JsonObject:
    fixtures = [
        run_fixture(
            fixture,
            intrinsic_table=intrinsic_table,
            sqrt_intrinsic_table=sqrt_intrinsic_table,
            rsqrt_intrinsic_table=rsqrt_intrinsic_table,
            samples=samples,
            frames_per_sample=frames_per_sample,
            warmup_frames=warmup_frames,
            rounds=rounds,
            device_index=device_index,
        )
        for fixture in default_fixtures()
    ]
    implementation = fixtures[0]["variants"]["amdPackedExactCircle"][
        "implementation"
    ]
    renderer = str(implementation["glRenderer"])
    vendor = str(implementation["glVendor"])
    gl_version = str(implementation["glVersion"])
    amd_mesa = (
        "amd" in f"{vendor} {renderer}".lower()
        and "mesa" in gl_version.lower()
    )
    all_candidate_images_exact = all(
        bool(
            case["variants"]["amdPackedExactCircle"][
                "exactOnEveryRound"
            ]
        )
        for case in fixtures
    )
    all_performance_gates_passed = all(
        bool(case["localGatePassed"])
        for case in fixtures
    )
    return {
        "liquidGlassExactSpecializationGateSchemaVersion": 1,
        "generatedAt": datetime.now(UTC).isoformat(),
        "host": {
            "platform": platform.platform(),
            "python": platform.python_version(),
        },
        "measurementPolicy": {
            "samplesPerRound": samples,
            "framesPerSample": frames_per_sample,
            "warmupFramesPerRound": warmup_frames,
            "rounds": rounds,
            "roundOrder": "AB then BA, alternating for additional rounds",
            "timer": "OpenGL GL_TIME_ELAPSED query around draw layers only",
            "activePixels": ACTIVE_PIXEL_COUNT,
        },
        "intrinsicTable": {
            "path": str(intrinsic_table),
            "sha256": sha256_file(intrinsic_table),
            "bytes": intrinsic_table.stat().st_size,
        },
        "packedIntrinsicTables": {
            "sqrt": {
                "path": str(sqrt_intrinsic_table),
                "sha256": sha256_file(sqrt_intrinsic_table),
                "bytes": sqrt_intrinsic_table.stat().st_size,
            },
            "rsqrt": {
                "path": str(rsqrt_intrinsic_table),
                "sha256": sha256_file(rsqrt_intrinsic_table),
                "bytes": rsqrt_intrinsic_table.stat().st_size,
            },
            "gpuBytes": (
                sqrt_intrinsic_table.stat().st_size
                + rsqrt_intrinsic_table.stat().st_size
            ),
            "gpuByteReductionPercent": (
                100.0
                * (
                    intrinsic_table.stat().st_size
                    - sqrt_intrinsic_table.stat().st_size
                    - rsqrt_intrinsic_table.stat().st_size
                )
                / intrinsic_table.stat().st_size
            ),
            "losslessAcrossAllMantissas": True,
        },
        "fixtures": fixtures,
        "conclusion": {
            "fixtureCount": len(fixtures),
            "allCandidateImagesExact": all_candidate_images_exact,
            "allPerformanceGatesPassed": all_performance_gates_passed,
            "measuredDeviceIsAmdMesa": amd_mesa,
            "localAmdSpecializationAuthorized": (
                all_candidate_images_exact
                and all_performance_gates_passed
                and amd_mesa
            ),
            "productionShaderAuthorized": False,
            "productionBlockers": [
                "preregistered unseen macOS pixel holdout",
                "independent repeat/final macOS gate",
                "integration into Walle's runtime shader path",
                "production-resolution Tracy, GPU-time, and VRAM gates",
            ],
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--intrinsic-table",
        type=Path,
        default=Path("artifacts/apple-float-intrinsics-r8-30556057571.bin"),
    )
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
    parser.add_argument("--samples", type=int, default=12)
    parser.add_argument("--frames-per-sample", type=int, default=2)
    parser.add_argument("--warmup-frames", type=int, default=8)
    parser.add_argument("--rounds", type=int, default=2)
    parser.add_argument("--device-index", type=int)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    if arguments.samples < 2:
        parser.error("--samples must be at least two")
    if arguments.frames_per_sample < 1:
        parser.error("--frames-per-sample must be positive")
    if arguments.warmup_frames < 1:
        parser.error("--warmup-frames must be positive")
    if arguments.rounds < 2:
        parser.error("--rounds must be at least two for AB/BA ordering")

    report = run_gate(
        intrinsic_table=arguments.intrinsic_table,
        sqrt_intrinsic_table=arguments.sqrt_intrinsic_table,
        rsqrt_intrinsic_table=arguments.rsqrt_intrinsic_table,
        samples=arguments.samples,
        frames_per_sample=arguments.frames_per_sample,
        warmup_frames=arguments.warmup_frames,
        rounds=arguments.rounds,
        device_index=arguments.device_index,
    )
    encoded = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if arguments.output is None:
        print(encoded, end="")
    else:
        arguments.output.write_text(encoded, encoding="utf-8")
        print(arguments.output)
    return 0 if report["conclusion"]["localAmdSpecializationAuthorized"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
