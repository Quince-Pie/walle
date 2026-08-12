#!/usr/bin/env python3
"""Validate and solve the native Liquid Glass LOD sweep."""

import argparse
import hashlib
import json
import platform
import resource
import struct
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from liquid_glass_sampler_probe import SamplerProbe


type JsonObject = dict[str, Any]
type UInt8Array = NDArray[np.uint8]

EXPECTED_RIG = "native-lod-sweep-1.0.0"
FLAT_RIG = "native-flat-lod-sweep-1.0.0"
EXPECTED_SCHEMA = 1
AMPLITUDES = (0, 1, 8, 32, 127)
BIN_COUNT = 129
PRODUCTION_STATE_INDEX = BIN_COUNT
STATE_COUNT = BIN_COUNT + 1
SITE_COORDINATES = (112, 338, 564, 790)
SITE_COUNT = 16
PATCH_RADIUS = 40
PATCH_SIDE = 2 * PATCH_RADIUS + 1
SQUARE_SIDE = 96
CHANNELS = 3
SOURCE_CODE = 128
CHANNEL_SIGNS = np.asarray((1, -1, 1), dtype=np.int16)
IDENTITY_VALUES: JsonObject = {
    "inputFaceColorMatrixBlack": 0,
    "inputFaceColorMatrixSaturation": 1,
    "inputFaceColorMatrixWhite": 1,
    "inputSDRHoldingToneEnabled": False,
}
FLAT_BLUR_VALUES: JsonObject = {
    "inputBlurOpacity0": 1,
    "inputBlurOpacity1": 1,
    "inputBlurOpacity2": 1,
    "inputBlurOpacity3": 1,
    "inputBlurOpacity4": 1,
    "inputInnerRefractionAmount": 0,
    "inputOuterRefractionAmount": 0,
    "inputRefractionOpacity": 0,
}


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def float32_bits(value: float) -> str:
    bits = struct.unpack("<I", struct.pack("<f", value))[0]
    return f"{bits:08x}"


def _expected_blur_radius(numerator: int) -> np.float32:
    if numerator == 0:
        return np.float32(0)
    if numerator == 64:
        return np.float32(2)
    if numerator == 128:
        return np.float32(4)
    centered_lod = (numerator + 0.25) / 64
    if numerator < 64:
        return np.float32(2 * (2**centered_lod - 1))
    return np.float32(2**centered_lod)


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


def _rig_configuration(manifest: JsonObject) -> bool:
    rig = manifest.get("rigVersion")
    kind = manifest.get("sweepKind")
    if (
        rig == EXPECTED_RIG
        and kind == "deep-interior-phase-controlled-lod-curve"
    ):
        if manifest.get("flatBlurProfileInputs") not in (None,):
            raise ValueError("default native LOD profile marker differs")
        return False
    if (
        rig == FLAT_RIG
        and kind == "flat-blur-profile-phase-controlled-lod-curve"
    ):
        if manifest.get("flatBlurProfileInputs") != FLAT_BLUR_VALUES:
            raise ValueError("flat native LOD profile inputs differ")
        return True
    raise ValueError(f"unexpected native LOD rig/kind: {rig!r}/{kind!r}")


def _expected_filter_values(
    radius: float,
    *,
    flat_profile: bool,
) -> JsonObject:
    values = dict(IDENTITY_VALUES)
    if flat_profile:
        values.update(FLAT_BLUR_VALUES)
    values["inputBlurRadius"] = radius
    return values


