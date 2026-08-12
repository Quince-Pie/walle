#!/usr/bin/env python3
"""Recover numerator-dependent Apple raster divider behavior."""

import argparse
import json
from collections import Counter
from fractions import Fraction
from pathlib import Path
from typing import Any

import liquid_glass_raster_interpolant as raster
import liquid_glass_raster_tomography as tomography


type JsonObject = dict[str, Any]
INTERNAL_QUOTIENT_PRECISION_BITS = 27
INTERNAL_OFFSET_SEARCH_RADIUS = 8


def run_length_encode(errors: list[int]) -> list[JsonObject]:
    if not errors:
        return []
    runs: list[JsonObject] = []
    start = 0
    selected = errors[0]
    for index, error in enumerate(errors[1:], start=1):
        if error == selected:
            continue
        runs.append({
            "startIndex": start,
            "endIndexInclusive": index - 1,
            "errorUlp": selected,
        })
        start = index
        selected = error
    runs.append({
        "startIndex": start,
        "endIndexInclusive": len(errors) - 1,
        "errorUlp": selected,
    })
    return runs


def significand_lattice_index(
    value: Fraction,
    precision_bits: int,
) -> int:
    """Return the nearest-even index on a normalized binary lattice."""

    exponent = raster.floor_binary_exponent(value)
    step = raster.power_of_two(exponent - precision_bits + 1)
    return raster.round_fraction_to_integer_nearest_even(value / step)


def matching_lattice_offsets(
    value: Fraction,
    observed_bits: int,
    *,
    precision_bits: int,
    radius: int,
) -> list[int]:
    """Find nearby internal lattice values that round to an observation."""

    return [
        offset
        for offset in range(-radius, radius + 1)
        if raster.round_fraction_to_float32_bits(
            raster.quantize_binary_significand(
                value,
                precision_bits,
                lattice_offset=offset,
            )
        )
        == observed_bits
    ]


def minimum_magnitude_offset(offsets: list[int]) -> int:
    """Select the smallest correction without hiding an equidistant tie."""

    if not offsets:
        raise ValueError("at least one matching lattice offset is required")
    magnitude = min(abs(offset) for offset in offsets)
    selected = [
        offset
        for offset in offsets
        if abs(offset) == magnitude
    ]
    if len(selected) != 1:
        raise ValueError(
            f"equidistant lattice-offset ambiguity: {selected}"
        )
    return selected[0]


def load_numerator_cases(
    root: Path,
) -> tuple[list[tomography.TomographyCase], JsonObject]:
    manifest = json.loads(
        (root / "manifest.json").read_text(encoding="utf-8")
    )
    schema = manifest.get("schemaVersion")
    if schema not in {12, 13, 14, 15, 16, 17, 18}:
        raise ValueError(
            "raster probe schema 12 through 18 is required"
        )
    records = manifest.get("numeratorTomographyCases", [])
    expected_case_count = {
        12: 256,
        13: 256,
        14: 512,
        15: 768,
        16: 768,
        17: 768,
        18: 768,
    }[schema]
    if (
        len(records) != expected_case_count
        or any(record.get("role") != "discovery" for record in records)
    ):
        raise ValueError(
            f"{expected_case_count} discovery numerator cases are required"
        )
    return [
        tomography.TomographyCase(root=root, record=record)
        for record in records
    ], manifest


