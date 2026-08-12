#!/usr/bin/env python3
"""Recover Apple raster product-lattice rules from schema-18 discovery data."""

import argparse
import json
import os
import sys
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from fractions import Fraction
from pathlib import Path
from typing import Any

import liquid_glass_raster_interpolant as raster
import liquid_glass_raster_setup_model as setup
import liquid_glass_raster_threshold as threshold
import liquid_glass_raster_tomography as tomography


type JsonObject = dict[str, Any]
type GroupKey = tuple[int, int]

SCHEMA_VERSION = 18
RIG_VERSION = "metal-raster-interpolant-probe-18.0.0"
EXPECTED_CASE_COUNT = 1_520
EXPECTED_DISCOVERY_CASE_COUNT = 1_264
EXPECTED_HOLDOUT_CASE_COUNT = 256
EXPECTED_GROUP_CASE_COUNT = 8
EXPECTED_GROUP_SAMPLE_COUNT = 64
EXPECTED_OUTPUTS_PER_CASE = 8
OFFSET_RADIUS = 32


def fraction_record(value: Fraction) -> JsonObject:
    return {
        "numerator": value.numerator,
        "denominator": value.denominator,
        "decimal": float(value),
    }


def round_product_index_to_float32_bits(
    index: int,
    product_exponent: int,
) -> int:
    """Round a positive 27-bit product index to binary32."""
    if index <= 0:
        raise ValueError("product index must be positive")
    discarded_bit_count = max(index.bit_length() - 24, 0)
    divisor = 1 << discarded_bit_count
    significand, remainder = divmod(index, divisor)
    doubled = 2 * remainder
    significand += doubled > divisor or (
        doubled == divisor and significand & 1
    )
    if significand == 1 << 24:
        significand >>= 1
        discarded_bit_count += 1
    if not (1 << 23 <= significand < 1 << 24):
        raise ValueError("rounded product is outside binary32 normals")
    exponent = (
        product_exponent
        + discarded_bit_count
        - 3
        + 127
    )
    if not (0 < exponent < 0xFF):
        raise ValueError("product exponent is outside binary32 normals")
    return (exponent << 23) | (significand - (1 << 23))


def reciprocal_exponent(dimension: int) -> int:
    return -(dimension - 1).bit_length()


def ratio_has_binary_exponent(
    numerator: int,
    dimension: int,
    exponent: int,
) -> bool:
    denominator = 65_536 * dimension
    scaled = numerator << -exponent
    return denominator <= scaled < 2 * denominator


def integer_product_lattice_details(
    numerator: int,
    dimension: int,
) -> tuple[int, Fraction, int]:
    reciprocal_binary_exponent = reciprocal_exponent(dimension)
    reciprocal_significand = raster.round_fraction_to_integer_nearest_even(
        Fraction(1 << (24 - reciprocal_binary_exponent), dimension)
    )
    product = numerator * reciprocal_significand
    product_shift = product.bit_length() - 27
    if product_shift <= 0:
        raise ValueError("product shift is not positive")
    modulus = 1 << product_shift
    floor_index, remainder = divmod(product, modulus)
    product_exponent = (
        product.bit_length()
        - 1
        + reciprocal_binary_exponent
        - 40
    )
    return (
        floor_index,
        Fraction(remainder, modulus),
        product_exponent,
    )


def integer_product_lattice(
    numerator: int,
    dimension: int,
) -> tuple[int, Fraction]:
    floor_index, product_fraction, _product_exponent = (
        integer_product_lattice_details(
            numerator,
            dimension,
        )
    )
    return floor_index, product_fraction


