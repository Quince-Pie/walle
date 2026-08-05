#!/usr/bin/env python3
"""Audit effective tile constants under one schema-3 slope proxy."""

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import liquid_glass_geometry_coordinate_gate as geometry
import liquid_glass_raster_interpolant as raster
import liquid_glass_raster_tile_numerator as numerator
import liquid_glass_raster_tile_selector as selector
import liquid_glass_raster_tile_slope_selector as slope_selector
import validate_raster_tile_numerator as capture


type JsonObject = dict[str, Any]


def distribution(values: Counter[int]) -> dict[str, int]:
    return {str(key): count for key, count in sorted(values.items())}


def analyze(root: Path, *, slope_name: str) -> JsonObject:
    records = selector.raw_records(root)
    selector_table = geometry.load_selector_table(geometry.SELECTOR_TABLE_PATH)
    difference_distribution: Counter[int] = Counter()
    constant_count_distribution: Counter[int] = Counter()
    case_differences: dict[str, Counter[int]] = defaultdict(Counter)
    endpoint_differences: dict[str, Counter[int]] = defaultdict(Counter)
    group_count = 0
    recovered_count = 0
    mismatch_records: list[JsonObject] = []

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
            features = selector.endpoint_features(endpoint)
            for (axis, primitive, tile), samples in groups.items():
                extent = capture_case.width if axis == 0 else capture_case.height
                opposite_edge = capture_case.height if axis == 0 else capture_case.width
                origin = capture_case.originX if axis == 0 else capture_case.originY
                displacement = tile * capture.TILE_SIZE - origin
                internal = geometry.internal_slope(
                    high - low,
                    opposite_edge=opposite_edge,
                    determinant=determinant,
                    reciprocal_index=reciprocal_index,
                )
                slopes = selector.ideal_slope_variants(endpoint, extent, internal)
                if slope_name not in slopes:
                    raise ValueError(f"unknown slope model: {slope_name}")
                slope = slopes[slope_name]
                constants = slope_selector.constant_candidates(
                    records,
                    case_index=case_index,
                    endpoint_index=endpoint_index,
                    samples=samples,
                    axis=axis,
                    slope=slope,
                )
                predicted = numerator.simple_constant_models(
                    low,
                    high,
                    extent,
                    displacement,
                )["correctly-rounded-exact"]
                group_count += 1
                constant_count_distribution[len(constants)] += 1
                if not constants:
                    continue
                recovered_count += 1
                differences = sorted({bits - predicted for bits in constants})
                if len(differences) != 1:
                    raise ValueError("effective constants have different ULP offsets")
                difference = differences[0]
                difference_distribution[difference] += 1
                case_differences[capture_case.name][difference] += 1
                endpoint_key = (
                    f"b{features['base']}:r{features['residue']}:"
                    f"s{features['span']}:rev{int(features['reverse'])}"
                )
                endpoint_differences[endpoint_key][difference] += 1
                if difference and len(mismatch_records) < 20_000:
                    mismatch_records.append(
                        {
                            "case": capture_case.name,
                            "endpoint": endpoint.name,
                            "axis": "x" if axis == 0 else "y",
                            "primitive": primitive,
                            "tile": tile,
                            "displacement": displacement,
                            "base": features["base"],
                            "residue": features["residue"],
                            "span": features["span"],
                            "reverse": features["reverse"],
                            "predictedBits": f"0x{predicted:08x}",
                            "effectiveConstantBits": [
                                f"0x{bits:08x}" for bits in constants
                            ],
                            "signedUlpDifference": difference,
                        }
                    )

    return {
        "liquidGlassRasterTileProxyAuditSchemaVersion": 1,
        "source": str(root),
        "scope": {
            "endpointRole": selector.DISCOVERY_ENDPOINT_ROLE,
            "sealedHoldoutRead": False,
            "slopeModel": slope_name,
        },
        "measurement": {
            "groupCount": group_count,
            "recoveredGroupCount": recovered_count,
            "unrecoveredGroupCount": group_count - recovered_count,
            "constantCountDistribution": distribution(constant_count_distribution),
            "effectiveMinusExactConstantUlpDistribution": distribution(
                difference_distribution
            ),
            "caseDistributions": {
                name: distribution(values)
                for name, values in sorted(case_differences.items())
            },
            "endpointFeatureDistributions": {
                name: distribution(values)
                for name, values in sorted(endpoint_differences.items())
            },
            "mismatchRecords": mismatch_records,
            "allMismatchRecordsRetained": len(mismatch_records)
            == recovered_count - difference_distribution[0],
        },
        "conclusions": {
            "constantLawEstablished": False,
            "sealedHoldoutAuthorized": False,
            "productionShaderAuthorized": False,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("probe", type=Path)
    parser.add_argument(
        "--slope",
        choices=("internal", "ideal-p27-nearest", "ideal-p27-toward-zero"),
        default="ideal-p27-toward-zero",
    )
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    arguments.output.write_text(
        json.dumps(
            analyze(arguments.probe, slope_name=arguments.slope),
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
