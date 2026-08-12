#!/usr/bin/env python3
"""Identify clear Liquid Glass' amplitude law from the v2.15 training sweep."""

import argparse
import hashlib
import json
import platform
from dataclasses import dataclass
from importlib.metadata import version as package_version
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from liquid_glass_spatial_fit import CaptureSet


type BoolArray = NDArray[np.bool_]
type FloatArray = NDArray[np.float64]
type JsonObject = dict[str, Any]

RIG_VERSION = "2.15.0"
SCENE = "circle-4000-center"
AMPLITUDES = tuple(range(65))
HISTORICAL_AMPLITUDES = (17, 31, 47, 64)
BOUNDARY_EXCLUSION_PIXELS = 512
DEFAULT_SAMPLE_STRIDE = 5
INTERVAL_ITERATIONS = 64
QUANTIZATION_INTERVAL_MARGIN = 1e-7


@dataclass(frozen=True, slots=True)
class ModelSpec:
    name: str
    family: str
    source_space: str
    output_space: str
    fractions: tuple[float, ...] = ()
    intermediate_quantizer: str = "continuous"
    polynomial_degree: int = 0
    include_odd_residue: bool = False


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def dense_training_background(amplitude: int) -> str:
    if amplitude not in AMPLITUDES[1:]:
        raise ValueError(f"invalid dense-training amplitude: {amplitude}")
    if amplitude == 64:
        return "noise-rgb-a064-kernel-train-00"
    if amplitude in HISTORICAL_AMPLITUDES:
        return f"noise-rgb-a{amplitude:03d}-tomography-train-00"
    return f"noise-rgb-a{amplitude:03d}-sweep-train-00"


def fit_amplitude_mask(
    amplitudes: FloatArray,
) -> BoolArray:
    integer_amplitudes = amplitudes.astype(np.int64)
    if not np.array_equal(amplitudes, integer_amplitudes):
        raise ValueError("amplitudes must be integers")
    # Fixed before Apple output exists: two residues fit each two-residue
    # validation block while both parities occur on each side.
    return np.isin(integer_amplitudes % 4, (0, 1))


def srgb_to_linear(values: FloatArray) -> FloatArray:
    normalized = np.clip(values, 0.0, 1.0)
    return np.where(
        normalized <= 0.04045,
        normalized / 12.92,
        np.power((normalized + 0.055) / 1.055, 2.4),
    )


def linear_to_srgb(values: FloatArray) -> FloatArray:
    normalized = np.clip(values, 0.0, 1.0)
    return np.where(
        normalized <= 0.0031308,
        normalized * 12.92,
        1.055 * np.power(normalized, 1.0 / 2.4) - 0.055,
    )


def codes_to_domain(values: FloatArray, domain: str) -> FloatArray:
    match domain:
        case "code":
            return values
        case "linear-srgb":
            return srgb_to_linear(values / 255.0)
        case _:
            raise ValueError(f"unknown code domain: {domain}")


def domain_to_codes(values: FloatArray, domain: str) -> FloatArray:
    match domain:
        case "code":
            return values
        case "linear-srgb":
            return linear_to_srgb(values) * 255.0
        case _:
            raise ValueError(f"unknown code domain: {domain}")


def quantize_intermediate(values: FloatArray, mode: str) -> FloatArray:
    match mode:
        case "continuous":
            return values
        case "floor":
            return np.floor(values)
        case "half-up":
            return np.floor(values + 0.5)
        case "half-even":
            return np.rint(values)
        case "ceil":
            return np.ceil(values)
        case _:
            raise ValueError(f"unknown intermediate quantizer: {mode}")


