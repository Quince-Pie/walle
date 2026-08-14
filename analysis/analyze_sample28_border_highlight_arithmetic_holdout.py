#!/usr/bin/env python3
"""Independently gate the prospective sample-28 SDF arithmetic holdout."""

import argparse
import hashlib
import json
import struct
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

import analyze_sample28_border_highlight_arithmetic as calibration
from apple_glass_reference_renderer import AppleGlassReferenceRenderer


type JsonObject = dict[str, Any]
type UInt32Image = NDArray[np.uint32]

ROOT = Path(__file__).resolve().parent.parent
WIDTH = 1_024
HEIGHT = 1_024
PIXELS = WIDTH * HEIGHT
PREFIX = (
    "transition-background-uniform-28-current-Iscd-final-highlight-alpha-"
)
CAPTURE_COMMIT = "49732f6291c80ef7c2b17529369ddbee379fe396"
RECORDED_COMMIT = "49732f620d7b92092cb55afc6715f1a4de3f150e"
PREREGISTRATION_SHA256 = (
    "475e4997a20da7eb7de5b3eeee0e068ab0562aecba5be749a20f573f0810b862"
)
EXPECTED_CAPTURE_SHA256 = {
    "capture-session-preflight.json": (
        "a424b3c50899149ba79ef5e70687a01f896e8fbf058da5437a5a564c71a14034"
    ),
    "capture-exit-status.txt": (
        "9a271f2a916b0b6ee6cecb2426f0b3206ef074578be55d9bc94f6f3fe3ab86aa"
    ),
    "capture-source-sha256.txt": (
        "e2fed41419f6dc3c6a844b19936cd3b0713b098e7edfb499730f8ea98b06a44c"
    ),
    "transition-timeline.json": (
        "e7d9a931b74237a04780ddb6908498db48c192284647935fa4c59310f1efc385"
    ),
    "sample28-border-highlight-arithmetic.json": (
        "781d05a78e78a512e2a1d2756a7a2b5394347bce0f3d725c4e28485fe5294a39"
    ),
}
EXPECTED_SOURCE_IDENTITIES = {
    "Sources/GlassIntrospect/main.swift": (
        "1f3f345cdba6bb328f6987a1948bac126ec9abca5356715272fc4eb3adea9e7e"
    ),
    "Analysis/natural_sample28_border_highlight_arithmetic_preregistration.json": (
        PREREGISTRATION_SHA256
    ),
    "/tmp/lg-holdout-sdf-v4-build.U1PuJH/glass-transition-introspect": (
        "5c8016631ba10ce3a453cf1c19ebd40dbbdd8f697f9f7675bca927451dff50d9"
    ),
}
EXPECTED_CASES = {
    "wide-coarse",
    "tall-coarse",
    "wide-ulp",
    "tall-ulp",
}
HOLDOUT_STAGES = tuple(
    stage
    for stage in calibration.STAGES
    if not stage.name.startswith("interpolant-")
)


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def parse_source_identities(path: Path) -> JsonObject:
    identities: JsonObject = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        digest, name = line.split(maxsplit=1)
        identities[name] = digest
    if identities != EXPECTED_SOURCE_IDENTITIES:
        raise ValueError("captured source identities differ")
    return identities


def validate_git_identity() -> JsonObject:
    repository = ROOT / "lg-test"
    commit = subprocess.check_output(
        ["git", "-C", repository, "rev-parse", CAPTURE_COMMIT],
        text=True,
    ).strip()
    if commit != CAPTURE_COMMIT:
        raise ValueError("capture commit is unavailable")
    committed: JsonObject = {}
    for name in (
        "Sources/GlassIntrospect/main.swift",
        "Analysis/natural_sample28_border_highlight_arithmetic_preregistration.json",
    ):
        payload = subprocess.check_output(
            ["git", "-C", repository, "show", f"{CAPTURE_COMMIT}:{name}"]
        )
        digest = sha256_bytes(payload)
        if digest != EXPECTED_SOURCE_IDENTITIES[name]:
            raise ValueError(f"committed source identity differs: {name}")
        committed[name] = digest
    return committed