def _validate_states(manifest: JsonObject) -> list[JsonObject]:
    design = manifest.get("lodDesign")
    if not isinstance(design, dict):
        raise ValueError("native LOD design is missing")
    if (
        design.get("quantizedFractionDenominator") != 64
        or design.get("productionState") != "production-blur-1"
    ):
        raise ValueError("native LOD quantization design differs")
    states = design.get("states")
    if not isinstance(states, list) or len(states) != STATE_COUNT:
        raise ValueError("native LOD state catalog differs")
    for numerator, state in enumerate(states[:BIN_COUNT]):
        expected_radius = _expected_blur_radius(numerator)
        if (
            state.get("index") != numerator
            or state.get("name") != f"lod-bin-{numerator:03d}"
            or state.get("targetLodNumerator") != numerator
            or state.get("targetLodDenominator") != 64
            or state.get("productionRadius") is not False
            or state.get("requestedBlurRadiusFloat32Bits")
            != float32_bits(float(expected_radius))
        ):
            raise ValueError(
                f"native LOD state differs at bin {numerator}"
            )
    production = states[PRODUCTION_STATE_INDEX]
    if (
        production.get("index") != PRODUCTION_STATE_INDEX
        or production.get("name") != "production-blur-1"
        or production.get("targetLodNumerator") != 37
        or production.get("targetLodDenominator") != 64
        or production.get("productionRadius") is not True
        or production.get("requestedBlurRadiusFloat32Bits")
        != "3f800000"
    ):
        raise ValueError("native production LOD state differs")
    return states


def _validate_catalog(
    manifest: JsonObject,
) -> list[JsonObject]:
    flat_profile = _rig_configuration(manifest)
    if manifest.get("schemaVersion") != EXPECTED_SCHEMA:
        raise ValueError("native LOD manifest schema differs")
    source = manifest.get("sourceDesign")
    if not isinstance(source, dict):
        raise ValueError("native LOD source design is missing")
    if (
        tuple(source.get("amplitudesCodes", ())) != AMPLITUDES
        or source.get("baseCode") != SOURCE_CODE
        or source.get("squareWidth") != SQUARE_SIDE
        or source.get("squareHeight") != SQUARE_SIDE
        or source.get("patchRadiusPixels") != PATCH_RADIUS
        or source.get("patchSidePixels") != PATCH_SIDE
        or source.get("reducedGridPixelSizeSourcePixels") != 2
        or source.get("phasePeriodReducedGridPixels") != 4
        or source.get("channelSigns")
        != {"red": 1, "green": -1, "blue": 1}
        or source.get("sites") != _expected_sites()
    ):
        raise ValueError("native LOD source geometry differs")
    states = _validate_states(manifest)
    captures = manifest.get("captures")
    if (
        not isinstance(captures, list)
        or len(captures) != len(AMPLITUDES)
    ):
        raise ValueError("native LOD capture catalog differs")
    for amplitude, capture in zip(
        AMPLITUDES,
        captures,
        strict=True,
    ):
        if (
            capture.get("amplitudeCodes") != amplitude
            or int(capture.get("controlStabilitySamples", 0)) < 2
            or int(
                capture.get("materializedStabilitySamples", 0)
            ) < 2
        ):
            raise ValueError(
                f"native LOD capture differs at amplitude {amplitude}"
            )
        records = capture.get("states")
        if not isinstance(records, list) or len(records) != STATE_COUNT:
            raise ValueError(
                f"native LOD state records differ at {amplitude}"
            )
        for expected, record in zip(
            states,
            records,
            strict=True,
        ):
            expected_radius = float(
                np.float32(record.get("requestedBlurRadius"))
            )
            if (
                record.get("index") != expected["index"]
                or record.get("name") != expected["name"]
                or record.get("targetLodNumerator")
                != expected["targetLodNumerator"]
                or record.get("requestedBlurRadiusFloat32Bits")
                != expected["requestedBlurRadiusFloat32Bits"]
                or record.get("readbackBlurRadiusFloat32Bits")
                != expected["requestedBlurRadiusFloat32Bits"]
                or int(record.get("stabilitySamples", 0)) < 2
                or record.get("captureBackend")
                != "CGWindowListCreateImage"
            ):
                raise ValueError(
                    "native LOD state readback or stability differs "
                    f"at amplitude {amplitude}, "
                    f"state {expected['name']}"
                )
            if flat_profile:
                expected_values = _expected_filter_values(
                    expected_radius,
                    flat_profile=True,
                )
                expected_bits = {
                    key: float32_bits(float(value))
                    for key, value in expected_values.items()
                    if not isinstance(value, bool)
                }
                if (
                    record.get("inputReadbacks") != expected_values
                    or record.get("inputReadbackFloat32Bits")
                    != expected_bits
                ):
                    raise ValueError(
                        "flat native LOD full readback differs "
                        f"at amplitude {amplitude}, "
                        f"state {expected['name']}"
                    )
    return states


