#!/usr/bin/env python3
"""Validate Apple's fixed-production-resource Liquid Glass LOD oracle."""

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
    CHANNELS,
    IDENTITY_VALUES,
    PATCH_SIDE,
    SITE_COUNT,
    _expected_blur_radius,
    _expected_sites,
    float32_bits,
)


type JsonObject = dict[str, Any]
type UInt8Array = NDArray[np.uint8]

EXPECTED_RIG = "native-production-kernel-lod-sweep-1.0.0"
EXPECTED_KIND = "production-profile-fixed-resource-randomized-lod-curve"
EXPECTED_SCHEMA = 1
PATTERN_COUNT = 6
STATE_COUNT = 40
GRID_STATE_START = 1
GRID_STATE_STOP = 39
LEADING_PRODUCTION_STATE = 0
GRID_37_STATE = 38
TRAILING_PRODUCTION_STATE = 39
TILE_SIDE = 64
SOURCE_CODE = 128
CONTROL_MEMBER = "native-production-kernel-control-patches.rgb8"
IDENTITY_MEMBER = "native-production-kernel-identity-patches.rgb8"
ICC_MEMBER = "native-production-kernel-capture-colorspace.icc"
BLOCK_BYTES = 1024 * 1024

SOURCE_DEFINITIONS: tuple[
    tuple[str, str, int | None],
    ...,
] = (
    ("constant-128-calibration", "calibration", None),
    ("broadband-train-243f6a88", "train", 0x243F_6A88),
    ("broadband-train-85a308d3", "train", 0x85A3_08D3),
    ("broadband-train-13198a2e", "train", 0x1319_8A2E),
    ("broadband-holdout-03707344", "holdout", 0x0370_7344),
    ("broadband-holdout-a4093822", "holdout", 0xA409_3822),
)

