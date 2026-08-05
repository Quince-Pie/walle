#!/usr/bin/env python3
"""Fit v2.11 adaptive probes without consuming any declared holdout."""

import argparse
import hashlib
import json
import math
import platform
from dataclasses import dataclass
from importlib.metadata import version as package_version
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from liquid_glass_spatial_fit import (
    CaptureSet,
    error_summary,
    from_working_space,
    per_scale_polynomial_design,
    polynomial_exponents,
    to_working_space,
)
from liquid_glass_v210_fit import prediction_report, ridge_solve


type JsonObject = dict[str, Any]
type FloatArray = NDArray[np.float64]

VARIANTS = ("dark/clear", "dark/regular", "light/regular")
SOURCE_SPACES = ("srgb-code", "linear-srgb")
GAUSSIAN_SIGMAS = (0.0, 0.5, 1.0, 2.0, 4.0, 8.0, 16.0, 32.0, 64.0, 128.0, 256.0)
SCALE_COUNTS = (1, 3, 5, 7, 9, 11)
RIDGE_PENALTIES = (1e-3, 0.1, 10.0, 1000.0)
TRAINING_STRIDE = 13
HOLDOUT_STRIDE = 11
BOUNDARY_EXCLUSION_PIXELS = 512


@dataclass(slots=True, kw_only=True)
class ProbeSamples:
    group: str
    background: str
    scale_inputs: list[FloatArray]
    outputs: dict[str, FloatArray]


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def adaptive_probe_pairs() -> dict[str, tuple[str, str]]:
    pairs = {
        f"context-b{block_size:04d}": (
            f"context-rgb-grid-b{block_size:04d}-train",
            f"context-rgb-midpoint-b{block_size:04d}-holdout",
        )
        for block_size in (4, 16, 64, 256)
    }
    pairs.update(
        {
            f"noise-{channel}-m{center:03d}": (
                f"noise-{channel}-m{center:03d}-a032-b0016-train",
                f"noise-{channel}-m{center:03d}-a032-b0016-holdout",
            )
            for channel in ("gray", "rgb")
            for center in (64, 128, 192)
        }
    )
    return pairs


def sample_region(
    shape: tuple[int, int],
    *,
    stride: int,
    margin: int = BOUNDARY_EXCLUSION_PIXELS,
) -> tuple[slice, slice]:
    height, width = shape
    if stride <= 0 or height <= 2 * margin or width <= 2 * margin:
        raise ValueError("invalid sampling geometry")
    return (
        slice(margin, height - margin, stride),
        slice(margin, width - margin, stride),
    )


def multiscale_inputs(
    source: FloatArray,
    *,
    source_space: str,
    stride: int,
) -> list[FloatArray]:
    height, width = source.shape[:2]
    region = sample_region((height, width), stride=stride)
    working = to_working_space(source, source_space)
    transformed = np.fft.rfft2(working, axes=(0, 1))
    vertical = np.fft.fftfreq(height)[:, np.newaxis]
    horizontal = np.fft.rfftfreq(width)[np.newaxis, :]
    squared_frequency = np.square(vertical) + np.square(horizontal)
    result: list[FloatArray] = []
    for sigma in GAUSSIAN_SIGMAS:
        if sigma == 0:
            filtered = working
        else:
            response = np.exp(-2.0 * math.pi**2 * sigma**2 * squared_frequency)
            filtered = np.fft.irfft2(
                transformed * response[:, :, np.newaxis],
                s=(height, width),
                axes=(0, 1),
            )
        result.append(from_working_space(filtered[region], source_space).reshape(-1, 3))
    return result


def variant_image(
    captures: CaptureSet,
    background: str,
    variant: str,
) -> FloatArray:
    appearance, material = variant.split("/")
    return captures.image(
        background,
        "circle-4000-center",
        material,
        appearance,
    )


def load_probe_samples(
    captures: CaptureSet,
    backgrounds: dict[str, str],
    *,
    source_space: str,
    stride: int,
) -> dict[str, ProbeSamples]:
    result: dict[str, ProbeSamples] = {}
    for group, background in backgrounds.items():
        source = captures.reference_image(background)
        region = sample_region(source.shape[:2], stride=stride)
        result[group] = ProbeSamples(
            group=group,
            background=background,
            scale_inputs=multiscale_inputs(
                source,
                source_space=source_space,
                stride=stride,
            ),
            outputs={
                variant: variant_image(captures, background, variant)[region].reshape(
                    -1, 3
                )
                for variant in VARIANTS
            },
        )
    return result