@dataclass(frozen=True, slots=True)
class LodSweep:
    manifest: JsonObject
    control: UInt8Array
    identity: UInt8Array
    member_hashes: dict[str, str]

    @classmethod
    def open(cls, path: Path) -> "LodSweep":
        if path.is_dir():
            manifest = json.loads(
                (path / "manifest.json").read_text(encoding="utf-8")
            )

            def read(name: str) -> bytes:
                return (path / name).read_bytes()

        else:
            with zipfile.ZipFile(path) as archive:
                names = archive.namelist()
                if len(names) != len(set(names)):
                    raise ValueError(
                        "native LOD archive has duplicate members"
                    )
                try:
                    manifest = json.loads(
                        archive.read("manifest.json")
                    )
                except KeyError as error:
                    raise ValueError(
                        "native LOD manifest is missing"
                    ) from error
                members = {
                    name: archive.read(name)
                    for name in names
                    if name.startswith("native-lod-")
                    or name.startswith("native-flat-lod-")
                }

            def read(name: str) -> bytes:
                try:
                    return members[name]
                except KeyError as error:
                    raise ValueError(
                        f"native LOD member is missing: {name}"
                    ) from error

        _validate_catalog(manifest)
        evidence = manifest.get("nativeCaptureEvidence")
        if not isinstance(evidence, dict):
            raise ValueError("native LOD evidence is missing")
        control_records = (
            len(AMPLITUDES) * SITE_COUNT * PATCH_SIDE**2
        )
        identity_records = control_records * STATE_COUNT
        if (
            evidence.get("schemaVersion") != EXPECTED_SCHEMA
            or evidence.get("recordFormat") != "RGB8"
            or evidence.get("recordStrideBytes") != CHANNELS
            or evidence.get("recordCount") != identity_records
            or evidence.get("controlRecordCount") != control_records
        ):
            raise ValueError("native LOD evidence layout differs")
        member_hashes: dict[str, str] = {}

        def checked(
            *,
            file_key: str,
            bytes_key: str,
            hash_key: str,
            expected_bytes: int,
        ) -> bytes:
            name = str(evidence[file_key])
            value = read(name)
            if (
                len(value) != expected_bytes
                or evidence.get(bytes_key) != expected_bytes
            ):
                raise ValueError(
                    f"native LOD stream length differs: {name}"
                )
            digest = sha256_bytes(value)
            if digest != evidence.get(hash_key):
                raise ValueError(
                    f"native LOD stream hash differs: {name}"
                )
            member_hashes[name] = digest
            return value

        control_bytes = checked(
            file_key="controlFile",
            bytes_key="controlFileBytes",
            hash_key="controlFileSha256",
            expected_bytes=control_records * CHANNELS,
        )
        identity_bytes = checked(
            file_key="file",
            bytes_key="fileBytes",
            hash_key="fileSha256",
            expected_bytes=identity_records * CHANNELS,
        )
        if icc_name := evidence.get("iccFile"):
            icc = read(str(icc_name))
            if (
                len(icc) != evidence.get("iccFileBytes")
                or sha256_bytes(icc) != evidence.get("iccFileSha256")
            ):
                raise ValueError("native LOD ICC evidence differs")
            member_hashes[str(icc_name)] = sha256_bytes(icc)
        control = np.frombuffer(
            control_bytes,
            dtype=np.uint8,
        ).reshape(
            len(AMPLITUDES),
            SITE_COUNT,
            PATCH_SIDE,
            PATCH_SIDE,
            CHANNELS,
        )
        identity = np.frombuffer(
            identity_bytes,
            dtype=np.uint8,
        ).reshape(
            len(AMPLITUDES),
            STATE_COUNT,
            SITE_COUNT,
            PATCH_SIDE,
            PATCH_SIDE,
            CHANNELS,
        )
        return cls(
            manifest=manifest,
            control=control,
            identity=identity,
            member_hashes=member_hashes,
        )


