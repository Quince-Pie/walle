#!/usr/bin/env python3
"""Validate raw Apple Metal texture-sampler arithmetic."""

import argparse
import hashlib
import json
import platform
import resource
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray


type HalfArray = NDArray[np.float16]
type UInt16Array = NDArray[np.uint16]
type JsonObject = dict[str, Any]

PAIR_COUNT = 256 * 256
FRACTION_COUNT = 257
FIXED_RECORD_WORDS = 8
FRACTION_RECORD_WORDS = 2
BILINEAR_RECORD_WORDS = 3
UNORM_MIP_RECORD_WORDS = 1
UNORM_TRILINEAR_POSITIONS = (
    "center",
    "x-quarter",
    "x-half",
    "x-three-quarter",
    "y-quarter",
    "y-half",
    "y-three-quarter",
)
UNORM_TRILINEAR_FIELDS = 3
UNORM_TRILINEAR_RECORD_WORDS = (
    len(UNORM_TRILINEAR_POSITIONS) * UNORM_TRILINEAR_FIELDS
)
UNORM_PHASE_TRILINEAR_SIDE = 4
UNORM_PHASE_TRILINEAR_POSITIONS = (
    UNORM_PHASE_TRILINEAR_SIDE**2
)
UNORM_PHASE_TRILINEAR_RECORD_WORDS = (
    UNORM_PHASE_TRILINEAR_POSITIONS * UNORM_TRILINEAR_FIELDS
)
PRODUCTION_PHASE_COUNT = 256
PRODUCTION_LOD_COUNT = 65
PRODUCTION_GRID_RECORD_COUNT = (
    PRODUCTION_PHASE_COUNT
    * PRODUCTION_PHASE_COUNT
    * PRODUCTION_LOD_COUNT
)
PRODUCTION_GRID_RECORD_WORDS = 4
LOD_EXPRESSION_RECORD_COUNT = 130
LOD_EXPRESSION_RECORD_WORDS = 4
EXPECTED_RIGS = {
    "metal-sampler-probe-1.0.0": 1,
    "metal-sampler-probe-1.1.0": 2,
    "metal-sampler-probe-1.2.0": 3,
    "metal-sampler-probe-1.3.0": 4,
    "metal-sampler-probe-1.4.0": 5,
    "metal-sampler-probe-1.5.0": 6,
    "metal-sampler-probe-1.6.0": 7,
    "metal-sampler-probe-1.7.0": 8,
}


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def half_round_ties_up(value: NDArray[np.float64]) -> HalfArray:
    """Round nonnegative exact values to half, resolving ties upward."""
    nearest_even = value.astype(np.float16)
    upward = np.nextafter(
        nearest_even,
        np.float16(np.inf),
        dtype=np.float16,
    )
    upward_midpoint = (
        nearest_even.astype(np.float64)
        + upward.astype(np.float64)
    ) / 2
    return np.where(
        value == upward_midpoint,
        upward,
        nearest_even,
    ).astype(np.float16)


def half_linear_ties_up(
    first: HalfArray,
    second: HalfArray,
    numerator: int,
    *,
    denominator: int = 256,
) -> HalfArray:
    if not 0 <= numerator <= denominator:
        raise ValueError("linear fraction is outside [0, 1]")
    exact = (
        (denominator - numerator) * first.astype(np.float64)
        + numerator * second.astype(np.float64)
    ) / denominator
    return half_round_ties_up(exact)


def rgba8_unorm_linear_ties_up(
    first_codes: NDArray[np.integer],
    second_codes: NDArray[np.integer],
    numerator: int,
    *,
    denominator: int = 256,
) -> HalfArray:
    """Reproduce Apple's fixed-point RGBA8 UNORM linear filtering."""
    if not 0 <= numerator <= denominator:
        raise ValueError("linear fraction is outside [0, 1]")
    first = np.asarray(first_codes, dtype=np.float64)
    second = np.asarray(second_codes, dtype=np.float64)
    if (
        np.any(first < 0)
        or np.any(first > 255)
        or np.any(second < 0)
        or np.any(second > 255)
    ):
        raise ValueError("RGBA8 UNORM input code is outside [0, 255]")
    exact_codes = (
        (denominator - numerator) * first
        + numerator * second
    ) / denominator
    return rgba8_unorm_exact_codes_ties_up(exact_codes)


def rgba8_unorm_exact_codes_ties_up(
    exact_codes: NDArray[np.floating],
) -> HalfArray:
    """Quantize an exact normalized-sampler result in code units."""
    exact_codes = np.asarray(exact_codes, dtype=np.float64)
    if np.any(exact_codes < 0) or np.any(exact_codes > 255):
        raise ValueError("RGBA8 UNORM code value is outside [0, 255]")
    fixed_codes = np.floor(exact_codes * 16 + 0.5) / 16
    return (fixed_codes / 255).astype(np.float16)


