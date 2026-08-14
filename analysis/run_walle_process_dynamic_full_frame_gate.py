#!/usr/bin/env python3
"""Run the promoted eight-state dynamic matrix inside release Walle."""

import argparse
import json
import os
import subprocess
import time
from pathlib import Path

from run_walle_owned_static_gl_gate import (
    PROTECTED_SHADER_SHA256,
    ROOT,
    parse_output,
    sha256_file,
)


SAMPLES = (1, 4, 8, 12, 16, 20, 24, 28)
EXPECTED_TIMELINE_SHA256 = (
    "9343c8d2e2edb3748869c35dac1f0e6c381bd58426d86d4d6f84ae4556eaeade"
)
ALLOWED_INPUT_ROLES = {
    "independent-input",
    "measured-apple-hardware-intrinsic-lookup",
}

type JsonObject = dict[str, object]

SCOPE: JsonObject = {
    "prospectiveEightStateDynamicInputs": True,
    "capturedRenderInputs": False,
    "capturedFinalOutputsUsedForComparisonOnly": True,
    "releaseWalleExecutableRendered": True,
    "productionWalleProcessRendered": True,
    "walleLayerShellEglSurfaceRendered": True,
    "exactDynamicDiagnosticMode": True,
    "bothFinalHighlightTopologiesRendered": True,
    "ordinaryWallpaperTransitionModeRendered": False,
    "continuousLiveTransitionStateRendered": False,
    "physicalRetinaOutput": False,
    "formalLiquidGlassParity": False,
}


def validate_fixture(directory: Path, sample: int) -> JsonObject:
    manifest_path = directory / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError(f"fixture {sample} manifest is not an object")
    expected = {
        "schemaVersion": 2,
        "sampleIndex": sample,
        "captureTimelineSha256": EXPECTED_TIMELINE_SHA256,
        "captureAdmission": "prospective-explicit-timeline-sha256",
        "capturedFinalOutputUsedForComparisonOnly": True,
        "capturedRenderInputFields": [],
    }
    for field, value in expected.items():
        if manifest.get(field) != value:
            raise ValueError(f"fixture {sample} {field} differs")
    files = manifest.get("files")
    if not isinstance(files, dict) or not files:
        raise ValueError(f"fixture {sample} file inventory is absent")
    captured_oracles = 0
    for name, untyped_metadata in files.items():
        if not isinstance(name, str) or not isinstance(untyped_metadata, dict):
            raise ValueError(f"fixture {sample} file inventory is malformed")
        metadata = untyped_metadata
        path = directory / name
        if not path.is_file():
            raise ValueError(f"fixture {sample} file is absent: {name}")
        if path.stat().st_size != metadata.get("byteCount"):
            raise ValueError(f"fixture {sample} file size differs: {name}")
        if sha256_file(path) != metadata.get("sha256"):
            raise ValueError(f"fixture {sample} file hash differs: {name}")
        role = metadata.get("role")
        if role == "captured-comparison-oracle-only":
            captured_oracles += 1
            if name != "reference-bottom-left.rgba8":
                raise ValueError(f"fixture {sample} captured oracle role differs")
        elif role not in ALLOWED_INPUT_ROLES:
            raise ValueError(f"fixture {sample} inadmissible input role: {role}")
    if captured_oracles != 1:
        raise ValueError(f"fixture {sample} captured oracle count differs")
    return {
        "sampleIndex": sample,
        "manifestSHA256": sha256_file(manifest_path),
        "remainingFloat32Bits": manifest.get("remainingFloat32Bits"),
        "material": manifest.get("material"),
        "appearance": manifest.get("appearance"),
        "highlightVertexCount": (
            int(files["highlight-vertices.f32"]["byteCount"]) // 32
        ),
        "highlightIndexCount": (
            int(files["highlight-indices.u16"]["byteCount"]) // 2
        ),
    }


