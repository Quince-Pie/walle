#!/usr/bin/env python3
"""Analyze schema-16 low-bit refinement of Apple raster setup."""

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

EXPECTED_CASE_COUNT = 70
EXPECTED_OFFSETS = tuple(range(-3, 5))
EXPECTED_SAMPLE_COUNT_PER_AXIS = EXPECTED_CASE_COUNT * 8
SUPPORTED_RIGS = {
    17: "metal-raster-interpolant-probe-17.0.0",
    18: "metal-raster-interpolant-probe-18.0.0",
}


def single_round_reciprocal_25_product(
    delta: Fraction,
    dimension: int,
) -> int:
    reciprocal = raster.quantize_binary_significand(
        Fraction(1, dimension),
        setup.RECIPROCAL_PRECISION_BITS,
    )
    return raster.round_fraction_to_float32_bits(delta * reciprocal)


def source_mismatches(path: Path) -> list[JsonObject]:
    report = json.loads(path.read_text(encoding="utf-8"))
    if (
        report.get("liquidGlassRasterSetupModelAnalysisSchemaVersion") != 1
        or report.get("selectedRole") != "discovery"
        or report.get("holdoutOpened") is not False
        or report.get("fixedFunctionSetupFullyDetermined") is not False
    ):
        raise ValueError("the preregistered discovery setup-model report is required")
    model = report.get("models", {}).get(
        "nearestEven25BitReciprocalThen27BitProduct",
        {},
    )
    mismatches = model.get("mismatches", [])
    if (
        model.get("mismatchCount") != EXPECTED_CASE_COUNT
        or len(mismatches) != EXPECTED_CASE_COUNT
        or any(
            abs(int(record["observedMinusPredictedFloatUlp"])) != 1
            for record in mismatches
        )
    ):
        raise ValueError("the setup-model report does not contain 70 one-ULP residuals")
    return sorted(
        mismatches,
        key=lambda record: (
            int(record["dimension"]),
            int(record["numeratorIndex"]),
        ),
    )


def expected_case_records(
    mismatches: list[JsonObject],
) -> list[JsonObject]:
    records: list[JsonObject] = []
    for mismatch in mismatches:
        dimension = int(mismatch["dimension"])
        numerator_index = int(mismatch["numeratorIndex"])
        anchor = int(mismatch["deltaNumerator"])
        base_case = f"tomography-discovery-factor-h064-w{dimension:03d}"
        if mismatch["baseCase"] != base_case:
            raise ValueError(f"unexpected setup-model base case {mismatch['baseCase']}")
        records.append(
            {
                "name": (
                    "numerator-refinement-discovery-"
                    f"factor-h064-w{dimension:03d}-"
                    f"anchor-{numerator_index:03d}"
                ),
                "baseCase": base_case,
                "anchorNumeratorIndex": numerator_index,
                "deltaNumerators": [anchor + offset for offset in EXPECTED_OFFSETS],
            }
        )
    return records


def load_refinement_cases(
    root: Path,
    mismatches: list[JsonObject],
) -> tuple[list[tomography.TomographyCase], JsonObject]:
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    schema = manifest.get("schemaVersion")
    if manifest.get("rigVersion") != SUPPORTED_RIGS.get(schema):
        raise ValueError("raster probe schema 17 or 18 is required")
    records = manifest.get("numeratorRefinementCases", [])
    projection = [
        {
            "name": record.get("name"),
            "baseCase": record.get("baseCase"),
            "anchorNumeratorIndex": record.get("anchorNumeratorIndex"),
            "deltaNumerators": record.get("deltaNumerators"),
        }
        for record in records
    ]
    expected = expected_case_records(mismatches)
    if (
        len(records) != EXPECTED_CASE_COUNT
        or projection != expected
        or any(record.get("role") != "discovery" for record in records)
        or any("holdout" in str(record.get("name")) for record in records)
    ):
        raise ValueError("schema-16 refinement records differ from preregistration")
    return [
        tomography.TomographyCase(root=root, record=record) for record in records
    ], manifest


def error_distribution(errors: Counter[int]) -> JsonObject:
    return {str(error): count for error, count in sorted(errors.items())}