def rgba8_unorm_mip_ties_up(
    level_zero_codes: NDArray[np.integer],
    level_one_codes: NDArray[np.integer],
    numerator: int,
) -> HalfArray:
    """Reproduce Apple's RGBA8 UNORM trilinear mip interpolation."""
    if not 0 <= numerator <= 256:
        raise ValueError("mip fraction is outside [0, 1]")
    quantized_numerator = (numerator // 4) * 4
    return rgba8_unorm_linear_ties_up(
        level_zero_codes,
        level_one_codes,
        quantized_numerator,
    )


def mismatch_metrics(
    predicted: UInt16Array,
    measured: UInt16Array,
) -> JsonObject:
    if predicted.shape != measured.shape:
        raise ValueError("sampler comparison shapes differ")
    changed = predicted != measured
    distance = np.abs(
        predicted.astype(np.int32)
        - measured.astype(np.int32)
    )
    return {
        "values": int(changed.size),
        "mismatchedValues": int(np.count_nonzero(changed)),
        "exactValueFraction": float(np.mean(~changed)),
        "maximumBinary16BitDistance": int(distance.max(initial=0)),
    }


@dataclass(frozen=True, slots=True)
class SamplerProbe:
    manifest: JsonObject
    fixed: UInt16Array
    fraction: UInt16Array | None
    bilinear: UInt16Array | None
    unorm_bilinear: UInt16Array | None
    unorm_mip: UInt16Array | None
    unorm_trilinear: UInt16Array | None
    unorm_phase_trilinear: UInt16Array | None
    unorm_production_grid: UInt16Array | None
    unorm_production_mips: tuple[
        NDArray[np.uint8],
        NDArray[np.uint8],
    ] | None
    lod_expression: UInt16Array | None
    member_hashes: dict[str, str]

    @classmethod
    def open(cls, path: Path) -> "SamplerProbe":
        if path.is_dir():
            manifest = json.loads(
                (path / "manifest.json").read_text(encoding="utf-8")
            )

            def read(name: str) -> bytes:
                return (path / name).read_bytes()

        else:
            with zipfile.ZipFile(path) as archive:
                members = {
                    name: archive.read(name)
                    for name in archive.namelist()
                }
            try:
                manifest = json.loads(members["manifest.json"])
            except KeyError as error:
                raise ValueError(
                    "sampler probe manifest is missing"
                ) from error

            def read(name: str) -> bytes:
                try:
                    return members[name]
                except KeyError as error:
                    raise ValueError(
                        f"sampler probe member is missing: {name}"
                    ) from error

        rig = manifest.get("rigVersion")
        if rig not in EXPECTED_RIGS:
            raise ValueError(f"unexpected sampler probe rig: {rig!r}")
        schema = EXPECTED_RIGS[rig]
        if manifest.get("schemaVersion") != schema:
            raise ValueError("sampler probe schema differs")

        member_hashes: dict[str, str] = {}

        def checked_binary(
            record: JsonObject,
            *,
            file_key: str,
            bytes_key: str,
            hash_key: str,
            expected_bytes: int,
        ) -> bytes:
            name = str(record[file_key])
            value = read(name)
            if (
                len(value) != expected_bytes
                or record.get(bytes_key) != expected_bytes
            ):
                raise ValueError(
                    f"sampler probe byte count differs: {name}"
                )
            digest = sha256_bytes(value)
            if digest != record.get(hash_key):
                raise ValueError(
                    f"sampler probe hash differs: {name}"
                )
            member_hashes[name] = digest
            return value

        if (
            manifest.get("recordCount") != PAIR_COUNT
            or manifest.get("recordStrideBytes")
            != FIXED_RECORD_WORDS * 2
        ):
            raise ValueError("fixed sampler record layout differs")
        fixed_bytes = checked_binary(
            manifest,
            file_key="binaryFile",
            bytes_key="binaryFileBytes",
            hash_key="binaryFileSha256",
            expected_bytes=PAIR_COUNT * FIXED_RECORD_WORDS * 2,
        )
        fixed = np.frombuffer(
            fixed_bytes,
            dtype="<u2",
        ).reshape(PAIR_COUNT, FIXED_RECORD_WORDS)

        fraction: UInt16Array | None = None
        if schema >= 2:
            record = manifest.get("fractionGrid")
            if not isinstance(record, dict):
                raise ValueError("sampler fraction grid is missing")
            if (
                record.get("fractionCount") != FRACTION_COUNT
                or record.get("recordCount")
                != PAIR_COUNT * FRACTION_COUNT
                or record.get("recordStrideBytes")
                != FRACTION_RECORD_WORDS * 2
            ):
                raise ValueError(
                    "sampler fraction-grid layout differs"
                )
            value = checked_binary(
                record,
                file_key="file",
                bytes_key="fileBytes",
                hash_key="fileSha256",
                expected_bytes=(
                    FRACTION_COUNT
                    * PAIR_COUNT
                    * FRACTION_RECORD_WORDS
                    * 2
                ),
            )
            fraction = np.frombuffer(
                value,
                dtype="<u2",
            ).reshape(
                FRACTION_COUNT,
                PAIR_COUNT,
                FRACTION_RECORD_WORDS,
            )

        bilinear: UInt16Array | None = None
        if schema >= 3:
            record = manifest.get("bilinearGrid")
            if not isinstance(record, dict):
                raise ValueError("sampler bilinear grid is missing")
            if (
                record.get("recordCount") != PAIR_COUNT
                or record.get("recordStrideBytes")
                != BILINEAR_RECORD_WORDS * 2
            ):
                raise ValueError(
                    "sampler bilinear-grid layout differs"
                )
            value = checked_binary(
                record,
                file_key="file",
                bytes_key="fileBytes",
                hash_key="fileSha256",
                expected_bytes=(
                    PAIR_COUNT * BILINEAR_RECORD_WORDS * 2
                ),
            )
            bilinear = np.frombuffer(
                value,
                dtype="<u2",
            ).reshape(PAIR_COUNT, BILINEAR_RECORD_WORDS)

        unorm_bilinear: UInt16Array | None = None
        unorm_mip: UInt16Array | None = None
        if schema >= 4:
            record = manifest.get("unormBilinearGrid")
            if not isinstance(record, dict):
                raise ValueError(
                    "sampler unorm bilinear grid is missing"
                )
            if (
                record.get("recordCount") != PAIR_COUNT
                or record.get("recordStrideBytes")
                != BILINEAR_RECORD_WORDS * 2
            ):
                raise ValueError(
                    "sampler unorm bilinear-grid layout differs"
                )
            value = checked_binary(
                record,
                file_key="file",
                bytes_key="fileBytes",
                hash_key="fileSha256",
                expected_bytes=(
                    PAIR_COUNT * BILINEAR_RECORD_WORDS * 2
                ),
            )
            unorm_bilinear = np.frombuffer(
                value,
                dtype="<u2",
            ).reshape(PAIR_COUNT, BILINEAR_RECORD_WORDS)

            record = manifest.get("unormMipGrid")
            if not isinstance(record, dict):
                raise ValueError("sampler unorm mip grid is missing")
            if (
                record.get("fractionCount") != FRACTION_COUNT
                or record.get("recordCount")
                != PAIR_COUNT * FRACTION_COUNT
                or record.get("recordStrideBytes")
                != UNORM_MIP_RECORD_WORDS * 2
            ):
                raise ValueError(
                    "sampler unorm mip-grid layout differs"
                )
            value = checked_binary(
                record,
                file_key="file",
                bytes_key="fileBytes",
                hash_key="fileSha256",
                expected_bytes=(
                    FRACTION_COUNT
                    * PAIR_COUNT
                    * UNORM_MIP_RECORD_WORDS
                    * 2
                ),
            )
            unorm_mip = np.frombuffer(
                value,
                dtype="<u2",
            ).reshape(FRACTION_COUNT, PAIR_COUNT)

        unorm_trilinear: UInt16Array | None = None
        if schema >= 5:
            record = manifest.get("unormTrilinearGrid")
            if not isinstance(record, dict):
                raise ValueError(
                    "sampler unorm trilinear grid is missing"
                )
            positions = record.get("positionsInRecordOrder")
            if (
                record.get("recordCount") != PAIR_COUNT
                or record.get("recordStrideBytes")
                != UNORM_TRILINEAR_RECORD_WORDS * 2
                or not isinstance(positions, list)
                or len(positions) != len(UNORM_TRILINEAR_POSITIONS)
                or not all(
                    isinstance(position, dict)
                    for position in positions
                )
                or tuple(
                    position.get("name")
                    for position in positions
                ) != UNORM_TRILINEAR_POSITIONS
            ):
                raise ValueError(
                    "sampler unorm trilinear-grid layout differs"
                )
            value = checked_binary(
                record,
                file_key="file",
                bytes_key="fileBytes",
                hash_key="fileSha256",
                expected_bytes=(
                    PAIR_COUNT * UNORM_TRILINEAR_RECORD_WORDS * 2
                ),
            )
            unorm_trilinear = np.frombuffer(
                value,
                dtype="<u2",
            ).reshape(
                PAIR_COUNT,
                len(UNORM_TRILINEAR_POSITIONS),
                UNORM_TRILINEAR_FIELDS,
            )

        unorm_phase_trilinear: UInt16Array | None = None
        if schema >= 6:
            record = manifest.get("unormPhaseTrilinearGrid")
            if not isinstance(record, dict):
                raise ValueError(
                    "sampler unorm phase trilinear grid is missing"
                )
            if (
                record.get("recordCount") != PAIR_COUNT
                or record.get("recordStrideBytes")
                != UNORM_PHASE_TRILINEAR_RECORD_WORDS * 2
                or record.get("phaseOrder")
                != "phase_y major, phase_x minor"
                or record.get("levelOneFractionalPhases")
                != ["1/8", "3/8", "5/8", "7/8"]
            ):
                raise ValueError(
                    "sampler unorm phase trilinear-grid "
                    "layout differs"
                )
            value = checked_binary(
                record,
                file_key="file",
                bytes_key="fileBytes",
                hash_key="fileSha256",
                expected_bytes=(
                    PAIR_COUNT
                    * UNORM_PHASE_TRILINEAR_RECORD_WORDS
                    * 2
                ),
            )
            unorm_phase_trilinear = np.frombuffer(
                value,
                dtype="<u2",
            ).reshape(
                PAIR_COUNT,
                UNORM_PHASE_TRILINEAR_POSITIONS,
                UNORM_TRILINEAR_FIELDS,
            )

        unorm_production_grid: UInt16Array | None = None
        unorm_production_mips: tuple[
            NDArray[np.uint8],
            NDArray[np.uint8],
        ] | None = None
        if schema >= 8:
            record = manifest.get("unormProductionGrid")
            if not isinstance(record, dict):
                raise ValueError(
                    "sampler unorm production grid is missing"
                )
            source = record.get("sourceTexture")
            levels = (
                source.get("levels")
                if isinstance(source, dict)
                else None
            )
            if (
                record.get("recordCount")
                != PRODUCTION_GRID_RECORD_COUNT
                or record.get("recordStrideBytes")
                != PRODUCTION_GRID_RECORD_WORDS * 2
                or record.get("phaseCountPerAxis")
                != PRODUCTION_PHASE_COUNT
                or record.get("lodFractionCount")
                != PRODUCTION_LOD_COUNT
                or record.get("recordOrder")
                != (
                    "lod numerator major, phase_y major, "
                    "phase_x minor"
                )
                or not isinstance(source, dict)
                or source.get("width") != 448
                or source.get("height") != 448
                or source.get("mipmapLevelCount") != 2
                or not isinstance(levels, list)
                or len(levels) != 2
                or not all(
                    isinstance(level, dict)
                    for level in levels
                )
            ):
                raise ValueError(
                    "sampler unorm production-grid layout differs"
                )
            value = checked_binary(
                record,
                file_key="file",
                bytes_key="fileBytes",
                hash_key="fileSha256",
                expected_bytes=(
                    PRODUCTION_GRID_RECORD_COUNT
                    * PRODUCTION_GRID_RECORD_WORDS
                    * 2
                ),
            )
            unorm_production_grid = np.frombuffer(
                value,
                dtype="<u2",
            ).reshape(
                PRODUCTION_LOD_COUNT,
                PRODUCTION_PHASE_COUNT,
                PRODUCTION_PHASE_COUNT,
                PRODUCTION_GRID_RECORD_WORDS,
            )
            mip_arrays: list[NDArray[np.uint8]] = []
            for expected_level, level in enumerate(levels):
                if not isinstance(level, dict):
                    raise ValueError(
                        "sampler production mip record differs"
                    )
                width = 448 >> expected_level
                height = 448 >> expected_level
                if (
                    level.get("level") != expected_level
                    or level.get("width") != width
                    or level.get("height") != height
                ):
                    raise ValueError(
                        "sampler production mip layout differs"
                    )
                mip_value = checked_binary(
                    level,
                    file_key="file",
                    bytes_key="fileBytes",
                    hash_key="fileSha256",
                    expected_bytes=width * height * 4,
                )
                mip_arrays.append(
                    np.frombuffer(
                        mip_value,
                        dtype=np.uint8,
                    ).reshape(height, width, 4)
                )
            unorm_production_mips = (
                mip_arrays[0],
                mip_arrays[1],
            )

        lod_expression: UInt16Array | None = None
        if schema >= 7:
            record = manifest.get("lodExpression")
            if not isinstance(record, dict):
                raise ValueError(
                    "sampler LOD expression evidence is missing"
                )
            states = record.get("states")
            if (
                record.get("recordCount")
                != LOD_EXPRESSION_RECORD_COUNT
                or record.get("recordStrideBytes")
                != LOD_EXPRESSION_RECORD_WORDS * 2
                or not isinstance(states, list)
                or len(states) != LOD_EXPRESSION_RECORD_COUNT
            ):
                raise ValueError(
                    "sampler LOD expression layout differs"
                )
            value = checked_binary(
                record,
                file_key="file",
                bytes_key="fileBytes",
                hash_key="fileSha256",
                expected_bytes=(
                    LOD_EXPRESSION_RECORD_COUNT
                    * LOD_EXPRESSION_RECORD_WORDS
                    * 2
                ),
            )
            lod_expression = np.frombuffer(
                value,
                dtype="<u2",
            ).reshape(
                LOD_EXPRESSION_RECORD_COUNT,
                LOD_EXPRESSION_RECORD_WORDS,
            )

        return cls(
            manifest=manifest,
            fixed=fixed,
            fraction=fraction,
            bilinear=bilinear,
            unorm_bilinear=unorm_bilinear,
            unorm_mip=unorm_mip,
            unorm_trilinear=unorm_trilinear,
            unorm_phase_trilinear=unorm_phase_trilinear,
            unorm_production_grid=unorm_production_grid,
            unorm_production_mips=unorm_production_mips,
            lod_expression=lod_expression,
            member_hashes=member_hashes,
        )


def _rgba8_probe_mip_levels() -> tuple[
    NDArray[np.float64],
    NDArray[np.float64],
]:
    pair_indices = np.arange(PAIR_COUNT, dtype=np.int64)
    level_one = np.full((256, 512), 128, dtype=np.float64)
    level_one.reshape(-1)[:PAIR_COUNT] = pair_indices & 255
    input_a = (pair_indices >> 8).astype(np.float64)
    populated_level_zero = np.repeat(
        np.repeat(
            input_a.reshape(128, 512),
            2,
            axis=0,
        ),
        2,
        axis=1,
    )
    level_zero = np.full((512, 1024), 128, dtype=np.float64)
    level_zero[:256] = populated_level_zero
    return level_zero, level_one


def _bilinear_code_values(
    texture: NDArray[np.float64],
    x: NDArray[np.float64],
    y: NDArray[np.float64],
) -> NDArray[np.float64]:
    x_floor = np.floor(x).astype(np.int64)
    y_floor = np.floor(y).astype(np.int64)
    x_fraction = x - x_floor
    y_fraction = y - y_floor
    x_zero = np.clip(x_floor, 0, texture.shape[1] - 1)
    y_zero = np.clip(y_floor, 0, texture.shape[0] - 1)
    x_one = np.clip(x_floor + 1, 0, texture.shape[1] - 1)
    y_one = np.clip(y_floor + 1, 0, texture.shape[0] - 1)
    return (
        texture[y_zero, x_zero]
        * (1 - x_fraction)
        * (1 - y_fraction)
        + texture[y_zero, x_one]
        * x_fraction
        * (1 - y_fraction)
        + texture[y_one, x_zero]
        * (1 - x_fraction)
        * y_fraction
        + texture[y_one, x_one]
        * x_fraction
        * y_fraction
    )


def _summed_metrics(
    records: list[JsonObject],
) -> JsonObject:
    values = sum(int(record["values"]) for record in records)
    mismatches = sum(
        int(record["mismatchedValues"]) for record in records
    )
    return {
        "values": values,
        "mismatchedValues": mismatches,
        "exactValueFraction": 1 - mismatches / values,
        "maximumBinary16BitDistance": max(
            int(record["maximumBinary16BitDistance"])
            for record in records
        ),
    }


def _analyze_unorm_trilinear(
    measured: UInt16Array,
    positions: tuple[
        tuple[str, float, float],
        ...,
    ],
) -> JsonObject:
    if measured.shape != (
        PAIR_COUNT,
        len(positions),
        UNORM_TRILINEAR_FIELDS,
    ):
        raise ValueError("unorm trilinear measurement shape differs")
    level_zero_texture, level_one_texture = (
        _rgba8_probe_mip_levels()
    )
    pair_indices = np.arange(PAIR_COUNT, dtype=np.int64)
    origins_x = pair_indices & 511
    origins_y = pair_indices >> 9
    level_zero_metrics: list[JsonObject] = []
    level_one_metrics: list[JsonObject] = []
    fused_metrics: list[JsonObject] = []
    staged_fixed_metrics: list[JsonObject] = []
    staged_half_metrics: list[JsonObject] = []
    discriminating_values = 0
    position_records: dict[str, JsonObject] = {}
    for position_index, (name, offset_x, offset_y) in enumerate(
        positions
    ):
        level_zero_exact = _bilinear_code_values(
            level_zero_texture,
            2 * (origins_x + offset_x) - 0.5,
            2 * (origins_y + offset_y) - 0.5,
        )
        level_one_exact = _bilinear_code_values(
            level_one_texture,
            origins_x + offset_x - 0.5,
            origins_y + offset_y - 0.5,
        )
        predicted_level_zero = (
            rgba8_unorm_exact_codes_ties_up(
                level_zero_exact
            ).view(np.uint16)
        )
        predicted_level_one = (
            rgba8_unorm_exact_codes_ties_up(
                level_one_exact
            ).view(np.uint16)
        )
        fused_exact_codes = (
            108 * level_zero_exact
            + 148 * level_one_exact
        ) / 256
        predicted_fused = rgba8_unorm_exact_codes_ties_up(
            fused_exact_codes
        ).view(np.uint16)
        fixed_level_zero = (
            np.floor(level_zero_exact * 16 + 0.5) / 16
        )
        fixed_level_one = (
            np.floor(level_one_exact * 16 + 0.5) / 16
        )
        predicted_staged_fixed = (
            rgba8_unorm_exact_codes_ties_up(
                (
                    108 * fixed_level_zero
                    + 148 * fixed_level_one
                ) / 256
            ).view(np.uint16)
        )
        predicted_staged_half = half_linear_ties_up(
            predicted_level_zero.view(np.float16),
            predicted_level_one.view(np.float16),
            148,
        ).view(np.uint16)
        actual = measured[:, position_index]
        current_level_zero = mismatch_metrics(
            predicted_level_zero,
            actual[:, 0],
        )
        current_fused = mismatch_metrics(
            predicted_fused,
            actual[:, 1],
        )
        current_level_one = mismatch_metrics(
            predicted_level_one,
            actual[:, 2],
        )
        current_staged_fixed = mismatch_metrics(
            predicted_staged_fixed,
            actual[:, 1],
        )
        current_staged_half = mismatch_metrics(
            predicted_staged_half,
            actual[:, 1],
        )
        level_zero_metrics.append(current_level_zero)
        level_one_metrics.append(current_level_one)
        fused_metrics.append(current_fused)
        staged_fixed_metrics.append(current_staged_fixed)
        staged_half_metrics.append(current_staged_half)
        discriminating_values += int(
            np.count_nonzero(
                predicted_fused != predicted_staged_fixed
            )
        )
        position_records[name] = {
            "levelOneTexelOffset": [offset_x - 0.5, offset_y - 0.5],
            "levelZeroEndpoint": current_level_zero,
            "levelOneEndpoint": current_level_one,
            "fusedThreeDimensionalCodeDomain": current_fused,
            "stagedFixedEndpointInterpolation":
                current_staged_fixed,
            "stagedHalfEndpointInterpolation":
                current_staged_half,
        }
    return {
        "positions": position_records,
        "totals": {
            "levelZeroEndpoints":
                _summed_metrics(level_zero_metrics),
            "levelOneEndpoints":
                _summed_metrics(level_one_metrics),
            "fusedThreeDimensionalCodeDomain":
                _summed_metrics(fused_metrics),
            "stagedFixedEndpointInterpolation":
                _summed_metrics(staged_fixed_metrics),
            "stagedHalfEndpointInterpolation":
                _summed_metrics(staged_half_metrics),
        },
        "fusedVsStagedDiscriminatingValues":
            discriminating_values,
        "recoveredSemantics": (
            "apply the quantized spatial and LOD weights to the "
            "original 8-bit texel codes as one exact "
            "three-dimensional weighted sum, round once to 1/16 "
            "code with midpoint ties upward, divide by 255, then "
            "convert to binary16"
        ),
    }


def _production_grid_axis(
    *,
    origin: float,
) -> NDArray[np.float32]:
    phases = (
        np.arange(
            PRODUCTION_PHASE_COUNT,
            dtype=np.float32,
        )
        / np.float32(PRODUCTION_PHASE_COUNT)
    )
    return (
        (
            np.float32(origin)
            + phases
            + np.float32(0.5)
        )
        / np.float32(448)
    ).astype(np.float32)


def _production_bilinear_fixture(
    texture: NDArray[np.uint8],
    *,
    coordinates_x: NDArray[np.float32],
    coordinates_y: NDArray[np.float32],
) -> tuple[
    NDArray[np.uint64],
    NDArray[np.uint64],
    NDArray[np.uint64],
    NDArray[np.uint64],
]:
    height, width, channels = texture.shape
    if channels != 4:
        raise ValueError("production sampler mip is not RGBA")
    position_x = (
        coordinates_x * np.float32(width)
        - np.float32(0.5)
    )
    position_y = (
        coordinates_y * np.float32(height)
        - np.float32(0.5)
    )
    origin_x = np.floor(position_x).astype(np.int64)
    origin_y = np.floor(position_y).astype(np.int64)
    fraction_x = position_x - np.floor(position_x)
    fraction_y = position_y - np.floor(position_y)
    weight_x = np.floor(
        fraction_x * np.float32(256)
        + np.float32(0.5)
    ).astype(np.uint64)
    weight_y = np.floor(
        fraction_y * np.float32(256)
        + np.float32(0.5)
    ).astype(np.uint64)
    inverse_x = 256 - weight_x
    inverse_y = 256 - weight_y
    x_zero = np.clip(origin_x, 0, width - 1)
    y_zero = np.clip(origin_y, 0, height - 1)
    x_one = np.clip(origin_x + 1, 0, width - 1)
    y_one = np.clip(origin_y + 1, 0, height - 1)
    codes = texture.astype(np.uint64)
    texel_00 = codes[y_zero[:, None], x_zero[None, :]]
    texel_10 = codes[y_zero[:, None], x_one[None, :]]
    texel_01 = codes[y_one[:, None], x_zero[None, :]]
    texel_11 = codes[y_one[:, None], x_one[None, :]]
    spatial_weights = np.stack(
        (
            inverse_y[:, None] * inverse_x[None, :],
            inverse_y[:, None] * weight_x[None, :],
            weight_y[:, None] * inverse_x[None, :],
            weight_y[:, None] * weight_x[None, :],
        ),
        axis=2,
    )
    texel_codes = np.stack(
        (
            texel_00,
            texel_10,
            texel_01,
            texel_11,
        ),
        axis=2,
    )
    return spatial_weights, texel_codes, weight_x, weight_y


def _production_bilinear_code_sum(
    texture: NDArray[np.uint8],
    *,
    coordinates_x: NDArray[np.float32],
    coordinates_y: NDArray[np.float32],
) -> tuple[
    NDArray[np.uint64],
    NDArray[np.uint64],
    NDArray[np.uint64],
]:
    spatial_weights, texel_codes, weight_x, weight_y = (
        _production_bilinear_fixture(
            texture,
            coordinates_x=coordinates_x,
            coordinates_y=coordinates_y,
        )
    )
    sum_2d = (
        spatial_weights[..., None] * texel_codes
    ).sum(axis=2)
    return sum_2d, weight_x, weight_y


def _analyze_unorm_production_grid(
    measured: UInt16Array,
    mip_levels: tuple[
        NDArray[np.uint8],
        NDArray[np.uint8],
    ],
) -> JsonObject:
    expected_shape = (
        PRODUCTION_LOD_COUNT,
        PRODUCTION_PHASE_COUNT,
        PRODUCTION_PHASE_COUNT,
        PRODUCTION_GRID_RECORD_WORDS,
    )
    if measured.shape != expected_shape:
        raise ValueError(
            "unorm production-grid measurement shape differs"
        )
    coordinates_x = _production_grid_axis(origin=137)
    coordinates_y = _production_grid_axis(origin=193)
    (
        level_zero_weights,
        level_zero_codes,
        level_zero_x,
        level_zero_y,
    ) = (
        _production_bilinear_fixture(
            mip_levels[0],
            coordinates_x=coordinates_x,
            coordinates_y=coordinates_y,
        )
    )
    (
        level_one_weights,
        level_one_codes,
        level_one_x,
        level_one_y,
    ) = (
        _production_bilinear_fixture(
            mip_levels[1],
            coordinates_x=coordinates_x,
            coordinates_y=coordinates_y,
        )
    )
    level_zero = (
        level_zero_weights[..., None]
        * level_zero_codes
    ).sum(axis=2)
    level_one = (
        level_one_weights[..., None]
        * level_one_codes
    ).sum(axis=2)
    texel_codes = np.concatenate(
        (level_zero_codes, level_one_codes),
        axis=2,
    )
    upper_row_tie = np.asarray(
        (
            True,
            True,
            False,
            False,
            True,
            True,
            False,
            False,
        ),
        dtype=np.bool_,
    )

    lod_records: list[JsonObject] = []
    total_values = 0
    total_mismatches = 0
    maximum_distance = 0
    mismatched_states = 0
    unbounded_mismatches = 0
    unbounded_maximum_distance = 0
    unbounded_mismatched_states = 0
    first_mismatches: list[JsonObject] = []
    for lod_numerator in range(PRODUCTION_LOD_COUNT):
        unbounded_combined = (
            level_zero * (64 - lod_numerator)
            + level_one * lod_numerator
        )
        unbounded_fixed_sixteenths = (
            unbounded_combined + np.uint64(131_072)
        ) // np.uint64(262_144)
        unbounded_predicted = (
            unbounded_fixed_sixteenths.astype(np.float64)
            / 4080
        ).astype(np.float16).view(np.uint16)

        raw_trilinear_weights = np.concatenate(
            (
                level_zero_weights
                * (64 - lod_numerator),
                level_one_weights * lod_numerator,
            ),
            axis=2,
        )
        weight_quotient = (
            raw_trilinear_weights // 64
        )
        weight_remainder = (
            raw_trilinear_weights % 64
        )
        q016_weights = (
            weight_quotient
            + (weight_remainder > 32)
            + (
                (weight_remainder == 32)
                & upper_row_tie
            )
        )
        weighted_codes = (
            q016_weights[..., None] * texel_codes
        ).sum(axis=2)
        fixed_sixteenths = (
            weighted_codes + np.uint64(2_048)
        ) // np.uint64(4_096)
        predicted = (
            fixed_sixteenths.astype(np.float64)
            / 4080
        ).astype(np.float16).view(np.uint16)

        actual = measured[lod_numerator]
        current = mismatch_metrics(predicted, actual)
        unbounded_current = mismatch_metrics(
            unbounded_predicted,
            actual,
        )
        changed = predicted != actual
        state_changed = np.any(changed, axis=2)
        unbounded_state_changed = np.any(
            unbounded_predicted != actual,
            axis=2,
        )
        current["mismatchedSpatialStates"] = int(
            np.count_nonzero(state_changed)
        )
        current["unbounded3DIntegerModel"] = (
            unbounded_current
        )
        lod_records.append(current)
        total_values += int(current["values"])
        total_mismatches += int(
            current["mismatchedValues"]
        )
        maximum_distance = max(
            maximum_distance,
            int(current["maximumBinary16BitDistance"]),
        )
        mismatched_states += int(
            current["mismatchedSpatialStates"]
        )
        unbounded_mismatches += int(
            unbounded_current["mismatchedValues"]
        )
        unbounded_maximum_distance = max(
            unbounded_maximum_distance,
            int(
                unbounded_current[
                    "maximumBinary16BitDistance"
                ]
            ),
        )
        unbounded_mismatched_states += int(
            np.count_nonzero(unbounded_state_changed)
        )
        if len(first_mismatches) < 16:
            for phase_y, phase_x, channel in np.argwhere(
                changed
            ):
                first_mismatches.append({
                    "lodNumerator64": lod_numerator,
                    "phaseX256": int(phase_x),
                    "phaseY256": int(phase_y),
                    "channel": int(channel),
                    "predictedBinary16Bits":
                        f"{int(predicted[
                            phase_y,
                            phase_x,
                            channel
                        ]):04x}",
                    "measuredBinary16Bits":
                        f"{int(actual[
                            phase_y,
                            phase_x,
                            channel
                        ]):04x}",
                })
                if len(first_mismatches) == 16:
                    break
    return {
        "q016RowDirectedTieModel": {
            "values": total_values,
            "mismatchedValues": total_mismatches,
            "exactValueFraction":
                1 - total_mismatches / total_values,
            "maximumBinary16BitDistance":
                maximum_distance,
            "mismatchedSpatialStatesByLodSum":
                mismatched_states,
        },
        "unbounded3DIntegerModel": {
            "values": total_values,
            "mismatchedValues": unbounded_mismatches,
            "exactValueFraction":
                1 - unbounded_mismatches / total_values,
            "maximumBinary16BitDistance":
                unbounded_maximum_distance,
            "mismatchedSpatialStatesByLodSum":
                unbounded_mismatched_states,
        },
        "perLodNumerator64": lod_records,
        "firstMismatches": first_mismatches,
        "derivedSpatialWeights": {
            "levelZeroX": level_zero_x.tolist(),
            "levelZeroY": level_zero_y.tolist(),
            "levelOneX": level_one_x.tolist(),
            "levelOneY": level_one_y.tolist(),
        },
        "testedStateCount": PRODUCTION_GRID_RECORD_COUNT,
        "testedValueCount": (
            PRODUCTION_GRID_RECORD_COUNT
            * PRODUCTION_GRID_RECORD_WORDS
        ),
        "model": (
            "round each float32 normalized-coordinate phase to "
            "the nearest 1/256 spatial weight; form eight "
            "22-bit trilinear corner weights; reduce each to "
            "Q0.16 with nearest rounding, directing exact "
            "half-way ties upward for the upper texel row and "
            "downward for the lower row; dot those weights with "
            "the original RGBA8 codes; round once to 1/16 code "
            "with midpoint ties upward; divide by 255; then "
            "convert to binary16"
        ),
    }


