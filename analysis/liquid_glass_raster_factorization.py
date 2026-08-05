#!/usr/bin/env python3
"""Analyze power-of-two-edge Apple raster factorization controls."""

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

import liquid_glass_raster_interpolant as raster
import liquid_glass_raster_reciprocal_sweep as reciprocal_sweep
import liquid_glass_raster_tomography as tomography


type JsonObject = dict[str, Any]
type SlopeKey = tuple[int, str]

FACTOR_NAME = re.compile(
    r"^tomography-discovery-factor-"
    r"h(?P<height>064|128)-w(?P<width>[0-9]{3})$"
)


def scale_positive_normal_bits(bits: int, power: int) -> int:
    """Scale one positive normal binary32 by an exact power of two."""

    sign = bits >> 31
    exponent = (bits >> 23) & 0xff
    if sign or not 0 < exponent < 0xff:
        raise ValueError("a positive normal binary32 value is required")
    scaled_exponent = exponent + power
    if not 0 < scaled_exponent < 0xff:
        raise ValueError("scaled value is outside the normal range")
    return (bits & 0x807fffff) | (scaled_exponent << 23)


def load_factor_cases(
    root: Path,
) -> tuple[dict[tuple[int, int], tomography.TomographyCase], JsonObject]:
    manifest = json.loads(
        (root / "manifest.json").read_text(encoding="utf-8")
    )
    if manifest.get("schemaVersion") not in {13, 14, 15}:
        raise ValueError(
            "raster probe schema 13 through 15 is required"
        )

    selected: dict[
        tuple[int, int],
        tomography.TomographyCase,
    ] = {}
    for record in manifest.get("reciprocalTomographyCases", []):
        name = str(record.get("name", ""))
        match = FACTOR_NAME.fullmatch(name)
        if match is None:
            continue
        if record.get("role") != "discovery" or "holdout" in name:
            raise ValueError(
                f"{name} is not discovery-only factorization evidence"
            )
        dimensions = (
            int(match.group("width")),
            int(match.group("height")),
        )
        crop = record.get("crop", {})
        if dimensions != (
            int(crop.get("width", -1)),
            int(crop.get("height", -1)),
        ):
            raise ValueError(f"{name} dimensions differ from its name")
        if dimensions in selected:
            raise ValueError(
                f"factorization dimensions are duplicated: {dimensions}"
            )
        selected[dimensions] = tomography.TomographyCase(
            root=root,
            record=record,
        )

    expected = {
        *((width, 64) for width in range(32, 128)),
        *((width, 128) for width in range(32, 64)),
    }
    if selected.keys() != expected:
        missing = sorted(expected - selected.keys())
        extra = sorted(selected.keys() - expected)
        raise ValueError(
            "factorization cases differ: "
            f"missing={missing}, extra={extra}"
        )
    return selected, manifest


def observation_map(
    observations: list[tomography.TomographySlope],
) -> dict[SlopeKey, int]:
    result: dict[SlopeKey, int] = {}
    for observation in observations:
        if len(observation.accepted_bits) != 1:
            raise ValueError(
                f"{observation.case.name} has an ambiguous slope"
            )
        key = (observation.delta_index, observation.axis)
        if key in result:
            raise ValueError(
                f"{observation.case.name} duplicates slope {key}"
            )
        result[key] = next(iter(observation.accepted_bits))
    if len(result) != 15:
        raise ValueError("each factorization case must expose 15 slopes")
    return result


