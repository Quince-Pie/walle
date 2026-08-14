#!/usr/bin/env python3
"""Measure Walle's background-only draw against Apple's exact pass prefix."""

import argparse
import hashlib
import json
import subprocess
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import numpy as np


type JsonObject = dict[str, Any]

ROOT = Path(__file__).resolve().parent.parent
WIDTH = 1024
HEIGHT = 1024
PIXEL_BYTES = WIDTH * HEIGHT * 4
SAMPLES = (1, 4, 8, 12, 16, 20, 24, 28)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1 << 20):
            digest.update(block)
    return digest.hexdigest()


def object_value(value: object, *, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} is not an object")
    return value


def records_by_sample(timeline: Mapping[str, Any]) -> dict[int, Mapping[str, Any]]:
    dynamic = object_value(
        timeline.get("dynamicBackgroundUniforms"),
        name="dynamic background uniforms",
    )
    values = dynamic.get("records")
    if not isinstance(values, list):
        raise ValueError("dynamic background records are absent")
    records: dict[int, Mapping[str, Any]] = {}
    for value in values:
        record = object_value(value, name="dynamic background record")
        sample = record.get("sampleIndex")
        if isinstance(sample, int):
            records[sample] = record
    if not set(SAMPLES) <= records.keys():
        raise ValueError("dynamic background samples are incomplete")
    return records


def apple_background_path(
    capture: Path,
    record: Mapping[str, Any],
) -> Path:
    render = object_value(record.get("render"), name="dynamic render")
    replay = object_value(render.get("exactPassReplay"), name="exact replay")
    reference = object_value(
        replay.get("finalHighlightInputReference"),
        name="final-highlight input reference",
    )
    output = object_value(reference.get("output"), name="background output")
    raw_name = output.get("rawFile")
    if (
        reference.get("executed") is not True
        or not isinstance(raw_name, str)
        or Path(raw_name).name != raw_name
        or output.get("width") != WIDTH
        or output.get("height") != HEIGHT
        or output.get("rawBytes") != PIXEL_BYTES
    ):
        raise ValueError("Apple background reference metadata differs")
    path = capture / raw_name
    if not path.is_file() or path.stat().st_size != PIXEL_BYTES:
        raise ValueError("Apple background reference is incomplete")
    return path


def apple_bottom_left_rgba(path: Path) -> np.ndarray:
    bgra = np.fromfile(path, dtype=np.uint8).reshape(HEIGHT, WIDTH, 4)
    return np.ascontiguousarray(np.flipud(bgra[..., [2, 1, 0, 3]]))


def candidate_bottom_left_rgba(path: Path) -> np.ndarray:
    values = np.fromfile(path, dtype=np.uint8)
    if values.size != PIXEL_BYTES:
        raise ValueError("Walle background candidate is incomplete")
    return values.reshape(HEIGHT, WIDTH, 4)


def compare(reference: np.ndarray, candidate: np.ndarray) -> JsonObject:
    delta = candidate.astype(np.int16) - reference.astype(np.int16)
    changed = delta != 0
    changed_pixels = np.any(changed, axis=2)
    coordinates = np.argwhere(changed_pixels)
    return {
        "checkedBytes": PIXEL_BYTES,
        "mismatchedBytes": int(np.count_nonzero(changed)),
        "mismatchedPixels": int(np.count_nonzero(changed_pixels)),
        "maximumChannelDelta": int(np.abs(delta).max(initial=0)),
        "mismatchedBytesByChannel": [
            int(value) for value in np.count_nonzero(changed, axis=(0, 1))
        ],
        "firstMismatches": [
            {
                "x": int(x),
                "yBottomLeft": int(y),
                "apple": [int(value) for value in reference[y, x]],
                "walle": [int(value) for value in candidate[y, x]],
            }
            for y, x in coordinates[:32]
        ],
    }


