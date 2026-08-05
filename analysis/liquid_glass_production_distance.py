#!/usr/bin/env python3
"""Analyze distance-only controls of Apple's production blur resource."""

import argparse
import hashlib
import itertools
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
    CHANNELS,
    IDENTITY_VALUES,
    PATCH_SIDE,
    SITE_COUNT,
    _expected_sites,
    float32_bits,
)
from liquid_glass_production_kernel import (
    PATTERN_COUNT,
    SOURCE_DEFINITIONS,
    TILE_SIDE,
    _expected_filter_bits,
    _source_manifest,
    expected_control_patches,
)


type JsonObject = dict[str, Any]
type UInt8Array = NDArray[np.uint8]

EXPECTED_RIG = "native-production-distance-sweep-1.0.0"
EXPECTED_KIND = "production-profile-distance-only-coarse-sdf-bracket"
EXPECTED_SCHEMA = 1
STATE_COUNT = 71
LEADING_PRODUCTION_STATE = 0
OPACITY_ONE_CONTROL_STATES = (1, 2)
OPACITY_HALF_CONTROL_STATES = (3, 4)
THRESHOLD_STATE_START = 5
THRESHOLD_STATE_STOP = 70
THRESHOLD_COUNT = THRESHOLD_STATE_STOP - THRESHOLD_STATE_START
TRAILING_PRODUCTION_STATE = 70
FIRST_LOWER_HALF_BITS = 0xF0E3
LAST_LOWER_HALF_BITS = 0xDE41
CONTROL_MEMBER = "native-production-distance-control-patches.rgb8"
IDENTITY_MEMBER = "native-production-distance-identity-patches.rgb8"
ICC_MEMBER = "native-production-distance-capture-colorspace.icc"
BLOCK_BYTES = 1024 * 1024