def compare_scaled_maps(
    left: dict[SlopeKey, int],
    right: dict[SlopeKey, int],
    powers: dict[str, int],
) -> JsonObject:
    """Compare left against an exact axis-specific scaling of right."""

    if left.keys() != right.keys():
        raise ValueError("slope maps have different keys")
    mismatches: list[JsonObject] = []
    for delta_index, axis in sorted(left):
        expected = scale_positive_normal_bits(
            right[(delta_index, axis)],
            powers[axis],
        )
        observed = left[(delta_index, axis)]
        if observed != expected:
            mismatches.append({
                "deltaIndex": delta_index,
                "axis": axis,
                "observedLeftBits": f"0x{observed:08x}",
                "expectedLeftBits": f"0x{expected:08x}",
                "rightBits": (
                    f"0x{right[(delta_index, axis)]:08x}"
                ),
                "floatUlpError": observed - expected,
            })
    return {
        "comparisonCount": len(left),
        "exactCount": len(left) - len(mismatches),
        "mismatchCount": len(mismatches),
        "exact": not mismatches,
        "mismatches": mismatches,
    }


def analyze_factorization(root: Path) -> JsonObject:
    cases, manifest = load_factor_cases(root)
    slope_maps: dict[tuple[int, int], dict[SlopeKey, int]] = {}
    measurements: dict[tuple[int, int], JsonObject] = {}
    observed_values = 0
    mismatched_values = 0
    ambiguous_slopes = 0
    ambiguous_constants = 0

    direct_matches = 0
    direct_errors: Counter[int] = Counter()
    nearest27_matches = 0
    nearest27_errors: Counter[int] = Counter()
    samples: list[JsonObject] = []

    for dimensions, case in sorted(cases.items()):
        observations, measurement = (
            reciprocal_sweep.recover_case_slopes(case)
        )
        slopes = observation_map(observations)
        slope_maps[dimensions] = slopes
        measurements[dimensions] = measurement
        observed_values += int(measurement["observedFloatValues"])
        mismatched_values += int(
            measurement["mismatchedFloatValues"]
        )
        ambiguous_slopes += int(
            measurement["ambiguousSlopeModels"]
        )
        ambiguous_constants += int(
            measurement["ambiguousTileConstants"]
        )

        for observation in observations:
            observed_bits = next(iter(observation.accepted_bits))
            dimension = (
                case.width
                if observation.axis == "x"
                else case.height
            )
            exact = (
                observation.numerator
                / (case.width * case.height)
            )
            direct_bits = raster.round_fraction_to_float32_bits(exact)
            nearest27_bits = raster.round_fraction_to_float32_bits(
                raster.quantize_binary_significand(exact, 27)
            )
            direct_error = observed_bits - direct_bits
            nearest27_error = observed_bits - nearest27_bits
            direct_matches += direct_error == 0
            nearest27_matches += nearest27_error == 0
            direct_errors[direct_error] += 1
            nearest27_errors[nearest27_error] += 1
            samples.append({
                "name": case.name,
                "width": case.width,
                "height": case.height,
                "area": case.width * case.height,
                "deltaIndex": observation.delta_index,
                "axis": observation.axis,
                "axisDimension": dimension,
                "observedBits": f"0x{observed_bits:08x}",
                "correctlyRoundedDivideBits":
                    f"0x{direct_bits:08x}",
                "observedMinusCorrectlyRoundedUlp":
                    direct_error,
                "nearestEven27BitPredictedBits":
                    f"0x{nearest27_bits:08x}",
                "observedMinusNearestEven27BitFloatUlp":
                    nearest27_error,
            })

    same_width_pairs: list[JsonObject] = []
    same_area_pairs: list[JsonObject] = []
    for width in range(32, 64):
        short = slope_maps[(width, 64)]
        tall = slope_maps[(width, 128)]
        same_width_pairs.append({
            "width": width,
            "height64": cases[(width, 64)].name,
            "height128": cases[(width, 128)].name,
            "expectedHeight64FromHeight128Powers": {
                "x": 0,
                "y": 1,
            },
            **compare_scaled_maps(
                short,
                tall,
                {"x": 0, "y": 1},
            ),
        })

        wide = slope_maps[(2 * width, 64)]
        same_area_pairs.append({
            "area": width * 128,
            "height128": cases[(width, 128)].name,
            "height64": cases[(2 * width, 64)].name,
            "expectedHeight128FromHeight64Powers": {
                "x": 1,
                "y": -1,
            },
            **compare_scaled_maps(
                tall,
                wide,
                {"x": 1, "y": -1},
            ),
        })

    constant_axis_groups: list[JsonObject] = []
    for height, widths in (
        (64, range(32, 128)),
        (128, range(32, 64)),
    ):
        for delta_index in range(7):
            records = [
                (
                    width,
                    slope_maps[(width, height)][
                        (delta_index, "y")
                    ],
                )
                for width in widths
            ]
            by_bits: dict[int, list[int]] = {}
            for width, bits in records:
                by_bits.setdefault(bits, []).append(width)
            constant_axis_groups.append({
                "height": height,
                "deltaIndex": delta_index,
                "caseCount": len(records),
                "uniqueObservedBits": len(by_bits),
                "dimensionOnlyExact": len(by_bits) == 1,
                "observedBitsByWidths": [
                    {
                        "bits": f"0x{bits:08x}",
                        "widths": selected_widths,
                    }
                    for bits, selected_widths in sorted(
                        by_bits.items()
                    )
                ],
            })

    same_width_exact = sum(
        int(pair["exactCount"])
        for pair in same_width_pairs
    )
    same_area_exact = sum(
        int(pair["exactCount"])
        for pair in same_area_pairs
    )

    discovery_cases = {
        case.name: case
        for case in tomography.load_tomography_cases(
            root,
            role="discovery",
        )
    }
    transfer_base_names = sorted({
        str(record["baseCase"])
        for record in manifest.get(
            "numeratorTomographyCases",
            [],
        )
    })
    if len(transfer_base_names) != 8:
        raise ValueError(
            "eight numerator-tomography base geometries are required"
        )
    transfer_cases: list[JsonObject] = []
    transfer_comparisons = 0
    transfer_exact = 0
    transfer_observed_values = 0
    transfer_mismatched_values = 0
    transfer_ambiguous_slopes = 0
    transfer_ambiguous_constants = 0
    for name in transfer_base_names:
        case = discovery_cases[name]
        observations, transfer_measurement = (
            reciprocal_sweep.recover_case_slopes(case)
        )
        transfer_observed_values += int(
            transfer_measurement["observedFloatValues"]
        )
        transfer_mismatched_values += int(
            transfer_measurement["mismatchedFloatValues"]
        )
        transfer_ambiguous_slopes += int(
            transfer_measurement["ambiguousSlopeModels"]
        )
        transfer_ambiguous_constants += int(
            transfer_measurement["ambiguousTileConstants"]
        )
        selected = observation_map(observations)
        case_mismatches: list[JsonObject] = []
        for (delta_index, axis), observed_bits in sorted(
            selected.items()
        ):
            dimension = (
                case.width
                if axis == "x"
                else case.height
            )
            reference_case = cases.get((dimension, 64))
            if reference_case is None:
                raise ValueError(
                    f"no power-of-two-edge reference for dimension "
                    f"{dimension}"
                )
            reference_bits = slope_maps[(dimension, 64)][
                (delta_index, "x")
            ]
            transfer_comparisons += 1
            transfer_exact += observed_bits == reference_bits
            if observed_bits != reference_bits:
                case_mismatches.append({
                    "deltaIndex": delta_index,
                    "axis": axis,
                    "axisDimension": dimension,
                    "observedNonPowerEdgeBits":
                        f"0x{observed_bits:08x}",
                    "powerOfTwoEdgeReferenceBits":
                        f"0x{reference_bits:08x}",
                    "floatUlpDifference":
                        observed_bits - reference_bits,
                })
        transfer_cases.append({
            "name": name,
            "width": case.width,
            "height": case.height,
            "comparisonCount": len(selected),
            "exactCount": len(selected) - len(case_mismatches),
            "mismatchCount": len(case_mismatches),
            "exact": not case_mismatches,
            "mismatches": case_mismatches,
        })

    slope_count = len(samples)
    return {
        "liquidGlassRasterFactorizationAnalysisSchemaVersion": 1,
        "probe": str(root),
        "manifestSha256": tomography.sha256_file(
            root / "manifest.json"
        ),
        "selectedRole": "discovery",
        "holdoutOpened": False,
        "factorizationCaseCount": len(cases),
        "sameWidthScalePairs": same_width_pairs,
        "sameAreaFactorizationPairs": same_area_pairs,
        "constantAxisGroups": constant_axis_groups,
        "samples": samples,
        "modelMeasurements": {
            "correctlyRoundedDivide": {
                "matchCount": direct_matches,
                "mismatchCount": slope_count - direct_matches,
                "matchFraction":
                    f"{direct_matches}/{slope_count}",
                "floatUlpErrorDistribution": {
                    str(error): count
                    for error, count in sorted(
                        direct_errors.items()
                    )
                },
                "exact": direct_matches == slope_count,
            },
            "nearestEven27BitQuotient": {
                "matchCount": nearest27_matches,
                "mismatchCount": slope_count - nearest27_matches,
                "matchFraction":
                    f"{nearest27_matches}/{slope_count}",
                "floatUlpErrorDistribution": {
                    str(error): count
                    for error, count in sorted(
                        nearest27_errors.items()
                    )
                },
                "exact": nearest27_matches == slope_count,
            },
        },
        "scaleInvariance": {
            "sameWidthComparisonCount":
                len(same_width_pairs) * 15,
            "sameWidthExactCount": same_width_exact,
            "sameWidthExact": (
                same_width_exact == len(same_width_pairs) * 15
            ),
            "sameAreaComparisonCount":
                len(same_area_pairs) * 15,
            "sameAreaExactCount": same_area_exact,
            "sameAreaExact": (
                same_area_exact == len(same_area_pairs) * 15
            ),
        },
        "dimensionOnlyWithinPowerOfTwoEdgeControls": {
            "groupCount": len(constant_axis_groups),
            "exactGroupCount": sum(
                bool(group["dimensionOnlyExact"])
                for group in constant_axis_groups
            ),
            "fullyExplainsControls": all(
                bool(group["dimensionOnlyExact"])
                for group in constant_axis_groups
            ),
        },
        "transferToNonPowerOppositeEdges": {
            "caseCount": len(transfer_cases),
            "comparisonCount": transfer_comparisons,
            "exactCount": transfer_exact,
            "mismatchCount":
                transfer_comparisons - transfer_exact,
            "exact": transfer_exact == transfer_comparisons,
            "cases": transfer_cases,
            "measurement": {
                "observedFloatValues":
                    transfer_observed_values,
                "mismatchedFloatValues":
                    transfer_mismatched_values,
                "ambiguousSlopeModels":
                    transfer_ambiguous_slopes,
                "ambiguousTileConstants":
                    transfer_ambiguous_constants,
                "exact": transfer_mismatched_values == 0,
            },
        },
        "measurement": {
            "slopeCount": slope_count,
            "observedFloatValues": observed_values,
            "mismatchedFloatValues": mismatched_values,
            "ambiguousSlopeModels": ambiguous_slopes,
            "ambiguousTileConstants": ambiguous_constants,
            "exact": mismatched_values == 0,
        },
        "reciprocalProductFullyDetermined": False,
        "holdoutAuthorized": False,
        "manifestRigVersion": manifest["rigVersion"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Analyze schema-13 raster factorization controls."
        )
    )
    parser.add_argument("probe", type=Path)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    report = analyze_factorization(arguments.probe)
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if arguments.output is None:
        print(rendered, end="")
    else:
        arguments.output.write_text(rendered, encoding="utf-8")
    return 0 if report["measurement"]["exact"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
