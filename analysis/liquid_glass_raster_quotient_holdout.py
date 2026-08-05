#!/usr/bin/env python3
"""Open the preregistered schema-22 raster quotient holdout."""

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

SCHEMA_VERSION = 22
RIG_VERSION = "metal-raster-interpolant-probe-22.0.0"
PRIMITIVE_COUNT = 2
TILE_COUNT = 5
PULL_COUNT = 2
SENTINEL = np.uint64(0xFFFFFFFFFFFFFFFF)
ORDERING = "width-major,numerator-major,primitive-major,tile-major,pull-offset-major"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def expected_positions_by_width() -> list[JsonObject]:
    return [
        {
            "width": width,
            "positions": corpus.expected_positions(width),
        }
        for width in corpus.HOLDOUT_WIDTHS
    ]


def validate_probe(root: Path) -> tuple[JsonObject, Path]:
    manifest_path = root / "manifest.json"
    manifest: JsonObject = json.loads(manifest_path.read_text(encoding="utf-8"))
    holdout = manifest.get("quotientHoldoutCorpus", {})
    path = root / str(holdout.get("file", ""))
    expected_bytes = (
        len(corpus.HOLDOUT_WIDTHS)
        * corpus.NUMERATOR_COUNT
        * PRIMITIVE_COUNT
        * TILE_COUNT
        * PULL_COUNT
        * np.dtype("<u4").itemsize
    )
    if (
        manifest.get("schemaVersion") != SCHEMA_VERSION
        or manifest.get("rigVersion") != RIG_VERSION
        or holdout.get("role") != "holdout"
        or holdout.get("widths") != list(corpus.HOLDOUT_WIDTHS)
        or holdout.get("discoveryWidthsExcluded") != list(corpus.DISCOVERY_WIDTHS)
        or set(holdout.get("widths", []))
        & set(holdout.get("discoveryWidthsExcluded", []))
        or holdout.get("height") != 64
        or holdout.get("originX") != 17
        or holdout.get("originY") != 19
        or holdout.get("targetWidth") != 160
        or holdout.get("targetHeight") != 160
        or holdout.get("instanceCount") != corpus.NUMERATOR_COUNT
        or holdout.get("numeratorLowerInclusive") != corpus.NUMERATOR_LOWER
        or holdout.get("numeratorUpperInclusive") != corpus.NUMERATOR_UPPER
        or holdout.get("deltaDenominator") != 65_536
        or holdout.get("primitiveCount") != PRIMITIVE_COUNT
        or holdout.get("tileCount") != TILE_COUNT
        or holdout.get("uncoveredRecordSentinel") != "0xffffffffffffffff"
        or holdout.get("pullOffsets") != [{"x": 0.0, "y": 0.5}, {"x": 0.9375, "y": 0.5}]
        or holdout.get("components") != ["xAt0", "xAt15Over16"]
        or holdout.get("ordering") != ORDERING
        or holdout.get("positionsByWidth") != expected_positions_by_width()
        or holdout.get("bytes") != expected_bytes
        or not path.is_file()
        or path.stat().st_size != expected_bytes
        or sha256_file(path) != holdout.get("sha256")
    ):
        raise ValueError("schema-22 quotient holdout metadata differs")
    return manifest, path


def validate_preregistration(path: Path) -> JsonObject:
    preregistration: JsonObject = json.loads(path.read_text(encoding="utf-8"))
    domain = preregistration.get("domain", {})
    prediction = preregistration.get("predictedTruthTable", {})
    if (
        preregistration.get("schemaVersion") != 1
        or preregistration.get("role") != "sealed-holdout-prediction"
        or preregistration.get("holdoutOpenedAtPreregistration") is not False
        or preregistration.get("model")
        != {
            "name": "truncatedRadix2PartialProducts8Bias0x1400",
            "partialProductRadix": 2,
            "partialProductTruncationBits": 8,
            "roundingBias": 5120,
            "reciprocalModel": "nearestEven25BitReciprocal",
        }
        or domain.get("widths") != list(corpus.HOLDOUT_WIDTHS)
        or domain.get("numeratorLowerInclusive") != corpus.NUMERATOR_LOWER
        or domain.get("numeratorUpperInclusive") != corpus.NUMERATOR_UPPER
        or domain.get("deltaDenominator") != 65_536
        or domain.get("ordering") != "width-major,numerator-major"
        or prediction.get("dtype") != "little-endian uint32 float bits"
        or prediction.get("shape")
        != [len(corpus.HOLDOUT_WIDTHS), corpus.NUMERATOR_COUNT]
        or prediction.get("bytes")
        != len(corpus.HOLDOUT_WIDTHS)
        * corpus.NUMERATOR_COUNT
        * np.dtype("<u4").itemsize
        or [
            record.get("width")
            for record in preregistration.get(
                "reciprocalPredictions",
                [],
            )
        ]
        != list(corpus.HOLDOUT_WIDTHS)
    ):
        raise ValueError("raster quotient holdout preregistration differs")
    predicted = preregistered_prediction_table(preregistration)
    if hashlib.sha256(predicted.tobytes(order="C")).hexdigest() != prediction.get(
        "sha256"
    ):
        raise ValueError("preregistered quotient prediction hash differs")
    return preregistration


