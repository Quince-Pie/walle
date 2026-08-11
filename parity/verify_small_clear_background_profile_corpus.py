#!/usr/bin/env python3
"""Gate Walle's small-clear Tghn profile packer against retained Apple bytes."""

import argparse
import hashlib
import json
import struct
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import analyze_small_clear_background as background
import analyze_transition_geometry_corpus_local_macos_26_6_1 as model
import validate_variable_blur_selected_region_origin as selected


PROFILE_SIZE = 210
FIXTURE_RECORD_HEADER_SIZE = 28
FIXTURE_MAGIC = b"WLGSCB1\0"
FIXTURE_SCHEMA_VERSION = 1
APPEARANCE_IDS = {"light": 0, "dark": 1}
DIRECTION_IDS = {"materialize": 0, "dematerialize": 1}


@dataclass(frozen=True, slots=True)
class FixtureRecord:
    appearance: int
    direction: int
    diameter: int
    sample_index: int
    fraction_word: int
    element_extent_word: int
    backdrop_scale_word: int
    profile: bytes


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path}: root is not an object")
    return value


def f32_word(value: float) -> int:
    return struct.unpack("<I", struct.pack("<f", value))[0]


def f64_word(value: float) -> int:
    return struct.unpack("<Q", struct.pack("<d", value))[0]


def emit_profile(
    emitter: Path,
    *,
    appearance: str,
    diameter: int,
    fraction: float,
    element_extent: float,
    backdrop_scale: float,
) -> bytes:
    completed = subprocess.run(
        (
            str(emitter),
            str(APPEARANCE_IDS[appearance]),
            str(diameter),
            f"{f32_word(fraction):08x}",
            f"{f64_word(element_extent):016x}",
            f"{f32_word(backdrop_scale):08x}",
        ),
        check=True,
        capture_output=True,
        text=True,
    )
    try:
        result = bytes.fromhex(completed.stdout.strip())
    except ValueError as error:
        raise ValueError("small-clear profile emitter returned non-hex output") from error
    if len(result) != PROFILE_SIZE:
        raise ValueError(
            f"small-clear profile emitter returned {len(result)} bytes, "
            f"expected {PROFILE_SIZE}"
        )
    return result


