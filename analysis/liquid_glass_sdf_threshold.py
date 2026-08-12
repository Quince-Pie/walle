#!/usr/bin/env python3
"""Recover Apple's sampled binary16 SDF from adjacent threshold probes."""

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
    SITE_COORDINATES,
    SITE_COUNT,
    float32_bits,
)
from liquid_glass_sdf_scale import (
    SCALE_HALF_BITS_MINIMUM,
    _air_profile_scale,
    _spatial_coordinates,
)


type JsonObject = dict[str, Any]
type BoolArray = NDArray[np.bool_]
type UInt8Array = NDArray[np.uint8]
type UInt16Array = NDArray[np.uint16]

EXPECTED_SCHEMA = 1
EXPECTED_RIG = "native-sdf-threshold-sweep-1.0.0"
EXPECTED_KIND = (
    "exhaustive-adjacent-binary16-distance-threshold-curve"
)
FIRST_LOWER_HALF_BITS = 0xDE41
LAST_LOWER_HALF_BITS = 0xDC3F
LOWER_HALF_BITS = np.arange(
    FIRST_LOWER_HALF_BITS,
    LAST_LOWER_HALF_BITS - 1,
    -1,
    dtype=np.uint16,
)
STATE_COUNT = int(LOWER_HALF_BITS.size)
CHANNELS = 3
RESOURCE_RADIUS = 4.0
SOURCE_PATTERN_INDEX = 0
SOURCE_TILE_SIDE = 64
CONTROL_MEMBER = "native-sdf-threshold-control-patches.rgb8"
IDENTITY_MEMBER = "native-sdf-threshold-identity-patches.rgb8"
ICC_MEMBER = "native-sdf-threshold-capture-colorspace.icc"
BLOCK_BYTES = 1024 * 1024


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(BLOCK_BYTES):
            digest.update(block)
    return digest.hexdigest()