def validate_preflight(path: Path) -> JsonObject:
    preflight = json.loads(path.read_text(encoding="utf-8"))
    if (
        preflight.get("passed") is not True
        or preflight.get("displayActive") is not True
        or preflight.get("displayAsleep") is not False
        or preflight.get("sessionLocked") is not False
        or preflight.get("sessionLoginDone") is not True
        or preflight.get("sessionOnConsole") is not True
        or preflight.get("backingScaleFactor") != 2
        or preflight.get("logicalPoints") != [1728, 1117]
        or preflight.get("physicalPixels") != [3456, 2234]
    ):
        raise ValueError("physical-Retina preflight differs")
    return preflight


def validate_commit_correction(capture: Path) -> JsonObject:
    recorded = (capture / "capture-source-commit.txt").read_text(
        encoding="utf-8"
    ).strip()
    correction = json.loads(
        (capture / "capture-source-commit-correction.json").read_text(
            encoding="utf-8"
        )
    )
    authentication = calibration.object_value(
        correction.get("authentication"), label="commit correction authentication"
    )
    if (
        recorded != RECORDED_COMMIT
        or correction.get("recordedCommit") != RECORDED_COMMIT
        or correction.get("correctCommit") != CAPTURE_COMMIT
        or correction.get("renderedEvidenceModified") is not False
        or authentication.get("committedMainSwiftSha256")
        != EXPECTED_SOURCE_IDENTITIES["Sources/GlassIntrospect/main.swift"]
        or authentication.get("capturedMainSwiftSha256")
        != EXPECTED_SOURCE_IDENTITIES["Sources/GlassIntrospect/main.swift"]
        or authentication.get("committedPreregistrationSha256")
        != PREREGISTRATION_SHA256
        or authentication.get("capturedPreregistrationSha256")
        != PREREGISTRATION_SHA256
        or authentication.get("executableSha256")
        != EXPECTED_SOURCE_IDENTITIES[
            "/tmp/lg-holdout-sdf-v4-build.U1PuJH/glass-transition-introspect"
        ]
    ):
        raise ValueError("capture commit correction differs")
    return correction


def validate_output(
    capture: Path,
    output: object,
    *,
    pixel_format: int,
    raw_bytes: int,
    label: str,
) -> tuple[Path, JsonObject]:
    value = calibration.object_value(output, label=label)
    name = value.get("rawFile")
    if (
        not isinstance(name, str)
        or value.get("rawCapture") is not True
        or value.get("width") != WIDTH
        or value.get("height") != HEIGHT
        or value.get("pixelFormat") != pixel_format
        or value.get("rawBytes") != raw_bytes
    ):
        raise ValueError(f"{label} metadata differs")
    path = capture / name
    if path.stat().st_size != raw_bytes:
        raise ValueError(f"{label} byte count differs")
    return path, {
        "rawFile": name,
        "rawBytes": raw_bytes,
        "fnv1a64": value.get("fnv1a64"),
        "sha256": calibration.sha256_file(path),
    }


def compare_bytes(reference: Path, candidate: Path) -> JsonObject:
    first = np.memmap(reference, mode="r", dtype=np.uint8)
    second = np.memmap(candidate, mode="r", dtype=np.uint8)
    return calibration.compare_words(first, second)


def reference_stage(
    capture: Path,
    prefix: str,
    stage: calibration.Stage,
) -> UInt32Image:
    words = calibration.load_uint32(
        capture / f"{prefix}{stage.file_suffix}"
    )[..., stage.channel]
    return (
        (words >> np.uint32(stage.shift)) & np.uint32(stage.mask)
    ).copy()


def alpha_oracle_uniform(payload: bytes) -> bytes:
    result = bytearray(payload)
    words = [0] * 15 + [0x3C00] * 4 + [0] * 5
    result[0x60:0x90] = struct.pack("<24H", *words)
    return bytes(result)


