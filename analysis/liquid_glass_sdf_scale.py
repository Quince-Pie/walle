#!/usr/bin/env python3
"""Invert Apple's SDF-conditioned blur profile at binary16 precision."""

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

from liquid_glass_fixed_resource_lod import (
    difference_metrics,
    response_signatures,
)
from liquid_glass_lod_cross_match import (
    CandidateBounds,
    exact_catalog_candidates,
    exact_signature_words,
)
from liquid_glass_lod_sweep import (
    AMPLITUDES,
    CHANNELS,
    IDENTITY_VALUES,
    LodSweep,
    PATCH_SIDE,
    SITE_COUNT,
    SOURCE_CODE,
    float32_bits,
)


type JsonObject = dict[str, Any]
type UInt8Array = NDArray[np.uint8]
type UInt16Array = NDArray[np.uint16]
type UInt64Array = NDArray[np.uint64]

EXPECTED_SCHEMA = 1
EXPECTED_RIG = "native-sdf-scale-sweep-1.0.0"
EXPECTED_KIND = "exhaustive-binary16-opacity-scale-curve"
SCALE_NUMERATOR_MINIMUM = 1638
SCALE_NUMERATOR_MAXIMUM = 2048
SCALE_DENOMINATOR = 2048
SCALE_HALF_BITS_MINIMUM = 0x3A66
SCALE_HALF_BITS_MAXIMUM = 0x3C00
RESOURCE_RADIUS = 4.0
STATE_COUNT = (
    SCALE_NUMERATOR_MAXIMUM - SCALE_NUMERATOR_MINIMUM + 1
)
SIGNATURE_BYTES = len(AMPLITUDES) * CHANNELS
DEFAULT_RADIUS_FOUR_STATE = 128
BLOCK_BYTES = 1024 * 1024


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(BLOCK_BYTES):
            digest.update(block)
    return digest.hexdigest()


def _expected_scale_state(
    index: int,
    numerator: int,
    *,
    pinned_pyramid: bool = False,
) -> JsonObject:
    scale = np.float32(numerator / SCALE_DENOMINATOR)
    effective_radius = np.float32(
        np.float32(RESOURCE_RADIUS) * scale
    )
    half_bits = SCALE_HALF_BITS_MINIMUM + index
    state: JsonObject = {
        "index": index,
        "name": (
            f"pinned-sdf-scale-half-{half_bits:04x}"
            if pinned_pyramid
            else f"sdf-scale-half-{half_bits:04x}"
        ),
        "resourceBlurRadius": RESOURCE_RADIUS,
        "resourceBlurRadiusFloat32Bits": "40800000",
        "constantBlurOpacityScale": float(scale),
        "constantBlurOpacityScaleFloat32Bits":
            float32_bits(float(scale)),
        "constantBlurOpacityScaleFloat16Bits":
            f"{half_bits:04x}",
        "constantBlurOpacityScaleNumerator": numerator,
        "constantBlurOpacityScaleDenominator":
            SCALE_DENOMINATOR,
        "targetEffectiveBlurRadius": float(effective_radius),
        "targetEffectiveBlurRadiusFloat32Bits":
            float32_bits(float(effective_radius)),
        "productionScale":
            numerator == SCALE_NUMERATOR_MAXIMUM,
    }
    if pinned_pyramid:
        state["pinnedPyramidProfile"] = True
    return state


def _expected_filter_values(
    scale: float,
    *,
    pinned_pyramid: bool = False,
) -> JsonObject:
    values = dict(IDENTITY_VALUES)
    if pinned_pyramid:
        values.update({
            "inputBlurOpacity0": scale,
            "inputBlurOpacity1": scale,
            "inputBlurOpacity2": 1,
            "inputBlurOpacity3": 1,
            "inputBlurOpacity4": 1,
            "inputBlurDistance0": -400,
            "inputBlurDistance1": -1,
            "inputBlurDistance2": 0,
            "inputBlurDistance3": 0,
            "inputBlurDistance4": 0,
        })
    else:
        values.update({
            f"inputBlurOpacity{index}": scale
            for index in range(5)
        })
    values.update({
        "inputInnerRefractionAmount": -60,
        "inputOuterRefractionAmount": 160,
        "inputRefractionOpacity": 0,
        "inputBlurRadius": RESOURCE_RADIUS,
    })
    return values


