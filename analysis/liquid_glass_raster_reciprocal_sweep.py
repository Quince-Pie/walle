#!/usr/bin/env python3
"""Recover Apple's hidden 25-bit raster reciprocal over one sweep partition."""

import argparse
import hashlib
import json
import struct
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

import liquid_glass_raster_quotient_corpus as corpus


type JsonObject = dict[str, Any]
type UIntArray = NDArray[np.uint32]

SCHEMA_VERSION = 1
RIG_VERSIONS_BY_ROLE = {
    "discovery": {
        "metal-raster-reciprocal-sweep-1.0.0",
        "metal-raster-reciprocal-sweep-1.1.0",
    },
    "holdout": {"metal-raster-reciprocal-sweep-1.1.0"},
}
WIDTH_LOWER = 128
WIDTH_UPPER = 16_384
TARGET_WIDTH = 160
TARGET_HEIGHT = 160
VIEWPORT_WIDTH = 32_768
ORIGIN_X = 17
ORIGIN_Y = 19
GEOMETRY_HEIGHT = 64
EDGE_AREA_MARGIN = 512
PRIMITIVE_COUNT = 2
TILE_COUNT = 5
PULL_COUNT = 2
CANDIDATE_RADIUS = 8
SENTINEL = np.uint64(0xFFFF_FFFF_FFFF_FFFF)
DISCOVERY_WIDTH_COUNT = 14_181
HOLDOUT_WIDTH_COUNT = 2_076
DISCOVERY_WIDTHS_SHA256 = (
    "865bff07b8ca4e440f7d1cc20bb6ec98f1bacee2ee780d85c53e54efcaccabff"
)
HOLDOUT_WIDTHS_SHA256 = (
    "ddda2c54ca06291eb8cbfeacacab3767c1358ed4d1cf0b14bfec805ad93c30ea"
)
HOLDOUT_OPENING_SHA256 = (
    "4f21f366543c0bd0e1c1d8eb5dec6f74045c6861a8c1a6774b3d6f9ae26ebbe4"
)
SIGNIFICAND_SHA256 = "2220ec200ebb378e3d315839e2ef59e4192a41d76d08fffebe84c5a03ad8258a"
DELTA_BITS_SHA256 = "4af6fce64ad188beb784cbea16c1d09ca2713825f8becee8ee64cabfd68caf8a"
SOURCE_TRUTH_SHA256 = (
    "069c044c3b38d0535656c0a6e4d12c07a80a2b9b528ae4eb80c4735381c2469a"
)
PRODUCTION_HOLDOUT_WIDTHS = (
    640,
    800,
    976,
    1_280,
    1_440,
    1_600,
    1_920,
    2_160,
    2_560,
    2_880,
    3_200,
    3_440,
    3_840,
    4_096,
    4_320,
    5_120,
    5_760,
    7_680,
    8_192,
    10_240,
    11_520,
    15_360,
    16_384,
)
WITNESS_SIGNIFICANDS = np.asarray(
    (
        12_310_539,
        10_561_315,
        8_936_464,
        8_393_727,
        16_724_323,
        8_393_489,
        16_276_106,
        8_393_693,
        16_450_452,
        15_671_128,
        9_479_541,
        16_747_356,
        12_063_463,
        8_393_506,
    ),
    dtype=np.uint64,
)


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def uint32_sha256(values: list[int] | tuple[int, ...]) -> str:
    return hashlib.sha256(
        b"".join(struct.pack("<I", value) for value in values)
    ).hexdigest()


def normalization_class(width: int) -> int:
    return width << (15 - width.bit_length())


PRODUCTION_HOLDOUT_CLASSES = frozenset(
    normalization_class(width) for width in PRODUCTION_HOLDOUT_WIDTHS
)


def is_holdout_width(width: int) -> bool:
    normalized = normalization_class(width)
    hashed = (normalized * 0x9E37_79B1) & 0xFFFF_FFFF
    return (hashed >> 29) == 0 or normalized in PRODUCTION_HOLDOUT_CLASSES


