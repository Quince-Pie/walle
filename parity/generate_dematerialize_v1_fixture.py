#!/usr/bin/env python3
"""Freeze the prospectively proved dematerialize words as a compact C fixture."""

import argparse
import hashlib
import importlib
import json
import struct
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any


MAGIC = b"WLGDTV1\0"
FIXTURE_SCHEMA_VERSION = 1
DEMATERIALIZE_DIRECTION_ID = 1
MODEL_SHA256 = "0fe38fbe4a55689af2157524545698bad021b39f3da830cbd86f6540c0370c5b"
AGGREGATE_SHA256 = "81812b504be06916ea37195a3c5f2c49bf49d93d391735bca22dea7359bb1790"


@dataclass(frozen=True, slots=True, kw_only=True)
class Case:
    case_id: str
    validation_filename: str
    material: str
    appearance: str
    material_id: int
    appearance_id: int
    diameter: int


CASES = (
    Case(
        case_id="clear-light-circle456",
        validation_filename="clear-light-validation.json",
        material="clear",
        appearance="light",
        material_id=0,
        appearance_id=0,
        diameter=456,
    ),
    Case(
        case_id="clear-dark-circle464",
        validation_filename="clear-dark-validation.json",
        material="clear",
        appearance="dark",
        material_id=0,
        appearance_id=1,
        diameter=464,
    ),
    Case(
        case_id="regular-light-circle472",
        validation_filename="regular-light-validation.json",
        material="regular",
        appearance="light",
        material_id=1,
        appearance_id=0,
        diameter=472,
    ),
    Case(
        case_id="regular-dark-circle480",
        validation_filename="regular-dark-validation.json",
        material="regular",
        appearance="dark",
        material_id=1,
        appearance_id=1,
        diameter=480,
    ),
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} is not a JSON object")
    return value


def load_model(analysis_dir: Path) -> ModuleType:
    source = analysis_dir / "analyze_transition_uniform_profile_calibration.py"
    if sha256_file(source) != MODEL_SHA256:
        raise ValueError("frozen common transition model SHA-256 differs")
    sys.path.insert(0, str(analysis_dir))
    return importlib.import_module("analyze_transition_uniform_profile_calibration")


def float32_from_bits(word: str) -> float:
    return struct.unpack("<f", struct.pack("<I", int(word, 16)))[0]


def float32_word(value: float) -> int:
    return struct.unpack("<I", struct.pack("<f", value))[0]


