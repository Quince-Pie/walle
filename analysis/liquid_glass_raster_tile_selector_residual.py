#!/usr/bin/env python3
"""Recover observational proxies for schema-3 selector counterexamples."""

import argparse
import json
from collections import Counter, defaultdict
from fractions import Fraction
from pathlib import Path
from typing import Any

import numpy as np

import liquid_glass_geometry_coordinate_gate as geometry
import liquid_glass_raster_interpolant as raster
import liquid_glass_raster_tile_numerator as recovery
import validate_raster_tile_numerator as capture


type JsonObject = dict[str, Any]

SLOPE_SEARCH_RADIUS = 128
CONSTANT_SEARCH_RADIUS = 8


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


def derivative_bits(
    local_pixel: int,
    position: float,
    slope: float,
    constant: float,
) -> int:
    if local_pixel & 1:
        left_position, right_position = position - 1.0, position
    else:
        left_position, right_position = position, position + 1.0
    left = raster.bits_float32(
        raster.apple_iterator_bits(left_position, slope, constant)
    )
    right = raster.bits_float32(
        raster.apple_iterator_bits(right_position, slope, constant)
    )
    return raster.float32_bits(raster.float32(right - left))


def recover_group(
    values: np.memmap,
    *,
    case_index: int,
    endpoint_index: int,
    capture_case: capture.CaptureCase,
    endpoint: capture.EndpointCase,
    samples: tuple[capture.SamplePosition, ...],
    axis: int,
) -> list[tuple[int, int]]:
    extent = capture_case.width if axis == 0 else capture_case.height
    delta = raster.float32_bits_fraction(
        endpoint.highBits
    ) - raster.float32_bits_fraction(endpoint.lowBits)
    ideal_slope = delta / extent
    centered = recovery.signed_quantized_slope(ideal_slope)
    step = recovery.slope_step(ideal_slope)
    pulls: list[tuple[float, int]] = []
    centers: list[tuple[float, int]] = []
    derivatives: list[tuple[int, float, int]] = []
    for sample in samples:
        coordinate = sample.x if axis == 0 else sample.y
        local_pixel = coordinate - sample.tile * capture.TILE_SIZE
        record = values[case_index, endpoint_index, sample.slot]
        pulls.extend(
            (
                local_pixel + numerator / 16,
                int(record[numerator]),
            )
            for numerator in capture.PULL_NUMERATORS
        )
        centers.append((local_pixel + 0.5, int(record[capture.PULL_COUNT])))
        derivatives.append(
            (
                local_pixel,
                local_pixel + 0.5,
                int(record[capture.PULL_COUNT + 1]),
            )
        )
    # Widely separated pull positions reject wrong slopes fastest.
    pulls.sort(key=lambda item: item[0])
    pull_order = [pulls[0], pulls[-1], *pulls[1:-1]]
    result: list[tuple[int, int]] = []
    for offset in range(-SLOPE_SEARCH_RADIUS, SLOPE_SEARCH_RADIUS + 1):
        slope = float(centered + offset * step)
        position, expected = pull_order[0]
        residual = raster.bits_float32(expected) - position * slope
        for constant_bits in neighboring_float32_bits(
            residual,
            CONSTANT_SEARCH_RADIUS,
        ):
            constant = raster.bits_float32(constant_bits)
            if not all(
                raster.pull_iterator_bits(position, slope, constant) == target
                for position, target in pull_order
            ):
                continue
            if not all(
                raster.apple_iterator_bits(position, slope, constant) == target
                for position, target in centers
            ):
                continue
            if not all(
                derivative_bits(local_pixel, position, slope, constant) == target
                for local_pixel, position, target in derivatives
            ):
                continue
            result.append((offset, constant_bits))
    return result


def group_samples(
    capture_case: capture.CaptureCase,
) -> dict[tuple[int, int, int], tuple[capture.SamplePosition, ...]]:
    grouped: dict[tuple[int, int, int], list[capture.SamplePosition]] = defaultdict(
        list
    )
    for sample in capture.sample_positions(capture_case):
        grouped[(sample.axis, sample.primitive, sample.tile)].append(sample)
    return {
        key: tuple(sorted(samples, key=lambda sample: sample.edge))
        for key, samples in grouped.items()
        if len(samples) == 2
    }


