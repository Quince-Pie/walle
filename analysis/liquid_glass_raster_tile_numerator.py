#!/usr/bin/env python3
"""Recover AGX per-tile affine slopes and constants from paired-edge pulls."""

import argparse
import hashlib
import json
import math
from collections import Counter, defaultdict
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any

import numpy as np

import liquid_glass_geometry_coordinate_gate as geometry
import liquid_glass_raster_interpolant as raster
import raster_tile_numerator_v2_contract as capture


type JsonObject = dict[str, Any]

SLOPE_PRECISION_BITS = 27
SLOPE_SEARCH_RADIUS = 128
CONSTANT_SEARCH_RADIUS = 8
EXPECTED_RAW_SHA256 = "82ca7103d63f4804e2cf4e0c6128874fe6513f960835c1f19166b89bcd9879fc"


@dataclass(frozen=True, slots=True)
class RecoveredCandidate:
    slope_offset: int
    slope_hex: str
    constant_bits: str


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def neighboring_float32_bits(value: float, radius: int) -> set[int]:
    center = np.float32(value)
    lower = center
    upper = center
    result = {raster.float32_bits(float(center))}
    for _ in range(radius):
        lower = np.nextafter(lower, np.float32(-np.inf))
        upper = np.nextafter(upper, np.float32(np.inf))
        result.add(raster.float32_bits(float(lower)))
        result.add(raster.float32_bits(float(upper)))
    return result


def signed_quantized_slope(value: Fraction) -> Fraction:
    sign = -1 if value < 0 else 1
    return sign * raster.quantize_binary_significand(
        abs(value),
        SLOPE_PRECISION_BITS,
    )


def slope_step(value: Fraction) -> Fraction:
    return raster.power_of_two(
        raster.floor_binary_exponent(abs(value)) - SLOPE_PRECISION_BITS + 1
    )


def recover_candidates(
    records: list[tuple[int, tuple[int, int, int, int]]],
    *,
    ideal_slope: Fraction,
    search_radius: int,
) -> list[RecoveredCandidate]:
    """Recover 27-bit slopes and float constants satisfying both edge pulls."""

    if len(records) != 2:
        raise ValueError("a paired-edge tile group is required")
    centered = signed_quantized_slope(ideal_slope)
    step = slope_step(ideal_slope)
    result: list[RecoveredCandidate] = []
    for offset in range(-search_radius, search_radius + 1):
        slope_fraction = centered + offset * step
        slope = float(slope_fraction)
        pulls: list[tuple[float, int]] = []
        centers: list[tuple[float, int]] = []
        derivatives: list[tuple[int, float, int]] = []
        for local_pixel, values in records:
            pulls.extend(
                (
                    (float(local_pixel), values[0]),
                    (float(local_pixel) + 0.9375, values[1]),
                )
            )
            centers.append((float(local_pixel) + 0.5, values[2]))
            derivatives.append((local_pixel, float(local_pixel) + 0.5, values[3]))

        position, expected = pulls[0]
        residual = raster.bits_float32(expected) - position * slope
        for constant_bits in neighboring_float32_bits(
            residual,
            CONSTANT_SEARCH_RADIUS,
        ):
            constant = raster.bits_float32(constant_bits)
            if not all(
                raster.pull_iterator_bits(position, slope, constant) == target
                for position, target in pulls
            ):
                continue
            if not all(
                raster.apple_iterator_bits(position, slope, constant) == target
                for position, target in centers
            ):
                continue
            if not all(
                raster.float32_bits(raster.float32(right - left)) == target
                for local_pixel, position, target in derivatives
                for left, right in (
                    (
                        (
                            raster.bits_float32(
                                raster.apple_iterator_bits(
                                    position - 1.0,
                                    slope,
                                    constant,
                                )
                            )
                            if local_pixel & 1
                            else raster.bits_float32(
                                raster.apple_iterator_bits(
                                    position,
                                    slope,
                                    constant,
                                )
                            )
                        ),
                        (
                            raster.bits_float32(
                                raster.apple_iterator_bits(
                                    position,
                                    slope,
                                    constant,
                                )
                            )
                            if local_pixel & 1
                            else raster.bits_float32(
                                raster.apple_iterator_bits(
                                    position + 1.0,
                                    slope,
                                    constant,
                                )
                            )
                        ),
                    ),
                )
            ):
                continue
            result.append(
                RecoveredCandidate(
                    slope_offset=offset,
                    slope_hex=slope.hex(),
                    constant_bits=f"0x{constant_bits:08x}",
                )
            )
    return result


def float32(value: float) -> float:
    return raster.float32(value)


