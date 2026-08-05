#!/usr/bin/env python3
"""Recover Apple's production Liquid Glass SDF half words exactly."""

import argparse
import hashlib
import json
import platform
import resource
import tempfile
import time
import zipfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from liquid_glass_fixed_resource_lod import difference_metrics
from liquid_glass_lod_sweep import (
    CHANNELS,
    PATCH_SIDE,
    SITE_COUNT,
    _expected_sites,
)
from liquid_glass_production_distance import (
    FIXED_OPACITIES,
    LEADING_PRODUCTION_STATE as PRIOR_PRODUCTION_STATE,
    OPACITY_HALF_CONTROL_STATES as PRIOR_HALF_STATES,
    OPACITY_ONE_CONTROL_STATES as PRIOR_ONE_STATES,
    ProductionDistanceSweep,
    _float16,
    _state_manifest,
    _state_values,
)
from liquid_glass_production_kernel import (
    SOURCE_DEFINITIONS,
    TILE_SIDE,
    _expected_filter_bits,
    _source_manifest,
    expected_control_patches,
)


type JsonObject = dict[str, Any]
type UInt8Array = NDArray[np.uint8]
type UInt16Array = NDArray[np.uint16]

EXPECTED_RIG = "native-production-sdf-exact-sweep-1.0.0"
EXPECTED_KIND = "production-profile-exact-adjacent-half-sdf-recovery"
EXPECTED_SCHEMA = 1
SOURCE_INDICES = (1, 2)
SOURCE_COUNT = len(SOURCE_INDICES)
FIRST_LOWER_HALF_BITS = 0xE7DD
LAST_LOWER_HALF_BITS = 0xE53E
LOWER_HALF_BITS = tuple(
    range(FIRST_LOWER_HALF_BITS, LAST_LOWER_HALF_BITS - 1, -1)
)
THRESHOLD_COUNT = len(LOWER_HALF_BITS)
STATE_COUNT = 4 + THRESHOLD_COUNT + 1
LEADING_PRODUCTION_STATE = 0
OPACITY_ONE_CONTROL_STATE = 1
OPACITY_HALF_CONTROL_STATES = (2, 3)
THRESHOLD_STATE_START = 4
THRESHOLD_STATE_STOP = THRESHOLD_STATE_START + THRESHOLD_COUNT
TRAILING_PRODUCTION_STATE = THRESHOLD_STATE_STOP
CONTROL_MEMBER = "native-production-sdf-exact-control-patches.rgb8"
IDENTITY_MEMBER = "native-production-sdf-exact-identity-patches.rgb8"
ICC_MEMBER = "native-production-sdf-exact-capture-colorspace.icc"
BLOCK_BYTES = 1024 * 1024


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(BLOCK_BYTES):
            digest.update(block)
    return digest.hexdigest()


def _selected_source_manifest() -> list[JsonObject]:
    manifest = _source_manifest()
    return [manifest[index] for index in SOURCE_INDICES]


