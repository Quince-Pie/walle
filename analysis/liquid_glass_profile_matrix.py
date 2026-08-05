#!/usr/bin/env python3
"""Decode and compare Apple's captured Liquid Glass material profiles."""

import argparse
import hashlib
import json
import math
import platform
import struct
from pathlib import Path
from typing import Any


type JsonObject = dict[str, Any]

GLASS_FRAGMENTS = {
    "clear": "glass_background_sdf_no_bleed_lph",
    "regular": "glass_background_sdf_lph",
}
GLASS_START = 48
GLASS_END = 258

# Offsets are relative to the complete fragment-buffer record. They are
# independently exercised by lg-test's byte-level uniform interventions.
FIELD_SPECS = (
    ("sdf_arg", 0, "4f"),
    ("sdf_transform", 16, "4f"),
    ("sdf_arg2", 32, "4f"),
    ("displacement_matrix", 48, "4f"),
    ("inner_refraction_amount", 64, "f"),
    ("inner_refraction_inverse_height", 68, "f"),
    ("outer_refraction_amount", 72, "f"),
    ("outer_refraction_inverse_height", 76, "f"),
    ("refraction_threshold_0", 80, "f"),
    ("refraction_threshold_1", 84, "f"),
    ("blur_radius", 88, "f"),
    ("edge_bleed_blur_radius", 92, "f"),
    ("edge_bleed_amount", 96, "f"),
    ("edge_bleed_inverse_height", 100, "f"),
    ("shadow_amount", 104, "f"),
    ("shadow_inverse_height", 108, "f"),
    ("shadow_offset", 112, "2f"),
    ("shadow_blur_radius", 120, "f"),
    ("shadow_inverse_radius", 124, "f"),
    ("face_matrix_0", 128, "4e"),
    ("face_matrix_1", 136, "4e"),
    ("face_matrix_2", 144, "4e"),
    ("bleed_matrix_0", 152, "4e"),
    ("bleed_matrix_1", 160, "4e"),
    ("bleed_matrix_2", 168, "4e"),
    ("shadow_matrix_0", 176, "4e"),
    ("shadow_matrix_1", 184, "4e"),
    ("shadow_matrix_2", 192, "4e"),
    ("shadow_contribution", 200, "f"),
    ("shadow_face_opacity", 204, "f"),
    ("blur_alpha", 208, "4e"),
    ("blur_distance", 216, "4e"),
    ("edge_bleed_distance", 224, "2e"),
    ("edge_bleed_opacity", 228, "e"),
    ("face_opacity", 230, "e"),
    ("bleed_darken", 232, "2e"),
    ("shadow_distance_offset", 236, "e"),
    ("shadow_opacity", 238, "e"),
    ("refraction_opacity", 240, "e"),
    ("holding_tone_opacity", 242, "e"),
    ("sdr_shadow_distance", 244, "2e"),
    ("clamp_limit", 248, "e"),
    ("preserve_hue", 250, "e"),
    ("sdr_white_value", 252, "e"),
    ("float_mix_workaround", 254, "e"),
    ("complex_refraction", 256, "e"),
)


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _json_number(value: float) -> float | str:
    if math.isnan(value):
        return "NaN"
    if math.isinf(value):
        return "-Infinity" if value < 0 else "Infinity"
    return value


def _component_bits(
    payload: bytes,
    *,
    offset: int,
    count: int,
    code: str,
) -> list[str]:
    if code == "f":
        values = struct.unpack_from(f"<{count}I", payload, offset)
        return [f"0x{value:08x}" for value in values]
    if code == "e":
        values = struct.unpack_from(f"<{count}H", payload, offset)
        return [f"0x{value:04x}" for value in values]
    raise ValueError(f"unsupported field code: {code}")


def decode_profile(payload: bytes) -> JsonObject:
    if len(payload) < GLASS_END:
        raise ValueError(
            f"uniform payload has {len(payload)} bytes; "
            f"expected at least {GLASS_END}"
        )
    fields: JsonObject = {}
    for name, offset, format_code in FIELD_SPECS:
        values = struct.unpack_from(f"<{format_code}", payload, offset)
        component_code = format_code[-1]
        fields[name] = {
            "values": [_json_number(float(value)) for value in values],
            "bits": _component_bits(
                payload,
                offset=offset,
                count=len(values),
                code=component_code,
            ),
        }
    glass_bytes = payload[GLASS_START:GLASS_END]
    return {
        "glassBytes": len(glass_bytes),
        "glassSha256": sha256_bytes(glass_bytes),
        "glassHex": glass_bytes.hex(),
        "fields": fields,
    }