def analyze(
    root: Path,
    setup_model_report: Path,
) -> JsonObject:
    mismatches = source_mismatches(setup_model_report)
    cases, _manifest = load_refinement_cases(root, mismatches)
    anchor_by_case = {
        expected["name"]: mismatch
        for expected, mismatch in zip(
            expected_case_records(mismatches),
            mismatches,
            strict=True,
        )
    }
    base_cases = {
        case.name: case
        for case in tomography.load_tomography_cases(
            root,
            role="discovery",
        )
    }
    mask_names = {str(case.record["primitiveMaskCase"]) for case in cases}
    masks = {name: base_cases[name].primitive_mask() for name in mask_names}

    samples_by_axis: dict[str, list[JsonObject]] = {
        "x": [],
        "y": [],
    }
    anchor_groups: list[JsonObject] = []
    observed_values = 0
    mismatched_values = 0
    ambiguous_slopes = 0
    ambiguous_constants = 0
    anchor_replay_mismatches = 0
    for case in cases:
        mask_name = str(case.record["primitiveMaskCase"])
        base_name = str(case.record["baseCase"])
        if mask_name != base_name or base_name not in base_cases:
            raise ValueError(f"{case.name} has an invalid primitive mask")
        primitive_observations: list[tomography.TomographySlope] = []
        for delta_index in range(8):
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

        anchor = anchor_by_case[case.name]
        anchor_numerator = int(anchor["deltaNumerator"])
        anchor_index = int(anchor["numeratorIndex"])
        group_samples: list[JsonObject] = []
        for observation in observations:
            if len(observation.accepted_bits) != 1:
                raise ValueError(f"{case.name} has an ambiguous slope")
            dimension = case.width if observation.axis == "x" else case.height
            numerator = case.numerators[observation.delta_index]
            delta = Fraction(numerator, case.denominator)
            observed_bits = next(iter(observation.accepted_bits))
            staged_bits = setup.reciprocal_25_product_27(
                delta,
                dimension,
            )
            single_round_bits = single_round_reciprocal_25_product(
                delta,
                dimension,
            )
            direct_bits = setup.correctly_rounded_divide(
                delta,
                dimension,
            )
            sample: JsonObject = {
                "name": case.name,
                "baseCase": base_name,
                "axis": observation.axis,
                "axisDimension": dimension,
                "anchorNumeratorIndex": anchor_index,
                "numeratorIndex": anchor_index,
                "refinementOffset": numerator - anchor_numerator,
                "deltaNumerator": numerator,
                "deltaDenominator": case.denominator,
                "observedBits": f"0x{observed_bits:08x}",
                "stagedPredictedBits": f"0x{staged_bits:08x}",
                "observedMinusStagedFloatUlp": observed_bits - staged_bits,
                "singleRoundReciprocalProductBits": f"0x{single_round_bits:08x}",
                "observedMinusSingleRoundFloatUlp": observed_bits - single_round_bits,
                "correctlyRoundedDivideBits": f"0x{direct_bits:08x}",
                "observedMinusCorrectlyRoundedDivideFloatUlp": observed_bits
                - direct_bits,
            }
            samples_by_axis[observation.axis].append(sample)
            group_samples.append(sample)
            if (
                observation.axis == "x"
                and numerator == anchor_numerator
                and observed_bits != int(str(anchor["observedBits"]), 16)
            ):
                anchor_replay_mismatches += 1
        anchor_groups.append(
            {
                "name": case.name,
                "baseCase": base_name,
                "dimension": case.width,
                "anchorNumeratorIndex": anchor_index,
                "anchorNumerator": anchor_numerator,
                "samples": sorted(
                    (sample for sample in group_samples if sample["axis"] == "x"),
                    key=lambda sample: int(sample["refinementOffset"]),
                ),
            }
        )

    for axis, samples in samples_by_axis.items():
        if len(samples) != EXPECTED_SAMPLE_COUNT_PER_AXIS:
            raise ValueError(
                f"expected {EXPECTED_SAMPLE_COUNT_PER_AXIS} {axis} "
                f"samples; found {len(samples)}"
            )

    x_samples = samples_by_axis["x"]
    y_samples = samples_by_axis["y"]
    models = {
        "correctlyRoundedDivide": setup.model_report(
            x_samples,
            setup.correctly_rounded_divide,
        ),
        "singleRoundNearestEven25BitReciprocalProduct": setup.model_report(
            x_samples,
            single_round_reciprocal_25_product,
        ),
        "nearestEven25BitReciprocalThen27BitProduct": setup.model_report(
            x_samples,
            setup.reciprocal_25_product_27,
        ),
    }
    y_errors = Counter(
        int(sample["observedMinusCorrectlyRoundedDivideFloatUlp"])
        for sample in y_samples
    )
    return {
        "liquidGlassRasterRefinementAnalysisSchemaVersion": 1,
        "probe": str(root),
        "manifestSha256": tomography.sha256_file(root / "manifest.json"),
        "sourceSetupModelReport": str(setup_model_report),
        "sourceSetupModelReportSha256": tomography.sha256_file(setup_model_report),
        "selectedRole": "discovery",
        "holdoutOpened": False,
        "selection": {
            "anchorCount": len(cases),
            "offsets": list(EXPECTED_OFFSETS),
            "xSampleCount": len(x_samples),
            "yControlSampleCount": len(y_samples),
        },
        "models": models,
        "powerOfTwoYControl": {
            "sampleCount": len(y_samples),
            "correctlyRoundedMatchCount": y_errors[0],
            "floatUlpErrorDistribution": error_distribution(y_errors),
            "exact": set(y_errors) == {0},
        },
        "anchorReplay": {
            "sampleCount": len(cases),
            "mismatchCount": anchor_replay_mismatches,
            "exact": anchor_replay_mismatches == 0,
        },
        "anchorGroups": anchor_groups,
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
    parser.add_argument("setup_model_report", type=Path)
    parser.add_argument("-o", "--output", type=Path)
    arguments = parser.parse_args()
    report = analyze(
        arguments.probe,
        arguments.setup_model_report,
    )
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if arguments.output is None:
        print(rendered, end="")
    else:
        arguments.output.write_text(rendered, encoding="utf-8")
    return 0 if report["measurement"]["exact"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
