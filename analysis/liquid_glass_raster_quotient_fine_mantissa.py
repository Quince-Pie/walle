#!/usr/bin/env python3
"""Open the preregistered schema-23 full-mantissa quotient test."""

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

import liquid_glass_raster_quotient_corpus as corpus


type JsonObject = dict[str, Any]
type UIntArray = NDArray[np.uint32]

SCHEMA_VERSION = 23
RIG_VERSION = "metal-raster-interpolant-probe-23.0.0"
WIDTHS = (
    33,
    37,
    43,
    44,
    49,
    52,
    55,
    59,
    61,
    67,
    73,
    79,
    85,
    90,
    91,
    96,
    97,
    100,
    101,
    103,
    109,
    115,
    121,
    127,
)
SAMPLE_COUNT = 8_192
PRIMITIVE_COUNT = 2
TILE_COUNT = 5
PULL_COUNT = 2
SENTINEL = np.uint64(0xFFFFFFFFFFFFFFFF)
SIGNIFICAND_SHA256 = "c55831b5269944773952e478ed7f6f0c7ec7c6f9d7b1a54f230ca34a3c8ad0ac"
DELTA_BITS_SHA256 = "9111298595dd270f0c2142382920a3d0d196044e67ab75054bdcb899736742ab"
PREDICTED_TRUTH_SHA256 = (
    "069c044c3b38d0535656c0a6e4d12c07a80a2b9b528ae4eb80c4735381c2469a"
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def generate_significands() -> NDArray[np.uint64]:
    values: list[int] = []
    seen: set[int] = set()
    for bank in range(16):
        numerator = 32_768 + 2_048 * bank + ((73 * bank + 19) & 255)
        for phase in range(256):
            significand = (numerator << 8) | phase
            if significand in seen:
                raise ValueError("structured fine-mantissa sample repeats")
            seen.add(significand)
            values.append(significand)
    state = 0x31_41_59
    while len(values) < SAMPLE_COUNT:
        state = (state * 0x5B_D1_E9_95 + 0x6C_8E_9C_F5) & 0x7F_FF_FF
        significand = 0x80_00_00 | state
        if significand not in seen:
            seen.add(significand)
            values.append(significand)
    if len(values) != SAMPLE_COUNT or len(seen) != SAMPLE_COUNT:
        raise ValueError("fine-mantissa sample generator differs")
    return np.asarray(values, dtype=np.uint64)


def delta_bits(significands: NDArray[np.uint64]) -> UIntArray:
    return (
        np.uint32(0x3F_00_00_00)
        | (significands.astype(np.uint32) & np.uint32(0x7F_FF_FF))
    ).astype("<u4")


def expected_position_records() -> list[JsonObject]:
    return [
        {
            "width": width,
            "positions": corpus.expected_positions(width),
        }
        for width in WIDTHS
    ]


def validate_probe(root: Path) -> tuple[JsonObject, Path]:
    manifest_path = root / "manifest.json"
    manifest: JsonObject = json.loads(manifest_path.read_text(encoding="utf-8"))
    evidence = manifest.get("quotientFineMantissaCorpus", {})
    path = root / str(evidence.get("file", ""))
    expected_bytes = (
        len(WIDTHS)
        * SAMPLE_COUNT
        * PRIMITIVE_COUNT
        * TILE_COUNT
        * PULL_COUNT
        * np.dtype("<u4").itemsize
    )
    if (
        manifest.get("schemaVersion") != SCHEMA_VERSION
        or manifest.get("rigVersion") != RIG_VERSION
        or evidence.get("role") != "prospective-holdout"
        or evidence.get("widths") != list(WIDTHS)
        or evidence.get("sampleCountPerWidth") != SAMPLE_COUNT
        or evidence.get("operandPrecisionBits") != 24
        or evidence.get("structuredSampleCount") != 4_096
        or evidence.get("permutedSampleCount") != 4_096
        or evidence.get("significandSha256") != SIGNIFICAND_SHA256
        or evidence.get("deltaBitsSha256") != DELTA_BITS_SHA256
        or evidence.get("height") != 64
        or evidence.get("originX") != 17
        or evidence.get("originY") != 19
        or evidence.get("targetWidth") != 160
        or evidence.get("targetHeight") != 160
        or evidence.get("primitiveCount") != PRIMITIVE_COUNT
        or evidence.get("tileCount") != TILE_COUNT
        or evidence.get("uncoveredRecordSentinel") != "0xffffffffffffffff"
        or evidence.get("pullOffsets")
        != [{"x": 0.0, "y": 0.5}, {"x": 0.9375, "y": 0.5}]
        or evidence.get("components") != ["xAt0", "xAt15Over16"]
        or evidence.get("ordering")
        != ("width-major,sample-major,primitive-major,tile-major,pull-offset-major")
        or evidence.get("positionsByWidth") != expected_position_records()
        or evidence.get("bytes") != expected_bytes
        or not path.is_file()
        or path.stat().st_size != expected_bytes
        or sha256_file(path) != evidence.get("sha256")
    ):
        raise ValueError("schema-23 fine-mantissa metadata differs")
    return manifest, path


def prediction_table(
    preregistration: JsonObject,
    significands: NDArray[np.uint64],
) -> UIntArray:
    reciprocal_records = preregistration.get("reciprocalPredictions", [])
    if [record.get("width") for record in reciprocal_records] != list(WIDTHS):
        raise ValueError("fine-mantissa reciprocal ordering differs")
    table = np.empty((len(WIDTHS), SAMPLE_COUNT), dtype="<u4")
    for width_index, record in enumerate(reciprocal_records):
        table[width_index] = corpus.truncated_radix2_product_bits(
            int(record["width"]),
            int(record["reciprocal25Index"]),
            significands,
            operand_precision_bits=24,
            partial_product_truncation_bits=16,
            rounding_bias=0x14_00_00,
        )[0]
    return table


def validate_preregistration(
    path: Path,
    significands: NDArray[np.uint64],
) -> tuple[JsonObject, UIntArray]:
    preregistration: JsonObject = json.loads(path.read_text(encoding="utf-8"))
    model = preregistration.get("model", {})
    generator = preregistration.get("sampleGenerator", {})
    prediction = preregistration.get("predictedTruthTable", {})
    generated_delta_bits = delta_bits(significands)
    if (
        preregistration.get("schemaVersion") != 1
        or preregistration.get("role") != "prospective-full-mantissa-prediction"
        or preregistration.get("fineMantissaObservedAtPreregistration") is not False
        or model.get("name") != "physicalTruncatedRadix2PartialProducts16Bias0x140000"
        or model.get("operandPrecisionBits") != 24
        or model.get("reciprocalPrecisionBits") != 25
        or model.get("productPrecisionBits") != 27
        or model.get("partialProductRadix") != 2
        or model.get("partialProductTruncationBits") != 16
        or model.get("roundingBias") != 0x14_00_00
        or generator.get("sampleCount") != SAMPLE_COUNT
        or generator.get("significandSha256") != SIGNIFICAND_SHA256
        or generator.get("deltaBitsSha256") != DELTA_BITS_SHA256
        or hashlib.sha256(significands.astype("<u4").tobytes(order="C")).hexdigest()
        != SIGNIFICAND_SHA256
        or hashlib.sha256(generated_delta_bits.tobytes(order="C")).hexdigest()
        != DELTA_BITS_SHA256
        or preregistration.get("domain")
        != {
            "widths": list(WIDTHS),
            "ordering": "width-major,sample-major",
        }
        or prediction
        != {
            "dtype": "little-endian uint32 float bits",
            "shape": [len(WIDTHS), SAMPLE_COUNT],
            "bytes": len(WIDTHS) * SAMPLE_COUNT * np.dtype("<u4").itemsize,
            "sha256": PREDICTED_TRUTH_SHA256,
        }
    ):
        raise ValueError("fine-mantissa preregistration differs")
    predicted = prediction_table(preregistration, significands)
    if (
        hashlib.sha256(predicted.tobytes(order="C")).hexdigest()
        != PREDICTED_TRUTH_SHA256
    ):
        raise ValueError("fine-mantissa prediction hash differs")
    return preregistration, predicted


def nearest_product27_bits(
    width: int,
    reciprocal: int,
    significands: NDArray[np.uint64],
) -> UIntArray:
    products = significands * np.uint64(reciprocal)
    _fraction, bit_lengths = np.frexp(products.astype(np.float64))
    shifts = bit_lengths.astype(np.int64) - 27
    divisors = np.left_shift(np.uint64(1), shifts.astype(np.uint64))
    indices = np.right_shift(products, shifts.astype(np.uint64))
    remainders = products & (divisors - 1)
    doubled = 2 * remainders
    indices += (
        (doubled > divisors) | ((doubled == divisors) & ((indices & 1) != 0))
    ).astype(np.uint64)
    reciprocal_exponent = -(width - 1).bit_length()
    values = np.ldexp(
        indices.astype(np.float64),
        reciprocal_exponent - 24 - 24 + shifts,
    )
    return np.asarray(values, dtype="<f4").view("<u4")


def error_distribution(observed: UIntArray, predicted: UIntArray) -> JsonObject:
    errors = observed.astype(np.int64) - predicted.astype(np.int64)
    unique, counts = np.unique(errors, return_counts=True)
    return {
        str(int(error)): int(count) for error, count in zip(unique, counts, strict=True)
    }


def analyze(
    root: Path,
    *,
    preregistration_path: Path,
    table_path: Path,
) -> JsonObject:
    manifest, pulls_path = validate_probe(root)
    significands = generate_significands()
    preregistration, predicted_table = validate_preregistration(
        preregistration_path,
        significands,
    )
    reciprocal_records = preregistration["reciprocalPredictions"]
    pulls = np.memmap(
        pulls_path,
        dtype="<u4",
        mode="r",
        shape=(
            len(WIDTHS),
            SAMPLE_COUNT,
            PRIMITIVE_COUNT,
            TILE_COUNT,
            PULL_COUNT,
        ),
    )
    observed_table = np.empty_like(predicted_table)
    candidate_counts: Counter[int] = Counter()
    mismatch_count = 0
    nearest_product_mismatch_count = 0
    discriminating_sample_count = 0
    widths: list[JsonObject] = []
    exact_delta_values = significands.astype(np.float64) * 2.0**-24

    for width_index, (width, reciprocal_record) in enumerate(
        zip(WIDTHS, reciprocal_records, strict=True)
    ):
        positions = corpus.expected_positions(width)
        expected_slots = {
            int(position["primitive"]) * TILE_COUNT + int(position["tile"])
            for position in positions
        }
        for primitive in range(PRIMITIVE_COUNT):
            for tile in range(TILE_COUNT):
                slot = primitive * TILE_COUNT + tile
                records = pulls[width_index, :, primitive, tile, :]
                packed = records.view("<u8").reshape(-1)
                if slot in expected_slots:
                    if np.any(packed == SENTINEL):
                        raise ValueError(
                            f"fine width {width} has an absent expected pull"
                        )
                elif np.any(packed != SENTINEL):
                    raise ValueError(f"fine width {width} has an unexpected pull")

        nominal = np.asarray(
            exact_delta_values / width,
            dtype="<f4",
        ).view("<u4")
        observed, counts = corpus.recover_width_from_nominal(
            pulls[width_index],
            positions,
            nominal,
        )
        candidate_counts.update(map(int, counts))
        if np.any(counts != 1):
            raise ValueError(f"fine width {width} does not recover one unique slope")
        observed_table[width_index] = observed
        predicted = predicted_table[width_index]
        mismatches = observed != predicted
        width_mismatch_count = int(np.count_nonzero(mismatches))
        mismatch_count += width_mismatch_count

        nearest_product = nearest_product27_bits(
            width,
            int(reciprocal_record["reciprocal25Index"]),
            significands,
        )
        discriminating = predicted != nearest_product
        width_discriminating_count = int(np.count_nonzero(discriminating))
        discriminating_sample_count += width_discriminating_count
        width_nearest_mismatch_count = int(
            np.count_nonzero(observed != nearest_product)
        )
        nearest_product_mismatch_count += width_nearest_mismatch_count
        widths.append(
            {
                "width": width,
                "sampleCount": SAMPLE_COUNT,
                "reciprocal25Index": int(reciprocal_record["reciprocal25Index"]),
                "preregisteredPhysicalModel": {
                    "matchCount": SAMPLE_COUNT - width_mismatch_count,
                    "mismatchCount": width_mismatch_count,
                    "floatUlpErrorDistribution": error_distribution(
                        observed,
                        predicted,
                    ),
                    "exact": width_mismatch_count == 0,
                },
                "nearestEven27BitProductControl": {
                    "matchCount": (SAMPLE_COUNT - width_nearest_mismatch_count),
                    "mismatchCount": width_nearest_mismatch_count,
                    "discriminatingSampleCount": (width_discriminating_count),
                    "exact": width_nearest_mismatch_count == 0,
                },
            }
        )

    table_path.write_bytes(observed_table.tobytes(order="C"))
    sample_count = int(observed_table.size)
    exact = mismatch_count == 0
    return {
        "liquidGlassRasterQuotientFineMantissaAnalysisSchemaVersion": 1,
        "probe": str(root),
        "manifestSha256": sha256_file(root / "manifest.json"),
        "fineMantissaCorpusSha256": sha256_file(pulls_path),
        "preregistration": str(preregistration_path),
        "preregistrationSha256": sha256_file(preregistration_path),
        "selectedRole": "prospective-holdout",
        "holdoutOpened": True,
        "holdoutAuthorized": True,
        "ciCommit": manifest.get("ciCommit"),
        "observedTruthTable": {
            "file": str(table_path),
            "bytes": table_path.stat().st_size,
            "sha256": sha256_file(table_path),
            "dtype": "little-endian uint32 float bits",
            "shape": [len(WIDTHS), SAMPLE_COUNT],
            "ordering": "width-major,sample-major",
            "widths": list(WIDTHS),
            "significandSha256": SIGNIFICAND_SHA256,
        },
        "measurement": {
            "widthCount": len(WIDTHS),
            "sampleCountPerWidth": SAMPLE_COUNT,
            "sampleCount": sample_count,
            "candidateSlopeCountDistribution": {
                str(count): frequency
                for count, frequency in sorted(candidate_counts.items())
            },
            "uniqueSlopeCount": candidate_counts[1],
            "preregisteredPredictionMatchCount": (sample_count - mismatch_count),
            "preregisteredPredictionMismatchCount": mismatch_count,
            "preregisteredPredictionMatchRate": (sample_count - mismatch_count)
            / sample_count,
            "nearestEven27BitProductMatchCount": (
                sample_count - nearest_product_mismatch_count
            ),
            "nearestEven27BitProductMismatchCount": (nearest_product_mismatch_count),
            "discriminatingSampleCount": discriminating_sample_count,
            "exact": exact,
        },
        "widths": widths,
        "conclusions": {
            "physicalPartialProductExtrapolationPassedProspectiveTest": (exact),
            "nonzeroLowEightOperandBitsValidated": exact,
            "sampled24BitMantissaModelFullyDetermined": exact,
            "all24BitMantissasExhaustivelyValidated": False,
            "widthsAbove127Validated": False,
            "portableCombinedDividerLawFullyDetermined": False,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("--preregistration", type=Path, required=True)
    parser.add_argument("--table", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    report = analyze(
        arguments.root,
        preregistration_path=arguments.preregistration,
        table_path=arguments.table,
    )
    arguments.output.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
