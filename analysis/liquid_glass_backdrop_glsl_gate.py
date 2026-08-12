#!/usr/bin/env python3
"""Bit-gate Apple's recovered backdrop mip arithmetic on desktop GLSL."""

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

from liquid_glass_backdrop_pyramid import (
    replay_copy_base_mip_software,
    unorm8,
)


type JsonObject = dict[str, Any]
type CodeImage = NDArray[np.uint8]

GL_RGBA8UI = 0x8D7C
SOURCE_SIDE = 448
DESTINATION_SIDE = 224
CHANNELS = 4


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def read_codes(
    path: Path,
    *,
    width: int,
    height: int,
) -> CodeImage:
    values = np.fromfile(path, dtype=np.uint8)
    expected = width * height * CHANNELS
    if values.size != expected:
        raise ValueError(
            f"{path} has {values.size} bytes; expected {expected}"
        )
    return values.reshape(height, width, CHANNELS)


def compare_codes(
    reference: CodeImage,
    candidate: CodeImage,
) -> JsonObject:
    delta = (
        candidate.astype(np.int16)
        - reference.astype(np.int16)
    )
    changed = delta != 0
    return {
        "exact": not bool(np.any(changed)),
        "observedBytes": int(delta.size),
        "mismatchedBytes": int(np.count_nonzero(changed)),
        "mismatchedPixels": int(
            np.count_nonzero(np.any(changed, axis=2))
        ),
        "maximumCodeDelta": int(
            np.abs(delta).max(initial=0)
        ),
        "meanAbsoluteCodeDelta": float(
            np.mean(np.abs(delta))
        ),
        "mismatchedBytesByChannel": [
            int(np.count_nonzero(changed[..., channel]))
            for channel in range(CHANNELS)
        ],
    }


def holdout_sources() -> list[tuple[str, CodeImage]]:
    coordinate_y, coordinate_x = np.mgrid[
        :SOURCE_SIDE,
        :SOURCE_SIDE,
    ]
    coordinate_hash = (
        coordinate_x.astype(np.uint32) * np.uint32(0x45D9F3B)
        ^ coordinate_y.astype(np.uint32) * np.uint32(0x119DE1F3)
    )
    hashed = np.empty(
        (SOURCE_SIDE, SOURCE_SIDE, CHANNELS),
        dtype=np.uint8,
    )
    hashed[..., 0] = coordinate_hash & np.uint32(255)
    hashed[..., 1] = (
        coordinate_hash >> np.uint32(8)
    ) & np.uint32(255)
    hashed[..., 2] = (
        coordinate_hash >> np.uint32(16)
    ) & np.uint32(255)
    hashed[..., 3] = 255

    ramp = np.empty_like(hashed)
    ramp[..., 0] = (
        17 * coordinate_x + 29 * coordinate_y
    ) & 255
    ramp[..., 1] = (
        71 * coordinate_x + 11 * coordinate_y + 113
    ) & 255
    ramp[..., 2] = (
        7 * coordinate_x + 97 * coordinate_y + 53
    ) & 255
    ramp[..., 3] = (
        coordinate_x + 3 * coordinate_y
    ) & 255

    extremes = np.empty_like(hashed)
    parity = (
        coordinate_x
        ^ coordinate_y
        ^ (coordinate_x >> 2)
        ^ (coordinate_y >> 3)
    ) & 1
    extremes[..., 0] = np.where(parity == 0, 0, 255)
    extremes[..., 1] = np.where(parity == 0, 255, 0)
    extremes[..., 2] = np.where(
        ((coordinate_x + coordinate_y) & 3) == 0,
        1,
        254,
    )
    extremes[..., 3] = 255

    generator = np.random.default_rng(0x4C4951554944)
    random_opaque = generator.integers(
        0,
        256,
        size=hashed.shape,
        dtype=np.uint8,
    )
    random_opaque[..., 3] = 255
    random_alpha = generator.integers(
        0,
        256,
        size=hashed.shape,
        dtype=np.uint8,
    )
    return [
        ("coordinate-hash", hashed),
        ("all-code-ramp", ramp),
        ("binary-extremes", extremes),
        ("random-opaque", random_opaque),
        ("random-alpha", random_alpha),
    ]


