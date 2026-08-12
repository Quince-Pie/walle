#!/usr/bin/env python3
"""Preregister exact outputs for unseen seeded Liquid Glass source fields."""

import argparse
import hashlib
import json
import platform
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import numpy as np

from apple_glass_reference_renderer import AppleGlassReferenceRenderer
from liquid_glass_exact_specialization_gate import (
    Fixture,
    default_fixtures,
    fixture_radius,
    sha256_file,
)
from liquid_glass_pack_intrinsic_tables import (
    circle_scale_reciprocal_bits,
)
from liquid_glass_shader_specialization import (
    load_amd_packed_exact_circle_shader,
)


type JsonObject = dict[str, Any]
type PatternName = Literal[
    "prospective-opaque-seeded-v2",
    "prospective-premultiplied-seeded-v2",
]

MASK64 = (1 << 64) - 1
PATTERN_SEEDS: dict[PatternName, int] = {
    "prospective-opaque-seeded-v2": 0x3C6EF372FE94F82B,
    "prospective-premultiplied-seeded-v2": 0xA54FF53A5F1D36F1,
}


def split_mix_64(value: int) -> int:
    value = (value + 0x9E3779B97F4A7C15) & MASK64
    value = ((value ^ (value >> 30)) * 0xBF58476D1CE4E5B9) & MASK64
    value = ((value ^ (value >> 27)) * 0x94D049BB133111EB) & MASK64
    return value ^ (value >> 31)


def seeded_texel(
    pattern: PatternName,
    *,
    x: int,
    y: int,
    level: int,
) -> tuple[int, int, int, int]:
    if min(x, y, level) < 0:
        raise ValueError("texture coordinates and level must be nonnegative")
    coordinate = (level << 56) ^ (y << 28) ^ x
    value = split_mix_64(PATTERN_SEEDS[pattern] ^ coordinate)
    blue = value & 0xFF
    green = (value >> 8) & 0xFF
    red = (value >> 16) & 0xFF
    alpha = (
        (value >> 24) & 0xFF
        if pattern == "prospective-premultiplied-seeded-v2"
        else 255
    )

    def premultiply(channel: int) -> int:
        return (channel * alpha + 127) // 255

    return (
        premultiply(blue),
        premultiply(green),
        premultiply(red),
        alpha,
    )


def generate_level(
    pattern: PatternName,
    *,
    width: int,
    height: int,
    level: int,
) -> bytes:
    if width < 1 or height < 1:
        raise ValueError("texture dimensions must be positive")
    result = bytearray(width * height * 4)
    for y in range(height):
        for x in range(width):
            offset = (y * width + x) * 4
            result[offset : offset + 4] = bytes(
                seeded_texel(pattern, x=x, y=y, level=level)
            )
    return bytes(result)


def fnv1a64(value: bytes) -> str:
    result = 0xCBF29CE484222325
    for byte in value:
        result ^= byte
        result = (result * 0x100000001B3) & MASK64
    return f"{result:016x}"


def source_mip_layout(capture: Path) -> tuple[tuple[int, int, int], ...]:
    runtime = json.loads(
        (capture / "runtime.json").read_text(encoding="utf-8")
    )
    snapshots = runtime["carendererEvidence"]["metalTextureSnapshots"][
        "snapshots"
    ]
    matches = [
        snapshot
        for snapshot in snapshots
        if snapshot.get("index") == 3
        and str(
            snapshot.get("pipeline", {})
            .get("creationDescriptor", {})
            .get("fragmentFunction", "")
        ).startswith("glass_background_sdf")
    ]
    if len(matches) != 1:
        raise ValueError(
            "capture must expose exactly one glass source texture; "
            f"found {len(matches)}"
        )
    return tuple(
        (
            int(level["level"]),
            int(level["width"]),
            int(level["height"]),
        )
        for level in sorted(
            matches[0]["mipSnapshots"],
            key=lambda record: int(record["level"]),
        )
    )


def generate_mips(
    pattern: PatternName,
    layout: tuple[tuple[int, int, int], ...],
) -> dict[int, bytes]:
    return {
        level: generate_level(
            pattern,
            width=width,
            height=height,
            level=level,
        )
        for level, width, height in layout
    }


def mip_report(
    layout: tuple[tuple[int, int, int], ...],
    mips: dict[int, bytes],
) -> list[JsonObject]:
    return [
        {
            "level": level,
            "width": width,
            "height": height,
            "bytes": len(mips[level]),
            "fnv1a64": fnv1a64(mips[level]),
            "sha256": hashlib.sha256(mips[level]).hexdigest(),
        }
        for level, width, height in layout
    ]