FIXED_OPACITIES = (1.0, 0.5, 0.5, 1.0, 1.0)
FIXED_TAIL: JsonObject = {
    "inputInnerRefractionAmount": -60,
    "inputOuterRefractionAmount": 160,
    "inputRefractionOpacity": 0,
    "inputBlurRadius": 1,
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(BLOCK_BYTES):
            digest.update(block)
    return digest.hexdigest()


def _float16(bits: int) -> np.float16:
    return np.asarray(bits, dtype=np.uint16).view(np.float16)[()]


def _float16_bits(value: float) -> str:
    bits = np.asarray(value, dtype=np.float16).view(np.uint16)[()]
    return f"{int(bits):04x}"


def _coarse_lower_half_bits() -> list[int]:
    span = FIRST_LOWER_HALF_BITS - LAST_LOWER_HALF_BITS
    result = [
        FIRST_LOWER_HALF_BITS
        - span * index // (THRESHOLD_COUNT - 1)
        for index in range(THRESHOLD_COUNT)
    ]
    if (
        len(set(result)) != THRESHOLD_COUNT
        or result[0] != FIRST_LOWER_HALF_BITS
        or result[-1] != LAST_LOWER_HALF_BITS
    ):
        raise AssertionError("coarse threshold design is invalid")
    return result


def _state_values(distances: tuple[float, ...]) -> JsonObject:
    return {
        **IDENTITY_VALUES,
        **{
            f"inputBlurOpacity{index}": opacity
            for index, opacity in enumerate(FIXED_OPACITIES)
        },
        **{
            f"inputBlurDistance{index}":
                float(np.float32(distance))
            for index, distance in enumerate(distances)
        },
        **FIXED_TAIL,
    }


def _state_manifest(
    *,
    index: int,
    name: str,
    distances: tuple[float, ...],
    hypothesis: str,
) -> JsonObject:
    float32_distances = tuple(
        float(np.float32(distance))
        for distance in distances
    )
    return {
        "index": index,
        "name": name,
        "resourceBlurRadius": 1,
        "resourceBlurRadiusFloat32Bits": "3f800000",
        "blurOpacities": list(FIXED_OPACITIES),
        "blurDistances": list(float32_distances),
        "blurDistanceFloat16Bits": [
            _float16_bits(distance)
            for distance in float32_distances
        ],
        "blurDistanceFloat32Bits": [
            float32_bits(distance)
            for distance in float32_distances
        ],
        "hypothesis": hypothesis,
    }


def _expected_states() -> list[JsonObject]:
    definitions: list[tuple[str, tuple[float, ...], str]] = [
        (
            "production-live-leading",
            (-400, -1, 0, 0, 0),
            "exact production opacity-one endpoint",
        ),
        (
            "positive-one-opacity-one-control",
            (1, 2, 2, 2, 2),
            "same opacity-one endpoint with different distances",
        ),
        (
            "positive-hundred-opacity-one-control",
            (100, 101, 101, 101, 101),
            "second opacity-one resource-invariance control",
        ),
        (
            "negative-twenty-thousand-opacity-half-control",
            (-20_000, -15_000, 0, 0, 0),
            "saturated opacity-one-half endpoint",
        ),
        (
            "sentinel-wide-opacity-half-control",
            (-10_008, -9_992, 0, 0, 0),
            "same opacity-one-half endpoint with adjacent sentinels",
        ),
    ]
    for lower_bits in _coarse_lower_half_bits():
        upper_bits = lower_bits - 1
        lower = float(np.float32(_float16(lower_bits)))
        upper = float(np.float32(_float16(upper_bits)))
        definitions.append((
            f"production-distance-threshold-lower-{lower_bits:04x}",
            (lower, upper, 0, 0, 0),
            "coarse same-resource adjacent-half SDF threshold",
        ))
    definitions.append((
        "production-live-trailing",
        (-400, -1, 0, 0, 0),
        "exact production repeatability control",
    ))
    if len(definitions) != STATE_COUNT:
        raise AssertionError("production-distance state count differs")
    return [
        _state_manifest(
            index=index,
            name=name,
            distances=distances,
            hypothesis=hypothesis,
        )
        for index, (name, distances, hypothesis) in enumerate(definitions)
    ]


def _validate_manifest(manifest: JsonObject) -> None:
    if (
        manifest.get("schemaVersion") != EXPECTED_SCHEMA
        or manifest.get("rigVersion") != EXPECTED_RIG
        or manifest.get("sweepKind") != EXPECTED_KIND
        or manifest.get("backingScaleFactor") != 1
    ):
        raise ValueError("production-distance rig differs")

    source = manifest.get("sourceDesign")
    if (
        not isinstance(source, dict)
        or source.get("kind")
        != "periodic-independent-rgb-system-identification"
        or source.get("sources") != _source_manifest()
        or source.get("tileWidthPixels") != TILE_SIDE
        or source.get("tileHeightPixels") != TILE_SIDE
        or source.get("patchSidePixels") != PATCH_SIDE
        or source.get("sites") != _expected_sites()
    ):
        raise ValueError("production-distance source design differs")

    expected_states = _expected_states()
    design = manifest.get("lodDesign")
    if (
        not isinstance(design, dict)
        or design.get("states") != expected_states
        or design.get("stateCount") != STATE_COUNT
        or design.get("resourceBlurRadius") != 1
        or design.get("leadingProductionStateIndex") != 0
        or design.get("opacityOneControlStateIndices") != [1, 2]
        or design.get("opacityHalfControlStateIndices") != [3, 4]
        or design.get("coarseThresholdStateIndexRangeInclusive")
        != [5, 69]
        or design.get("coarseThresholdCount") != THRESHOLD_COUNT
        or design.get("coarseLowerHalfBitsRangeInclusive")
        != ["f0e3", "de41"]
        or design.get("trailingProductionStateIndex") != 70
        or design.get("controlledVariables")
        != ["inputBlurDistance0Through4"]
        or design.get("fixedBlurOpacities")
        != list(FIXED_OPACITIES)
        or design.get("fixedInputBlurRadius") != 1
    ):
        raise ValueError("production-distance state design differs")

    marker = manifest.get("productionDistanceInputs")
    if (
        not isinstance(marker, dict)
        or marker.get("inputBlurRadius") != 1
        or marker.get("inputBlurOpacity0Through4")
        != list(FIXED_OPACITIES)
        or marker.get("inputBlurDistance0Through4")
        != "only controlled variables"
        or marker.get("inputInnerRefractionAmount") != -60
        or marker.get("inputOuterRefractionAmount") != 160
        or marker.get("inputRefractionOpacity") != 0
        or marker.get("inputFaceColorMatrixBlack") != 0
        or marker.get("inputFaceColorMatrixWhite") != 1
        or marker.get("inputFaceColorMatrixSaturation") != 1
        or marker.get("inputSDRHoldingToneEnabled") is not False
    ):
        raise ValueError("production-distance input marker differs")

    captures = manifest.get("captures")
    if not isinstance(captures, list) or len(captures) != PATTERN_COUNT:
        raise ValueError("production-distance capture catalog differs")
    for pattern_index, (
        definition,
        capture,
    ) in enumerate(zip(SOURCE_DEFINITIONS, captures, strict=True)):
        name, role, seed = definition
        if (
            capture.get("sourcePatternIndex") != pattern_index
            or capture.get("sourcePatternName") != name
            or capture.get("sourcePatternRole") != role
            or capture.get("sourcePatternSeed")
            != (None if seed is None else f"{seed:08x}")
            or capture.get("captureBackend")
            != "CGWindowListCreateImage"
            or int(capture.get("controlStabilitySamples", 0)) < 2
            or int(capture.get("materializedStabilitySamples", 0)) < 2
        ):
            raise ValueError(
                "production-distance source capture differs at "
                f"{pattern_index}"
            )
        records = capture.get("states")
        if not isinstance(records, list) or len(records) != STATE_COUNT:
            raise ValueError(
                "production-distance state catalog differs at "
                f"{pattern_index}"
            )
        for expected, record in zip(
            expected_states,
            records,
            strict=True,
        ):
            values = _state_values(tuple(expected["blurDistances"]))
            if (
                any(
                    record.get(key) != value
                    for key, value in expected.items()
                )
                or record.get("readbackBlurRadius") != 1
                or record.get("readbackBlurRadiusFloat32Bits")
                != "3f800000"
                or record.get("inputReadbacks") != values
                or record.get("inputReadbackFloat32Bits")
                != _expected_filter_bits(values)
                or record.get("captureBackend")
                != "CGWindowListCreateImage"
                or int(record.get("stabilitySamples", 0)) < 2
            ):
                raise ValueError(
                    "production-distance state/readback differs at "
                    f"source {pattern_index}, state {expected['name']}"
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
class ProductionDistanceSweep:
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
    ) -> "ProductionDistanceSweep":
        if not zipfile.is_zipfile(path):
            raise ValueError("production-distance artifact is not a ZIP")
        with zipfile.ZipFile(path) as archive:
            bad_member = archive.testzip()
            if bad_member is not None:
                raise ValueError(
                    f"production-distance CRC failed: {bad_member}"
                )
            try:
                manifest = json.load(archive.open("manifest.json"))
            except KeyError as error:
                raise ValueError(
                    "production-distance manifest is missing"
                ) from error
            _validate_manifest(manifest)
            evidence = manifest.get("nativeCaptureEvidence")
            if not isinstance(evidence, dict):
                raise ValueError(
                    "production-distance native evidence is missing"
                )
            spatial_records = SITE_COUNT * PATCH_SIDE**2
            control_records = PATTERN_COUNT * spatial_records
            identity_records = (
                PATTERN_COUNT * STATE_COUNT * spatial_records
            )
            expected = {
                "schemaVersion": EXPECTED_SCHEMA,
                "recordFormat": "RGB8",
                "recordStrideBytes": CHANNELS,
                "recordCount": identity_records,
                "file": IDENTITY_MEMBER,
                "fileBytes": identity_records * CHANNELS,
                "controlRecordCount": control_records,
                "controlFile": CONTROL_MEMBER,
                "controlFileBytes": control_records * CHANNELS,
            }
            if any(
                evidence.get(key) != value
                for key, value in expected.items()
            ):
                raise ValueError(
                    "production-distance stream metadata differs"
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
                control_size != expected["controlFileBytes"]
                or identity_size != expected["fileBytes"]
                or evidence.get("controlFileSha256") != control_hash
                or evidence.get("fileSha256") != identity_hash
            ):
                raise ValueError(
                    "production-distance stream digest differs"
                )
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
                    raise ValueError(
                        "production-distance ICC digest differs"
                    )
                member_hashes[ICC_MEMBER] = icc_hash

        spatial_shape = (
            SITE_COUNT,
            PATCH_SIDE,
            PATCH_SIDE,
            CHANNELS,
        )
        return cls(
            manifest=manifest,
            control=np.memmap(
                control_path,
                dtype=np.uint8,
                mode="r",
                shape=(PATTERN_COUNT, *spatial_shape),
            ),
            identity=np.memmap(
                identity_path,
                dtype=np.uint8,
                mode="r",
                shape=(PATTERN_COUNT, STATE_COUNT, *spatial_shape),
            ),
            member_hashes=member_hashes,
        )


def _threshold_diagnostics(identity: UInt8Array) -> JsonObject:
    if identity.shape[0] != PATTERN_COUNT or identity.shape[1] != STATE_COUNT:
        raise ValueError("production-distance identity shape differs")
    one = identity[1:, LEADING_PRODUCTION_STATE]
    half = identity[1:, OPACITY_HALF_CONTROL_STATES[0]]
    discriminating = one != half
    sampled_values = int(discriminating.size)
    discriminating_values = int(np.count_nonzero(discriminating))
    spatial_shape = identity.shape[2:-1]
    spatial_samples = int(np.prod(spatial_shape))
    pattern_spatial_coverage = np.any(
        discriminating,
        axis=-1,
    )
    pattern_coverage_records = {
        SOURCE_DEFINITIONS[index + 1][0]: {
            "spatialSamples": spatial_samples,
            "coveredSpatialSamples": int(np.count_nonzero(
                pattern_spatial_coverage[index]
            )),
            "coveredSpatialFraction": float(np.mean(
                pattern_spatial_coverage[index]
            )),
        }
        for index in range(PATTERN_COUNT - 1)
    }
    pair_coverage_records = []
    for first, second in itertools.combinations(
        range(PATTERN_COUNT - 1),
        2,
    ):
        coverage = (
            pattern_spatial_coverage[first]
            | pattern_spatial_coverage[second]
        )
        pair_coverage_records.append({
            "sourcePatternIndices": [first + 1, second + 1],
            "sourcePatternNames": [
                SOURCE_DEFINITIONS[first + 1][0],
                SOURCE_DEFINITIONS[second + 1][0],
            ],
            "coveredSpatialSamples":
                int(np.count_nonzero(coverage)),
            "coveredSpatialFraction": float(np.mean(coverage)),
        })
    classes = np.full(
        (THRESHOLD_COUNT, *spatial_shape),
        -1,
        dtype=np.int8,
    )
    seen_one = np.zeros_like(discriminating)
    value_reverse_transitions = 0
    intermediate_values = 0
    spatial_conflicts = 0
    spatial_uncovered = 0

    for output_index, state_index in enumerate(
        range(THRESHOLD_STATE_START, THRESHOLD_STATE_STOP)
    ):
        current = identity[1:, state_index]
        equals_one = current == one
        equals_half = current == half
        valid_endpoint = equals_one | equals_half
        intermediate_values += int(np.count_nonzero(
            discriminating & ~valid_endpoint
        ))
        current_one = discriminating & equals_one
        current_half = discriminating & equals_half
        value_reverse_transitions += int(np.count_nonzero(
            seen_one & current_half
        ))
        seen_one |= current_one

        one_evidence = np.any(current_one, axis=(0, -1))
        half_evidence = np.any(current_half, axis=(0, -1))
        conflict = one_evidence & half_evidence
        uncovered = ~(one_evidence | half_evidence)
        spatial_conflicts += int(np.count_nonzero(conflict))
        spatial_uncovered += int(np.count_nonzero(uncovered))
        current_class = classes[output_index]
        current_class[one_evidence & ~conflict] = 1
        current_class[half_evidence & ~conflict] = 0

    valid_spatial_curves = np.all(classes >= 0, axis=0)
    spatial_delta = np.diff(classes, axis=0)
    monotonic_spatial = valid_spatial_curves & np.all(
        spatial_delta >= 0,
        axis=0,
    )
    transition_count = np.count_nonzero(
        spatial_delta == 1,
        axis=0,
    )
    single_transition = monotonic_spatial & (transition_count == 1)
    first_one = np.argmax(classes == 1, axis=0)
    valid_first_one = single_transition & (first_one > 0)
    transition_histogram = {
        str(index): int(np.count_nonzero(
            valid_first_one & (first_one == index)
        ))
        for index in range(1, THRESHOLD_COUNT)
        if np.any(valid_first_one & (first_one == index))
    }

    lower_bits = _coarse_lower_half_bits()
    bracket_records = [
        {
            "firstOpacityOneThresholdIndex": index,
            "samples": count,
            "previousLowerHalfBits":
                f"{lower_bits[index - 1]:04x}",
            "previousLowerDistance":
                float(_float16(lower_bits[index - 1])),
            "currentLowerHalfBits":
                f"{lower_bits[index]:04x}",
            "currentLowerDistance":
                float(_float16(lower_bits[index])),
        }
        for index_string, count in transition_histogram.items()
        for index in (int(index_string),)
    ]
    return {
        "sampledRandomSourceValues": sampled_values,
        "endpointDiscriminatingValues": discriminating_values,
        "endpointDiscriminatingFraction":
            discriminating_values / sampled_values,
        "perPatternSpatialEndpointCoverage":
            pattern_coverage_records,
        "pairSpatialEndpointCoverage":
            pair_coverage_records,
        "thresholdStates": THRESHOLD_COUNT,
        "intermediateDiscriminatingValues": intermediate_values,
        "reverseValueTransitions": value_reverse_transitions,
        "spatialSamples": spatial_samples,
        "spatialClassConflictsAcrossSourcesOrChannels":
            spatial_conflicts,
        "spatialUncoveredStateSamples": spatial_uncovered,
        "validSpatialCurves": int(np.count_nonzero(
            valid_spatial_curves
        )),
        "monotonicSpatialCurves": int(np.count_nonzero(
            monotonic_spatial
        )),
        "singleTransitionSpatialCurves": int(np.count_nonzero(
            single_transition
        )),
        "allThresholdValuesAreExactEndpoints":
            intermediate_values == 0,
        "allSpatialClassesConsistent":
            spatial_conflicts == 0 and spatial_uncovered == 0,
        "allSpatialCurvesMonotonic":
            bool(np.all(monotonic_spatial)),
        "allSpatialCurvesTransitionExactlyOnce":
            bool(np.all(single_transition)),
        "coarseTransitionIndexHistogram": transition_histogram,
        "coarseBracketRecords": bracket_records,
    }


def analyze(path: Path) -> JsonObject:
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(
        prefix="liquid-glass-production-distance-"
    ) as temporary:
        sweep = ProductionDistanceSweep.open(
            path,
            scratch=Path(temporary),
        )
        source_fidelity = difference_metrics(
            sweep.control,
            expected_control_patches(),
        )
        production_repeat = difference_metrics(
            sweep.identity[:, LEADING_PRODUCTION_STATE],
            sweep.identity[:, TRAILING_PRODUCTION_STATE],
        )
        opacity_one_controls = {
            str(index): difference_metrics(
                sweep.identity[:, LEADING_PRODUCTION_STATE],
                sweep.identity[:, index],
            )
            for index in OPACITY_ONE_CONTROL_STATES
        }
        opacity_half_repeat = difference_metrics(
            sweep.identity[:, OPACITY_HALF_CONTROL_STATES[0]],
            sweep.identity[:, OPACITY_HALF_CONTROL_STATES[1]],
        )
        endpoint_difference = difference_metrics(
            sweep.identity[:, LEADING_PRODUCTION_STATE],
            sweep.identity[:, OPACITY_HALF_CONTROL_STATES[0]],
        )
        threshold = _threshold_diagnostics(sweep.identity)
        source = {
            "path": str(path),
            "sha256": sha256_file(path),
            "ciCommit": sweep.manifest["ciCommit"],
            "osVersion": sweep.manifest["osVersion"],
            "architecture": sweep.manifest["architecture"],
            "memberSha256": sweep.member_hashes,
        }

    opacity_one_exact = all(
        metrics["exact"]
        for metrics in opacity_one_controls.values()
    )
    resource_invariant = bool(
        production_repeat["exact"]
        and opacity_one_exact
        and opacity_half_repeat["exact"]
        and not endpoint_difference["exact"]
        and threshold["allThresholdValuesAreExactEndpoints"]
        and threshold["allSpatialClassesConsistent"]
        and threshold["allSpatialCurvesMonotonic"]
    )
    return {
        "liquidGlassProductionDistanceAnalysisSchemaVersion": 1,
        "analysisImplementation": {
            "file":
                "analysis/liquid_glass_production_distance.py",
            "sha256": sha256_file(Path(__file__).resolve()),
            "python": platform.python_version(),
            "numpy": np.__version__,
        },
        "source": source,
        "controls": {
            "capturedSourceVsDeterministicGenerator":
                source_fidelity,
            "leadingVsTrailingProduction": production_repeat,
            "opacityOneEquivalentDistanceProfiles":
                opacity_one_controls,
            "opacityHalfEquivalentDistanceProfiles":
                opacity_half_repeat,
            "opacityOneVsOpacityHalfEndpoint":
                endpoint_difference,
        },
        "thresholdAnalysis": threshold,
        "conclusion": {
            "captureControlsExact": source_fidelity["exact"],
            "distanceOnlyProductionResourceAccepted":
                resource_invariant,
            "coarseSdfBracketRecovered": bool(
                resource_invariant
                and threshold[
                    "allSpatialCurvesTransitionExactlyOnce"
                ]
            ),
            "productionShaderAuthorized": False,
            "nextGate": (
                "scan only the occupied coarse brackets at adjacent "
                "binary16 resolution, then decode production mip "
                "samples without changing opacity or radius"
                if resource_invariant
                else "identify the distance-dependent source "
                "resource configuration"
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
