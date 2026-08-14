#!/usr/bin/env python3
"""Generate and run the prospective eight-state Walle full-frame gate."""

import argparse
import hashlib
import json
import os
import struct
import subprocess
import sys
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Never

import numpy as np


type JsonObject = dict[str, Any]

ROOT = Path(__file__).resolve().parent.parent
SAMPLE_INDICES = (1, 4, 8, 12, 16, 20, 24, 28)
FRAME_BYTES = 1024 * 1024 * 4
CONFIG_FORMAT = "<8s23I15iI"
PROTECTED_SHADER_SHA256 = (
    "6489828f12de599da9633d6183266a81b71ed846a1b03c03cb4eb9c23639352d"
)
OPENED_TIMELINE_SHA256 = frozenset(
    {
        "71eec0992cecac4755ee706be8160c40daac3a00ccf015517ddac42972945e7c",
        "efaa2e4a2b8e9d1f87d429daef27411e76bf43cea8734f14cfaf65fbc3d1ca76",
        "22f13f4baa7984a5921b3bd989955336889eebebf73631c5fc1fed30db50bdca",
        "76310b754cf1eb1a15881e3d64a1aab75048e0fa57ce378cb891a7ec1efe9107",
        "52e4279fd374efc6a349cb3a5e69fcce0b60e538abc387cd6b75bee3866aa2d3",
        "c028e232c0eb06ade31f826578c7209ea2e19f69b65a65cdc723187bc34adc44",
        "609485e86b185358b0b762bd95143d3a29f3d1049b3a843997f0cf7b05fa9b0a",
        "3593baa93000e7aee8faacc17819ec8eb64e63323cc55bd7666e92f8606b5f8f",
        "e7d9a9310f252d9a304b9574a3422790445783d2876f96e083d89a771b05b638",
    }
)
EXPECTED_CAPTURE_PROFILE: JsonObject = {
    "material": "regular",
    "appearance": "dark",
    "direction": "dematerialize",
    "geometry": "circle-480-center",
    "backingScaleFactor": 2,
}
PREREGISTRATION = (
    ROOT / "lg-test/Analysis/walle_dynamic_full_frame_holdout_preregistration.json"
)
CAPTURE_RUNNER = (
    ROOT / "lg-test/Analysis/run_walle_dynamic_full_frame_holdout_local_macos_26_6_1.sh"
)
CAPTURE_PROBE = ROOT / "lg-test/Sources/GlassIntrospect/main.swift"


def fail(message: str) -> Never:
    raise ValueError(message)


def require(condition: bool, message: str) -> None:
    if not condition:
        fail(message)


def mapping(value: object, label: str) -> Mapping[str, Any]:
    require(isinstance(value, Mapping), f"{label} is not an object")
    return value


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1 << 20):
            digest.update(block)
    return digest.hexdigest()


def load_json(path: Path, label: str) -> JsonObject:
    value = json.loads(path.read_text(encoding="utf-8"))
    require(isinstance(value, dict), f"{label} is not a JSON object")
    return value


def parse_renderer_output(output: str) -> JsonObject:
    fields: JsonObject = {}
    for line in output.splitlines():
        key, separator, value = line.partition("=")
        if not separator:
            continue
        if value in {"true", "false"}:
            fields[key] = value == "true"
        elif value.isdecimal():
            fields[key] = int(value)
        else:
            fields[key] = value
    return fields


def mismatch_metrics(reference: bytes, candidate: bytes) -> JsonObject:
    require(len(reference) == FRAME_BYTES, "reference frame byte count differs")
    require(len(candidate) == FRAME_BYTES, "candidate frame byte count differs")
    reference_array = np.frombuffer(reference, dtype=np.uint8)
    candidate_array = np.frombuffer(candidate, dtype=np.uint8)
    delta = np.abs(reference_array.astype(np.int16) - candidate_array.astype(np.int16))
    unequal = delta != 0
    unequal_indices = np.flatnonzero(unequal)
    return {
        "checkedBytes": FRAME_BYTES,
        "mismatchedBytes": int(unequal_indices.size),
        "mismatchedPixels": int(
            np.count_nonzero(np.any(unequal.reshape((-1, 4)), axis=1))
        ),
        "maximumChannelDelta": int(delta.max(initial=0)),
        "firstMismatchedByte": (
            int(unequal_indices[0]) if unequal_indices.size else -1
        ),
        "exact": unequal_indices.size == 0,
        "referenceSHA256": sha256_bytes(reference),
        "candidateSHA256": sha256_bytes(candidate),
    }


