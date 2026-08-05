#!/usr/bin/env python3
"""Select schema-3 slope laws while treating each tile constant as unknown."""

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import numpy as np

import liquid_glass_geometry_coordinate_gate as geometry
import liquid_glass_raster_interpolant as raster
import liquid_glass_raster_tile_selector as selector
import validate_raster_tile_numerator as capture


type JsonObject = dict[str, Any]

CONSTANT_SEARCH_RADIUS = 2


def rounding_bounds(bits: int) -> tuple[float, float]:
    value = np.asarray([bits], dtype="<u4").view("<f4")[0]
    previous = np.nextafter(value, np.float32(-np.inf))
    following = np.nextafter(value, np.float32(np.inf))
    return (
        (float(previous) + float(value)) / 2,
        (float(value) + float(following)) / 2,
    )


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


def constant_candidates(
    records: np.memmap,
    *,
    case_index: int,
    endpoint_index: int,
    samples: tuple[capture.SamplePosition, ...],
    axis: int,
    slope: float,
) -> tuple[int, ...]:
    pulls: list[tuple[float, int]] = []
    centers: list[tuple[int, float, int, int]] = []
    lower = -np.inf
    upper = np.inf
    for sample in samples:
        coordinate = sample.x if axis == 0 else sample.y
        local_pixel = coordinate - sample.tile * capture.TILE_SIZE
        record = records[case_index, endpoint_index, sample.slot]
        for numerator, expected_word in zip(
            capture.PULL_NUMERATORS,
            record[: capture.PULL_COUNT],
            strict=True,
        ):
            position = local_pixel + numerator / 16
            expected = int(expected_word)
            pulls.append((position, expected))
            value_lower, value_upper = rounding_bounds(expected)
            lower = max(lower, value_lower - position * slope)
            upper = min(upper, value_upper - position * slope)
        centers.append(
            (
                local_pixel,
                local_pixel + 0.5,
                int(record[capture.PULL_COUNT]),
                int(record[capture.PULL_COUNT + 1]),
            )
        )
    if lower > upper:
        return ()

    center = np.float32(lower)
    if float(center) < lower:
        center = np.nextafter(center, np.float32(np.inf))
    candidates = {raster.float32_bits(float(center))}
    lower_neighbor = center
    upper_neighbor = center
    for _ in range(CONSTANT_SEARCH_RADIUS):
        lower_neighbor = np.nextafter(lower_neighbor, np.float32(-np.inf))
        upper_neighbor = np.nextafter(upper_neighbor, np.float32(np.inf))
        candidates.add(raster.float32_bits(float(lower_neighbor)))
        candidates.add(raster.float32_bits(float(upper_neighbor)))

    result: list[int] = []
    for bits in candidates:
        constant = raster.bits_float32(bits)
        if not lower <= constant <= upper:
            continue
        if not all(
            raster.pull_iterator_bits(position, slope, constant) == expected
            for position, expected in pulls
        ):
            continue
        if not all(
            raster.apple_iterator_bits(position, slope, constant) == center_expected
            and derivative_bits(
                local_pixel,
                position,
                slope,
                constant,
            )
            == derivative_expected
            for local_pixel, position, center_expected, derivative_expected in centers
        ):
            continue
        result.append(bits)
    return tuple(sorted(result))


def analyze(root: Path) -> JsonObject:
    records = selector.raw_records(root)
    selector_table = geometry.load_selector_table(geometry.SELECTOR_TABLE_PATH)
    group_signatures: Counter[tuple[str, ...]] = Counter()
    setup_groups: dict[tuple[str, str, str, int], list[set[str]]] = defaultdict(list)
    model_group_matches: Counter[str] = Counter()
    group_count = 0
    no_model_group_count = 0

    for case_index, capture_case in enumerate(capture.CASES):
        if capture_case.role == "sealed-holdout":
            continue
        groups = selector.paired_sample_groups(capture_case)
        determinant = capture_case.width * capture_case.height
        reciprocal_index = geometry.reciprocal_selector(determinant, selector_table)
        for endpoint_index, endpoint in enumerate(capture.ENDPOINTS):
            if endpoint.role != selector.DISCOVERY_ENDPOINT_ROLE:
                continue
            low = raster.bits_float32(endpoint.lowBits)
            high = raster.bits_float32(endpoint.highBits)
            for (axis, primitive, _tile), samples in groups.items():
                extent = capture_case.width if axis == 0 else capture_case.height
                opposite_edge = capture_case.height if axis == 0 else capture_case.width
                internal = geometry.internal_slope(
                    high - low,
                    opposite_edge=opposite_edge,
                    determinant=determinant,
                    reciprocal_index=reciprocal_index,
                )
                slopes = selector.ideal_slope_variants(endpoint, extent, internal)
                recovered_by_slope: dict[float, tuple[int, ...]] = {}
                accepted: set[str] = set()
                for name, slope in slopes.items():
                    constants = recovered_by_slope.get(slope)
                    if constants is None:
                        constants = constant_candidates(
                            records,
                            case_index=case_index,
                            endpoint_index=endpoint_index,
                            samples=samples,
                            axis=axis,
                            slope=slope,
                        )
                        recovered_by_slope[slope] = constants
                    if constants:
                        accepted.add(name)
                signature = tuple(sorted(accepted))
                group_count += 1
                no_model_group_count += not signature
                group_signatures[signature] += 1
                model_group_matches.update(signature)
                setup_groups[
                    (
                        capture_case.name,
                        endpoint.name,
                        "x" if axis == 0 else "y",
                        primitive,
                    )
                ].append(accepted)

    setup_reports: list[JsonObject] = []
    model_setup_matches: Counter[str] = Counter()
    no_model_setup_count = 0
    for (case_name, endpoint_name, axis, primitive), candidates in sorted(
        setup_groups.items()
    ):
        accepted = set.intersection(*candidates)
        model_setup_matches.update(accepted)
        no_model_setup_count += not accepted
        setup_reports.append(
            {
                "case": case_name,
                "endpoint": endpoint_name,
                "axis": axis,
                "primitive": primitive,
                "tileGroupCount": len(candidates),
                "acceptedModels": sorted(accepted),
            }
        )

    return {
        "liquidGlassRasterTileSlopeSelectorAnalysisSchemaVersion": 1,
        "source": str(root),
        "scope": {
            "endpointRole": selector.DISCOVERY_ENDPOINT_ROLE,
            "sealedHoldoutRead": False,
        },
        "measurement": {
            "groupCount": group_count,
            "noModelGroupCount": no_model_group_count,
            "modelGroupMatches": dict(sorted(model_group_matches.items())),
            "groupSignatures": [
                {"models": list(signature), "count": count}
                for signature, count in sorted(
                    group_signatures.items(), key=lambda item: (-item[1], item[0])
                )
            ],
            "setupCount": len(setup_reports),
            "noModelSetupCount": no_model_setup_count,
            "modelSetupMatches": dict(sorted(model_setup_matches.items())),
            "setups": setup_reports,
        },
        "conclusions": {
            "singleSlopeLawEstablished": False,
            "sealedHoldoutAuthorized": False,
            "productionShaderAuthorized": False,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("probe", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    arguments.output.write_text(
        json.dumps(analyze(arguments.probe), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