def source_fidelity(sweep: LodSweep) -> JsonObject:
    expected = np.full(
        sweep.control.shape,
        SOURCE_CODE,
        dtype=np.uint8,
    )
    center = PATCH_RADIUS
    for amplitude_index, amplitude in enumerate(AMPLITUDES):
        expected[
            amplitude_index,
            :,
            center:,
            center:,
            0,
        ] = SOURCE_CODE + amplitude
        expected[
            amplitude_index,
            :,
            center:,
            center:,
            1,
        ] = SOURCE_CODE - amplitude
        expected[
            amplitude_index,
            :,
            center:,
            center:,
            2,
        ] = SOURCE_CODE + amplitude
    changed = expected != sweep.control
    changed_pixels = np.any(changed, axis=-1)
    distance = np.abs(
        expected.astype(np.int16)
        - sweep.control.astype(np.int16)
    )
    return {
        "values": int(changed.size),
        "changedValues": int(np.count_nonzero(changed)),
        "exactValueFraction": float(np.mean(~changed)),
        "pixels": int(changed_pixels.size),
        "changedPixels": int(np.count_nonzero(changed_pixels)),
        "exactPixelFraction": float(np.mean(~changed_pixels)),
        "maximumAbsoluteCodes": int(distance.max(initial=0)),
        "exact": not bool(np.any(changed)),
    }


def difference_metrics(
    predicted: UInt8Array,
    actual: UInt8Array,
) -> JsonObject:
    if predicted.shape != actual.shape:
        raise ValueError("native LOD comparison shapes differ")
    changed = predicted != actual
    changed_pixels = np.any(changed, axis=-1)
    distance = np.abs(
        predicted.astype(np.int16) - actual.astype(np.int16)
    )
    return {
        "values": int(changed.size),
        "changedValues": int(np.count_nonzero(changed)),
        "exactValueFraction": float(np.mean(~changed)),
        "pixels": int(changed_pixels.size),
        "changedPixels": int(np.count_nonzero(changed_pixels)),
        "exactPixelFraction": float(np.mean(~changed_pixels)),
        "maximumAbsoluteCodes": int(distance.max(initial=0)),
    }


def _sampler_output_preimages() -> tuple[
    NDArray[np.float64],
    NDArray[np.float64],
]:
    fixed_indices = np.arange(16 * 255 + 1, dtype=np.int32)
    half_values = (
        fixed_indices.astype(np.float64) / (16 * 255)
    ).astype(np.float16)
    output_codes = np.clip(
        np.rint(
            half_values.astype(np.float32) * np.float32(255)
        ),
        0,
        255,
    ).astype(np.uint8)
    minimum = np.empty(256, dtype=np.float64)
    maximum = np.empty(256, dtype=np.float64)
    for code in range(256):
        selected = fixed_indices[output_codes == code]
        if not selected.size:
            raise AssertionError(
                f"output code {code} has no sampler preimage"
            )
        minimum[code] = max(0, (selected.min() - 0.5) / 16)
        maximum[code] = min(
            255,
            (selected.max() + 0.5) / 16,
        )
    return minimum, maximum