def preregistered_prediction_table(preregistration: JsonObject) -> UIntArray:
    predictions = {
        int(record["width"]): int(record["reciprocal25Index"])
        for record in preregistration.get("reciprocalPredictions", [])
    }
    if set(predictions) != set(corpus.HOLDOUT_WIDTHS):
        raise ValueError("preregistered reciprocal width set differs")
    table = np.empty(
        (len(corpus.HOLDOUT_WIDTHS), corpus.NUMERATOR_COUNT),
        dtype="<u4",
    )
    for width_index, width in enumerate(corpus.HOLDOUT_WIDTHS):
        table[width_index] = corpus.truncated_radix2_product27_bits(
            width,
            predictions[width],
        )[0]
    return table


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
    preregistration = validate_preregistration(preregistration_path)
    preregistered_table = preregistered_prediction_table(preregistration)
    preregistered_reciprocals = {
        int(record["width"]): int(record["reciprocal25Index"])
        for record in preregistration["reciprocalPredictions"]
    }
    pulls = np.memmap(
        pulls_path,
        dtype="<u4",
        mode="r",
        shape=(
            len(corpus.HOLDOUT_WIDTHS),
            corpus.NUMERATOR_COUNT,
            PRIMITIVE_COUNT,
            TILE_COUNT,
            PULL_COUNT,
        ),
    )
    observed_table = np.empty_like(preregistered_table)
    candidate_counts: Counter[int] = Counter()
    primary_mismatch_count = 0
    recovered_selector_mismatch_count = 0
    reciprocal_prediction_mismatch_count = 0
    widths: list[JsonObject] = []

    for width_index, width in enumerate(corpus.HOLDOUT_WIDTHS):
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
                            f"holdout width {width} has an absent expected pull"
                        )
                elif np.any(packed != SENTINEL):
                    raise ValueError(f"holdout width {width} has an unexpected pull")

        observed, counts = corpus.recover_width(
            width,
            pulls[width_index],
            positions,
        )
        candidate_counts.update(map(int, counts))
        if np.any(counts != 1):
            raise ValueError(f"holdout width {width} does not recover one unique slope")
        observed_table[width_index] = observed
        reciprocal_envelope = corpus.recover_reciprocal_envelope(
            width,
            observed,
        )
        recovered_reciprocal = int(reciprocal_envelope["reciprocal25Index"])
        preregistered_reciprocal = preregistered_reciprocals[width]
        reciprocal_matches = recovered_reciprocal == preregistered_reciprocal
        reciprocal_prediction_mismatch_count += not reciprocal_matches

        primary = preregistered_table[width_index]
        primary_mismatches = observed != primary
        width_primary_mismatch_count = int(np.count_nonzero(primary_mismatches))
        primary_mismatch_count += width_primary_mismatch_count

        recovered_selector = corpus.truncated_radix2_product27_bits(
            width,
            recovered_reciprocal,
        )[0]
        recovered_selector_mismatches = observed != recovered_selector
        width_selector_mismatch_count = int(
            np.count_nonzero(recovered_selector_mismatches)
        )
        recovered_selector_mismatch_count += width_selector_mismatch_count
        widths.append(
            {
                "width": width,
                "sampleCount": corpus.NUMERATOR_COUNT,
                "preregisteredReciprocal25Index": preregistered_reciprocal,
                "recoveredReciprocal25Index": recovered_reciprocal,
                "reciprocalPredictionExact": reciprocal_matches,
                "primaryPrediction": {
                    "matchCount": (
                        corpus.NUMERATOR_COUNT - width_primary_mismatch_count
                    ),
                    "mismatchCount": width_primary_mismatch_count,
                    "floatUlpErrorDistribution": error_distribution(
                        observed,
                        primary,
                    ),
                    "exact": width_primary_mismatch_count == 0,
                },
                "preregisteredSelectorUsingRecoveredReciprocal": {
                    "matchCount": (
                        corpus.NUMERATOR_COUNT - width_selector_mismatch_count
                    ),
                    "mismatchCount": width_selector_mismatch_count,
                    "floatUlpErrorDistribution": error_distribution(
                        observed,
                        recovered_selector,
                    ),
                    "exact": width_selector_mismatch_count == 0,
                },
                "reciprocalEnvelope": reciprocal_envelope,
            }
        )

    table_path.write_bytes(observed_table.tobytes(order="C"))
    sample_count = int(observed_table.size)
    primary_exact = primary_mismatch_count == 0
    selector_exact = recovered_selector_mismatch_count == 0
    reciprocal_exact = reciprocal_prediction_mismatch_count == 0
    return {
        "liquidGlassRasterQuotientHoldoutAnalysisSchemaVersion": 1,
        "probe": str(root),
        "manifestSha256": sha256_file(root / "manifest.json"),
        "holdoutCorpusSha256": sha256_file(pulls_path),
        "preregistration": str(preregistration_path),
        "preregistrationSha256": sha256_file(preregistration_path),
        "selectedRole": "holdout",
        "holdoutOpened": True,
        "holdoutAuthorized": True,
        "ciCommit": manifest.get("ciCommit"),
        "observedTruthTable": {
            "file": str(table_path),
            "bytes": table_path.stat().st_size,
            "sha256": sha256_file(table_path),
            "dtype": "little-endian uint32 float bits",
            "shape": [
                len(corpus.HOLDOUT_WIDTHS),
                corpus.NUMERATOR_COUNT,
            ],
            "ordering": "width-major,numerator-major",
            "widths": list(corpus.HOLDOUT_WIDTHS),
            "numeratorLowerInclusive": corpus.NUMERATOR_LOWER,
            "numeratorUpperInclusive": corpus.NUMERATOR_UPPER,
        },
        "measurement": {
            "widthCount": len(corpus.HOLDOUT_WIDTHS),
            "normalizedNumeratorCountPerWidth": corpus.NUMERATOR_COUNT,
            "sampleCount": sample_count,
            "candidateSlopeCountDistribution": {
                str(count): frequency
                for count, frequency in sorted(candidate_counts.items())
            },
            "uniqueSlopeCount": candidate_counts[1],
            "primaryPredictionMatchCount": (sample_count - primary_mismatch_count),
            "primaryPredictionMismatchCount": primary_mismatch_count,
            "primaryPredictionMatchRate": (sample_count - primary_mismatch_count)
            / sample_count,
            "recoveredSelectorMatchCount": (
                sample_count - recovered_selector_mismatch_count
            ),
            "recoveredSelectorMismatchCount": (recovered_selector_mismatch_count),
            "reciprocalPredictionMatchCount": (
                len(corpus.HOLDOUT_WIDTHS) - reciprocal_prediction_mismatch_count
            ),
            "reciprocalPredictionMismatchCount": (reciprocal_prediction_mismatch_count),
        },
        "widths": widths,
        "conclusions": {
            "preregisteredCombinedLawPassedSealedHoldout": primary_exact,
            "preregisteredProductSelectorPassedSealedHoldout": selector_exact,
            "nearestEvenReciprocalPredictionPassedSealedHoldout": (reciprocal_exact),
            "combinedDividerFullyDeterminedForSchema22HoldoutDomain": (
                primary_exact and selector_exact and reciprocal_exact
            ),
            "portableReciprocalIndexLawFullyDetermined": False,
            "portableCombinedDividerLawFullyDetermined": False,
            "full24BitNumeratorMantissaValidated": False,
            "widthsAbove127Validated": False,
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