def context_fields(path: Path) -> dict[str, str]:
    require(path.is_file(), "capture context is absent")
    fields: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        key, separator, value = line.partition("=")
        if separator and key.isidentifier():
            require(key not in fields, f"duplicate capture context field: {key}")
            fields[key] = value
    return fields


def validate_capture(
    capture: Path,
    expected_timeline_sha256: str,
    expected_capture_commit: str,
) -> JsonObject:
    timeline_path = capture / "transition-timeline.json"
    require(timeline_path.is_file(), "transition timeline is absent")
    observed_sha256 = sha256_file(timeline_path)
    require(
        observed_sha256 == expected_timeline_sha256,
        "capture timeline differs from the explicitly admitted SHA-256",
    )
    require(
        observed_sha256 not in OPENED_TIMELINE_SHA256,
        "prospective gate was given an opened calibration timeline",
    )
    timeline = load_json(timeline_path, "transition timeline")
    geometry = mapping(timeline.get("geometry"), "timeline geometry")
    dynamic = mapping(
        timeline.get("dynamicBackgroundUniforms"), "dynamic background uniforms"
    )
    require(timeline.get("schemaVersion") == 5, "timeline schema differs")
    require(timeline.get("material") == "regular", "capture material differs")
    require(timeline.get("appearance") == "dark", "capture appearance differs")
    require(timeline.get("direction") == "dematerialize", "capture direction differs")
    require(geometry.get("name") == "circle-480-center", "capture geometry differs")
    require(
        timeline.get("windowBackingScaleFactor") == 2,
        "capture is not physical Retina 2x",
    )
    require(timeline.get("failedSamples") == 0, "capture contains failed samples")
    require(
        dynamic.get("executed") is True
        and dynamic.get("sampleIndices") == list(SAMPLE_INDICES)
        and dynamic.get("executedSampleCount") == len(SAMPLE_INDICES),
        "dynamic capture inventory differs",
    )
    preflight_path = capture / "capture-session-preflight.json"
    preflight = load_json(preflight_path, "capture preflight")
    expected_preflight = {
        "localRetinaCaptureSessionPreflightSchemaVersion": 2,
        "passed": True,
        "displayActive": True,
        "displayAsleep": False,
        "sessionLocked": False,
        "sessionLoginDone": True,
        "sessionOnConsole": True,
        "backingScaleFactor": 2,
        "physicalPixels": [3456, 2234],
        "logicalPoints": [1728, 1117],
    }
    for field, expected in expected_preflight.items():
        require(preflight.get(field) == expected, f"capture preflight {field} differs")
    require(
        (capture / "capture-exit-status.txt").read_text(encoding="utf-8").strip()
        == "0",
        "native capture process failed",
    )
    context_path = capture / "capture-context.txt"
    context = context_fields(context_path)
    expected_context = {
        "CAPTURE_COMMIT": expected_capture_commit,
        "GITHUB_ACTIONS_USED": "0",
        "NATIVE_CAPTURE_DEBUGGER_USED": "0",
        "NIX_STORE_PATH_IN_NATIVE_BUILD_OR_CAPTURE": "0",
        "MACOS_PRODUCT_VERSION": "26.6.1",
        "MACOS_BUILD_VERSION": "25G76",
        "ARCHITECTURE": "arm64",
        "NATIVE_SDK_VERSION": "26.5",
        "PROBE_SHA256": sha256_file(CAPTURE_PROBE),
        "PREREGISTRATION_SHA256": sha256_file(PREREGISTRATION),
        "RUNNER_SHA256": sha256_file(CAPTURE_RUNNER),
        "LG_GLASS_MATERIAL": "regular",
        "LG_GLASS_APPEARANCE": "dark",
        "LG_GLASS_GEOMETRY": "circle-480-center",
        "LG_TRANSITION_TIMELINE": "1",
        "LG_TRANSITION_UNIFORMS": "1",
        "LG_TRANSITION_DIRECTION": "dematerialize",
        "LG_TRANSITION_HIGHLIGHT_TRACE": "1",
        "LG_TRANSITION_CURRENT_COMPOSITOR_TRANSFER_TRACE": "0",
        "LG_TRANSITION_ISCD_BORDER_TRACE": "0",
        "LG_ENABLE_UNSAFE_PRIVATE_INTERPOLANT_TRACE": "0",
    }
    for field, expected in expected_context.items():
        require(context.get(field) == expected, f"capture context {field} differs")
    require(
        context.get("TIMELINE_SHA256") == observed_sha256,
        "capture context timeline hash differs",
    )
    require(
        "/nix/store/" not in context_path.read_text(encoding="utf-8"),
        "capture context contains a Nix store path",
    )
    return {
        "path": str(capture.resolve()),
        "timelineSHA256": observed_sha256,
        "profile": EXPECTED_CAPTURE_PROFILE,
        "failedSamples": 0,
        "sampleIndices": list(SAMPLE_INDICES),
        "captureCommit": expected_capture_commit,
        "contextSHA256": sha256_file(context_path),
        "preflightSHA256": sha256_file(preflight_path),
    }