def analyze(root: Path, selector_report_path: Path) -> JsonObject:
    selector_report = json.loads(selector_report_path.read_text(encoding="utf-8"))
    examples = selector_report["measurement"]["noCandidateExamples"]
    if len(examples) != selector_report["measurement"]["noCandidateGroupCount"]:
        raise ValueError("selector report does not retain every counterexample")
    raw_path = root / "raster-tile-numerator.raw"
    values = np.memmap(
        raw_path,
        mode="r",
        dtype="<u4",
        shape=(
            len(capture.CASES),
            len(capture.ENDPOINTS),
            capture.SLOT_COUNT,
            capture.RECORD_COMPONENT_COUNT,
        ),
    )
    cases = {case.name: (index, case) for index, case in enumerate(capture.CASES)}
    endpoints = {
        endpoint.name: (index, endpoint)
        for index, endpoint in enumerate(capture.ENDPOINTS)
    }
    sample_cache = {case.name: group_samples(case) for case in capture.CASES}
    selector_table = geometry.load_selector_table(geometry.SELECTOR_TABLE_PATH)
    reports: list[JsonObject] = []
    candidate_count_distribution: Counter[int] = Counter()
    constant_count_distribution: Counter[int] = Counter()

    for example in examples:
        case_index, capture_case = cases[str(example["case"])]
        if capture_case.role == "sealed-holdout":
            raise ValueError("sealed holdout was routed into discovery recovery")
        endpoint_index, endpoint = endpoints[str(example["endpoint"])]
        axis = 0 if example["axis"] == "x" else 1
        primitive = int(example["primitive"])
        tile = int(example["tile"])
        candidates = recover_group(
            values,
            case_index=case_index,
            endpoint_index=endpoint_index,
            capture_case=capture_case,
            endpoint=endpoint,
            samples=sample_cache[capture_case.name][axis, primitive, tile],
            axis=axis,
        )
        if not candidates:
            raise ValueError(
                f"no observational candidate for {capture_case.name} "
                f"{endpoint.name} axis={axis} primitive={primitive} tile={tile}"
            )
        constants = sorted({constant for _, constant in candidates})
        extent = capture_case.width if axis == 0 else capture_case.height
        opposite_edge = capture_case.height if axis == 0 else capture_case.width
        determinant = capture_case.width * capture_case.height
        low = raster.bits_float32(endpoint.lowBits)
        high = raster.bits_float32(endpoint.highBits)
        internal_slope = geometry.internal_slope(
            high - low,
            opposite_edge=opposite_edge,
            determinant=determinant,
            reciprocal_index=geometry.reciprocal_selector(
                determinant,
                selector_table,
            ),
        )
        ideal_slope = (
            raster.float32_bits_fraction(endpoint.highBits)
            - raster.float32_bits_fraction(endpoint.lowBits)
        ) / extent
        centered = recovery.signed_quantized_slope(ideal_slope)
        step = recovery.slope_step(ideal_slope)
        internal_offset_fraction = (
            Fraction.from_float(internal_slope) - centered
        ) / step
        if internal_offset_fraction.denominator != 1:
            raise ValueError("internal slope does not lie on the recovered lattice")
        internal_offset = int(internal_offset_fraction)
        candidate_count_distribution[len(candidates)] += 1
        constant_count_distribution[len(constants)] += 1
        proposed = [int(value, 16) for value in example["candidateConstantBits"]]
        reports.append(
            {
                **{
                    key: example[key]
                    for key in (
                        "case",
                        "endpoint",
                        "axis",
                        "primitive",
                        "tile",
                        "displacement",
                    )
                },
                "candidateCount": len(candidates),
                "constantCount": len(constants),
                "constantBits": [f"0x{value:08x}" for value in constants],
                "slopeOffsetsByConstant": {
                    f"0x{constant:08x}": sorted(
                        offset
                        for offset, candidate_constant in candidates
                        if candidate_constant == constant
                    )
                    for constant in constants
                },
                "proposedConstantBits": [f"0x{value:08x}" for value in proposed],
                "internalSlopeOffset": internal_offset,
                "internalSlopeAccepted": any(
                    offset == internal_offset for offset, _ in candidates
                ),
                "minimumSignedConstantUlpDistance": min(
                    observed - predicted
                    for observed in constants
                    for predicted in proposed
                ),
                "maximumSignedConstantUlpDistance": max(
                    observed - predicted
                    for observed in constants
                    for predicted in proposed
                ),
            }
        )

    setup_offsets: dict[tuple[str, str, str, int], list[set[int]]] = defaultdict(list)
    for report in reports:
        setup_offsets[
            (
                str(report["case"]),
                str(report["endpoint"]),
                str(report["axis"]),
                int(report["primitive"]),
            )
        ].append(
            {
                int(offset)
                for offsets in report["slopeOffsetsByConstant"].values()
                for offset in offsets
            }
        )
    setup_reports: list[JsonObject] = []
    for (case_name, endpoint_name, axis, primitive), sets in sorted(
        setup_offsets.items()
    ):
        intersection = set.intersection(*sets)
        setup_reports.append(
            {
                "case": case_name,
                "endpoint": endpoint_name,
                "axis": axis,
                "primitive": primitive,
                "tileCount": len(sets),
                "candidateCount": len(intersection),
                "slopeOffsets": sorted(intersection),
            }
        )

    return {
        "liquidGlassRasterTileSelectorResidualSchemaVersion": 1,
        "source": str(root),
        "selectorReport": str(selector_report_path),
        "scope": {
            "sealedHoldoutRead": False,
            "counterexampleOnly": True,
        },
        "measurement": {
            "groupCount": len(reports),
            "allGroupsRecovered": True,
            "candidateCountDistribution": {
                str(key): value
                for key, value in sorted(candidate_count_distribution.items())
            },
            "constantCountDistribution": {
                str(key): value
                for key, value in sorted(constant_count_distribution.items())
            },
            "setupSlopeIntersections": {
                "setupCount": len(setup_reports),
                "allNonempty": all(
                    report["candidateCount"] for report in setup_reports
                ),
                "uniqueCount": sum(
                    report["candidateCount"] == 1 for report in setup_reports
                ),
                "maximumCandidateCount": max(
                    report["candidateCount"] for report in setup_reports
                ),
                "groups": setup_reports,
            },
            "groups": reports,
        },
        "conclusions": {
            "selectorLawEstablished": False,
            "sealedHoldoutAuthorized": False,
            "productionShaderAuthorized": False,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("probe", type=Path)
    parser.add_argument("selector_report", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    arguments.output.write_text(
        json.dumps(
            analyze(arguments.probe, arguments.selector_report),
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
