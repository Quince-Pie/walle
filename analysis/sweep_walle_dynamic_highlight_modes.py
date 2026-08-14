#!/usr/bin/env python3
"""Rank one-axis highlight arithmetic candidates against an exact Apple frame."""

from __future__ import annotations

import argparse
import json
import os
import re
import struct
import subprocess
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
CONFIG_BYTES = 164
MODE_OFFSET = 100
MODE_NAMES = (
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
    "sdfSquaredUlpBias",
    "sdfDistanceUlpBias",
    "sourceDivision",
    "sourceConstruction",
    "destinationDivision",
)
MODE_VALUES = {
    "derivative": range(5),
    "coordinate": range(2),
    "alphaUlpBias": range(-4, 5),
    "floatDivision": range(6),
    "coverage": range(3),
    "mix": range(5),
    "band": range(3),
    "normalize": range(6),
    "normalizedCoordinate": range(9),
    "sdfArithmetic": range(4),
    "sdfSquaredUlpBias": range(-4, 5),
    "sdfDistanceUlpBias": range(-4, 5),
    "sourceDivision": range(5),
    "sourceConstruction": range(7),
    "destinationDivision": range(7),
}
METRIC_PATTERN = re.compile(
    r"checkedBytes=(?P<checked>\d+)\n"
    r"mismatchedBytes=(?P<bytes>\d+)\n"
    r"mismatchedPixels=(?P<pixels>\d+)\n"
    r"maximumChannelDelta=(?P<delta>\d+)\n"
    r"exact=(?P<exact>true|false)"
)


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--renderer",
        type=Path,
        default=ROOT / "build/bin/quality/render_walle_exact_static_gl",
    )
    parser.add_argument("--fixture", type=Path, action="append", dest="fixtures")
    parser.add_argument(
        "--vertex",
        type=Path,
        default=(
            ROOT
            / "build/generated/liquid-glass/desktop/apple_glass_exact.vert.glsl"
        ),
    )
    parser.add_argument(
        "--fragment",
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
        default=ROOT / "artifacts/apple-float-intrinsics-r8-30556057571.bin",
    )
    parser.add_argument("--device-index", type=int, default=0)
    parser.add_argument("--mode", choices=MODE_NAMES, action="append", dest="modes")
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def decode_modes(config: bytes) -> dict[str, int]:
    if len(config) != CONFIG_BYTES or config[:8] != b"WALLELG3":
        raise ValueError("mode sweep requires a WALLELG3 fixture")
    values = struct.unpack_from("<15i", config, MODE_OFFSET)
    return dict(zip(MODE_NAMES, values, strict=True))


def encode_modes(config: bytes, modes: dict[str, int]) -> bytes:
    result = bytearray(config)
    struct.pack_into(
        "<15i",
        result,
        MODE_OFFSET,
        *(modes[name] for name in MODE_NAMES),
    )
    return bytes(result)


def render(
    *,
    renderer: Path,
    fixture: Path,
    vertex: Path,
    fragment: Path,
    intrinsic_table: Path,
    device_index: int,
) -> dict[str, int | bool]:
    completed = subprocess.run(
        (
            str(renderer),
            "--device-index",
            str(device_index),
            str(fixture),
            str(vertex),
            str(fragment),
            str(intrinsic_table),
        ),
        check=False,
        capture_output=True,
        text=True,
    )
    match = METRIC_PATTERN.search(completed.stdout)
    if match is None:
        raise RuntimeError(
            "renderer did not report a comparison:\n"
            f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
        )
    return {
        "checkedBytes": int(match["checked"]),
        "mismatchedBytes": int(match["bytes"]),
        "mismatchedPixels": int(match["pixels"]),
        "maximumChannelDelta": int(match["delta"]),
        "exact": match["exact"] == "true",
    }


def main() -> int:
    options = arguments()
    fixtures = options.fixtures or [
        ROOT
        / "build/generated/liquid-glass/dynamic-fixtures"
        / "regular-dark-dematerialize-24"
    ]
    selected_modes = options.modes or list(MODE_NAMES)
    base_config = (fixtures[0] / "config.bin").read_bytes()
    base_modes = decode_modes(base_config)
    fixture_configs = [(fixture / "config.bin").read_bytes() for fixture in fixtures]
    if any(decode_modes(config) != base_modes for config in fixture_configs):
        raise ValueError("fixture highlight mode baselines differ")
    results: list[dict[str, object]] = []
    with tempfile.TemporaryDirectory(prefix="walle-highlight-sweep-") as temporary:
        temporary_root = Path(temporary)
        sweep_fixtures: list[Path] = []
        for fixture_index, source_fixture in enumerate(fixtures):
            fixture = temporary_root / str(fixture_index)
            fixture.mkdir()
            sweep_fixtures.append(fixture)
            for source in source_fixture.iterdir():
                if source.name != "config.bin":
                    os.symlink(source.resolve(), fixture / source.name)
        for name in selected_modes:
            for value in MODE_VALUES[name]:
                modes = dict(base_modes)
                modes[name] = value
                metrics: list[dict[str, int | bool]] = []
                for fixture, config in zip(
                    sweep_fixtures, fixture_configs, strict=True
                ):
                    (fixture / "config.bin").write_bytes(
                        encode_modes(config, modes)
                    )
                    metrics.append(
                        render(
                            renderer=options.renderer.resolve(),
                            fixture=fixture,
                            vertex=options.vertex.resolve(),
                            fragment=options.fragment.resolve(),
                            intrinsic_table=options.intrinsic_table.resolve(),
                            device_index=options.device_index,
                        )
                    )
                results.append(
                    {
                        "mode": name,
                        "value": value,
                        "checkedBytes": sum(int(metric["checkedBytes"]) for metric in metrics),
                        "mismatchedBytes": sum(
                            int(metric["mismatchedBytes"]) for metric in metrics
                        ),
                        "mismatchedPixels": sum(
                            int(metric["mismatchedPixels"]) for metric in metrics
                        ),
                        "maximumChannelDelta": max(
                            int(metric["maximumChannelDelta"]) for metric in metrics
                        ),
                        "exact": all(bool(metric["exact"]) for metric in metrics),
                        "perFixture": [
                            {"fixture": str(source), **metric}
                            for source, metric in zip(fixtures, metrics, strict=True)
                        ],
                    }
                )

    ranking = sorted(
        results,
        key=lambda result: (
            int(result["mismatchedBytes"]),
            int(result["mismatchedPixels"]),
            int(result["maximumChannelDelta"]),
            str(result["mode"]),
            int(result["value"]),
        ),
    )
    report = {
        "schemaVersion": 1,
        "classification": "diagnostic one-axis exact-frame arithmetic sweep",
        "deviceIndex": options.device_index,
        "fixtureCount": len(fixtures),
        "fixtures": [str(fixture) for fixture in fixtures],
        "baseModes": base_modes,
        "candidateCount": len(results),
        "topCandidates": ranking[:32],
        "bestByMode": {
            name: min(
                (result for result in results if result["mode"] == name),
                key=lambda result: (
                    int(result["mismatchedBytes"]),
                    int(result["mismatchedPixels"]),
                    int(result["maximumChannelDelta"]),
                ),
            )
            for name in selected_modes
        },
        "results": results,
    }
    options.output.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps({key: report[key] for key in ("candidateCount", "topCandidates", "bestByMode")}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
