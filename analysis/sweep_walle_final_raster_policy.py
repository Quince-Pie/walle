#!/usr/bin/env python3
"""Sweep AGX coefficient policies against Apple's final-highlight interpolants.

The full RGBA32UI oracle is axis-separable.  Collapse it to one expected word
for every (sample, channel, primitive, raster coordinate) tuple, then evaluate
coefficient policies against that compact oracle.  This keeps policy searches
CPU-only and avoids conflating fixed-function setup with highlight arithmetic.
"""

import argparse
import json
import sys
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any, NamedTuple

import numpy as np


type JsonObject = dict[str, Any]

ROOT = Path(__file__).resolve().parent.parent
LG_ANALYSIS = ROOT / "lg-test" / "Analysis"
if str(LG_ANALYSIS) not in sys.path:
    sys.path.insert(0, str(LG_ANALYSIS))

import raster_tile_coefficient_model_v3 as coefficient_model  # noqa: E402
import raster_tile_selector_model as raster_arithmetic  # noqa: E402

from compare_walle_dynamic_highlight_half import (  # noqa: E402
    SAMPLES,
    apple_interpolant,
    highlight_quad,
    trace_records,
)
from liquid_glass_runtime_raster_coefficients import (  # noqa: E402
    coordinate_axis_bits,
    primitive_ids,
    visible_pixel_bounds,
)


class AxisOracle(NamedTuple):
    sample: int
    channel: int
    primitive: int
    coordinates: range
    expected: np.ndarray
    present: np.ndarray


def build_oracles(
    capture: Path,
    fixtures: Path,
    samples: tuple[int, ...],
) -> tuple[list[tuple[Any, list[AxisOracle]]], JsonObject]:
    timeline = json.loads(
        (capture / "transition-timeline.json").read_text(encoding="utf-8")
    )
    records = trace_records(timeline)
    result: list[tuple[Any, list[AxisOracle]]] = []
    metadata: JsonObject = {}
    for sample in samples:
        fixture = fixtures / f"regular-dark-dematerialize-{sample:02d}"
        quad = highlight_quad(fixture)
        trace, trace_metadata = apple_interpolant(capture, records[sample])
        left, bottom, right, top = visible_pixel_bounds(quad.case)
        left = max(0, left)
        bottom = max(0, bottom)
        right = min(trace.shape[1], right)
        top = min(trace.shape[0], top)
        yy, xx = np.indices((top - bottom, right - left), dtype=np.uint32)
        xx += np.uint32(left)
        yy += np.uint32(bottom)
        primitives = primitive_ids(quad, xx, yy)
        local_trace = trace[bottom:top, left:right]
        rasterized = np.any(local_trace != 0, axis=2)
        axes: list[AxisOracle] = []
        for channel in (0, 1):
            axis = quad.channelAxes[channel]
            coordinates = range(left, right) if axis == 0 else range(bottom, top)
            coordinate_grid = xx if axis == 0 else yy
            for primitive in (0, 1):
                expected = np.zeros(len(coordinates), dtype=np.uint32)
                present = np.zeros(len(coordinates), dtype=np.bool_)
                for offset, coordinate in enumerate(coordinates):
                    selected = (
                        rasterized
                        & (primitives == primitive)
                        & (coordinate_grid == coordinate)
                    )
                    values = np.unique(local_trace[..., channel][selected])
                    if values.size == 0:
                        continue
                    if values.size != 1:
                        raise ValueError(
                            f"sample {sample} channel {channel} primitive "
                            f"{primitive} coordinate {coordinate} has "
                            f"{values.size} Apple values"
                        )
                    expected[offset] = values[0]
                    present[offset] = True
                axes.append(
                    AxisOracle(
                        sample,
                        channel,
                        primitive,
                        coordinates,
                        expected,
                        present,
                    )
                )
        result.append((quad, axes))
        metadata[str(sample)] = {
            "quadFixedBounds": [
                quad.case.originXFixed,
                quad.case.originYFixed,
                quad.case.originXFixed + quad.case.widthFixed,
                quad.case.originYFixed + quad.case.heightFixed,
            ],
            "oracleWords": sum(int(np.count_nonzero(axis.present)) for axis in axes),
            "trace": trace_metadata,
        }
    return result, metadata