def _expected_sites() -> list[JsonObject]:
    return [
        {
            "index": phase_y * 4 + phase_x,
            "x": x,
            "y": y,
            "reducedGridPhaseX": phase_x,
            "reducedGridPhaseY": phase_y,
            "observedReducedGridPhaseX": (x // 2) & 3,
            "observedReducedGridPhaseY": (y // 2) & 3,
        }
        for phase_y, y in enumerate(SITE_COORDINATES)
        for phase_x, x in enumerate(SITE_COORDINATES)
    ]


def _source_code(x: int, y: int, channel: int) -> int:
    value = (
        (x & (SOURCE_TILE_SIDE - 1))
        | ((y & (SOURCE_TILE_SIDE - 1)) << 6)
        | (channel << 12)
    )
    value ^= 0x9E37_79B9
    value = ((value ^ (value >> 16)) * 0x7FEB_352D) & 0xFFFF_FFFF
    value = ((value ^ (value >> 15)) * 0x846C_A68B) & 0xFFFF_FFFF
    value ^= value >> 16
    return 16 + value % 224


def _float16_from_bits(bits: int) -> np.float16:
    return np.asarray(bits, dtype=np.uint16).view(np.float16)[()]


def _expected_state(index: int, lower_bits: int) -> JsonObject:
    upper_bits = lower_bits - 1
    lower = np.float32(_float16_from_bits(lower_bits))
    upper = np.float32(_float16_from_bits(upper_bits))
    return {
        "index": index,
        "name": f"sdf-threshold-lower-{lower_bits:04x}",
        "resourceBlurRadius": RESOURCE_RADIUS,
        "resourceBlurRadiusFloat32Bits": "40800000",
        "lowerDistance": float(lower),
        "lowerDistanceFloat16Bits": f"{lower_bits:04x}",
        "lowerDistanceFloat32Bits": float32_bits(float(lower)),
        "upperDistance": float(upper),
        "upperDistanceFloat16Bits": f"{upper_bits:04x}",
        "upperDistanceFloat32Bits": float32_bits(float(upper)),
        "adjacentBinary16Breakpoints": True,
        "expectedAllBlurredEndpoint": index == 0,
        "expectedAllUnblurredEndpoint": index == STATE_COUNT - 1,
    }


def _expected_filter_values(lower_bits: int) -> JsonObject:
    lower = float(np.float32(_float16_from_bits(lower_bits)))
    upper = float(np.float32(_float16_from_bits(lower_bits - 1)))
    return {
        **IDENTITY_VALUES,
        "inputBlurOpacity0": 0,
        "inputBlurOpacity1": 1,
        "inputBlurOpacity2": 1,
        "inputBlurOpacity3": 1,
        "inputBlurOpacity4": 1,
        "inputBlurDistance0": lower,
        "inputBlurDistance1": upper,
        "inputBlurDistance2": 0,
        "inputBlurDistance3": 0,
        "inputBlurDistance4": 0,
        "inputInnerRefractionAmount": -60,
        "inputOuterRefractionAmount": 160,
        "inputRefractionOpacity": 0,
        "inputBlurRadius": RESOURCE_RADIUS,
    }


def _validate_manifest(manifest: JsonObject) -> list[JsonObject]:
    if (
        manifest.get("schemaVersion") != EXPECTED_SCHEMA
        or manifest.get("rigVersion") != EXPECTED_RIG
        or manifest.get("sweepKind") != EXPECTED_KIND
    ):
        raise ValueError("SDF threshold rig differs")

    source = manifest.get("sourceDesign")
    if not isinstance(source, dict):
        raise ValueError("SDF threshold source design is missing")
    if (
        source.get("kind") != "periodic-deterministic-hash-rgb"
        or source.get("sourcePatternIndex") != SOURCE_PATTERN_INDEX
        or source.get("tileWidthPixels") != SOURCE_TILE_SIDE
        or source.get("tileHeightPixels") != SOURCE_TILE_SIDE
        or source.get("channelCodeRangeInclusive") != [16, 239]
        or source.get("alphaCode") != 255
        or source.get("patchRadiusPixels") != 40
        or source.get("patchSidePixels") != PATCH_SIDE
        or source.get("reducedGridPixelSizeSourcePixels") != 2
        or source.get("phasePeriodReducedGridPixels") != 4
        or source.get("sites") != _expected_sites()
    ):
        raise ValueError("SDF threshold source design differs")

    design = manifest.get("lodDesign")
    expected_states = [
        _expected_state(index, int(bits))
        for index, bits in enumerate(LOWER_HALF_BITS)
    ]
    if not isinstance(design, dict):
        raise ValueError("SDF threshold design is missing")
    if (
        design.get("states") != expected_states
        or design.get("resourceBlurRadius") != RESOURCE_RADIUS
        or design.get(
            "lowerDistanceFloat16BitsTraversalInclusive"
        ) != ["de41", "dc3f"]
        or design.get("lowerDistanceTraversal")
        != "strictly increasing numeric value"
        or design.get("upperDistance")
        != "next greater finite binary16 value"
        or design.get("expectedSdfFloat16BitsRangeInclusive")
        != ["de40", "dc40"]
        or design.get("activeInteriorOpacityIndices") != [0, 1]
        or design.get("blurOpacities") != [0, 1, 1, 1, 1]
        or design.get("fixedTrailingBlurDistances") != [0, 0, 0]
    ):
        raise ValueError("SDF threshold state design differs")

    expected_inputs = {
        "inputBlurRadius": RESOURCE_RADIUS,
        "inputBlurOpacity0Through4": [0, 1, 1, 1, 1],
        "inputBlurDistance0":
            "enumerates lower binary16 breakpoint "
            "from 0xde41 through 0xdc3f",
        "inputBlurDistance1":
            "next greater finite binary16 value",
        "inputBlurDistance2Through4": [0, 0, 0],
        "inputInnerRefractionAmount": -60,
        "inputOuterRefractionAmount": 160,
        "inputRefractionOpacity": 0,
    }
    if manifest.get("sdfThresholdInputs") != expected_inputs:
        raise ValueError("SDF threshold fixed inputs differ")
    for marker in (
        "flatBlurProfileInputs",
        "fixedResourceInputs",
        "sdfScaleInputs",
        "pinnedSdfScaleInputs",
    ):
        if manifest.get(marker) is not None:
            raise ValueError(f"SDF threshold unrelated marker differs: {marker}")

    captures = manifest.get("captures")
    if not isinstance(captures, list) or len(captures) != 1:
        raise ValueError("SDF threshold capture catalog differs")
    capture = captures[0]
    if (
        capture.get("sourcePatternIndex") != SOURCE_PATTERN_INDEX
        or int(capture.get("controlStabilitySamples", 0)) < 2
        or int(capture.get("materializedStabilitySamples", 0)) < 2
        or capture.get("captureBackend")
        != "CGWindowListCreateImage"
    ):
        raise ValueError("SDF threshold source capture differs")
    records = capture.get("states")
    if not isinstance(records, list) or len(records) != STATE_COUNT:
        raise ValueError("SDF threshold capture states differ")
    for expected, record, lower_bits_value in zip(
        expected_states,
        records,
        LOWER_HALF_BITS,
        strict=True,
    ):
        values = _expected_filter_values(int(lower_bits_value))
        bits = {
            key: float32_bits(float(value))
            for key, value in values.items()
            if not isinstance(value, bool)
        }
        if (
            any(
                record.get(key) != value
                for key, value in expected.items()
            )
            or record.get("inputReadbacks") != values
            or record.get("inputReadbackFloat32Bits") != bits
            or record.get("readbackBlurRadius") != RESOURCE_RADIUS
            or record.get("readbackBlurRadiusFloat32Bits")
            != "40800000"
            or int(record.get("stabilitySamples", 0)) < 2
            or record.get("captureBackend")
            != "CGWindowListCreateImage"
        ):
            raise ValueError(
                "SDF threshold readback differs at "
                f"{expected['name']}"
            )
    return expected_states


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
class SdfThresholdSweep:
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
    ) -> "SdfThresholdSweep":
        if not zipfile.is_zipfile(path):
            raise ValueError("SDF threshold artifact is not a ZIP archive")
        with zipfile.ZipFile(path) as archive:
            bad_member = archive.testzip()
            if bad_member is not None:
                raise ValueError(
                    f"SDF threshold CRC failed: {bad_member}"
                )
            try:
                manifest = json.load(archive.open("manifest.json"))
            except KeyError as error:
                raise ValueError(
                    "SDF threshold manifest is missing"
                ) from error
            _validate_manifest(manifest)
            evidence = manifest.get("nativeCaptureEvidence")
            if not isinstance(evidence, dict):
                raise ValueError(
                    "SDF threshold native evidence is missing"
                )
            expected_control_bytes = (
                SITE_COUNT * PATCH_SIDE**2 * CHANNELS
            )
            expected_identity_bytes = (
                STATE_COUNT * expected_control_bytes
            )
            expected_evidence = {
                "recordFormat": "RGB8",
                "recordStrideBytes": CHANNELS,
                "recordCount":
                    STATE_COUNT * SITE_COUNT * PATCH_SIDE**2,
                "file": IDENTITY_MEMBER,
                "fileBytes": expected_identity_bytes,
                "controlRecordCount":
                    SITE_COUNT * PATCH_SIDE**2,
                "controlFile": CONTROL_MEMBER,
                "controlFileBytes": expected_control_bytes,
            }
            if any(
                evidence.get(key) != value
                for key, value in expected_evidence.items()
            ):
                raise ValueError(
                    "SDF threshold native stream metadata differs"
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
                control_size != expected_control_bytes
                or identity_size != expected_identity_bytes
                or evidence.get("controlFileSha256") != control_hash
                or evidence.get("fileSha256") != identity_hash
            ):
                raise ValueError(
                    "SDF threshold native stream digest differs"
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
                        "SDF threshold ICC digest differs"
                    )
                member_hashes[ICC_MEMBER] = icc_hash

        control = np.memmap(
            control_path,
            dtype=np.uint8,
            mode="r",
            shape=(
                SITE_COUNT,
                PATCH_SIDE,
                PATCH_SIDE,
                CHANNELS,
            ),
        )
        identity = np.memmap(
            identity_path,
            dtype=np.uint8,
            mode="r",
            shape=(
                STATE_COUNT,
                SITE_COUNT,
                PATCH_SIDE,
                PATCH_SIDE,
                CHANNELS,
            ),
        )
        return cls(
            manifest=manifest,
            control=control,
            identity=identity,
            member_hashes=member_hashes,
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class TransitionDecode:
    field_half_bits: UInt16Array
    transition_index: UInt16Array
    endpoint_discriminating: BoolArray
    binary_monotonic: BoolArray
    intermediate_state_count: UInt16Array
    endpoint_change_count: UInt16Array


def _decode_transitions(
    identity: UInt8Array,
    lower_half_bits: UInt16Array,
) -> TransitionDecode:
    if (
        identity.ndim < 3
        or identity.shape[0] != lower_half_bits.size
        or identity.shape[-1] != CHANNELS
    ):
        raise ValueError("SDF threshold curve shape differs")
    spatial_shape = identity.shape[1:-1]
    curve = identity.reshape(identity.shape[0], -1, CHANNELS)
    first = curve[0]
    last = curve[-1]
    endpoint_discriminating = np.any(first != last, axis=-1)
    equals_first = np.all(curve == first[np.newaxis], axis=-1)
    equals_last = np.all(curve == last[np.newaxis], axis=-1)
    intermediate = ~(equals_first | equals_last)
    intermediate_count = np.count_nonzero(
        intermediate,
        axis=0,
    ).astype(np.uint16)
    endpoint_change_count = np.count_nonzero(
        equals_first[1:] != equals_first[:-1],
        axis=0,
    ).astype(np.uint16)
    binary_monotonic = (
        endpoint_discriminating
        & equals_first[0]
        & equals_last[-1]
        & (intermediate_count == 0)
        & (endpoint_change_count == 1)
    )
    transition_index = np.argmax(
        equals_last,
        axis=0,
    ).astype(np.uint16)
    field_half_bits = np.full(
        transition_index.shape,
        np.uint16(0xFFFF),
    )
    field_half_bits[binary_monotonic] = lower_half_bits[
        transition_index[binary_monotonic]
    ]
    return TransitionDecode(
        field_half_bits=field_half_bits.reshape(spatial_shape),
        transition_index=transition_index.reshape(spatial_shape),
        endpoint_discriminating=endpoint_discriminating.reshape(
            spatial_shape
        ),
        binary_monotonic=binary_monotonic.reshape(spatial_shape),
        intermediate_state_count=intermediate_count.reshape(
            spatial_shape
        ),
        endpoint_change_count=endpoint_change_count.reshape(
            spatial_shape
        ),
    )


def _transition_report(decode: TransitionDecode) -> JsonObject:
    valid = decode.binary_monotonic
    bits = decode.field_half_bits[valid]
    histogram = np.bincount(
        bits.astype(np.int64),
        minlength=0x10000,
    )
    values = bits.view(np.float16)
    return {
        "spatialSamples": int(valid.size),
        "endpointDiscriminatingSamples": int(
            np.count_nonzero(decode.endpoint_discriminating)
        ),
        "binaryMonotonicSamples": int(np.count_nonzero(valid)),
        "binaryMonotonicFraction": float(np.mean(valid)),
        "allSamplesBinaryMonotonic": bool(np.all(valid)),
        "samplesWithIntermediateResponses": int(
            np.count_nonzero(decode.intermediate_state_count)
        ),
        "samplesWithWrongEndpointChangeCount": int(
            np.count_nonzero(decode.endpoint_change_count != 1)
        ),
        "fieldHalfBitsMinimumNumeric": (
            f"{int(bits[np.argmin(values)]):04x}"
            if bits.size
            else None
        ),
        "fieldHalfBitsMaximumNumeric": (
            f"{int(bits[np.argmax(values)]):04x}"
            if bits.size
            else None
        ),
        "fieldValueMinimum": (
            float(np.min(values))
            if values.size
            else None
        ),
        "fieldValueMaximum": (
            float(np.max(values))
            if values.size
            else None
        ),
        "fieldHalfBitsHistogram": {
            f"{index:04x}": int(count)
            for index, count in enumerate(histogram)
            if count
        },
    }


def _analytic_circle_report(
    decode: TransitionDecode,
    source_design: JsonObject,
    glass_shape: JsonObject,
    *,
    offset: float,
) -> JsonObject:
    x, y = _spatial_coordinates(source_design, offset=offset)
    center_x = float(glass_shape["centerX"])
    center_y = float(glass_shape["centerY"])
    radius = float(glass_shape["diameter"]) / 2
    radial_distance = np.sqrt(
        np.square(x - center_x)
        + np.square(y - center_y)
    )
    sdf = np.asarray(
        -400 + radial_distance * (400 / radius),
        dtype=np.float32,
    )
    predicted = sdf.astype(np.float16).view(np.uint16)
    measured = decode.field_half_bits.reshape(-1)
    valid = decode.binary_monotonic.reshape(-1)
    difference = (
        measured[valid].astype(np.int32)
        - predicted[valid].astype(np.int32)
    )
    exact = difference == 0
    return {
        "offset": offset,
        "validSamples": int(np.count_nonzero(valid)),
        "exactHalfWords": int(np.count_nonzero(exact)),
        "exactHalfWordFraction": (
            float(np.mean(exact))
            if exact.size
            else None
        ),
        "allHalfWordsExact": bool(exact.size and np.all(exact)),
        "maximumAbsoluteHalfWordSteps": (
            int(np.max(np.abs(difference), initial=0))
        ),
        "signedHalfWordStepHistogram": {
            str(int(step)): int(count)
            for step, count in zip(
                *np.unique(difference, return_counts=True),
                strict=True,
            )
        },
    }


def _pinned_candidate_report(
    decode: TransitionDecode,
    pinned_map_path: Path,
) -> JsonObject:
    with np.load(pinned_map_path) as maps:
        lower = maps["candidate_lower"]
        upper = maps["candidate_upper"]
    if lower.shape != decode.field_half_bits.shape or upper.shape != lower.shape:
        raise ValueError("pinned SDF candidate map shape differs")
    valid = decode.binary_monotonic
    field = decode.field_half_bits[valid]
    sdf = field.view(np.float16).astype(np.float32)
    profile_bits = _air_profile_scale(sdf)
    state = (
        profile_bits.astype(np.int32)
        - SCALE_HALF_BITS_MINIMUM
    )
    accepted = (
        (state >= lower[valid].astype(np.int32))
        & (state <= upper[valid].astype(np.int32))
    )
    return {
        "path": str(pinned_map_path),
        "sha256": sha256_file(pinned_map_path),
        "validSamples": int(accepted.size),
        "acceptedSamples": int(np.count_nonzero(accepted)),
        "acceptedFraction": (
            float(np.mean(accepted))
            if accepted.size
            else None
        ),
        "allAccepted": bool(accepted.size and np.all(accepted)),
    }


def _write_maps(
    path: Path,
    decode: TransitionDecode,
) -> JsonObject:
    with path.open("wb") as stream:
        np.savez_compressed(
            stream,
            field_half_bits=decode.field_half_bits,
            transition_index=decode.transition_index,
            endpoint_discriminating=
                decode.endpoint_discriminating,
            binary_monotonic=decode.binary_monotonic,
            intermediate_state_count=
                decode.intermediate_state_count,
            endpoint_change_count=
                decode.endpoint_change_count,
        )
    return {
        "path": str(path),
        "sha256": sha256_file(path),
        "spatialShape": list(decode.field_half_bits.shape),
        "invalidFieldHalfBitsSentinel": "ffff",
        "arrays": [
            "binary_monotonic",
            "endpoint_change_count",
            "endpoint_discriminating",
            "field_half_bits",
            "intermediate_state_count",
            "transition_index",
        ],
    }


def analyze(
    threshold_path: Path,
    *,
    pinned_map_path: Path | None = None,
    map_output: Path | None = None,
) -> JsonObject:
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(
        prefix="liquid-glass-sdf-threshold-"
    ) as temporary:
        sweep = SdfThresholdSweep.open(
            threshold_path,
            scratch=Path(temporary),
        )
        decode = _decode_transitions(
            sweep.identity,
            LOWER_HALF_BITS,
        )
        transition = _transition_report(decode)
        analytic = {
            name: _analytic_circle_report(
                decode,
                sweep.manifest["sourceDesign"],
                sweep.manifest["glassShape"],
                offset=offset,
            )
            for name, offset in (
                ("offset_minus_half", -0.5),
                ("offset_zero", 0.0),
                ("offset_plus_half", 0.5),
            )
        }
        pinned = (
            _pinned_candidate_report(
                decode,
                pinned_map_path,
            )
            if pinned_map_path is not None
            else None
        )
        map_record = (
            _write_maps(map_output, decode)
            if map_output is not None
            else None
        )
        endpoints = {
            "blurredVsUnblurred":
                difference_metrics(
                    sweep.identity[0],
                    sweep.identity[-1],
                ),
            "controlVsBlurred":
                difference_metrics(
                    sweep.control,
                    sweep.identity[0],
                ),
            "controlVsUnblurred":
                difference_metrics(
                    sweep.control,
                    sweep.identity[-1],
                ),
        }
        source = {
            "path": str(threshold_path),
            "sha256": sha256_file(threshold_path),
            "ciCommit": sweep.manifest["ciCommit"],
            "osVersion": sweep.manifest["osVersion"],
            "architecture": sweep.manifest["architecture"],
            "memberSha256": sweep.member_hashes,
        }

    transition_exact = bool(
        transition["allSamplesBinaryMonotonic"]
    )
    pinned_exact = bool(
        pinned is not None and pinned["allAccepted"]
    )
    return {
        "liquidGlassSdfThresholdAnalysisSchemaVersion": 1,
        "analysisImplementation": {
            "file": "analysis/liquid_glass_sdf_threshold.py",
            "sha256": sha256_file(Path(__file__).resolve()),
            "python": platform.python_version(),
            "numpy": np.__version__,
        },
        "source": source,
        "thresholdDesign": {
            "states": STATE_COUNT,
            "lowerHalfBitsTraversalInclusive": ["de41", "dc3f"],
            "firstLowerDistance": float(
                _float16_from_bits(FIRST_LOWER_HALF_BITS)
            ),
            "lastLowerDistance": float(
                _float16_from_bits(LAST_LOWER_HALF_BITS)
            ),
            "classification":
                "The first state is the radius-four endpoint. "
                "At the first state whose lower breakpoint is at "
                "least the sampled binary16 SDF, the response "
                "switches to the radius-zero endpoint.",
        },
        "endpointMeasurements": endpoints,
        "exactTransitionDecode": transition,
        "analyticCircleComparisons": analytic,
        "pinnedCatalogConsistency": pinned,
        "mapArtifact": map_record,
        "conclusion": {
            "sampledSdfHalfWordsRecovered": transition_exact,
            "pinnedCatalogConsistent": pinned_exact,
            "sdfStageComplete": bool(
                transition_exact and pinned_exact
            ),
            "productionShaderAuthorized": False,
            "requiredGate":
                "every curve binary and monotonic, every recovered "
                "half word accepted by the protected pinned catalog",
            "remainingProductionGate":
                "exact radius-one source-pyramid taps and zero "
                "unequal channels on protected Apple captures",
        },
        "resourceMeasurements": {
            "analysisSeconds": time.perf_counter() - started,
            "maximumResidentSetKiB":
                resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
    )
    parser.add_argument("threshold_artifact", type=Path)
    parser.add_argument("--pinned-map", type=Path)
    parser.add_argument("--map-output", type=Path)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    report = analyze(
        arguments.threshold_artifact,
        pinned_map_path=arguments.pinned_map,
        map_output=arguments.map_output,
    )
    encoded = json.dumps(
        report,
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
