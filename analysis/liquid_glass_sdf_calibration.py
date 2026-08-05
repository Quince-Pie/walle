#!/usr/bin/env python3
"""Analyze controlled Apple Liquid Glass SDF-distance interventions."""

import argparse
import hashlib
import json
import platform
import resource
import tempfile
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from liquid_glass_fixed_resource_lod import difference_metrics
from liquid_glass_lod_sweep import (
    IDENTITY_VALUES,
    PATCH_SIDE,
    SITE_COUNT,
    float32_bits,
)


type JsonObject = dict[str, Any]
type UInt8Array = NDArray[np.uint8]

EXPECTED_RIG = "native-sdf-distance-calibration-1.0.0"
EXPECTED_KIND = "controlled-sdf-distance-mutability-and-range-grid"
RESOURCE_RADIUS = 4.0
CHANNELS = 3
CONTROL_MEMBER = "native-sdf-calibration-control-patches.rgb8"
IDENTITY_MEMBER = "native-sdf-calibration-identity-patches.rgb8"
ICC_MEMBER = "native-sdf-calibration-capture-colorspace.icc"
BLOCK_BYTES = 1024 * 1024

CALIBRATION_STATES = (
    (
        "pinned-radius-zero",
        (0.0, 0.0, 1.0, 1.0, 1.0),
        (-400.0, -1.0, 0.0, 0.0, 0.0),
        "radius-zero endpoint with the trailing resource maxima pinned",
    ),
    (
        "pinned-radius-four",
        (1.0, 1.0, 1.0, 1.0, 1.0),
        (-400.0, -1.0, 0.0, 0.0, 0.0),
        "radius-four endpoint",
    ),
    (
        "collapsed-sentinel-threshold",
        (0.0, 1.0, 1.0, 1.0, 1.0),
        (-10_000.0, -9_999.0, 0.0, 0.0, 0.0),
        "both breakpoints collapse to binary16 negative ten thousand",
    ),
    (
        "sentinel-lower-bracket",
        (0.0, 1.0, 1.0, 1.0, 1.0),
        (-10_008.0, -10_000.0, 0.0, 0.0, 0.0),
        "an exact negative-ten-thousand field selects radius four",
    ),
    (
        "sentinel-upper-bracket",
        (0.0, 1.0, 1.0, 1.0, 1.0),
        (-10_000.0, -9_992.0, 0.0, 0.0, 0.0),
        "an exact negative-ten-thousand field selects radius zero",
    ),
    (
        "far-positive-threshold",
        (0.0, 1.0, 1.0, 1.0, 1.0),
        (1.0, 2.0, 2.0, 2.0, 2.0),
        "protected interior predicts the radius-zero endpoint",
    ),
    (
        "live-range-threshold",
        (0.0, 1.0, 1.0, 1.0, 1.0),
        (-400.0, -1.0, 0.0, 0.0, 0.0),
        "tests the live public distance interval",
    ),
    (
        "raw-lower-threshold",
        (0.0, 1.0, 1.0, 1.0, 1.0),
        (-400.25, -400.0, 0.0, 0.0, 0.0),
        "repeats the first failed raw threshold endpoint",
    ),
    (
        "raw-upper-threshold",
        (0.0, 1.0, 1.0, 1.0, 1.0),
        (-271.75, -271.5, 0.0, 0.0, 0.0),
        "repeats the last failed raw threshold endpoint",
    ),
    (
        "normalized-full-threshold",
        (0.0, 1.0, 1.0, 1.0, 1.0),
        (-1.0, 0.0, 0.0, 0.0, 0.0),
        "tests a normalized signed-distance interval",
    ),
    (
        "normalized-interior-threshold",
        (0.0, 1.0, 1.0, 1.0, 1.0),
        (-0.75, -0.5, 0.0, 0.0, 0.0),
        "tests the protected normalized interior",
    ),
)
STATE_COUNT = len(CALIBRATION_STATES)
SAME_PROFILE_INDICES = tuple(range(2, STATE_COUNT))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(BLOCK_BYTES):
            digest.update(block)
    return digest.hexdigest()


def _float16_bits(value: float) -> str:
    bits = np.asarray(value, dtype=np.float16).view(np.uint16)[()]
    return f"{int(bits):04x}"