def affine_segment_feasibility(
    sequences: UInt8Array,
    fractions: NDArray[np.float64] | None = None,
) -> JsonObject:
    if sequences.ndim != 2 or sequences.shape[1] != 65:
        raise ValueError("LOD affine sequences must have 65 bins")
    if fractions is None:
        fractions = np.arange(65, dtype=np.float64) / 64
    else:
        fractions = np.asarray(fractions, dtype=np.float64)
        if (
            fractions.shape != (65,)
            or not np.all(np.diff(fractions) > 0)
        ):
            raise ValueError(
                "LOD affine fractions must increase across 65 bins"
            )
    unique, counts = np.unique(
        sequences,
        axis=0,
        return_counts=True,
    )
    code_minimum, code_maximum = _sampler_output_preimages()
    lower_values = code_minimum[unique]
    upper_values = code_maximum[unique]
    slope_minimum = np.full(unique.shape[0], -np.inf)
    slope_maximum = np.full(unique.shape[0], np.inf)
    for first in range(64):
        for second in range(first + 1, 65):
            fraction_distance = (
                fractions[second] - fractions[first]
            )
            slope_minimum = np.maximum(
                slope_minimum,
                (
                    lower_values[:, second]
                    - upper_values[:, first]
                ) / fraction_distance,
            )
            slope_maximum = np.minimum(
                slope_maximum,
                (
                    upper_values[:, second]
                    - lower_values[:, first]
                ) / fraction_distance,
            )
    tolerance = np.finfo(np.float64).eps * 1024
    feasible = slope_minimum <= slope_maximum + tolerance
    incompatible_occurrences = int(counts[~feasible].sum())
    maximum_bound_conflict = float(
        np.maximum(
            slope_minimum - slope_maximum,
            0,
        ).max(initial=0)
    )
    return {
        "sequences": int(sequences.shape[0]),
        "uniqueSequences": int(unique.shape[0]),
        "incompatibleUniqueSequences": int(
            np.count_nonzero(~feasible)
        ),
        "incompatibleSequenceOccurrences":
            incompatible_occurrences,
        "compatibleSequenceFraction": (
            1 - incompatible_occurrences / sequences.shape[0]
        ),
        "maximumSlopeBoundConflictCodes":
            maximum_bound_conflict,
        "allCompatible": bool(np.all(feasible)),
        "model": (
            "there exist latent unrounded mip endpoint code "
            "values whose exact affine blends at the supplied "
            "fractions, followed by "
            "one fixed-sixteenth-code ties-up sampler round and "
            "binary16 conversion, produce every captured code"
        ),
        "fractionMinimum": float(fractions[0]),
        "fractionMaximum": float(fractions[-1]),
    }


def signed_channel_diagnostics(stream: UInt8Array) -> JsonObject:
    red_blue = stream[..., 0] != stream[..., 2]
    residual = (
        stream[..., 0].astype(np.int16)
        + stream[..., 1].astype(np.int16)
        - 2 * SOURCE_CODE
    )
    values, counts = np.unique(residual, return_counts=True)
    return {
        "redBlueValues": int(red_blue.size),
        "redBlueChangedValues": int(np.count_nonzero(red_blue)),
        "redBlueExact": not bool(np.any(red_blue)),
        "positiveNegativeSymmetryResidualHistogram": {
            str(int(value)): int(count)
            for value, count in zip(values, counts, strict=True)
        },
        "maximumPositiveNegativeSymmetryErrorCodes": int(
            np.abs(residual).max(initial=0)
        ),
    }


