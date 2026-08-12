#!/usr/bin/env python3
"""Separate Apple raster divider and non-power factorization errors."""

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

import liquid_glass_raster_tomography as tomography


type JsonObject = dict[str, Any]

ORIGINAL_NAME = re.compile(
    r"^tomography-discovery-reciprocal-bin-"
    r"[0-9]{3}-(?P<width>[0-9]{3})x(?P<height>[0-9]{3})$"
)
FACTOR_NAME = re.compile(
    r"^tomography-discovery-factor-h064-w(?P<width>[0-9]{3})$"
)


def counter_report(counter: Counter[int]) -> dict[str, int]:
    return {
        str(value): count
        for value, count in sorted(counter.items())
    }


def keyed_samples(
    records: list[JsonObject],
    *,
    axis: str,
) -> dict[int, JsonObject]:
    selected = {
        int(record["numeratorIndex"]): record
        for record in records
        if record["axis"] == axis
    }
    if selected.keys() != set(range(256)):
        raise ValueError(
            f"axis {axis} does not contain 256 numerator samples"
        )
    return selected


def paired_axis_report(
    by_case: dict[str, list[JsonObject]],
    factors: dict[int, str],
    originals: list[tuple[str, int, int]],
    *,
    axis: str,
) -> JsonObject:
    divider_errors: Counter[int] = Counter()
    factorization_shifts: Counter[int] = Counter()
    final_errors: Counter[int] = Counter()
    joint: Counter[tuple[int, int]] = Counter()
    pair_reports: list[JsonObject] = []
    for original_name, width, height in sorted(originals):
        dimension = width if axis == "x" else height
        opposite_edge = height if axis == "x" else width
        factor_name = factors.get(dimension)
        if factor_name is None:
            raise ValueError(
                f"no factor control exists for {axis} dimension "
                f"{dimension}"
            )
        original = keyed_samples(
            by_case[original_name],
            axis=axis,
        )
        # A factor control's x slope has the same requested delta and
        # dimension, with an exactly power-of-two opposite edge.
        factor = keyed_samples(
            by_case[factor_name],
            axis="x",
        )
        pair_shifts: Counter[int] = Counter()
        pair_divider_errors: Counter[int] = Counter()
        pair_final_errors: Counter[int] = Counter()
        for numerator_index in range(256):
            original_sample = original[numerator_index]
            factor_sample = factor[numerator_index]
            if (
                original_sample["deltaNumerator"]
                != factor_sample["deltaNumerator"]
                or original_sample["deltaDenominator"]
                != factor_sample["deltaDenominator"]
                or int(original_sample["axisDimension"]) != dimension
                or int(factor_sample["axisDimension"]) != dimension
            ):
                raise ValueError(
                    f"{original_name} {axis} numerator pairing differs"
                )
            direct_bits = int(
                original_sample["correctlyRoundedDivideBits"],
                16,
            )
            if direct_bits != int(
                factor_sample["correctlyRoundedDivideBits"],
                16,
            ):
                raise ValueError(
                    f"{original_name} {axis} direct references differ"
                )
            factor_bits = int(factor_sample["observedBits"], 16)
            original_bits = int(
                original_sample["observedBits"],
                16,
            )
            divider_error = factor_bits - direct_bits
            factorization_shift = original_bits - factor_bits
            final_error = original_bits - direct_bits
            if final_error != divider_error + factorization_shift:
                raise ValueError(
                    "paired error decomposition is not additive"
                )
            divider_errors[divider_error] += 1
            factorization_shifts[factorization_shift] += 1
            final_errors[final_error] += 1
            joint[(divider_error, factorization_shift)] += 1
            pair_divider_errors[divider_error] += 1
            pair_shifts[factorization_shift] += 1
            pair_final_errors[final_error] += 1

        pair_reports.append({
            "originalCase": original_name,
            "factorCase": factor_name,
            "axis": axis,
            "axisDimension": dimension,
            "originalOppositeEdge": opposite_edge,
            "factorOppositeEdge": 64,
            "sampleCount": 256,
            "dividerFloatUlpErrorDistribution":
                counter_report(pair_divider_errors),
            "factorizationFloatUlpShiftDistribution":
                counter_report(pair_shifts),
            "finalFloatUlpErrorDistribution":
                counter_report(pair_final_errors),
            "factorizationNoChangeCount": pair_shifts[0],
        })

    sample_count = sum(divider_errors.values())
    cancelled_errors = sum(
        count
        for (divider_error, shift), count in joint.items()
        if divider_error != 0 and divider_error + shift == 0
    )
    introduced_errors = sum(
        count
        for (divider_error, shift), count in joint.items()
        if divider_error == 0 and shift != 0
    )
    return {
        "pairCount": len(pair_reports),
        "sampleCount": sample_count,
        "dividerFloatUlpErrorDistribution":
            counter_report(divider_errors),
        "factorizationFloatUlpShiftDistribution":
            counter_report(factorization_shifts),
        "finalFloatUlpErrorDistribution":
            counter_report(final_errors),
        "unchangedByFactorizationCount":
            factorization_shifts[0],
        "changedByFactorizationCount":
            sample_count - factorization_shifts[0],
        "dividerExactCount": divider_errors[0],
        "finalExactCount": final_errors[0],
        "netExactGainFromFactorization":
            final_errors[0] - divider_errors[0],
        "cancelledDividerErrors": cancelled_errors,
        "introducedErrors": introduced_errors,
        "jointDividerErrorAndFactorizationShift": [
            {
                "dividerFloatUlpError": divider_error,
                "factorizationFloatUlpShift": shift,
                "sampleCount": count,
            }
            for (divider_error, shift), count in sorted(
                joint.items()
            )
        ],
        "pairs": pair_reports,
    }