def _expected_states() -> list[JsonObject]:
    definitions: list[tuple[str, tuple[float, ...], str]] = [
        (
            "production-exact-live-leading",
            (-400, -1, 0, 0, 0),
            "exact production opacity-one endpoint",
        ),
        (
            "production-exact-positive-one-control",
            (1, 2, 2, 2, 2),
            "same opacity-one endpoint with different distances",
        ),
        (
            "production-exact-far-negative-half-control",
            (-20_000, -15_000, 0, 0, 0),
            "saturated opacity-one-half endpoint",
        ),
        (
            "production-exact-sentinel-half-control",
            (-10_008, -9_992, 0, 0, 0),
            "same opacity-one-half endpoint with adjacent sentinels",
        ),
    ]
    for lower_bits in LOWER_HALF_BITS:
        upper_bits = lower_bits - 1
        definitions.append((
            f"production-exact-threshold-lower-{lower_bits:04x}",
            (
                float(np.float32(_float16(lower_bits))),
                float(np.float32(_float16(upper_bits))),
                0,
                0,
                0,
            ),
            "exact same-resource adjacent-half SDF threshold",
        ))
    definitions.append((
        "production-exact-live-trailing",
        (-400, -1, 0, 0, 0),
        "exact production repeatability control",
    ))
    if len(definitions) != STATE_COUNT:
        raise AssertionError("exact SDF state count differs")
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
        raise ValueError("exact production SDF rig differs")

    source = manifest.get("sourceDesign")
    if (
        not isinstance(source, dict)
        or source.get("kind")
        != "periodic-independent-rgb-system-identification"
        or source.get("sources") != _selected_source_manifest()
        or source.get("tileWidthPixels") != TILE_SIDE
        or source.get("tileHeightPixels") != TILE_SIDE
        or source.get("patchSidePixels") != PATCH_SIDE
        or source.get("sites") != _expected_sites()
    ):
        raise ValueError("exact production SDF source design differs")

    expected_states = _expected_states()
    design = manifest.get("lodDesign")
    if (
        not isinstance(design, dict)
        or design.get("states") != expected_states
        or design.get("stateCount") != STATE_COUNT
        or design.get("resourceBlurRadius") != 1
        or design.get("leadingProductionStateIndex") != 0
        or design.get("opacityOneControlStateIndex") != 1
        or design.get("opacityHalfControlStateIndices") != [2, 3]
        or design.get("thresholdStateIndexRangeInclusive")
        != [4, 675]
        or design.get("thresholdCount") != THRESHOLD_COUNT
        or design.get("lowerHalfBitsTraversalInclusive")
        != ["e7dd", "e53e"]
        or design.get("lowerDistanceTraversal")
        != "strictly increasing numeric value"
        or design.get("upperDistance")
        != "next greater finite binary16 value"
        or design.get("trailingProductionStateIndex") != 676
        or design.get("controlledVariables")
        != ["inputBlurDistance0Through4"]
        or design.get("fixedBlurOpacities")
        != list(FIXED_OPACITIES)
        or design.get("fixedInputBlurRadius") != 1
        or design.get("sourcePatternIndices") != list(SOURCE_INDICES)
        or design.get("sourceCoverageEvidenceRun") != 30518053052
        or design.get("sourceCoverageSpatialSamples") != 104_976
        or design.get("sourceCoverageMissingSamples") != 0
    ):
        raise ValueError("exact production SDF state design differs")

    marker = manifest.get("productionSdfExactInputs")
    if (
        not isinstance(marker, dict)
        or marker.get("inputBlurRadius") != 1
        or marker.get("inputBlurOpacity0Through4")
        != list(FIXED_OPACITIES)
        or marker.get("inputBlurDistance0")
        != (
            "enumerates every binary16 lower breakpoint "
            "from 0xe7dd through 0xe53e"
        )
        or marker.get("inputBlurDistance1")
        != "next greater finite binary16 value"
        or marker.get("inputBlurDistance2Through4") != [0, 0, 0]
        or marker.get("inputInnerRefractionAmount") != -60
        or marker.get("inputOuterRefractionAmount") != 160
        or marker.get("inputRefractionOpacity") != 0
        or marker.get("inputFaceColorMatrixBlack") != 0
        or marker.get("inputFaceColorMatrixWhite") != 1
        or marker.get("inputFaceColorMatrixSaturation") != 1
        or marker.get("inputSDRHoldingToneEnabled") is not False
    ):
        raise ValueError("exact production SDF input marker differs")

    captures = manifest.get("captures")
    if not isinstance(captures, list) or len(captures) != SOURCE_COUNT:
        raise ValueError("exact production SDF capture catalog differs")
    for source_index, capture in zip(
        SOURCE_INDICES,
        captures,
        strict=True,
    ):
        name, role, seed = SOURCE_DEFINITIONS[source_index]
        if (
            capture.get("sourcePatternIndex") != source_index
            or capture.get("sourcePatternName") != name
            or capture.get("sourcePatternRole") != role
            or capture.get("sourcePatternSeed") != f"{seed:08x}"
            or capture.get("captureBackend")
            != "CGWindowListCreateImage"
            or int(capture.get("controlStabilitySamples", 0)) < 2
            or int(capture.get("materializedStabilitySamples", 0)) < 2
        ):
            raise ValueError(
                f"exact production SDF source differs at {source_index}"
            )
        records = capture.get("states")
        if not isinstance(records, list) or len(records) != STATE_COUNT:
            raise ValueError(
                "exact production SDF state catalog differs at "
                f"{source_index}"
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
                    "exact production SDF state/readback differs at "
                    f"source {source_index}, state {expected['name']}"
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
class ProductionSdfExactSweep:
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
    ) -> "ProductionSdfExactSweep":
        if not zipfile.is_zipfile(path):
            raise ValueError("exact production SDF artifact is not a ZIP")
        with zipfile.ZipFile(path) as archive:
            bad_member = archive.testzip()
            if bad_member is not None:
                raise ValueError(
                    f"exact production SDF CRC failed: {bad_member}"
                )
            try:
                manifest = json.load(archive.open("manifest.json"))
            except KeyError as error:
                raise ValueError(
                    "exact production SDF manifest is missing"
                ) from error
            _validate_manifest(manifest)
            evidence = manifest.get("nativeCaptureEvidence")
            if not isinstance(evidence, dict):
                raise ValueError(
                    "exact production SDF native evidence is missing"
                )
            spatial_records = SITE_COUNT * PATCH_SIDE**2
            control_records = SOURCE_COUNT * spatial_records
            identity_records = (
                SOURCE_COUNT * STATE_COUNT * spatial_records
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
                    "exact production SDF stream metadata differs"
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
                    "exact production SDF stream digest differs"
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
                        "exact production SDF ICC digest differs"
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
                shape=(SOURCE_COUNT, *spatial_shape),
            ),
            identity=np.memmap(
                identity_path,
                dtype=np.uint8,
                mode="r",
                shape=(SOURCE_COUNT, STATE_COUNT, *spatial_shape),
            ),
            member_hashes=member_hashes,
        )


