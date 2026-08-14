#!/usr/bin/env python3
"""Analyze the exhaustive AGX direct-user-clip reciprocal census.

The input table was exported from rasterizer-generated coefficient triples.  It
contains no reference image or output pixel.  This analysis identifies the
reciprocal endpoint and the following numerator product stage without claiming
that user clip distances and AGX's built-in guard clipping share every stage.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
from pathlib import Path
from typing import Final

import numpy as np
from numpy.typing import NDArray


type JsonValue = (
    None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]
)
type JsonObject = dict[str, JsonValue]
type UInt32Array = NDArray[np.uint32]
type UInt64Array = NDArray[np.uint64]

ROOT: Final = Path(__file__).resolve().parent.parent
CAPTURE_ROOT: Final = ROOT / "build" / "analysis-agx-direct-user-clip"
EXHAUSTIVE_ROOT: Final = CAPTURE_ROOT / "exhaustive-reciprocal"
TABLE: Final = EXHAUSTIVE_ROOT / "reveal-agx-direct-clip-reciprocal-u32.bin"
TABLE_MANIFEST: Final = (
    EXHAUSTIVE_ROOT / "reveal-agx-direct-clip-reciprocal-manifest.json"
)
ZERO_ROOT: Final = CAPTURE_ROOT / "zero-crossing-capture"
DENSE_ROOT: Final = CAPTURE_ROOT / "dense-divider-capture"
NUMERATOR_ROOT: Final = CAPTURE_ROOT / "numerator-capture"
FAST_INTRINSICS: Final = (
    ROOT / "artifacts" / "apple-float-intrinsics-r8-30556057571.bin"
)
P25_SELECTOR: Final = ROOT / "parity" / "raster_p25_selector_ceil_bits.bin"
DEFAULT_OUTPUT: Final = (
    EXHAUSTIVE_ROOT / "reveal-agx-direct-clip-reciprocal-result.json"
)

ENTRY_COUNT: Final = 1 << 23
RECORD_COUNT: Final = 83_872
DISCOVERY_RECORD_COUNT: Final = 8_193 * 8
PATTERN_COUNT: Final = 8
VECTOR_COUNT: Final = 101
VECTOR_WORDS: Final = 4
RECORD_WORDS: Final = VECTOR_COUNT * VECTOR_WORDS
COEFFICIENT_VECTOR: Final = 5
COEFFICIENT_COMPONENT: Final = 2
NUMERATORS: Final = (1, 2, 3, 5, 7, 255, 256, 257)

EXPECTED_SHA256: Final = {
    TABLE: "7381fe62080a7187016d3f32299ea93fbbbe9d974ad8338033c5d161be25720b",
    TABLE_MANIFEST: "5519970eddcb7bd9bbd2c393e329338bb30c720e20e1968a84626cff4425d2e2",
    FAST_INTRINSICS: "fff71cc0d4428677ca5bc58b91212a7166b701e4efe504c3d71cab70846d0449",
    P25_SELECTOR: "9fbc083dfd9c89fc0bcdc89308acfc4530d408e93789a7dab89ee59ff60a198f",
    ZERO_ROOT
    / "manifest.json": "78a857c2561869cb6988e9f5b24dae63aae0d36ed9efa766d3d7d3e1c54502a8",
    ZERO_ROOT
    / "reveal-agx-clip-weight-tomography.raw": "027a6717fe9c7acfba1cfb18601feb58fda4a096d2966117cbd298efd79b5812",
    DENSE_ROOT
    / "manifest.json": "fa1ee6ae6ff85f3e331801265e7885a806648ea79772c013be6d2f5587280d46",
    DENSE_ROOT
    / "reveal-agx-clip-weight-tomography.raw": "b7e85cccfbb719bdfc7596b7444f899da0f3db24f5a707b9f1382aa1331a4013",
    NUMERATOR_ROOT
    / "manifest.json": "f8eeed627d75edefee47b96995f8b4173a16aa96c53224de2bd6e28c1cdb028f",
    NUMERATOR_ROOT
    / "reveal-agx-clip-weight-tomography.raw": "7dc8d6bcf8a5be1ee96ca7f3e04ad8f851e8f8f5434f74c83b142d6ad510cbe2",
    ROOT
    / "analysis"
    / "extract_reveal_agx_clip_reciprocal_table.py": "a1138d60362017afea39aed9b2880d5257f4a36e29ee154ff3998df0e48bf0a7",
    ROOT
    / "analysis"
    / "run_reveal_agx_direct_clip_exhaustive_mantissa.sh": "199a02adb4036827b196ab6c8c1df91f00dcd79eec9880138414fdf19ad40f41",
    ROOT
    / "analysis"
    / "reveal_agx_direct_user_clip_exhaustive_mantissa_experiment.patch": "57532c8cb1472b0e7d88c6b9dd538b1617bfdbb763ed44fa3b30f45cd3cf0ef2",
    ROOT
    / "analysis"
    / "reveal_agx_direct_user_clip_numerator_experiment.patch": "25ab509be5fdbd94c22e26e371f653859d985786e0f5be202926baa9fe049c48",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(4 * 1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def sha256_bytes(value: NDArray[np.generic] | bytes) -> str:
    data = memoryview(value).cast("B") if isinstance(value, np.ndarray) else value
    return hashlib.sha256(data).hexdigest()


def verify_inputs() -> list[JsonObject]:
    verified: list[JsonObject] = []
    for path, expected in EXPECTED_SHA256.items():
        actual = sha256(path)
        if actual != expected:
            raise ValueError(f"{path.relative_to(ROOT)} SHA-256 differs")
        verified.append(
            {
                "path": str(path.relative_to(ROOT)),
                "bytes": path.stat().st_size,
                "sha256": actual,
            }
        )

    manifest = json.loads(TABLE_MANIFEST.read_text(encoding="utf-8"))
    if (
        manifest.get("schema") != "walle-reveal-agx-direct-clip-reciprocal-table-v1"
        or manifest.get("table", {}).get("bytes") != ENTRY_COUNT * 4
        or manifest.get("table", {}).get("sha256") != EXPECTED_SHA256[TABLE]
        or len(manifest.get("chunks", [])) != 128
        or [chunk.get("mantissaLow7") for chunk in manifest["chunks"]]
        != list(range(128))
    ):
        raise ValueError("exhaustive table manifest differs")
    return verified


def coefficient_census(root: Path) -> UInt32Array:
    raw_path = root / "reveal-agx-clip-weight-tomography.raw"
    raw = np.memmap(
        raw_path,
        dtype="<u4",
        mode="r",
        shape=(RECORD_COUNT, RECORD_WORDS),
    )
    discovery = raw[:DISCOVERY_RECORD_COUNT].reshape(
        8_193, PATTERN_COUNT, VECTOR_COUNT, VECTOR_WORDS
    )
    return np.asarray(discovery[:, :, COEFFICIENT_VECTOR, COEFFICIENT_COMPONENT]).copy()


def reciprocal_indices(table_bits: UInt32Array) -> UInt64Array:
    if table_bits.size != ENTRY_COUNT:
        raise ValueError("reciprocal table entry count differs")
    if int(table_bits[0]) != 0x3780_0000:
        raise ValueError("power-of-two reciprocal endpoint differs")
    exponent = (table_bits >> np.uint32(23)) & np.uint32(0xFF)
    if np.any(exponent[1:] != 110):
        raise ValueError("non-boundary reciprocal exponent differs")
    indices = np.empty(ENTRY_COUNT, dtype=np.uint64)
    indices[0] = 1 << 24
    indices[1:] = (1 << 23) + (table_bits[1:] & np.uint32(0x007F_FFFF))
    return indices


def packed_selector_sha256(selector: NDArray[np.bool_]) -> str:
    return sha256_bytes(np.packbits(selector, bitorder="little"))


def reciprocal_analysis(table_bits: UInt32Array) -> tuple[JsonObject, UInt64Array]:
    mantissas = np.arange(ENTRY_COUNT, dtype=np.uint64)
    denominators = (1 << 23) + mantissas
    numerator = np.uint64(1 << 47)
    floor, remainder = np.divmod(numerator, denominators)
    observed = reciprocal_indices(table_bits)
    selected_ceil = observed == floor + 1
    exact_boundary = remainder == 0
    valid = (observed == floor) | (selected_ceil & ~exact_boundary)
    if not np.all(valid):
        raise ValueError("an observed reciprocal is not an adjacent exact endpoint")

    rne_up = (2 * remainder > denominators) | (
        (2 * remainder == denominators) & ((floor & 1) != 0)
    )
    rne = floor + rne_up
    observed_minus_rne = observed.astype(np.int64) - rne.astype(np.int64)

    packed_intrinsics = np.fromfile(FAST_INTRINSICS, dtype=np.uint8)
    if packed_intrinsics.size != ENTRY_COUNT:
        raise ValueError("Apple intrinsic table length differs")
    fast_correction = ((packed_intrinsics >> np.uint8(6)) & np.uint8(3)).astype(
        np.int8
    ) - np.int8(1)
    fast = rne.astype(np.int64) + fast_correction.astype(np.int64)
    observed_minus_fast = observed.astype(np.int64) - fast

    p25 = np.fromfile(P25_SELECTOR, dtype=np.uint8)
    p25_indices = 2 * mantissas
    p25_ceil = ((p25[p25_indices >> 3] >> (p25_indices & 7)) & 1).astype(bool)

    selector = selected_ceil & ~exact_boundary
    floor_fraction = remainder[~selector].astype(np.float64) / denominators[
        ~selector
    ].astype(np.float64)
    ceil_fraction = remainder[selector].astype(np.float64) / denominators[
        selector
    ].astype(np.float64)
    transitions = int(np.count_nonzero(selector[1:] != selector[:-1]))
    monotonic = bool(np.all(observed[1:] <= observed[:-1]))
    unique, counts = np.unique(observed_minus_rne, return_counts=True)
    rne_distribution = {str(int(key)): int(count) for key, count in zip(unique, counts)}
    unique, counts = np.unique(observed_minus_fast, return_counts=True)
    fast_distribution = {
        str(int(key)): int(count) for key, count in zip(unique, counts)
    }

    return (
        {
            "inputDomain": {
                "binary32BitsInclusive": ["0x47800000", "0x47ffffff"],
                "mantissaCount": ENTRY_COUNT,
                "exactNormalizedQuotient": "2^47 / (2^23 + mantissa)",
            },
            "adjacentEndpointClassification": {
                "exactPowerOfTwoCount": int(np.count_nonzero(exact_boundary)),
                "nonexactFloorCount": int(
                    np.count_nonzero(~selector & ~exact_boundary)
                ),
                "nonexactCeilCount": int(np.count_nonzero(selector)),
                "outsideAdjacentEndpointsCount": int(np.count_nonzero(~valid)),
                "allExact": bool(np.all(valid)),
            },
            "nearestEvenComparison": {
                "observedMinusNearestEvenUlp": rne_distribution,
                "matchCount": int(np.count_nonzero(observed_minus_rne == 0)),
                "mismatchCount": int(np.count_nonzero(observed_minus_rne != 0)),
            },
            "appleShaderFastReciprocalComparison": {
                "observedMinusFastReciprocalUlp": fast_distribution,
                "matchCount": int(np.count_nonzero(observed_minus_fast == 0)),
                "mismatchCount": int(np.count_nonzero(observed_minus_fast != 0)),
            },
            "selectorFingerprint": {
                "ceilBitsetSha256": packed_selector_sha256(selector),
                "ceilCount": int(np.count_nonzero(selector)),
                "floorOrExactCount": int(np.count_nonzero(~selector)),
                "transitionCount": transitions,
                "monotonicOutput": monotonic,
                "minimumCeilRemainderFraction": float(np.min(ceil_fraction)),
                "maximumFloorRemainderFraction": float(np.max(floor_fraction)),
                "singleRemainderThresholdPossible": bool(
                    np.min(ceil_fraction) > np.max(floor_fraction)
                ),
            },
            "p25RasterSelectorComparison": {
                "normalization": "P25 key = 2 * (2^23 + mantissa)",
                "matchCount": int(np.count_nonzero(p25_ceil == selector)),
                "mismatchCount": int(np.count_nonzero(p25_ceil != selector)),
            },
        },
        observed,
    )


def capture_slice_analysis(table_bits: UInt32Array) -> JsonObject:
    zero = coefficient_census(ZERO_ROOT)
    dense = coefficient_census(DENSE_ROOT)
    zero_expected = table_bits[np.arange(8_193, dtype=np.uint64) << 7]
    dense_expected = table_bits[np.arange(8_193, dtype=np.uint64)]
    return {
        "zeroCrossing": {
            "denominators": "65536 + d, d=0...8192",
            "comparisonCount": 8_193,
            "tableMismatchCount": int(np.count_nonzero(zero[:, 0] != zero_expected)),
            "eightPatternRepeatMismatchCount": int(
                np.count_nonzero(zero != zero[:, :1])
            ),
        },
        "denseDivider": {
            "denominatorBits": "0x47800000 + d, d=0...8192",
            "comparisonCount": 8_193,
            "tableMismatchCount": int(np.count_nonzero(dense[:, 0] != dense_expected)),
            "eightPatternRepeatMismatchCount": int(
                np.count_nonzero(dense != dense[:, :1])
            ),
        },
    }


def float32_parts(bits: int) -> tuple[int, int]:
    exponent = (bits >> 23) & 0xFF
    if bits >> 31 or not 0 < exponent < 0xFF:
        raise ValueError("product analysis requires a positive normal binary32")
    return (1 << 23) | (bits & 0x007F_FFFF), exponent - 150


def positive_dyadic_float32_bits(index: int, lsb_exponent: int) -> int:
    if index <= 0:
        raise ValueError("dyadic index must be positive")
    bit_count = index.bit_length()
    if bit_count > 24:
        shift = bit_count - 24
        quotient, remainder = divmod(index, 1 << shift)
        quotient += 2 * remainder > 1 << shift or (
            2 * remainder == 1 << shift and bool(quotient & 1)
        )
        index = quotient
        lsb_exponent += shift
        if index == 1 << 24:
            index >>= 1
            lsb_exponent += 1
        bit_count = index.bit_length()
    exponent = bit_count - 1 + lsb_exponent
    if not -126 <= exponent <= 127:
        raise ValueError("dyadic value escaped the normal binary32 range")
    significand = index << (24 - bit_count)
    return ((exponent + 127) << 23) | (significand - (1 << 23))


def truncated_product_bits(left_bits: int, right_bits: int) -> int:
    left, left_exponent = float32_parts(left_bits)
    right, right_exponent = float32_parts(right_bits)
    product_shift = (left * right).bit_length() - 24
    partial = sum(
        ((left << bit) >> 18) << 18
        for bit in range(right.bit_length())
        if right & (1 << bit)
    )
    index = (partial + (17 << 18)) >> product_shift
    return positive_dyadic_float32_bits(
        index, left_exponent + right_exponent + product_shift
    )


def numerator_product_analysis() -> JsonObject:
    observed = coefficient_census(NUMERATOR_ROOT)
    predicted = np.empty_like(observed)
    numerator_bits = [
        struct.unpack("<I", struct.pack("<f", float(value)))[0] for value in NUMERATORS
    ]
    for distance in range(8_193):
        reciprocal_bits = int(observed[distance, 0])
        for pattern, bits in enumerate(numerator_bits):
            predicted[distance, pattern] = truncated_product_bits(reciprocal_bits, bits)
    mismatches = int(np.count_nonzero(predicted != observed))
    return {
        "numerators": list(NUMERATORS),
        "comparisonCount": int(predicted.size),
        "mismatchCount": mismatches,
        "predictedBitsSha256": sha256_bytes(predicted),
        "law": {
            "outputSignificandBits": 24,
            "partialProductsDiscardColumnsBelow": 18,
            "biasUnitsAtDiscardColumn": 17,
            "description": (
                "sum each set-bit partial product after truncating below column "
                "18, add 17 units at column 18, normalize to 24 bits"
            ),
        },
        "allExact": mismatches == 0,
    }


def analyze() -> JsonObject:
    inputs = verify_inputs()
    table_bits = np.fromfile(TABLE, dtype="<u4")
    reciprocal, _observed = reciprocal_analysis(table_bits)
    slices = capture_slice_analysis(table_bits)
    numerator = numerator_product_analysis()
    passed = bool(
        reciprocal["adjacentEndpointClassification"]["allExact"]
        and slices["zeroCrossing"]["tableMismatchCount"] == 0
        and slices["zeroCrossing"]["eightPatternRepeatMismatchCount"] == 0
        and slices["denseDivider"]["tableMismatchCount"] == 0
        and slices["denseDivider"]["eightPatternRepeatMismatchCount"] == 0
        and numerator["allExact"]
    )
    return {
        "schema": "walle-reveal-agx-direct-clip-reciprocal-analysis-v1",
        "passed": passed,
        "authority": {
            "opensReferencePixels": False,
            "opensSealedTomographyHoldouts": False,
            "usesOnlyRasterizerGeneratedCoefficientTriples": True,
            "establishesDirectUserClipReciprocalEndpointDomain": True,
            "establishesFollowingNumeratorProductForMeasuredDomain": True,
            "establishesBuiltinGuardClipEquivalence": False,
            "establishesFullPostClipTriangleSetupLaw": False,
            "authorizesProductionParityClaim": False,
        },
        "inputs": inputs,
        "reciprocal": reciprocal,
        "captureSliceJoins": slices,
        "numeratorProduct": numerator,
        "algorithmicConclusion": (
            "For this direct user-clip construction, AGX first selects one of the "
            "two adjacent exact reciprocal endpoints, then applies an exact measured "
            "24/18/17 truncated-partial-product stage. The exhaustive selector is "
            "neither nearest-even, the shader fast reciprocal, the admitted P25 "
            "raster selector, nor a single remainder threshold. Its compact generation "
            "law and its equivalence to built-in guard clipping remain unresolved."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    arguments = parser.parse_args()
    result = analyze()
    encoded = json.dumps(result, indent=2, sort_keys=True) + "\n"
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(encoded, encoding="utf-8")
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
