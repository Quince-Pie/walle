#!/usr/bin/env python3
"""Validate Apple's fixed-function raster partial-product selector."""

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


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def resolve_report_file(report_path: Path, recorded_path: str) -> Path:
    path = Path(recorded_path)
    for candidate in (
        path,
        report_path.parent / path,
        report_path.parent.parent / path,
    ):
        if candidate.is_file():
            return candidate
    raise ValueError(f"recorded truth-table file is absent: {recorded_path}")


def validate_source(report_path: Path) -> tuple[JsonObject, Path, UIntArray]:
    report: JsonObject = json.loads(report_path.read_text(encoding="utf-8"))
    truth = report.get("truthTable", {})
    truth_path = resolve_report_file(report_path, str(truth.get("file", "")))
    expected_shape = (
        len(corpus.DISCOVERY_WIDTHS),
        corpus.NUMERATOR_COUNT,
    )
    expected_bytes = int(np.prod(expected_shape)) * np.dtype("<u4").itemsize
    if (
        report.get("selectedRole") != "discovery"
        or report.get("holdoutOpened") is not False
        or report.get("measurement", {}).get("exact") is not True
        or report.get("dividerTruthTableFullyDeterminedForDiscoveryDomain") is not True
        or truth.get("discoveryDomainFullyEnumerated") is not True
        or truth.get("dtype") != "little-endian uint32 float bits"
        or truth.get("shape") != list(expected_shape)
        or truth.get("ordering") != "width-major,numerator-major"
        or truth.get("widths") != list(corpus.DISCOVERY_WIDTHS)
        or truth.get("numeratorLowerInclusive") != corpus.NUMERATOR_LOWER
        or truth.get("numeratorUpperInclusive") != corpus.NUMERATOR_UPPER
        or truth.get("bytes") != expected_bytes
        or truth_path.stat().st_size != expected_bytes
        or sha256_file(truth_path) != truth.get("sha256")
        or len(report.get("widths", [])) != len(corpus.DISCOVERY_WIDTHS)
    ):
        raise ValueError("exact discovery truth-table report is required")
    table = np.memmap(
        truth_path,
        dtype="<u4",
        mode="r",
        shape=expected_shape,
    )
    return report, truth_path, table


def distribution(counter: Counter[int]) -> JsonObject:
    return {str(key): value for key, value in sorted(counter.items())}


