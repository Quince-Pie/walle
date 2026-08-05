#!/usr/bin/env python3
"""Recover Apple's exhaustive fixed-function quotient truth table."""

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray


type JsonObject = dict[str, Any]
type UIntArray = NDArray[np.uint32]
type BoolArray = NDArray[np.bool_]

SUPPORTED_PROBE_VERSIONS = {
    20: "metal-raster-interpolant-probe-20.0.0",
    21: "metal-raster-interpolant-probe-21.0.0",
    22: "metal-raster-interpolant-probe-22.0.0",
}
HOLDOUT_WIDTHS = tuple(range(37, 128, 6))
DISCOVERY_WIDTHS = tuple(
    width for width in range(32, 128) if width not in HOLDOUT_WIDTHS
)
NUMERATOR_LOWER = 32_768
NUMERATOR_UPPER = 65_535
NUMERATOR_COUNT = NUMERATOR_UPPER - NUMERATOR_LOWER + 1
PRIMITIVE_COUNT = 2
TILE_COUNT = 5
PULL_COUNT = 2
PULL_OFFSET = 0.9375
SLOPE_SEARCH_RADIUS = 64
RECIPROCAL_OFFSET_SEARCH_RADIUS = 8
PARTIAL_PRODUCT_TRUNCATION_BITS = 8
PARTIAL_PRODUCT_ROUNDING_BIAS = 0x1400
SENTINEL = np.uint64(0xFFFFFFFFFFFFFFFF)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def expected_positions(width: int) -> list[JsonObject]:
    origin_x = 17
    origin_y = 19
    height = 64
    positions: list[JsonObject] = []
    for primitive in range(PRIMITIVE_COUNT):
        for tile in range(
            origin_x // 32,
            (origin_x + width - 1) // 32 + 1,
        ):
            lower = max(origin_x, tile * 32) - origin_x
            upper = (
                min(
                    origin_x + width - 1,
                    tile * 32 + 31,
                )
                - origin_x
            )
            local_x = upper if primitive == 0 else lower
            covered = (
                height * (2 * local_x + 1) > width
                if primitive == 0
                else height * (2 * local_x + 1) < (2 * height - 1) * width
            )
            if covered:
                positions.append(
                    {
                        "primitive": primitive,
                        "tile": tile,
                        "x": origin_x + local_x,
                        "y": (origin_y + height - 1 if primitive == 0 else origin_y),
                    }
                )
    return positions


def validate_manifest(root: Path) -> tuple[JsonObject, Path]:
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    schema_version = manifest.get("schemaVersion")
    if not isinstance(schema_version, int) or manifest.get(
        "rigVersion"
    ) != SUPPORTED_PROBE_VERSIONS.get(schema_version):
        raise ValueError("raster quotient corpus schema 20, 21, or 22 is required")
    corpus = manifest.get("quotientCorpus", {})
    expected_position_records = [
        {
            "width": width,
            "positions": expected_positions(width),
        }
        for width in DISCOVERY_WIDTHS
    ]
    expected_bytes = (
        len(DISCOVERY_WIDTHS)
        * NUMERATOR_COUNT
        * PRIMITIVE_COUNT
        * TILE_COUNT
        * PULL_COUNT
        * np.dtype("<u4").itemsize
    )
    corpus_path = root / str(corpus.get("file", ""))
    if (
        corpus.get("role") != "discovery"
        or corpus.get("widths") != list(DISCOVERY_WIDTHS)
        or corpus.get("holdoutWidthsExcluded") != list(HOLDOUT_WIDTHS)
        or corpus.get("height") != 64
        or corpus.get("originX") != 17
        or corpus.get("originY") != 19
        or corpus.get("targetWidth") != 160
        or corpus.get("targetHeight") != 160
        or corpus.get("instanceCount") != NUMERATOR_COUNT
        or corpus.get("numeratorLowerInclusive") != NUMERATOR_LOWER
        or corpus.get("numeratorUpperInclusive") != NUMERATOR_UPPER
        or corpus.get("deltaDenominator") != 65_536
        or corpus.get("primitiveCount") != PRIMITIVE_COUNT
        or corpus.get("tileCount") != TILE_COUNT
        or corpus.get("uncoveredRecordSentinel") != "0xffffffffffffffff"
        or corpus.get("positionsByWidth") != expected_position_records
        or corpus.get("bytes") != expected_bytes
        or not corpus_path.is_file()
        or corpus_path.stat().st_size != expected_bytes
        or sha256_file(corpus_path) != corpus.get("sha256")
    ):
        raise ValueError("raster quotient corpus metadata differs")
    return manifest, corpus_path


