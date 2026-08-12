#!/usr/bin/env python3
"""Probe a local, phase-conditioned Liquid Glass operator on pixel noise."""

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
from numpy.lib.stride_tricks import sliding_window_view
from numpy.typing import NDArray
from scipy.sparse import coo_matrix
from scipy.sparse.linalg import factorized

from liquid_glass_spatial_fit import (
    CaptureSet,
    error_summary,
    fit_polynomial_transfer,
    predict_polynomial_transfer,
)


type FloatArray = NDArray[np.float64]
type IntArray = NDArray[np.int64]
type JsonObject = dict[str, Any]

RADII = (2, 4, 6, 8)
PHASE_PERIODS = (1, 2, 4, 8)
SOURCE_BACKGROUND_TRAIN = "noise-rgb-a064-train"
SOURCE_BACKGROUND_HOLDOUT = "noise-rgb-a064-holdout"
SCENE = "circle-0500-center"
MATERIAL = "clear"
APPEARANCE = "dark"
GIANT_SCENE = "circle-4000-center"
CIRCLE_RADIUS_PIXELS = 250
SCORING_RADIUS_PIXELS = 200
GEOMETRY_SWEEP_RADII_PIXELS = (50, 100, 150, 200)
GIANT_MARGIN_PIXELS = 512
GIANT_SAMPLE_COUNT = 200_000
GIANT_SAMPLE_SEED = 0x8E4C_91A2
RIDGE_PENALTY = 1.0
RECONSTRUCTION_SIGMAS = (0.0, 0.5, 1.0, 1.5, 2.0, 3.0, 4.0)
RECONSTRUCTION_DEGREES = (1, 2, 3)
INTERPOLATIONS = {
    "area": cv2.INTER_AREA,
    "linear": cv2.INTER_LINEAR,
    "cubic": cv2.INTER_CUBIC,
    "lanczos4": cv2.INTER_LANCZOS4,
}


@dataclass(frozen=True, slots=True)
class Coordinates:
    y: IntArray
    x: IntArray

    def select_phase(self, phase_period: int, phase_y: int, phase_x: int) -> "Coordinates":
        selected = (self.y % phase_period == phase_y) & (
            self.x % phase_period == phase_x
        )
        return Coordinates(y=self.y[selected], x=self.x[selected])


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def disk_coordinates(
    *,
    center_x: int,
    center_y: int,
    radius: int,
) -> Coordinates:
    offsets = np.arange(-radius, radius + 1, dtype=np.int64)
    relative_y, relative_x = np.meshgrid(offsets, offsets, indexing="ij")
    selected = np.square(relative_x) + np.square(relative_y) <= radius * radius
    return Coordinates(
        y=(relative_y[selected] + center_y).astype(np.int64),
        x=(relative_x[selected] + center_x).astype(np.int64),
    )