def model_design(
    samples: ProbeSamples,
    *,
    degree: int,
    scale_count: int,
) -> FloatArray:
    return per_scale_polynomial_design(
        samples.scale_inputs[:scale_count],
        degree=degree,
    )


def cross_validate_candidate(
    groups: dict[str, ProbeSamples],
    *,
    variant: str,
    degree: int,
    scale_count: int,
    penalty: float,
) -> JsonObject:
    designs = {
        name: model_design(
            samples,
            degree=degree,
            scale_count=scale_count,
        )
        for name, samples in groups.items()
    }
    moments = {
        name: (
            design.T @ design,
            design.T @ groups[name].outputs[variant],
        )
        for name, design in designs.items()
    }
    term_count = next(iter(designs.values())).shape[1]
    total_gram = np.zeros((term_count, term_count), dtype=np.float64)
    total_rhs = np.zeros((term_count, 3), dtype=np.float64)
    for gram, right_hand_side in moments.values():
        total_gram += gram
        total_rhs += right_hand_side

    actual_parts: list[FloatArray] = []
    predicted_parts: list[FloatArray] = []
    probe_errors: JsonObject = {}
    for name, design in designs.items():
        gram, right_hand_side = moments[name]
        coefficients = ridge_solve(
            total_gram - gram,
            total_rhs - right_hand_side,
            penalty=penalty,
        )
        actual = groups[name].outputs[variant]
        predicted = np.clip(design @ coefficients, 0.0, 255.0)
        actual_parts.append(actual)
        predicted_parts.append(predicted)
        probe_errors[name] = error_summary(actual, predicted)

    actual = np.vstack(actual_parts)
    predicted = np.vstack(predicted_parts)
    return {
        "pooledError": error_summary(actual, predicted),
        "worstProbeMeanAbsoluteCodes": max(
            float(record["meanAbsoluteCodes"]) for record in probe_errors.values()
        ),
        "probeErrors": probe_errors,
    }


def select_model(
    training_by_space: dict[str, dict[str, ProbeSamples]],
    *,
    variant: str,
) -> tuple[JsonObject, list[JsonObject]]:
    candidates: list[JsonObject] = []
    for source_space, groups in training_by_space.items():
        for scale_count in SCALE_COUNTS:
            for degree in (1, 2, 3):
                terms = 1 + scale_count * (len(polynomial_exponents(degree)) - 1)
                for penalty in RIDGE_PENALTIES:
                    validation = cross_validate_candidate(
                        groups,
                        variant=variant,
                        degree=degree,
                        scale_count=scale_count,
                        penalty=penalty,
                    )
                    candidates.append(
                        {
                            "sourceSpace": source_space,
                            "degreePerScale": degree,
                            "scaleCount": scale_count,
                            "scalesPixels": list(GAUSSIAN_SIGMAS[:scale_count]),
                            "terms": terms,
                            "ridgePenalty": penalty,
                            **validation,
                        }
                    )
    selected = min(
        candidates,
        key=lambda candidate: (
            float(candidate["pooledError"]["meanAbsoluteCodes"]),
            float(candidate["worstProbeMeanAbsoluteCodes"]),
            int(candidate["terms"]),
            float(candidate["ridgePenalty"]),
        ),
    )
    return selected, candidates


def fit_coefficients(
    groups: dict[str, ProbeSamples],
    *,
    variant: str,
    degree: int,
    scale_count: int,
    penalty: float,
) -> FloatArray:
    term_count = 1 + scale_count * (len(polynomial_exponents(degree)) - 1)
    gram = np.zeros((term_count, term_count), dtype=np.float64)
    right_hand_side = np.zeros((term_count, 3), dtype=np.float64)
    for samples in groups.values():
        design = model_design(
            samples,
            degree=degree,
            scale_count=scale_count,
        )
        gram += design.T @ design
        right_hand_side += design.T @ samples.outputs[variant]
    return ridge_solve(gram, right_hand_side, penalty=penalty)