def analyze(path: Path) -> JsonObject:
    started = time.perf_counter()
    probe = SamplerProbe.open(path)
    codes = np.arange(256, dtype=np.float32)
    half_codes = (codes / np.float32(255)).astype(np.float16)
    input_a = np.repeat(half_codes, 256)
    input_b = np.tile(half_codes, 256)
    expected_a_codes = np.repeat(
        np.arange(256, dtype=np.uint16),
        256,
    )
    expected_b_codes = np.tile(
        np.arange(256, dtype=np.uint16),
        256,
    )
    input_order_exact = bool(
        np.array_equal(probe.fixed[:, 0], expected_a_codes)
        and np.array_equal(probe.fixed[:, 1], expected_b_codes)
    )
    endpoint_metrics = {
        "levelZero": mismatch_metrics(
            input_a.view(np.uint16),
            probe.fixed[:, 6],
        ),
        "levelOne": mismatch_metrics(
            input_b.view(np.uint16),
            probe.fixed[:, 7],
        ),
    }

    fixed_results = {}
    for name, numerator, column in (
        ("quarter", 64, 2),
        ("half", 128, 3),
        ("threeQuarter", 192, 4),
    ):
        predicted = half_linear_ties_up(
            input_a,
            input_b,
            numerator,
        )
        fixed_results[name] = mismatch_metrics(
            predicted.view(np.uint16),
            probe.fixed[:, column],
        )

    fraction_result: JsonObject | None = None
    if probe.fraction is not None:
        half_mismatches = 0
        half_nearest_even_mismatches = 0
        maximum_distance = 0
        format_mismatches = 0
        maximum_format_absolute = 0.0
        unorm_mismatches = 0
        unorm_nearest_even_mismatches = 0
        unorm_maximum_distance = 0
        for numerator in range(FRACTION_COUNT):
            exact = (
                (256 - numerator) * input_a.astype(np.float64)
                + numerator * input_b.astype(np.float64)
            ) / 256
            predicted = half_round_ties_up(exact).view(np.uint16)
            nearest_even = exact.astype(np.float16).view(np.uint16)
            measured = probe.fraction[numerator, :, 0]
            distance = np.abs(
                predicted.astype(np.int32)
                - measured.astype(np.int32)
            )
            half_mismatches += int(
                np.count_nonzero(predicted != measured)
            )
            half_nearest_even_mismatches += int(
                np.count_nonzero(nearest_even != measured)
            )
            maximum_distance = max(
                maximum_distance,
                int(distance.max(initial=0)),
            )
            unorm = probe.fraction[numerator, :, 1]
            format_mismatches += int(
                np.count_nonzero(measured != unorm)
            )
            unorm_predicted = rgba8_unorm_linear_ties_up(
                expected_a_codes,
                expected_b_codes,
                numerator,
            ).view(np.uint16)
            unorm_distance = np.abs(
                unorm_predicted.astype(np.int32)
                - unorm.astype(np.int32)
            )
            unorm_mismatches += int(
                np.count_nonzero(unorm_predicted != unorm)
            )
            unorm_maximum_distance = max(
                unorm_maximum_distance,
                int(unorm_distance.max(initial=0)),
            )
            exact_codes = (
                (256 - numerator)
                * expected_a_codes.astype(np.float64)
                + numerator
                * expected_b_codes.astype(np.float64)
            ) / 256
            nearest_fixed_codes = (
                np.rint(exact_codes * 16) / 16
            )
            unorm_nearest_even = (
                nearest_fixed_codes / 255
            ).astype(np.float16).view(np.uint16)
            unorm_nearest_even_mismatches += int(
                np.count_nonzero(unorm_nearest_even != unorm)
            )
            numeric_difference = np.abs(
                measured.view(np.float16).astype(np.float32)
                - unorm.view(np.float16).astype(np.float32)
            )
            maximum_format_absolute = max(
                maximum_format_absolute,
                float(numeric_difference.max(initial=0)),
            )

        mip_mismatches = np.count_nonzero(
            probe.fraction[:, :, 0]
            != probe.fixed[None, :, 5],
            axis=1,
        )
        matching_rows = np.flatnonzero(
            mip_mismatches == 0
        ).astype(int)
        symmetry_mismatches = int(np.count_nonzero(
            probe.fraction[:, :, 0].reshape(
                FRACTION_COUNT,
                256,
                256,
            )
            != probe.fraction[
                ::-1,
                :,
                0,
            ].reshape(
                FRACTION_COUNT,
                256,
                256,
            ).transpose(0, 2, 1)
        ))
        fraction_result = {
            "values": FRACTION_COUNT * PAIR_COUNT,
            "halfFloatExactWeightedTiesUp": {
                "mismatchedValues": half_mismatches,
                "maximumBinary16BitDistance":
                    maximum_distance,
            },
            "halfFloatNearestEven": {
                "mismatchedValues":
                    half_nearest_even_mismatches,
            },
            "pairDirectionSymmetryMismatchedValues":
                symmetry_mismatches,
            "halfFloatVsRgba8Unorm": {
                "mismatchedValues": format_mismatches,
                "maximumAbsoluteNumericDifference":
                    maximum_format_absolute,
            },
            "rgba8UnormFixedSixteenthCodeTiesUp": {
                "mismatchedValues": unorm_mismatches,
                "maximumBinary16BitDistance":
                    unorm_maximum_distance,
            },
            "rgba8UnormFixedSixteenthCodeNearestEven": {
                "mismatchedValues":
                    unorm_nearest_even_mismatches,
            },
            "radiusOneMip": {
                "exactFractionRows256":
                    matching_rows.tolist(),
                "uniqueExactFraction": (
                    int(matching_rows[0])
                    if matching_rows.size == 1
                    else None
                ),
                "recoveredWeight": (
                    f"{int(matching_rows[0])}/256 = "
                    f"{int(matching_rows[0] // 4)}/64"
                    if (
                        matching_rows.size == 1
                        and matching_rows[0] % 4 == 0
                    )
                    else None
                ),
                "nearestAlternativeMismatchedValues": int(
                    np.partition(mip_mismatches, 1)[1]
                ),
            },
        }

    bilinear_result: JsonObject | None = None
    if probe.bilinear is not None:
        records = {}
        for column, numerator in enumerate((16, 48, 144)):
            predicted = half_linear_ties_up(
                input_b,
                input_a,
                numerator,
            )
            records[f"{numerator}/256"] = mismatch_metrics(
                predicted.view(np.uint16),
                probe.bilinear[:, column],
            )
        bilinear_result = {
            "inputAWeights": records,
            "recoveredSemantics": (
                "one exact four-texel weighted sum followed by "
                "binary16 rounding with midpoint ties upward"
            ),
        }

    unorm_bilinear_result: JsonObject | None = None
    if probe.unorm_bilinear is not None:
        records = {}
        for column, numerator in enumerate((16, 48, 144)):
            predicted = rgba8_unorm_linear_ties_up(
                expected_b_codes,
                expected_a_codes,
                numerator,
            )
            records[f"{numerator}/256"] = mismatch_metrics(
                predicted.view(np.uint16),
                probe.unorm_bilinear[:, column],
            )
        unorm_bilinear_result = {
            "inputAWeights": records,
            "recoveredSemantics": (
                "form one exact four-texel weighted value in "
                "8-bit code units, round once to 1/16 code with "
                "midpoint ties upward, divide by 255, then convert "
                "to binary16"
            ),
        }

    unorm_mip_result: JsonObject | None = None
    if probe.unorm_mip is not None:
        exact_mismatches = 0
        exact_maximum_distance = 0
        unquantized_mismatches = 0
        nearest_mismatches = 0
        repeated_row_mismatches = 0
        for numerator in range(FRACTION_COUNT):
            measured = probe.unorm_mip[numerator]
            predicted = rgba8_unorm_mip_ties_up(
                expected_a_codes,
                expected_b_codes,
                numerator,
            ).view(np.uint16)
            distance = np.abs(
                predicted.astype(np.int32)
                - measured.astype(np.int32)
            )
            exact_mismatches += int(
                np.count_nonzero(predicted != measured)
            )
            exact_maximum_distance = max(
                exact_maximum_distance,
                int(distance.max(initial=0)),
            )
            unquantized = rgba8_unorm_linear_ties_up(
                expected_a_codes,
                expected_b_codes,
                numerator,
            ).view(np.uint16)
            unquantized_mismatches += int(
                np.count_nonzero(unquantized != measured)
            )
            nearest_numerator = min(
                256,
                ((numerator + 2) // 4) * 4,
            )
            nearest = rgba8_unorm_linear_ties_up(
                expected_a_codes,
                expected_b_codes,
                nearest_numerator,
            ).view(np.uint16)
            nearest_mismatches += int(
                np.count_nonzero(nearest != measured)
            )
            quantized_row = (numerator // 4) * 4
            repeated_row_mismatches += int(
                np.count_nonzero(
                    measured != probe.unorm_mip[quantized_row]
                )
            )
        unorm_mip_result = {
            "values": FRACTION_COUNT * PAIR_COUNT,
            "floorToOneSixtyFourthThenFixedSixteenthCodeTiesUp": {
                "mismatchedValues": exact_mismatches,
                "maximumBinary16BitDistance":
                    exact_maximum_distance,
            },
            "withoutLodQuantization": {
                "mismatchedValues": unquantized_mismatches,
            },
            "nearestOneSixtyFourthLod": {
                "mismatchedValues": nearest_mismatches,
            },
            "measuredRowsVsFlooredFractionRow": {
                "mismatchedValues": repeated_row_mismatches,
            },
            "recoveredLodFractionCount": 65,
            "recoveredLodFractions":
                "floor(input_fraction * 64) / 64",
            "recoveredSemantics": (
                "floor the nonnegative LOD fraction to 1/64, "
                "form the exact mip-endpoint weighted value in "
                "8-bit code units, round once to 1/16 code with "
                "midpoint ties upward, divide by 255, then convert "
                "to binary16"
            ),
        }

    unorm_trilinear_result: JsonObject | None = None
    if probe.unorm_trilinear is not None:
        unorm_trilinear_result = _analyze_unorm_trilinear(
            probe.unorm_trilinear,
            (
                ("center", 0.5, 0.5),
                ("x-quarter", 0.75, 0.5),
                ("x-half", 1.0, 0.5),
                ("x-three-quarter", 1.25, 0.5),
                ("y-quarter", 0.5, 0.75),
                ("y-half", 0.5, 1.0),
                ("y-three-quarter", 0.5, 1.25),
            ),
        )

    unorm_phase_trilinear_result: JsonObject | None = None
    if probe.unorm_phase_trilinear is not None:
        phase_offsets = (0.625, 0.875, 1.125, 1.375)
        unorm_phase_trilinear_result = (
            _analyze_unorm_trilinear(
                probe.unorm_phase_trilinear,
                tuple(
                    (
                        f"phase-{phase_y}-{phase_x}",
                        offset_x,
                        offset_y,
                    )
                    for phase_y, offset_y in enumerate(
                        phase_offsets
                    )
                    for phase_x, offset_x in enumerate(
                        phase_offsets
                    )
                ),
            )
        )

    unorm_production_grid_result: JsonObject | None = None
    if (
        probe.unorm_production_grid is not None
        and probe.unorm_production_mips is not None
    ):
        unorm_production_grid_result = (
            _analyze_unorm_production_grid(
                probe.unorm_production_grid,
                probe.unorm_production_mips,
            )
        )

    lod_expression_result: JsonObject | None = None
    if probe.lod_expression is not None:
        states = probe.manifest["lodExpression"]["states"]
        measured_radius_bits = (
            probe.lod_expression[:, 0].astype(np.uint32)
            | (
                probe.lod_expression[:, 1].astype(np.uint32)
                << 16
            )
        )
        expected_radius_bits = np.asarray(
            [
                int(
                    state["requestedBlurRadiusFloat32Bits"],
                    16,
                )
                for state in states
            ],
            dtype=np.uint32,
        )
        argument_bits = probe.lod_expression[:, 2]
        lod_bits = probe.lod_expression[:, 3]
        lod_values = lod_bits.view(np.float16).astype(np.float64)
        effective_bins = np.floor(
            lod_values * 64
        ).astype(np.int64)
        target_bins = np.asarray(
            [
                int(state["targetLodNumerator"])
                for state in states
            ],
            dtype=np.int64,
        )
        radius_mismatches = int(np.count_nonzero(
            measured_radius_bits != expected_radius_bits
        ))
        bin_mismatches = int(np.count_nonzero(
            effective_bins != target_bins
        ))
        records = [
            {
                "index": index,
                "name": state["name"],
                "targetLodNumerator64":
                    int(target_bins[index]),
                "radiusFloat32Bits":
                    f"{int(measured_radius_bits[index]):08x}",
                "argumentBinary16Bits":
                    f"{int(argument_bits[index]):04x}",
                "lodBinary16Bits":
                    f"{int(lod_bits[index]):04x}",
                "lodValue": float(lod_values[index]),
                "effectiveFloorLodNumerator64":
                    int(effective_bins[index]),
            }
            for index, state in enumerate(states)
        ]
        production = records[-1]
        grid_37 = records[37]
        lod_expression_result = {
            "records": records,
            "radiusInputBitMismatches": radius_mismatches,
            "targetFloorBinMismatches": bin_mismatches,
            "allRadiusInputsExact": radius_mismatches == 0,
            "allTargetFloorBinsExact": bin_mismatches == 0,
            "productionBlurOne": production,
            "gridBin37": grid_37,
            "productionAndGridShareFloorBin": (
                production["effectiveFloorLodNumerator64"]
                == grid_37["effectiveFloorLodNumerator64"]
            ),
            "productionAndGridHalfLodBitsEqual": (
                production["lodBinary16Bits"]
                == grid_37["lodBinary16Bits"]
            ),
        }

    elapsed = time.perf_counter() - started
    sampler_exact = (
        all(
            record["mismatchedValues"] == 0
            for record in fixed_results.values()
        )
        and (
            fraction_result is None
            or (
                fraction_result[
                    "halfFloatExactWeightedTiesUp"
                ]["mismatchedValues"] == 0
                and fraction_result[
                    "rgba8UnormFixedSixteenthCodeTiesUp"
                ]["mismatchedValues"] == 0
            )
        )
        and (
            bilinear_result is None
            or all(
                record["mismatchedValues"] == 0
                for record in bilinear_result[
                    "inputAWeights"
                ].values()
            )
        )
        and (
            unorm_bilinear_result is None
            or all(
                record["mismatchedValues"] == 0
                for record in unorm_bilinear_result[
                    "inputAWeights"
                ].values()
            )
        )
        and (
            unorm_mip_result is None
            or unorm_mip_result[
                "floorToOneSixtyFourthThenFixedSixteenthCodeTiesUp"
            ]["mismatchedValues"] == 0
        )
        and (
            unorm_trilinear_result is None
            or unorm_trilinear_result["totals"][
                "fusedThreeDimensionalCodeDomain"
            ]["mismatchedValues"] == 0
        )
        and (
            unorm_phase_trilinear_result is None
            or unorm_phase_trilinear_result["totals"][
                "fusedThreeDimensionalCodeDomain"
            ]["mismatchedValues"] == 0
        )
        and (
            unorm_production_grid_result is None
            or unorm_production_grid_result[
                "q016RowDirectedTieModel"
            ]["mismatchedValues"] == 0
        )
        and (
            lod_expression_result is None
            or (
                lod_expression_result[
                    "allRadiusInputsExact"
                ]
                and lod_expression_result[
                    "allTargetFloorBinsExact"
                ]
            )
        )
    )
    return {
        "liquidGlassSamplerProbeAnalysisSchemaVersion": 3,
        "analysisImplementation": {
            "file": "analysis/liquid_glass_sampler_probe.py",
            "sha256": sha256_file(Path(__file__).resolve()),
            "python": platform.python_version(),
            "numpy": np.__version__,
        },
        "source": {
            "path": str(path),
            "sha256": sha256_file(path) if path.is_file() else None,
            "rigVersion": probe.manifest["rigVersion"],
            "ciCommit": probe.manifest["ciCommit"],
            "osVersion": probe.manifest["osVersion"],
            "device": probe.manifest["device"],
            "memberSha256": probe.member_hashes,
        },
        "validation": {
            "inputOrderExact": input_order_exact,
            "endpointConversions": endpoint_metrics,
        },
        "linearSampler": {
            "fixedFractions": fixed_results,
            "fractionGrid": fraction_result,
            "bilinearGrid": bilinear_result,
            "rgba8UnormBilinearGrid":
                unorm_bilinear_result,
            "rgba8UnormMipGrid": unorm_mip_result,
            "rgba8UnormTrilinearGrid":
                unorm_trilinear_result,
            "rgba8UnormPhaseTrilinearGrid":
                unorm_phase_trilinear_result,
            "rgba8UnormProductionGrid":
                unorm_production_grid_result,
            "lodExpression": lod_expression_result,
        },
        "recoveredSemantics": {
            "halfTextureInterpolation": (
                "form the exact weighted value at the sampler's "
                "quantized fraction, then round once to binary16; "
                "an exact midpoint rounds toward +infinity"
            ),
            "rgba8UnormTextureInterpolation": (
                "form the exact weighted value in 8-bit code units, "
                "round once to 1/16-code fixed point with midpoint "
                "ties upward, divide by 255, then convert to binary16"
            ),
            "rgba8UnormMipInterpolation": (
                "floor the nonnegative LOD fraction to 1/64, then "
                "form eight trilinear corner weights and reduce "
                "them to Q0.16; exact reduction ties round upward "
                "for the upper texel row and downward for the "
                "lower row; dot the normalized weights with the "
                "original texel codes before the single "
                "fixed-sixteenth-code rounding step"
            ),
            "radiusOneLodFraction": "37/64",
        },
        "resourceMeasurements": {
            "analysisSeconds": elapsed,
            "maximumResidentSetKiB":
                resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        },
        "conclusion": {
            "appleHalfSamplerArithmeticBitExact":
                bool(sampler_exact),
            "productionShaderAuthorized": False,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate Apple Metal sampler evidence."
    )
    parser.add_argument("sampler_probe", type=Path)
    parser.add_argument("-o", "--output", type=Path)
    arguments = parser.parse_args()
    result = analyze(arguments.sampler_probe)
    encoded = json.dumps(
        result,
        indent=2,
        sort_keys=True,
        allow_nan=False,
    ) + "\n"
    if arguments.output is None:
        print(encoded, end="")
    else:
        arguments.output.write_text(
            encoded,
            encoding="utf-8",
        )
        print(arguments.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