def timeline_records(
    path: Path,
    emitter: Path,
) -> tuple[dict[str, Any], list[FixtureRecord]]:
    timeline = load_object(path)
    appearance = timeline.get("appearance")
    direction = timeline.get("direction")
    geometry = timeline.get("geometry")
    dynamic = timeline.get("dynamicBackgroundUniforms")
    records = dynamic.get("records") if isinstance(dynamic, dict) else None
    if (
        appearance not in APPEARANCE_IDS
        or direction not in DIRECTION_IDS
        or not isinstance(geometry, dict)
        or isinstance(geometry.get("width"), bool)
        or not isinstance(geometry.get("width"), int)
        or geometry.get("width") != geometry.get("height")
        or not isinstance(records, list)
    ):
        raise ValueError(f"{path}: small-clear timeline contract differs")
    diameter = geometry["width"]

    fixtures: list[FixtureRecord] = []
    exact_bytes = 0
    mismatches: list[dict[str, Any]] = []
    observed_stream = bytearray()
    predicted_stream = bytearray()
    for ordinal, untyped_record in enumerate(records, start=1):
        if not isinstance(untyped_record, dict):
            raise ValueError(f"{path}: dynamic record {ordinal} is not an object")
        if not background.target_records(untyped_record):
            continue
        remaining = model.float32(
            model.finite(untyped_record.get("remaining"), "remaining")
        )
        states = model.layer_states(untyped_record)
        element = model.mapping(
            states.get(background.TARGET_LAYER_PATH), "element layer state"
        )
        element_extent = model.vector(element.get("bounds"), "element bounds", 4)[2]
        backdrop_scale, _ = selected.allocation.captured_scale(untyped_record)
        observed = model.payload(
            background.one_snapshot(
                untyped_record,
                pipeline=background.PIPELINE,
                stage="fragment",
                index=1,
            )
        )[:PROFILE_SIZE]
        if len(observed) != PROFILE_SIZE:
            raise ValueError(f"{path}: dynamic record {ordinal} profile is truncated")
        predicted = emit_profile(
            emitter,
            appearance=appearance,
            diameter=diameter,
            fraction=remaining,
            element_extent=element_extent,
            backdrop_scale=backdrop_scale,
        )
        equal = [left == right for left, right in zip(predicted, observed, strict=True)]
        exact_bytes += sum(equal)
        observed_stream.extend(observed)
        predicted_stream.extend(predicted)
        unequal_offsets = [index for index, value in enumerate(equal) if not value]
        sample_index = model.integer(
            untyped_record.get("sampleIndex"), "sample index"
        )
        if unequal_offsets:
            mismatches.append(
                {
                    "sampleIndex": sample_index,
                    "unequalByteOffsets": unequal_offsets,
                    "predictedSHA256": hashlib.sha256(predicted).hexdigest(),
                    "observedSHA256": hashlib.sha256(observed).hexdigest(),
                }
            )
        fixtures.append(
            FixtureRecord(
                appearance=APPEARANCE_IDS[appearance],
                direction=DIRECTION_IDS[direction],
                diameter=diameter,
                sample_index=sample_index,
                fraction_word=f32_word(remaining),
                element_extent_word=f64_word(element_extent),
                backdrop_scale_word=f32_word(backdrop_scale),
                profile=observed,
            )
        )

    compared_bytes = len(fixtures) * PROFILE_SIZE
    return (
        {
            "timeline": str(path),
            "timelineSHA256": file_sha256(path),
            "appearance": appearance,
            "direction": direction,
            "diameter": diameter,
            "profileCount": len(fixtures),
            "exactProfileCount": len(fixtures) - len(mismatches),
            "comparedByteCount": compared_bytes,
            "exactByteCount": exact_bytes,
            "observedSHA256": hashlib.sha256(observed_stream).hexdigest(),
            "predictedSHA256": hashlib.sha256(predicted_stream).hexdigest(),
            "mismatches": mismatches,
        },
        fixtures,
    )