def _state_values(
    opacities: tuple[float, ...],
    distances: tuple[float, ...],
) -> JsonObject:
    return {
        **IDENTITY_VALUES,
        **{
            f"inputBlurOpacity{index}": value
            for index, value in enumerate(opacities)
        },
        **{
            f"inputBlurDistance{index}": value
            for index, value in enumerate(distances)
        },
        "inputInnerRefractionAmount": -60,
        "inputOuterRefractionAmount": 160,
        "inputRefractionOpacity": 0,
        "inputBlurRadius": RESOURCE_RADIUS,
    }


def _expected_states() -> list[JsonObject]:
    return [
        {
            "index": index,
            "name": f"sdf-calibration-{name}",
            "resourceBlurRadius": RESOURCE_RADIUS,
            "resourceBlurRadiusFloat32Bits": "40800000",
            "blurOpacities": list(opacities),
            "blurDistances": list(distances),
            "blurDistanceFloat16Bits": [
                _float16_bits(value)
                for value in distances
            ],
            "hypothesis": hypothesis,
        }
        for index, (
            name,
            opacities,
            distances,
            hypothesis,
        ) in enumerate(CALIBRATION_STATES)
    ]


def _validate_manifest(manifest: JsonObject) -> None:
    if (
        manifest.get("schemaVersion") != 1
        or manifest.get("rigVersion") != EXPECTED_RIG
        or manifest.get("sweepKind") != EXPECTED_KIND
    ):
        raise ValueError("SDF calibration rig differs")
    design = manifest.get("lodDesign")
    expected_states = _expected_states()
    if (
        not isinstance(design, dict)
        or design.get("states") != expected_states
        or design.get("resourceBlurRadius") != RESOURCE_RADIUS
        or design.get("stateCount") != STATE_COUNT
        or design.get("controlledVariables")
        != [
            "inputBlurOpacity0Through4",
            "inputBlurDistance0Through4",
        ]
        or design.get("fixedInputs")
        != {
            "inputBlurRadius": RESOURCE_RADIUS,
            "inputInnerRefractionAmount": -60,
            "inputOuterRefractionAmount": 160,
            "inputRefractionOpacity": 0,
        }
    ):
        raise ValueError("SDF calibration design differs")
    marker = manifest.get("sdfCalibrationInputs")
    if marker != {
        "inputBlurRadius": RESOURCE_RADIUS,
        "stateNames": [state[0] for state in CALIBRATION_STATES],
        "inputInnerRefractionAmount": -60,
        "inputOuterRefractionAmount": 160,
        "inputRefractionOpacity": 0,
    }:
        raise ValueError("SDF calibration input marker differs")

    captures = manifest.get("captures")
    if not isinstance(captures, list) or len(captures) != 1:
        raise ValueError("SDF calibration capture catalog differs")
    capture = captures[0]
    if (
        capture.get("sourcePatternIndex") != 0
        or capture.get("captureBackend") != "CGWindowListCreateImage"
        or int(capture.get("controlStabilitySamples", 0)) < 2
        or int(capture.get("materializedStabilitySamples", 0)) < 2
    ):
        raise ValueError("SDF calibration source capture differs")
    records = capture.get("states")
    if not isinstance(records, list) or len(records) != STATE_COUNT:
        raise ValueError("SDF calibration state catalog differs")
    for definition, expected, record in zip(
        CALIBRATION_STATES,
        expected_states,
        records,
        strict=True,
    ):
        values = _state_values(definition[1], definition[2])
        bits = {
            key: float32_bits(float(value))
            for key, value in values.items()
            if not isinstance(value, bool)
        }
        if (
            any(record.get(key) != value for key, value in expected.items())
            or record.get("inputReadbacks") != values
            or record.get("inputReadbackFloat32Bits") != bits
            or record.get("readbackBlurRadius") != RESOURCE_RADIUS
            or record.get("readbackBlurRadiusFloat32Bits") != "40800000"
            or record.get("captureBackend") != "CGWindowListCreateImage"
            or int(record.get("stabilitySamples", 0)) < 2
        ):
            raise ValueError(
                "SDF calibration readback differs at "
                f"{expected['name']}"
            )


def _copy_member(
    archive: zipfile.ZipFile,
    member: str,
    output: Path,
) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with archive.open(member) as source, output.open("xb") as target:
        while block := source.read(BLOCK_BYTES):
            target.write(block)
            digest.update(block)
            size += len(block)
    return size, digest.hexdigest()