def _pipeline_fragment(snapshot: JsonObject) -> str:
    pipeline = snapshot.get("pipeline", {})
    descriptor = (
        pipeline.get("creationDescriptor", {})
        if isinstance(pipeline, dict)
        else {}
    )
    return (
        str(descriptor.get("fragmentFunction", ""))
        if isinstance(descriptor, dict)
        else ""
    )


def _glass_uniform_snapshots(
    runtime: JsonObject,
) -> tuple[str, list[JsonObject]]:
    material = runtime.get("materialProfileEvidence", {}).get(
        "material",
    )
    try:
        fragment = GLASS_FRAGMENTS[str(material)]
    except KeyError as error:
        raise ValueError(
            f"unsupported captured material profile: {material!r}"
        ) from error
    snapshots = runtime.get("carendererEvidence", {}).get(
        "metalBufferSnapshots",
        {},
    ).get("snapshots", [])
    return fragment, [
        snapshot
        for snapshot in snapshots
        if isinstance(snapshot, dict)
        and snapshot.get("stage") == "fragment"
        and snapshot.get("index") == 1
        and _pipeline_fragment(snapshot) == fragment
    ]


def _payload(snapshot: JsonObject) -> bytes:
    payload = snapshot.get("payload", {})
    encoded = (
        payload.get("hex")
        if isinstance(payload, dict)
        else None
    )
    if not isinstance(encoded, str):
        raise ValueError("uniform snapshot has no hexadecimal payload")
    return bytes.fromhex(encoded)


def _native_replay_gate(runtime: JsonObject) -> JsonObject:
    exact = runtime.get("carendererEvidence", {}).get(
        "exactPassReplay",
        {},
    )
    independent = exact.get("independentGlassReplay", {})
    candidates = {
        candidate.get("name"): candidate
        for candidate in independent.get("candidates", [])
        if isinstance(candidate, dict)
    }
    profile = candidates.get("custom_profile_fragment_replay", {})
    comparison = profile.get("comparison", {})
    captured_exact = (
        exact.get("executed") is True
        and exact.get("exactByteMatch") is True
        and exact.get("mismatchedByteCount") == 0
        and exact.get("maximumChannelDelta") == 0
    )
    profile_exact = (
        comparison.get("compared") is True
        and comparison.get("exactByteMatch") is True
        and comparison.get("mismatchedByteCount") == 0
        and comparison.get("maximumChannelDelta") == 0
    )
    return {
        "capturedPassExact": captured_exact,
        "independentProfileExact": profile_exact,
        "capturedPass": exact,
        "independentProfileComparison": comparison,
    }


def analyze_artifact(artifact: Path) -> JsonObject:
    runtime_path = artifact / "runtime.json"
    runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
    fragment, snapshots = _glass_uniform_snapshots(runtime)
    if len(snapshots) != 2:
        raise ValueError(
            f"{artifact} has {len(snapshots)} glass uniform records; "
            "expected the main and shadow draws"
        )
    decoded = [decode_profile(_payload(snapshot)) for snapshot in snapshots]
    glass_bodies_equal = (
        decoded[0]["glassHex"] == decoded[1]["glassHex"]
    )
    profile = runtime.get("materialProfileEvidence", {})
    return {
        "artifact": str(artifact),
        "runtimeSchemaVersion": runtime.get("schemaVersion"),
        "runtimeJsonSha256":
            hashlib.sha256(runtime_path.read_bytes()).hexdigest(),
        "material": profile.get("material"),
        "requestedAppearance":
            profile.get("requestedAppearance"),
        "effectiveAppearanceName":
            profile.get("effectiveAppearanceName"),
        "effectiveAppearanceMatchesRequest":
            profile.get("effectiveAppearanceMatchesRequest"),
        "fragmentFunction": fragment,
        "drawUniformBodiesEqual": glass_bodies_equal,
        "draws": [
            {
                "sequence": snapshot.get("sequence"),
                "bufferOffset": snapshot.get("offset"),
                "sdfMode":
                    decoded_profile["fields"]["sdf_arg"][
                        "values"
                    ][2],
                "profile": decoded_profile,
            }
            for snapshot, decoded_profile in zip(
                snapshots,
                decoded,
                strict=True,
            )
        ],
        "profile": decoded[0],
        "nativeReplayGate": _native_replay_gate(runtime),
    }


