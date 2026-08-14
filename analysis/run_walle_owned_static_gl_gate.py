#!/usr/bin/env python3
"""Run the exact static fixture matrix through the Walle-owned C renderer."""

import argparse
import hashlib
import json
import subprocess
import time
from pathlib import Path
from typing import Any


type JsonObject = dict[str, Any]

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ("clear-light", "clear-dark", "regular-light", "regular-dark")
PROTECTED_SHADER_SHA256 = (
    "6489828f12de599da9633d6183266a81b71ed846a1b03c03cb4eb9c23639352d"
)

SCOPE: JsonObject = {
    "independentlyGeneratedCompleteStaticRenderInputs": True,
    "capturedRenderInputs": False,
    "capturedFinalOutputsUsedForComparisonOnly": True,
    "walleOwnedCGlRendererRendered": True,
    "productionWalleProcessRendered": False,
    "productionWalleWaylandSurfaceRendered": False,
    "physicalRetinaOutput": False,
    "formalLiquidGlassParity": False,
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def parse_output(output: str) -> JsonObject:
    fields: JsonObject = {}
    for line in output.splitlines():
        key, separator, value = line.partition("=")
        if not separator:
            continue
        if value in ("true", "false"):
            fields[key] = value == "true"
        elif value.isdecimal():
            fields[key] = int(value)
        else:
            fields[key] = value
    return fields


def validate_fixture_manifest(path: Path) -> JsonObject:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    fixtures = manifest.get("fixtures")
    if not isinstance(fixtures, list) or len(fixtures) != len(FIXTURES):
        raise ValueError("static fixture manifest is incomplete")
    for fixture in fixtures:
        if not isinstance(fixture, dict):
            raise ValueError("static fixture manifest entry is malformed")
        if fixture.get("renderInputsCaptured") is not False:
            raise ValueError("static fixture retained a captured render input")
        if fixture.get("capturedFinalOutputUsedForComparisonOnly") is not True:
            raise ValueError("static fixture comparison authority is ambiguous")
        files = fixture.get("files")
        if not isinstance(files, dict):
            raise ValueError("static fixture file manifest is malformed")
        for name, metadata in files.items():
            if not isinstance(metadata, dict):
                raise ValueError(f"fixture metadata is malformed: {name}")
            expected_role = (
                "captured-comparison-oracle-only"
                if name == "reference-bottom-left.rgba8"
                else "independent-input"
            )
            if metadata.get("role") != expected_role:
                raise ValueError(f"fixture file has an invalid role: {name}")
    return manifest


def run_gate(arguments: argparse.Namespace) -> JsonObject:
    fixture_manifest_path = arguments.fixtures / "manifest.json"
    fixture_manifest = validate_fixture_manifest(fixture_manifest_path)
    production_shader = ROOT / "shaders/frag.glsl"
    production_hash = sha256_file(production_shader)
    if production_hash != PROTECTED_SHADER_SHA256:
        raise ValueError("protected production shader changed")

    runs: list[JsonObject] = []
    for device in arguments.device_index:
        for fixture in FIXTURES:
            material = "clear" if fixture.startswith("clear-") else "regular"
            command = (
                str(arguments.renderer),
                "--device-index",
                str(device),
                str(arguments.fixtures / fixture),
                str(arguments.shaders / "apple_glass_exact.vert.glsl"),
                str(
                    arguments.shaders
                    / f"apple_glass_exact_{material}.frag.glsl"
                ),
                str(arguments.intrinsic_table),
            )
            started = time.perf_counter()
            completed = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
            )
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            parsed = parse_output(completed.stdout)
            runs.append(
                {
                    "deviceIndex": device,
                    "fixture": fixture,
                    "material": material,
                    "exitCode": completed.returncode,
                    "elapsedMilliseconds": elapsed_ms,
                    "renderer": parsed.get("GL_RENDERER"),
                    "glVersion": parsed.get("GL_VERSION"),
                    "checkedBytes": parsed.get("checkedBytes"),
                    "mismatchedBytes": parsed.get("mismatchedBytes"),
                    "mismatchedPixels": parsed.get("mismatchedPixels"),
                    "maximumChannelDelta": parsed.get(
                        "maximumChannelDelta"
                    ),
                    "exact": parsed.get("exact") is True,
                    "stderr": completed.stderr,
                }
            )

    exact = all(
        run["exitCode"] == 0
        and run["checkedBytes"] == 4_194_304
        and run["mismatchedBytes"] == 0
        and run["mismatchedPixels"] == 0
        and run["maximumChannelDelta"] == 0
        and run["exact"] is True
        for run in runs
    )
    return {
        "schemaVersion": 1,
        "scope": SCOPE,
        "implementation": {
            "rendererBinary": str(arguments.renderer),
            "rendererBinarySha256": sha256_file(arguments.renderer),
            "rendererSource": "parity/render_walle_exact_static_gl.c",
            "rendererSourceSha256": sha256_file(
                ROOT / "parity/render_walle_exact_static_gl.c"
            ),
            "shaderManifestSha256": sha256_file(
                arguments.shaders / "manifest.json"
            ),
            "fixtureManifestSha256": sha256_file(fixture_manifest_path),
            "intrinsicTableSha256": sha256_file(arguments.intrinsic_table),
            "fixtureManifest": fixture_manifest,
        },
        "productionShader": {
            "path": "shaders/frag.glsl",
            "sha256": production_hash,
            "renderedByThisGate": False,
        },
        "runs": runs,
        "totals": {
            "checkedBytes": sum(
                int(run["checkedBytes"] or 0) for run in runs
            ),
            "mismatchedBytes": sum(
                int(run["mismatchedBytes"] or 0) for run in runs
            ),
            "mismatchedPixels": sum(
                int(run["mismatchedPixels"] or 0) for run in runs
            ),
        },
        "gate": {
            "walleOwnedStaticGlExact": exact,
            "productionWalleParityEstablished": False,
        },
    }


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--renderer",
        type=Path,
        default=ROOT / "build/bin/quality/render_walle_exact_static_gl",
    )
    parser.add_argument(
        "--shaders",
        type=Path,
        default=ROOT / "build/generated/liquid-glass/desktop",
    )
    parser.add_argument(
        "--fixtures",
        type=Path,
        default=ROOT / "build/generated/liquid-glass/static-fixtures",
    )
    parser.add_argument(
        "--intrinsic-table",
        type=Path,
        default=(
            ROOT / "artifacts/apple-float-intrinsics-r8-30556057571.bin"
        ),
    )
    parser.add_argument("--device-index", type=int, action="append")
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    if arguments.device_index is None:
        arguments.device_index = [0, 1]
    return arguments


def main() -> int:
    arguments = parse_arguments()
    report = run_gate(arguments)
    encoded = json.dumps(report, indent=2, sort_keys=True) + "\n"
    print(encoded, end="")
    if arguments.output is not None:
        arguments.output.write_text(encoded, encoding="utf-8")
    return 0 if report["gate"]["walleOwnedStaticGlExact"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