def generate(lg_test_root: Path) -> tuple[bytes, dict[str, Any]]:
    analysis_dir = lg_test_root / "Analysis"
    aggregate_path = (
        analysis_dir / "transition_uniform_dematerialize_4510db6_holdout_result.json"
    )
    if sha256_file(aggregate_path) != AGGREGATE_SHA256:
        raise ValueError("prospective dematerialize aggregate SHA-256 differs")
    aggregate = load_json(aggregate_path)
    aggregate_cases = {
        entry["caseId"]: entry
        for entry in aggregate.get("cases", [])
        if isinstance(entry, dict) and isinstance(entry.get("caseId"), str)
    }
    if (
        aggregate.get("captureCommit")
        != "4510db6f19883f9b6964588643099b9b7857bee7"
        or set(aggregate_cases) != {case.case_id for case in CASES}
    ):
        raise ValueError("prospective dematerialize aggregate contract differs")

    model = load_model(analysis_dir)
    field_count = len(model.NUMERIC_FIELDS)
    if field_count != 47 or model.CLAMP_FIELD != "inputClamp":
        raise ValueError("frozen numeric field inventory differs")

    fixture = bytearray(
        struct.pack(
            "<8sIIII",
            MAGIC,
            FIXTURE_SCHEMA_VERSION,
            field_count,
            len(CASES),
            31,
        )
    )
    manifest_cases: list[dict[str, Any]] = []
    comparison_count = 0
    artifact_dir = (
        lg_test_root
        / "artifacts"
        / "local-transition-uniform-dematerialize-4510db6"
    )

    for case in CASES:
        validation_path = artifact_dir / case.validation_filename
        aggregate_case = aggregate_cases[case.case_id]
        validation_sha256 = sha256_file(validation_path)
        if validation_sha256 != aggregate_case.get("validationSHA256"):
            raise ValueError(f"{case.case_id} validation SHA-256 differs")
        validation = load_json(validation_path)
        analysis = validation.get("uniformAnalysis")
        profile = validation.get("profile")
        records = analysis.get("records") if isinstance(analysis, dict) else None
        if (
            validation.get("caseId") != case.case_id
            or validation.get("captureCommit") != aggregate.get("captureCommit")
            or not isinstance(profile, dict)
            or profile.get("material") != case.material
            or profile.get("appearance") != case.appearance
            or profile.get("direction") != "dematerialize"
            or not isinstance(analysis, dict)
            or analysis.get("material") != case.material
            or analysis.get("appearance") != case.appearance
            or analysis.get("direction") != "dematerialize"
            or analysis.get("diameter") != case.diameter
            or analysis.get("numericComparisonCount") != 1_457
            or analysis.get("numericExactMatchCount") != 1_457
            or analysis.get("structuredRecordCount") != 31
            or not isinstance(records, list)
            or len(records) != 31
        ):
            raise ValueError(f"{case.case_id} validation contract differs")

        fixture.extend(
            struct.pack(
                "<BBHI",
                case.material_id,
                case.appearance_id,
                DEMATERIALIZE_DIRECTION_ID,
                case.diameter,
            )
        )
        for expected_sample_index, record in enumerate(records, start=1):
            if (
                not isinstance(record, dict)
                or record.get("sampleIndex") != expected_sample_index
            ):
                raise ValueError(f"{case.case_id} sample sequence differs")
            fraction_word = int(record["fractionBits"], 16)
            fraction = float32_from_bits(record["fractionBits"])
            predicted = model.predict_numeric_fields(
                material=case.material,
                appearance=case.appearance,
                diameter=case.diameter,
                fraction=fraction,
            )
            fixture.extend(struct.pack("<I", fraction_word))
            for field in model.NUMERIC_FIELDS:
                word = (
                    int(record["inputClampBits"], 16)
                    if field == model.CLAMP_FIELD
                    else float32_word(predicted[field])
                )
                fixture.extend(struct.pack("<I", word))
                comparison_count += 1

        manifest_cases.append(
            {
                "caseId": case.case_id,
                "material": case.material,
                "appearance": case.appearance,
                "direction": "dematerialize",
                "diameter": case.diameter,
                "validationSHA256": validation_sha256,
                "timelineSHA256": aggregate_case["timelineSHA256"],
                "nativeClampResultSHA256": aggregate_case[
                    "nativeClampResultSHA256"
                ],
            }
        )

    if comparison_count != 5_828:
        raise ValueError("fixture comparison count differs")
    encoded = bytes(fixture)
    manifest = {
        "dematerializeV1FixtureSchemaVersion": FIXTURE_SCHEMA_VERSION,
        "classification": (
            "Walle fixture derived from the prospective four-profile "
            "dematerialize transfer"
        ),
        "comparisonPrecision": "IEEE-754 binary32 words",
        "numericFieldCount": field_count,
        "dynamicStateCount": 124,
        "numericComparisonCount": comparison_count,
        "fixtureByteCount": len(encoded),
        "fixtureSHA256": hashlib.sha256(encoded).hexdigest(),
        "source": {
            "generatorSHA256": sha256_file(Path(__file__)),
            "modelSHA256": MODEL_SHA256,
            "prospectiveAggregateSHA256": AGGREGATE_SHA256,
        },
        "cases": manifest_cases,
        "scope": {
            "materializeNumericTransferEstablished": True,
            "dematerializeNumericTransferEstablished": True,
            "directionCompleteNumericTransferEstablished": True,
            "physicalPixelParityEstablished": False,
            "independentWalleZeroByteFrameEstablished": False,
            "liquidGlassParityEstablished": False,
        },
    }
    return encoded, manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lg-test-root", type=Path, default=Path("lg-test"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    arguments = parser.parse_args()
    fixture, manifest = generate(arguments.lg_test_root)
    arguments.output.write_bytes(fixture)
    arguments.manifest.write_text(
        json.dumps(manifest, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    print(arguments.output)
    print(arguments.manifest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
