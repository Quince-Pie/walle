#!/usr/bin/env python3
"""Evaluate staged fixed-function models against Apple raster slopes."""

import argparse
import json
import re
from collections import Counter
from fractions import Fraction
from pathlib import Path
from typing import Any, Callable

import liquid_glass_raster_interpolant as raster


type JsonObject = dict[str, Any]
type Predictor = Callable[[Fraction, int], int]

FACTOR_CASE = re.compile(
    r"^tomography-discovery-factor-h064-w[0-9]{3}$"
)
EXPECTED_SAMPLE_COUNT = 4096
EXPECTED_SAMPLES_PER_DIMENSION = 256
RECIPROCAL_PRECISION_BITS = 25
PRODUCT_PRECISION_BITS = 27


def correctly_rounded_divide(delta: Fraction, dimension: int) -> int:
    return raster.round_fraction_to_float32_bits(delta / dimension)


def nearest_27_bit_quotient(delta: Fraction, dimension: int) -> int:
    quotient = raster.quantize_binary_significand(
        delta / dimension,
        PRODUCT_PRECISION_BITS,
    )
    return raster.round_fraction_to_float32_bits(quotient)


def reciprocal_25_product_27(delta: Fraction, dimension: int) -> int:
    reciprocal = raster.quantize_binary_significand(
        Fraction(1, dimension),
        RECIPROCAL_PRECISION_BITS,
    )
    product = raster.quantize_binary_significand(
        delta * reciprocal,
        PRODUCT_PRECISION_BITS,
    )
    return raster.round_fraction_to_float32_bits(product)


def error_distribution(errors: Counter[int]) -> dict[str, int]:
    return {
        str(error): count
        for error, count in sorted(errors.items())
    }


def model_report(
    samples: list[JsonObject],
    predictor: Predictor,
) -> JsonObject:
    errors: Counter[int] = Counter()
    by_dimension: dict[int, Counter[int]] = {}
    mismatches: list[JsonObject] = []
    for sample in samples:
        dimension = int(sample["axisDimension"])
        delta = Fraction(
            int(sample["deltaNumerator"]),
            int(sample["deltaDenominator"]),
        )
        observed = int(str(sample["observedBits"]), 16)
        predicted = predictor(delta, dimension)
        error = observed - predicted
        errors[error] += 1
        by_dimension.setdefault(dimension, Counter())[error] += 1
        if error:
            mismatches.append({
                "baseCase": sample["baseCase"],
                "dimension": dimension,
                "numeratorIndex": int(sample["numeratorIndex"]),
                "deltaNumerator": int(sample["deltaNumerator"]),
                "deltaDenominator": int(sample["deltaDenominator"]),
                "predictedBits": f"0x{predicted:08x}",
                "observedBits": f"0x{observed:08x}",
                "observedMinusPredictedFloatUlp": error,
            })
    sample_count = len(samples)
    match_count = errors[0]
    return {
        "sampleCount": sample_count,
        "matchCount": match_count,
        "mismatchCount": sample_count - match_count,
        "matchFraction": f"{match_count}/{sample_count}",
        "matchRate": match_count / sample_count,
        "floatUlpErrorDistribution": error_distribution(errors),
        "byDimension": [
            {
                "dimension": dimension,
                "sampleCount": sum(distribution.values()),
                "matchCount": distribution[0],
                "floatUlpErrorDistribution":
                    error_distribution(distribution),
            }
            for dimension, distribution in sorted(by_dimension.items())
        ],
        "mismatches": mismatches,
        "exact": not mismatches,
    }


def selected_factor_samples(source: JsonObject) -> list[JsonObject]:
    if (
        source.get("liquidGlassRasterNumeratorAnalysisSchemaVersion")
        != 1
        or source.get("selectedRole") != "discovery"
        or source.get("holdoutOpened") is not False
        or source.get("measurement", {}).get("exact") is not True
    ):
        raise ValueError(
            "an exact discovery-only numerator report is required"
        )
    selected = [
        sample
        for sample in source.get("samples", [])
        if sample.get("axis") == "x"
        and FACTOR_CASE.fullmatch(str(sample.get("baseCase", "")))
    ]
    if len(selected) != EXPECTED_SAMPLE_COUNT:
        raise ValueError(
            f"expected {EXPECTED_SAMPLE_COUNT} factor-control samples; "
            f"found {len(selected)}"
        )
    counts = Counter(int(sample["axisDimension"]) for sample in selected)
    if (
        len(counts) != 16
        or set(counts.values()) != {EXPECTED_SAMPLES_PER_DIMENSION}
    ):
        raise ValueError(
            "expected 16 dimensions with 256 samples each; "
            f"found {dict(sorted(counts.items()))}"
        )
    return selected


def analyze(path: Path) -> JsonObject:
    source = json.loads(path.read_text(encoding="utf-8"))
    samples = selected_factor_samples(source)
    models = {
        "correctlyRoundedDivide": model_report(
            samples,
            correctly_rounded_divide,
        ),
        "nearestEven27BitQuotient": model_report(
            samples,
            nearest_27_bit_quotient,
        ),
        "nearestEven25BitReciprocalThen27BitProduct": model_report(
            samples,
            reciprocal_25_product_27,
        ),
    }
    baseline_mismatches = int(
        models["correctlyRoundedDivide"]["mismatchCount"]
    )
    staged_mismatches = int(
        models[
            "nearestEven25BitReciprocalThen27BitProduct"
        ]["mismatchCount"]
    )
    return {
        "liquidGlassRasterSetupModelAnalysisSchemaVersion": 1,
        "source": str(path),
        "selectedRole": "discovery",
        "holdoutOpened": False,
        "selection": {
            "axis": "x",
            "oppositeEdge": 64,
            "factorizationRemovedByPowerOfTwoEdge": True,
            "dimensionCount": 16,
            "sampleCount": len(samples),
        },
        "models": models,
        "measurement": {
            "bestMeasuredModel":
                "nearestEven25BitReciprocalThen27BitProduct",
            "reciprocalPrecisionBits": RECIPROCAL_PRECISION_BITS,
            "productPrecisionBits": PRODUCT_PRECISION_BITS,
            "baselineMismatchCount": baseline_mismatches,
            "stagedMismatchCount": staged_mismatches,
            "mismatchesRemoved": (
                baseline_mismatches - staged_mismatches
            ),
            "mismatchReductionPercent": (
                100.0
                * (baseline_mismatches - staged_mismatches)
                / baseline_mismatches
            ),
            "remainingErrorsAreOneFloatUlp": set(
                models[
                    "nearestEven25BitReciprocalThen27BitProduct"
                ]["floatUlpErrorDistribution"]
            ) <= {"-1", "0", "1"},
        },
        "fixedFunctionSetupFullyDetermined": False,
        "holdoutAuthorized": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("numerator_report", type=Path)
    parser.add_argument("-o", "--output", type=Path)
    arguments = parser.parse_args()
    report = analyze(arguments.numerator_report)
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if arguments.output is None:
        print(rendered, end="")
    else:
        arguments.output.write_text(rendered, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