def aggregate_axis_reports(
    reports: list[JsonObject],
) -> JsonObject:
    def combined_distribution(field: str) -> Counter[int]:
        result: Counter[int] = Counter()
        for report in reports:
            result.update({
                int(value): int(count)
                for value, count in report[field].items()
            })
        return result

    sample_count = sum(int(report["sampleCount"]) for report in reports)
    divider = combined_distribution(
        "dividerFloatUlpErrorDistribution"
    )
    factorization = combined_distribution(
        "factorizationFloatUlpShiftDistribution"
    )
    final = combined_distribution(
        "finalFloatUlpErrorDistribution"
    )
    return {
        "axisCount": len(reports),
        "pairCount": sum(int(report["pairCount"]) for report in reports),
        "sampleCount": sample_count,
        "dividerFloatUlpErrorDistribution":
            counter_report(divider),
        "factorizationFloatUlpShiftDistribution":
            counter_report(factorization),
        "finalFloatUlpErrorDistribution":
            counter_report(final),
        "unchangedByFactorizationCount": factorization[0],
        "changedByFactorizationCount":
            sample_count - factorization[0],
        "dividerExactCount": divider[0],
        "finalExactCount": final[0],
        "netExactGainFromFactorization":
            final[0] - divider[0],
        "cancelledDividerErrors": sum(
            int(report["cancelledDividerErrors"])
            for report in reports
        ),
        "introducedErrors": sum(
            int(report["introducedErrors"])
            for report in reports
        ),
    }


def analyze_paired_numerators(report_path: Path) -> JsonObject:
    source = json.loads(report_path.read_text(encoding="utf-8"))
    if (
        source.get(
            "liquidGlassRasterNumeratorAnalysisSchemaVersion"
        )
        != 1
        or source.get("holdoutOpened") is not False
        or source.get("selectedRole") != "discovery"
        or source.get("measurement", {}).get("exact") is not True
    ):
        raise ValueError(
            "an exact discovery-only numerator report is required"
        )

    by_case: dict[str, list[JsonObject]] = {}
    for sample in source.get("samples", []):
        by_case.setdefault(
            str(sample["baseCase"]),
            [],
        ).append(sample)

    factors: dict[int, str] = {}
    originals: list[tuple[str, int, int]] = []
    for name in by_case:
        if match := FACTOR_NAME.fullmatch(name):
            width = int(match.group("width"))
            if width in factors:
                raise ValueError(
                    f"factor width {width} is duplicated"
                )
            factors[width] = name
        elif match := ORIGINAL_NAME.fullmatch(name):
            originals.append((
                name,
                int(match.group("width")),
                int(match.group("height")),
            ))
    if len(factors) not in {8, 16} or len(originals) != 8:
        raise ValueError(
            "eight originals and eight or sixteen factor controls "
            "are required"
        )

    paired_x = paired_axis_report(
        by_case,
        factors,
        originals,
        axis="x",
    )
    y_captured = all(
        height in factors
        for _, _, height in originals
    )
    paired_y = (
        paired_axis_report(
            by_case,
            factors,
            originals,
            axis="y",
        )
        if y_captured
        else None
    )
    paired_aggregate = aggregate_axis_reports(
        [paired_x, paired_y]
        if paired_y is not None
        else [paired_x]
    )

    factor_y_errors: Counter[int] = Counter()
    for factor_name in factors.values():
        for sample in keyed_samples(
            by_case[factor_name],
            axis="y",
        ).values():
            factor_y_errors[
                int(sample["observedBits"], 16)
                - int(sample["correctlyRoundedDivideBits"], 16)
            ] += 1

    return {
        "liquidGlassRasterPairedNumeratorAnalysisSchemaVersion": 1,
        "sourceReport": str(report_path),
        "sourceReportSha256": tomography.sha256_file(report_path),
        "selectedRole": "discovery",
        "holdoutOpened": False,
        "pairedXAxis": paired_x,
        "pairedYAxis": paired_y,
        "pairedAggregate": paired_aggregate,
        "powerOfTwoYAxisControl": {
            "sampleCount": sum(factor_y_errors.values()),
            "floatUlpErrorDistribution":
                counter_report(factor_y_errors),
            "exact": set(factor_y_errors) == {0},
        },
        "inference": (
            "The non-power opposite edge is a distinct setup stage: "
            "its shifts both cancel and introduce errors relative to the "
            "matched power-of-two-edge divider control."
        ),
        "pairedYAxisCaptured": y_captured,
        "setupArithmeticFullyDetermined": False,
        "holdoutAuthorized": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Separate divider and factorization errors in schema 14."
        )
    )
    parser.add_argument("report", type=Path)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    report = analyze_paired_numerators(arguments.report)
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if arguments.output is None:
        print(rendered, end="")
    else:
        arguments.output.write_text(rendered, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