def decode_sdf_half_words(
    identity: UInt8Array,
) -> tuple[JsonObject, UInt16Array, NDArray[np.bool_]]:
    if identity.shape[0] != SOURCE_COUNT or identity.shape[1] != STATE_COUNT:
        raise ValueError("exact production SDF identity shape differs")
    one = identity[:, LEADING_PRODUCTION_STATE]
    half = identity[:, OPACITY_HALF_CONTROL_STATES[0]]
    discriminating = one != half
    spatial_shape = identity.shape[2:-1]
    spatial_samples = int(np.prod(spatial_shape))
    seen_one = np.zeros(spatial_shape, dtype=np.bool_)
    first_one = np.full(spatial_shape, -1, dtype=np.int16)
    transition_count = np.zeros(spatial_shape, dtype=np.uint16)
    invalid_spatial = np.zeros(spatial_shape, dtype=np.bool_)
    intermediate_values = 0
    spatial_conflicts = 0
    spatial_uncovered = 0
    reverse_spatial_states = 0
    endpoint_discriminating_values = int(np.count_nonzero(
        discriminating
    ))

    for threshold_index, state_index in enumerate(
        range(THRESHOLD_STATE_START, THRESHOLD_STATE_STOP)
    ):
        current = identity[:, state_index]
        equals_one = current == one
        equals_half = current == half
        intermediate_values += int(np.count_nonzero(
            discriminating & ~(equals_one | equals_half)
        ))
        intermediate_spatial = np.any(
            discriminating & ~(equals_one | equals_half),
            axis=(0, -1),
        )
        one_evidence = np.any(
            discriminating & equals_one,
            axis=(0, -1),
        )
        half_evidence = np.any(
            discriminating & equals_half,
            axis=(0, -1),
        )
        conflict = one_evidence & half_evidence
        uncovered = ~(one_evidence | half_evidence)
        spatial_conflicts += int(np.count_nonzero(conflict))
        spatial_uncovered += int(np.count_nonzero(uncovered))
        current_one = one_evidence & ~conflict
        current_half = half_evidence & ~conflict
        reverse_spatial_states += int(np.count_nonzero(
            seen_one & current_half
        ))
        invalid_spatial |= (
            conflict
            | uncovered
            | intermediate_spatial
            | (seen_one & current_half)
        )
        transition = current_one & ~seen_one
        first_one[transition] = threshold_index
        transition_count += transition
        seen_one |= current_one

    valid = (
        (first_one >= 0)
        & (transition_count == 1)
        & ~invalid_spatial
    )
    bits_catalog = np.asarray(LOWER_HALF_BITS, dtype=np.uint16)
    recovered = np.zeros(spatial_shape, dtype=np.uint16)
    recovered[valid] = bits_catalog[first_one[valid]]
    values = recovered[valid].view(np.float16)
    histogram = Counter(int(value) for value in recovered[valid])
    diagnostics = {
        "spatialSamples": spatial_samples,
        "endpointDiscriminatingValues":
            endpoint_discriminating_values,
        "intermediateDiscriminatingValues": intermediate_values,
        "spatialClassConflictsAcrossSourcesOrChannels":
            spatial_conflicts,
        "spatialUncoveredStateSamples": spatial_uncovered,
        "reverseSpatialStateSamples": reverse_spatial_states,
        "singleTransitionSpatialSamples":
            int(np.count_nonzero(valid)),
        "allThresholdValuesAreExactEndpoints":
            intermediate_values == 0,
        "allSpatialClassesConsistent":
            spatial_conflicts == 0 and spatial_uncovered == 0,
        "allSpatialSamplesRecovered":
            bool(np.all(valid)),
        "fieldHalfBitsMinimumNumeric":
            f"{int(recovered[valid].max(initial=0)):04x}",
        "fieldHalfBitsMaximumNumeric":
            f"{int(recovered[valid].min(initial=0xffff)):04x}",
        "fieldValueMinimum": float(values.min(initial=np.float16(np.inf))),
        "fieldValueMaximum": float(values.max(initial=np.float16(-np.inf))),
        "fieldHalfBitsHistogram": {
            f"{bits:04x}": count
            for bits, count in sorted(
                histogram.items(),
                reverse=True,
            )
        },
    }
    return diagnostics, recovered, valid