def validate_fixture(
    directory: Path, sample_index: int, timeline_sha256: str
) -> JsonObject:
    manifest_path = directory / "manifest.json"
    manifest = load_json(manifest_path, "fixture manifest")
    require(manifest.get("schemaVersion") == 2, "fixture schema differs")
    require(manifest.get("sampleIndex") == sample_index, "fixture sample differs")
    require(
        manifest.get("captureTimelineSha256") == timeline_sha256,
        "fixture timeline authority differs",
    )
    require(
        manifest.get("captureAdmission") == "prospective-explicit-timeline-sha256",
        "fixture was not prospectively admitted",
    )
    require(
        manifest.get("capturedRenderInputFields") == [],
        "fixture retained a captured renderer input",
    )
    require(
        manifest.get("capturedFinalOutputUsedForComparisonOnly") is True,
        "fixture output authority is ambiguous",
    )
    construction = mapping(manifest.get("construction"), "fixture construction")
    scissor = mapping(construction.get("backgroundScissor"), "background scissor")
    require(
        scissor.get("source") == "independent-public-state-constructor"
        and scissor.get("capturedStructuralOracleExact") is True,
        "background scissor is not independently constructed and authenticated",
    )

    files = mapping(manifest.get("files"), "fixture files")
    for name, untyped_metadata in files.items():
        require(isinstance(name, str), "fixture filename is malformed")
        metadata = mapping(untyped_metadata, f"fixture metadata {name}")
        path = directory / name
        require(path.is_file(), f"fixture file is absent: {name}")
        require(
            path.stat().st_size == metadata.get("byteCount"), f"{name} size differs"
        )
        require(sha256_file(path) == metadata.get("sha256"), f"{name} hash differs")
        expected_role = (
            "captured-comparison-oracle-only"
            if name == "reference-bottom-left.rgba8"
            else "measured-apple-hardware-intrinsic-lookup"
            if name == "half-intrinsics.r32ui"
            else "independent-input"
        )
        require(metadata.get("role") == expected_role, f"{name} role differs")

    config = struct.unpack(CONFIG_FORMAT, (directory / "config.bin").read_bytes())
    require(config[0] == b"WALLELG3", "fixture config identity differs")
    highlight_vertex_count = int(config[17])
    highlight_index_count = int(config[18])
    require(
        (highlight_vertex_count, highlight_index_count) in {(4, 6), (16, 24)},
        "fixture highlight topology differs",
    )
    highlight = mapping(
        construction.get("highlightInterpolantModel"), "highlight interpolant model"
    )
    return {
        "manifestSHA256": sha256_file(manifest_path),
        "remainingFloat32Bits": manifest.get("remainingFloat32Bits"),
        "highlightVertexCount": highlight_vertex_count,
        "highlightIndexCount": highlight_index_count,
        "highlightBackFacing": highlight.get("backFacing"),
        "fileCount": len(files),
    }