def selected_widths(*, holdout: bool) -> list[int]:
    return [
        width
        for width in range(WIDTH_LOWER, WIDTH_UPPER + 1)
        if is_holdout_width(width) is holdout
    ]


def expected_positions(width: int) -> list[JsonObject]:
    last_tile = min(
        (ORIGIN_X + width - 1) // 32,
        (TARGET_WIDTH - 1) // 32,
    )
    positions: list[JsonObject] = []
    for primitive in range(PRIMITIVE_COUNT):
        for tile in range(ORIGIN_X // 32, last_tile + 1):
            lower = max(ORIGIN_X, tile * 32) - ORIGIN_X
            upper = min(ORIGIN_X + width - 1, tile * 32 + 31) - ORIGIN_X
            local_x = upper if primitive == 0 else lower
            signed_interior = (
                GEOMETRY_HEIGHT * (2 * local_x + 1) - width
                if primitive == 0
                else (2 * GEOMETRY_HEIGHT - 1) * width
                - GEOMETRY_HEIGHT * (2 * local_x + 1)
            )
            if signed_interior > EDGE_AREA_MARGIN:
                positions.append(
                    {
                        "primitive": primitive,
                        "tile": tile,
                        "x": ORIGIN_X + local_x,
                        "y": (
                            ORIGIN_Y + GEOMETRY_HEIGHT - 1
                            if primitive == 0
                            else ORIGIN_Y
                        ),
                    }
                )
    return positions


def nearest_even_reciprocal_index(width: int) -> int:
    exponent = -(width - 1).bit_length()
    return corpus.round_integer_nearest_even(1 << (24 - exponent), width)


def validate_inputs(
    root: Path,
    preregistration_path: Path,
) -> tuple[JsonObject, JsonObject, Path, list[int], str]:
    preregistration: JsonObject = json.loads(
        preregistration_path.read_text(encoding="utf-8")
    )
    manifest: JsonObject = json.loads(
        (root / "manifest.json").read_text(encoding="utf-8")
    )
    evidence = manifest.get("reciprocalSweep", {})
    if not isinstance(evidence, dict):
        raise ValueError("reciprocal sweep evidence is not an object")
    role = evidence.get("role")
    if role not in RIG_VERSIONS_BY_ROLE:
        raise ValueError("reciprocal sweep role differs")
    rig_version = manifest.get("rigVersion")
    discovery = selected_widths(holdout=False)
    holdout = selected_widths(holdout=True)
    widths = holdout if role == "holdout" else discovery
    expected_width_count = (
        HOLDOUT_WIDTH_COUNT if role == "holdout" else DISCOVERY_WIDTH_COUNT
    )
    expected_widths_sha256 = (
        HOLDOUT_WIDTHS_SHA256
        if role == "holdout"
        else DISCOVERY_WIDTHS_SHA256
    )
    path = root / str(evidence.get("file", ""))
    expected_bytes = (
        len(widths)
        * WITNESS_SIGNIFICANDS.size
        * PRIMITIVE_COUNT
        * TILE_COUNT
        * PULL_COUNT
        * np.dtype("<u4").itemsize
    )
    opening_path = preregistration_path.with_name(
        "raster_reciprocal_holdout_opening.json"
    )
    if role == "holdout":
        if (
            not opening_path.is_file()
            or sha256_path(opening_path) != HOLDOUT_OPENING_SHA256
            or evidence.get("holdoutOpeningAuthorized") is not True
            or evidence.get("holdoutOpeningFile")
            != "Analysis/raster_reciprocal_holdout_opening.json"
            or evidence.get("holdoutOpeningSha256")
            != HOLDOUT_OPENING_SHA256
        ):
            raise ValueError("reciprocal holdout opening differs")
    elif any(
        key in evidence
        for key in (
            "holdoutOpeningAuthorized",
            "holdoutOpeningFile",
            "holdoutOpeningSha256",
        )
    ):
        raise ValueError("discovery evidence contains holdout authorization")
    if (
        preregistration.get("schemaVersion") != 1
        or preregistration.get("role") != "reciprocal-index-discovery"
        or preregistration.get("discoveryObservedAtPreregistration") is not False
        or preregistration.get("holdoutObservedAtPreregistration") is not False
        or manifest.get("schemaVersion") != SCHEMA_VERSION
        or rig_version not in RIG_VERSIONS_BY_ROLE[role]
        or evidence.get("widths") != widths
        or evidence.get("widthCount") != expected_width_count
        or evidence.get("widthsSha256") != expected_widths_sha256
        or evidence.get("holdoutWidthCount") != HOLDOUT_WIDTH_COUNT
        or evidence.get("holdoutWidthsSha256") != HOLDOUT_WIDTHS_SHA256
        or (
            rig_version == "metal-raster-reciprocal-sweep-1.1.0"
            and (
                evidence.get("discoveryWidthCount") != DISCOVERY_WIDTH_COUNT
                or evidence.get("discoveryWidthsSha256")
                != DISCOVERY_WIDTHS_SHA256
            )
        )
        or evidence.get("witnessSignificands")
        != WITNESS_SIGNIFICANDS.astype(int).tolist()
        or evidence.get("witnessCount") != WITNESS_SIGNIFICANDS.size
        or evidence.get("witnessSignificandsSha256") != SIGNIFICAND_SHA256
        or evidence.get("deltaFloatBitsSha256") != DELTA_BITS_SHA256
        or evidence.get("candidateRadiusInternalUlps") != CANDIDATE_RADIUS
        or evidence.get("candidateCount") != 2 * CANDIDATE_RADIUS + 1
        or evidence.get("targetWidth") != TARGET_WIDTH
        or evidence.get("targetHeight") != TARGET_HEIGHT
        or evidence.get("viewportWidth") != VIEWPORT_WIDTH
        or evidence.get("originX") != ORIGIN_X
        or evidence.get("originY") != ORIGIN_Y
        or evidence.get("geometryHeight") != GEOMETRY_HEIGHT
        or evidence.get("edgeAreaMargin") != EDGE_AREA_MARGIN
        or evidence.get("primitiveCount") != PRIMITIVE_COUNT
        or evidence.get("tileCount") != TILE_COUNT
        or evidence.get("positionRule")
        != "unclipped-power2-viewport-interior-area-margin-v3"
        or evidence.get("sourcePhysicalTruthTableSha256") != SOURCE_TRUTH_SHA256
        or evidence.get("preregistrationSha256")
        != sha256_path(preregistration_path)
        or len(discovery) != DISCOVERY_WIDTH_COUNT
        or uint32_sha256(discovery) != DISCOVERY_WIDTHS_SHA256
        or len(holdout) != HOLDOUT_WIDTH_COUNT
        or uint32_sha256(holdout) != HOLDOUT_WIDTHS_SHA256
        or evidence.get("bytes") != expected_bytes
        or not path.is_file()
        or path.stat().st_size != expected_bytes
        or sha256_path(path) != evidence.get("sha256")
    ):
        raise ValueError(f"reciprocal {role} inputs differ")
    return preregistration, manifest, path, widths, role


def decode_width(
    width: int,
    pulls: UIntArray,
    positions: list[JsonObject],
) -> tuple[int, UIntArray, int]:
    nearest = nearest_even_reciprocal_index(width)
    candidate_bits = np.stack(
        [
            corpus.truncated_radix2_product_bits(
                width,
                nearest + offset,
                WITNESS_SIGNIFICANDS,
                operand_precision_bits=24,
                partial_product_truncation_bits=16,
                rounding_bias=0x14_0000,
            )[0]
            for offset in range(-CANDIDATE_RADIUS, CANDIDATE_RADIUS + 1)
        ]
    )
    candidate_count = candidate_bits.shape[0]
    witness_count = candidate_bits.shape[1]
    slopes = corpus.float_values(candidate_bits.reshape(-1)).astype(np.float64)
    accepted = np.ones((candidate_count, witness_count), dtype=np.bool_)
    for position in positions:
        primitive = int(position["primitive"])
        tile = int(position["tile"])
        tiled_pulls = np.broadcast_to(
            pulls[:, primitive, tile, :],
            (candidate_count, witness_count, PULL_COUNT),
        ).reshape(-1, PULL_COUNT)
        accepted &= corpus.pair_accepts_slope(
            slopes,
            position=float(int(position["x"]) % 32),
            pulls=tiled_pulls,
        ).reshape(candidate_count, witness_count)
    matches = np.flatnonzero(np.all(accepted, axis=1))
    if matches.size != 1:
        raise ValueError(
            f"width {width} accepted {matches.size} reciprocal candidates"
        )
    candidate_index = int(matches[0])
    return (
        candidate_index - CANDIDATE_RADIUS,
        candidate_bits[candidate_index],
        int(matches.size),
    )


def analyze(
    root: Path,
    *,
    preregistration_path: Path,
    reciprocal_table_path: Path,
    coefficient_table_path: Path,
) -> JsonObject:
    _preregistration, manifest, pulls_path, widths, role = validate_inputs(
        root,
        preregistration_path,
    )
    witness_count = WITNESS_SIGNIFICANDS.size
    pulls = np.memmap(
        pulls_path,
        dtype="<u4",
        mode="r",
        shape=(
            len(widths),
            witness_count,
            PRIMITIVE_COUNT,
            TILE_COUNT,
            PULL_COUNT,
        ),
    )
    reciprocal_table = np.empty(len(widths), dtype="<u4")
    coefficient_table = np.empty((len(widths), witness_count), dtype="<u4")
    offset_counts: Counter[int] = Counter()
    match_counts: Counter[int] = Counter()
    records: list[JsonObject] = []
    selected_by_class: dict[int, int] = {}
    scale_equivalence_comparisons = 0
    scale_equivalence_mismatches = 0

    for width_index, width in enumerate(widths):
        positions = expected_positions(width)
        expected_slots = {
            int(position["primitive"]) * TILE_COUNT + int(position["tile"])
            for position in positions
        }
        packed = pulls[width_index].view("<u8").reshape(witness_count, -1)
        for slot in range(PRIMITIVE_COUNT * TILE_COUNT):
            absent = packed[:, slot] == SENTINEL
            if slot in expected_slots:
                if np.any(absent):
                    raise ValueError(f"width {width} has an absent expected pull")
            elif np.any(~absent):
                raise ValueError(f"width {width} has an unexpected pull")

        offset, coefficient_bits, match_count = decode_width(
            width,
            pulls[width_index],
            positions,
        )
        nearest = nearest_even_reciprocal_index(width)
        selected = nearest + offset
        reciprocal_table[width_index] = selected
        coefficient_table[width_index] = coefficient_bits
        offset_counts[offset] += 1
        match_counts[match_count] += 1

        normalized = normalization_class(width)
        if normalized in selected_by_class:
            scale_equivalence_comparisons += 1
            if selected_by_class[normalized] != selected:
                scale_equivalence_mismatches += 1
        else:
            selected_by_class[normalized] = selected

        exponent = -(width - 1).bit_length()
        exact_scale = 1 << (24 - exponent)
        records.append(
            {
                "width": width,
                "normalizationClass": normalized,
                "reciprocalExponent": exponent,
                "nearestEven25Index": nearest,
                "selectedReciprocal25Index": selected,
                "nearestEvenOffset": offset,
                "selectedErrorNumerator": selected * width - exact_scale,
                "selectedErrorDenominator": width,
            }
        )

    reciprocal_table_path.write_bytes(reciprocal_table.tobytes(order="C"))
    coefficient_table_path.write_bytes(coefficient_table.tobytes(order="C"))
    coefficient_count = int(coefficient_table.size)
    return {
        "liquidGlassRasterReciprocalSweepAnalysisSchemaVersion": 2,
        "probe": str(root),
        "manifestSha256": sha256_path(root / "manifest.json"),
        "pullsSha256": sha256_path(pulls_path),
        "preregistration": str(preregistration_path),
        "preregistrationSha256": sha256_path(preregistration_path),
        "selectedRole": role,
        "discoveryOpened": role == "discovery",
        "holdoutOpened": role == "holdout",
        "holdoutOpeningClassification": (
            "calibration-not-prospective-validation"
            if role == "holdout"
            else None
        ),
        "ciCommit": manifest.get("ciCommit"),
        "reciprocalTruthTable": {
            "file": str(reciprocal_table_path),
            "bytes": reciprocal_table_path.stat().st_size,
            "sha256": sha256_path(reciprocal_table_path),
            "dtype": "little-endian uint32",
            "shape": [len(widths)],
            "ordering": f"{role}-width-major",
            "widthsSha256": (
                HOLDOUT_WIDTHS_SHA256
                if role == "holdout"
                else DISCOVERY_WIDTHS_SHA256
            ),
        },
        "coefficientTruthTable": {
            "file": str(coefficient_table_path),
            "bytes": coefficient_table_path.stat().st_size,
            "sha256": sha256_path(coefficient_table_path),
            "dtype": "little-endian uint32 float bits",
            "shape": [len(widths), witness_count],
            "ordering": f"{role}-width-major,witness-major",
            "significandSha256": SIGNIFICAND_SHA256,
        },
        "measurement": {
            "widthCount": len(widths),
            "witnessCountPerWidth": witness_count,
            "coefficientCount": coefficient_count,
            "candidateMatchCountDistribution": {
                str(count): frequency
                for count, frequency in sorted(match_counts.items())
            },
            "nearestEvenOffsetDistribution": {
                str(offset): frequency
                for offset, frequency in sorted(offset_counts.items())
            },
            "nearestEvenMatchCount": offset_counts[0],
            "nonNearestCount": coefficient_count // witness_count - offset_counts[0],
            "physicalProductMatchCount": coefficient_count,
            "physicalProductMismatchCount": 0,
            "scaleEquivalenceClassCount": len(selected_by_class),
            "scaleEquivalenceComparisonCount": scale_equivalence_comparisons,
            "scaleEquivalenceMismatchCount": scale_equivalence_mismatches,
            "exact": (
                match_counts == Counter({1: len(widths)})
                and scale_equivalence_mismatches == 0
            ),
        },
        "widths": records,
        "conclusions": {
            "physicalPartialProductLawTransfersAcrossSelectedRange": True,
            "maximumSelectedWidth": max(widths),
            "reciprocalIndexUniquelyRecoveredForSelectedWidths": True,
            "reciprocalIndexWithinOneNearestEvenUlpOnSelectedPartition": (
                set(offset_counts) <= {-1, 0, 1}
            ),
            "powerOfTwoScaleEquivalenceExactOnSelectedPartition": (
                scale_equivalence_mismatches == 0
            ),
            "reciprocalGenerationLawFullyDetermined": False,
            "sealedProductionHoldoutRemainsUnopened": role == "discovery",
            "productionHoldoutWidthsMeasuredAsCalibration": (
                role == "holdout"
                and all(width in widths for width in PRODUCTION_HOLDOUT_WIDTHS)
            ),
            "portableCombinedDividerLawFullyDetermined": False,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("--preregistration", type=Path, required=True)
    parser.add_argument("--reciprocal-table", type=Path, required=True)
    parser.add_argument("--coefficient-table", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    report = analyze(
        arguments.root,
        preregistration_path=arguments.preregistration,
        reciprocal_table_path=arguments.reciprocal_table,
        coefficient_table_path=arguments.coefficient_table,
    )
    arguments.output.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