def sampled_rectangle_coordinates(
    shape: tuple[int, int],
    *,
    margin: int,
    sample_count: int,
    seed: int,
) -> Coordinates:
    height, width = shape
    inner_height = height - 2 * margin
    inner_width = width - 2 * margin
    population = inner_height * inner_width
    if (
        margin < 0
        or inner_height <= 0
        or inner_width <= 0
        or sample_count <= 0
        or sample_count > population
    ):
        raise ValueError("invalid rectangle sampling geometry")
    generator = np.random.default_rng(seed)
    indexes = generator.choice(
        population,
        size=sample_count,
        replace=False,
    )
    return Coordinates(
        y=(indexes // inner_width + margin).astype(np.int64),
        x=(indexes % inner_width + margin).astype(np.int64),
    )


def patch_view(source: FloatArray, radius: int) -> FloatArray:
    size = radius * 2 + 1
    return sliding_window_view(source, (size, size), axis=(0, 1))


def design_matrix(
    patches: FloatArray,
    coordinates: Coordinates,
    *,
    radius: int,
) -> FloatArray:
    selected = patches[coordinates.y - radius, coordinates.x - radius]
    flattened = selected.reshape(selected.shape[0], -1)
    return np.column_stack(
        (
            np.ones(flattened.shape[0], dtype=np.float64),
            flattened,
        )
    )


def fit_phase_models(
    source: FloatArray,
    output: FloatArray,
    coordinates: Coordinates,
    *,
    radius: int,
    phase_period: int,
    penalty: float,
) -> dict[tuple[int, int], FloatArray]:
    patches = patch_view(source, radius)
    coefficients: dict[tuple[int, int], FloatArray] = {}
    for phase_y in range(phase_period):
        for phase_x in range(phase_period):
            phase_coordinates = coordinates.select_phase(
                phase_period,
                phase_y,
                phase_x,
            )
            design = design_matrix(
                patches,
                phase_coordinates,
                radius=radius,
            )
            target = output[phase_coordinates.y, phase_coordinates.x]
            gram = design.T @ design
            regularizer = np.eye(gram.shape[0], dtype=np.float64) * penalty
            regularizer[0, 0] = 0.0
            coefficients[(phase_y, phase_x)] = np.linalg.solve(
                gram + regularizer,
                design.T @ target,
            )
    return coefficients


def predict_phase_models(
    source: FloatArray,
    coordinates: Coordinates,
    coefficients: dict[tuple[int, int], FloatArray],
    *,
    radius: int,
    phase_period: int,
) -> FloatArray:
    patches = patch_view(source, radius)
    prediction = np.empty((coordinates.y.size, 3), dtype=np.float64)
    indexes = np.arange(coordinates.y.size, dtype=np.int64)
    for phase_y in range(phase_period):
        for phase_x in range(phase_period):
            selected = (coordinates.y % phase_period == phase_y) & (
                coordinates.x % phase_period == phase_x
            )
            phase_coordinates = Coordinates(
                y=coordinates.y[selected],
                x=coordinates.x[selected],
            )
            design = design_matrix(
                patches,
                phase_coordinates,
                radius=radius,
            )
            prediction[indexes[selected]] = (
                design @ coefficients[(phase_y, phase_x)]
            )
    return np.clip(prediction, 0.0, 255.0)


def prediction_summary(actual: FloatArray, predicted: FloatArray) -> JsonObject:
    continuous = error_summary(actual, predicted)
    rounded = np.clip(np.floor(predicted + 0.5), 0.0, 255.0)
    delta = np.abs(actual - rounded)
    return {
        "continuous": continuous,
        "rounded": {
            **error_summary(actual, rounded),
            "exactChannelFraction": float(np.mean(delta == 0.0)),
            "exactPixelFraction": float(np.mean(np.all(delta == 0.0, axis=1))),
        },
    }


def half_resolution_reconstruction(
    source: FloatArray,
    *,
    blur_position: str,
    sigma: float,
    down_interpolation: int,
) -> FloatArray:
    working = source.astype(np.float32)
    if blur_position == "before-downsample" and sigma > 0.0:
        working = cv2.GaussianBlur(
            working,
            (0, 0),
            sigmaX=sigma,
            sigmaY=sigma,
            borderType=cv2.BORDER_REFLECT_101,
        )
    height, width = working.shape[:2]
    reduced = cv2.resize(
        working,
        (width // 2, height // 2),
        interpolation=down_interpolation,
    )
    if blur_position == "after-downsample" and sigma > 0.0:
        reduced = cv2.GaussianBlur(
            reduced,
            (0, 0),
            sigmaX=sigma,
            sigmaY=sigma,
            borderType=cv2.BORDER_REFLECT_101,
        )
    return cv2.resize(
        reduced,
        (width, height),
        interpolation=cv2.INTER_LINEAR,
    ).astype(np.float64)


def linear_interpolation_matrix(
    source_size: int,
    target_size: int,
):
    if source_size <= 0 or target_size <= 0:
        raise ValueError("interpolation dimensions must be positive")
    target = np.arange(target_size, dtype=np.int64)
    coordinate = (target + 0.5) * source_size / target_size - 0.5
    left = np.floor(coordinate).astype(np.int64)
    fraction = coordinate - left
    rows = np.repeat(target, 2)
    columns = np.column_stack((left, left + 1)).reshape(-1)
    columns = np.clip(columns, 0, source_size - 1)
    values = np.column_stack((1.0 - fraction, fraction)).reshape(-1)
    return coo_matrix(
        (values, (rows, columns)),
        shape=(target_size, source_size),
    ).tocsr()


def bilinear_subspace_projection(
    image: FloatArray,
    *,
    factor: int,
) -> tuple[FloatArray, FloatArray]:
    height, width = image.shape[:2]
    if (
        factor <= 0
        or height % factor
        or width % factor
        or image.ndim != 3
        or image.shape[2] != 3
    ):
        raise ValueError("invalid bilinear projection geometry")
    vertical = linear_interpolation_matrix(height // factor, height)
    horizontal = linear_interpolation_matrix(width // factor, width)
    solve_vertical = factorized((vertical.T @ vertical).tocsc())
    solve_horizontal = factorized((horizontal.T @ horizontal).tocsc())
    low_resolution = np.empty((height // factor, width // factor, 3))
    reconstructed = np.empty_like(image, dtype=np.float64)
    for channel in range(3):
        right_hand_side = vertical.T @ image[:, :, channel] @ horizontal
        low_resolution[:, :, channel] = solve_horizontal(
            solve_vertical(right_hand_side).T
        ).T
        reconstructed[:, :, channel] = (
            vertical @ low_resolution[:, :, channel] @ horizontal.T
        )
    return low_resolution, reconstructed


def regular_bilinear_subspace_report(captures: CaptureSet) -> JsonObject:
    region = (
        slice(GIANT_MARGIN_PIXELS, -GIANT_MARGIN_PIXELS),
        slice(GIANT_MARGIN_PIXELS, -GIANT_MARGIN_PIXELS),
    )
    records: JsonObject = {}
    for appearance in ("dark", "light"):
        for role in ("train", "holdout"):
            actual = captures.image(
                f"noise-rgb-a064-{role}",
                GIANT_SCENE,
                "regular",
                appearance,
            )
            low_resolution, reconstructed = bilinear_subspace_projection(
                actual,
                factor=4,
            )
            records[f"{appearance}/{role}"] = {
                "lowResolutionMinimumCodes": low_resolution.min(
                    axis=(0, 1)
                ).tolist(),
                "lowResolutionMaximumCodes": low_resolution.max(
                    axis=(0, 1)
                ).tolist(),
                "centralProjection": prediction_summary(
                    actual[region].reshape(-1, 3),
                    reconstructed[region].reshape(-1, 3),
                ),
            }
    return {
        "factor": 4,
        "interpolation": "linear",
        "coordinateConvention": "half-pixel",
        "fit": "unconstrained least-squares low-resolution image",
        "boundaryExclusionPixels": GIANT_MARGIN_PIXELS,
        "interpretation": (
            "This is the best continuous projection onto the measured 4x "
            "bilinear reconstruction subspace, not a source-to-output model."
        ),
        "records": records,
    }


def reconstruction_search(
    train_source: FloatArray,
    holdout_source: FloatArray,
    train_output: FloatArray,
    holdout_output: FloatArray,
    coordinates: Coordinates,
) -> JsonObject:
    train_actual = train_output[coordinates.y, coordinates.x]
    holdout_actual = holdout_output[coordinates.y, coordinates.x]
    candidates: list[JsonObject] = []
    for blur_position in ("before-downsample", "after-downsample"):
        for sigma in RECONSTRUCTION_SIGMAS:
            for interpolation_name, interpolation in INTERPOLATIONS.items():
                train_filtered = half_resolution_reconstruction(
                    train_source,
                    blur_position=blur_position,
                    sigma=sigma,
                    down_interpolation=interpolation,
                )[coordinates.y, coordinates.x]
                holdout_filtered = half_resolution_reconstruction(
                    holdout_source,
                    blur_position=blur_position,
                    sigma=sigma,
                    down_interpolation=interpolation,
                )[coordinates.y, coordinates.x]
                for degree in RECONSTRUCTION_DEGREES:
                    exponents, coefficients = fit_polynomial_transfer(
                        train_filtered,
                        train_actual,
                        degree=degree,
                    )
                    train_prediction = predict_polynomial_transfer(
                        train_filtered,
                        exponents,
                        coefficients,
                    )
                    holdout_prediction = predict_polynomial_transfer(
                        holdout_filtered,
                        exponents,
                        coefficients,
                    )
                    candidates.append(
                        {
                            "blurPosition": blur_position,
                            "sigmaPixelsAtBlurStage": sigma,
                            "downsampleInterpolation": interpolation_name,
                            "upsampleInterpolation": "linear",
                            "pointwisePolynomialDegree": degree,
                            "pointwisePolynomialTerms": len(exponents),
                            "training": prediction_summary(
                                train_actual,
                                train_prediction,
                            ),
                            "independentSeedHoldout": prediction_summary(
                                holdout_actual,
                                holdout_prediction,
                            ),
                        }
                    )
    ranked = sorted(
        candidates,
        key=lambda candidate: (
            float(candidate["training"]["continuous"]["meanAbsoluteCodes"]),
            int(candidate["pointwisePolynomialTerms"]),
        ),
    )
    return {
        "selectionRule": (
            "rank only by training-seed continuous MAE, then polynomial term "
            "count; the independent seed is reported but not used to rank"
        ),
        "candidateCount": len(candidates),
        "trainingSelected": ranked[0],
        "bestTrainingCandidates": ranked[:24],
    }


def geometry_radius_sweep(
    train_source: FloatArray,
    holdout_source: FloatArray,
    train_output: FloatArray,
    holdout_output: FloatArray,
    *,
    center_x: int,
    center_y: int,
) -> list[JsonObject]:
    radius = 8
    phase_period = 2
    result: list[JsonObject] = []
    for scoring_radius in GEOMETRY_SWEEP_RADII_PIXELS:
        coordinates = disk_coordinates(
            center_x=center_x,
            center_y=center_y,
            radius=scoring_radius,
        )
        coefficients = fit_phase_models(
            train_source,
            train_output,
            coordinates,
            radius=radius,
            phase_period=phase_period,
            penalty=RIDGE_PENALTY,
        )
        train_prediction = predict_phase_models(
            train_source,
            coordinates,
            coefficients,
            radius=radius,
            phase_period=phase_period,
        )
        holdout_prediction = predict_phase_models(
            holdout_source,
            coordinates,
            coefficients,
            radius=radius,
            phase_period=phase_period,
        )
        result.append(
            {
                "scoringDiskRadiusPixels": scoring_radius,
                "samples": int(coordinates.y.size),
                "training": prediction_summary(
                    train_output[coordinates.y, coordinates.x],
                    train_prediction,
                ),
                "independentSeedHoldout": prediction_summary(
                    holdout_output[coordinates.y, coordinates.x],
                    holdout_prediction,
                ),
            }
        )
    return result


def regular_giant_phase_probe(captures: CaptureSet) -> JsonObject:
    train_source = (
        captures.reference_image(SOURCE_BACKGROUND_TRAIN) - 128.0
    ) / 64.0
    holdout_source = (
        captures.reference_image(SOURCE_BACKGROUND_HOLDOUT) - 128.0
    ) / 64.0
    coordinates = sampled_rectangle_coordinates(
        train_source.shape[:2],
        margin=GIANT_MARGIN_PIXELS,
        sample_count=GIANT_SAMPLE_COUNT,
        seed=GIANT_SAMPLE_SEED,
    )
    result: JsonObject = {}
    for appearance in ("dark", "light"):
        train_output = captures.image(
            SOURCE_BACKGROUND_TRAIN,
            GIANT_SCENE,
            "regular",
            appearance,
        )
        holdout_output = captures.image(
            SOURCE_BACKGROUND_HOLDOUT,
            GIANT_SCENE,
            "regular",
            appearance,
        )
        actual_train = train_output[coordinates.y, coordinates.x]
        actual_holdout = holdout_output[coordinates.y, coordinates.x]
        candidates: list[JsonObject] = []
        for phase_period in PHASE_PERIODS:
            coefficients = fit_phase_models(
                train_source,
                train_output,
                coordinates,
                radius=8,
                phase_period=phase_period,
                penalty=RIDGE_PENALTY,
            )
            train_prediction = predict_phase_models(
                train_source,
                coordinates,
                coefficients,
                radius=8,
                phase_period=phase_period,
            )
            holdout_prediction = predict_phase_models(
                holdout_source,
                coordinates,
                coefficients,
                radius=8,
                phase_period=phase_period,
            )
            candidates.append(
                {
                    "phasePeriodPixels": phase_period,
                    "phaseCount": phase_period**2,
                    "termsPerPhase": 1 + 3 * 17**2,
                    "minimumSamplesPerPhase": min(
                        coordinates.select_phase(
                            phase_period,
                            phase_y,
                            phase_x,
                        ).y.size
                        for phase_y in range(phase_period)
                        for phase_x in range(phase_period)
                    ),
                    "training": prediction_summary(
                        actual_train,
                        train_prediction,
                    ),
                    "independentSeedHoldout": prediction_summary(
                        actual_holdout,
                        holdout_prediction,
                    ),
                }
            )
        result[appearance] = candidates
    return {
        "scene": GIANT_SCENE,
        "material": "regular",
        "trainingBackground": SOURCE_BACKGROUND_TRAIN,
        "holdoutBackground": SOURCE_BACKGROUND_HOLDOUT,
        "radiusPixels": 8,
        "sampleCount": int(coordinates.y.size),
        "sampleSeed": f"0x{GIANT_SAMPLE_SEED:08x}",
        "boundaryExclusionPixels": GIANT_MARGIN_PIXELS,
        "note": (
            "This compact kernel diagnoses output-grid phase. Regular also "
            "has a broad response outside the 17x17 support, so these errors "
            "are not a complete regular-material model."
        ),
        "appearances": result,
    }


def fit_report(captures: CaptureSet) -> JsonObject:
    center_x, center_y = captures.scene_center(SCENE)
    scale = float(captures.manifest["backingScaleFactor"])
    circle_radius = round(CIRCLE_RADIUS_PIXELS * scale)
    scoring_radius = round(SCORING_RADIUS_PIXELS * scale)
    maximum_radius = max(RADII)
    if scoring_radius + maximum_radius >= circle_radius:
        raise ValueError("scoring disk does not exclude the glass boundary")

    coordinates = disk_coordinates(
        center_x=center_x,
        center_y=center_y,
        radius=scoring_radius,
    )
    train_source_codes = captures.reference_image(SOURCE_BACKGROUND_TRAIN)
    holdout_source_codes = captures.reference_image(SOURCE_BACKGROUND_HOLDOUT)
    train_source = (train_source_codes - 128.0) / 64.0
    holdout_source = (holdout_source_codes - 128.0) / 64.0
    train_output = captures.image(
        SOURCE_BACKGROUND_TRAIN,
        SCENE,
        MATERIAL,
        APPEARANCE,
    )
    holdout_output = captures.image(
        SOURCE_BACKGROUND_HOLDOUT,
        SCENE,
        MATERIAL,
        APPEARANCE,
    )
    actual_train = train_output[coordinates.y, coordinates.x]
    actual_holdout = holdout_output[coordinates.y, coordinates.x]

    candidates: list[JsonObject] = []
    for radius in RADII:
        for phase_period in PHASE_PERIODS:
            coefficients = fit_phase_models(
                train_source,
                train_output,
                coordinates,
                radius=radius,
                phase_period=phase_period,
                penalty=RIDGE_PENALTY,
            )
            train_prediction = predict_phase_models(
                train_source,
                coordinates,
                coefficients,
                radius=radius,
                phase_period=phase_period,
            )
            holdout_prediction = predict_phase_models(
                holdout_source,
                coordinates,
                coefficients,
                radius=radius,
                phase_period=phase_period,
            )
            terms_per_phase = 1 + 3 * (2 * radius + 1) ** 2
            candidates.append(
                {
                    "radiusPixels": radius,
                    "phasePeriodPixels": phase_period,
                    "phaseCount": phase_period**2,
                    "termsPerPhase": terms_per_phase,
                    "totalTerms": terms_per_phase * phase_period**2,
                    "samples": int(coordinates.y.size),
                    "minimumSamplesPerPhase": min(
                        int(
                            coordinates.select_phase(
                                phase_period,
                                phase_y,
                                phase_x,
                            ).y.size
                        )
                        for phase_y in range(phase_period)
                        for phase_x in range(phase_period)
                    ),
                    "training": prediction_summary(
                        actual_train,
                        train_prediction,
                    ),
                    "independentSeedHoldout": prediction_summary(
                        actual_holdout,
                        holdout_prediction,
                    ),
                }
            )

    return {
        "polyphaseProbeSchemaVersion": 1,
        "analysisImplementation": {
            "file": "analysis/liquid_glass_polyphase_probe.py",
            "sha256": file_sha256(Path(__file__).resolve()),
            "python": platform.python_version(),
            "numpy": package_version("numpy"),
            "opencv": package_version("opencv"),
        },
        "source": {
            "rigVersion": captures.manifest.get("rigVersion"),
            "ciCommit": captures.manifest.get("ciCommit"),
            "scene": SCENE,
            "material": MATERIAL,
            "appearance": APPEARANCE,
            "trainingBackground": SOURCE_BACKGROUND_TRAIN,
            "holdoutBackground": SOURCE_BACKGROUND_HOLDOUT,
        },
        "policy": {
            "productionShaderModified": False,
            "selectionEvidence": "training seed only",
            "evaluationEvidence": "independent declared holdout seed",
            "purpose": (
                "diagnose compact local support and fixed-grid phase behavior; "
                "not a parity claim"
            ),
        },
        "model": {
            "kind": "phase-conditioned complete 3x3 local RGB convolution",
            "inputNormalization": "(captured source code - 128) / 64",
            "ridgePenalty": RIDGE_PENALTY,
            "candidateRadiiPixels": list(RADII),
            "candidatePhasePeriodsPixels": list(PHASE_PERIODS),
            "scoringDiskRadiusPixels": scoring_radius,
            "glassBoundaryExclusionPixels": circle_radius - scoring_radius,
        },
        "candidates": candidates,
        "geometryRadiusSweep": {
            "fixedModel": {
                "radiusPixels": 8,
                "phasePeriodPixels": 2,
            },
            "records": geometry_radius_sweep(
                train_source,
                holdout_source,
                train_output,
                holdout_output,
                center_x=center_x,
                center_y=center_y,
            ),
        },
        "halfResolutionPipelineSearch": reconstruction_search(
            train_source_codes,
            holdout_source_codes,
            train_output,
            holdout_output,
            coordinates,
        ),
        "regularGiantPhaseProbe": regular_giant_phase_probe(captures),
        "regularBilinearSubspaceProjection": (
            regular_bilinear_subspace_report(captures)
        ),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Probe a local polyphase Liquid Glass operator.",
    )
    parser.add_argument("captures", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    captures = CaptureSet.open(args.captures)
    try:
        if captures.manifest.get("rigVersion") != "2.11.0":
            raise ValueError("polyphase probe requires rig 2.11.0")
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