def compare_policy(
    captures: list[tuple[Any, list[AxisOracle]]],
    selector_table: tuple[int, ...],
    policy: coefficient_model.CoefficientPolicy,
) -> JsonObject:
    sample_mismatches: dict[str, int] = {}
    channel_mismatches = [0, 0]
    total = 0
    mismatched = 0
    maximum_ulp_distance = 0
    for quad, axes in captures:
        sample_mismatch = 0
        for oracle in axes:
            candidate = coordinate_axis_bits(
                quad,
                channel=oracle.channel,
                primitive=oracle.primitive,
                coordinates=oracle.coordinates,
                selector_table=selector_table,
                policy=policy,
            )
            selected_candidate = candidate[oracle.present]
            selected_expected = oracle.expected[oracle.present]
            changed = selected_candidate != selected_expected
            count = int(np.count_nonzero(changed))
            total += int(selected_expected.size)
            mismatched += count
            sample_mismatch += count
            channel_mismatches[oracle.channel] += count
            if count:
                distances = np.abs(
                    selected_candidate[changed].astype(np.int64)
                    - selected_expected[changed].astype(np.int64)
                )
                maximum_ulp_distance = max(
                    maximum_ulp_distance,
                    int(distances.max(initial=0)),
                )
        sample_mismatches[str(axes[0].sample)] = sample_mismatch
    return {
        "checkedWords": total,
        "mismatchedWords": mismatched,
        "mismatchedChannels": channel_mismatches,
        "mismatchedSamples": sample_mismatches,
        "maximumUnsignedBitDistance": maximum_ulp_distance,
        "exact": mismatched == 0,
    }


def named_policies() -> list[tuple[str, coefficient_model.CoefficientPolicy]]:
    baseline = coefficient_model.MEASURED_POLICY
    result: list[tuple[str, coefficient_model.CoefficientPolicy]] = [
        ("measured", baseline)
    ]
    for value in range(0, 32):
        result.append(
            (f"slope-first-bias-{value}", replace(baseline, slope_first_bias=value))
        )
        result.append(
            (
                f"constant-first-bias-{value}",
                replace(baseline, constant_first_bias=value),
            )
        )
        result.append((f"tile-bias-{value}", replace(baseline, tile_bias=value)))
        result.append(
            (
                f"reciprocal-bias-{value}",
                replace(baseline, reciprocal_bias=value),
            )
        )
    for value in range(14, 25):
        result.append(
            (
                f"tile-truncation-{value}",
                replace(
                    baseline,
                    tile_truncation_bits=value,
                    tile_propagated_column_count=min(
                        baseline.tile_propagated_column_count,
                        value,
                    ),
                ),
            )
        )
        result.append(
            (
                f"reciprocal-truncation-{value}",
                replace(baseline, reciprocal_truncation_bits=value),
            )
        )
    for value in range(0, 20):
        result.append(
            (
                f"top-columns-{value}",
                replace(
                    baseline,
                    tile_carry_mode="top-columns",
                    tile_propagated_column_count=value,
                ),
            )
        )
    for value in range(0, 9):
        result.append(
            (
                f"sticky-{value}",
                replace(
                    baseline,
                    tile_carry_mode="sticky",
                    tile_sticky_carry_limit=value,
                ),
            )
        )
    result.append(
        ("aggregate", replace(baseline, tile_carry_mode="aggregate"))
    )
    return result


def run(arguments: argparse.Namespace) -> JsonObject:
    captures, oracle_metadata = build_oracles(
        arguments.capture,
        arguments.fixtures,
        arguments.sample_index or SAMPLES,
    )
    selectors = raster_arithmetic.load_selector_table()
    results = []
    for name, policy in named_policies():
        comparison = compare_policy(captures, selectors, policy)
        results.append(
            {
                "name": name,
                "policy": asdict(policy),
                **comparison,
            }
        )
    results.sort(
        key=lambda value: (
            value["mismatchedWords"],
            value["maximumUnsignedBitDistance"],
            value["name"],
        )
    )
    return {
        "schemaVersion": 1,
        "scope": "Apple final-highlight unique SDF axis interpolants",
        "capture": str(arguments.capture),
        "fixtures": str(arguments.fixtures),
        "oracle": oracle_metadata,
        "policyCount": len(results),
        "best": results[: arguments.report_limit],
        "exactPolicies": [value for value in results if value["exact"]],
    }


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--capture",
        type=Path,
        default=ROOT / "artifacts/local-natural-walle-current-alpha-interpolant-02",
    )
    parser.add_argument(
        "--fixtures",
        type=Path,
        default=(
            ROOT
            / "build/generated/liquid-glass"
            / "dynamic-current-alpha-interpolant-fixtures"
        ),
    )
    parser.add_argument("--sample-index", type=int, action="append")
    parser.add_argument("--report-limit", type=int, default=24)
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def main() -> int:
    arguments = parse_arguments()
    report = run(arguments)
    encoded = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if arguments.output is None:
        print(encoded, end="")
    else:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(encoded, encoding="utf-8")
        print(arguments.output)
    return 0 if report["exactPolicies"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