def _metal_lod_calibration(path: Path) -> tuple[
    NDArray[np.float64],
    JsonObject,
]:
    probe = SamplerProbe.open(path)
    if probe.lod_expression is None:
        raise ValueError(
            "sampler probe has no LOD expression evidence"
        )
    lod_bits = probe.lod_expression[:, 3]
    lod_values = lod_bits.view(np.float16).astype(np.float64)
    target = np.concatenate((
        np.arange(BIN_COUNT, dtype=np.int64),
        np.asarray((37,), dtype=np.int64),
    ))
    floor_bins = np.floor(lod_values * 64).astype(np.int64)
    return lod_values, {
        "path": str(path),
        "sha256": sha256_file(path) if path.is_file() else None,
        "rigVersion": probe.manifest["rigVersion"],
        "ciCommit": probe.manifest["ciCommit"],
        "radiusInputBitsExact": bool(np.array_equal(
            (
                probe.lod_expression[:, 0].astype(np.uint32)
                | (
                    probe.lod_expression[:, 1].astype(np.uint32)
                    << 16
                )
            ),
            np.asarray(
                [
                    int(
                        state[
                            "requestedBlurRadiusFloat32Bits"
                        ],
                        16,
                    )
                    for state in probe.manifest[
                        "lodExpression"
                    ]["states"]
                ],
                dtype=np.uint32,
            ),
        )),
        "targetFloorBinMismatches": int(
            np.count_nonzero(floor_bins != target)
        ),
        "productionHalfLodBits":
            f"{int(lod_bits[PRODUCTION_STATE_INDEX]):04x}",
        "gridBin37HalfLodBits":
            f"{int(lod_bits[37]):04x}",
        "productionHalfLodValue":
            float(lod_values[PRODUCTION_STATE_INDEX]),
        "gridBin37HalfLodValue": float(lod_values[37]),
        "productionAndGridShareFloorBin": bool(
            floor_bins[PRODUCTION_STATE_INDEX]
            == floor_bins[37]
        ),
        "productionAndGridHalfLodBitsEqual": bool(
            lod_bits[PRODUCTION_STATE_INDEX] == lod_bits[37]
        ),
    }