@dataclass(frozen=True, slots=True, kw_only=True)
class SdfCalibrationSweep:
    manifest: JsonObject
    control: UInt8Array
    identity: UInt8Array
    member_hashes: dict[str, str]

    @classmethod
    def open(
        cls,
        path: Path,
        *,
        scratch: Path,
    ) -> "SdfCalibrationSweep":
        if not zipfile.is_zipfile(path):
            raise ValueError("SDF calibration artifact is not a ZIP")
        with zipfile.ZipFile(path) as archive:
            bad_member = archive.testzip()
            if bad_member is not None:
                raise ValueError(
                    f"SDF calibration CRC failed: {bad_member}"
                )
            try:
                manifest = json.load(archive.open("manifest.json"))
            except KeyError as error:
                raise ValueError(
                    "SDF calibration manifest is missing"
                ) from error
            _validate_manifest(manifest)
            evidence = manifest.get("nativeCaptureEvidence")
            if not isinstance(evidence, dict):
                raise ValueError("SDF calibration evidence is missing")
            samples = SITE_COUNT * PATCH_SIDE**2
            control_bytes = samples * CHANNELS
            identity_bytes = STATE_COUNT * control_bytes
            expected = {
                "recordFormat": "RGB8",
                "recordStrideBytes": CHANNELS,
                "recordCount": STATE_COUNT * samples,
                "file": IDENTITY_MEMBER,
                "fileBytes": identity_bytes,
                "controlRecordCount": samples,
                "controlFile": CONTROL_MEMBER,
                "controlFileBytes": control_bytes,
            }
            if any(evidence.get(key) != value for key, value in expected.items()):
                raise ValueError(
                    "SDF calibration stream metadata differs"
                )
            control_path = scratch / "control.rgb8"
            identity_path = scratch / "identity.rgb8"
            control_size, control_hash = _copy_member(
                archive,
                CONTROL_MEMBER,
                control_path,
            )
            identity_size, identity_hash = _copy_member(
                archive,
                IDENTITY_MEMBER,
                identity_path,
            )
            if (
                control_size != control_bytes
                or identity_size != identity_bytes
                or evidence.get("controlFileSha256") != control_hash
                or evidence.get("fileSha256") != identity_hash
            ):
                raise ValueError("SDF calibration stream digest differs")
            member_hashes = {
                CONTROL_MEMBER: control_hash,
                IDENTITY_MEMBER: identity_hash,
            }
            if ICC_MEMBER in archive.namelist():
                icc_path = scratch / "capture.icc"
                _, icc_hash = _copy_member(
                    archive,
                    ICC_MEMBER,
                    icc_path,
                )
                if (
                    evidence.get("iccFile") != ICC_MEMBER
                    or evidence.get("iccFileSha256") != icc_hash
                ):
                    raise ValueError("SDF calibration ICC digest differs")
                member_hashes[ICC_MEMBER] = icc_hash

        shape = (SITE_COUNT, PATCH_SIDE, PATCH_SIDE, CHANNELS)
        return cls(
            manifest=manifest,
            control=np.memmap(
                control_path,
                dtype=np.uint8,
                mode="r",
                shape=shape,
            ),
            identity=np.memmap(
                identity_path,
                dtype=np.uint8,
                mode="r",
                shape=(STATE_COUNT, *shape),
            ),
            member_hashes=member_hashes,
        )


def _state_comparisons(identity: UInt8Array) -> list[JsonObject]:
    radius_zero = identity[0]
    radius_four = identity[1]
    return [
        {
            "index": index,
            "name": f"sdf-calibration-{definition[0]}",
            "vsPinnedRadiusZero": difference_metrics(state, radius_zero),
            "vsPinnedRadiusFour": difference_metrics(state, radius_four),
        }
        for index, (definition, state) in enumerate(
            zip(CALIBRATION_STATES, identity, strict=True)
        )
    ]


def _same_profile_analysis(identity: UInt8Array) -> JsonObject:
    groups: list[list[int]] = []
    for index in SAME_PROFILE_INDICES:
        for group in groups:
            if np.array_equal(identity[index], identity[group[0]]):
                group.append(index)
                break
        else:
            groups.append([index])

    classes = [
        {
            "representativeIndex": group[0],
            "representativeName":
                f"sdf-calibration-{CALIBRATION_STATES[group[0]][0]}",
            "memberIndices": group,
            "memberNames": [
                f"sdf-calibration-{CALIBRATION_STATES[index][0]}"
                for index in group
            ],
        }
        for group in groups
    ]
    class_difference = (
        difference_metrics(
            identity[groups[0][0]],
            identity[groups[1][0]],
        )
        if len(groups) == 2
        else None
    )
    return {
        "blurOpacities": [0.0, 1.0, 1.0, 1.0, 1.0],
        "stateIndices": list(SAME_PROFILE_INDICES),
        "exactResponseClasses": classes,
        "classCount": len(groups),
        "twoClassDifference": class_difference,
    }


