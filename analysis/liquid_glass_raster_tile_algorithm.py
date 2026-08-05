#!/usr/bin/env python3
"""Replay one frozen schema-3 near-equal tile interpolation algorithm."""

import argparse
import json
from collections import Counter
from fractions import Fraction
from pathlib import Path
from typing import Any

import liquid_glass_geometry_coordinate_gate as geometry
import liquid_glass_raster_interpolant as raster
import liquid_glass_raster_tile_selector as selector
import liquid_glass_raster_tile_slope_selector as slope_audit
import validate_raster_tile_numerator as capture


type JsonObject = dict[str, Any]

SLOPE_PHASE_MIDDLE_LOWER = Fraction(3, 8)
SLOPE_PHASE_MIDDLE_UPPER = Fraction(1, 2)
SLOPE_PHASE_UPPER = Fraction(15, 16)
SLOPE_PRECISION_BITS = 27
CONSTANT_PRECISION_BITS = 28
MAX_EXAMPLES = 4_096


def selected_slope(
    endpoint: capture.EndpointCase,
    extent: int,
    internal: float,
) -> tuple[str, float, Fraction]:
    delta = raster.float32_bits_fraction(
        endpoint.highBits
    ) - raster.float32_bits_fraction(endpoint.lowBits)
    if delta == 0:
        return "zero", 0.0, Fraction(0)
    sign = -1 if delta < 0 else 1
    magnitude = abs(delta) / extent
    exponent = raster.floor_binary_exponent(magnitude)
    step = raster.power_of_two(exponent - SLOPE_PRECISION_BITS + 1)
    floor_value = raster.quantize_binary_significand_directed(
        magnitude,
        SLOPE_PRECISION_BITS,
        "down",
    )
    phase = (magnitude - floor_value) / step
    if (
        SLOPE_PHASE_MIDDLE_LOWER <= phase < SLOPE_PHASE_MIDDLE_UPPER
        or phase >= SLOPE_PHASE_UPPER
    ):
        return "fixed-product", internal, phase
    return "strict-below-floor", float(sign * (floor_value - step)), phase


def constant_bits(
    capture_case: capture.CaptureCase,
    endpoint: capture.EndpointCase,
    *,
    axis: int,
    tile: int,
) -> int:
    extent = capture_case.width if axis == 0 else capture_case.height
    origin = capture_case.originX if axis == 0 else capture_case.originY
    displacement = tile * capture.TILE_SIZE - origin
    low = raster.float32_bits_fraction(endpoint.lowBits)
    high = raster.float32_bits_fraction(endpoint.highBits)
    exact = low + (high - low) * displacement / extent
    if exact == 0:
        return 0
    quantized = raster.quantize_binary_significand(
        abs(exact),
        CONSTANT_PRECISION_BITS,
    )
    return raster.round_fraction_to_float32_bits(-quantized if exact < 0 else quantized)


def record_matches(
    records: Any,
    *,
    case_index: int,
    endpoint_index: int,
    sample: capture.SamplePosition,
    axis: int,
    slope: float,
    constant: float,
) -> tuple[bool, bool, bool]:
    coordinate = sample.x if axis == 0 else sample.y
    local_pixel = coordinate - sample.tile * capture.TILE_SIZE
    actual = records[case_index, endpoint_index, sample.slot]
    pulls_exact = all(
        raster.pull_iterator_bits(
            local_pixel + numerator / 16,
            slope,
            constant,
        )
        == int(expected)
        for numerator, expected in zip(
            capture.PULL_NUMERATORS,
            actual[: capture.PULL_COUNT],
            strict=True,
        )
    )
    center_position = local_pixel + 0.5
    center_exact = raster.apple_iterator_bits(center_position, slope, constant) == int(
        actual[capture.PULL_COUNT]
    )
    derivative_exact = slope_audit.derivative_bits(
        local_pixel,
        center_position,
        slope,
        constant,
    ) == int(actual[capture.PULL_COUNT + 1])
    return pulls_exact, center_exact, derivative_exact