def fit_offset_threshold(
    samples: list[JsonObject],
    *,
    offset_radius: int = OFFSET_RADIUS,
) -> list[JsonObject]:
    candidates: list[JsonObject] = []
    for offset in range(-offset_radius, offset_radius + 1):
        exclusive_lower = Fraction(0)
        inclusive_upper = Fraction(1)
        informative_sample_count = 0
        for sample in samples:
            floor_index = int(sample["floorIndex"])
            product_exponent = int(sample["productExponent"])
            observed_bits = int(str(sample["observedBits"]), 16)
            product_fraction = Fraction(
                int(sample["productFraction"]["numerator"]),
                int(sample["productFraction"]["denominator"]),
            )
            floor_bits = round_product_index_to_float32_bits(
                floor_index + offset,
                product_exponent,
            )
            ceil_bits = round_product_index_to_float32_bits(
                floor_index + offset + 1,
                product_exponent,
            )
            floor_matches = floor_bits == observed_bits
            ceil_matches = ceil_bits == observed_bits
            if floor_matches and ceil_matches:
                continue
            if floor_matches:
                exclusive_lower = max(
                    exclusive_lower,
                    product_fraction,
                )
                informative_sample_count += 1
                continue
            if ceil_matches:
                inclusive_upper = min(
                    inclusive_upper,
                    product_fraction,
                )
                informative_sample_count += 1
                continue
            break
        else:
            if exclusive_lower < inclusive_upper:
                candidates.append(
                    {
                        "latticeOffset": offset,
                        "thresholdInterval": {
                            "exclusiveLower": fraction_record(
                                exclusive_lower
                            ),
                            "inclusiveUpper": fraction_record(
                                inclusive_upper
                            ),
                        },
                        "informativeSampleCount":
                            informative_sample_count,
                    }
                )
    return candidates


def candidate_interval(candidate: JsonObject) -> tuple[Fraction, Fraction]:
    interval = candidate["thresholdInterval"]
    lower = interval["exclusiveLower"]
    upper = interval["inclusiveUpper"]
    return (
        Fraction(int(lower["numerator"]), int(lower["denominator"])),
        Fraction(int(upper["numerator"]), int(upper["denominator"])),
    )


def evaluate_complete_numerator_domain(
    dimension: int,
    normalization_shift: int,
    candidates: list[JsonObject],
) -> JsonObject:
    quotient_exponent = (
        reciprocal_exponent(dimension) - normalization_shift
    )
    eligible_count = 0
    ambiguous_count = 0
    ambiguous_examples: list[JsonObject] = []
    prediction_cardinality: Counter[int] = Counter()
    for numerator in range(1, 65_536):
        if not ratio_has_binary_exponent(
            numerator,
            dimension,
            quotient_exponent,
        ):
            continue
        eligible_count += 1
        (
            floor_index,
            product_fraction,
            product_exponent,
        ) = integer_product_lattice_details(
            numerator,
            dimension,
        )
        predictions: set[int] = set()
        for candidate in candidates:
            offset = int(candidate["latticeOffset"])
            lower, upper = candidate_interval(candidate)
            if product_fraction <= lower:
                decisions = (0,)
            elif product_fraction >= upper:
                decisions = (1,)
            else:
                decisions = (0, 1)
            predictions.update(
                round_product_index_to_float32_bits(
                    floor_index + offset + decision,
                    product_exponent,
                )
                for decision in decisions
            )
        prediction_cardinality[len(predictions)] += 1
        if len(predictions) > 1:
            ambiguous_count += 1
            if len(ambiguous_examples) < 16:
                ambiguous_examples.append(
                    {
                        "deltaNumerator": numerator,
                        "deltaDenominator": 65_536,
                        "productFraction": fraction_record(
                            product_fraction
                        ),
                        "predictedBits": [
                            f"0x{bits:08x}"
                            for bits in sorted(predictions)
                        ],
                    }
                )
    return {
        "eligibleNumeratorCount": eligible_count,
        "unambiguousNumeratorCount":
            eligible_count - ambiguous_count,
        "ambiguousNumeratorCount": ambiguous_count,
        "predictionCardinalityDistribution": {
            str(cardinality): count
            for cardinality, count
            in sorted(prediction_cardinality.items())
        },
        "ambiguousExamples": ambiguous_examples,
        "observationalOutputFullyDetermined":
            eligible_count > 0 and ambiguous_count == 0,
    }