def model_specs() -> tuple[ModelSpec, ...]:
    models = [
        ModelSpec(
            name="code-polynomial-d1",
            family="polynomial",
            source_space="code",
            output_space="code",
            polynomial_degree=1,
        ),
        ModelSpec(
            name="code-polynomial-d1-odd",
            family="polynomial",
            source_space="code",
            output_space="code",
            polynomial_degree=1,
            include_odd_residue=True,
        ),
        ModelSpec(
            name="code-polynomial-d2",
            family="polynomial",
            source_space="code",
            output_space="code",
            polynomial_degree=2,
        ),
        ModelSpec(
            name="code-polynomial-d2-odd",
            family="polynomial",
            source_space="code",
            output_space="code",
            polynomial_degree=2,
            include_odd_residue=True,
        ),
        ModelSpec(
            name="code-polynomial-d3-odd",
            family="polynomial",
            source_space="code",
            output_space="code",
            polynomial_degree=3,
            include_odd_residue=True,
        ),
    ]
    for source_space, output_space in (
        ("code", "code"),
        ("linear-srgb", "code"),
        ("linear-srgb", "linear-srgb"),
    ):
        models.append(
            ModelSpec(
                name=f"endpoints-{source_space}-to-{output_space}",
                family="fraction-basis",
                source_space=source_space,
                output_space=output_space,
                fractions=(-1.0, 1.0),
            )
        )
        for quantizer in (
            "continuous",
            "floor",
            "half-up",
            "half-even",
            "ceil",
        ):
            models.append(
                ModelSpec(
                    name=(
                        f"half-grid-{source_space}-to-{output_space}-"
                        f"{quantizer}"
                    ),
                    family="fraction-basis",
                    source_space=source_space,
                    output_space=output_space,
                    fractions=(-1.0, -0.5, 0.5, 1.0),
                    intermediate_quantizer=quantizer,
                )
            )
    return tuple(models)


def model_basis(spec: ModelSpec, amplitudes: FloatArray) -> FloatArray:
    if amplitudes.ndim != 1:
        raise ValueError("amplitudes must be a vector")
    normalized = amplitudes / 64.0
    columns: list[FloatArray] = [np.ones_like(normalized)]
    match spec.family:
        case "polynomial":
            columns.extend(
                np.power(normalized, degree)
                for degree in range(1, spec.polynomial_degree + 1)
            )
            if spec.include_odd_residue:
                columns.append((amplitudes.astype(np.int64) % 2).astype(np.float64))
        case "fraction-basis":
            for fraction in spec.fractions:
                source_codes = 128.0 + fraction * amplitudes
                source_codes = quantize_intermediate(
                    source_codes,
                    spec.intermediate_quantizer,
                )
                columns.append(
                    codes_to_domain(source_codes, spec.source_space)
                )
        case _:
            raise ValueError(f"unknown model family: {spec.family}")
    basis = np.column_stack(columns)
    independent: list[int] = []
    rank = 0
    for column in range(basis.shape[1]):
        candidate = basis[:, [*independent, column]]
        candidate_rank = int(np.linalg.matrix_rank(candidate, tol=1e-12))
        if candidate_rank > rank:
            independent.append(column)
            rank = candidate_rank
    return basis[:, independent]


def code_intervals(
    codes: FloatArray,
    *,
    domain: str,
) -> tuple[FloatArray, FloatArray]:
    half_width = 0.5 - QUANTIZATION_INTERVAL_MARGIN
    lower_codes = np.clip(codes - half_width, 0.0, 255.0)
    upper_codes = np.clip(codes + half_width, 0.0, 255.0)
    return (
        codes_to_domain(lower_codes, domain),
        codes_to_domain(upper_codes, domain),
    )


def fit_interval_model(
    basis: FloatArray,
    codes: FloatArray,
    *,
    output_space: str,
    fit_mask: BoolArray,
    iterations: int = INTERVAL_ITERATIONS,
) -> FloatArray:
    if (
        basis.ndim != 2
        or codes.ndim != 2
        or basis.shape[0] != codes.shape[0]
        or fit_mask.shape != (basis.shape[0],)
        or iterations < 0
    ):
        raise ValueError("invalid interval-fit inputs")
    fitting_basis = basis[fit_mask]
    pseudo_inverse = np.linalg.pinv(fitting_basis, rcond=1e-12)
    target = codes_to_domain(codes[fit_mask], output_space)
    if iterations == 0:
        return pseudo_inverse @ target

    lower, upper = code_intervals(codes[fit_mask], domain=output_space)
    bounded = target.copy()
    dual = np.zeros_like(target)
    coefficients = pseudo_inverse @ bounded
    for _ in range(iterations):
        coefficients = pseudo_inverse @ (bounded - dual)
        fitted = fitting_basis @ coefficients
        bounded = np.clip(fitted + dual, lower, upper)
        dual += fitted - bounded
    return coefficients