def run(arguments: argparse.Namespace) -> JsonObject:
    timeline_path = arguments.capture / "transition-timeline.json"
    timeline = json.loads(timeline_path.read_text(encoding="utf-8"))
    records = records_by_sample(timeline)
    cases: list[JsonObject] = []
    implementation: JsonObject | None = None
    with tempfile.TemporaryDirectory(prefix="walle-background-") as temporary:
        temporary_root = Path(temporary)
        for sample in SAMPLES:
            fixture = arguments.fixtures / f"regular-dark-dematerialize-{sample:02d}"
            candidate_path = temporary_root / f"candidate-{sample:02d}.rgba8"
            command = (
                str(arguments.renderer),
                "--device-index",
                str(arguments.device_index),
                str(fixture),
                str(arguments.vertex_shader),
                str(arguments.fragment_shader),
                str(arguments.intrinsic_table),
                str(candidate_path),
                "2",
            )
            completed = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
            )
            if completed.returncode not in {0, 1} or not candidate_path.is_file():
                raise RuntimeError(
                    "Walle background renderer failed:\n"
                    f"stdout:\n{completed.stdout}\n"
                    f"stderr:\n{completed.stderr}"
                )
            if implementation is None:
                lines = {
                    key: value
                    for line in completed.stdout.splitlines()
                    if "=" in line
                    for key, value in [line.split("=", 1)]
                    if key in {"device", "GL_VENDOR", "GL_RENDERER", "GL_VERSION"}
                }
                implementation = {
                    "deviceIndex": arguments.device_index,
                    **lines,
                }
            manifest = json.loads(
                (fixture / "manifest.json").read_text(encoding="utf-8")
            )
            construction = object_value(
                manifest.get("construction"),
                name="fixture construction",
            )
            scissor = object_value(
                construction.get("backgroundScissor"),
                name="background scissor",
            )
            reference_path = apple_background_path(
                arguments.capture,
                records[sample],
            )
            cases.append(
                {
                    "sampleIndex": sample,
                    "fixture": str(fixture),
                    "appleReference": {
                        "path": str(reference_path),
                        "sha256": sha256_file(reference_path),
                    },
                    "backgroundScissor": scissor,
                    "comparison": compare(
                        apple_bottom_left_rgba(reference_path),
                        candidate_bottom_left_rgba(candidate_path),
                    ),
                }
            )
    return {
        "schemaVersion": 1,
        "classification": "captured-scissor background-only AMD pixel comparison",
        "capture": str(arguments.capture),
        "captureTimelineSha256": sha256_file(timeline_path),
        "fixtures": str(arguments.fixtures),
        "implementation": implementation,
        "cases": cases,
        "totals": {
            "checkedBytes": sum(case["comparison"]["checkedBytes"] for case in cases),
            "mismatchedBytes": sum(
                case["comparison"]["mismatchedBytes"] for case in cases
            ),
            "mismatchedPixels": sum(
                case["comparison"]["mismatchedPixels"] for case in cases
            ),
            "maximumChannelDelta": max(
                case["comparison"]["maximumChannelDelta"] for case in cases
            ),
            "mismatchedBytesByChannel": [
                sum(
                    case["comparison"]["mismatchedBytesByChannel"][channel]
                    for case in cases
                )
                for channel in range(4)
            ],
        },
    }


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--capture",
        type=Path,
        default=ROOT / "artifacts/local-natural-walle-current-alpha-interpolant-02",
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
        "--renderer",
        type=Path,
        default=ROOT / "build/bin/quality/render_walle_exact_static_gl",
    )
    parser.add_argument(
        "--vertex-shader",
        type=Path,
        default=(
            ROOT
            / "build/generated/liquid-glass/desktop"
            / "apple_glass_exact.vert.glsl"
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
        default=ROOT / "artifacts/apple-float-intrinsics-r8-30556057571.bin",
    )
    parser.add_argument("--device-index", type=int, default=0)
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def main() -> int:
    arguments = parse_arguments()
    report = run(arguments)
    encoded = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if arguments.output is None:
        print(encoded, end="")
    else:
        arguments.output.write_text(encoded, encoding="utf-8")
        print(arguments.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
