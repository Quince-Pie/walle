#!/usr/bin/env python3
"""Compare Walle's transition-profile emitter with retained Apple payloads."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import struct
import subprocess
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any


PROFILE_SIZE = 258
MODELED_PROFILE_OFFSET = 64
MATERIAL_IDS = {"clear": 0, "regular": 1}
APPEARANCE_IDS = {"light": 0, "dark": 1}
DIRECTION_IDS = {"materialize": 0, "dematerialize": 1}
FIXTURE_MAGIC = b"WLGTPV1\0"
FIXTURE_SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class FixtureRecord:
    material: int
    appearance: int
    direction: int
    diameter: int
    fraction_word: int
    sdf_half_width_word: int
    sdf_half_height_word: int
    source_texel_step_x_word: int
    source_texel_step_y_word: int
    profile: bytes


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path}: root is not an object")
    return value


def f32_word(value: float) -> int:
    return struct.unpack("<I", struct.pack("<f", value))[0]


def payload_f32_word(payload: bytes, offset: int) -> int:
    return struct.unpack_from("<I", payload, offset)[0]


def f32_word_text(value: float) -> str:
    return f"{f32_word(value):08x}"


def snapshot_payload(snapshot: Any) -> bytes | None:
    if not isinstance(snapshot, dict):
        return None
    payload = snapshot.get("payload")
    if not isinstance(payload, dict) or not isinstance(payload.get("hex"), str):
        return None
    try:
        result = bytes.fromhex(payload["hex"])
    except ValueError:
        return None
    return result if len(result) >= PROFILE_SIZE else None


def snapshots(record: dict[str, Any]) -> list[dict[str, Any]]:
    render = record.get("render")
    if not isinstance(render, dict):
        raise ValueError("dynamic record has no render object")
    capture = render.get("metalBufferSnapshots")
    if not isinstance(capture, dict) or not isinstance(capture.get("snapshots"), list):
        raise ValueError("dynamic record has no Metal buffer snapshots")
    return [entry for entry in capture["snapshots"] if isinstance(entry, dict)]


def matches_profile_signature(
    snapshot: dict[str, Any], payload: bytes, regular: bool
) -> bool:
    if snapshot.get("stage") != "fragment" or snapshot.get("index") != 1:
        return False
    words = struct.unpack_from("<16f", payload)
    expected_fourth = 0.5 if regular else 0.0
    return (
        math.isfinite(words[0])
        and words[0] > 0.0
        and math.isfinite(words[1])
        and words[1] > 0.0
        and words[2:4] == (4.0, expected_fourth)
        and words[4:8] == (1.0, 0.0, 0.0, 1.0)
        and words[8] == 1.0
        and words[9] == 1.0
        and words[10] == min(words[0], words[1])
        and words[11] == 0.0
        and math.isfinite(words[12])
        and words[12] > 0.0
        and words[13:15] == (0.0, 0.0)
        and math.isfinite(words[15])
        and words[15] < 0.0
    )


def matches_background_scalars(payload: bytes, values: dict[str, Any]) -> bool:
    fields = (
        (64, "inputInnerRefractionAmount"),
        (72, "inputOuterRefractionAmount"),
        (80, "inputRefractionDistance0"),
        (96, "inputBleedAmount"),
        (104, "inputShadowAmount"),
        (200, "inputShadowVibrancyContribution"),
    )
    for offset, name in fields:
        value = values.get(name)
        if not isinstance(value, (int, float)):
            raise ValueError(f"background filter has no numeric {name}")
        if payload_f32_word(payload, offset) != f32_word(float(value)):
            return False
    face_opacity = values.get("inputFaceOpacity")
    if not isinstance(face_opacity, (int, float)):
        raise ValueError("background filter has no numeric inputFaceOpacity")
    return payload[230:232] == struct.pack("<e", float(face_opacity))


def extract_profile(record: dict[str, Any], *, regular: bool) -> bytes:
    filter_value = record.get("filter")
    values = filter_value.get("inputValues") if isinstance(filter_value, dict) else None
    if not isinstance(values, dict):
        raise ValueError("dynamic record has no background-filter values")
    candidates: list[bytes] = []
    for snapshot in snapshots(record):
        payload = snapshot_payload(snapshot)
        if (
            payload is not None
            and matches_profile_signature(snapshot, payload, regular)
            and matches_background_scalars(payload, values)
        ):
            candidates.append(payload[:PROFILE_SIZE])
    if len(candidates) != 1:
        raise ValueError(
            f"dynamic record has {len(candidates)} positive-Y background profiles"
        )
    return candidates[0]


def emit_profile(
    emitter: Path,
    *,
    material: str,
    appearance: str,
    diameter: int,
    fraction: float,
    observed: bytes,
) -> bytes:
    geometry = struct.unpack_from("<16f", observed)
    arguments = (
        str(emitter),
        str(MATERIAL_IDS[material]),
        str(APPEARANCE_IDS[appearance]),
        str(diameter),
        f32_word_text(fraction),
        f32_word_text(geometry[0]),
        f32_word_text(geometry[1]),
        f32_word_text(geometry[12]),
        f32_word_text(-geometry[15]),
    )
    completed = subprocess.run(
        arguments,
        check=True,
        capture_output=True,
        text=True,
    )
    try:
        result = bytes.fromhex(completed.stdout.strip())
    except ValueError as error:
        raise ValueError("transition-profile emitter returned non-hex output") from error
    if len(result) != PROFILE_SIZE:
        raise ValueError(
            f"transition-profile emitter returned {len(result)} bytes, expected {PROFILE_SIZE}"
        )
    return result


def verify_timeline(
    path: Path, emitter: Path
) -> tuple[dict[str, Any], list[FixtureRecord]]:
    timeline = load_object(path)
    material = timeline.get("material")
    appearance = timeline.get("appearance")
    direction = timeline.get("direction")
    geometry = timeline.get("geometry")
    dynamic = timeline.get("dynamicBackgroundUniforms")
    records = dynamic.get("records") if isinstance(dynamic, dict) else None
    if (
        material not in MATERIAL_IDS
        or appearance not in APPEARANCE_IDS
        or direction not in {"materialize", "dematerialize"}
        or not isinstance(geometry, dict)
        or not isinstance(geometry.get("width"), int)
        or geometry.get("width") != geometry.get("height")
        or not isinstance(records, list)
        or not records
    ):
        raise ValueError(f"{path}: transition corpus contract differs")

    mismatches: list[dict[str, Any]] = []
    fixture_records: list[FixtureRecord] = []
    exact_bytes = 0
    modeled_exact_bytes = 0
    for ordinal, record_value in enumerate(records, start=1):
        if not isinstance(record_value, dict):
            raise ValueError(f"{path}: dynamic record {ordinal} is not an object")
        filter_value = record_value.get("filter")
        values = filter_value.get("inputValues") if isinstance(filter_value, dict) else None
        fraction = values.get("inputFaceOpacity") if isinstance(values, dict) else None
        if not isinstance(fraction, (int, float)):
            raise ValueError(f"{path}: dynamic record {ordinal} has no fraction")
        observed = extract_profile(record_value, regular=material == "regular")
        predicted = emit_profile(
            emitter,
            material=material,
            appearance=appearance,
            diameter=geometry["width"],
            fraction=float(fraction),
            observed=observed,
        )
        profile_geometry = struct.unpack_from("<16f", observed)
        fixture_records.append(
            FixtureRecord(
                material=MATERIAL_IDS[material],
                appearance=APPEARANCE_IDS[appearance],
                direction=DIRECTION_IDS[direction],
                diameter=geometry["width"],
                fraction_word=f32_word(float(fraction)),
                sdf_half_width_word=payload_f32_word(observed, 0),
                sdf_half_height_word=payload_f32_word(observed, 4),
                source_texel_step_x_word=payload_f32_word(observed, 48),
                source_texel_step_y_word=f32_word(-profile_geometry[15]),
                profile=observed,
            )
        )
        equal = [left == right for left, right in zip(predicted, observed, strict=True)]
        exact_bytes += sum(equal)
        modeled_exact_bytes += sum(equal[MODELED_PROFILE_OFFSET:])
        unequal_offsets = [index for index, value in enumerate(equal) if not value]
        if unequal_offsets:
            mismatches.append(
                {
                    "sampleIndex": record_value.get("sampleIndex"),
                    "unequalByteOffsets": unequal_offsets,
                    "predictedClampHalf": predicted[248:250].hex(),
                    "observedClampHalf": observed[248:250].hex(),
                }
            )

    profile_count = len(records)
    report = {
        "timeline": str(path),
        "timelineSHA256": file_sha256(path),
        "material": material,
        "appearance": appearance,
        "direction": direction,
        "diameter": geometry["width"],
        "profileCount": profile_count,
        "exactProfileCount": profile_count - len(mismatches),
        "comparedByteCount": profile_count * PROFILE_SIZE,
        "exactByteCount": exact_bytes,
        "modeledByteOffset": MODELED_PROFILE_OFFSET,
        "modeledComparedByteCount": profile_count
        * (PROFILE_SIZE - MODELED_PROFILE_OFFSET),
        "modeledExactByteCount": modeled_exact_bytes,
        "mismatches": mismatches,
    }
    return report, fixture_records


def encode_fixture(records: list[FixtureRecord]) -> bytes:
    result = bytearray(
        struct.pack(
            "<8sIIII",
            FIXTURE_MAGIC,
            FIXTURE_SCHEMA_VERSION,
            PROFILE_SIZE,
            MODELED_PROFILE_OFFSET,
            len(records),
        )
    )
    for record in records:
        result.extend(
            struct.pack(
                "<BBBBIIIIII",
                record.material,
                record.appearance,
                record.direction,
                0,
                record.diameter,
                record.fraction_word,
                record.sdf_half_width_word,
                record.sdf_half_height_word,
                record.source_texel_step_x_word,
                record.source_texel_step_y_word,
            )
        )
        result.extend(record.profile)
    return bytes(result)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--emitter", required=True, type=Path)
    parser.add_argument("--timeline", action="append", required=True, type=Path)
    parser.add_argument("--json-output", type=Path)
    parser.add_argument("--fixture-output", type=Path)
    parser.add_argument("--fixture-manifest-output", type=Path)
    arguments = parser.parse_args()
    emitter = arguments.emitter.resolve()
    if not emitter.is_file():
        parser.error(f"emitter is not a file: {emitter}")

    if (arguments.fixture_output is None) != (
        arguments.fixture_manifest_output is None
    ):
        parser.error("fixture output and fixture manifest output must be used together")

    verified = [
        verify_timeline(path.resolve(), emitter) for path in arguments.timeline
    ]
    results = [report for report, _ in verified]
    fixture_records = [record for _, records in verified for record in records]
    totals: defaultdict[str, int] = defaultdict(int)
    for result in results:
        for name in (
            "profileCount",
            "exactProfileCount",
            "comparedByteCount",
            "exactByteCount",
            "modeledComparedByteCount",
            "modeledExactByteCount",
        ):
            totals[name] += result[name]
    report = {
        "schemaVersion": 1,
        "classification": "captured-geometry transition-profile byte gate",
        "profileSizeBytes": PROFILE_SIZE,
        "modeledByteOffset": MODELED_PROFILE_OFFSET,
        "emitter": str(emitter),
        "emitterSHA256": file_sha256(emitter),
        "timelines": results,
        "totals": dict(totals),
        "exact": (
            totals["profileCount"] == totals["exactProfileCount"]
            and totals["comparedByteCount"] == totals["exactByteCount"]
        ),
    }
    encoded = json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n"
    if arguments.json_output is not None:
        arguments.json_output.write_text(encoded, encoding="utf-8")
    if arguments.fixture_output is not None:
        if not report["exact"]:
            raise ValueError("refusing to freeze a non-exact transition-profile corpus")
        fixture = encode_fixture(fixture_records)
        arguments.fixture_output.write_bytes(fixture)
        manifest = {
            "schemaVersion": FIXTURE_SCHEMA_VERSION,
            "classification": "retained macOS 26.6.1 transition-profile payload fixture",
            "fixtureSHA256": hashlib.sha256(fixture).hexdigest(),
            "fixtureByteCount": len(fixture),
            "profileSizeBytes": PROFILE_SIZE,
            "modeledByteOffset": MODELED_PROFILE_OFFSET,
            "profileCount": len(fixture_records),
            "comparedByteCount": totals["comparedByteCount"],
            "modeledComparedByteCount": totals["modeledComparedByteCount"],
            "generatorSHA256": file_sha256(Path(__file__)),
            "timelines": [
                {
                    name: result[name]
                    for name in (
                        "timelineSHA256",
                        "material",
                        "appearance",
                        "direction",
                        "diameter",
                        "profileCount",
                    )
                }
                for result in results
            ],
            "scope": {
                "transitionProfilePackingEstablished": True,
                "capturedGeometryFieldsUsed": True,
                "dynamicGeometryEstablished": False,
                "physicalPixelParityEstablished": False,
                "liquidGlassParityEstablished": False,
            },
        }
        arguments.fixture_manifest_output.write_text(
            json.dumps(manifest, indent=2, sort_keys=True, allow_nan=False) + "\n",
            encoding="utf-8",
        )
    print(
        "transition profile corpus: "
        f"{totals['exactProfileCount']}/{totals['profileCount']} exact profiles, "
        f"{totals['exactByteCount']}/{totals['comparedByteCount']} exact bytes, "
        f"{totals['modeledExactByteCount']}/"
        f"{totals['modeledComparedByteCount']} exact modeled bytes"
    )
    return 0 if report["exact"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