def quantize_output_codes(values: FloatArray) -> NDArray[np.uint8]:
    return np.clip(np.floor(values + 0.5), 0.0, 255.0).astype(np.uint8)


def exact_error_summary(
    actual: NDArray[np.uint8],
    predicted: NDArray[np.uint8],
    selected: BoolArray,
) -> JsonObject:
    if actual.shape != predicted.shape or selected.shape != (actual.shape[0],):
        raise ValueError("invalid exact-error inputs")
    delta = np.abs(
        actual[selected].astype(np.int16)
        - predicted[selected].astype(np.int16)
    )
    channels = int(delta.size)
    exact = int(np.count_nonzero(delta == 0))
    return {
        "channels": channels,
        "exactChannels": exact,
        "exactChannelFraction": exact / channels if channels else None,
        "meanAbsoluteCodes": float(delta.mean()) if channels else None,
        "rootMeanSquareCodes": (
            float(np.sqrt(np.mean(np.square(delta, dtype=np.float64))))
            if channels
            else None
        ),
        "p95AbsoluteCodes": (
            float(np.quantile(delta, 0.95)) if channels else None
        ),
        "maximumAbsoluteCodes": int(delta.max(initial=0)),
    }


def evaluate_model(
    spec: ModelSpec,
    amplitudes: FloatArray,
    actual: NDArray[np.uint8],
    *,
    fit_mask: BoolArray,
) -> JsonObject:
    basis = model_basis(spec, amplitudes)
    actual_float = actual.astype(np.float64)
    coefficients = fit_interval_model(
        basis,
        actual_float,
        output_space=spec.output_space,
        fit_mask=fit_mask,
        iterations=0,
    )
    predicted_domain = basis @ coefficients
    predicted = quantize_output_codes(
        domain_to_codes(predicted_domain, spec.output_space)
    )

    all_mask = np.ones_like(fit_mask)
    validation_mask = ~fit_mask
    all_coefficients = fit_interval_model(
        basis,
        actual_float,
        output_space=spec.output_space,
        fit_mask=all_mask,
    )
    all_refit = quantize_output_codes(
        domain_to_codes(
            basis @ all_coefficients,
            spec.output_space,
        )
    )
    residue_predictions = np.empty_like(actual)
    residue_folds: list[JsonObject] = []
    integer_amplitudes = amplitudes.astype(np.int64)
    for residue in range(4):
        held = integer_amplitudes % 4 == residue
        fold_coefficients = fit_interval_model(
            basis,
            actual_float,
            output_space=spec.output_space,
            fit_mask=~held,
            iterations=0,
        )
        fold_prediction = quantize_output_codes(
            domain_to_codes(
                basis @ fold_coefficients,
                spec.output_space,
            )
        )
        residue_predictions[held] = fold_prediction[held]
        residue_folds.append(
            {
                "heldResidueModulo4": residue,
                **exact_error_summary(
                    actual,
                    fold_prediction,
                    held,
                ),
            }
        )
    return {
        "name": spec.name,
        "family": spec.family,
        "sourceSpace": spec.source_space,
        "outputSpace": spec.output_space,
        "intermediateQuantizer": spec.intermediate_quantizer,
        "terms": int(basis.shape[1]),
        "rankOnFitAmplitudes": int(np.linalg.matrix_rank(basis[fit_mask])),
        "conditionNumberOnFitAmplitudes": float(
            np.linalg.cond(basis[fit_mask])
        ),
        "fit": exact_error_summary(actual, predicted, fit_mask),
        "validation": exact_error_summary(
            actual,
            predicted,
            validation_mask,
        ),
        "all": exact_error_summary(actual, predicted, all_mask),
        "refitAllAmplitudes": exact_error_summary(
            actual,
            all_refit,
            all_mask,
        ),
        "leaveOneResidueOut": {
            **exact_error_summary(
                actual,
                residue_predictions,
                all_mask,
            ),
            "folds": residue_folds,
        },
    }