def load_residue_records(
    root: Path,
) -> tuple[list[JsonObject], JsonObject]:
    manifest = json.loads(
        (root / "manifest.json").read_text(encoding="utf-8")
    )
    if (
        manifest.get("schemaVersion") != SCHEMA_VERSION
        or manifest.get("rigVersion") != RIG_VERSION
    ):
        raise ValueError("raster probe schema 18 is required")
    records = manifest.get("numeratorResidueCases", [])
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
        or len({str(record.get("name")) for record in records})
        != len(records)
    ):
        raise ValueError("schema-18 residue case set differs")
    records_by_group: dict[GroupKey, list[JsonObject]] = defaultdict(list)
    for record in records:
        name = str(record["name"])
        role = str(record["role"])
        dimension = int(record["crop"]["width"])
        normalization_shift = int(record["normalizationShift"])
        if (
            role not in name
            or normalization_shift not in {0, 1}
            or len(record["deltaNumerators"])
            != EXPECTED_OUTPUTS_PER_CASE
            or len(set(record["deltaNumerators"]))
            != EXPECTED_OUTPUTS_PER_CASE
        ):
            raise ValueError(f"{name} residue metadata differs")
        records_by_group[(dimension, normalization_shift)].append(
            record
        )
    if (
        len(records_by_group) != 190
        or any(
            len(group) != EXPECTED_GROUP_CASE_COUNT
            for group in records_by_group.values()
        )
        or any(
            len(
                {
                    int(numerator)
                    for record in group
                    for numerator in record["deltaNumerators"]
                }
            )
            != EXPECTED_GROUP_SAMPLE_COUNT
            for group in records_by_group.values()
        )
    ):
        raise ValueError("schema-18 residue groups differ")
    return records, manifest


def recover_case_samples(
    case: tomography.TomographyCase,
    *,
    mask: tomography.UIntSurface,
) -> tuple[list[JsonObject], Counter[int], JsonObject]:
    primitive_observations: list[tomography.TomographySlope] = []
    observed_values = 0
    mismatched_values = 0
    ambiguous_slopes = 0
    ambiguous_constants = 0
    for delta_index in range(EXPECTED_OUTPUTS_PER_CASE):
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
                observed_values += int(report["observedFloatValues"])
                mismatched_values += int(
                    report["mismatchedFloatValues"]
                )
                ambiguous_slopes += not bool(
                    report["slopeFullyDetermined"]
                )
                ambiguous_constants += int(
                    report["ambiguousTileConstantCount"]
                )
    observations = tomography.deduplicate_primitive_observations(
        primitive_observations
    )
    if len(observations) != 16:
        raise ValueError(f"{case.name} does not recover 16 slopes")

    x_samples: list[JsonObject] = []
    y_errors: Counter[int] = Counter()
    dimension = case.width
    for observation in observations:
        if len(observation.accepted_bits) != 1:
            raise ValueError(f"{case.name} has an ambiguous slope")
        numerator = case.numerators[observation.delta_index]
        delta = Fraction(numerator, case.denominator)
        observed_bits = next(iter(observation.accepted_bits))
        if observation.axis == "y":
            direct = setup.correctly_rounded_divide(
                delta,
                case.height,
            )
            y_errors[observed_bits - direct] += 1
            continue
        lattice = threshold.product_lattice(delta, dimension)
        x_samples.append(
            {
                "case": case.name,
                "thresholdTargetNumerator": int(
                    case.record["thresholdTargetNumerator"]
                ),
                "thresholdTargetDenominator": int(
                    case.record["thresholdTargetDenominator"]
                ),
                "deltaIndex": observation.delta_index,
                "deltaNumerator": numerator,
                "deltaDenominator": case.denominator,
                "productExponent":
                    raster.floor_binary_exponent(
                        lattice["product"]
                    ),
                "floorIndex": int(lattice["floorIndex"]),
                "floorResidueModulo8":
                    int(lattice["floorIndex"]) & 7,
                "productFraction": fraction_record(
                    lattice["fraction"]
                ),
                "observedBits": f"0x{observed_bits:08x}",
            }
        )
    x_samples.sort(
        key=lambda sample: (
            int(sample["thresholdTargetNumerator"]),
            int(sample["deltaIndex"]),
        )
    )
    return (
        x_samples,
        y_errors,
        {
            "observedFloatValues": observed_values,
            "mismatchedFloatValues": mismatched_values,
            "ambiguousSlopeModels": ambiguous_slopes,
            "ambiguousTileConstants": ambiguous_constants,
        },
    )