def _validate_manifest(
    manifest: JsonObject,
    *,
    pinned_pyramid: bool = False,
) -> list[JsonObject]:
    expected_rig = (
        "native-pinned-sdf-scale-sweep-1.0.0"
        if pinned_pyramid
        else EXPECTED_RIG
    )
    expected_kind = (
        "exhaustive-binary16-interior-scale-"
        "pinned-profile-curve"
        if pinned_pyramid
        else EXPECTED_KIND
    )
    if (
        manifest.get("schemaVersion") != EXPECTED_SCHEMA
        or manifest.get("rigVersion") != expected_rig
        or manifest.get("sweepKind") != expected_kind
    ):
        raise ValueError("SDF scale rig differs")
    if (
        manifest.get("flatBlurProfileInputs") is not None
        or manifest.get("fixedResourceInputs") is not None
    ):
        raise ValueError("SDF scale unrelated input markers differ")
    expected_all_inputs = {
            "inputBlurRadius": RESOURCE_RADIUS,
            "inputBlurOpacity0Through4":
                "all enumerate every binary16 value "
                "from 0x3a66 through 0x3c00",
            "inputInnerRefractionAmount": -60,
            "inputOuterRefractionAmount": 160,
            "inputRefractionOpacity": 0,
    }
    expected_pinned_inputs = {
        "inputBlurRadius": RESOURCE_RADIUS,
        "inputBlurOpacity0And1":
            "both enumerate every binary16 value "
            "from 0x3a66 through 0x3c00",
        "inputBlurOpacity2Through4": 1,
        "inputBlurDistance0Through4":
            [-400, -1, 0, 0, 0],
        "inputInnerRefractionAmount": -60,
        "inputOuterRefractionAmount": 160,
        "inputRefractionOpacity": 0,
    }
    if (
        manifest.get("sdfScaleInputs")
        != (None if pinned_pyramid else expected_all_inputs)
        or manifest.get("pinnedSdfScaleInputs")
        != (expected_pinned_inputs if pinned_pyramid else None)
    ):
        raise ValueError("SDF scale fixed inputs differ")

    expected_states = [
        _expected_scale_state(
            index,
            numerator,
            pinned_pyramid=pinned_pyramid,
        )
        for index, numerator in enumerate(
            range(
                SCALE_NUMERATOR_MINIMUM,
                SCALE_NUMERATOR_MAXIMUM + 1,
            )
        )
    ]
    design = manifest.get("lodDesign")
    expected_design = (
        {
            "states": expected_states,
            "resourceBlurRadius": RESOURCE_RADIUS,
            "constantInteriorBlurOpacityScaleNumeratorRangeInclusive": [
                SCALE_NUMERATOR_MINIMUM,
                SCALE_NUMERATOR_MAXIMUM,
            ],
            "constantInteriorBlurOpacityScaleDenominator":
                SCALE_DENOMINATOR,
            "constantInteriorBlurOpacityScaleFloat16BitsRangeInclusive": [
                f"{SCALE_HALF_BITS_MINIMUM:04x}",
                f"{SCALE_HALF_BITS_MAXIMUM:04x}",
            ],
            "activeInteriorOpacityIndices": [0, 1],
            "pinnedOpacityIndices": [2, 3, 4],
            "pinnedOpacity": 1,
            "blurDistances": [-400, -1, 0, 0, 0],
        }
        if pinned_pyramid
        else {
            "states": expected_states,
            "resourceBlurRadius": RESOURCE_RADIUS,
            "constantBlurOpacityScaleNumeratorRangeInclusive": [
                SCALE_NUMERATOR_MINIMUM,
                SCALE_NUMERATOR_MAXIMUM,
            ],
            "constantBlurOpacityScaleDenominator":
                SCALE_DENOMINATOR,
            "constantBlurOpacityScaleFloat16BitsRangeInclusive": [
                f"{SCALE_HALF_BITS_MINIMUM:04x}",
                f"{SCALE_HALF_BITS_MAXIMUM:04x}",
            ],
        }
    )
    if design != expected_design:
        raise ValueError("SDF scale state design differs")

    captures = manifest.get("captures")
    if (
        not isinstance(captures, list)
        or len(captures) != len(AMPLITUDES)
    ):
        raise ValueError("SDF scale capture catalog differs")
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
            or capture.get("captureBackend")
            != "CGWindowListCreateImage"
        ):
            raise ValueError(
                f"SDF scale capture differs at {amplitude}"
            )
        records = capture.get("states")
        if (
            not isinstance(records, list)
            or len(records) != STATE_COUNT
        ):
            raise ValueError(
                f"SDF scale states differ at {amplitude}"
            )
        for expected, record in zip(
            expected_states,
            records,
            strict=True,
        ):
            scale = float(
                expected["constantBlurOpacityScale"]
            )
            values = _expected_filter_values(
                scale,
                pinned_pyramid=pinned_pyramid,
            )
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
                or record.get("readbackBlurRadius")
                != RESOURCE_RADIUS
                or record.get("readbackBlurRadiusFloat32Bits")
                != "40800000"
                or int(record.get("stabilitySamples", 0)) < 2
                or record.get("captureBackend")
                != "CGWindowListCreateImage"
            ):
                raise ValueError(
                    "SDF scale readback differs at "
                    f"{amplitude}/{expected['name']}"
                )
    return expected_states