def encode_fixture(records: list[FixtureRecord]) -> bytes:
    result = bytearray(
        struct.pack(
            "<8sIIII",
            FIXTURE_MAGIC,
            FIXTURE_SCHEMA_VERSION,
            PROFILE_SIZE,
            FIXTURE_RECORD_HEADER_SIZE,
            len(records),
        )
    )
    for record in records:
        result.extend(
            struct.pack(
                "<BBBBIIIQI",
                record.appearance,
                record.direction,
                0,
                0,
                record.diameter,
                record.sample_index,
                record.fraction_word,
                record.element_extent_word,
                record.backdrop_scale_word,
            )
        )
        result.extend(record.profile)
    return bytes(result)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture-root", required=True, type=Path)
    parser.add_argument("--emitter", required=True, type=Path)
    parser.add_argument("--json-output", type=Path)
    parser.add_argument("--fixture-output", type=Path)
    parser.add_argument("--fixture-manifest-output", type=Path)
    arguments = parser.parse_args()
    capture_root = arguments.capture_root.resolve()
    emitter = arguments.emitter.resolve()
    if not capture_root.is_dir():
        parser.error(f"capture root is not a directory: {capture_root}")
    if not emitter.is_file():
        parser.error(f"emitter is not a file: {emitter}")
    if (arguments.fixture_output is None) != (
        arguments.fixture_manifest_output is None
    ):
        parser.error("fixture output and fixture manifest output must be used together")

    retained = background.analyze(capture_root)
    if (
        retained.get("stateCount") != 60
        or retained.get("publicProfileNumericLawClosed") is not True
        or retained.get("bindingTopology", {}).get(
            "fragmentProfileMeaningfulBytesPerState"
        )
        != PROFILE_SIZE
    ):
        raise ValueError("retained small-clear construction contract differs")

    verified: list[tuple[dict[str, Any], list[FixtureRecord]]] = []
    with background.opened.opened_producer_fragments():
        for case_id, expected_sha256 in background.TIMELINES:
            path = capture_root / case_id / "transition-timeline.json"
            if file_sha256(path) != expected_sha256:
                raise ValueError(f"timeline SHA-256 differs: {case_id}")
            verified.append(timeline_records(path, emitter))
    reports = [report for report, _ in verified]
    fixtures = [record for _, records in verified for record in records]
    profile_count = sum(report["profileCount"] for report in reports)
    exact_profile_count = sum(report["exactProfileCount"] for report in reports)
    compared_bytes = sum(report["comparedByteCount"] for report in reports)
    exact_bytes = sum(report["exactByteCount"] for report in reports)
    exact = (
        profile_count == 60
        and exact_profile_count == profile_count
        and exact_bytes == compared_bytes == 12_600
    )
    observed_stream = b"".join(record.profile for record in fixtures)
    observed_sha256 = hashlib.sha256(observed_stream).hexdigest()
    report = {
        "schemaVersion": 1,
        "classification": "retained small-clear Tghn independent profile byte gate",
        "profileSizeBytes": PROFILE_SIZE,
        "emitter": str(emitter),
        "emitterSHA256": file_sha256(emitter),
        "timelines": reports,
        "totals": {
            "profileCount": profile_count,
            "exactProfileCount": exact_profile_count,
            "comparedByteCount": compared_bytes,
            "exactByteCount": exact_bytes,
            "observedSHA256": observed_sha256,
            "predictedSHA256": observed_sha256 if exact else None,
        },
        "exact": exact,
    }
    encoded_report = json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n"
    if arguments.json_output is not None:
        arguments.json_output.write_text(encoded_report, encoding="utf-8")
    if arguments.fixture_output is not None:
        if not exact:
            raise ValueError("refusing to freeze a non-exact small-clear profile corpus")
        fixture = encode_fixture(fixtures)
        arguments.fixture_output.write_bytes(fixture)
        manifest = {
            "schemaVersion": FIXTURE_SCHEMA_VERSION,
            "classification": "retained macOS 26.6.1 small-clear Tghn profile fixture",
            "fixtureSHA256": hashlib.sha256(fixture).hexdigest(),
            "fixtureByteCount": len(fixture),
            "profileSizeBytes": PROFILE_SIZE,
            "profileCount": profile_count,
            "comparedByteCount": compared_bytes,
            "profileStreamSHA256": observed_sha256,
            "generatorSHA256": file_sha256(Path(__file__)),
            "timelines": [
                {
                    name: item[name]
                    for name in (
                        "timelineSHA256",
                        "appearance",
                        "direction",
                        "diameter",
                        "profileCount",
                    )
                }
                for item in reports
            ],
            "scope": {
                "capturedBytesUsedOnlyAsTestOracle": True,
                "smallClearTghnProfilePackingEstablished": True,
                "smallClearTghnPixelSemanticsEstablishedElsewhere": True,
                "smallClearTmuaCompositionEstablished": False,
                "productionWalleParityEstablished": False,
            },
        }
        arguments.fixture_manifest_output.write_text(
            json.dumps(manifest, indent=2, sort_keys=True, allow_nan=False) + "\n",
            encoding="utf-8",
        )
    print(
        "small-clear Tghn profile corpus: "
        f"{exact_profile_count}/{profile_count} exact profiles, "
        f"{exact_bytes}/{compared_bytes} exact bytes"
    )
    return 0 if exact else 1


if __name__ == "__main__":
    raise SystemExit(main())
