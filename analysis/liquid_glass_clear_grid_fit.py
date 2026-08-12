#!/usr/bin/env python3
"""Identify Liquid Glass clear material on its measured half-resolution grid."""

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

from liquid_glass_polyphase_probe import (
    bilinear_subspace_projection,
    prediction_summary,
)
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
MATERIAL = "clear"
APPEARANCE = "dark"
APPEARANCES = ("dark", "light")
CHANNEL_KINDS = ("gray", "rgb")
AMPLITUDES = (16, 64)
LOCAL_MEANS = (64, 128, 192)
BOUNDARY_EXCLUSION_PIXELS = 512
TRAINING_STRIDE = 13
HOLDOUT_STRIDE = 11
SOURCE_SPACES = ("srgb-code", "linear-srgb")
INTERPOLATIONS = {
    "area": cv2.INTER_AREA,
    "cubic": cv2.INTER_CUBIC,
    "lanczos4": cv2.INTER_LANCZOS4,
}
RIDGE_PENALTIES = (1e-3, 0.1, 10.0, 1000.0)
HALF_SIGMAS = (0.0, 0.5, 1.0, 2.0)
QUARTER_SIGMAS = (0.0, 0.5, 1.0, 1.5, 2.0, 3.0, 4.0, 8.0)
EIGHTH_SIGMAS = (0.0, 0.5, 1.0, 2.0)


def feature_name(grid: str, sigma: float) -> str:
    return f"{grid}/sigma-{sigma:g}"


HALF_FEATURES = tuple(feature_name("half", sigma) for sigma in HALF_SIGMAS)
QUARTER_FEATURES = tuple(
    feature_name("quarter", sigma) for sigma in QUARTER_SIGMAS
)
EIGHTH_FEATURES = tuple(
    feature_name("eighth", sigma) for sigma in EIGHTH_SIGMAS
)
FEATURE_MODELS = {
    "half-only": (HALF_FEATURES[0],),
    "half-gaussian-bank": HALF_FEATURES,
    "half-plus-quarter-single": (
        HALF_FEATURES[0],
        feature_name("quarter", 1.0),
    ),
    "half-plus-quarter-compact": (
        HALF_FEATURES[0],
        *QUARTER_FEATURES[:4],
    ),
    "half-plus-quarter-full": (
        HALF_FEATURES[0],
        *QUARTER_FEATURES,
    ),
    "half-quarter-eighth": (
        HALF_FEATURES[0],
        *QUARTER_FEATURES[:4],
        *EIGHTH_FEATURES,
    ),
}


@dataclass(slots=True, kw_only=True)
class ProbeFeatures:
    name: str
    background: str
    features: dict[str, FloatArray]
    output: FloatArray
    radius_fraction: FloatArray


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def probe_backgrounds(role: str) -> dict[str, str]:
    backgrounds = {
        f"pixel-{channel}-a{amplitude:03d}": (
            f"noise-{channel}-a{amplitude:03d}-{role}"
        )
        for channel in CHANNEL_KINDS
        for amplitude in AMPLITUDES
    }
    backgrounds.update(
        {
            f"block-{channel}-m{mean:03d}": (
                f"noise-{channel}-m{mean:03d}-a032-b0016-{role}"
            )
            for channel in CHANNEL_KINDS
            for mean in LOCAL_MEANS
        }
    )
    return backgrounds


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


def sampled_radius_fraction(
    shape: tuple[int, int],
    *,
    center_x: float,
    center_y: float,
    radius: float,
    stride: int,
    margin: int = BOUNDARY_EXCLUSION_PIXELS,
) -> FloatArray:
    if radius <= 0.0:
        raise ValueError("glass radius must be positive")
    region = sample_region(shape, stride=stride, margin=margin)
    y = np.arange(shape[0], dtype=np.float64)[region[0]]
    x = np.arange(shape[1], dtype=np.float64)[region[1]]
    grid_y, grid_x = np.meshgrid(y, x, indexing="ij")
    return (
        np.hypot(grid_x - center_x, grid_y - center_y) / radius
    ).reshape(-1)


def scene_circle_geometry(captures: CaptureSet) -> tuple[float, float, float]:
    scene = captures.scenes[SCENE]
    shapes = scene.get("shapes")
    if not isinstance(shapes, list) or len(shapes) != 1:
        raise ValueError(f"{SCENE} must contain exactly one shape")
    shape = shapes[0]
    scale = float(captures.manifest["backingScaleFactor"])
    width = float(shape["width"]) * scale
    height = float(shape["height"]) * scale
    if shape.get("kind") != "circle" or width != height:
        raise ValueError(f"{SCENE} must be a circle")
    return (
        float(shape["centerX"]) * scale,
        float(shape["centerY"]) * scale,
        width / 2.0,
    )