def validate_capture(
    capture: Path,
    preregistration: Mapping[str, Any],
) -> tuple[JsonObject, JsonObject]:
    fixed_hashes = {
        name: calibration.require_hash(
            capture / name, expected, label=f"capture file {name}"
        )
        for name, expected in EXPECTED_CAPTURE_SHA256.items()
    }
    if (capture / "capture-exit-status.txt").read_text(encoding="utf-8") != "0\n":
        raise ValueError("capture exit status differs")
    preflight = validate_preflight(capture / "capture-session-preflight.json")
    sources = parse_source_identities(capture / "capture-source-sha256.txt")
    correction = validate_commit_correction(capture)
    committed = validate_git_identity()

    timeline = json.loads(
        (capture / "transition-timeline.json").read_text(encoding="utf-8")
    )
    if (
        timeline.get("failedSamples") != 0
        or timeline.get("sampleCount") != 33
        or timeline.get("appearance") != "dark"
        or timeline.get("material") != "regular"
        or timeline.get("direction") != "dematerialize"
        or timeline.get("windowBackingScaleFactor") != 2
    ):
        raise ValueError("capture timeline scope differs")
    dynamic = calibration.object_value(
        timeline.get("dynamicBackgroundUniforms"), label="dynamic uniforms"
    )
    records = dynamic.get("records")
    if not isinstance(records, list):
        raise ValueError("dynamic uniform records are absent")
    sample = next(
        (
            value
            for value in records
            if isinstance(value, Mapping) and value.get("sampleIndex") == 28
        ),
        None,
    )
    if sample is None:
        raise ValueError("sample-28 dynamic uniform record is absent")
    nested = sample["render"]["exactPassReplay"]["currentIscdInterpolantTrace"]
    record = json.loads(
        (capture / "sample28-border-highlight-arithmetic.json").read_text(
            encoding="utf-8"
        )
    )
    if nested != record:
        raise ValueError("selected sample-28 record differs from timeline")

    transport = calibration.object_value(
        record.get("sample28BorderFragmentTransport"), label="fragment transport"
    )
    diagnostics = calibration.object_value(
        record.get("customHighlightSDFDiagnostics"), label="SDF diagnostics"
    )
    tomography = calibration.object_value(
        record.get("stageTomography"), label="tomography"
    )
    holdout = calibration.object_value(
        record.get("sdfArithmeticHoldout"), label="SDF holdout"
    )
    if (
        record.get("executed") is not True
        or record.get("systemSpecializationExact") is not True
        or record.get("capturedAppleFunctionUnmodified") is not True
        or transport.get("executed") is not True
        or diagnostics.get("executed") is not True
        or diagnostics.get("pipelineCount") != 9
        or diagnostics.get("replayCount") != 9
        or tomography.get("executed") is not True
        or tomography.get("caseCount") != 10
        or holdout.get("executed") is not True
        or holdout.get("caseCount") != 4
        or holdout.get("customDiagnosticStageCount") != 9
        or holdout.get("capturedAppleFunctionUnmodified") is not True
        or holdout.get("currentSystemSpecializationUnmodified") is not True
    ):
        raise ValueError("sample-28 prospective trace is incomplete")

    preregistered_cases = preregistration["prospectiveHoldout"][
        "sdfInputInterventions"
    ]
    preregistered_by_name = {case["name"]: case for case in preregistered_cases}
    cases = holdout.get("cases")
    if (
        not isinstance(cases, list)
        or {case.get("name") for case in cases if isinstance(case, Mapping)}
        != EXPECTED_CASES
        or set(preregistered_by_name) != EXPECTED_CASES
    ):
        raise ValueError("prospective SDF case set differs")
    for raw_case in cases:
        case = calibration.object_value(raw_case, label="SDF holdout case")
        frozen = preregistered_by_name[case["name"]]
        frozen_runtime_edits = [
            {
                "field": edit["field"],
                "recordOffset": edit["recordOffset"],
                "hex": edit["hex"],
            }
            for edit in frozen["edits"]
        ]
        comparison = case.get("capturedPrivateVsCurrentSystem")
        calibration.exact_comparison(
            comparison,
            label=f"{case['name']} captured/private specialization",
        )
        if (
            case.get("executed") is not True
            or case.get("stageReplayCount") != 9
            or case.get("edits") != frozen_runtime_edits
            or case.get("naturalUniformPrefixSHA256")
            != frozen["naturalUniformPrefixSha256"]
            or case.get("alphaOracleUniformPrefixSHA256")
            != frozen["alphaOracleUniformPrefixSha256"]
            or case.get("sdfRecordSHA256") != frozen["sdfRecordSha256"]
        ):
            raise ValueError(f"prospective SDF case differs: {case['name']}")
    return record, {
        "fixedFileSha256": fixed_hashes,
        "preflight": preflight,
        "capturedSourceIdentities": sources,
        "committedSourceIdentities": committed,
        "commitCorrection": correction,
        "timelineSampleCount": timeline["sampleCount"],
    }