def recover_group(
    task: tuple[
        str,
        GroupKey,
        list[JsonObject],
        JsonObject,
    ],
) -> tuple[JsonObject, dict[int, int], JsonObject]:
    root_text, group_key, records, base_record = task
    root = Path(root_text)
    base_case = tomography.TomographyCase(
        root=root,
        record=base_record,
    )
    mask = base_case.primitive_mask()
    samples: list[JsonObject] = []
    y_errors: Counter[int] = Counter()
    measurement: Counter[str] = Counter()
    for record in records:
        case = tomography.TomographyCase(
            root=root,
            record=record,
        )
        base_name = str(record["baseCase"])
        if (
            base_name != base_case.name
            or record["primitiveMaskCase"] != base_name
        ):
            raise ValueError(f"{case.name} has an invalid primitive mask")
        case_samples, case_y_errors, case_measurement = (
            recover_case_samples(
                case,
                mask=mask,
            )
        )
        samples.extend(case_samples)
        y_errors.update(case_y_errors)
        measurement.update(case_measurement)
    if len(samples) != EXPECTED_GROUP_SAMPLE_COUNT:
        raise ValueError(
            f"{group_key} discovery residue sample count differs"
        )
    dimension, normalization_shift = group_key
    candidates = fit_offset_threshold(samples)
    domain = evaluate_complete_numerator_domain(
        dimension,
        normalization_shift,
        candidates,
    )
    return (
        {
            "dimension": dimension,
            "normalizationShift": normalization_shift,
            "sampleCount": len(samples),
            "samples": samples,
            "offsetThresholdCandidates": candidates,
            "completeBranchDomain": domain,
        },
        dict(y_errors),
        dict(measurement),
    )


