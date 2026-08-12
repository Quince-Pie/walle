#!/usr/bin/env python3
"""Fit regular material on its measured quarter-resolution sampling grid."""

import argparse
import hashlib
import json
import platform
from dataclasses import dataclass
from importlib.metadata import version as package_version
from pathlib import Path
from typing import Any

import cv2
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


type FloatArray = NDArray[np.float64]
type JsonObject = dict[str, Any]

SCENE = "circle-4000-center"
APPEARANCES = ("dark", "light")
CHANNEL_KINDS = ("gray", "rgb")
AMPLITUDES = (16, 64)
LOW_RESOLUTION_SIGMAS = (0.0, 0.5, 1.0, 2.0, 4.0, 8.0, 16.0, 32.0, 64.0)
SCALE_COUNTS = (1, 3, 5, 7, 9)
SOURCE_SPACES = ("srgb-code", "linear-srgb")
RIDGE_PENALTIES = (1e-3, 0.1, 10.0, 1000.0)
INTERPOLATIONS = {
    "area": cv2.INTER_AREA,
    "linear": cv2.INTER_LINEAR,
    "cubic": cv2.INTER_CUBIC,
    "lanczos4": cv2.INTER_LANCZOS4,
}
TRAINING_STRIDE = 13
HOLDOUT_STRIDE = 11
BOUNDARY_EXCLUSION_PIXELS = 512


@dataclass(slots=True, kw_only=True)
class ProbeFeatures:
    name: str
    background: str
    raw: FloatArray
    quarter_scales: list[FloatArray]
    outputs: dict[str, FloatArray]


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def probe_backgrounds(role: str) -> dict[str, str]:
    return {
        f"{channel}-a{amplitude:03d}": (
            f"noise-{channel}-a{amplitude:03d}-{role}"
        )
        for channel in CHANNEL_KINDS
        for amplitude in AMPLITUDES
    }


def sample_region(
    shape: tuple[int, int],
    *,
    stride: int,
    margin: int = BOUNDARY_EXCLUSION_PIXELS,
) -> tuple[slice, slice]:
    height, width = shape
    if stride <= 0 or height <= 2 * margin or width <= 2 * margin:
        raise ValueError("invalid sample geometry")
    return (
        slice(margin, height - margin, stride),
        slice(margin, width - margin, stride),
    )