def analyze_numerators(root: Path) -> JsonObject:
    cases, manifest = load_numerator_cases(root)
    base_cases = {
        case.name: case
        for case in tomography.load_tomography_cases(
            root,
            role="discovery",
        )
    }
    masks = {
        name: base_cases[name].primitive_mask()
        for name in {
            str(case.record["primitiveMaskCase"])
            for case in cases
        }
    }

    samples: list[JsonObject] = []
    observed_values = 0
    mismatched_values = 0
    ambiguous_slopes = 0
    ambiguous_constants = 0
    nearest27_matches = 0
    nearest27_offset_distribution: Counter[int] = Counter()
    nearest27_float_error_by_low_bits: dict[
        int,
        Counter[int],
    ] = {}
    for case in cases:
        base_name = str(case.record["baseCase"])
        mask_name = str(case.record["primitiveMaskCase"])
        if base_name != mask_name or base_name not in base_cases:
            raise ValueError(
                f"{case.name} base or mask case differs"
            )
        mask = masks[mask_name]
        primitive_observations: list[
            tomography.TomographySlope
        ] = []
        for delta_index in range(8):
            for axis in ("x", "y"):
                for primitive in (0, 1):
                    observation, report = (
                        tomography.recover_tomography_slope(
                            case,
                            delta_index=delta_index,
                            axis=axis,
                            primitive=primitive,
                            mask=mask,
                        )
                    )
                    primitive_observations.append(observation)
                    observed_values += int(
                        report["observedFloatValues"]
                    )
                    mismatched_values += int(
                        report["mismatchedFloatValues"]
                    )
                    ambiguous_slopes += not bool(
                        report["slopeFullyDetermined"]
                    )
                    ambiguous_constants += int(
                        report["ambiguousTileConstantCount"]
                    )
        observations = (
            tomography.deduplicate_primitive_observations(
                primitive_observations
            )
        )
        if len(observations) != 16:
            raise ValueError(
                f"{case.name} does not recover 16 slopes"
            )
        for observation in observations:
            if len(observation.accepted_bits) != 1:
                raise ValueError(
                    f"{case.name} has an ambiguous slope"
                )
            axis_dimension = (
                case.width
                if observation.axis == "x"
                else case.height
            )
            other_dimension = (
                case.height
                if observation.axis == "x"
                else case.width
            )
            delta_numerator = case.numerators[
                observation.delta_index
            ]
            delta = Fraction(delta_numerator, case.denominator)
            setup_numerator = delta * other_dimension
            area = case.width * case.height
            direct_bits = raster.round_fraction_to_float32_bits(
                delta / axis_dimension
            )
            observed_bits = next(iter(observation.accepted_bits))
            exact_quotient = delta / axis_dimension
            nearest27 = raster.quantize_binary_significand(
                exact_quotient,
                INTERNAL_QUOTIENT_PRECISION_BITS,
            )
            nearest27_bits = raster.round_fraction_to_float32_bits(
                nearest27
            )
            nearest27_index = significand_lattice_index(
                exact_quotient,
                INTERNAL_QUOTIENT_PRECISION_BITS,
            )
            matching_offsets = matching_lattice_offsets(
                exact_quotient,
                observed_bits,
                precision_bits=INTERNAL_QUOTIENT_PRECISION_BITS,
                radius=INTERNAL_OFFSET_SEARCH_RADIUS,
            )
            if not matching_offsets:
                raise ValueError(
                    f"{case.name} slope is outside the 27-bit "
                    f"offset search radius"
                )
            minimum_offset = minimum_magnitude_offset(
                matching_offsets
            )
            nearest27_matches += observed_bits == nearest27_bits
            nearest27_offset_distribution[minimum_offset] += 1
            nearest27_float_error_by_low_bits.setdefault(
                nearest27_index & 0b111,
                Counter(),
            )[observed_bits - nearest27_bits] += 1
            samples.append({
                "name": case.name,
                "baseCase": base_name,
                "bankIndex": int(case.record["bankIndex"]),
                "deltaIndex": observation.delta_index,
                "numeratorIndex": (
                    int(case.record["bankIndex"]) * 8
                    + observation.delta_index
                ),
                "deltaNumerator": delta_numerator,
                "deltaDenominator": case.denominator,
                "axis": observation.axis,
                "axisDimension": axis_dimension,
                "otherDimension": other_dimension,
                "area": area,
                "setupNumeratorBits": (
                    f"0x{raster.round_fraction_to_float32_bits(
                        setup_numerator
                    ):08x}"
                ),
                "setupDenominatorBits": (
                    f"0x{raster.round_fraction_to_float32_bits(
                        Fraction(area)
                    ):08x}"
                ),
                "observedBits": f"0x{observed_bits:08x}",
                "correctlyRoundedDivideBits":
                    f"0x{direct_bits:08x}",
                "observedMinusCorrectlyRoundedUlp":
                    observed_bits - direct_bits,
                "nearestEven27BitQuotientHex":
                    float(nearest27).hex(),
                "nearestEven27BitSignificandLowThreeBits":
                    nearest27_index & 0b111,
                "nearestEven27BitPredictedBits":
                    f"0x{nearest27_bits:08x}",
                "observedMinusNearestEven27BitFloatUlp":
                    observed_bits - nearest27_bits,
                "matching27BitLatticeOffsetsWithinRadius":
                    matching_offsets,
                "minimumMagnitude27BitLatticeOffset":
                    minimum_offset,
            })

    by_geometry_axis: dict[
        tuple[str, str],
        list[JsonObject],
    ] = {}
    for sample in samples:
        key = (str(sample["baseCase"]), str(sample["axis"]))
        by_geometry_axis.setdefault(key, []).append(sample)

    geometry_reports: list[JsonObject] = []
    for (base_name, axis), records in sorted(
        by_geometry_axis.items()
    ):
        records.sort(key=lambda record: int(record["numeratorIndex"]))
        indices = [
            int(record["numeratorIndex"])
            for record in records
        ]
        if indices != list(range(256)):
            raise ValueError(
                f"{base_name} {axis} numerator indices differ"
            )
        errors = [
            int(record["observedMinusCorrectlyRoundedUlp"])
            for record in records
        ]
        distribution = Counter(errors)
        geometry_reports.append({
            "baseCase": base_name,
            "axis": axis,
            "width": base_cases[base_name].width,
            "height": base_cases[base_name].height,
            "area": (
                base_cases[base_name].width
                * base_cases[base_name].height
            ),
            "errorUlpDistribution": {
                str(error): count
                for error, count in sorted(distribution.items())
            },
            "errorRuns": run_length_encode(errors),
            "errorRunCount": len(run_length_encode(errors)),
            "mixedErrorSigns": (
                any(error < 0 for error in distribution)
                and any(error > 0 for error in distribution)
            ),
        })

    return {
        "liquidGlassRasterNumeratorAnalysisSchemaVersion": 1,
        "probe": str(root),
        "manifestSha256": tomography.sha256_file(
            root / "manifest.json"
        ),
        "selectedRole": "discovery",
        "holdoutOpened": False,
        "numeratorRange": {
            "count": 256,
            "first": 32_832,
            "step": 128,
            "last": 65_472,
            "denominator": 65_536,
            "normalizedInterval": "[0.5,1)",
        },
        "geometryAxes": geometry_reports,
        "samples": samples,
        "measurement": {
            "bankCaseCount": len(cases),
            "slopeCount": len(samples),
            "observedFloatValues": observed_values,
            "mismatchedFloatValues": mismatched_values,
            "ambiguousSlopeModels": ambiguous_slopes,
            "ambiguousTileConstants": ambiguous_constants,
            "exact": mismatched_values == 0,
        },
        "nearestEven27BitQuotientModel": {
            "expression": (
                "roundFloat32(roundNearestEvenSignificand("
                "exactDelta/axisDimension, 27))"
            ),
            "precisionBits": INTERNAL_QUOTIENT_PRECISION_BITS,
            "matchCount": nearest27_matches,
            "mismatchCount": len(samples) - nearest27_matches,
            "matchFraction":
                f"{nearest27_matches}/{len(samples)}",
            "matchRate": nearest27_matches / len(samples),
            "minimumMagnitudeLatticeOffsetDistribution": {
                str(offset): count
                for offset, count in sorted(
                    nearest27_offset_distribution.items()
                )
            },
            "maximumAbsoluteMinimumLatticeOffset": max(
                abs(offset)
                for offset in nearest27_offset_distribution
            ),
            "offsetSearchRadius": INTERNAL_OFFSET_SEARCH_RADIUS,
            "allSamplesExplainedWithinOffsetRadius": (
                sum(nearest27_offset_distribution.values())
                == len(samples)
            ),
            "floatUlpErrorByNearest27SignificandLowThreeBits": {
                str(low_bits): {
                    str(error): count
                    for error, count in sorted(distribution.items())
                }
                for low_bits, distribution in sorted(
                    nearest27_float_error_by_low_bits.items()
                )
            },
            "fullyDetermined": False,
        },
        "dividerFullyDetermined": False,
        "holdoutAuthorized": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Analyze schema-12 through schema-17 raster numerator "
            "tomography."
        )
    )
    parser.add_argument("probe", type=Path)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    report = analyze_numerators(arguments.probe)
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if arguments.output is None:
        print(rendered, end="")
    else:
        arguments.output.write_text(rendered, encoding="utf-8")
    return 0 if report["measurement"]["exact"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