def run_gate(
    capture: Path,
    shader_path: Path,
    *,
    output_tile_size: int,
    local_size: int,
) -> JsonObject:
    started = time.perf_counter()
    runtime_path = capture / "runtime.json"
    runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
    downsample = runtime["variableBlurDownsampleEvidence"]
    source_path = capture / downsample["sourceFile"]
    reference_path = capture / downsample["referenceFile"]
    source = read_codes(
        source_path,
        width=SOURCE_SIDE,
        height=SOURCE_SIDE,
    )
    reference = read_codes(
        reference_path,
        width=DESTINATION_SIDE,
        height=DESTINATION_SIDE,
    )

    context = moderngl.create_standalone_context(
        require=450,
        backend="egl",
    )
    shader = context.compute_shader(
        shader_path.read_text(encoding="utf-8")
    )
    source_texture = context.texture(
        (SOURCE_SIDE, SOURCE_SIDE),
        CHANNELS,
        source.tobytes(),
        alignment=1,
        dtype="u1",
        internal_format=GL_RGBA8UI,
    )
    destination_texture = context.texture(
        (DESTINATION_SIDE, DESTINATION_SIDE),
        CHANNELS,
        alignment=1,
        dtype="u1",
        internal_format=GL_RGBA8UI,
    )
    source_texture.filter = (
        moderngl.NEAREST,
        moderngl.NEAREST,
    )
    source_texture.use(location=0)
    destination_texture.bind_to_image(
        1,
        read=False,
        write=True,
    )
    shader["SourceCodes"].value = 0

    group_x = (
        DESTINATION_SIDE + output_tile_size - 1
    ) // output_tile_size
    group_y = (
        DESTINATION_SIDE + output_tile_size - 1
    ) // output_tile_size
    for _ in range(5):
        shader.run(group_x=group_x, group_y=group_y)
    context.finish()

    latency_samples = np.empty(50, dtype=np.float64)
    for index in range(latency_samples.size):
        sample_start = time.perf_counter_ns()
        shader.run(group_x=group_x, group_y=group_y)
        context.finish()
        latency_samples[index] = (
            time.perf_counter_ns() - sample_start
        ) / 1_000_000

    context.memory_barrier()
    output = np.frombuffer(
        destination_texture.read(alignment=1),
        dtype=np.uint8,
    ).reshape(
        DESTINATION_SIDE,
        DESTINATION_SIDE,
        CHANNELS,
    )
    comparison = compare_codes(reference, output)
    cases: list[JsonObject] = [
        {
            "name": "apple-live-full-rank",
            **comparison,
        }
    ]
    for name, holdout_source in holdout_sources():
        source_texture.write(
            holdout_source.tobytes(),
            alignment=1,
        )
        shader.run(group_x=group_x, group_y=group_y)
        context.finish()
        context.memory_barrier()
        holdout_output = np.frombuffer(
            destination_texture.read(alignment=1),
            dtype=np.uint8,
        ).reshape(
            DESTINATION_SIDE,
            DESTINATION_SIDE,
            CHANNELS,
        )
        holdout_reference = unorm8(
            replay_copy_base_mip_software(holdout_source)
        )
        cases.append({
            "name": name,
            **compare_codes(
                holdout_reference,
                holdout_output,
            ),
        })
    implementation = {
        "glVersion": context.info["GL_VERSION"],
        "glVendor": context.info["GL_VENDOR"],
        "glRenderer": context.info["GL_RENDERER"],
        "moderngl": moderngl.__version__,
        "numpy": np.__version__,
        "python": platform.python_version(),
    }
    destination_texture.release()
    source_texture.release()
    shader.release()
    context.release()

    exact = all(bool(case["exact"]) for case in cases)
    observed_bytes = sum(
        int(case["observedBytes"]) for case in cases
    )
    mismatched_bytes = sum(
        int(case["mismatchedBytes"]) for case in cases
    )
    return {
        "liquidGlassBackdropGlslGateSchemaVersion": 1,
        "capture": {
            "path": str(capture),
            "runtimeJsonSha256": sha256_file(runtime_path),
            "source": str(source_path),
            "sourceSha256": sha256_file(source_path),
            "reference": str(reference_path),
            "referenceSha256": sha256_file(reference_path),
        },
        "implementation": {
            **implementation,
            "computeShader": str(shader_path),
            "computeShaderSha256": sha256_file(shader_path),
            "workgroupSize": [local_size, local_size, 1],
            "outputTileSize": [
                output_tile_size,
                output_tile_size,
            ],
            "dispatchThreadgroups": [group_x, group_y, 1],
        },
        "model": {
            "prefilter":
                "ordered binary16 2x2 add/add/add/multiply-one-quarter",
            "kernel":
                "13-tap AGX2 ordered binary16 sums and fused FMAs",
            "output":
                "explicit round-to-nearest-even BGRA8 code",
        },
        "comparison": comparison,
        "cases": cases,
        "latencyMilliseconds": {
            "samples": int(latency_samples.size),
            "minimum": float(np.min(latency_samples)),
            "median": float(np.median(latency_samples)),
            "p95": float(
                np.percentile(latency_samples, 95)
            ),
            "maximum": float(np.max(latency_samples)),
        },
        "gate": {
            "exact": exact,
            "observedBytes": observed_bytes,
            "mismatchedBytes": mismatched_bytes,
            "productionBackdropAuthorized": exact,
        },
        "resourceMeasurements": {
            "analysisSeconds": time.perf_counter() - started,
            "maximumResidentSetKiB":
                resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the Apple backdrop arithmetic AMD GLSL bit gate."
    )
    parser.add_argument("capture", type=Path)
    parser.add_argument(
        "--shader",
        type=Path,
        default=Path(
            "analysis/apple_glass_backdrop_copy.comp.glsl"
        ),
    )
    parser.add_argument(
        "--output-tile-size",
        type=int,
        default=8,
    )
    parser.add_argument(
        "--local-size",
        type=int,
        default=8,
    )
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    report = run_gate(
        arguments.capture,
        arguments.shader,
        output_tile_size=arguments.output_tile_size,
        local_size=arguments.local_size,
    )
    encoded = json.dumps(
        report,
        indent=2,
        sort_keys=True,
        allow_nan=False,
    ) + "\n"
    if arguments.output is None:
        print(encoded, end="")
    else:
        arguments.output.write_text(
            encoded,
            encoding="utf-8",
        )
        print(arguments.output)
    return 0 if report["gate"]["exact"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