def quarter_scale_features(
    source: FloatArray,
    *,
    source_space: str,
    downsample_interpolation: int,
    stride: int,
) -> tuple[FloatArray, list[FloatArray]]:
    height, width = source.shape[:2]
    if height % 4 or width % 4:
        raise ValueError("quarter-grid fit requires dimensions divisible by four")
    region = sample_region((height, width), stride=stride)
    working = to_working_space(source, source_space).astype(np.float32)
    reduced = cv2.resize(
        working,
        (width // 4, height // 4),
        interpolation=downsample_interpolation,
    )
    scales: list[FloatArray] = []
    for sigma in LOW_RESOLUTION_SIGMAS:
        if sigma == 0.0:
            filtered = reduced
        else:
            filtered = cv2.GaussianBlur(
                reduced,
                (0, 0),
                sigmaX=sigma,
                sigmaY=sigma,
                borderType=cv2.BORDER_REFLECT_101,
            )
        reconstructed = cv2.resize(
            filtered,
            (width, height),
            interpolation=cv2.INTER_LINEAR,
        )
        sampled = reconstructed[region].reshape(-1, 3).astype(np.float64)
        scales.append(from_working_space(sampled, source_space))
    return source[region].reshape(-1, 3), scales


def load_probe_features(
    captures: CaptureSet,
    backgrounds: dict[str, str],
    *,
    source_space: str,
    interpolation: int,
    stride: int,
) -> dict[str, ProbeFeatures]:
    result: dict[str, ProbeFeatures] = {}
    for name, background in backgrounds.items():
        source = captures.reference_image(background)
        region = sample_region(source.shape[:2], stride=stride)
        raw, scales = quarter_scale_features(
            source,
            source_space=source_space,
            downsample_interpolation=interpolation,
            stride=stride,
        )
        result[name] = ProbeFeatures(
            name=name,
            background=background,
            raw=raw,
            quarter_scales=scales,
            outputs={
                appearance: captures.image(
                    background,
                    SCENE,
                    "regular",
                    appearance,
                )[region].reshape(-1, 3)
                for appearance in APPEARANCES
            },
        )
    return result


def model_design(
    probe: ProbeFeatures,
    *,
    scale_count: int,
    include_raw: bool,
    degree: int,
) -> FloatArray:
    inputs = [
        *([probe.raw] if include_raw else []),
        *probe.quarter_scales[:scale_count],
    ]
    return per_scale_polynomial_design(inputs, degree=degree)


def cross_validate_candidate(
    groups: dict[str, ProbeFeatures],
    *,
    appearance: str,
    scale_count: int,
    include_raw: bool,
    degree: int,
    penalty: float,
) -> JsonObject:
    designs = {
        name: model_design(
            probe,
            scale_count=scale_count,
            include_raw=include_raw,
            degree=degree,
        )
        for name, probe in groups.items()
    }
    moments = {
        name: (
            design.T @ design,
            design.T @ groups[name].outputs[appearance],
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
        actual = groups[name].outputs[appearance]
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


def fit_coefficients(
    groups: dict[str, ProbeFeatures],
    *,
    appearance: str,
    scale_count: int,
    include_raw: bool,
    degree: int,
    penalty: float,
) -> FloatArray:
    terms_per_input = len(polynomial_exponents(degree)) - 1
    term_count = 1 + terms_per_input * (scale_count + int(include_raw))
    gram = np.zeros((term_count, term_count), dtype=np.float64)
    right_hand_side = np.zeros((term_count, 3), dtype=np.float64)
    for probe in groups.values():
        design = model_design(
            probe,
            scale_count=scale_count,
            include_raw=include_raw,
            degree=degree,
        )
        gram += design.T @ design
        right_hand_side += design.T @ probe.outputs[appearance]
    return ridge_solve(gram, right_hand_side, penalty=penalty)


def evaluate_groups(
    groups: dict[str, ProbeFeatures],
    *,
    appearance: str,
    scale_count: int,
    include_raw: bool,
    degree: int,
    coefficients: FloatArray,
) -> JsonObject:
    actual_parts: list[FloatArray] = []
    predicted_parts: list[FloatArray] = []
    probes: JsonObject = {}
    for name, probe in groups.items():
        actual = probe.outputs[appearance]
        predicted = np.clip(
            model_design(
                probe,
                scale_count=scale_count,
                include_raw=include_raw,
                degree=degree,
            )
            @ coefficients,
            0.0,
            255.0,
        )
        probes[name] = prediction_report(actual, predicted)
        actual_parts.append(actual)
        predicted_parts.append(predicted)
    return {
        "pooled": prediction_report(
            np.vstack(actual_parts),
            np.vstack(predicted_parts),
        ),
        "probes": probes,
    }


def fit_report(captures: CaptureSet) -> JsonObject:
    training_backgrounds = probe_backgrounds("train")
    holdout_backgrounds = probe_backgrounds("holdout")
    feature_sets: dict[
        tuple[str, str],
        tuple[dict[str, ProbeFeatures], dict[str, ProbeFeatures]],
    ] = {}
    for source_space in SOURCE_SPACES:
        for interpolation_name, interpolation in INTERPOLATIONS.items():
            feature_sets[(source_space, interpolation_name)] = (
                load_probe_features(
                    captures,
                    training_backgrounds,
                    source_space=source_space,
                    interpolation=interpolation,
                    stride=TRAINING_STRIDE,
                ),
                load_probe_features(
                    captures,
                    holdout_backgrounds,
                    source_space=source_space,
                    interpolation=interpolation,
                    stride=HOLDOUT_STRIDE,
                ),
            )

    fits: JsonObject = {}
    for appearance in APPEARANCES:
        candidates: list[JsonObject] = []
        for (source_space, interpolation_name), (training, _) in feature_sets.items():
            for scale_count in SCALE_COUNTS:
                for include_raw in (False, True):
                    for degree in (1, 2, 3):
                        terms_per_input = len(polynomial_exponents(degree)) - 1
                        for penalty in RIDGE_PENALTIES:
                            validation = cross_validate_candidate(
                                training,
                                appearance=appearance,
                                scale_count=scale_count,
                                include_raw=include_raw,
                                degree=degree,
                                penalty=penalty,
                            )
                            candidates.append(
                                {
                                    "sourceSpace": source_space,
                                    "downsampleInterpolation": interpolation_name,
                                    "scaleCount": scale_count,
                                    "lowResolutionSigmasPixels": list(
                                        LOW_RESOLUTION_SIGMAS[:scale_count]
                                    ),
                                    "equivalentFullResolutionSigmasPixels": [
                                        sigma * 4
                                        for sigma in LOW_RESOLUTION_SIGMAS[:scale_count]
                                    ],
                                    "includesFullResolutionRawPath": include_raw,
                                    "degreePerScale": degree,
                                    "terms": 1
                                    + terms_per_input
                                    * (scale_count + int(include_raw)),
                                    "ridgePenalty": penalty,
                                    **validation,
                                }
                            )
        ranked = sorted(
            candidates,
            key=lambda candidate: (
                float(candidate["pooledError"]["meanAbsoluteCodes"]),
                float(candidate["worstProbeMeanAbsoluteCodes"]),
                int(candidate["terms"]),
                float(candidate["ridgePenalty"]),
            ),
        )
        best_by_pipeline = [
            min(
                (
                    candidate
                    for candidate in candidates
                    if candidate["sourceSpace"] == source_space
                    and candidate["downsampleInterpolation"]
                    == interpolation_name
                ),
                key=lambda candidate: (
                    float(candidate["pooledError"]["meanAbsoluteCodes"]),
                    float(candidate["worstProbeMeanAbsoluteCodes"]),
                    int(candidate["terms"]),
                ),
            )
            for source_space in SOURCE_SPACES
            for interpolation_name in INTERPOLATIONS
        ]
        selected = ranked[0]
        training, holdout = feature_sets[
            (
                str(selected["sourceSpace"]),
                str(selected["downsampleInterpolation"]),
            )
        ]
        scale_count = int(selected["scaleCount"])
        include_raw = bool(selected["includesFullResolutionRawPath"])
        degree = int(selected["degreePerScale"])
        coefficients = fit_coefficients(
            training,
            appearance=appearance,
            scale_count=scale_count,
            include_raw=include_raw,
            degree=degree,
            penalty=float(selected["ridgePenalty"]),
        )
        fits[appearance] = {
            "selected": selected,
            "candidateCount": len(candidates),
            "bestCandidates": ranked[:24],
            "bestBySourceSpaceAndDownsampleInterpolation": best_by_pipeline,
            "coefficients": coefficients.tolist(),
            "holdouts": evaluate_groups(
                holdout,
                appearance=appearance,
                scale_count=scale_count,
                include_raw=include_raw,
                degree=degree,
                coefficients=coefficients,
            ),
        }

    return {
        "quarterGridFitSchemaVersion": 1,
        "analysisImplementation": {
            "file": "analysis/liquid_glass_quarter_grid_fit.py",
            "sha256": file_sha256(Path(__file__).resolve()),
            "python": platform.python_version(),
            "numpy": package_version("numpy"),
            "opencv": package_version("opencv"),
        },
        "source": {
            "rigVersion": captures.manifest.get("rigVersion"),
            "ciCommit": captures.manifest.get("ciCommit"),
            "scene": SCENE,
            "material": "regular",
            "trainingBackgrounds": list(training_backgrounds.values()),
            "holdoutBackgrounds": list(holdout_backgrounds.values()),
        },
        "policy": {
            "productionShaderModified": False,
            "selectionRule": (
                "minimize leave-one-training-probe-out pooled MAE, then worst "
                "probe MAE, term count, and ridge penalty; independent seeds "
                "are opened only after selection"
            ),
        },
        "featureModel": {
            "kind": (
                "quarter-resolution source, Gaussian bank on the quarter grid, "
                "measured 4x half-pixel linear reconstruction, optional "
                "full-resolution raw path, and a complete RGB polynomial per "
                "spatial scale"
            ),
            "candidateSourceSpaces": list(SOURCE_SPACES),
            "candidateDownsampleInterpolations": list(INTERPOLATIONS),
            "candidateLowResolutionSigmasPixels": list(LOW_RESOLUTION_SIGMAS),
            "candidateScaleCounts": list(SCALE_COUNTS),
            "candidateDegreesPerScale": [1, 2, 3],
            "candidateRidgePenalties": list(RIDGE_PENALTIES),
            "boundaryExclusionPixels": BOUNDARY_EXCLUSION_PIXELS,
            "trainingStride": TRAINING_STRIDE,
            "holdoutStride": HOLDOUT_STRIDE,
        },
        "fits": fits,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fit regular Liquid Glass on its measured quarter grid.",
    )
    parser.add_argument("captures", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    captures = CaptureSet.open(args.captures)
    try:
        if captures.manifest.get("rigVersion") != "2.11.0":
            raise ValueError("quarter-grid fit requires rig 2.11.0")
        report = fit_report(captures)
    finally:
        captures.close()

    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