def evaluate_groups(
    groups: dict[str, ProbeSamples],
    *,
    variant: str,
    coefficients: FloatArray,
    degree: int,
    scale_count: int,
    constant_baseline: FloatArray,
) -> JsonObject:
    actual_parts: list[FloatArray] = []
    predicted_parts: list[FloatArray] = []
    constant_parts: list[FloatArray] = []
    records: JsonObject = {}
    for name, samples in groups.items():
        actual = samples.outputs[variant]
        design = model_design(
            samples,
            degree=degree,
            scale_count=scale_count,
        )
        predicted = np.clip(design @ coefficients, 0.0, 255.0)
        constant = np.broadcast_to(constant_baseline, actual.shape)
        records[name] = {
            "background": samples.background,
            "model": prediction_report(actual, predicted),
            "trainingMeanBaseline": prediction_report(actual, constant),
        }
        actual_parts.append(actual)
        predicted_parts.append(predicted)
        constant_parts.append(constant)
    actual = np.vstack(actual_parts)
    predicted = np.vstack(predicted_parts)
    constant = np.vstack(constant_parts)
    return {
        "pooledModel": prediction_report(actual, predicted),
        "pooledTrainingMeanBaseline": prediction_report(actual, constant),
        "probes": records,
    }


def training_mean(
    groups: dict[str, ProbeSamples],
    *,
    variant: str,
) -> FloatArray:
    total = np.zeros(3, dtype=np.float64)
    count = 0
    for samples in groups.values():
        values = samples.outputs[variant]
        total += values.sum(axis=0)
        count += values.shape[0]
    return total / count


def clear_appearance_identity(captures: CaptureSet) -> JsonObject:
    backgrounds = {
        background for pair in adaptive_probe_pairs().values() for background in pair
    }
    backgrounds.add("context-rgb-grid-b0016-shifted-check")
    records: JsonObject = {}
    exact = 0
    for background in sorted(backgrounds):
        dark = captures.records[(background, "circle-4000-center", "clear", "dark")]
        light = captures.records[(background, "circle-4000-center", "clear", "light")]
        identical = dark.get("pixelSha256") == light.get("pixelSha256")
        exact += identical
        records[background] = {
            "pixelExact": identical,
            "darkPixelSha256": dark.get("pixelSha256"),
            "lightPixelSha256": light.get("pixelSha256"),
        }
    return {
        "requiredProbeCount": len(backgrounds),
        "pixelExactProbeCount": exact,
        "allPixelExact": exact == len(backgrounds),
        "records": records,
    }


