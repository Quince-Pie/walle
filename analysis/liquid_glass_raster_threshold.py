#!/usr/bin/env python3
"""Recover discovery-only Apple raster product-rounding thresholds."""

import argparse
import json
from collections import Counter
from fractions import Fraction
from pathlib import Path
from typing import Any

import liquid_glass_raster_interpolant as raster
import liquid_glass_raster_setup_model as setup
import liquid_glass_raster_tomography as tomography


type JsonObject = dict[str, Any]

EXPECTED_CASE_COUNT = 190
EXPECTED_DISCOVERY_CASE_COUNT = 158
EXPECTED_HOLDOUT_CASE_COUNT = 32
EXPECTED_OUTPUTS_PER_CASE = 8
SUPPORTED_RIGS = {
    17: "metal-raster-interpolant-probe-17.0.0",
    18: "metal-raster-interpolant-probe-18.0.0",
}


def fraction_record(value: Fraction) -> JsonObject:
    return {
        "numerator": value.numerator,
        "denominator": value.denominator,
        "decimal": float(value),
    }


def product_lattice(
    delta: Fraction,
    dimension: int,
) -> JsonObject:
    reciprocal = raster.quantize_binary_significand(
        Fraction(1, dimension),
        setup.RECIPROCAL_PRECISION_BITS,
    )
    product = delta * reciprocal
    exponent = raster.floor_binary_exponent(product)
    step = raster.power_of_two(exponent - setup.PRODUCT_PRECISION_BITS + 1)
    scaled = product / step
    floor_index, remainder = divmod(
        scaled.numerator,
        scaled.denominator,
    )
    fraction = Fraction(remainder, scaled.denominator)
    ceil_index = floor_index + bool(remainder)
    floor_bits = raster.round_fraction_to_float32_bits(floor_index * step)
    ceil_bits = raster.round_fraction_to_float32_bits(ceil_index * step)
    return {
        "reciprocal": reciprocal,
        "product": product,
        "step": step,
        "floorIndex": floor_index,
        "ceilIndex": ceil_index,
        "fraction": fraction,
        "floorBits": floor_bits,
        "ceilBits": ceil_bits,
    }


def threshold_classification(
    observed_bits: int,
    lattice: JsonObject,
) -> str:
    floor_matches = observed_bits == int(lattice["floorBits"])
    ceil_matches = observed_bits == int(lattice["ceilBits"])
    if floor_matches and ceil_matches:
        return "masked"
    if floor_matches:
        return "floor"
    if ceil_matches:
        return "ceil"
    return "outside-adjacent-lattice"


def load_threshold_records(
    root: Path,
) -> tuple[list[JsonObject], JsonObject]:
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    schema = manifest.get("schemaVersion")
    if manifest.get("rigVersion") != SUPPORTED_RIGS.get(schema):
        raise ValueError("raster probe schema 17 or 18 is required")
    records = manifest.get("numeratorThresholdCases", [])
    role_counts = Counter(str(record.get("role")) for record in records)
    if (
        len(records) != EXPECTED_CASE_COUNT
        or role_counts
        != Counter(
            {
                "discovery": EXPECTED_DISCOVERY_CASE_COUNT,
                "holdout": EXPECTED_HOLDOUT_CASE_COUNT,
            }
        )
        or len({str(record.get("name")) for record in records}) != len(records)
    ):
        raise ValueError("schema-17 threshold case set differs")
    for record in records:
        role = str(record["role"])
        name = str(record["name"])
        if (
            role not in name
            or int(record["normalizationShift"]) not in {0, 1}
            or len(record["deltaNumerators"]) != EXPECTED_OUTPUTS_PER_CASE
            or len(set(record["deltaNumerators"])) != EXPECTED_OUTPUTS_PER_CASE
        ):
            raise ValueError(f"{name} threshold metadata differs")
    return records, manifest