def predict_fixture(
    fixture: Fixture,
    *,
    pattern: PatternName,
    mips: dict[int, bytes],
    intrinsic_table: Path,
    sqrt_intrinsic_table: Path,
    rsqrt_intrinsic_table: Path,
    device_index: int | None,
) -> JsonObject:
    radius = fixture_radius(fixture)
    reciprocal_bits = circle_scale_reciprocal_bits(
        radius,
        intrinsic_table,
    )
    context_arguments: dict[str, object] = {}
    if device_index is not None:
        context_arguments["device_index"] = device_index
    with AppleGlassReferenceRenderer(
        fixture.capture,
        fragment_shader_source=load_amd_packed_exact_circle_shader(
            fixture.material
        ),
        sqrt_intrinsic_table=sqrt_intrinsic_table,
        rsqrt_intrinsic_table=rsqrt_intrinsic_table,
        circle_scale_reciprocal_bits=reciprocal_bits,
        interpolant_coefficient_table=fixture.coefficient_table,
        interpolant_source_slope_bits=fixture.source_slope_bits,
        load_interpolant_trace=False,
        load_interpolant_axis_trace=False,
        load_diagnostic_traces=False,
        source_mip_bgra_overrides=mips,
        context_arguments=context_arguments,
    ) as renderer:
        rgba = renderer.render()
        raw_bgra = np.ascontiguousarray(rgba[..., [2, 1, 0, 3]]).tobytes()
        return {
            "pattern": pattern,
            "material": fixture.material,
            "capture": str(fixture.capture),
            "captureRuntimeSha256": sha256_file(
                fixture.capture / "runtime.json"
            ),
            "rasterCoefficientTableSha256": sha256_file(
                fixture.coefficient_table
            ),
            "circleScaleReciprocalBits": f"0x{reciprocal_bits:08x}",
            "output": {
                "format": "BGRA8Unorm",
                "width": 1024,
                "height": 1024,
                "bytes": len(raw_bgra),
                "fnv1a64": fnv1a64(raw_bgra),
                "sha256": hashlib.sha256(raw_bgra).hexdigest(),
            },
            "implementation": renderer.implementation,
        }


def build_preregistration(
    *,
    intrinsic_table: Path,
    sqrt_intrinsic_table: Path,
    rsqrt_intrinsic_table: Path,
    device_index: int | None,
) -> JsonObject:
    fixtures = default_fixtures()
    layouts = {
        fixture.material: source_mip_layout(fixture.capture)
        for fixture in fixtures
    }
    generated: dict[
        tuple[PatternName, str],
        dict[int, bytes],
    ] = {}
    sources: JsonObject = {}
    for pattern in PATTERN_SEEDS:
        sources[pattern] = {}
        for material, layout in layouts.items():
            mips = generate_mips(pattern, layout)
            generated[(pattern, material)] = mips
            sources[pattern][material] = mip_report(layout, mips)

    predictions: JsonObject = {}
    for fixture in fixtures:
        predictions[fixture.name] = {}
        for pattern in PATTERN_SEEDS:
            predictions[fixture.name][pattern] = predict_fixture(
                fixture,
                pattern=pattern,
                mips=generated[(pattern, fixture.material)],
                intrinsic_table=intrinsic_table,
                sqrt_intrinsic_table=sqrt_intrinsic_table,
                rsqrt_intrinsic_table=rsqrt_intrinsic_table,
                device_index=device_index,
            )

    return {
        "liquidGlassUnseenSourceHoldoutSchemaVersion": 1,
        "generatedAt": datetime.now(UTC).isoformat(),
        "status": "preregistered-before-apple-capture",
        "acceptance": (
            "Every source mip must match its preregistered hash, and the "
            "Apple private fragment, independent Metal reconstruction, and "
            "frozen AMD GLSL prediction must have identical BGRA8 bytes."
        ),
        "scope": {
            "geometry": "circle-800-center",
            "materials": ["clear", "regular"],
            "appearances": ["light", "dark"],
            "patterns": list(PATTERN_SEEDS),
            "appleOutputAvailableDuringPrediction": False,
        },
        "generator": {
            "algorithm": (
                "SplitMix64(seed XOR ((level << 56) XOR (y << 28) XOR x)); "
                "BGRA bytes 0..23; optional alpha byte 24..31; RGB uses "
                "integer (channel * alpha + 127) / 255 premultiplication"
            ),
            "seeds": {
                name: f"0x{seed:016x}"
                for name, seed in PATTERN_SEEDS.items()
            },
            "file": "analysis/liquid_glass_unseen_holdout.py",
        },
        "host": {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "deviceIndex": device_index,
        },
        "intrinsicTables": {
            "source": {
                "bytes": intrinsic_table.stat().st_size,
                "sha256": sha256_file(intrinsic_table),
            },
            "sqrt": {
                "bytes": sqrt_intrinsic_table.stat().st_size,
                "sha256": sha256_file(sqrt_intrinsic_table),
            },
            "rsqrt": {
                "bytes": rsqrt_intrinsic_table.stat().st_size,
                "sha256": sha256_file(rsqrt_intrinsic_table),
            },
        },
        "sources": sources,
        "predictions": predictions,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
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
    parser.add_argument("--device-index", type=int)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    report = build_preregistration(
        intrinsic_table=arguments.intrinsic_table,
        sqrt_intrinsic_table=arguments.sqrt_intrinsic_table,
        rsqrt_intrinsic_table=arguments.rsqrt_intrinsic_table,
        device_index=arguments.device_index,
    )
    encoded = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if arguments.output is None:
        print(encoded, end="")
    else:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(encoded, encoding="utf-8")
        print(arguments.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