def _changed_fields(
    baseline: JsonObject,
    candidate: JsonObject,
) -> list[str]:
    baseline_fields = baseline["fields"]
    candidate_fields = candidate["fields"]
    return [
        name
        for name in baseline_fields
        if baseline_fields[name]["bits"]
        != candidate_fields[name]["bits"]
    ]


def analyze(artifacts: list[Path]) -> JsonObject:
    profiles = [analyze_artifact(path) for path in artifacts]
    profiles.sort(
        key=lambda profile: (
            str(profile["material"]),
            str(profile["requestedAppearance"]),
        )
    )
    baseline = next(
        (
            profile
            for profile in profiles
            if profile["material"] == "clear"
            and profile["requestedAppearance"] == "light"
        ),
        profiles[0],
    )
    comparisons = []
    baseline_bytes = bytes.fromhex(baseline["profile"]["glassHex"])
    for profile in profiles:
        candidate_bytes = bytes.fromhex(profile["profile"]["glassHex"])
        changed_offsets = [
            offset
            for offset, (left, right) in enumerate(
                zip(baseline_bytes, candidate_bytes, strict=True)
            )
            if left != right
        ]
        comparisons.append({
            "material": profile["material"],
            "appearance": profile["requestedAppearance"],
            "baselineMaterial": baseline["material"],
            "baselineAppearance":
                baseline["requestedAppearance"],
            "changedBytes": len(changed_offsets),
            "changedByteOffsetsRelativeToGlass":
                changed_offsets,
            "changedFields": _changed_fields(
                baseline["profile"],
                profile["profile"],
            ),
        })
    observed_profiles = {
        (
            profile["material"],
            profile["requestedAppearance"],
        )
        for profile in profiles
    }
    expected_profiles = {
        ("clear", "light"),
        ("clear", "dark"),
        ("regular", "light"),
        ("regular", "dark"),
    }
    all_native_exact = all(
        profile["nativeReplayGate"]["capturedPassExact"]
        and profile["nativeReplayGate"][
            "independentProfileExact"
        ]
        for profile in profiles
    )
    return {
        "liquidGlassProfileMatrixAnalysisSchemaVersion": 1,
        "implementation": {
            "file": "analysis/liquid_glass_profile_matrix.py",
            "python": platform.python_version(),
        },
        "profiles": profiles,
        "comparisonsToClearLight": comparisons,
        "distinctGlassProfiles": len({
            profile["profile"]["glassSha256"]
            for profile in profiles
        }),
        "conclusion": {
            "completeFourProfileMatrix":
                observed_profiles == expected_profiles,
            "effectiveAppearancesVerified": all(
                profile[
                    "effectiveAppearanceMatchesRequest"
                ] is True
                for profile in profiles
            ),
            "mainAndShadowUniformBodiesEqual": all(
                profile["drawUniformBodiesEqual"]
                for profile in profiles
            ),
            "nativeProfileEquationsExact": all_native_exact,
            "portableProfileTableRecovered": (
                observed_profiles == expected_profiles
                and all_native_exact
            ),
            "productionShaderAuthorized": False,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Decode clear/regular and light/dark native material profiles."
        )
    )
    parser.add_argument("artifacts", nargs="+", type=Path)
    parser.add_argument("-o", "--output", type=Path)
    arguments = parser.parse_args()
    report = analyze(arguments.artifacts)
    encoded = json.dumps(
        report,
        indent=2,
        sort_keys=True,
        allow_nan=False,
    ) + "\n"
    if arguments.output is None:
        print(encoded, end="")
    else:
        arguments.output.write_text(encoded, encoding="utf-8")
        print(arguments.output)
    return 0 if all(
        report["conclusion"][key]
        for key in (
            "completeFourProfileMatrix",
            "effectiveAppearancesVerified",
            "mainAndShadowUniformBodiesEqual",
            "nativeProfileEquationsExact",
            "portableProfileTableRecovered",
        )
    ) else 1


if __name__ == "__main__":
    raise SystemExit(main())