def analyze(
    path: Path,
    sampler_probe: Path | None = None,
) -> JsonObject:
    started = time.perf_counter()
    sweep = LodSweep.open(path)
    controls = source_fidelity(sweep)
    amplitude_zero = sweep.identity[0]
    baseline_changed = amplitude_zero != SOURCE_CODE
    production = difference_metrics(
        sweep.identity[:, PRODUCTION_STATE_INDEX],
        sweep.identity[:, 37],
    )
    adjacent_changed = [
        int(np.count_nonzero(
            sweep.identity[:, numerator]
            != sweep.identity[:, numerator - 1]
        ))
        for numerator in range(1, BIN_COUNT)
    ]
    first_segment = np.moveaxis(
        sweep.identity[:, :65],
        1,
        -1,
    ).reshape(-1, 65)
    second_segment = np.moveaxis(
        sweep.identity[:, 64:129],
        1,
        -1,
    ).reshape(-1, 65)
    first_feasibility = affine_segment_feasibility(first_segment)
    second_feasibility = affine_segment_feasibility(second_segment)
    calibration: JsonObject | None = None
    half_lod_feasibility: JsonObject | None = None
    if sampler_probe is not None:
        lod_values, calibration = _metal_lod_calibration(
            sampler_probe
        )
        half_lod_feasibility = {
            "levelZeroToOne": affine_segment_feasibility(
                first_segment,
                lod_values[:65],
            ),
            "levelOneToTwo": affine_segment_feasibility(
                second_segment,
                lod_values[64:129] - 1,
            ),
        }
    return {
        "liquidGlassLodSweepAnalysisSchemaVersion": 1,
        "analysisImplementation": {
            "file": "analysis/liquid_glass_lod_sweep.py",
            "sha256": sha256_file(Path(__file__).resolve()),
            "python": platform.python_version(),
            "numpy": np.__version__,
        },
        "source": {
            "path": str(path),
            "sha256": sha256_file(path) if path.is_file() else None,
            "rigVersion": sweep.manifest["rigVersion"],
            "flatBlurProfile":
                sweep.manifest["rigVersion"] == FLAT_RIG,
            "ciCommit": sweep.manifest["ciCommit"],
            "osVersion": sweep.manifest["osVersion"],
            "architecture": sweep.manifest["architecture"],
            "memberSha256": sweep.member_hashes,
        },
        "controls": {
            "sourceFidelity": controls,
            "identityAmplitudeZero": {
                "values": int(baseline_changed.size),
                "changedValues": int(
                    np.count_nonzero(baseline_changed)
                ),
                "exact": not bool(np.any(baseline_changed)),
            },
        },
        "lodQuantization": {
            "metalExpressionCalibration": calibration,
            "productionCaptureVsGridBin37": production,
            "adjacentBinChangedValues": adjacent_changed,
            "adjacentBinsWithAnyObservableChange": int(
                np.count_nonzero(adjacent_changed)
            ),
        },
        "fusedTrilinearFeasibility": {
            "coarseFloorBinFractions": {
                "levelZeroToOne": first_feasibility,
                "levelOneToTwo": second_feasibility,
            },
            "measuredHalfLodFractions":
                half_lod_feasibility,
            "interpretation": (
                (
                    "The all-ones blur profile removes every "
                    "SDF-conditioned alpha difference, making each "
                    "requested-radius state spatially stationary. It "
                    "does not hold the upstream mip resource fixed "
                    "across requested radii. Affine incompatibility, "
                    "together with different native pixels for "
                    "production radius one and grid state 37 despite "
                    "their shared calibrated sampler bucket, is "
                    "evidence that requested radius also configures "
                    "upstream mip generation."
                )
                if sweep.manifest["rigVersion"] == FLAT_RIG
                else (
                    "The default blur-opacity profile multiplies radius "
                    "by the sampled SDF, while requested radius can also "
                    "configure the upstream mip resource. Its state "
                    "labels therefore change two variables and cannot "
                    "serve as a fixed-resource LOD interpolation test."
                )
            ),
        },
        "signedChannelDiagnostics":
            signed_channel_diagnostics(sweep.identity),
        "resourceMeasurements": {
            "analysisSeconds": time.perf_counter() - started,
            "maximumResidentSetKiB":
                resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        },
        "conclusion": {
            "captureControlsExact": controls["exact"],
            "spatialSdfConditioningRemoved":
                sweep.manifest["rigVersion"] == FLAT_RIG,
            "productionAndGridCaptureExactDuplicate":
                production["changedValues"] == 0,
            "productionAndGridShareCalibratedFloorBin": (
                calibration is not None
                and calibration[
                    "productionAndGridShareFloorBin"
                ]
            ),
            "crossRequestedRadiiUseOneFixedMipResource": (
                first_feasibility["allCompatible"]
                and second_feasibility["allCompatible"]
            ),
            "sameSamplerBucketDifferentRequestedRadiusChanged": (
                calibration is not None
                and calibration[
                    "productionAndGridShareFloorBin"
                ]
                and production["changedValues"] != 0
            ),
            "flatGridIsBitwiseCompleteLodOracle": (
                sweep.manifest["rigVersion"] == FLAT_RIG
                and first_feasibility["allCompatible"]
                and second_feasibility["allCompatible"]
                and production["changedValues"] == 0
            ),
            "productionShaderAuthorized": False,
            "requiredGate":
                "zero unequal channels on protected Apple captures",
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Analyze the native Liquid Glass LOD sweep."
    )
    parser.add_argument("lod_sweep", type=Path)
    parser.add_argument(
        "--sampler-probe",
        type=Path,
        help="Metal sampler probe containing LOD expression bits.",
    )
    parser.add_argument("-o", "--output", type=Path)
    arguments = parser.parse_args()
    result = analyze(
        arguments.lod_sweep,
        sampler_probe=arguments.sampler_probe,
    )
    encoded = json.dumps(
        result,
        indent=2,
        sort_keys=True,
        allow_nan=False,
    ) + "\n"
    if arguments.output is None:
        print(encoded, end="")
    else:
        arguments.output.write_text(encoded, encoding="utf-8")
        print(arguments.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
