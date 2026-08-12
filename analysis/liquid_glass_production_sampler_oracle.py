#!/usr/bin/env python3
"""Rank portable edge/shadow models against production BGRA8 oracles."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from apple_glass_reference_renderer import AppleGlassReferenceRenderer
from liquid_glass_exact_specialization_gate import default_fixtures
from liquid_glass_glsl_end_to_end_gate import configure_recovered_material
from liquid_glass_regular_source_calibration import load_bgra, source_mips


type JsonObject = dict[str, Any]
type CodeImage = NDArray[np.uint8]
type PixelMask = NDArray[np.bool_]

CAPTURE_SIZE = 1024
ORACLE_PATTERNS = (
    "opaque-coordinate-hash",
    "sampler-basis-level-zero",
    "sampler-basis-level-one",
    "prospective-opaque-seeded-v1",
)
ORACLE_NAMES = (
    "production-edge-sample",
    "production-shadow-sample",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1 << 20):
            digest.update(block)
    return digest.hexdigest()


def comparison(
    reference: CodeImage,
    candidate: CodeImage,
    *,
    mask: PixelMask,
) -> JsonObject:
    if reference.shape != candidate.shape:
        raise ValueError(
            f"image shapes differ: {reference.shape} != {candidate.shape}"
        )
    if mask.shape != reference.shape[:2]:
        raise ValueError("comparison mask dimensions differ")
    difference = candidate.astype(np.int16) - reference.astype(np.int16)
    selected = difference[mask]
    changed = selected != 0
    changed_pixels = np.any(changed, axis=1)
    return {
        "exact": not bool(np.any(changed)),
        "observedValues": int(selected.size),
        "mismatchedValues": int(np.count_nonzero(changed)),
        "mismatchedPixels": int(np.count_nonzero(changed_pixels)),
        "maximumCodeDelta": int(np.abs(selected).max(initial=0)),
        "meanAbsoluteCodeDelta": float(np.abs(selected).mean()),
    }


def oracle_masks() -> dict[str, PixelMask]:
    edge = np.zeros((CAPTURE_SIZE, CAPTURE_SIZE), dtype=np.bool_)
    edge[112:912, 112:912] = True
    shadow = np.zeros_like(edge)
    shadow[56:952, 64:960] = True
    shadow[112:912, 112:912] = False
    return {
        "production-edge-sample": edge,
        "production-shadow-sample": shadow,
    }


def set_common_oracle_uniforms(
    renderer: AppleGlassReferenceRenderer,
) -> None:
    zero = (0.0, 0.0, 0.0, 0.0)
    renderer.program["FaceMatrix0"].value = zero
    renderer.program["FaceMatrix1"].value = zero
    renderer.program["FaceMatrix2"].value = zero
    renderer.program["FaceOpacity"].value = 1.0
    renderer.program["HoldingToneOpacity"].value = 0.0
    renderer.program["ClampLimit"].value = 0.0


def set_oracle_uniforms(
    renderer: AppleGlassReferenceRenderer,
    oracle: str,
) -> None:
    set_common_oracle_uniforms(renderer)
    if oracle == "production-edge-sample":
        renderer.program["BleedMatrix0"].value = (1.0, 0.0, 0.0, 0.0)
        renderer.program["BleedMatrix1"].value = (0.0, 1.0, 0.0, 0.0)
        renderer.program["BleedMatrix2"].value = (0.0, 0.0, 1.0, 0.0)
        renderer.program["EdgeBleedDistance"].value = (-65504.0, -65472.0)
        renderer.program["EdgeBleedOpacity"].value = 1.0
        renderer.program["BleedDarken"].value = (0.0, 1.0)
        renderer.program["ShadowOpacity"].value = 0.0
        return
    if oracle == "production-shadow-sample":
        renderer.program["ShadowInverseRadius"].value = 0.0
        renderer.program["ShadowMatrix0"].value = (1.0, 0.0, 0.0, 0.0)
        renderer.program["ShadowMatrix1"].value = (0.0, 1.0, 0.0, 0.0)
        renderer.program["ShadowMatrix2"].value = (0.0, 0.0, 1.0, 0.0)
        renderer.program["ShadowContribution"].value = 1.0
        renderer.program["ShadowFaceOpacity"].value = 1.0
        renderer.program["EdgeBleedOpacity"].value = 0.0
        renderer.program["ShadowOpacity"].value = 2.0
        return
    raise ValueError(f"unsupported production sampler oracle: {oracle}")


def output_path(capture: Path, replay: JsonObject) -> Path:
    output = replay.get("output", {})
    filename = output.get("rawFile")
    if not isinstance(filename, str):
        raise ValueError("production sampler oracle output is missing")
    path = capture / filename
    if not path.is_file():
        raise ValueError(f"production sampler oracle output is absent: {path}")
    return path


def analyze_pattern(
    capture: Path,
    record: JsonObject,
    *,
    intrinsic_table: Path,
    coefficient_table: Path,
    source_slope_bits: int,
    device_index: int | None,
) -> JsonObject:
    pattern = record.get("name")
    if pattern not in ORACLE_PATTERNS:
        raise ValueError(f"unsupported oracle pattern: {pattern}")
    oracle_records = {
        oracle.get("name"): oracle
        for oracle in record.get("productionSamplerOracles", [])
        if isinstance(oracle, dict)
    }
    if set(oracle_records) != set(ORACLE_NAMES):
        raise ValueError(f"{pattern} production oracle inventory differs")
    context_arguments: dict[str, object] = {}
    if device_index is not None:
        context_arguments["device_index"] = device_index
    masks = oracle_masks()
    result: dict[str, Any] = {"pattern": pattern, "oracles": {}}
    for oracle_name in ORACLE_NAMES:
        oracle = oracle_records[oracle_name]
        reference_path = output_path(capture, oracle["reference"])
        candidate_path = output_path(capture, oracle["candidate"])
        if reference_path.read_bytes() != candidate_path.read_bytes():
            raise ValueError(
                f"{pattern} {oracle_name} Apple/custom output differs"
            )
        reference = load_bgra(reference_path)
        model_uniform = (
            "EdgeSamplerCoordinateModel"
            if oracle_name == "production-edge-sample"
            else "ShadowSamplerCoordinateModel"
        )
        models = []
        with AppleGlassReferenceRenderer(
            capture,
            intrinsic_table=intrinsic_table,
            interpolant_coefficient_table=coefficient_table,
            interpolant_source_slope_bits=source_slope_bits,
            load_interpolant_trace=False,
            load_interpolant_axis_trace=False,
            load_diagnostic_traces=False,
            source_mip_bgra_overrides=source_mips(capture, record),
            context_arguments=context_arguments,
        ) as renderer:
            configure_recovered_material(renderer)
            renderer.program["CoordinateMode"].value = 5
            set_oracle_uniforms(renderer, oracle_name)
            for model in range(7):
                renderer.program[model_uniform].value = model
                candidate = renderer.render()
                models.append({
                    "model": model,
                    "scope": comparison(
                        reference,
                        candidate,
                        mask=masks[oracle_name],
                    ),
                    "fullOutput": comparison(
                        reference,
                        candidate,
                        mask=np.ones_like(masks[oracle_name]),
                    ),
                })
            implementation = renderer.implementation
        best = min(
            models,
            key=lambda item: (
                item["scope"]["mismatchedValues"],
                item["scope"]["mismatchedPixels"],
                item["scope"]["maximumCodeDelta"],
                item["model"],
            ),
        )
        result["oracles"][oracle_name] = {
            "reference": {
                "path": str(reference_path),
                "sha256": sha256_file(reference_path),
            },
            "models": models,
            "bestModel": best["model"],
            "bestScope": best["scope"],
        }
        result["implementation"] = implementation
    return result


def analyze(
    capture: Path,
    *,
    intrinsic_table: Path,
    device_index: int | None,
) -> JsonObject:
    runtime_path = capture / "runtime.json"
    runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
    profile = runtime.get("materialProfileEvidence", {})
    profile_name = (
        f"{profile.get('material')}-{profile.get('requestedAppearance')}"
    )
    fixtures = {fixture.name: fixture for fixture in default_fixtures()}
    if profile_name not in fixtures or not profile_name.startswith("regular-"):
        raise ValueError(f"unsupported production oracle profile: {profile_name}")
    fixture = fixtures[profile_name]
    differential = runtime["carendererEvidence"]["exactPassReplay"][
        "independentGlassReplay"
    ]["sourceTextureDifferential"]
    if differential.get("schemaVersion") not in {4, 5}:
        raise ValueError("production sampler oracle requires schema 4 or 5")
    records = {
        record.get("name"): record
        for record in differential.get("records", [])
        if isinstance(record, dict)
    }
    if not set(ORACLE_PATTERNS) <= records.keys():
        raise ValueError("production sampler oracle patterns are incomplete")
    patterns = [
        analyze_pattern(
            capture,
            records[pattern],
            intrinsic_table=intrinsic_table,
            coefficient_table=fixture.coefficient_table,
            source_slope_bits=fixture.source_slope_bits,
            device_index=device_index,
        )
        for pattern in ORACLE_PATTERNS
    ]
    return {
        "liquidGlassProductionSamplerOracleAnalysisSchemaVersion": 1,
        "capture": str(capture),
        "runtimeSha256": sha256_file(runtime_path),
        "profile": profile_name,
        "patterns": patterns,
        "role": "opened-calibration-not-prospective-validation",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture", type=Path)
    parser.add_argument(
        "--intrinsic-table",
        type=Path,
        default=Path("artifacts/apple-float-intrinsics-r8-30556057571.bin"),
    )
    parser.add_argument("--device-index", type=int)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    report = analyze(
        arguments.capture,
        intrinsic_table=arguments.intrinsic_table,
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