PRODUCTION_PROFILE: JsonObject = {
    "inputBlurOpacity1": 0.5,
    "inputBlurOpacity2": 0.5,
    "inputBlurOpacity3": 1,
    "inputBlurOpacity4": 1,
    "inputBlurDistance0": -400,
    "inputBlurDistance1": -1,
    "inputBlurDistance2": 0,
    "inputBlurDistance3": 0,
    "inputBlurDistance4": 0,
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


def _source_manifest() -> list[JsonObject]:
    return [
        {
            "index": index,
            "name": name,
            "role": role,
            "seedHex": None if seed is None else f"{seed:08x}",
        }
        for index, (name, role, seed) in enumerate(SOURCE_DEFINITIONS)
    ]


def _state_values(active_opacity: float) -> JsonObject:
    return {
        **IDENTITY_VALUES,
        "inputBlurOpacity0": float(np.float32(active_opacity)),
        **PRODUCTION_PROFILE,
    }


def _state_manifest(
    *,
    index: int,
    name: str,
    opacity: np.float32,
    numerator: int,
    production: bool,
) -> JsonObject:
    value = float(opacity)
    return {
        "index": index,
        "name": name,
        "resourceBlurRadius": 1,
        "resourceBlurRadiusFloat32Bits": "3f800000",
        "activeBlurOpacity0": value,
        "activeBlurOpacity0Float32Bits": float32_bits(value),
        "inactiveBlurOpacities1Through4": [0.5, 0.5, 1, 1],
        "blurDistances": [-400, -1, 0, 0, 0],
        "targetEffectiveBlurRadius": value,
        "targetEffectiveBlurRadiusFloat32Bits": float32_bits(value),
        "targetLodNumerator": numerator,
        "targetLodDenominator": 64,
        "productionOpacity": production,
    }


def _expected_states() -> list[JsonObject]:
    states = [
        _state_manifest(
            index=0,
            name="production-opacity-one-leading",
            opacity=np.float32(1),
            numerator=37,
            production=True,
        )
    ]
    for numerator in range(38):
        states.append(_state_manifest(
            index=len(states),
            name=f"production-resource-lod-bin-{numerator:03d}",
            opacity=_expected_blur_radius(numerator),
            numerator=numerator,
            production=False,
        ))
    states.append(_state_manifest(
        index=len(states),
        name="production-opacity-one-trailing",
        opacity=np.float32(1),
        numerator=37,
        production=True,
    ))
    return states


def _expected_filter_bits(values: JsonObject) -> dict[str, str]:
    return {
        key: float32_bits(float(value))
        for key, value in values.items()
        if not isinstance(value, bool)
    }


def _validate_manifest(manifest: JsonObject) -> None:
    if (
        manifest.get("schemaVersion") != EXPECTED_SCHEMA
        or manifest.get("rigVersion") != EXPECTED_RIG
        or manifest.get("sweepKind") != EXPECTED_KIND
        or manifest.get("backingScaleFactor") != 1
    ):
        raise ValueError("production-kernel rig differs")

    source = manifest.get("sourceDesign")
    if (
        not isinstance(source, dict)
        or source.get("kind")
        != "periodic-independent-rgb-system-identification"
        or source.get("sources") != _source_manifest()
        or source.get("tileWidthPixels") != TILE_SIDE
        or source.get("tileHeightPixels") != TILE_SIDE
        or source.get("constantCalibrationCode") != SOURCE_CODE
        or source.get("randomChannelCodeRangeInclusive") != [16, 239]
        or source.get("fitPolicy") != "calibration and train roles only"
        or source.get("acceptancePolicy")
        != "zero unequal native RGB values on both holdout seeds"
        or source.get("patchSidePixels") != PATCH_SIDE
        or source.get("sites") != _expected_sites()
    ):
        raise ValueError("production-kernel source design differs")

    expected_states = _expected_states()
    design = manifest.get("lodDesign")
    if (
        not isinstance(design, dict)
        or design.get("states") != expected_states
        or design.get("stateCount") != STATE_COUNT
        or design.get("resourceBlurRadius") != 1
        or design.get("leadingProductionStateIndex")
        != LEADING_PRODUCTION_STATE
        or design.get("lodGridStateIndexRangeInclusive") != [1, 38]
        or design.get("lodGridNumeratorRangeInclusive") != [0, 37]
        or design.get("gridThirtySevenStateIndex") != GRID_37_STATE
        or design.get("trailingProductionStateIndex")
        != TRAILING_PRODUCTION_STATE
        or design.get("controlledVariable") != "inputBlurOpacity0"
        or design.get("fixedInactiveBlurOpacities1Through4")
        != [0.5, 0.5, 1, 1]
        or design.get("fixedBlurDistances") != [-400, -1, 0, 0, 0]
    ):
        raise ValueError("production-kernel LOD design differs")

    marker = manifest.get("productionKernelInputs")
    if (
        not isinstance(marker, dict)
        or marker.get("inputBlurRadius") != 1
        or marker.get("inputBlurOpacity1Through4")
        != [0.5, 0.5, 1, 1]
        or marker.get("inputBlurDistance0Through4")
        != [-400, -1, 0, 0, 0]
        or marker.get("inputInnerRefractionAmount") != -60
        or marker.get("inputOuterRefractionAmount") != 160
        or marker.get("inputRefractionOpacity") != 0
        or marker.get("inputFaceColorMatrixBlack") != 0
        or marker.get("inputFaceColorMatrixWhite") != 1
        or marker.get("inputFaceColorMatrixSaturation") != 1
        or marker.get("inputSDRHoldingToneEnabled") is not False
    ):
        raise ValueError("production-kernel input marker differs")

    captures = manifest.get("captures")
    if not isinstance(captures, list) or len(captures) != PATTERN_COUNT:
        raise ValueError("production-kernel capture catalog differs")
    for index, (
        definition,
        capture,
    ) in enumerate(zip(SOURCE_DEFINITIONS, captures, strict=True)):
        name, role, seed = definition
        if (
            capture.get("sourcePatternIndex") != index
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
                f"production-kernel source capture differs at {index}"
            )
        records = capture.get("states")
        if not isinstance(records, list) or len(records) != STATE_COUNT:
            raise ValueError(
                f"production-kernel state catalog differs at {index}"
            )
        for expected, record in zip(
            expected_states,
            records,
            strict=True,
        ):
            values = _state_values(expected["activeBlurOpacity0"])
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
                    "production-kernel state/readback differs at "
                    f"source {index}, state {expected['name']}"
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
class ProductionKernelSweep:
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
    ) -> "ProductionKernelSweep":
        if not zipfile.is_zipfile(path):
            raise ValueError("production-kernel artifact is not a ZIP")
        with zipfile.ZipFile(path) as archive:
            bad_member = archive.testzip()
            if bad_member is not None:
                raise ValueError(
                    f"production-kernel CRC failed: {bad_member}"
                )
            try:
                manifest = json.load(archive.open("manifest.json"))
            except KeyError as error:
                raise ValueError(
                    "production-kernel manifest is missing"
                ) from error
            _validate_manifest(manifest)
            evidence = manifest.get("nativeCaptureEvidence")
            if not isinstance(evidence, dict):
                raise ValueError(
                    "production-kernel native evidence is missing"
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
                    "production-kernel stream metadata differs"
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
                    "production-kernel stream digest differs"
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
                        "production-kernel ICC digest differs"
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


def source_code(
    *,
    seed: int,
    x: UInt8Array | NDArray[np.int64],
    y: UInt8Array | NDArray[np.int64],
    channel: int,
) -> UInt8Array:
    value = (
        np.asarray(x, dtype=np.uint32) & np.uint32(TILE_SIDE - 1)
    ) | (
        (
            np.asarray(y, dtype=np.uint32)
            & np.uint32(TILE_SIDE - 1)
        )
        << np.uint32(6)
    ) | np.uint32(channel << 12)
    value ^= np.uint32(seed)
    value = np.multiply(
        value ^ (value >> np.uint32(16)),
        np.uint32(0x7FEB_352D),
        dtype=np.uint32,
    )
    value = np.multiply(
        value ^ (value >> np.uint32(15)),
        np.uint32(0x846C_A68B),
        dtype=np.uint32,
    )
    value ^= value >> np.uint32(16)
    return (16 + value % np.uint32(224)).astype(np.uint8)


def expected_control_patches() -> UInt8Array:
    result = np.empty(
        (
            PATTERN_COUNT,
            SITE_COUNT,
            PATCH_SIDE,
            PATCH_SIDE,
            CHANNELS,
        ),
        dtype=np.uint8,
    )
    offsets = np.arange(
        -(PATCH_SIDE // 2),
        PATCH_SIDE // 2 + 1,
        dtype=np.int64,
    )
    dy, dx = np.meshgrid(offsets, offsets, indexing="ij")
    for pattern_index, (_, _, seed) in enumerate(SOURCE_DEFINITIONS):
        for site in _expected_sites():
            if seed is None:
                result[pattern_index, site["index"]] = SOURCE_CODE
                continue
            for channel in range(CHANNELS):
                result[
                    pattern_index,
                    site["index"],
                    ...,
                    channel,
                ] = source_code(
                    seed=seed,
                    x=site["x"] + dx,
                    y=site["y"] + dy,
                    channel=channel,
                )
    return result


def _curve_diagnostics(identity: UInt8Array) -> JsonObject:
    grid = identity[:, GRID_STATE_START:GRID_STATE_STOP]
    delta = np.diff(grid.astype(np.int16), axis=1)
    nondecreasing = np.all(delta >= 0, axis=1)
    nonincreasing = np.all(delta <= 0, axis=1)
    monotonic = nondecreasing | nonincreasing
    flat = np.all(delta == 0, axis=1)
    changed_steps = np.count_nonzero(delta, axis=1)
    values = int(monotonic.size)
    return {
        "sampledChannelCurves": values,
        "monotonicCurves": int(np.count_nonzero(monotonic)),
        "monotonicFraction": float(np.mean(monotonic)),
        "flatCurves": int(np.count_nonzero(flat)),
        "nonflatCurves": int(np.count_nonzero(~flat)),
        "minimumChangedSteps": int(changed_steps.min()),
        "maximumChangedSteps": int(changed_steps.max()),
        "allMonotonic": bool(np.all(monotonic)),
    }


def analyze(path: Path) -> JsonObject:
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(
        prefix="liquid-glass-production-kernel-"
    ) as temporary:
        sweep = ProductionKernelSweep.open(
            path,
            scratch=Path(temporary),
        )
        control_fidelity = difference_metrics(
            sweep.control,
            expected_control_patches(),
        )
        production_repeat = difference_metrics(
            sweep.identity[:, LEADING_PRODUCTION_STATE],
            sweep.identity[:, TRAILING_PRODUCTION_STATE],
        )
        grid_37 = difference_metrics(
            sweep.identity[:, LEADING_PRODUCTION_STATE],
            sweep.identity[:, GRID_37_STATE],
        )
        constant_control = difference_metrics(
            sweep.identity[0],
            np.broadcast_to(
                sweep.control[0],
                sweep.identity[0].shape,
            ),
        )
        curve_diagnostics = _curve_diagnostics(sweep.identity)
        role_metrics = {
            role: {
                "productionRepeat": difference_metrics(
                    sweep.identity[indices, LEADING_PRODUCTION_STATE],
                    sweep.identity[indices, TRAILING_PRODUCTION_STATE],
                ),
                "gridThirtySevenVsProduction":
                    difference_metrics(
                        sweep.identity[
                            indices,
                            LEADING_PRODUCTION_STATE,
                        ],
                        sweep.identity[indices, GRID_37_STATE],
                    ),
            }
            for role, indices in {
                "train": np.asarray((1, 2, 3)),
                "holdout": np.asarray((4, 5)),
            }.items()
        }
        source = {
            "path": str(path),
            "sha256": sha256_file(path),
            "ciCommit": sweep.manifest["ciCommit"],
            "osVersion": sweep.manifest["osVersion"],
            "architecture": sweep.manifest["architecture"],
            "memberSha256": sweep.member_hashes,
        }

    resource_invariant = bool(
        production_repeat["exact"] and grid_37["exact"]
    )
    return {
        "liquidGlassProductionKernelAnalysisSchemaVersion": 1,
        "analysisImplementation": {
            "file":
                "analysis/liquid_glass_production_kernel.py",
            "sha256": sha256_file(Path(__file__).resolve()),
            "python": platform.python_version(),
            "numpy": np.__version__,
        },
        "source": source,
        "controls": {
            "capturedSourceVsDeterministicGenerator":
                control_fidelity,
            "constantInputAcrossAllLodStates":
                constant_control,
        },
        "resourceInvariance": {
            "leadingVsTrailingProduction": production_repeat,
            "gridThirtySevenVsExactProduction": grid_37,
            "byPreregisteredRole": role_metrics,
            "accepted": resource_invariant,
        },
        "lodCurves": curve_diagnostics,
        "conclusion": {
            "captureControlsExact": control_fidelity["exact"],
            "fixedProductionResourceAccepted": resource_invariant,
            "allObservedCurvesMonotonic":
                curve_diagnostics["allMonotonic"],
            "productionShaderAuthorized": False,
            "nextGate": (
                "decode exact mip endpoint samples, recover the "
                "seven-tap host uniforms on train seeds, and replay "
                "both protected holdouts with zero unequal channels"
                if resource_invariant
                else "identify which active-opacity input rebuilds "
                "the production source resource"
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