def run(arguments: argparse.Namespace) -> JsonObject:
    source_hashes = {
        relative: calibration.require_hash(
            ROOT / relative, expected, label=relative
        )
        for relative, expected in calibration.EXPECTED_SOURCE_SHA256.items()
    }
    production_hash = calibration.require_hash(
        ROOT / "shaders/frag.glsl",
        calibration.EXPECTED_PRODUCTION_SHADER_SHA256,
        label="protected production shader",
    )
    intrinsic_hash = calibration.require_hash(
        arguments.intrinsic_table,
        calibration.EXPECTED_INTRINSIC_SHA256,
        label="Apple float-intrinsic table",
    )
    preregistration_path = ROOT / (
        "lg-test/Analysis/"
        "natural_sample28_border_highlight_arithmetic_preregistration.json"
    )
    preregistration_hash = calibration.require_hash(
        preregistration_path,
        PREREGISTRATION_SHA256,
        label="prospective arithmetic preregistration",
    )
    preregistration = json.loads(
        preregistration_path.read_text(encoding="utf-8")
    )
    if preregistration.get("schemaVersion") != 4:
        raise ValueError("prospective arithmetic preregistration schema differs")
    record, capture_provenance = validate_capture(
        arguments.capture, preregistration
    )
    manifest, fixture_provenance = calibration.validate_fixture(arguments.fixture)
    modes, config_provenance = calibration.sweep.load_modes(arguments.fixture)
    base_payload = (arguments.fixture / "highlight-uniform.bin").read_bytes()
    if (
        len(base_payload) != 248
        or sha256_bytes(base_payload)
        != preregistration["prospectiveHoldout"]["baseUniformPrefixSha256"]
    ):
        raise ValueError("frozen base uniform differs")

    raw_cases = record["sdfArithmeticHoldout"]["cases"]
    case_results: JsonObject = {}
    raw_file_hashes: JsonObject = {}
    with AppleGlassReferenceRenderer(
        arguments.fixture,
        **calibration.renderer_options(
            arguments.fixture,
            manifest,
            arguments.intrinsic_table,
            arguments.device_index,
        ),
    ) as renderer:
        renderer.program["CoordinateMode"].value = 7
        renderer.program["AppleInterpolantAxisStart"].value = 0
        renderer.program["UseAppleIntrinsicTable"].value = 1
        renderer.program["UseAppleHalfIntrinsicTable"].value = 0
        calibration.sweep.set_modes(renderer, modes)

        for raw_case in raw_cases:
            case = calibration.object_value(raw_case, label="SDF holdout case")
            name = case["name"]
            edited = calibration.edited_uniform(base_payload, case.get("edits"))
            oracle_payload = alpha_oracle_uniform(edited)
            if (
                sha256_bytes(edited) != case["naturalUniformPrefixSHA256"]
                or sha256_bytes(oracle_payload)
                != case["alphaOracleUniformPrefixSHA256"]
                or sha256_bytes(oracle_payload[:48]) != case["sdfRecordSHA256"]
            ):
                raise ValueError(f"holdout payload identity differs: {name}")

            case_prefix = f"{PREFIX}sdf-holdout-{name}-"
            stage_replays = {
                value["name"]: value
                for value in case["stageReplays"]
                if isinstance(value, Mapping)
            }
            if set(stage_replays) != {
                "sdf",
                "sdf-float",
                "sdf-geometry",
                "sdf-oval",
                "sdf-normal",
                "sdf-shape-geometry",
                "sdf-shape-normal",
                "sdf-radial-normal",
                "sdf-composite-normal",
            }:
                raise ValueError(f"holdout diagnostic stage set differs: {name}")
            for stage_name, replay in stage_replays.items():
                output = replay["replay"]["output"]
                pixel_format = 115 if stage_name == "sdf" else 123
                raw_bytes = PIXELS * (8 if stage_name == "sdf" else 16)
                path, provenance = validate_output(
                    arguments.capture,
                    output,
                    pixel_format=pixel_format,
                    raw_bytes=raw_bytes,
                    label=f"{name} {stage_name}",
                )
                expected_name = (
                    f"{case_prefix}custom-{stage_name}-"
                    + ("rgba16f.raw" if stage_name == "sdf" else "rgba32ui.raw")
                )
                if path.name != expected_name:
                    raise ValueError(f"holdout stage filename differs: {name} {stage_name}")
                raw_file_hashes[path.name] = provenance

            private_path, private_provenance = validate_output(
                arguments.capture,
                case["capturedPrivateBGRA8"]["output"],
                pixel_format=80,
                raw_bytes=PIXELS * 4,
                label=f"{name} captured private",
            )
            system_path, system_provenance = validate_output(
                arguments.capture,
                case["currentSystemBGRA8"]["output"],
                pixel_format=80,
                raw_bytes=PIXELS * 4,
                label=f"{name} current system",
            )
            half_path, half_provenance = validate_output(
                arguments.capture,
                case["currentSystemRGBA16Float"]["output"],
                pixel_format=115,
                raw_bytes=PIXELS * 8,
                label=f"{name} current system half",
            )
            raw_file_hashes[private_path.name] = private_provenance
            raw_file_hashes[system_path.name] = system_provenance
            raw_file_hashes[half_path.name] = half_provenance
            private_system = compare_bytes(private_path, system_path)

            candidate_stages: JsonObject = {}
            renderer.program["HighlightSdfNormalMode"].value = 5
            for stage in HOLDOUT_STAGES:
                candidate_stages[stage.name] = calibration.compare_words(
                    reference_stage(arguments.capture, case_prefix, stage),
                    calibration.render_stage(renderer, oracle_payload, stage),
                )
            candidate_stage_total = calibration.summarize(candidate_stages)

            sdf_reference = calibration.load_half(
                arguments.capture / f"{case_prefix}custom-sdf-rgba16f.raw"
            )
            candidate_sdf = calibration.compare_words(
                sdf_reference[..., :3],
                renderer.render_final_highlight_half(
                    uniform_payload=oracle_payload,
                    trace_mode=1,
                )[..., :3],
            )
            alpha_reference = calibration.alpha_reference(half_path)
            candidate_alpha = calibration.compare_words(
                alpha_reference,
                calibration.render_alpha(renderer, oracle_payload),
            )

            radial_stage = next(
                stage
                for stage in HOLDOUT_STAGES
                if stage.name == "radial-input-y"
            )
            renderer.program["HighlightSdfNormalMode"].value = 0
            baseline_radial = calibration.compare_words(
                reference_stage(arguments.capture, case_prefix, radial_stage),
                calibration.render_stage(renderer, oracle_payload, radial_stage),
            )
            baseline_alpha = calibration.compare_words(
                alpha_reference,
                calibration.render_alpha(renderer, oracle_payload),
            )
            case_exact = (
                private_system["mismatchedWords"] == 0
                and candidate_stage_total["mismatchedWords"] == 0
                and candidate_sdf["mismatchedWords"] == 0
                and candidate_alpha["mismatchedWords"] == 0
            )
            positive_control = baseline_radial["mismatchedWords"] > 0
            case_results[name] = {
                "uniforms": {
                    "naturalSha256": sha256_bytes(edited),
                    "alphaOracleSha256": sha256_bytes(oracle_payload),
                    "sdfRecordSha256": sha256_bytes(oracle_payload[:48]),
                },
                "capturedPrivateVsCurrentSystem": private_system,
                "baseline": {
                    "radialInputY": baseline_radial,
                    "alpha": baseline_alpha,
                    "positiveControlPassed": positive_control,
                },
                "candidate": {
                    "stageTotals": candidate_stage_total,
                    "stages": candidate_stages,
                    "sdfXYZ": candidate_sdf,
                    "alpha": candidate_alpha,
                },
                "exact": case_exact,
            }
        implementation = renderer.implementation

    candidate_stage_totals = calibration.summarize(
        {
            f"{case_name}/{stage_name}": comparison
            for case_name, case in case_results.items()
            for stage_name, comparison in case["candidate"]["stages"].items()
        }
    )
    candidate_sdf_totals = calibration.summarize(
        {
            name: case["candidate"]["sdfXYZ"]
            for name, case in case_results.items()
        }
    )
    candidate_alpha_totals = calibration.summarize(
        {
            name: case["candidate"]["alpha"]
            for name, case in case_results.items()
        }
    )
    private_system_totals = calibration.summarize(
        {
            name: case["capturedPrivateVsCurrentSystem"]
            for name, case in case_results.items()
        }
    )
    positive_controls_passed = all(
        case["baseline"]["positiveControlPassed"]
        for case in case_results.values()
    )
    holdout_exact = (
        set(case_results) == EXPECTED_CASES
        and all(case["exact"] for case in case_results.values())
        and positive_controls_passed
    )
    return {
        "schemaVersion": 1,
        "scope": "prospective non-square sample-28 final-highlight SDF arithmetic holdout",
        "provenance": {
            "capture": capture_provenance,
            "fixture": fixture_provenance,
            "config": config_provenance,
            "sources": source_hashes,
            "analyzerSha256": calibration.sha256_file(Path(__file__)),
            "preregistrationSha256": preregistration_hash,
            "floatIntrinsicTableSha256": intrinsic_hash,
            "rawFiles": raw_file_hashes,
            "productionShader": {
                "sha256": production_hash,
                "modified": False,
                "renderedByThisGate": False,
            },
            "implementation": implementation,
        },
        "recoveredRule": {
            "radialY": "point.y * (arg.x * apple_fast_reciprocal(arg.y))",
            "radialSquared": "radial.y * radial.y + (radial.x * radial.x)",
            "radialInverseLength": "apple_fast_rsqrt(radialSquared)",
            "perPixelCorrectionTableUsed": False,
            "capturedPixelSurfaceUsedAsRendererInput": False,
        },
        "totals": {
            "candidateStages": candidate_stage_totals,
            "candidateSdfXYZ": candidate_sdf_totals,
            "candidateAlpha": candidate_alpha_totals,
            "capturedPrivateVsCurrentSystem": private_system_totals,
        },
        "cases": case_results,
        "gate": {
            "positiveControlsPassed": positive_controls_passed,
            "prospectiveHoldoutExact": holdout_exact,
            "comparisonTolerance": 0,
            "calibrationPromoted": holdout_exact,
            "safeForGuardedWalleIntegration": holdout_exact,
            "eightStateAmdFrameGateRequired": True,
            "generalTopologySelectorRequired": True,
            "remainingAlgorithmFamilyUnknowns": 1,
            "productionWalleParityEstablished": False,
            "shaderQualityReductionAllowed": False,
        },
    }


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--capture",
        type=Path,
        default=(
            ROOT
            / "artifacts/local-retina-sample28-sdf-holdout-49732f6-v2"
        ),
    )
    parser.add_argument(
        "--fixture",
        type=Path,
        default=Path("/tmp/walle-border-fixture-base"),
    )
    parser.add_argument(
        "--intrinsic-table",
        type=Path,
        default=ROOT / "artifacts/apple-float-intrinsics-r8-30556057571.bin",
    )
    parser.add_argument("--device-index", type=int, default=1)
    parser.add_argument(
        "--output",
        type=Path,
        default=(
            ROOT
            / "artifacts/sample28-border-highlight-arithmetic-holdout-result.json"
        ),
    )
    return parser.parse_args()


def main() -> int:
    arguments = parse_arguments()
    report = run(arguments)
    encoded = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if arguments.output is None:
        print(encoded, end="")
    else:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(encoded, encoding="utf-8")
        print(arguments.output)
    return 0 if report["gate"]["prospectiveHoldoutExact"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