def float_values(bits: UIntArray) -> NDArray[np.float32]:
    return bits.view("<f4")


def rounding_bounds(
    bits: UIntArray,
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    values = float_values(bits)
    previous = np.nextafter(
        values,
        np.float32(-np.inf),
    ).astype(np.float64)
    following = np.nextafter(
        values,
        np.float32(np.inf),
    ).astype(np.float64)
    exact = values.astype(np.float64)
    return (previous + exact) / 2, (exact + following) / 2


def pair_accepts_slope(
    slopes: NDArray[np.float64],
    *,
    position: float,
    pulls: UIntArray,
) -> BoolArray:
    lower0, upper0 = rounding_bounds(pulls[:, 0])
    lower1, upper1 = rounding_bounds(pulls[:, 1])
    lower = np.maximum(
        lower0 - position * slopes,
        lower1 - (position + PULL_OFFSET) * slopes,
    )
    upper = np.minimum(
        upper0 - position * slopes,
        upper1 - (position + PULL_OFFSET) * slopes,
    )
    constants = np.asarray(lower, dtype=np.float32)
    constants = np.where(
        constants.astype(np.float64) < lower,
        np.nextafter(constants, np.float32(np.inf)),
        constants,
    )

    def exact(candidate: NDArray[np.float32]) -> BoolArray:
        candidate64 = candidate.astype(np.float64)
        predicted0 = np.asarray(
            position * slopes + candidate64,
            dtype="<f4",
        ).view("<u4")
        predicted1 = np.asarray(
            (position + PULL_OFFSET) * slopes + candidate64,
            dtype="<f4",
        ).view("<u4")
        return (
            (candidate64 <= upper)
            & (predicted0 == pulls[:, 0])
            & (predicted1 == pulls[:, 1])
        )

    return exact(constants) | exact(np.nextafter(constants, np.float32(np.inf)))


def correctly_rounded_bits(width: int) -> UIntArray:
    numerators = np.arange(
        NUMERATOR_LOWER,
        NUMERATOR_UPPER + 1,
        dtype=np.float64,
    )
    return np.asarray(
        numerators / (65_536 * width),
        dtype="<f4",
    ).view("<u4")


def recover_width(
    width: int,
    pulls: UIntArray,
    positions: list[JsonObject],
    *,
    search_radius: int = SLOPE_SEARCH_RADIUS,
) -> tuple[UIntArray, NDArray[np.uint16]]:
    nominal = correctly_rounded_bits(width)
    return recover_width_from_nominal(
        pulls,
        positions,
        nominal,
        search_radius=search_radius,
    )


def recover_width_from_nominal(
    pulls: UIntArray,
    positions: list[JsonObject],
    nominal: UIntArray,
    *,
    search_radius: int = SLOPE_SEARCH_RADIUS,
) -> tuple[UIntArray, NDArray[np.uint16]]:
    sample_count = int(nominal.size)
    if pulls.shape[0] != sample_count:
        raise ValueError("pull and nominal sample counts differ")
    candidate_counts = np.zeros(sample_count, dtype=np.uint16)
    selected = np.zeros(sample_count, dtype="<u4")
    for offset in range(-search_radius, search_radius + 1):
        candidate_bits = (nominal.astype(np.int64) + offset).astype("<u4")
        slopes = float_values(candidate_bits).astype(np.float64)
        accepted = np.ones(sample_count, dtype=np.bool_)
        for position_record in positions:
            primitive = int(position_record["primitive"])
            tile = int(position_record["tile"])
            position = float(int(position_record["x"]) % 32)
            accepted &= pair_accepts_slope(
                slopes,
                position=position,
                pulls=pulls[:, primitive, tile, :],
            )
            if not np.any(accepted):
                break
        candidate_counts += accepted
        selected[accepted] = candidate_bits[accepted]
    return selected, candidate_counts


def round_integer_nearest_even(
    numerator: int,
    denominator: int,
) -> int:
    quotient, remainder = divmod(numerator, denominator)
    doubled = 2 * remainder
    return quotient + (
        doubled > denominator or (doubled == denominator and quotient & 1)
    )


def reciprocal25_index(width: int) -> tuple[int, int]:
    reciprocal_exponent = -(width - 1).bit_length()
    reciprocal_significand = round_integer_nearest_even(
        1 << (24 - reciprocal_exponent),
        width,
    )
    return reciprocal_exponent, reciprocal_significand


def product27_endpoint_bits(
    width: int,
    reciprocal_significand: int,
) -> tuple[UIntArray, UIntArray, UIntArray, NDArray[np.uint64]]:
    reciprocal_exponent = -(width - 1).bit_length()
    numerators = np.arange(
        NUMERATOR_LOWER,
        NUMERATOR_UPPER + 1,
        dtype=np.uint64,
    )
    products = numerators * np.uint64(reciprocal_significand)
    _significands, bit_lengths = np.frexp(products.astype(np.float64))
    shifts = bit_lengths.astype(np.int64) - 27
    floor_indices = np.right_shift(
        products,
        shifts.astype(np.uint64),
    )
    floor_values = np.ldexp(
        floor_indices.astype(np.float64),
        reciprocal_exponent - 24 - 16 + shifts,
    )
    ceil_values = np.ldexp(
        (floor_indices + 1).astype(np.float64),
        reciprocal_exponent - 24 - 16 + shifts,
    )
    return (
        np.asarray(floor_values, dtype="<f4").view("<u4"),
        np.asarray(ceil_values, dtype="<f4").view("<u4"),
        shifts,
        products,
    )


def reciprocal25_product27_bits(
    width: int,
    *,
    reciprocal_offset: int = 0,
) -> UIntArray:
    reciprocal_exponent, reciprocal_significand = reciprocal25_index(width)
    reciprocal_significand += reciprocal_offset
    floor_bits, ceil_bits, shifts, products = product27_endpoint_bits(
        width,
        reciprocal_significand,
    )
    divisors = np.left_shift(
        np.uint64(1),
        shifts.astype(np.uint64),
    )
    indices = np.right_shift(
        products,
        shifts.astype(np.uint64),
    )
    remainders = products & (divisors - 1)
    doubled = 2 * remainders
    indices += (
        (doubled > divisors) | ((doubled == divisors) & ((indices & 1) != 0))
    ).astype(np.uint64)
    values = np.ldexp(
        indices.astype(np.float64),
        reciprocal_exponent - 24 - 16 + shifts,
    )
    rounded = np.asarray(values, dtype="<f4").view("<u4")
    if np.any((rounded != floor_bits) & (rounded != ceil_bits)):
        raise ValueError("rounded product left its faithful envelope")
    return rounded


def truncated_radix2_product27_bits(
    width: int,
    reciprocal_significand: int,
) -> tuple[UIntArray, NDArray[np.uint64], NDArray[np.uint64]]:
    """Reproduce Apple's truncated partial-product raster multiply."""
    numerators = np.arange(
        NUMERATOR_LOWER,
        NUMERATOR_UPPER + 1,
        dtype=np.uint64,
    )
    result = truncated_radix2_product_bits(
        width,
        reciprocal_significand,
        numerators,
        operand_precision_bits=16,
        partial_product_truncation_bits=PARTIAL_PRODUCT_TRUNCATION_BITS,
        rounding_bias=PARTIAL_PRODUCT_ROUNDING_BIAS,
    )
    bits = result[0]
    floor_bits, ceil_bits, _shifts, _products = product27_endpoint_bits(
        width,
        reciprocal_significand,
    )
    if np.any((bits != floor_bits) & (bits != ceil_bits)):
        raise ValueError("truncated partial-product result left its envelope")
    return result


def truncated_radix2_product_bits(
    width: int,
    reciprocal_significand: int,
    operand_significands: NDArray[np.uint64],
    *,
    operand_precision_bits: int,
    partial_product_truncation_bits: int,
    rounding_bias: int,
) -> tuple[UIntArray, NDArray[np.uint64], NDArray[np.uint64]]:
    exact_products = operand_significands * np.uint64(reciprocal_significand)
    _significands, bit_lengths = np.frexp(exact_products.astype(np.float64))
    shifts = bit_lengths.astype(np.int64) - 27
    truncated_products = np.zeros_like(exact_products)
    for bit in range(reciprocal_significand.bit_length()):
        if reciprocal_significand & (1 << bit):
            partial = np.left_shift(operand_significands, np.uint64(bit))
            truncated_products += np.left_shift(
                np.right_shift(
                    partial,
                    np.uint64(partial_product_truncation_bits),
                ),
                np.uint64(partial_product_truncation_bits),
            )
    product_indices = np.right_shift(
        truncated_products + np.uint64(rounding_bias),
        shifts.astype(np.uint64),
    )
    reciprocal_exponent = -(width - 1).bit_length()
    values = np.ldexp(
        product_indices.astype(np.float64),
        reciprocal_exponent - 24 - operand_precision_bits + shifts,
    )
    bits = np.asarray(values, dtype="<f4").view("<u4")
    return bits, exact_products - truncated_products, product_indices


def recover_reciprocal_envelope(
    width: int,
    observed: UIntArray,
) -> JsonObject:
    reciprocal_exponent, nearest_index = reciprocal25_index(width)
    candidates: list[JsonObject] = []
    for offset in range(
        -RECIPROCAL_OFFSET_SEARCH_RADIUS,
        RECIPROCAL_OFFSET_SEARCH_RADIUS + 1,
    ):
        index = nearest_index + offset
        floor_bits, ceil_bits, _shifts, _products = product27_endpoint_bits(
            width, index
        )
        accepted = (observed == floor_bits) | (observed == ceil_bits)
        if np.all(accepted):
            sensitive = floor_bits != ceil_bits
            candidates.append(
                {
                    "nearestEvenOffset": offset,
                    "reciprocal25Index": index,
                    "sensitiveSampleCount": int(np.count_nonzero(sensitive)),
                    "sensitiveFloorCount": int(
                        np.count_nonzero(sensitive & (observed == floor_bits))
                    ),
                    "sensitiveCeilCount": int(
                        np.count_nonzero(sensitive & (observed == ceil_bits))
                    ),
                }
            )
    if len(candidates) != 1:
        raise ValueError(f"width {width} has {len(candidates)} reciprocal envelopes")
    selected = candidates[0]
    exact_scale = 1 << (24 - reciprocal_exponent)
    selected["exactReciprocalErrorNumerator"] = (
        int(selected["reciprocal25Index"]) * width - exact_scale
    )
    selected["exactReciprocalErrorDenominator"] = width
    selected["unique"] = True
    return selected


def distribution(values: NDArray[np.signedinteger[Any]]) -> JsonObject:
    unique, counts = np.unique(values, return_counts=True)
    return {
        str(int(value)): int(count) for value, count in zip(unique, counts, strict=True)
    }


def run_count(values: NDArray[np.signedinteger[Any]]) -> int:
    return 1 + int(np.count_nonzero(values[1:] != values[:-1]))


def scaled_normalized_bits(
    table: UIntArray,
    width_index: int,
    numerator: int,
) -> int:
    shift = 0
    normalized = numerator
    while normalized < NUMERATOR_LOWER:
        normalized <<= 1
        shift += 1
    bits = int(table[width_index, normalized - NUMERATOR_LOWER])
    return bits - (shift << 23)


def crosscheck_residue_report(
    table: UIntArray,
    source: JsonObject,
) -> JsonObject:
    width_indices = {width: index for index, width in enumerate(DISCOVERY_WIDTHS)}
    sample_count = 0
    mismatch_count = 0
    mismatches: list[JsonObject] = []
    for group in source.get("residueGroups", []):
        width = int(group["dimension"])
        width_index = width_indices[width]
        for sample in group["samples"]:
            numerator = int(sample["deltaNumerator"])
            predicted = scaled_normalized_bits(
                table,
                width_index,
                numerator,
            )
            observed = int(str(sample["observedBits"]), 16)
            sample_count += 1
            if predicted != observed:
                mismatch_count += 1
                if len(mismatches) < 32:
                    mismatches.append(
                        {
                            "dimension": width,
                            "deltaNumerator": numerator,
                            "predictedBits": f"0x{predicted:08x}",
                            "observedBits": f"0x{observed:08x}",
                        }
                    )
    return {
        "sampleCount": sample_count,
        "mismatchCount": mismatch_count,
        "mismatches": mismatches,
        "exact": sample_count == 10_112 and mismatch_count == 0,
    }


def analyze(
    root: Path,
    *,
    residue_report: Path,
    table_path: Path,
) -> JsonObject:
    _manifest, corpus_path = validate_manifest(root)
    pulls = np.memmap(
        corpus_path,
        dtype="<u4",
        mode="r",
        shape=(
            len(DISCOVERY_WIDTHS),
            NUMERATOR_COUNT,
            PRIMITIVE_COUNT,
            TILE_COUNT,
            PULL_COUNT,
        ),
    )
    table = np.empty(
        (len(DISCOVERY_WIDTHS), NUMERATOR_COUNT),
        dtype="<u4",
    )
    candidate_distribution: Counter[int] = Counter()
    direct_errors: Counter[int] = Counter()
    staged_errors: Counter[int] = Counter()
    recovered_staged_errors: Counter[int] = Counter()
    reciprocal_offsets: Counter[int] = Counter()
    reciprocal_envelope_sensitive_count = 0
    widths_report: list[JsonObject] = []

    for width_index, width in enumerate(DISCOVERY_WIDTHS):
        positions = expected_positions(width)
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
                        raise ValueError(f"width {width} has an absent expected pull")
                elif np.any(packed != SENTINEL):
                    raise ValueError(f"width {width} has an unexpected pull")

        selected, candidate_counts = recover_width(
            width,
            pulls[width_index],
            positions,
        )
        candidate_distribution.update(map(int, candidate_counts))
        if np.any(candidate_counts != 1):
            raise ValueError(f"width {width} does not recover one unique slope")
        table[width_index] = selected
        direct = correctly_rounded_bits(width)
        staged = reciprocal25_product27_bits(width)
        reciprocal_envelope = recover_reciprocal_envelope(
            width,
            selected,
        )
        reciprocal_offset = int(reciprocal_envelope["nearestEvenOffset"])
        recovered_staged = reciprocal25_product27_bits(
            width,
            reciprocal_offset=reciprocal_offset,
        )
        direct_error = selected.astype(np.int64) - direct
        staged_error = selected.astype(np.int64) - staged
        recovered_staged_error = selected.astype(np.int64) - recovered_staged
        direct_errors.update(map(int, direct_error))
        staged_errors.update(map(int, staged_error))
        recovered_staged_errors.update(map(int, recovered_staged_error))
        reciprocal_offsets[reciprocal_offset] += 1
        reciprocal_envelope_sensitive_count += int(
            reciprocal_envelope["sensitiveSampleCount"]
        )
        widths_report.append(
            {
                "width": width,
                "sampleCount": NUMERATOR_COUNT,
                "positionCount": len(positions),
                "correctlyRoundedDivide": {
                    "matchCount": int(np.count_nonzero(direct_error == 0)),
                    "floatUlpErrorDistribution": distribution(direct_error),
                    "errorRunCount": run_count(direct_error),
                },
                "reciprocal25Product27": {
                    "matchCount": int(np.count_nonzero(staged_error == 0)),
                    "floatUlpErrorDistribution": distribution(staged_error),
                    "errorRunCount": run_count(staged_error),
                },
                "uniqueReciprocal25FaithfulProduct27Envelope": reciprocal_envelope,
                "selectedReciprocal25NearestEvenProduct27": {
                    "matchCount": int(np.count_nonzero(recovered_staged_error == 0)),
                    "floatUlpErrorDistribution": distribution(recovered_staged_error),
                    "errorRunCount": run_count(recovered_staged_error),
                },
            }
        )
        if (width_index + 1) % 10 == 0:
            print(
                f"recovered {width_index + 1}/{len(DISCOVERY_WIDTHS)} widths",
                flush=True,
            )

    table_path.write_bytes(table.tobytes(order="C"))
    residue_source = json.loads(residue_report.read_text(encoding="utf-8"))
    crosscheck = crosscheck_residue_report(table, residue_source)
    if not crosscheck["exact"]:
        raise ValueError("schema-18 residue crosscheck differs")

    sample_count = len(DISCOVERY_WIDTHS) * NUMERATOR_COUNT
    direct_match_count = direct_errors[0]
    staged_match_count = staged_errors[0]
    recovered_staged_match_count = recovered_staged_errors[0]
    production_index = DISCOVERY_WIDTHS.index(100)
    production_direct_errors = table[production_index].astype(
        np.int64
    ) - correctly_rounded_bits(100)
    width_indices = {width: index for index, width in enumerate(DISCOVERY_WIDTHS)}
    scale_pairs: list[JsonObject] = []
    for width in DISCOVERY_WIDTHS:
        scaled_width = 2 * width
        if width >= 64 or scaled_width not in width_indices:
            continue
        differences = table[width_indices[width]].astype(np.int64) - table[
            width_indices[scaled_width]
        ].astype(np.int64)
        mismatch_count = int(np.count_nonzero(differences != 1 << 23))
        scale_pairs.append(
            {
                "width": width,
                "scaledWidth": scaled_width,
                "sampleCount": NUMERATOR_COUNT,
                "expectedFloatExponentBitDifference": 1 << 23,
                "mismatchCount": mismatch_count,
                "exact": mismatch_count == 0,
            }
        )
    return {
        "liquidGlassRasterQuotientCorpusAnalysisSchemaVersion": 1,
        "probe": str(root),
        "manifestSha256": sha256_file(root / "manifest.json"),
        "corpusSha256": sha256_file(corpus_path),
        "selectedRole": "discovery",
        "holdoutOpened": False,
        "holdoutAuthorized": False,
        "measurement": {
            "widthCount": len(DISCOVERY_WIDTHS),
            "normalizedNumeratorCountPerWidth": NUMERATOR_COUNT,
            "sampleCount": sample_count,
            "candidateSlopeCountDistribution": {
                str(count): frequency
                for count, frequency in sorted(candidate_distribution.items())
            },
            "uniqueSlopeCount": candidate_distribution[1],
            "searchRadiusFloatUlps": SLOPE_SEARCH_RADIUS,
            "exact": candidate_distribution == Counter({1: sample_count}),
        },
        "schema18Crosscheck": crosscheck,
        "truthTable": {
            "file": str(table_path),
            "bytes": table_path.stat().st_size,
            "sha256": sha256_file(table_path),
            "dtype": "little-endian uint32 float bits",
            "shape": [len(DISCOVERY_WIDTHS), NUMERATOR_COUNT],
            "ordering": "width-major,numerator-major",
            "widths": list(DISCOVERY_WIDTHS),
            "numeratorLowerInclusive": NUMERATOR_LOWER,
            "numeratorUpperInclusive": NUMERATOR_UPPER,
            "discoveryDomainFullyEnumerated": True,
        },
        "models": {
            "correctlyRoundedDivide": {
                "matchCount": direct_match_count,
                "mismatchCount": sample_count - direct_match_count,
                "matchRate": direct_match_count / sample_count,
                "floatUlpErrorDistribution": {
                    str(error): count for error, count in sorted(direct_errors.items())
                },
                "exact": direct_match_count == sample_count,
            },
            "nearestEven25BitReciprocalThen27BitProduct": {
                "matchCount": staged_match_count,
                "mismatchCount": sample_count - staged_match_count,
                "matchRate": staged_match_count / sample_count,
                "floatUlpErrorDistribution": {
                    str(error): count for error, count in sorted(staged_errors.items())
                },
                "exact": staged_match_count == sample_count,
            },
            "recovered25BitReciprocalThenNearestEven27BitProduct": {
                "matchCount": recovered_staged_match_count,
                "mismatchCount": sample_count - recovered_staged_match_count,
                "matchRate": recovered_staged_match_count / sample_count,
                "floatUlpErrorDistribution": {
                    str(error): count
                    for error, count in sorted(recovered_staged_errors.items())
                },
                "exact": recovered_staged_match_count == sample_count,
            },
            "unique25BitReciprocalAndFaithful27BitProductEnvelope": {
                "reciprocalOffsetSearchRadius": RECIPROCAL_OFFSET_SEARCH_RADIUS,
                "nearestEvenOffsetDistribution": {
                    str(offset): count
                    for offset, count in sorted(reciprocal_offsets.items())
                },
                "nonNearestEvenWidths": [
                    report["width"]
                    for report in widths_report
                    if report["uniqueReciprocal25FaithfulProduct27Envelope"][
                        "nearestEvenOffset"
                    ]
                    != 0
                ],
                "sensitiveSampleCount": reciprocal_envelope_sensitive_count,
                "sampleCount": sample_count,
                "exact": True,
            },
        },
        "powerOfTwoScaleInvariance": {
            "pairCount": len(scale_pairs),
            "pairs": scale_pairs,
            "exact": all(pair["exact"] for pair in scale_pairs),
        },
        "normalizedProductionWidth100": {
            "sampleCount": NUMERATOR_COUNT,
            "correctlyRoundedMatchCount": int(
                np.count_nonzero(production_direct_errors == 0)
            ),
            "floatUlpErrorDistribution": distribution(production_direct_errors),
            "errorRunCount": run_count(production_direct_errors),
        },
        "widths": widths_report,
        "fixedFunctionSetupFullyDetermined": False,
        "dividerTruthTableFullyDeterminedForDiscoveryDomain": True,
        "reciprocal25IndexFullyDeterminedForDiscoveryDomain": True,
        "product27FaithfulEnvelopeFullyDetermined": True,
        "portableDividerLawFullyDetermined": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("--residue-report", type=Path, required=True)
    parser.add_argument("--table", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    report = analyze(
        arguments.root,
        residue_report=arguments.residue_report,
        table_path=arguments.table,
    )
    arguments.output.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