def simple_constant_models(
    low: float,
    high: float,
    extent: int,
    displacement: int,
) -> dict[str, int]:
    """Evaluate deliberately explicit exposed-float endpoint orderings."""

    remaining = extent - displacement
    extent_float = float32(float(extent))
    displacement_float = float32(float(displacement))
    remaining_float = float32(float(remaining))
    delta = float32(high - low)
    exact = low + (high - low) * displacement / extent

    delta_product = float32(delta * displacement_float)
    delta_quotient = float32(delta_product / extent_float)
    low_product = float32(low * remaining_float)
    high_product = float32(high * displacement_float)
    low_quotient = float32(low_product / extent_float)
    high_quotient = float32(high_product / extent_float)
    low_weight = float32(remaining_float / extent_float)
    high_weight = float32(displacement_float / extent_float)
    return {
        "correctly-rounded-exact": raster.float32_bits(float32(exact)),
        "float-delta-product-divide-add": raster.float32_bits(
            float32(low + delta_quotient)
        ),
        "float-endpoint-products-add-divide": raster.float32_bits(
            float32(float32(low_product + high_product) / extent_float)
        ),
        "float-endpoint-product-divides-add": raster.float32_bits(
            float32(low_quotient + high_quotient)
        ),
        "float-rounded-weights-products-add": raster.float32_bits(
            float32(float32(low * low_weight) + float32(high * high_weight))
        ),
        "float-rounded-weights-fma": raster.float32_bits(
            float32(math.fma(high, high_weight, low * low_weight))
        ),
    }


def raw_records(root: Path) -> np.ndarray:
    raw_path = root / "raster-tile-numerator.raw"
    if (
        raw_path.stat().st_size != capture.raw_bytes()
        or sha256_file(raw_path) != EXPECTED_RAW_SHA256
    ):
        raise ValueError("paired tile-numerator evidence differs")
    return np.fromfile(raw_path, dtype="<u4").reshape(
        len(capture.CASES),
        len(capture.ENDPOINTS),
        capture.SLOT_COUNT,
        4,
    )


def paired_sample_groups(
    capture_case: capture.CaptureCase,
) -> dict[tuple[int, int, int], list[capture.SamplePosition]]:
    groups: dict[tuple[int, int, int], list[capture.SamplePosition]] = defaultdict(list)
    for sample in capture.sample_positions(capture_case):
        groups[(sample.axis, sample.primitive, sample.tile)].append(sample)
    return {
        key: sorted(samples, key=lambda sample: sample.edge)
        for key, samples in groups.items()
        if len(samples) == 2
    }


def endpoint_fraction(endpoint: capture.EndpointCase) -> tuple[Fraction, Fraction]:
    return (
        raster.float32_bits_fraction(endpoint.lowBits),
        raster.float32_bits_fraction(endpoint.highBits),
    )