def generator_environment() -> dict[str, str]:
    environment = dict(os.environ)
    module_paths = (ROOT / "analysis", ROOT / "lg-test/Analysis")
    inherited = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = os.pathsep.join(
        [*(str(path) for path in module_paths), *([inherited] if inherited else [])]
    )
    return environment


def run_gate(arguments: argparse.Namespace) -> JsonObject:
    production_shader = ROOT / "shaders/frag.glsl"
    require(
        sha256_file(production_shader) == PROTECTED_SHADER_SHA256,
        "protected production shader changed",
    )
    require(not arguments.work_directory.exists(), "gate work directory already exists")
    arguments.work_directory.mkdir(parents=True)
    capture = validate_capture(
        arguments.capture,
        arguments.expected_timeline_sha256,
        arguments.expected_capture_commit,
    )

    runs: list[JsonObject] = []
    for sample_index in SAMPLE_INDICES:
        label = f"{sample_index:02d}"
        fixture_directory = arguments.work_directory / f"fixture-{label}"
        generator_command = (
            sys.executable,
            str(arguments.generator),
            "--capture",
            str(arguments.capture),
            "--output",
            str(fixture_directory),
            "--profile-emitter",
            str(arguments.profile_emitter),
            "--sample-index",
            str(sample_index),
            "--expected-timeline-sha256",
            arguments.expected_timeline_sha256,
        )
        generated = subprocess.run(
            generator_command,
            check=False,
            capture_output=True,
            text=True,
            env=generator_environment(),
        )
        require(
            generated.returncode == 0,
            f"fixture generation failed for sample {label}: {generated.stderr}",
        )
        fixture = validate_fixture(
            fixture_directory, sample_index, arguments.expected_timeline_sha256
        )

        candidate_path = arguments.work_directory / f"candidate-{label}.rgba8"
        renderer_command = (
            str(arguments.renderer),
            "--device-index",
            str(arguments.device_index),
            str(fixture_directory),
            str(arguments.vertex_shader),
            str(arguments.fragment_shader),
            str(arguments.intrinsic_table),
            str(candidate_path),
            "3",
        )
        started = time.perf_counter()
        completed = subprocess.run(
            renderer_command,
            check=False,
            capture_output=True,
            text=True,
        )
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        require(
            completed.returncode in {0, 1},
            f"renderer failed for sample {label}: {completed.stderr}",
        )
        require(candidate_path.is_file(), f"candidate frame {label} is absent")
        parsed = parse_renderer_output(completed.stdout)
        renderer_name = parsed.get("GL_RENDERER")
        require(
            isinstance(renderer_name, str)
            and arguments.required_renderer_substring in renderer_name,
            f"sample {label} ran on an unadmitted renderer: {renderer_name}",
        )
        independent = mismatch_metrics(
            (fixture_directory / "reference-bottom-left.rgba8").read_bytes(),
            candidate_path.read_bytes(),
        )
        for field in (
            "checkedBytes",
            "mismatchedBytes",
            "mismatchedPixels",
            "maximumChannelDelta",
            "exact",
        ):
            require(
                parsed.get(field) == independent[field],
                f"renderer and independent comparison disagree for {label}: {field}",
            )
        require(
            completed.returncode == (0 if independent["exact"] else 1),
            f"renderer exit status disagrees for sample {label}",
        )
        runs.append(
            {
                "sampleIndex": sample_index,
                "fixture": fixture,
                "device": parsed.get("device"),
                "glVendor": parsed.get("GL_VENDOR"),
                "glRenderer": renderer_name,
                "glVersion": parsed.get("GL_VERSION"),
                "elapsedMilliseconds": elapsed_ms,
                "comparison": independent,
                "rendererExitCode": completed.returncode,
                "rendererStderr": completed.stderr,
            }
        )

    exact = all(run["comparison"]["exact"] for run in runs)
    topology_counts = {
        str(index_count): sum(
            run["fixture"]["highlightIndexCount"] == index_count for run in runs
        )
        for index_count in (6, 24)
    }
    return {
        "schemaVersion": 1,
        "status": (
            "accepted-prospective-eight-state-full-frame-exact"
            if exact
            else "rejected-prospective-eight-state-full-frame-mismatch"
        ),
        "scope": {
            "prospectiveNaturalCapture": True,
            "independentlyGeneratedCompleteRenderInputs": True,
            "capturedRenderInputs": False,
            "capturedFinalOutputsUsedForComparisonOnly": True,
            "walleOwnedCGlRendererRendered": True,
            "productionWalleProcessRendered": False,
            "physicalRetinaWalleOutput": False,
            "tolerance": 0,
            "qualityRegressionAllowed": False,
        },
        "capture": capture,
        "implementation": {
            "generator": str(arguments.generator.resolve()),
            "generatorSHA256": sha256_file(arguments.generator),
            "profileEmitter": str(arguments.profile_emitter.resolve()),
            "profileEmitterSHA256": sha256_file(arguments.profile_emitter),
            "renderer": str(arguments.renderer.resolve()),
            "rendererSHA256": sha256_file(arguments.renderer),
            "rendererSourceSHA256": sha256_file(
                ROOT / "parity/render_walle_exact_static_gl.c"
            ),
            "vertexShaderSHA256": sha256_file(arguments.vertex_shader),
            "fragmentShaderSHA256": sha256_file(arguments.fragment_shader),
            "floatIntrinsicTableSHA256": sha256_file(arguments.intrinsic_table),
            "protectedProductionShaderSHA256": sha256_file(production_shader),
        },
        "runs": runs,
        "topologyCounts": topology_counts,
        "totals": {
            "checkedBytes": sum(run["comparison"]["checkedBytes"] for run in runs),
            "mismatchedBytes": sum(
                run["comparison"]["mismatchedBytes"] for run in runs
            ),
            "mismatchedPixels": sum(
                run["comparison"]["mismatchedPixels"] for run in runs
            ),
            "maximumChannelDelta": max(
                run["comparison"]["maximumChannelDelta"] for run in runs
            ),
        },
        "gate": {
            "prospectiveEightStateFullFrameExact": exact,
            "universalTopologySelectorEstablished": False,
            "productionWalleParityEstablished": False,
        },
    }


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--capture", type=Path, required=True)
    parser.add_argument("--expected-timeline-sha256", required=True)
    parser.add_argument("--expected-capture-commit", required=True)
    parser.add_argument("--work-directory", type=Path, required=True)
    parser.add_argument(
        "--generator",
        type=Path,
        default=ROOT / "analysis/generate_walle_exact_dynamic_fixture.py",
    )
    parser.add_argument(
        "--profile-emitter",
        type=Path,
        default=ROOT / "build/bin/quality/emit_liquid_glass_transition_profile",
    )
    parser.add_argument(
        "--renderer",
        type=Path,
        default=ROOT / "build/bin/quality/render_walle_exact_static_gl",
    )
    parser.add_argument("--vertex-shader", type=Path, required=True)
    parser.add_argument("--fragment-shader", type=Path, required=True)
    parser.add_argument(
        "--intrinsic-table",
        type=Path,
        default=ROOT / "artifacts/apple-float-intrinsics-r8-30556057571.bin",
    )
    parser.add_argument("--device-index", type=int, default=0)
    parser.add_argument(
        "--required-renderer-substring", default="AMD Radeon RX 9070 XT"
    )
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    require(
        len(arguments.expected_timeline_sha256) == 64
        and all(
            character in "0123456789abcdef"
            for character in arguments.expected_timeline_sha256
        ),
        "expected timeline SHA-256 is malformed",
    )
    require(
        len(arguments.expected_capture_commit) == 40
        and all(
            character in "0123456789abcdef"
            for character in arguments.expected_capture_commit
        ),
        "expected capture commit is malformed",
    )
    return arguments


def main() -> int:
    arguments = parse_arguments()
    report = run_gate(arguments)
    encoded = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if arguments.output is not None:
        arguments.output.write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    return 0 if report["gate"]["prospectiveEightStateFullFrameExact"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