def analyze(root: Path) -> JsonObject:
    records, _manifest = load_threshold_records(root)
    discovery_records = [record for record in records if record["role"] == "discovery"]
    cases = [
        tomography.TomographyCase(root=root, record=record)
        for record in discovery_records
    ]
    base_cases = {
        case.name: case
        for case in tomography.load_tomography_cases(
            root,
            role="discovery",
        )
    }
    mask_names = {str(case.record["primitiveMaskCase"]) for case in cases}
    masks = {name: base_cases[name].primitive_mask() for name in mask_names}

    groups: list[JsonObject] = []
    y_errors: Counter[int] = Counter()
    classification_counts: Counter[str] = Counter()
    observed_values = 0
    mismatched_values = 0
    ambiguous_slopes = 0
    ambiguous_constants = 0
    for case in cases:
        base_name = str(case.record["baseCase"])
        mask_name = str(case.record["primitiveMaskCase"])
        if base_name != mask_name or base_name not in base_cases:
            raise ValueError(f"{case.name} has an invalid primitive mask")
        primitive_observations: list[tomography.TomographySlope] = []
        for delta_index in range(EXPECTED_OUTPUTS_PER_CASE):
            for axis in ("x", "y"):
                for primitive in (0, 1):
                    observation, report = tomography.recover_tomography_slope(
                        case,
                        delta_index=delta_index,
                        axis=axis,
                        primitive=primitive,
                        mask=masks[mask_name],
                    )
                    primitive_observations.append(observation)
                    observed_values += int(report["observedFloatValues"])
                    mismatched_values += int(report["mismatchedFloatValues"])
                    ambiguous_slopes += not bool(report["slopeFullyDetermined"])
                    ambiguous_constants += int(report["ambiguousTileConstantCount"])
        observations = tomography.deduplicate_primitive_observations(
            primitive_observations
        )
        if len(observations) != 16:
            raise ValueError(f"{case.name} does not recover 16 slopes")

        x_samples: list[JsonObject] = []
        for observation in observations:
            if len(observation.accepted_bits) != 1:
                raise ValueError(f"{case.name} has an ambiguous slope")
            numerator = case.numerators[observation.delta_index]
            delta = Fraction(numerator, case.denominator)
            observed_bits = next(iter(observation.accepted_bits))
            dimension = case.width if observation.axis == "x" else case.height
            if observation.axis == "y":
                direct = setup.correctly_rounded_divide(
                    delta,
                    dimension,
                )
                y_errors[observed_bits - direct] += 1
                continue

            lattice = product_lattice(delta, dimension)
            classification = threshold_classification(
                observed_bits,
                lattice,
            )
            classification_counts[classification] += 1
            x_samples.append(
                {
                    "deltaIndex": observation.delta_index,
                    "deltaNumerator": numerator,
                    "deltaDenominator": case.denominator,
                    "productFraction": fraction_record(lattice["fraction"]),
                    "floorBits": f"0x{int(lattice['floorBits']):08x}",
                    "ceilBits": f"0x{int(lattice['ceilBits']):08x}",
                    "observedBits": f"0x{observed_bits:08x}",
                    "classification": classification,
                }
            )
        x_samples.sort(key=lambda sample: int(sample["deltaIndex"]))
        floor_fractions = [
            sample["productFraction"]
            for sample in x_samples
            if sample["classification"] == "floor"
        ]
        ceil_fractions = [
            sample["productFraction"]
            for sample in x_samples
            if sample["classification"] == "ceil"
        ]
        exclusive_lower = max(
            (
                Fraction(
                    int(record["numerator"]),
                    int(record["denominator"]),
                )
                for record in floor_fractions
            ),
            default=Fraction(0),
        )
        inclusive_upper = min(
            (
                Fraction(
                    int(record["numerator"]),
                    int(record["denominator"]),
                )
                for record in ceil_fractions
            ),
            default=Fraction(1),
        )
        reciprocal = product_lattice(
            Fraction(case.numerators[0], case.denominator),
            case.width,
        )["reciprocal"]
        reciprocal_exponent = raster.floor_binary_exponent(reciprocal)
        reciprocal_step = raster.power_of_two(
            reciprocal_exponent - setup.RECIPROCAL_PRECISION_BITS + 1
        )
        reciprocal_index = int(reciprocal / reciprocal_step)
        groups.append(
            {
                "name": case.name,
                "baseCase": base_name,
                "dimension": case.width,
                "normalizationShift": int(case.record["normalizationShift"]),
                "reciprocal25Index": reciprocal_index,
                "reciprocal25LowEightBits": reciprocal_index & 0xFF,
                "thresholdInterval": {
                    "exclusiveLower": fraction_record(exclusive_lower),
                    "inclusiveUpper": fraction_record(inclusive_upper),
                    "nonempty": exclusive_lower < inclusive_upper,
                },
                "samples": x_samples,
            }
        )

    outside_count = classification_counts["outside-adjacent-lattice"]
    nonempty_count = sum(
        bool(group["thresholdInterval"]["nonempty"]) for group in groups
    )
    return {
        "liquidGlassRasterThresholdAnalysisSchemaVersion": 1,
        "probe": str(root),
        "manifestSha256": tomography.sha256_file(root / "manifest.json"),
        "selectedRole": "discovery",
        "holdoutOpened": False,
        "selection": {
            "discoveryCaseCount": len(cases),
            "holdoutCaseCount": EXPECTED_HOLDOUT_CASE_COUNT,
            "xSampleCount": len(cases) * EXPECTED_OUTPUTS_PER_CASE,
            "yControlSampleCount": len(cases) * EXPECTED_OUTPUTS_PER_CASE,
        },
        "thresholdGroups": groups,
        "thresholdModel": {
            "expression": (
                "roundFloat32(floor(product/ulp27) + "
                "(fraction(product/ulp27) >= threshold))"
            ),
            "classificationCounts": {
                key: count for key, count in sorted(classification_counts.items())
            },
            "outsideAdjacentLatticeCount": outside_count,
            "nonemptyIntervalCount": nonempty_count,
            "caseCount": len(groups),
            "allDiscoveryCasesHaveAThreshold": outside_count == 0
            and nonempty_count == len(groups),
            "denominatorBitLawFullyDetermined": False,
        },
        "powerOfTwoYControl": {
            "sampleCount": sum(y_errors.values()),
            "correctlyRoundedMatchCount": y_errors[0],
            "floatUlpErrorDistribution": {
                str(error): count for error, count in sorted(y_errors.items())
            },
            "exact": set(y_errors) == {0},
        },
        "measurement": {
            "observedFloatValues": observed_values,
            "mismatchedFloatValues": mismatched_values,
            "ambiguousSlopeModels": ambiguous_slopes,
            "ambiguousTileConstants": ambiguous_constants,
            "exact": mismatched_values == 0,
        },
        "fixedFunctionSetupFullyDetermined": False,
        "holdoutAuthorized": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("probe", type=Path)
    parser.add_argument("-o", "--output", type=Path)
    arguments = parser.parse_args()
    report = analyze(arguments.probe)
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if arguments.output is None:
        print(rendered, end="")
    else:
        arguments.output.write_text(rendered, encoding="utf-8")
    return 0 if report["measurement"]["exact"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
