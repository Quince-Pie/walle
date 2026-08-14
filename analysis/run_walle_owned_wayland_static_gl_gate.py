#!/usr/bin/env python3
"""Run the exact static fixture matrix through a real Wayland EGL surface."""

import argparse
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any

from run_walle_owned_static_gl_gate import (
    FIXTURES,
    PROTECTED_SHADER_SHA256,
    ROOT,
    parse_output,
    sha256_file,
    validate_fixture_manifest,
)


type JsonObject = dict[str, Any]

SCOPE: JsonObject = {
    "independentlyGeneratedCompleteStaticRenderInputs": True,
    "capturedRenderInputs": False,
    "capturedFinalOutputsUsedForComparisonOnly": True,
    "walleOwnedCGlRendererRendered": True,
    "walleOwnedWaylandEglWindowSurfaceRendered": True,
    "productionWalleProcessRendered": False,
    "productionWalleWaylandSurfaceRendered": False,
    "physicalRetinaOutput": False,
    "formalLiquidGlassParity": False,
}


def run_gate(arguments: argparse.Namespace) -> JsonObject:
    fixture_manifest_path = arguments.fixtures / "manifest.json"
    fixture_manifest = validate_fixture_manifest(fixture_manifest_path)
    production_shader = ROOT / "shaders/frag.glsl"
    production_hash = sha256_file(production_shader)
    if production_hash != PROTECTED_SHADER_SHA256:
        raise ValueError("protected production shader changed")

    runs: list[JsonObject] = []
    for fixture in FIXTURES:
        material = "clear" if fixture.startswith("clear-") else "regular"
        command = (
            str(arguments.renderer),
            "--wayland-display",
            arguments.wayland_display,
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
                "fixture": fixture,
                "material": material,
                "exitCode": completed.returncode,
                "elapsedMilliseconds": elapsed_ms,
                "waylandDisplay": parsed.get("waylandDisplay"),
                "renderer": parsed.get("GL_RENDERER"),
                "glVersion": parsed.get("GL_VERSION"),
                "offscreenCheckedBytes": parsed.get("checkedBytes"),
                "offscreenMismatchedBytes": parsed.get(
                    "mismatchedBytes"
                ),
                "offscreenMismatchedPixels": parsed.get(
                    "mismatchedPixels"
                ),
                "offscreenMaximumChannelDelta": parsed.get(
                    "maximumChannelDelta"
                ),
                "offscreenExact": parsed.get("exact") is True,
                "waylandCheckedBytes": parsed.get("waylandCheckedBytes"),
                "waylandMismatchedBytes": parsed.get(
                    "waylandMismatchedBytes"
                ),
                "waylandMismatchedPixels": parsed.get(
                    "waylandMismatchedPixels"
                ),
                "waylandMaximumChannelDelta": parsed.get(
                    "waylandMaximumChannelDelta"
                ),
                "waylandExact": parsed.get("waylandExact") is True,
                "stderr": completed.stderr,
            }
        )

    exact = all(
        run["exitCode"] == 0
        and run["offscreenCheckedBytes"] == 4_194_304
        and run["offscreenMismatchedBytes"] == 0
        and run["offscreenMismatchedPixels"] == 0
        and run["offscreenMaximumChannelDelta"] == 0
        and run["offscreenExact"] is True
        and run["waylandCheckedBytes"] == 4_194_304
        and run["waylandMismatchedBytes"] == 0
        and run["waylandMismatchedPixels"] == 0
        and run["waylandMaximumChannelDelta"] == 0
        and run["waylandExact"] is True
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
            "offscreenCheckedBytes": sum(
                int(run["offscreenCheckedBytes"] or 0) for run in runs
            ),
            "offscreenMismatchedBytes": sum(
                int(run["offscreenMismatchedBytes"] or 0) for run in runs
            ),
            "offscreenMismatchedPixels": sum(
                int(run["offscreenMismatchedPixels"] or 0) for run in runs
            ),
            "waylandCheckedBytes": sum(
                int(run["waylandCheckedBytes"] or 0) for run in runs
            ),
            "waylandMismatchedBytes": sum(
                int(run["waylandMismatchedBytes"] or 0) for run in runs
            ),
            "waylandMismatchedPixels": sum(
                int(run["waylandMismatchedPixels"] or 0) for run in runs
            ),
        },
        "gate": {
            "walleOwnedWaylandStaticGlExact": exact,
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
    parser.add_argument(
        "--wayland-display",
        default=os.environ.get("WAYLAND_DISPLAY", "wayland-1"),
    )
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def main() -> int:
    arguments = parse_arguments()
    report = run_gate(arguments)
    encoded = json.dumps(report, indent=2, sort_keys=True) + "\n"
    print(encoded, end="")
    if arguments.output is not None:
        arguments.output.write_text(encoded, encoding="utf-8")
    return 0 if report["gate"]["walleOwnedWaylandStaticGlExact"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