def analyze(
    root: Path,
    *,
    jobs: int | None = None,
) -> JsonObject:
    records, _manifest = load_residue_records(root)
    discovery_records = [
        record
        for record in records
        if record["role"] == "discovery"
    ]
    base_cases = {
        case.name: case.record
        for case in tomography.load_tomography_cases(
            root,
            role="discovery",
        )
    }
    records_by_group: dict[GroupKey, list[JsonObject]] = defaultdict(
        list
    )
    for record in discovery_records:
        records_by_group[
            (
                int(record["crop"]["width"]),
                int(record["normalizationShift"]),
            )
        ].append(record)
    if (
        len(records_by_group) != 158
        or any(
            len(group) != EXPECTED_GROUP_CASE_COUNT
            for group in records_by_group.values()
        )
    ):
        raise ValueError("discovery residue case groups differ")
    tasks = []
    for group_key, group_records in sorted(records_by_group.items()):
        group_records.sort(
            key=lambda record: int(
                record["thresholdTargetNumerator"]
            )
        )
        base_name = str(group_records[0]["baseCase"])
        if (
            base_name not in base_cases
            or any(
                record["baseCase"] != base_name
                or record["primitiveMaskCase"] != base_name
                for record in group_records
            )
        ):
            raise ValueError(f"{group_key} has an invalid primitive mask")
        tasks.append(
            (
                str(root),
                group_key,
                group_records,
                base_cases[base_name],
            )
        )

    worker_count = min(
        jobs if jobs is not None else (os.process_cpu_count() or 1),
        len(tasks),
    )
    if worker_count <= 0:
        raise ValueError("analysis worker count must be positive")
    if worker_count == 1:
        recovered_groups = map(recover_group, tasks)
    else:
        executor = ProcessPoolExecutor(max_workers=worker_count)
        recovered_groups = executor.map(
            recover_group,
            tasks,
            chunksize=1,
        )

    groups: list[JsonObject] = []
    y_errors: Counter[int] = Counter()
    measurement: Counter[str] = Counter()
    candidate_count_distribution: Counter[int] = Counter()
    ambiguous_domain_numerators = 0
    for index, (
        group,
        group_y_errors,
        group_measurement,
    ) in enumerate(recovered_groups, start=1):
        groups.append(group)
        y_errors.update(group_y_errors)
        measurement.update(group_measurement)
        candidates = group["offsetThresholdCandidates"]
        domain = group["completeBranchDomain"]
        candidate_count_distribution[len(candidates)] += 1
        ambiguous_domain_numerators += int(
            domain["ambiguousNumeratorCount"]
        )
        if index % 16 == 0 or index == len(tasks):
            print(
                f"recovered {index}/{len(tasks)} "
                "discovery width/branch groups",
                file=sys.stderr,
                flush=True,
            )
    if worker_count != 1:
        executor.shutdown()

    exact_measurement = (
        measurement["mismatchedFloatValues"] == 0
        and measurement["ambiguousSlopeModels"] == 0
        and measurement["ambiguousTileConstants"] == 0
    )
    all_groups_have_a_model = all(
        group["offsetThresholdCandidates"] for group in groups
    )
    output_fully_determined = all(
        group["completeBranchDomain"][
            "observationalOutputFullyDetermined"
        ]
        for group in groups
    )
    return {
        "liquidGlassRasterResidueAnalysisSchemaVersion": 1,
        "probe": str(root),
        "manifestSha256": tomography.sha256_file(
            root / "manifest.json"
        ),
        "selectedRole": "discovery",
        "holdoutOpened": False,
        "selection": {
            "discoveryCaseCount": len(discovery_records),
            "discoveryGroupCount": len(groups),
            "holdoutCaseCount": EXPECTED_HOLDOUT_CASE_COUNT,
            "samplesPerGroup": EXPECTED_GROUP_SAMPLE_COUNT,
            "xSampleCount":
                len(groups) * EXPECTED_GROUP_SAMPLE_COUNT,
            "yControlSampleCount":
                len(groups) * EXPECTED_GROUP_SAMPLE_COUNT,
        },
        "residueGroups": groups,
        "offsetThresholdModel": {
            "expression": (
                "roundFloat32((floor(product/ulp27) + latticeOffset "
                "+ (fraction(product/ulp27) >= threshold)) * ulp27)"
            ),
            "offsetSearchRadius": OFFSET_RADIUS,
            "candidateCountDistribution": {
                str(count): groups_with_count
                for count, groups_with_count
                in sorted(candidate_count_distribution.items())
            },
            "allDiscoveryGroupsHaveAModel":
                all_groups_have_a_model,
            "observationalOutputFullyDetermined":
                output_fully_determined,
            "ambiguousCompleteDomainNumeratorCount":
                ambiguous_domain_numerators,
            "denominatorBitLawFullyDetermined": False,
        },
        "powerOfTwoYControl": {
            "sampleCount": sum(y_errors.values()),
            "correctlyRoundedMatchCount": y_errors[0],
            "floatUlpErrorDistribution": {
                str(error): count
                for error, count in sorted(y_errors.items())
            },
            "exact": set(y_errors) == {0},
        },
        "measurement": {
            **measurement,
            "exact": exact_measurement,
        },
        "fixedFunctionSetupFullyDetermined": False,
        "holdoutAuthorized": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("probe", type=Path)
    parser.add_argument("-o", "--output", type=Path)
    parser.add_argument(
        "-j",
        "--jobs",
        type=int,
        help="parallel width/branch workers (default: CPU count)",
    )
    arguments = parser.parse_args()
    report = analyze(arguments.probe, jobs=arguments.jobs)
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if arguments.output is None:
        print(rendered, end="")
    else:
        arguments.output.write_text(rendered, encoding="utf-8")
    return 0 if report["measurement"]["exact"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