def analyze(path: Path) -> JsonObject:
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(
        prefix="liquid-glass-sdf-calibration-"
    ) as temporary:
        sweep = SdfCalibrationSweep.open(
            path,
            scratch=Path(temporary),
        )
        comparisons = _state_comparisons(sweep.identity)
        same_profile = _same_profile_analysis(sweep.identity)
        endpoint_difference = difference_metrics(
            sweep.identity[0],
            sweep.identity[1],
        )
        source = {
            "path": str(path),
            "sha256": sha256_file(path),
            "ciCommit": sweep.manifest["ciCommit"],
            "osVersion": sweep.manifest["osVersion"],
            "architecture": sweep.manifest["architecture"],
            "memberSha256": sweep.member_hashes,
        }

    response_groups = [
        group["memberIndices"]
        for group in same_profile["exactResponseClasses"]
    ]
    sentinel_states_same = any(
        3 in group and 4 in group
        for group in response_groups
    )
    zero_class = next(
        (group for group in response_groups if 5 in group),
        [],
    )
    live_zero = bool(
        comparisons[6]["vsPinnedRadiusZero"]["exact"]
        and all(index in zero_class for index in range(5, 11))
    )
    two_class_difference = same_profile["twoClassDifference"]
    distance_live = bool(
        not endpoint_difference["exact"]
        and same_profile["classCount"] == 2
        and isinstance(two_class_difference, dict)
        and two_class_difference["changedPixels"]
        == two_class_difference["pixels"]
        and sentinel_states_same
        and live_zero
    )
    return {
        "liquidGlassSdfCalibrationAnalysisSchemaVersion": 1,
        "analysisImplementation": {
            "file": "analysis/liquid_glass_sdf_calibration.py",
            "sha256": sha256_file(Path(__file__).resolve()),
            "python": platform.python_version(),
            "numpy": np.__version__,
        },
        "source": source,
        "endpointDifference": endpoint_difference,
        "stateComparisons": comparisons,
        "sameProfileAnalysis": same_profile,
        "conclusion": {
            "pinnedEndpointsDiscriminate":
                not endpoint_difference["exact"],
            "pinnedEndpointComparisonsHaveResourceProfileConfound": True,
            "distanceInputsRendererLive": distance_live,
            "sameProfileDistanceInterventionHasTwoExactClasses":
                bool(same_profile["classCount"] == 2),
            "sameProfileClassesDifferAtEveryPixel": bool(
                isinstance(two_class_difference, dict)
                and two_class_difference["changedPixels"]
                == two_class_difference["pixels"]
            ),
            "adjacentSentinelBracketsSelectSameClass":
                bool(sentinel_states_same),
            "deepInteriorSentinelExactlyNegativeTenThousand": False,
            "exactSampledSdfHalfWordsRecovered": False,
            "liveRangeMatchesPinnedRadiusZeroExactly":
                bool(comparisons[6]["vsPinnedRadiusZero"]["exact"]),
            "protectedGridUsesOpacityZeroAtLiveProfile": live_zero,
            "protectedGridIsDeeperThanRuntimeEightHundredPixelRange":
                live_zero,
            "rawThresholdAssumptionAccepted": False,
            "productionShaderAuthorized": False,
            "nextGate": (
                "recover and replay the production radius-one source "
                "pyramid and filter on randomized protected holdouts"
                if distance_live and live_zero
                else "resolve the same-profile distance intervention"
            ),
        },
        "resourceMeasurements": {
            "analysisSeconds": time.perf_counter() - started,
            "maximumResidentSetKiB":
                resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact", type=Path)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    encoded = json.dumps(
        analyze(arguments.artifact),
        indent=2,
        sort_keys=True,
        allow_nan=False,
    ) + "\n"
    if arguments.output is None:
        print(encoded, end="")
    else:
        arguments.output.write_text(encoded)


if __name__ == "__main__":
    main()