def _copy_zip_member(
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
class SdfScaleSweep:
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
        pinned_pyramid: bool = False,
    ) -> "SdfScaleSweep":
        if path.is_dir():
            manifest = json.loads(
                (path / "manifest.json").read_text(
                    encoding="utf-8"
                )
            )

            def read_small(name: str) -> bytes:
                return (path / name).read_bytes()

            identity_path = path
        else:
            with zipfile.ZipFile(path) as archive:
                names = archive.namelist()
                if len(names) != len(set(names)):
                    raise ValueError(
                        "SDF scale archive has duplicate members"
                    )
                try:
                    manifest = json.loads(
                        archive.read("manifest.json")
                    )
                except KeyError as error:
                    raise ValueError(
                        "SDF scale manifest is missing"
                    ) from error

                def read_small(name: str) -> bytes:
                    try:
                        return archive.read(name)
                    except KeyError as error:
                        raise ValueError(
                            "SDF scale member is missing: "
                            f"{name}"
                        ) from error

                _validate_manifest(
                    manifest,
                    pinned_pyramid=pinned_pyramid,
                )
                evidence = manifest.get("nativeCaptureEvidence")
                if not isinstance(evidence, dict):
                    raise ValueError(
                        "SDF scale evidence is missing"
                    )
                identity_name = str(evidence["file"])
                raw_path = scratch / (
                    "pinned-sdf-scale-identity.rgb8"
                    if pinned_pyramid
                    else "sdf-scale-identity.rgb8"
                )
                try:
                    identity_size, identity_hash = (
                        _copy_zip_member(
                            archive,
                            identity_name,
                            raw_path,
                        )
                    )
                except KeyError as error:
                    raise ValueError(
                        "SDF scale identity stream is missing"
                    ) from error
                identity_path = raw_path

            def read_small(name: str) -> bytes:
                with zipfile.ZipFile(path) as reopened:
                    try:
                        return reopened.read(name)
                    except KeyError as error:
                        raise ValueError(
                            "SDF scale member is missing: "
                            f"{name}"
                        ) from error

        expected_states = _validate_manifest(
            manifest,
            pinned_pyramid=pinned_pyramid,
        )
        if len(expected_states) != STATE_COUNT:
            raise AssertionError("SDF scale state count differs")
        evidence = manifest.get("nativeCaptureEvidence")
        if not isinstance(evidence, dict):
            raise ValueError("SDF scale evidence is missing")
        control_records = (
            len(AMPLITUDES) * SITE_COUNT * PATCH_SIDE**2
        )
        identity_records = control_records * STATE_COUNT
        if (
            evidence.get("schemaVersion") != EXPECTED_SCHEMA
            or evidence.get("recordFormat") != "RGB8"
            or evidence.get("recordStrideBytes") != CHANNELS
            or evidence.get("controlRecordCount") != control_records
            or evidence.get("recordCount") != identity_records
        ):
            raise ValueError("SDF scale stream layout differs")

        control_name = str(evidence["controlFile"])
        control_bytes = read_small(control_name)
        control_hash = hashlib.sha256(control_bytes).hexdigest()
        expected_control_bytes = control_records * CHANNELS
        if (
            len(control_bytes) != expected_control_bytes
            or evidence.get("controlFileBytes")
            != expected_control_bytes
            or evidence.get("controlFileSha256")
            != control_hash
        ):
            raise ValueError(
                "SDF scale control stream differs"
            )

        identity_name = str(evidence["file"])
        expected_identity_bytes = identity_records * CHANNELS
        if path.is_dir():
            identity_path = path / identity_name
            identity_size = identity_path.stat().st_size
            identity_hash = sha256_file(identity_path)
        if (
            identity_size != expected_identity_bytes
            or evidence.get("fileBytes")
            != expected_identity_bytes
            or evidence.get("fileSha256")
            != identity_hash
        ):
            raise ValueError(
                "SDF scale identity stream differs"
            )

        member_hashes = {
            control_name: control_hash,
            identity_name: identity_hash,
        }
        if icc_name := evidence.get("iccFile"):
            icc = read_small(str(icc_name))
            icc_hash = hashlib.sha256(icc).hexdigest()
            if (
                len(icc) != evidence.get("iccFileBytes")
                or icc_hash != evidence.get("iccFileSha256")
            ):
                raise ValueError(
                    "SDF scale ICC evidence differs"
                )
            member_hashes[str(icc_name)] = icc_hash

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
        identity = np.memmap(
            identity_path,
            dtype=np.uint8,
            mode="r",
            shape=(
                len(AMPLITUDES),
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


def _state_signature_words(
    identity: UInt8Array,
    state_index: int,
) -> UInt64Array:
    signatures = np.ascontiguousarray(
        np.transpose(
            identity[:, state_index],
            (1, 2, 3, 0, 4),
        )
    ).reshape(
        SITE_COUNT * PATCH_SIDE**2,
        SIGNATURE_BYTES,
    )
    return exact_signature_words(signatures)


def _catalog_words(
    identity: UInt8Array,
    path: Path,
) -> UInt64Array:
    spatial_count = SITE_COUNT * PATCH_SIDE**2
    words = np.memmap(
        path,
        dtype=np.uint64,
        mode="w+",
        shape=(STATE_COUNT, spatial_count, 2),
    )
    for state_index in range(STATE_COUNT):
        words[state_index] = _state_signature_words(
            identity,
            state_index,
        )
    words.flush()
    return words


def _candidate_summary(
    bounds: CandidateBounds,
) -> JsonObject:
    if bounds.count.shape != (
        1,
        SITE_COUNT * PATCH_SIDE**2,
    ):
        raise ValueError("SDF candidate bounds shape differs")
    count = bounds.count[0]
    lower = bounds.lower[0]
    matched = count != 0
    unique = count == 1
    histogram = np.bincount(
        lower[unique],
        minlength=STATE_COUNT,
    )
    noncontiguous = matched & ~bounds.contiguous[0]
    return {
        "spatialSignatures": int(count.size),
        "unmatchedSignatures":
            int(np.count_nonzero(~matched)),
        "uniqueScaleSignatures":
            int(np.count_nonzero(unique)),
        "ambiguousScaleSignatures":
            int(np.count_nonzero(count > 1)),
        "noncontiguousCandidateSignatures":
            int(np.count_nonzero(noncontiguous)),
        "allSignaturesMatched": bool(np.all(matched)),
        "allCandidateSetsContiguous":
            not bool(np.any(noncontiguous)),
        "candidateCountMinimum": int(count.min()),
        "candidateCountMaximum": int(count.max()),
        "uniqueScaleHistogram": {
            f"{SCALE_HALF_BITS_MINIMUM + index:04x}":
                int(value)
            for index, value in enumerate(histogram)
            if value
        },
    }


def _spatial_coordinates(
    source_design: JsonObject,
    *,
    offset: float,
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    sites = source_design.get("sites")
    if not isinstance(sites, list) or len(sites) != SITE_COUNT:
        raise ValueError("SDF scale sites differ")
    delta_y, delta_x = np.mgrid[
        -PATCH_SIDE // 2 + 1:PATCH_SIDE // 2 + 1,
        -PATCH_SIDE // 2 + 1:PATCH_SIDE // 2 + 1,
    ]
    x = np.asarray([
        np.asarray(site["x"] + delta_x, dtype=np.float64)
        + offset
        for site in sites
    ])
    y = np.asarray([
        np.asarray(site["y"] + delta_y, dtype=np.float64)
        + offset
        for site in sites
    ])
    return x.reshape(-1), y.reshape(-1)


def _air_profile_scale(sdf: NDArray[np.float32]) -> UInt16Array:
    sdf_half = sdf.astype(np.float16)
    distance0 = np.float32(-400)
    distance1 = np.float32(-1)
    interval = np.float32(distance1 - distance0)
    inverse = np.float32(np.float32(1) / interval)
    bias = np.float32(-distance0 / interval)
    interpolation = (
        sdf_half.astype(np.float64)
        * np.float64(inverse)
        + np.float64(bias)
    ).astype(np.float32)
    interpolation = np.clip(
        interpolation,
        np.float32(0),
        np.float32(1),
    ).astype(np.float16)
    weighted = np.multiply(
        np.float16(0.5),
        interpolation,
        dtype=np.float16,
    )
    scale = np.subtract(
        np.float16(1),
        weighted,
        dtype=np.float16,
    )
    return scale.view(np.uint16)


def _normalized_circle_prediction(
    source_design: JsonObject,
    glass_shape: JsonObject,
    *,
    offset: float,
) -> UInt16Array:
    x, y = _spatial_coordinates(
        source_design,
        offset=offset,
    )
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
    return _air_profile_scale(sdf)


def _prediction_metrics(
    predicted_half_bits: UInt16Array,
    probe_words: UInt64Array,
    catalog_words: UInt64Array,
) -> JsonObject:
    if predicted_half_bits.shape != (probe_words.shape[0],):
        raise ValueError("SDF scale prediction shape differs")
    index = (
        predicted_half_bits.astype(np.int32)
        - SCALE_HALF_BITS_MINIMUM
    )
    valid = (index >= 0) & (index < STATE_COUNT)
    spatial = np.arange(probe_words.shape[0])
    exact = np.zeros(probe_words.shape[0], dtype=np.bool_)
    exact[valid] = np.all(
        probe_words[valid]
        == catalog_words[
            index[valid],
            spatial[valid],
        ],
        axis=1,
    )
    return {
        "spatialSignatures": int(exact.size),
        "predictionsInCatalog": int(np.count_nonzero(valid)),
        "exactSignatureMatches": int(np.count_nonzero(exact)),
        "exactSignatureFraction": float(np.mean(exact)),
        "allSignaturesExact": bool(np.all(exact)),
        "predictedHalfBitsMinimum": (
            f"{int(predicted_half_bits.min()):04x}"
        ),
        "predictedHalfBitsMaximum": (
            f"{int(predicted_half_bits.max()):04x}"
        ),
    }


def _unique_radial_fit(
    bounds: CandidateBounds,
    source_design: JsonObject,
) -> JsonObject:
    count = bounds.count[0]
    unique = count == 1
    state = bounds.lower[0, unique].astype(np.float64)
    x, y = _spatial_coordinates(source_design, offset=0)
    radius = np.sqrt(
        np.square(x[unique] - 512)
        + np.square(y[unique] - 512)
    )
    scale = (
        SCALE_NUMERATOR_MINIMUM + state
    ) / SCALE_DENOMINATOR
    if radius.size == 0:
        return {
            "identifiedUniqueSignatures": 0,
            "scaleIntercept": None,
            "scaleSlopePerSourcePixel": None,
            "expectedNormalizedCircleIntercept": 1.0,
            "expectedNormalizedCircleSlopePerSourcePixel":
                -1 / 3990,
            "rootMeanSquareScaleResidual": None,
            "maximumAbsoluteScaleResidual": None,
            "limitation": (
                "No response signature identifies exactly one catalog "
                "state. Exact acceptance still uses every full 15-byte "
                "signature and no fitted tolerance."
            ),
        }
    design = np.column_stack((
        np.ones(radius.size),
        radius,
    ))
    coefficients, *_ = np.linalg.lstsq(
        design,
        scale,
        rcond=None,
    )
    predicted = design @ coefficients
    residual = scale - predicted
    return {
        "identifiedUniqueSignatures": int(radius.size),
        "scaleIntercept": float(coefficients[0]),
        "scaleSlopePerSourcePixel":
            float(coefficients[1]),
        "expectedNormalizedCircleIntercept": 1.0,
        "expectedNormalizedCircleSlopePerSourcePixel":
            -1 / 3990,
        "rootMeanSquareScaleResidual": float(
            np.sqrt(np.mean(np.square(residual)))
        ),
        "maximumAbsoluteScaleResidual": float(
            np.max(np.abs(residual), initial=0)
        ),
        "limitation": (
            "The fit uses only response signatures that identify one "
            "catalog state. It is diagnostic; exact acceptance uses "
            "the full 15-byte signatures and no fitted tolerance."
        ),
    }


def _write_maps(
    path: Path,
    bounds: CandidateBounds,
    predictions: dict[str, UInt16Array],
) -> JsonObject:
    shape = (SITE_COUNT, PATCH_SIDE, PATCH_SIDE)
    arrays: dict[str, NDArray[Any]] = {
        "candidate_count": bounds.count[0].reshape(shape),
        "candidate_lower": bounds.lower[0].reshape(shape),
        "candidate_upper": bounds.upper[0].reshape(shape),
    }
    arrays.update({
        f"normalized_circle_{name}_half_bits":
            value.reshape(shape)
        for name, value in predictions.items()
    })
    with path.open("wb") as stream:
        np.savez_compressed(stream, **arrays)
    return {
        "path": str(path),
        "sha256": sha256_file(path),
        "spatialShape": list(shape),
        "dtype": "uint16",
        "unmatchedSentinel": STATE_COUNT,
        "arrays": sorted(arrays),
    }


def analyze(
    sdf_scale_path: Path,
    default_path: Path,
    *,
    map_output: Path | None = None,
) -> JsonObject:
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(
        prefix="liquid-glass-sdf-scale-"
    ) as temporary:
        scratch = Path(temporary)
        sweep = SdfScaleSweep.open(
            sdf_scale_path,
            scratch=scratch,
        )
        default = LodSweep.open(default_path)
        for key in (
            "osVersion",
            "architecture",
            "sourceDesign",
            "glassShape",
        ):
            if (
                sweep.manifest.get(key)
                != default.manifest.get(key)
            ):
                raise ValueError(
                    "SDF scale and default metadata differ: "
                    f"{key}"
                )

        controls = difference_metrics(
            sweep.control,
            default.control,
        )
        amplitude_zero = difference_metrics(
            sweep.identity[0],
            np.full_like(
                sweep.identity[0],
                SOURCE_CODE,
            ),
        )
        catalog_words = _catalog_words(
            sweep.identity,
            scratch / "catalog.u64",
        )
        probe_words = _state_signature_words(
            default.identity,
            DEFAULT_RADIUS_FOUR_STATE,
        )
        bounds = exact_catalog_candidates(
            probe_words[np.newaxis, ...],
            catalog_words,
        )
        candidate_summary = _candidate_summary(bounds)

        predictions = {
            name: _normalized_circle_prediction(
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
        prediction_reports = {
            name: _prediction_metrics(
                value,
                probe_words,
                catalog_words,
            )
            for name, value in predictions.items()
        }
        map_record = (
            _write_maps(
                map_output,
                bounds,
                predictions,
            )
            if map_output is not None
            else None
        )
        radial_fit = _unique_radial_fit(
            bounds,
            sweep.manifest["sourceDesign"],
        )
        source_record = {
            "path": str(sdf_scale_path),
            "sha256": (
                sha256_file(sdf_scale_path)
                if sdf_scale_path.is_file()
                else None
            ),
            "ciCommit": sweep.manifest["ciCommit"],
            "memberSha256": sweep.member_hashes,
        }
        default_record = {
            "path": str(default_path),
            "sha256": (
                sha256_file(default_path)
                if default_path.is_file()
                else None
            ),
            "ciCommit": default.manifest["ciCommit"],
        }

    return {
        "liquidGlassSdfScaleAnalysisSchemaVersion": 1,
        "analysisImplementation": {
            "file": "analysis/liquid_glass_sdf_scale.py",
            "sha256": sha256_file(Path(__file__).resolve()),
            "python": platform.python_version(),
            "numpy": np.__version__,
        },
        "sources": {
            "sdfScale": source_record,
            "default": default_record,
            "osVersion": default.manifest["osVersion"],
            "architecture": default.manifest["architecture"],
            "sourceAndGeometryExact": True,
        },
        "controls": {
            "source": controls,
            "identityAmplitudeZero": amplitude_zero,
        },
        "exactScaleCatalogMatching": {
            "equality": (
                "All five native RGB8 amplitude responses are packed "
                "losslessly into two uint64 words. Matching is direct "
                "15-byte equality, with no hash, fit, or tolerance."
            ),
            "scaleStates": STATE_COUNT,
            "halfBitsRangeInclusive": [
                f"{SCALE_HALF_BITS_MINIMUM:04x}",
                f"{SCALE_HALF_BITS_MAXIMUM:04x}",
            ],
            "summary": candidate_summary,
            "candidateMaps": map_record,
        },
        "analyticSdfTests": {
            "profileArithmetic": (
                "Apple AIR float32 interval FMA, saturation and "
                "binary16 conversion, followed by ordered binary16 "
                "opacity arithmetic"
            ),
            "normalizedCircle": prediction_reports,
            "uniqueStateRadialFit": radial_fit,
        },
        "resourceMeasurements": {
            "analysisSeconds": time.perf_counter() - started,
            "maximumResidentSetKiB":
                resource.getrusage(
                    resource.RUSAGE_SELF
                ).ru_maxrss,
        },
        "conclusion": {
            "captureControlsExact": (
                controls["exact"]
                and amplitude_zero["exact"]
            ),
            "defaultRadiusFourExplainedByExhaustiveScaleCatalog":
                candidate_summary["allSignaturesMatched"],
            "normalizedCircleAirModelExact": any(
                report["allSignaturesExact"]
                for report in prediction_reports.values()
            ),
            "productionShaderAuthorized": False,
            "requiredGate":
                "zero unequal channels on protected Apple captures",
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Invert Apple's default Liquid Glass SDF-conditioned "
            "blur profile against every relevant binary16 scale."
        )
    )
    parser.add_argument("sdf_scale_sweep", type=Path)
    parser.add_argument("default_lod_sweep", type=Path)
    parser.add_argument("--map-output", type=Path)
    parser.add_argument("-o", "--output", type=Path)
    arguments = parser.parse_args()
    result = analyze(
        arguments.sdf_scale_sweep,
        arguments.default_lod_sweep,
        map_output=arguments.map_output,
    )
    encoded = json.dumps(
        result,
        indent=2,
        sort_keys=True,
        allow_nan=False,
    ) + "\n"
    if arguments.output:
        arguments.output.write_text(encoded, encoding="utf-8")
        print(arguments.output)
    else:
        print(encoded, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