def sample_slices(
    shape: tuple[int, int],
    *,
    stride: int,
) -> tuple[slice, slice]:
    height, width = shape
    margin = BOUNDARY_EXCLUSION_PIXELS
    if stride <= 0 or height <= 2 * margin or width <= 2 * margin:
        raise ValueError("invalid dense-sweep sampling geometry")
    return (
        slice(margin, height - margin, stride),
        slice(margin, width - margin, stride),
    )


def load_training_traces(
    captures: CaptureSet,
    *,
    stride: int,
) -> tuple[NDArray[np.uint8], JsonObject]:
    if captures.manifest.get("rigVersion") != RIG_VERSION:
        raise ValueError(f"expected Liquid Glass rig {RIG_VERSION}")
    base = captures.image("gray-128", SCENE, "clear", "dark")
    region = sample_slices(base.shape[:2], stride=stride)
    sampled = [base[region].astype(np.uint8).reshape(-1)]

    endpoint_signs: NDArray[np.bool_] | None = None
    differing_sign_channels = 0
    compared_sign_channels = 0
    for amplitude in AMPLITUDES[1:]:
        background = dense_training_background(amplitude)
        if "holdout" in background:
            raise AssertionError("protected holdout entered dense-sweep fitting")
        output = captures.image(
            background,
            SCENE,
            "clear",
            "dark",
        )
        sampled.append(output[region].astype(np.uint8).reshape(-1))

        source_signs = captures.reference_image(background)[region] > 128.0
        if endpoint_signs is None:
            endpoint_signs = source_signs
        else:
            differing_sign_channels += int(
                np.count_nonzero(source_signs != endpoint_signs)
            )
            compared_sign_channels += int(source_signs.size)

    traces = np.stack(sampled)
    return traces, {
        "sampleStridePixels": stride,
        "boundaryExclusionPixels": BOUNDARY_EXCLUSION_PIXELS,
        "sampledPixels": int(traces.shape[1] // 3),
        "sampledChannelsPerAmplitude": int(traces.shape[1]),
        "sourceSignIdentity": {
            "comparedChannels": compared_sign_channels,
            "differingChannels": differing_sign_channels,
            "exact": differing_sign_channels == 0,
        },
    }


def trace_structure(actual: NDArray[np.uint8]) -> JsonObject:
    signed = actual.astype(np.int16)
    first = np.diff(signed, axis=0)
    second = np.diff(signed, n=2, axis=0)
    nondecreasing = np.all(first >= 0, axis=0)
    nonincreasing = np.all(first <= 0, axis=0)
    monotone = nondecreasing | nonincreasing
    centered = actual.astype(np.float64)
    centered -= centered.mean(axis=0, keepdims=True)
    covariance = centered @ centered.T / centered.shape[1]
    eigenvalues = np.maximum(
        np.linalg.eigvalsh(covariance)[::-1],
        0.0,
    )
    total_energy = float(eigenvalues.sum())
    leading_spectrum = eigenvalues[:16]
    return {
        "traces": int(actual.shape[1]),
        "monotoneTraceFraction": float(np.mean(monotone)),
        "firstDifferenceCodes": {
            "minimum": int(first.min(initial=0)),
            "maximum": int(first.max(initial=0)),
        },
        "secondDifferenceCodes": {
            "minimum": int(second.min(initial=0)),
            "maximum": int(second.max(initial=0)),
            "nonzeroFraction": float(np.mean(second != 0)),
        },
        "centeredAmplitudeCovarianceSpectrum": {
            "leadingEigenvalues": leading_spectrum.tolist(),
            "leadingEnergyFractions": (
                (leading_spectrum / total_energy).tolist()
                if total_energy
                else [0.0] * leading_spectrum.size
            ),
            "leadingCumulativeEnergyFractions": (
                np.cumsum(leading_spectrum / total_energy).tolist()
                if total_energy
                else [0.0] * leading_spectrum.size
            ),
        },
    }


def build_report(
    captures: CaptureSet,
    *,
    stride: int = DEFAULT_SAMPLE_STRIDE,
) -> JsonObject:
    actual, sampling = load_training_traces(captures, stride=stride)
    amplitudes = np.asarray(AMPLITUDES, dtype=np.float64)
    fit_mask = fit_amplitude_mask(amplitudes)
    candidates = [
        evaluate_model(
            spec,
            amplitudes,
            actual,
            fit_mask=fit_mask,
        )
        for spec in model_specs()
    ]
    candidates.sort(
        key=lambda record: (
            -float(record["leaveOneResidueOut"]["exactChannelFraction"]),
            float(record["leaveOneResidueOut"]["meanAbsoluteCodes"]),
            -float(record["validation"]["exactChannelFraction"]),
            int(record["terms"]),
            str(record["name"]),
        )
    )
    protected_backgrounds = sorted(
        {
            str(record.get("background"))
            for record in captures.manifest.get("captures", [])
            if "-holdout-" in str(record.get("background"))
            and (
                "-tomography-" in str(record.get("background"))
                or "-sweep-" in str(record.get("background"))
            )
        }
    )
    return {
        "clearAmplitudeSweepSchemaVersion": 1,
        "analysisImplementation": {
            "file": "analysis/liquid_glass_clear_amplitude_sweep.py",
            "sha256": file_sha256(Path(__file__).resolve()),
            "python": platform.python_version(),
            "numpy": np.__version__,
            "Pillow": package_version("Pillow"),
        },
        "source": {
            "artifact": captures.root.name,
            "rigVersion": captures.manifest.get("rigVersion"),
            "ciCommit": captures.manifest.get("ciCommit"),
            "osVersion": captures.manifest.get("osVersion"),
            "osBuild": captures.manifest.get("osBuild"),
            "references": len(captures.manifest.get("references", [])),
            "staticCaptures": len(captures.manifest.get("captures", [])),
        },
        "sampling": sampling,
        "amplitudePartition": {
            "fitRule": "amplitude modulo 4 is 0 or 1",
            "fitAmplitudes": amplitudes[fit_mask].astype(int).tolist(),
            "validationAmplitudes": amplitudes[~fit_mask].astype(int).tolist(),
        },
        "traceStructure": trace_structure(actual),
        "rankedCandidates": candidates,
        "selectedCandidate": candidates[0]["name"],
        "policy": {
            "fitInputs": (
                "train-00 amplitudes 1 through 64 under circle-4000-center; "
                "amplitude zero is the gray-128 control"
            ),
            "protectedBackgrounds": protected_backgrounds,
            "protectedHoldoutOutputsDecoded": False,
            "productionShaderModified": False,
            "qualityGate": (
                "zero unequal decoded channels on fresh protected Apple captures"
            ),
        },
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Rank exact amplitude-law hypotheses on the v2.15 training sweep "
            "without opening protected output images."
        )
    )
    parser.add_argument("artifact", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument(
        "--stride",
        type=int,
        default=DEFAULT_SAMPLE_STRIDE,
        help="central-region sampling stride in pixels",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    captures = CaptureSet.open(args.artifact)
    try:
        report = build_report(captures, stride=args.stride)
    finally:
        captures.close()
    serialized = json.dumps(
        report,
        indent=2,
        sort_keys=True,
        allow_nan=False,
    )
    if args.report is None:
        print(serialized)
    else:
        args.report.write_text(f"{serialized}\n", encoding="utf-8")
        print(args.report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