def pipeline_feature_bank(
    source: FloatArray,
    *,
    source_space: str,
    interpolation: int,
    stride: int,
) -> dict[str, FloatArray]:
    height, width = source.shape[:2]
    if height % 8 or width % 8:
        raise ValueError("clear-grid fit requires dimensions divisible by eight")
    region = sample_region((height, width), stride=stride)
    working = to_working_space(source, source_space).astype(np.float32)
    result: dict[str, FloatArray] = {}
    grids = (
        ("half", 2, HALF_SIGMAS),
        ("quarter", 4, QUARTER_SIGMAS),
        ("eighth", 8, EIGHTH_SIGMAS),
    )
    for grid_name, factor, sigmas in grids:
        reduced = cv2.resize(
            working,
            (width // factor, height // factor),
            interpolation=interpolation,
        )
        for sigma in sigmas:
            filtered = (
                reduced
                if sigma == 0.0
                else cv2.GaussianBlur(
                    reduced,
                    (0, 0),
                    sigmaX=sigma,
                    sigmaY=sigma,
                    borderType=cv2.BORDER_REFLECT_101,
                )
            )
            reconstructed = cv2.resize(
                filtered,
                (width, height),
                interpolation=cv2.INTER_LINEAR,
            )
            sampled = reconstructed[region].reshape(-1, 3).astype(np.float64)
            result[feature_name(grid_name, sigma)] = from_working_space(
                sampled,
                source_space,
            )
    return result


def symmetric_ring_masks(maximum_radius: int) -> dict[int, FloatArray]:
    if maximum_radius < 0:
        raise ValueError("ring radius must be nonnegative")
    size = maximum_radius * 2 + 1
    offsets: dict[int, list[tuple[int, int]]] = {}
    for y in range(-maximum_radius, maximum_radius + 1):
        for x in range(-maximum_radius, maximum_radius + 1):
            offsets.setdefault(x * x + y * y, []).append((y, x))
    result: dict[int, FloatArray] = {}
    for squared_radius, positions in offsets.items():
        kernel = np.zeros((size, size), dtype=np.float64)
        weight = 1.0 / len(positions)
        for y, x in positions:
            kernel[y + maximum_radius, x + maximum_radius] = weight
        result[squared_radius] = kernel
    return result


def ring_feature_name(squared_radius: int) -> str:
    return f"half/ring-{squared_radius}"


def hybrid_feature_names(
    radius: int,
    *,
    ring_masks: dict[int, FloatArray],
    support_shape: str = "disk",
) -> tuple[str, ...]:
    if support_shape == "disk":
        selected_rings = (
            squared_radius
            for squared_radius in sorted(ring_masks)
            if squared_radius <= radius * radius
        )
    elif support_shape == "square":
        maximum_radius = (next(iter(ring_masks.values())).shape[0] - 1) // 2
        if radius != maximum_radius:
            raise ValueError("square support requires the maximum ring radius")
        selected_rings = iter(sorted(ring_masks))
    else:
        raise ValueError(f"unknown support shape: {support_shape}")
    return (
        *(
            ring_feature_name(squared_radius)
            for squared_radius in selected_rings
        ),
        *QUARTER_FEATURES[:4],
        *EIGHTH_FEATURES,
    )


def hybrid_feature_bank(
    source: FloatArray,
    *,
    source_space: str,
    interpolation: int,
    stride: int,
    ring_masks: dict[int, FloatArray],
) -> dict[str, FloatArray]:
    height, width = source.shape[:2]
    if height % 8 or width % 8:
        raise ValueError("hybrid fit requires dimensions divisible by eight")
    region = sample_region((height, width), stride=stride)
    working = to_working_space(source, source_space).astype(np.float32)
    half = cv2.resize(
        working,
        (width // 2, height // 2),
        interpolation=interpolation,
    )
    result: dict[str, FloatArray] = {}
    for squared_radius, kernel in ring_masks.items():
        filtered = cv2.filter2D(
            half,
            ddepth=-1,
            kernel=kernel,
            borderType=cv2.BORDER_REFLECT_101,
        )
        reconstructed = cv2.resize(
            filtered,
            (width, height),
            interpolation=cv2.INTER_LINEAR,
        )
        sampled = reconstructed[region].reshape(-1, 3).astype(np.float64)
        result[ring_feature_name(squared_radius)] = from_working_space(
            sampled,
            source_space,
        )

    for grid_name, factor, sigmas in (
        ("quarter", 4, QUARTER_SIGMAS[:4]),
        ("eighth", 8, EIGHTH_SIGMAS),
    ):
        reduced = cv2.resize(
            working,
            (width // factor, height // factor),
            interpolation=interpolation,
        )
        for sigma in sigmas:
            filtered = (
                reduced
                if sigma == 0.0
                else cv2.GaussianBlur(
                    reduced,
                    (0, 0),
                    sigmaX=sigma,
                    sigmaY=sigma,
                    borderType=cv2.BORDER_REFLECT_101,
                )
            )
            reconstructed = cv2.resize(
                filtered,
                (width, height),
                interpolation=cv2.INTER_LINEAR,
            )
            sampled = reconstructed[region].reshape(-1, 3).astype(np.float64)
            result[feature_name(grid_name, sigma)] = from_working_space(
                sampled,
                source_space,
            )
    return result


def load_probe_features(
    captures: CaptureSet,
    backgrounds: dict[str, str],
    *,
    source_space: str,
    interpolation: int,
    stride: int,
) -> dict[str, ProbeFeatures]:
    result: dict[str, ProbeFeatures] = {}
    center_x, center_y, radius = scene_circle_geometry(captures)
    for name, background in backgrounds.items():
        source = captures.reference_image(background)
        region = sample_region(source.shape[:2], stride=stride)
        result[name] = ProbeFeatures(
            name=name,
            background=background,
            features=pipeline_feature_bank(
                source,
                source_space=source_space,
                interpolation=interpolation,
                stride=stride,
            ),
            output=captures.image(
                background,
                SCENE,
                MATERIAL,
                APPEARANCE,
            )[region].reshape(-1, 3),
            radius_fraction=sampled_radius_fraction(
                source.shape[:2],
                center_x=center_x,
                center_y=center_y,
                radius=radius,
                stride=stride,
            ),
        )
    return result


def load_hybrid_probe_features(
    captures: CaptureSet,
    backgrounds: dict[str, str],
    *,
    source_space: str,
    interpolation: int,
    stride: int,
    ring_masks: dict[int, FloatArray],
) -> dict[str, ProbeFeatures]:
    result: dict[str, ProbeFeatures] = {}
    center_x, center_y, radius = scene_circle_geometry(captures)
    for name, background in backgrounds.items():
        source = captures.reference_image(background)
        region = sample_region(source.shape[:2], stride=stride)
        result[name] = ProbeFeatures(
            name=name,
            background=background,
            features=hybrid_feature_bank(
                source,
                source_space=source_space,
                interpolation=interpolation,
                stride=stride,
                ring_masks=ring_masks,
            ),
            output=captures.image(
                background,
                SCENE,
                MATERIAL,
                APPEARANCE,
            )[region].reshape(-1, 3),
            radius_fraction=sampled_radius_fraction(
                source.shape[:2],
                center_x=center_x,
                center_y=center_y,
                radius=radius,
                stride=stride,
            ),
        )
    return result


def model_design(
    probe: ProbeFeatures,
    *,
    feature_names: tuple[str, ...],
    degree: int,
) -> FloatArray:
    return per_scale_polynomial_design(
        [probe.features[name] for name in feature_names],
        degree=degree,
    )


def cross_validate_candidate(
    groups: dict[str, ProbeFeatures],
    *,
    feature_names: tuple[str, ...],
    degree: int,
    penalty: float,
) -> JsonObject:
    designs = {
        name: model_design(
            probe,
            feature_names=feature_names,
            degree=degree,
        )
        for name, probe in groups.items()
    }
    moments = {
        name: (design.T @ design, design.T @ groups[name].output)
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
        actual = groups[name].output
        predicted = np.clip(design @ coefficients, 0.0, 255.0)
        actual_parts.append(actual)
        predicted_parts.append(predicted)
        probe_errors[name] = error_summary(actual, predicted)
    actual = np.vstack(actual_parts)
    predicted = np.vstack(predicted_parts)
    return {
        "pooled": prediction_summary(actual, predicted),
        "worstProbeMeanAbsoluteCodes": max(
            float(record["meanAbsoluteCodes"]) for record in probe_errors.values()
        ),
        "probeErrors": probe_errors,
    }


def fit_coefficients(
    groups: dict[str, ProbeFeatures],
    *,
    feature_names: tuple[str, ...],
    degree: int,
    penalty: float,
) -> FloatArray:
    terms_per_feature = len(polynomial_exponents(degree)) - 1
    term_count = 1 + terms_per_feature * len(feature_names)
    gram = np.zeros((term_count, term_count), dtype=np.float64)
    right_hand_side = np.zeros((term_count, 3), dtype=np.float64)
    for probe in groups.values():
        design = model_design(
            probe,
            feature_names=feature_names,
            degree=degree,
        )
        gram += design.T @ design
        right_hand_side += design.T @ probe.output
    return ridge_solve(gram, right_hand_side, penalty=penalty)


def evaluate_groups(
    groups: dict[str, ProbeFeatures],
    *,
    feature_names: tuple[str, ...],
    degree: int,
    coefficients: FloatArray,
) -> JsonObject:
    actual_parts: list[FloatArray] = []
    predicted_parts: list[FloatArray] = []
    probes: JsonObject = {}
    for name, probe in groups.items():
        actual = probe.output
        predicted = np.clip(
            model_design(
                probe,
                feature_names=feature_names,
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


def radial_model_design(
    probe: ProbeFeatures,
    *,
    feature_names: tuple[str, ...],
    radial_degree: int,
) -> FloatArray:
    if radial_degree < 0:
        raise ValueError("radial degree must be nonnegative")
    columns = [np.ones(probe.output.shape[0], dtype=np.float64)]
    for power in range(radial_degree + 1):
        radial_term = np.power(probe.radius_fraction, power)
        for name in feature_names:
            centered = (probe.features[name] - 128.0) / 64.0
            columns.extend(
                centered[:, channel] * radial_term for channel in range(3)
            )
    return np.column_stack(columns)


def cross_validate_radial_candidate(
    groups: dict[str, ProbeFeatures],
    *,
    feature_names: tuple[str, ...],
    radial_degree: int,
    penalty: float,
) -> JsonObject:
    designs = {
        name: radial_model_design(
            probe,
            feature_names=feature_names,
            radial_degree=radial_degree,
        )
        for name, probe in groups.items()
    }
    moments = {
        name: (design.T @ design, design.T @ groups[name].output)
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
        actual = groups[name].output
        predicted = np.clip(design @ coefficients, 0.0, 255.0)
        actual_parts.append(actual)
        predicted_parts.append(predicted)
        probe_errors[name] = error_summary(actual, predicted)
    actual = np.vstack(actual_parts)
    predicted = np.vstack(predicted_parts)
    return {
        "pooled": prediction_summary(actual, predicted),
        "worstProbeMeanAbsoluteCodes": max(
            float(record["meanAbsoluteCodes"]) for record in probe_errors.values()
        ),
        "probeErrors": probe_errors,
    }


def fit_radial_coefficients(
    groups: dict[str, ProbeFeatures],
    *,
    feature_names: tuple[str, ...],
    radial_degree: int,
    penalty: float,
) -> FloatArray:
    term_count = 1 + 3 * len(feature_names) * (radial_degree + 1)
    gram = np.zeros((term_count, term_count), dtype=np.float64)
    right_hand_side = np.zeros((term_count, 3), dtype=np.float64)
    for probe in groups.values():
        design = radial_model_design(
            probe,
            feature_names=feature_names,
            radial_degree=radial_degree,
        )
        gram += design.T @ design
        right_hand_side += design.T @ probe.output
    return ridge_solve(gram, right_hand_side, penalty=penalty)


def evaluate_radial_groups(
    groups: dict[str, ProbeFeatures],
    *,
    feature_names: tuple[str, ...],
    radial_degree: int,
    coefficients: FloatArray,
) -> JsonObject:
    actual_parts: list[FloatArray] = []
    predicted_parts: list[FloatArray] = []
    probes: JsonObject = {}
    for name, probe in groups.items():
        actual = probe.output
        predicted = np.clip(
            radial_model_design(
                probe,
                feature_names=feature_names,
                radial_degree=radial_degree,
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


def appearance_identity_report(captures: CaptureSet) -> JsonObject:
    records = [
        record
        for record in captures.manifest.get("captures", [])
        if record.get("scene") == SCENE and record.get("overlay") == MATERIAL
    ]
    by_background = {
        (
            str(record["background"]),
            str(record["appearance"]),
        ): str(record["pixelSha256"])
        for record in records
    }
    backgrounds = sorted({background for background, _ in by_background})
    differing = [
        background
        for background in backgrounds
        if by_background.get((background, "dark"))
        != by_background.get((background, "light"))
    ]
    return {
        "backgrounds": len(backgrounds),
        "capturePairs": len(backgrounds),
        "exactDecodedPixelHashPairs": len(backgrounds) - len(differing),
        "differingBackgrounds": differing,
    }


def amplitude_linearity_report(captures: CaptureSet) -> JsonObject:
    base = captures.image("gray-128", SCENE, MATERIAL, APPEARANCE)
    region = sample_region(base.shape[:2], stride=1)
    base = base[region]
    result: JsonObject = {}
    for channel in CHANNEL_KINDS:
        for role in ("train", "holdout"):
            low = captures.image(
                f"noise-{channel}-a016-{role}",
                SCENE,
                MATERIAL,
                APPEARANCE,
            )[region]
            high = captures.image(
                f"noise-{channel}-a064-{role}",
                SCENE,
                MATERIAL,
                APPEARANCE,
            )[region]
            predicted = base + (high - base) / 4.0
            equation_residual = 4.0 * (low - base) - (high - base)
            result[f"{channel}/{role}"] = {
                "continuous": error_summary(low, predicted),
                "exactIntegerEquationChannelFraction": float(
                    np.mean(equation_residual == 0.0)
                ),
                "maximumIntegerEquationResidualCodes": float(
                    np.max(np.abs(equation_residual), initial=0.0)
                ),
            }
    return {
        "equation": "O(a=16) = O(a=0) + (O(a=64) - O(a=0)) / 4",
        "baseBackground": "gray-128",
        "records": result,
    }


def adjacent_phase_report(captures: CaptureSet) -> JsonObject:
    output = captures.image(
        "noise-rgb-a064-holdout",
        SCENE,
        MATERIAL,
        APPEARANCE,
    )
    margin = BOUNDARY_EXCLUSION_PIXELS
    central = output[margin:-margin, margin:-margin]
    result: JsonObject = {}
    for period in (2, 4):
        horizontal = np.mean(np.abs(np.diff(central, axis=1)), axis=2)
        vertical = np.mean(np.abs(np.diff(central, axis=0)), axis=2)
        x_coordinates = np.arange(margin, output.shape[1] - margin - 1)
        y_coordinates = np.arange(margin, output.shape[0] - margin - 1)
        result[str(period)] = {
            "horizontalMeanAbsoluteCodesByLeftPixelPhase": [
                float(horizontal[:, x_coordinates % period == phase].mean())
                for phase in range(period)
            ],
            "verticalMeanAbsoluteCodesByTopPixelPhase": [
                float(vertical[y_coordinates % period == phase].mean())
                for phase in range(period)
            ],
        }
    return {
        "background": "noise-rgb-a064-holdout",
        "boundaryExclusionPixels": margin,
        "records": result,
    }


def radial_response_report(captures: CaptureSet) -> JsonObject:
    background = "noise-rgb-a064-holdout"
    source = captures.reference_image(background).astype(np.float32)
    output = captures.image(
        background,
        SCENE,
        MATERIAL,
        APPEARANCE,
    )
    height, width = source.shape[:2]
    half = cv2.resize(
        source,
        (width // 2, height // 2),
        interpolation=cv2.INTER_AREA,
    )
    half_reconstruction = cv2.resize(
        half,
        (width, height),
        interpolation=cv2.INTER_LINEAR,
    ).astype(np.float64)
    center_x, center_y, radius = scene_circle_geometry(captures)
    grid_y, grid_x = np.indices((height, width), dtype=np.float64)
    distance = np.hypot(grid_x - center_x, grid_y - center_y)
    records: list[JsonObject] = []
    for lower in range(0, 900, 100):
        upper = lower + 100
        selected = (distance >= lower) & (distance < upper)
        actual = output[selected]
        predictor = half_reconstruction[selected]
        slopes: list[float] = []
        correlations: list[float] = []
        for channel in range(3):
            x = predictor[:, channel] - predictor[:, channel].mean()
            y = actual[:, channel] - actual[:, channel].mean()
            denominator = float(np.dot(x, x))
            slopes.append(float(np.dot(x, y) / denominator))
            correlations.append(float(np.corrcoef(x, y)[0, 1]))
        records.append(
            {
                "minimumRadiusPixels": lower,
                "maximumRadiusPixels": upper,
                "minimumRadiusFraction": lower / radius,
                "maximumRadiusFraction": upper / radius,
                "pixelCount": int(np.count_nonzero(selected)),
                "outputMeanCodes": actual.mean(axis=0).tolist(),
                "outputStandardDeviationCodes": actual.std(axis=0).tolist(),
                "halfGridRegressionSlopeByChannel": slopes,
                "halfGridCorrelationByChannel": correlations,
            }
        )
    return {
        "background": background,
        "predictor": (
            "2x area reduction followed by half-pixel linear reconstruction"
        ),
        "records": records,
    }


def bilinear_projection_report(captures: CaptureSet) -> JsonObject:
    region = (
        slice(BOUNDARY_EXCLUSION_PIXELS, -BOUNDARY_EXCLUSION_PIXELS),
        slice(BOUNDARY_EXCLUSION_PIXELS, -BOUNDARY_EXCLUSION_PIXELS),
    )
    records: JsonObject = {}
    for role in ("train", "holdout"):
        actual = captures.image(
            f"noise-rgb-a064-{role}",
            SCENE,
            MATERIAL,
            APPEARANCE,
        )
        role_records: JsonObject = {}
        for factor in (2, 4):
            low_resolution, reconstructed = bilinear_subspace_projection(
                actual,
                factor=factor,
            )
            role_records[str(factor)] = {
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
        records[role] = role_records
    return {
        "interpolation": "linear",
        "coordinateConvention": "half-pixel",
        "fit": "unconstrained least-squares low-resolution image",
        "boundaryExclusionPixels": BOUNDARY_EXCLUSION_PIXELS,
        "interpretation": (
            "This isolates output-grid structure. It is not a source-to-output "
            "model and cannot be counted as renderer parity."
        ),
        "records": records,
    }


def model_report(captures: CaptureSet) -> JsonObject:
    training_backgrounds = probe_backgrounds("train")
    holdout_backgrounds = probe_backgrounds("holdout")
    training_sets: dict[
        tuple[str, str],
        dict[str, ProbeFeatures],
    ] = {}
    candidates: list[JsonObject] = []
    for source_space in SOURCE_SPACES:
        for interpolation_name, interpolation in INTERPOLATIONS.items():
            groups = load_probe_features(
                captures,
                training_backgrounds,
                source_space=source_space,
                interpolation=interpolation,
                stride=TRAINING_STRIDE,
            )
            training_sets[(source_space, interpolation_name)] = groups
            for model_name, feature_names in FEATURE_MODELS.items():
                for penalty in RIDGE_PENALTIES:
                    validation = cross_validate_candidate(
                        groups,
                        feature_names=feature_names,
                        degree=1,
                        penalty=penalty,
                    )
                    candidates.append(
                        {
                            "sourceSpace": source_space,
                            "downsampleInterpolation": interpolation_name,
                            "featureModel": model_name,
                            "features": list(feature_names),
                            "degreePerFeature": 1,
                            "terms": 1 + 3 * len(feature_names),
                            "ridgePenalty": penalty,
                            **validation,
                        }
                    )

    ranked = sorted(
        candidates,
        key=lambda candidate: (
            float(
                candidate["pooled"]["continuous"]["meanAbsoluteCodes"]
            ),
            float(candidate["worstProbeMeanAbsoluteCodes"]),
            int(candidate["terms"]),
            float(candidate["ridgePenalty"]),
        ),
    )
    selected_linear = ranked[0]
    selected_key = (
        str(selected_linear["sourceSpace"]),
        str(selected_linear["downsampleInterpolation"]),
    )
    selected_features = tuple(str(name) for name in selected_linear["features"])
    training = training_sets[selected_key]

    degree_candidates: list[JsonObject] = []
    for degree in (1, 2, 3):
        terms_per_feature = len(polynomial_exponents(degree)) - 1
        for penalty in RIDGE_PENALTIES:
            validation = cross_validate_candidate(
                training,
                feature_names=selected_features,
                degree=degree,
                penalty=penalty,
            )
            degree_candidates.append(
                {
                    "degreePerFeature": degree,
                    "terms": 1 + terms_per_feature * len(selected_features),
                    "ridgePenalty": penalty,
                    **validation,
                }
            )
    degree_ranked = sorted(
        degree_candidates,
        key=lambda candidate: (
            float(
                candidate["pooled"]["continuous"]["meanAbsoluteCodes"]
            ),
            float(candidate["worstProbeMeanAbsoluteCodes"]),
            int(candidate["terms"]),
            float(candidate["ridgePenalty"]),
        ),
    )
    selected_degree = degree_ranked[0]
    degree = int(selected_degree["degreePerFeature"])
    penalty = float(selected_degree["ridgePenalty"])
    coefficients = fit_coefficients(
        training,
        feature_names=selected_features,
        degree=degree,
        penalty=penalty,
    )

    holdout = load_probe_features(
        captures,
        holdout_backgrounds,
        source_space=selected_key[0],
        interpolation=INTERPOLATIONS[selected_key[1]],
        stride=HOLDOUT_STRIDE,
    )
    holdout_report = evaluate_groups(
        holdout,
        feature_names=selected_features,
        degree=degree,
        coefficients=coefficients,
    )
    block_holdout_names = [
        name for name in holdout if name.startswith("block-")
    ]
    block_holdout_report = evaluate_groups(
        {name: holdout[name] for name in block_holdout_names},
        feature_names=selected_features,
        degree=degree,
        coefficients=coefficients,
    )
    return {
        "selectionRule": (
            "Select source space, reduction filter, and fixed structural "
            "feature family by leave-one-training-probe-out continuous MAE, "
            "then worst probe MAE, term count, and ridge penalty. Audit "
            "polynomial degree with the same training-only rule. Open "
            "independently seeded holdouts only after coefficients are fixed."
        ),
        "trainingBackgrounds": list(training_backgrounds.values()),
        "holdoutBackgrounds": list(holdout_backgrounds.values()),
        "linearCandidateCount": len(candidates),
        "selectedLinearStructure": selected_linear,
        "bestLinearCandidates": ranked[:24],
        "degreeAudit": {
            "candidateCount": len(degree_candidates),
            "selected": selected_degree,
            "candidates": degree_ranked,
        },
        "selected": {
            "sourceSpace": selected_key[0],
            "downsampleInterpolation": selected_key[1],
            "featureModel": str(selected_linear["featureModel"]),
            "features": list(selected_features),
            "degreePerFeature": degree,
            "terms": int(selected_degree["terms"]),
            "ridgePenalty": penalty,
            "coefficients": coefficients.tolist(),
        },
        "holdouts": holdout_report,
        "previouslyUnopenedBlockMeanHoldouts": block_holdout_report,
    }


def radial_structure_report(
    captures: CaptureSet,
    *,
    source_space: str,
    interpolation_name: str,
    feature_names: tuple[str, ...],
) -> JsonObject:
    training = load_probe_features(
        captures,
        probe_backgrounds("train"),
        source_space=source_space,
        interpolation=INTERPOLATIONS[interpolation_name],
        stride=TRAINING_STRIDE,
    )
    candidates: list[JsonObject] = []
    for radial_degree in range(5):
        for penalty in RIDGE_PENALTIES:
            validation = cross_validate_radial_candidate(
                training,
                feature_names=feature_names,
                radial_degree=radial_degree,
                penalty=penalty,
            )
            candidates.append(
                {
                    "radialDegree": radial_degree,
                    "radialBasis": [
                        f"(distance / circleRadius)^{power}"
                        for power in range(radial_degree + 1)
                    ],
                    "terms": 1
                    + 3 * len(feature_names) * (radial_degree + 1),
                    "ridgePenalty": penalty,
                    **validation,
                }
            )
    ranked = sorted(
        candidates,
        key=lambda candidate: (
            float(
                candidate["pooled"]["continuous"]["meanAbsoluteCodes"]
            ),
            float(candidate["worstProbeMeanAbsoluteCodes"]),
            int(candidate["terms"]),
            float(candidate["ridgePenalty"]),
        ),
    )
    selected = ranked[0]
    radial_degree = int(selected["radialDegree"])
    penalty = float(selected["ridgePenalty"])
    coefficients = fit_radial_coefficients(
        training,
        feature_names=feature_names,
        radial_degree=radial_degree,
        penalty=penalty,
    )
    holdout = load_probe_features(
        captures,
        probe_backgrounds("holdout"),
        source_space=source_space,
        interpolation=INTERPOLATIONS[interpolation_name],
        stride=HOLDOUT_STRIDE,
    )
    holdout_report = evaluate_radial_groups(
        holdout,
        feature_names=feature_names,
        radial_degree=radial_degree,
        coefficients=coefficients,
    )
    block_holdouts = {
        name: probe
        for name, probe in holdout.items()
        if name.startswith("block-")
    }
    return {
        "selectionRule": (
            "With the source pipeline and multiresolution feature family "
            "already frozen by the preceding training-only search, select "
            "radial polynomial degree and ridge penalty only by "
            "leave-one-training-probe-out error."
        ),
        "candidateCount": len(candidates),
        "selected": {
            **selected,
            "coefficients": coefficients.tolist(),
        },
        "candidates": ranked,
        "holdouts": holdout_report,
        "previouslyUnopenedBlockMeanHoldouts": evaluate_radial_groups(
            block_holdouts,
            feature_names=feature_names,
            radial_degree=radial_degree,
            coefficients=coefficients,
        ),
    }


def hybrid_kernel_report(
    captures: CaptureSet,
    *,
    source_space: str,
    interpolation_name: str,
    radial_degree: int,
) -> JsonObject:
    maximum_radius = 6
    ring_masks = symmetric_ring_masks(maximum_radius)
    training = load_hybrid_probe_features(
        captures,
        probe_backgrounds("train"),
        source_space=source_space,
        interpolation=INTERPOLATIONS[interpolation_name],
        stride=TRAINING_STRIDE,
        ring_masks=ring_masks,
    )
    candidates: list[JsonObject] = []
    for radius, support_shape in (
        (4, "disk"),
        (5, "disk"),
        (6, "disk"),
        (6, "square"),
    ):
        feature_names = hybrid_feature_names(
            radius,
            ring_masks=ring_masks,
            support_shape=support_shape,
        )
        for penalty in RIDGE_PENALTIES:
            validation = cross_validate_radial_candidate(
                training,
                feature_names=feature_names,
                radial_degree=radial_degree,
                penalty=penalty,
            )
            candidates.append(
                {
                    "halfGridKernelRadiusPixels": radius,
                    "halfGridKernelDiameterPixels": radius * 2 + 1,
                    "equivalentFullResolutionRadiusPixels": radius * 2,
                    "halfGridKernelSupportShape": support_shape,
                    "radialDegree": radial_degree,
                    "features": list(feature_names),
                    "terms": 1
                    + 3 * len(feature_names) * (radial_degree + 1),
                    "ridgePenalty": penalty,
                    **validation,
                }
            )
    ranked = sorted(
        candidates,
        key=lambda candidate: (
            float(
                candidate["pooled"]["continuous"]["meanAbsoluteCodes"]
            ),
            float(candidate["worstProbeMeanAbsoluteCodes"]),
            int(candidate["terms"]),
            float(candidate["ridgePenalty"]),
        ),
    )
    selected = ranked[0]
    feature_names = tuple(str(name) for name in selected["features"])
    penalty = float(selected["ridgePenalty"])
    coefficients = fit_radial_coefficients(
        training,
        feature_names=feature_names,
        radial_degree=radial_degree,
        penalty=penalty,
    )
    holdout = load_hybrid_probe_features(
        captures,
        probe_backgrounds("holdout"),
        source_space=source_space,
        interpolation=INTERPOLATIONS[interpolation_name],
        stride=HOLDOUT_STRIDE,
        ring_masks=ring_masks,
    )
    holdout_report = evaluate_radial_groups(
        holdout,
        feature_names=feature_names,
        radial_degree=radial_degree,
        coefficients=coefficients,
    )
    block_holdouts = {
        name: probe
        for name, probe in holdout.items()
        if name.startswith("block-")
    }
    return {
        "selectionRule": (
            "Freeze the selected source pipeline and radial degree, then "
            "select only half-grid kernel support and ridge penalty by "
            "leave-one-training-probe-out error. Holdouts are opened after "
            "the coefficients are fixed."
        ),
        "kernelSymmetry": (
            "Each half-grid tap is shared by all offsets with the same "
            "squared radius; quarter/eighth features retain their measured "
            "absolute grid phases."
        ),
        "candidateCount": len(candidates),
        "selected": {
            **selected,
            "coefficients": coefficients.tolist(),
        },
        "candidates": ranked,
        "holdouts": holdout_report,
        "previouslyUnopenedBlockMeanHoldouts": evaluate_radial_groups(
            block_holdouts,
            feature_names=feature_names,
            radial_degree=radial_degree,
            coefficients=coefficients,
        ),
    }


def fit_report(captures: CaptureSet) -> JsonObject:
    fitted_model = model_report(captures)
    selected = fitted_model["selected"]
    source_space = str(selected["sourceSpace"])
    interpolation_name = str(selected["downsampleInterpolation"])
    feature_names = tuple(str(name) for name in selected["features"])
    radial_model = radial_structure_report(
        captures,
        source_space=source_space,
        interpolation_name=interpolation_name,
        feature_names=feature_names,
    )
    radial_degree = int(radial_model["selected"]["radialDegree"])
    hybrid_model = hybrid_kernel_report(
        captures,
        source_space=source_space,
        interpolation_name=interpolation_name,
        radial_degree=radial_degree,
    )
    return {
        "clearGridFitSchemaVersion": 2,
        "analysisImplementation": {
            "file": "analysis/liquid_glass_clear_grid_fit.py",
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
            "appearanceUsedForFit": APPEARANCE,
        },
        "policy": {
            "productionShaderModified": False,
            "purpose": (
                "identify output grids and reject or retain compact "
                "source-to-output hypotheses; not a parity claim"
            ),
            "qualityGate": (
                "zero unequal decoded channels on protected Apple captures"
            ),
            "developmentDisclosure": (
                "The pixel-scale RGB +/-64 holdout was inspected while "
                "developing the structural hypotheses. The independent "
                "block-mean holdouts were not used for candidate selection "
                "or coefficient fitting and are reported separately."
            ),
        },
        "appearanceIdentity": appearance_identity_report(captures),
        "amplitudeLinearity": amplitude_linearity_report(captures),
        "adjacentPhase": adjacent_phase_report(captures),
        "radialResponse": radial_response_report(captures),
        "bilinearSubspaceProjection": bilinear_projection_report(captures),
        "modelFit": fitted_model,
        "radiallyModulatedModelFit": radial_model,
        "radialKernelHybridFit": hybrid_model,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fit clear Liquid Glass on its measured half-grid.",
    )
    parser.add_argument("captures", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    captures = CaptureSet.open(args.captures)
    try:
        if captures.manifest.get("rigVersion") != "2.12.0":
            raise ValueError("clear-grid fit requires rig 2.12.0")
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