def fit_report(
    captures: CaptureSet,
    measurements: JsonObject,
) -> JsonObject:
    pairs = adaptive_probe_pairs()
    training_backgrounds = {
        group: backgrounds[0] for group, backgrounds in pairs.items()
    }
    holdout_backgrounds = {
        group: backgrounds[1] for group, backgrounds in pairs.items()
    }
    shifted_backgrounds = {
        "translated-context-b0016": "context-rgb-grid-b0016-shifted-check"
    }

    training_by_space = {
        source_space: load_probe_samples(
            captures,
            training_backgrounds,
            source_space=source_space,
            stride=TRAINING_STRIDE,
        )
        for source_space in SOURCE_SPACES
    }
    holdout_by_space = {
        source_space: load_probe_samples(
            captures,
            holdout_backgrounds,
            source_space=source_space,
            stride=HOLDOUT_STRIDE,
        )
        for source_space in SOURCE_SPACES
    }
    shifted_by_space = {
        source_space: load_probe_samples(
            captures,
            shifted_backgrounds,
            source_space=source_space,
            stride=HOLDOUT_STRIDE,
        )
        for source_space in SOURCE_SPACES
    }

    fits: JsonObject = {}
    for variant in VARIANTS:
        selected, candidates = select_model(
            training_by_space,
            variant=variant,
        )
        source_space = str(selected["sourceSpace"])
        degree = int(selected["degreePerScale"])
        scale_count = int(selected["scaleCount"])
        penalty = float(selected["ridgePenalty"])
        training = training_by_space[source_space]
        coefficients = fit_coefficients(
            training,
            variant=variant,
            degree=degree,
            scale_count=scale_count,
            penalty=penalty,
        )
        baseline = training_mean(training, variant=variant)
        fits[variant] = {
            "selected": selected,
            "candidateCount": len(candidates),
            "bestCandidates": sorted(
                candidates,
                key=lambda candidate: (
                    float(candidate["pooledError"]["meanAbsoluteCodes"]),
                    float(candidate["worstProbeMeanAbsoluteCodes"]),
                    int(candidate["terms"]),
                ),
            )[:12],
            "trainingSamples": sum(
                samples.outputs[variant].shape[0] for samples in training.values()
            ),
            "trainingMeanCodes": baseline.tolist(),
            "coefficients": coefficients.tolist(),
            "holdouts": evaluate_groups(
                holdout_by_space[source_space],
                variant=variant,
                coefficients=coefficients,
                degree=degree,
                scale_count=scale_count,
                constant_baseline=baseline,
            ),
            "translatedDiagnostic": evaluate_groups(
                shifted_by_space[source_space],
                variant=variant,
                coefficients=coefficients,
                degree=degree,
                scale_count=scale_count,
                constant_baseline=baseline,
            ),
        }

    return {
        "v211FitSchemaVersion": 1,
        "analysisImplementation": {
            "file": "analysis/liquid_glass_v211_fit.py",
            "sha256": file_sha256(Path(__file__).resolve()),
            "python": platform.python_version(),
            "numpy": package_version("numpy"),
            "scipy": package_version("scipy"),
            "Pillow": package_version("Pillow"),
        },
        "source": {
            "captureArtifact": {
                "rigVersion": captures.manifest.get("rigVersion"),
                "ciCommit": captures.manifest.get("ciCommit"),
                "requestedSuite": captures.manifest.get("requestedSuite"),
                "references": len(captures.manifest.get("references", [])),
                "staticCaptures": len(captures.manifest.get("captures", [])),
            },
            "measurementReport": {
                "analysisSchemaVersion": measurements.get("analysisSchemaVersion"),
                "analysisImplementation": measurements.get("analysisImplementation"),
                "artifact": measurements.get("artifact"),
            },
        },
        "policy": {
            "productionShaderModified": False,
            "trainingEvidence": list(training_backgrounds.values()),
            "holdoutEvidence": list(holdout_backgrounds.values()),
            "diagnosticEvidence": list(shifted_backgrounds.values()),
            "selectionRule": (
                "minimize pooled leave-one-training-probe-out MAE, then worst "
                "training-probe MAE, term count, and ridge penalty; holdouts "
                "and translated diagnostic are opened only after selection"
            ),
        },
        "featureModel": {
            "kind": (
                "complete RGB polynomial per isotropic Gaussian scale with one "
                "shared intercept"
            ),
            "sourceSpaces": list(SOURCE_SPACES),
            "candidateScalesPixels": list(GAUSSIAN_SIGMAS),
            "candidateScaleCounts": list(SCALE_COUNTS),
            "candidateDegreesPerScale": [1, 2, 3],
            "candidateRidgePenalties": list(RIDGE_PENALTIES),
            "boundaryExclusionPixels": BOUNDARY_EXCLUSION_PIXELS,
            "trainingSampleStride": TRAINING_STRIDE,
            "holdoutSampleStride": HOLDOUT_STRIDE,
            "periodicFilterBoundaryNote": (
                "FFT filtering is periodic; all scoring excludes 512 pixels "
                "from each window edge"
            ),
        },
        "clearAppearanceIdentity": clear_appearance_identity(captures),
        "capturedTranslationEquivariance": measurements[
            "adaptiveSpatialProbeStatistics"
        ]["translationEquivariance"],
        "fits": fits,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fit and hold out Liquid Glass v2.11 adaptive probes.",
    )
    parser.add_argument("captures", type=Path)
    parser.add_argument("measurements", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    measurements = json.loads(args.measurements.read_text(encoding="utf-8"))
    if not isinstance(measurements, dict):
        raise ValueError("measurement report must be a JSON object")
    if measurements.get("analysisSchemaVersion") != 8:
        raise ValueError("v2.11 fitting requires analysis schema 8")

    captures = CaptureSet.open(args.captures)
    try:
        if captures.manifest.get("rigVersion") != "2.11.0":
            raise ValueError("v2.11 fitting requires rig 2.11.0")
        report = fit_report(captures, measurements)
    finally:
        captures.close()

    report["source"]["captureArtifact"].update(
        {
            "file": args.captures.name,
            "sha256": file_sha256(args.captures) if args.captures.is_file() else None,
        }
    )
    report["source"]["measurementReport"].update(
        {
            "file": args.measurements.name,
            "sha256": file_sha256(args.measurements),
        }
    )
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(args.report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