def analyze(report_path: Path) -> JsonObject:
    source, truth_path, truth = validate_source(report_path)
    sample_count = int(truth.size)
    mismatch_count = 0
    informative_sample_count = 0
    informative_mismatch_count = 0
    envelope_escape_count = 0
    discarded_partial_distribution: Counter[int] = Counter()
    product_index_offset_distribution: Counter[int] = Counter()
    widths: list[JsonObject] = []

    for width_index, (width, width_source) in enumerate(
        zip(corpus.DISCOVERY_WIDTHS, source["widths"], strict=True)
    ):
        if width_source.get("width") != width:
            raise ValueError("source width ordering differs")
        envelope = width_source.get(
            "uniqueReciprocal25FaithfulProduct27Envelope",
            {},
        )
        if envelope.get("unique") is not True:
            raise ValueError(f"width {width} reciprocal envelope is not unique")
        reciprocal_significand = int(envelope["reciprocal25Index"])
        predicted, discarded_partials, product_indices = (
            corpus.truncated_radix2_product27_bits(
                width,
                reciprocal_significand,
            )
        )
        floor_bits, ceil_bits, shifts, exact_products = corpus.product27_endpoint_bits(
            width,
            reciprocal_significand,
        )
        exact_product_indices = np.right_shift(
            exact_products,
            shifts.astype(np.uint64),
        )
        product_index_offsets = product_indices.astype(
            np.int64
        ) - exact_product_indices.astype(np.int64)
        observed = truth[width_index]
        mismatches = predicted != observed
        informative = floor_bits != ceil_bits
        envelope_escape = (predicted != floor_bits) & (predicted != ceil_bits)
        width_mismatch_count = int(np.count_nonzero(mismatches))
        width_informative_count = int(np.count_nonzero(informative))
        width_informative_mismatch_count = int(
            np.count_nonzero(mismatches & informative)
        )
        width_envelope_escape_count = int(np.count_nonzero(envelope_escape))
        mismatch_count += width_mismatch_count
        informative_sample_count += width_informative_count
        informative_mismatch_count += width_informative_mismatch_count
        envelope_escape_count += width_envelope_escape_count
        discarded_partial_distribution.update(map(int, discarded_partials))
        product_index_offset_distribution.update(map(int, product_index_offsets))
        widths.append(
            {
                "width": width,
                "reciprocal25Index": reciprocal_significand,
                "sampleCount": corpus.NUMERATOR_COUNT,
                "matchCount": corpus.NUMERATOR_COUNT - width_mismatch_count,
                "mismatchCount": width_mismatch_count,
                "informativeSampleCount": width_informative_count,
                "informativeMismatchCount": width_informative_mismatch_count,
                "envelopeEscapeCount": width_envelope_escape_count,
                "discardedPartialMinimum": int(discarded_partials.min()),
                "discardedPartialMaximum": int(discarded_partials.max()),
                "exact": width_mismatch_count == 0 and width_envelope_escape_count == 0,
            }
        )

    exact = (
        mismatch_count == 0
        and informative_mismatch_count == 0
        and envelope_escape_count == 0
    )
    return {
        "liquidGlassRasterQuotientSelectorAnalysisSchemaVersion": 1,
        "source": str(report_path),
        "sourceSha256": sha256_file(report_path),
        "truthTable": str(truth_path),
        "truthTableSha256": sha256_file(truth_path),
        "selectedRole": "discovery",
        "holdoutOpened": False,
        "holdoutAuthorized": False,
        "model": {
            "name": "truncatedRadix2PartialProducts8Bias0x1400",
            "reciprocalInput": (
                "unique recovered 25-bit significand for each discovery width"
            ),
            "partialProductRadix": 2,
            "partialProductTruncationBits": (corpus.PARTIAL_PRODUCT_TRUNCATION_BITS),
            "partialProductTruncationUnit": (
                1 << corpus.PARTIAL_PRODUCT_TRUNCATION_BITS
            ),
            "roundingBias": corpus.PARTIAL_PRODUCT_ROUNDING_BIAS,
            "roundingBiasHex": (f"0x{corpus.PARTIAL_PRODUCT_ROUNDING_BIAS:04x}"),
            "integerExpression": (
                "A=sum((n*2^j >> 8) << 8 for every set bit j in R25); "
                "Q27=(A+0x1400) >> (bit_length(n*R25)-27)"
            ),
            "finalConversion": "round-to-nearest-even binary32",
        },
        "measurement": {
            "widthCount": len(corpus.DISCOVERY_WIDTHS),
            "normalizedNumeratorCountPerWidth": corpus.NUMERATOR_COUNT,
            "sampleCount": sample_count,
            "matchCount": sample_count - mismatch_count,
            "mismatchCount": mismatch_count,
            "matchRate": (sample_count - mismatch_count) / sample_count,
            "informativeSampleCount": informative_sample_count,
            "informativeMismatchCount": informative_mismatch_count,
            "envelopeEscapeCount": envelope_escape_count,
            "discardedPartialMinimum": min(discarded_partial_distribution),
            "discardedPartialMaximum": max(discarded_partial_distribution),
            "discardedPartialDistribution": distribution(
                discarded_partial_distribution
            ),
            "productIndexOffsetDistribution": distribution(
                product_index_offset_distribution
            ),
            "exact": exact,
        },
        "widths": widths,
        "conclusions": {
            "productSelectorFullyDeterminedForDiscoveryDomain": exact,
            "fixedFunctionQuotientFullyReproducedForDiscoveryDomain": exact,
            "portableReciprocalIndexLawFullyDetermined": False,
            "portableCombinedDividerLawFullyDetermined": False,
            "sealedHoldoutRequired": True,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    report = analyze(arguments.source)
    arguments.output.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