def _sample_coordinates() -> tuple[
    NDArray[np.float32],
    NDArray[np.float32],
]:
    offsets = np.arange(
        -(PATCH_SIDE // 2),
        PATCH_SIDE // 2 + 1,
        dtype=np.float32,
    )
    dy, dx = np.meshgrid(offsets, offsets, indexing="ij")
    x = np.empty((SITE_COUNT, PATCH_SIDE, PATCH_SIDE), dtype=np.float32)
    y = np.empty_like(x)
    for site in _expected_sites():
        x[site["index"]] = np.float32(site["x"]) + dx
        y[site["index"]] = np.float32(site["y"]) + dy
    return x, y


def analytic_circle_comparisons(
    recovered: UInt16Array,
    valid: NDArray[np.bool_],
) -> JsonObject:
    x, y = _sample_coordinates()
    records: JsonObject = {}
    for offset in (-0.5, 0.0, 0.5):
        dx = x + np.float32(offset) - np.float32(512)
        dy = y + np.float32(offset) - np.float32(512)
        radius = np.sqrt(
            dx * dx + dy * dy,
            dtype=np.float32,
        )
        prediction = (
            radius - np.float32(2000)
        ).astype(np.float16).view(np.uint16)
        changed = valid & (prediction != recovered)
        records[f"pixelOffset{offset:+.1f}"] = {
            "offset": offset,
            "validSamples": int(np.count_nonzero(valid)),
            "exactHalfWords": int(np.count_nonzero(
                valid & ~changed
            )),
            "exactHalfWordFraction": float(np.mean(
                prediction[valid] == recovered[valid]
            )),
            "changedHalfWords": int(np.count_nonzero(changed)),
            "allHalfWordsExact": not bool(np.any(changed)),
            "maximumAbsoluteHalfWordSteps": int(
                np.abs(
                    prediction[valid].astype(np.int32)
                    - recovered[valid].astype(np.int32)
                ).max(initial=0)
            ),
        }
    return records


def analyze(
    path: Path,
    *,
    map_output: Path | None = None,
    prior_distance_artifact: Path | None = None,
) -> JsonObject:
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(
        prefix="liquid-glass-production-sdf-exact-"
    ) as temporary:
        sweep = ProductionSdfExactSweep.open(
            path,
            scratch=Path(temporary),
        )
        source_fidelity = difference_metrics(
            sweep.control,
            expected_control_patches()[list(SOURCE_INDICES)],
        )
        production_repeat = difference_metrics(
            sweep.identity[:, LEADING_PRODUCTION_STATE],
            sweep.identity[:, TRAILING_PRODUCTION_STATE],
        )
        opacity_one_control = difference_metrics(
            sweep.identity[:, LEADING_PRODUCTION_STATE],
            sweep.identity[:, OPACITY_ONE_CONTROL_STATE],
        )
        opacity_half_control = difference_metrics(
            sweep.identity[:, OPACITY_HALF_CONTROL_STATES[0]],
            sweep.identity[:, OPACITY_HALF_CONTROL_STATES[1]],
        )
        endpoint_difference = difference_metrics(
            sweep.identity[:, LEADING_PRODUCTION_STATE],
            sweep.identity[:, OPACITY_HALF_CONTROL_STATES[0]],
        )
        cross_release: JsonObject | None = None
        if prior_distance_artifact is not None:
            prior_scratch = Path(temporary) / "prior-distance"
            prior_scratch.mkdir()
            prior = ProductionDistanceSweep.open(
                prior_distance_artifact,
                scratch=prior_scratch,
            )
            cross_release = {
                "priorArtifact": {
                    "path": str(prior_distance_artifact),
                    "sha256": sha256_file(prior_distance_artifact),
                    "ciCommit": prior.manifest["ciCommit"],
                    "osVersion": prior.manifest["osVersion"],
                },
                "currentOsVersion": sweep.manifest["osVersion"],
                "sourcePatternIndices": list(SOURCE_INDICES),
                "productionEndpoint": difference_metrics(
                    prior.identity[
                        list(SOURCE_INDICES),
                        PRIOR_PRODUCTION_STATE,
                    ],
                    sweep.identity[:, LEADING_PRODUCTION_STATE],
                ),
                "opacityOneControl": difference_metrics(
                    prior.identity[
                        list(SOURCE_INDICES),
                        PRIOR_ONE_STATES[0],
                    ],
                    sweep.identity[:, OPACITY_ONE_CONTROL_STATE],
                ),
                "opacityHalfControl": difference_metrics(
                    prior.identity[
                        list(SOURCE_INDICES),
                        PRIOR_HALF_STATES[0],
                    ],
                    sweep.identity[
                        :,
                        OPACITY_HALF_CONTROL_STATES[0],
                    ],
                ),
            }
            cross_release["allSharedEndpointsExact"] = all(
                cross_release[key]["exact"]
                for key in (
                    "productionEndpoint",
                    "opacityOneControl",
                    "opacityHalfControl",
                )
            )
        decode, recovered, valid = decode_sdf_half_words(
            sweep.identity
        )
        circle = analytic_circle_comparisons(recovered, valid)
        source = {
            "path": str(path),
            "sha256": sha256_file(path),
            "ciCommit": sweep.manifest["ciCommit"],
            "osVersion": sweep.manifest["osVersion"],
            "architecture": sweep.manifest["architecture"],
            "memberSha256": sweep.member_hashes,
        }
        map_record: JsonObject | None = None
        if map_output is not None:
            x, y = _sample_coordinates()
            np.savez_compressed(
                map_output,
                field_half_bits=recovered,
                field_values=recovered.view(np.float16),
                valid=valid,
                sample_x=x,
                sample_y=y,
                lower_half_bits=np.asarray(
                    LOWER_HALF_BITS,
                    dtype=np.uint16,
                ),
                source_pattern_indices=np.asarray(
                    SOURCE_INDICES,
                    dtype=np.uint8,
                ),
            )
            map_record = {
                "path": str(map_output),
                "sha256": sha256_file(map_output),
                "arrays": {
                    "field_half_bits": list(recovered.shape),
                    "field_values": list(recovered.shape),
                    "valid": list(valid.shape),
                    "sample_x": list(x.shape),
                    "sample_y": list(y.shape),
                    "lower_half_bits": [THRESHOLD_COUNT],
                    "source_pattern_indices": [SOURCE_COUNT],
                },
            }

    accepted = bool(
        source_fidelity["exact"]
        and production_repeat["exact"]
        and opacity_one_control["exact"]
        and opacity_half_control["exact"]
        and not endpoint_difference["exact"]
        and decode["allThresholdValuesAreExactEndpoints"]
        and decode["allSpatialClassesConsistent"]
        and decode["allSpatialSamplesRecovered"]
    )
    return {
        "liquidGlassProductionSdfExactAnalysisSchemaVersion": 1,
        "analysisImplementation": {
            "file":
                "analysis/liquid_glass_production_sdf_exact.py",
            "sha256": sha256_file(Path(__file__).resolve()),
            "python": platform.python_version(),
            "numpy": np.__version__,
        },
        "source": source,
        "controls": {
            "capturedSourceVsDeterministicGenerator":
                source_fidelity,
            "leadingVsTrailingProduction": production_repeat,
            "opacityOneEquivalentDistanceProfile":
                opacity_one_control,
            "opacityHalfEquivalentDistanceProfiles":
                opacity_half_control,
            "opacityOneVsOpacityHalfEndpoint":
                endpoint_difference,
        },
        "crossReleaseEndpointStability": cross_release,
        "exactTransitionDecode": decode,
        "analyticCircleComparisons": circle,
        "mapArtifact": map_record,
        "conclusion": {
            "distanceOnlyProductionResourceAccepted": accepted,
            "sampledSdfHalfWordsRecovered": accepted,
            "productionShaderAuthorized": False,
            "nextGate": (
                "replay the decoded coverage-raster and jump-flood "
                "SDF path on general-shape holdouts, then recover "
                "the production seven-tap source-pyramid uniforms "
                "and replay protected source holdouts"
                if accepted
                else "resolve the failed exact SDF control"
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
    parser.add_argument("--map-output", type=Path)
    parser.add_argument("--prior-distance-artifact", type=Path)
    arguments = parser.parse_args()
    encoded = json.dumps(
        analyze(
            arguments.artifact,
            map_output=arguments.map_output,
            prior_distance_artifact=
                arguments.prior_distance_artifact,
        ),
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