def run_gate(arguments: argparse.Namespace) -> JsonObject:
    production_shader = ROOT / "shaders/frag.glsl"
    production_hash = sha256_file(production_shader)
    if production_hash != PROTECTED_SHADER_SHA256:
        raise ValueError("protected production shader changed")
    if not arguments.walle.is_file():
        raise ValueError("release Walle executable is absent")

    fixtures = [
        validate_fixture(arguments.fixtures / f"fixture-{sample:02d}", sample)
        for sample in SAMPLES
    ]
    topology_inventory = {
        (fixture["highlightVertexCount"], fixture["highlightIndexCount"])
        for fixture in fixtures
    }
    if topology_inventory != {(4, 6), (16, 24)}:
        raise ValueError("dynamic fixture topology inventory differs")

    environment = os.environ.copy()
    environment["WAYLAND_DISPLAY"] = arguments.wayland_display
    runs: list[JsonObject] = []
    for fixture in fixtures:
        sample = int(fixture["sampleIndex"])
        material = str(fixture["material"])
        command = (
            str(arguments.walle),
            "--exact-static-fixture",
            str(arguments.fixtures / f"fixture-{sample:02d}"),
            "--exact-static-vertex",
            str(arguments.shaders / "apple_glass_exact.vert.glsl"),
            "--exact-static-fragment",
            str(arguments.shaders / f"apple_glass_exact_{material}.frag.glsl"),
            "--exact-static-intrinsic",
            str(arguments.intrinsic_table),
        )
        started = time.perf_counter()
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            env=environment,
        )
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        parsed = parse_output(completed.stdout)
        runs.append(
            {
                **fixture,
                "exitCode": completed.returncode,
                "elapsedMilliseconds": elapsed_ms,
                "offscreenCheckedBytes": parsed.get("checkedBytes"),
                "offscreenMismatchedBytes": parsed.get("mismatchedBytes"),
                "offscreenMismatchedPixels": parsed.get("mismatchedPixels"),
                "offscreenMaximumChannelDelta": parsed.get("maximumChannelDelta"),
                "offscreenExact": parsed.get("exact") is True,
                "layerShellCheckedBytes": parsed.get("waylandCheckedBytes"),
                "layerShellMismatchedBytes": parsed.get("waylandMismatchedBytes"),
                "layerShellMismatchedPixels": parsed.get("waylandMismatchedPixels"),
                "layerShellMaximumChannelDelta": parsed.get("waylandMaximumChannelDelta"),
                "layerShellExact": parsed.get("waylandExact") is True,
                "walleExecutableProcessRendered": (
                    parsed.get("walleExecutableProcessRendered") is True
                ),
                "walleLayerShellSurfaceRendered": (
                    parsed.get("walleLayerShellSurfaceRendered") is True
                ),
                "walleDynamicDiagnosticExact": (
                    parsed.get("walleStaticDiagnosticExact") is True
                ),
                "stdout": completed.stdout,
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
        and run["layerShellCheckedBytes"] == 4_194_304
        and run["layerShellMismatchedBytes"] == 0
        and run["layerShellMismatchedPixels"] == 0
        and run["layerShellMaximumChannelDelta"] == 0
        and run["layerShellExact"] is True
        and run["walleExecutableProcessRendered"] is True
        and run["walleLayerShellSurfaceRendered"] is True
        and run["walleDynamicDiagnosticExact"] is True
        for run in runs
    )
    return {
        "schemaVersion": 1,
        "scope": SCOPE,
        "implementation": {
            "walleBinary": str(arguments.walle),
            "walleBinarySHA256": sha256_file(arguments.walle),
            "walleSourceSHA256": sha256_file(ROOT / "walle.c"),
            "exactRendererSourceSHA256": sha256_file(
                ROOT / "parity/render_walle_exact_static_gl.c"
            ),
            "shaderManifestSHA256": sha256_file(arguments.shaders / "manifest.json"),
            "intrinsicTableSHA256": sha256_file(arguments.intrinsic_table),
            "captureTimelineSHA256": EXPECTED_TIMELINE_SHA256,
            "waylandDisplay": arguments.wayland_display,
        },
        "productionShader": {
            "path": "shaders/frag.glsl",
            "sha256": production_hash,
            "renderedByThisGate": False,
        },
        "runs": runs,
        "totals": {
            "offscreenCheckedBytes": sum(int(run["offscreenCheckedBytes"]) for run in runs),
            "offscreenMismatchedBytes": sum(
                int(run["offscreenMismatchedBytes"]) for run in runs
            ),
            "offscreenMismatchedPixels": sum(
                int(run["offscreenMismatchedPixels"]) for run in runs
            ),
            "layerShellCheckedBytes": sum(
                int(run["layerShellCheckedBytes"]) for run in runs
            ),
            "layerShellMismatchedBytes": sum(
                int(run["layerShellMismatchedBytes"]) for run in runs
            ),
            "layerShellMismatchedPixels": sum(
                int(run["layerShellMismatchedPixels"]) for run in runs
            ),
        },
        "gate": {
            "walleReleaseProcessEightStateDynamicLayerShellExact": exact,
            "remainingAppleAlgorithmUnknowns": 0,
            "ordinaryWallpaperTransitionParityEstablished": False,
            "formalLiquidGlassParityEstablished": False,
        },
    }


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--walle", type=Path, default=ROOT / "build/bin/release/walle")
    parser.add_argument("--shaders", required=True, type=Path)
    parser.add_argument("--fixtures", required=True, type=Path)
    parser.add_argument("--intrinsic-table", required=True, type=Path)
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
    return (
        0
        if report["gate"]["walleReleaseProcessEightStateDynamicLayerShellExact"]
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