def analyze(root: Path, *, slope_search_radius: int) -> JsonObject:
    values = raw_records(root)
    selector_table = geometry.load_selector_table(geometry.SELECTOR_TABLE_PATH)
    recovered_groups: list[JsonObject] = []
    constant_model_matches: Counter[str] = Counter()
    constant_model_comparisons: Counter[str] = Counter()
    recovery_count_distribution: Counter[int] = Counter()
    constant_count_distribution: Counter[int] = Counter()
    unit_span_candidates: dict[
        tuple[int, int, int, int],
        list[set[Fraction]],
    ] = defaultdict(list)

    for case_index, capture_case in enumerate(capture.CASES):
        groups = paired_sample_groups(capture_case)
        for endpoint_index, endpoint in enumerate(capture.ENDPOINTS):
            low_fraction, high_fraction = endpoint_fraction(endpoint)
            delta_fraction = high_fraction - low_fraction
            if delta_fraction == 0:
                continue
            for (axis, primitive, tile), samples in groups.items():
                extent = capture_case.width if axis == 0 else capture_case.height
                origin = capture_case.originX if axis == 0 else capture_case.originY
                records = []
                for sample in samples:
                    coordinate = sample.x if axis == 0 else sample.y
                    records.append(
                        (
                            coordinate - tile * capture.TILE_SIZE,
                            tuple(
                                int(value)
                                for value in values[
                                    case_index,
                                    endpoint_index,
                                    sample.slot,
                                ]
                            ),
                        )
                    )
                candidates = recover_candidates(
                    records,
                    ideal_slope=delta_fraction / extent,
                    search_radius=slope_search_radius,
                )
                if not candidates:
                    raise ValueError(
                        f"no candidate for {capture_case.name} {endpoint.name} "
                        f"axis={axis} primitive={primitive} tile={tile}"
                    )
                constants = sorted({value.constant_bits for value in candidates})
                recovery_count_distribution[len(candidates)] += 1
                constant_count_distribution[len(constants)] += 1
                displacement = tile * capture.TILE_SIZE - origin
                models = simple_constant_models(
                    float(low_fraction),
                    float(high_fraction),
                    extent,
                    displacement,
                )
                if len(constants) == 1:
                    observed = int(constants[0], 16)
                    for name, predicted in models.items():
                        constant_model_comparisons[name] += 1
                        constant_model_matches[name] += predicted == observed

                if abs(delta_fraction) == 1:
                    normalized = {
                        abs(Fraction.from_float(float.fromhex(value.slope_hex)))
                        for value in candidates
                    }
                    unit_span_candidates[(case_index, axis, primitive, tile)].append(
                        normalized
                    )

                recovered_groups.append(
                    {
                        "case": capture_case.name,
                        "caseRole": capture_case.role,
                        "endpoint": endpoint.name,
                        "axis": "x" if axis == 0 else "y",
                        "primitive": primitive,
                        "tile": tile,
                        "edgePixels": [
                            sample.x if axis == 0 else sample.y for sample in samples
                        ],
                        "extent": extent,
                        "origin": origin,
                        "tileDisplacement": displacement,
                        "candidateCount": len(candidates),
                        "constantCount": len(constants),
                        "constantBits": constants,
                        "minimumSlopeOffset": min(
                            value.slope_offset for value in candidates
                        ),
                        "maximumSlopeOffset": max(
                            value.slope_offset for value in candidates
                        ),
                        "slopeOffsetsByConstant": {
                            constant_bits: sorted(
                                value.slope_offset
                                for value in candidates
                                if value.constant_bits == constant_bits
                            )
                            for constant_bits in constants
                        },
                        "simpleConstantModels": {
                            name: f"0x{bits:08x}" for name, bits in models.items()
                        },
                    }
                )

    unit_span_reports: list[JsonObject] = []
    for key, candidate_sets in sorted(unit_span_candidates.items()):
        if len(candidate_sets) != 6:
            raise ValueError(f"unit-span endpoint count differs for {key}")
        intersection = set.intersection(*candidate_sets)
        if not intersection:
            raise ValueError(f"unit-span slopes conflict for {key}")
        unit_span_reports.append(
            {
                "case": capture.CASES[key[0]].name,
                "axis": "x" if key[1] == 0 else "y",
                "primitive": key[2],
                "tile": key[3],
                "sharedCandidateCount": len(intersection),
                "sharedSlopeHex": [
                    float(value).hex() for value in sorted(intersection)
                ],
            }
        )

    unique_constants = constant_count_distribution[1]
    return {
        "liquidGlassRasterTileNumeratorAnalysisSchemaVersion": 1,
        "probe": str(root),
        "rawSha256": EXPECTED_RAW_SHA256,
        "selectedRole": "discovery",
        "holdoutOpened": False,
        "configuration": {
            "slopePrecisionBits": SLOPE_PRECISION_BITS,
            "slopeSearchRadius": slope_search_radius,
            "constantSearchRadius": CONSTANT_SEARCH_RADIUS,
        },
        "measurement": {
            "pairedGroupCount": len(recovered_groups),
            "allGroupsRecovered": True,
            "uniqueConstantCount": unique_constants,
            "uniqueConstantRate": unique_constants / len(recovered_groups),
            "recoveryCandidateCountDistribution": {
                str(key): value
                for key, value in sorted(recovery_count_distribution.items())
            },
            "constantCountDistribution": {
                str(key): value
                for key, value in sorted(constant_count_distribution.items())
            },
        },
        "unitSpanSlopeLaw": {
            "groupCount": len(unit_span_reports),
            "allSixEndpointIntersectionsNonempty": True,
            "uniqueSharedSlopeCount": sum(
                report["sharedCandidateCount"] == 1 for report in unit_span_reports
            ),
            "maximumSharedCandidateCount": max(
                int(report["sharedCandidateCount"]) for report in unit_span_reports
            ),
            "groups": unit_span_reports,
        },
        "simpleConstantModels": [
            {
                "name": name,
                "matchCount": constant_model_matches[name],
                "comparisonCount": constant_model_comparisons[name],
                "matchRate": (
                    constant_model_matches[name] / constant_model_comparisons[name]
                ),
                "exact": (
                    constant_model_matches[name] == constant_model_comparisons[name]
                ),
            }
            for name in sorted(constant_model_comparisons)
        ],
        "groups": recovered_groups,
        "constantArithmeticFullyDetermined": False,
        "holdoutAuthorized": False,
        "productionShaderAuthorized": False,
        "selectorTableLoaded": len(selector_table) == geometry.SELECTOR_TABLE_COUNT,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("probe", type=Path)
    parser.add_argument("--slope-search-radius", type=int, default=SLOPE_SEARCH_RADIUS)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    report = analyze(
        arguments.probe,
        slope_search_radius=arguments.slope_search_radius,
    )
    arguments.output.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