def analyze(root: Path) -> JsonObject:
    records = selector.raw_records(root)
    selector_table = geometry.load_selector_table(geometry.SELECTOR_TABLE_PATH)
    branch_groups: Counter[str] = Counter()
    branch_setups: set[tuple[str, str, str, int, str]] = set()
    pull_mismatches = 0
    center_mismatches = 0
    derivative_mismatches = 0
    record_count = 0
    group_count = 0
    mismatch_examples: list[JsonObject] = []

    for case_index, capture_case in enumerate(capture.CASES):
        if capture_case.role == "sealed-holdout":
            continue
        determinant = capture_case.width * capture_case.height
        reciprocal_index = geometry.reciprocal_selector(determinant, selector_table)
        for endpoint_index, endpoint in enumerate(capture.ENDPOINTS):
            if endpoint.role != selector.DISCOVERY_ENDPOINT_ROLE:
                continue
            low = raster.bits_float32(endpoint.lowBits)
            high = raster.bits_float32(endpoint.highBits)
            for (axis, primitive, tile), samples in selector.paired_sample_groups(
                capture_case
            ).items():
                extent = capture_case.width if axis == 0 else capture_case.height
                opposite_edge = capture_case.height if axis == 0 else capture_case.width
                internal = geometry.internal_slope(
                    high - low,
                    opposite_edge=opposite_edge,
                    determinant=determinant,
                    reciprocal_index=reciprocal_index,
                )
                branch, slope, phase = selected_slope(endpoint, extent, internal)
                bits = constant_bits(
                    capture_case,
                    endpoint,
                    axis=axis,
                    tile=tile,
                )
                constant = raster.bits_float32(bits)
                group_count += 1
                branch_groups[branch] += 1
                branch_setups.add(
                    (
                        capture_case.name,
                        endpoint.name,
                        "x" if axis == 0 else "y",
                        primitive,
                        branch,
                    )
                )
                for sample in samples:
                    pulls_exact, center_exact, derivative_exact = record_matches(
                        records,
                        case_index=case_index,
                        endpoint_index=endpoint_index,
                        sample=sample,
                        axis=axis,
                        slope=slope,
                        constant=constant,
                    )
                    record_count += 1
                    pull_mismatches += not pulls_exact
                    center_mismatches += not center_exact
                    derivative_mismatches += not derivative_exact
                    if (
                        not (pulls_exact and center_exact and derivative_exact)
                        and len(mismatch_examples) < MAX_EXAMPLES
                    ):
                        mismatch_examples.append(
                            {
                                "case": capture_case.name,
                                "endpoint": endpoint.name,
                                "axis": "x" if axis == 0 else "y",
                                "primitive": primitive,
                                "tile": tile,
                                "edge": sample.edge,
                                "branch": branch,
                                "phase": str(phase),
                                "pullsExact": pulls_exact,
                                "centerExact": center_exact,
                                "derivativeExact": derivative_exact,
                            }
                        )

    mismatch_count = pull_mismatches + center_mismatches + derivative_mismatches
    return {
        "liquidGlassRasterTileAlgorithmAnalysisSchemaVersion": 1,
        "source": str(root),
        "scope": {
            "endpointRole": selector.DISCOVERY_ENDPOINT_ROLE,
            "sealedHoldoutRead": False,
        },
        "algorithm": {
            "slopePrecisionBits": SLOPE_PRECISION_BITS,
            "slopeFixedProductPhaseIntervals": [
                f"[{SLOPE_PHASE_MIDDLE_LOWER},{SLOPE_PHASE_MIDDLE_UPPER})",
                f"[{SLOPE_PHASE_UPPER},1)",
            ],
            "lowerBranch": "one 27-bit lattice step below directed floor",
            "upperBranch": "established fixed-product coefficient",
            "constantPrecisionBits": CONSTANT_PRECISION_BITS,
            "constantRounding": "nearest-even then binary32 nearest-even",
            "iteratorPullRounding": "binary32 nearest-even fused multiply-add",
            "iteratorCenterRounding": "binary32 toward zero",
            "derivativeRule": "odd-minus-even within each 2x2 quad",
        },
        "measurement": {
            "groupCount": group_count,
            "recordCount": record_count,
            "branchGroupCounts": dict(sorted(branch_groups.items())),
            "branchSetupCounts": dict(
                sorted(Counter(key[-1] for key in branch_setups).items())
            ),
            "pullMismatchRecordCount": pull_mismatches,
            "centerMismatchRecordCount": center_mismatches,
            "derivativeMismatchRecordCount": derivative_mismatches,
            "totalComponentMismatchRecordCount": mismatch_count,
            "exact": mismatch_count == 0,
            "mismatchExamples": mismatch_examples,
        },
        "conclusions": {
            "discoveryAlgorithmFullyReproduced": mismatch_count == 0,
            "sealedHoldoutAuthorized": mismatch_count == 0,
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
