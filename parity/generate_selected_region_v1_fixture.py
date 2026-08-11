#!/usr/bin/env python3
"""Freeze the prospective selected-region holdout as a compact C fixture."""

import argparse
import hashlib
import importlib
import json
import struct
import sys
from pathlib import Path
from types import ModuleType
from typing import Any


MAGIC = b"WLGSRO1\0"
FIXTURE_SCHEMA_VERSION = 1
DIAMETER = 500
MODEL_SHA256 = "0fe38fbe4a55689af2157524545698bad021b39f3da830cbd86f6540c0370c5b"
VALIDATOR_SHA256 = "781eca4d9a716c77f28315c181fd53f6466df7a34d805be2fedf3549b1b28600"
HOLDOUT_SHA256 = "eb780c4bc6e7376a3a5857b51dda939d2766ce236e0bf023e4bd53668902a3a1"


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


def load_module(analysis_dir: Path, name: str, expected_sha256: str) -> ModuleType:
    source = analysis_dir / f"{name}.py"
    if sha256_file(source) != expected_sha256:
        raise ValueError(f"frozen {name} SHA-256 differs")
    if str(analysis_dir) not in sys.path:
        sys.path.insert(0, str(analysis_dir))
    return importlib.import_module(name)


def float32_word(value: float) -> int:
    return struct.unpack("<I", struct.pack("<f", value))[0]


def generate(lg_test_root: Path) -> tuple[bytes, dict[str, Any]]:
    analysis_dir = lg_test_root / "Analysis"
    holdout_path = (
        analysis_dir
        / "variable_blur_selected_region_origin_circle500_holdout_result.json"
    )
    if sha256_file(holdout_path) != HOLDOUT_SHA256:
        raise ValueError("selected-region holdout SHA-256 differs")
    holdout = load_json(holdout_path)
    states = holdout.get("states")
    if (
        holdout.get("status") != "passed"
        or holdout.get("authority") != "holdout"
        or holdout.get("geometry") != "circle-500-center"
        or holdout.get("sampleCount") != 32
        or holdout.get("selectedRegionOriginTransferPassed") is not True
        or holdout.get("selectedRegionAllocationTransferPassed") is not True
        or not isinstance(states, list)
        or len(states) != 32
    ):
        raise ValueError("selected-region holdout contract differs")

    model = load_module(
        analysis_dir,
        "analyze_transition_uniform_profile_calibration",
        MODEL_SHA256,
    )
    selected = load_module(
        analysis_dir,
        "validate_variable_blur_selected_region_origin",
        VALIDATOR_SHA256,
    )
    fixture = bytearray(
        struct.pack(
            "<8sIIII", MAGIC, FIXTURE_SCHEMA_VERSION, len(states), 448, DIAMETER
        )
    )

    for expected_index, state in enumerate(states, start=1):
        if not isinstance(state, dict) or state.get("sampleIndex") != expected_index:
            raise ValueError("selected-region sample sequence differs")
        fraction = float(state["remaining"])
        backdrop_scale = float(state["backdropScale"])
        bounds = [int(value) for value in state["bounds"]]
        material = model.predict_numeric_fields(
            material="regular",
            appearance="dark",
            diameter=DIAMETER,
            fraction=fraction,
        )
        radius1 = selected.predict_radius1(
            blur_radius=material["inputBlurRadius"],
            bleed_blur_radius=material["inputBleedBlurRadius"],
            backdrop_scale=backdrop_scale,
        )
        if float32_word(radius1) != float32_word(float(state["radius1"])):
            raise ValueError("selected-region radius join differs")
        mip = selected.predict_mip_policy(radius1=radius1, source_extent=bounds[2:])
        integer_bounds = selected.predict_integer_bounds(
            bounds=bounds,
            radius1=radius1,
            alignment_scale=int(mip["alignmentScale"]),
        )
        if (
            integer_bounds != state["helperIntegerBounds"]
            or int(mip["alignmentExponent"]) != state["alignmentExponent"]
            or int(mip["alignmentScale"]) != state["alignmentScale"]
            or int(mip["levelCount"]) != state["destinationMipCount"]
        ):
            raise ValueError("selected-region frozen model differs")

        fixture.extend(
            struct.pack("<II", float32_word(fraction), float32_word(backdrop_scale))
        )
        fixture.extend(struct.pack("<4i", *bounds))
        fixture.extend(
            struct.pack(
                "<6I",
                float32_word(radius1),
                float32_word(float(mip["scaledRadius"])),
                int(mip["maximumLevelCount"]),
                int(mip["levelCount"]),
                int(mip["alignmentExponent"]),
                int(mip["alignmentScale"]),
            )
        )
        fixture.extend(struct.pack("<4i", *state["helperIntegerBounds"]))
        fixture.extend(struct.pack("<2I", *state["allocatedExtent"]))
        fixture.extend(struct.pack("<2i", *state["copyOffset"]))

    encoded = bytes(fixture)
    manifest = {
        "selectedRegionV1FixtureSchemaVersion": FIXTURE_SCHEMA_VERSION,
        "classification": "Walle fixture derived from the prospective circle-500 selected-region transfer",
        "sampleCount": len(states),
        "exactOutputComparisonCount": 448,
        "fixtureByteCount": len(encoded),
        "fixtureSHA256": hashlib.sha256(encoded).hexdigest(),
        "source": {
            "generatorSHA256": sha256_file(Path(__file__)),
            "materializeModelSHA256": MODEL_SHA256,
            "selectedRegionValidatorSHA256": VALIDATOR_SHA256,
            "prospectiveHoldoutSHA256": HOLDOUT_SHA256,
        },
        "scope": {
            "regularDarkMaterializeSelectedRegionTransferEstablished": True,
            "otherProfilesEstablished": False,
            "independentOpticalTransferEstablished": False,
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
