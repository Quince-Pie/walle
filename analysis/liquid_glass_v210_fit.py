#!/usr/bin/env python3
"""Cross-validate context-aware models on the Liquid Glass v2.10 probes."""

import argparse
import hashlib
import json
import math
import platform
from importlib.metadata import version as package_version
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from liquid_glass_spatial_fit import (
    APPEARANCES,
    CaptureSet,
    SpatialModel,
    chart_filter_inputs,
    error_summary,
    filter_image_periodic,
    from_working_space,
    per_scale_polynomial_design,
    polynomial_exponents,
    to_working_space,
    values_in_chart_order,
)


type JsonObject = dict[str, Any]
type FloatArray = NDArray[np.float64]

CHART_SIGMAS = (0.25, 2.0, 4.0, 8.0, 16.0, 32.0, 64.0, 128.0, 256.0, 512.0)
SOURCE_SPACES = ("srgb-code", "linear-srgb")
RIDGE_PENALTIES = (1e-6, 1e-4, 1e-2, 1.0, 100.0)
LOCAL_RIDGE_PENALTIES = (1e-6, 1e-3, 0.1, 10.0, 1000.0)
GAUSSIAN_BANK_SIGMAS = (
    1.0,
    2.0,
    4.0,
    6.0,
    8.0,
    12.0,
    16.0,
    24.0,
    32.0,
    48.0,
    64.0,
    96.0,
    128.0,
)
GAUSSIAN_BANK_SCALE_COUNTS = (3, 5, 7, 9, 11, 13)
RAW_INCLUSIVE_BANK_SIGMAS = (
    0.0,
    0.25,
    0.5,
    0.75,
    1.0,
    1.5,
    2.0,
    3.0,
    4.0,
    6.0,
    8.0,
    12.0,
    16.0,
    24.0,
    32.0,
    48.0,
    64.0,
    96.0,
    128.0,
    192.0,
    256.0,
)
RAW_INCLUSIVE_BANK_RIDGE_PENALTIES = (1e-3, 0.1, 10.0)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def spatial_model_from_json(record: JsonObject) -> SpatialModel:
    components = record["components"]
    return SpatialModel(
        pipeline=str(record["pipeline"]),
        source_space=str(record["sourceSpace"]),
        sigmas=np.asarray(
            [component["sigmaPixels"] for component in components],
            dtype=np.float64,
        ),
        weights=np.asarray(
            [component["weight"] for component in components],
            dtype=np.float64,
        ),
        shift_pixels=float(record["shiftPixels"]),
    )


def frequency_models(spatial_report: JsonObject) -> dict[str, SpatialModel]:
    records = spatial_report["validation"]["frequencyTrainedKernel"]
    return {
        appearance: spatial_model_from_json(records[appearance]["selected"]["model"])
        for appearance in APPEARANCES
    }


def chart_catalog(measurements: JsonObject) -> tuple[dict[str, JsonObject], list[str]]:
    on_grid_training = {
        "onGridOrdered": measurements["denseColorTransfer"],
        "onGridAffine": measurements["denseColorContextRepeat"],
    }
    on_grid_random = measurements["denseColorContextTraining"]["charts"]
    for index, chart in enumerate(on_grid_random.values()):
        on_grid_training[f"onGridRandom{index:02d}"] = chart

    off_grid_random = measurements["denseColorHoldoutContextTraining"]["charts"]
    charts = {
        **on_grid_training,
        **{
            f"offGridRandom{index:02d}": chart
            for index, chart in enumerate(off_grid_random.values())
        },
        "finalOnGridShuffle": measurements["denseColorContextHoldout"],
        "finalOffGridOrdered": measurements["denseColorHoldout"],
        "finalOffGridShuffle": measurements["denseColorHoldoutContextRepeat"],
    }
    training_names = [
        *on_grid_training,
        *(f"offGridRandom{index:02d}" for index in range(4)),
    ]
    for name, chart in charts.items():
        if not isinstance(chart, dict) or chart.get("available") is not True:
            raise ValueError(f"required v2.10 chart is unavailable: {name}")
    return charts, training_names


def ordered_output(
    reference: JsonObject,
    candidate: JsonObject,
    *,
    combination: str,
) -> FloatArray:
    candidate_values = np.asarray(
        candidate[combination]["outputCodes"],
        dtype=np.float64,
    )
    return values_in_chart_order(reference, candidate, candidate_values)


def context_delta_report(
    charts: dict[str, JsonObject],
    *,
    combination: str,
) -> JsonObject:
    references = {
        "onGrid": charts["onGridOrdered"],
        "offGrid": charts["finalOffGridOrdered"],
    }
    result: JsonObject = {}
    for name, chart in charts.items():
        family = "offGrid" if name.startswith(("offGrid", "finalOffGrid")) else "onGrid"
        reference = references[family]
        if chart is reference:
            continue
        reference_output = np.asarray(
            reference[combination]["outputCodes"],
            dtype=np.float64,
        )
        candidate_output = ordered_output(
            reference,
            chart,
            combination=combination,
        )
        result[name] = error_summary(reference_output, candidate_output)
    return result


def ridge_solve(
    gram: FloatArray,
    right_hand_side: FloatArray,
    *,
    penalty: float,
) -> FloatArray:
    regularizer = np.eye(gram.shape[0], dtype=np.float64) * penalty
    regularizer[0, 0] = 0.0
    return np.linalg.solve(gram + regularizer, right_hand_side)


def clipped_prediction(design: FloatArray, coefficients: FloatArray) -> FloatArray:
    return np.clip(design @ coefficients, 0.0, 255.0)


def layout_cross_validation(
    designs: dict[str, FloatArray],
    outputs: dict[str, FloatArray],
    training_names: list[str],
    *,
    penalty: float,
) -> JsonObject:
    moments = {
        name: (designs[name].T @ designs[name], designs[name].T @ outputs[name])
        for name in training_names
    }
    total_gram = sum((value[0] for value in moments.values()), start=0)
    total_rhs = sum((value[1] for value in moments.values()), start=0)
    actual_parts: list[FloatArray] = []
    predicted_parts: list[FloatArray] = []
    layouts: JsonObject = {}
    for name in training_names:
        gram, rhs = moments[name]
        coefficients = ridge_solve(
            total_gram - gram,
            total_rhs - rhs,
            penalty=penalty,
        )
        prediction = clipped_prediction(designs[name], coefficients)
        actual_parts.append(outputs[name])
        predicted_parts.append(prediction)
        layouts[name] = error_summary(outputs[name], prediction)
    actual = np.vstack(actual_parts)
    predicted = np.vstack(predicted_parts)
    return {
        "pooledError": error_summary(actual, predicted),
        "worstLayoutMeanAbsoluteCodes": max(
            float(record["meanAbsoluteCodes"]) for record in layouts.values()
        ),
        "layoutErrors": layouts,
    }


def chart_model_validation(
    captures: CaptureSet,
    measurements: JsonObject,
) -> JsonObject:
    charts, training_names = chart_catalog(measurements)
    width_points, height_points = captures.manifest["windowPoints"]
    scale = float(captures.manifest["backingScaleFactor"])
    width = round(float(width_points) * scale)
    height = round(float(height_points) * scale)
    result: JsonObject = {}

    for appearance in APPEARANCES:
        outputs = {
            name: np.asarray(
                chart[f"{appearance}/regular"]["outputCodes"],
                dtype=np.float64,
            )
            for name, chart in charts.items()
        }
        designs: dict[tuple[str, int, str], FloatArray] = {}
        for source_space in SOURCE_SPACES:
            scale_inputs: dict[str, list[FloatArray]] = {name: [] for name in charts}
            for sigma in CHART_SIGMAS:
                model = SpatialModel(
                    pipeline="tone-after-spatial-filter",
                    source_space=source_space,
                    sigmas=np.asarray([sigma], dtype=np.float64),
                    weights=np.ones(1, dtype=np.float64),
                    shift_pixels=0.0,
                )
                for name, chart in charts.items():
                    scale_inputs[name].append(
                        chart_filter_inputs(
                            chart,
                            model,
                            appearance,
                            width=width,
                            height=height,
                        )
                    )
            for degree in (1, 2, 3):
                for name, values in scale_inputs.items():
                    designs[(source_space, degree, name)] = per_scale_polynomial_design(
                        values, degree=degree
                    )

        candidates: list[JsonObject] = []
        for source_space in SOURCE_SPACES:
            for degree in (1, 2, 3):
                candidate_designs = {
                    name: designs[(source_space, degree, name)] for name in charts
                }
                for penalty in RIDGE_PENALTIES:
                    validation = layout_cross_validation(
                        candidate_designs,
                        outputs,
                        training_names,
                        penalty=penalty,
                    )
                    candidates.append(
                        {
                            "sourceSpace": source_space,
                            "degreePerScale": degree,
                            "terms": int(candidate_designs[training_names[0]].shape[1]),
                            "ridgePenalty": penalty,
                            **validation,
                        }
                    )
        selected = min(
            candidates,
            key=lambda candidate: (
                float(candidate["pooledError"]["meanAbsoluteCodes"]),
                float(candidate["worstLayoutMeanAbsoluteCodes"]),
                int(candidate["terms"]),
                float(candidate["ridgePenalty"]),
            ),
        )
        source_space = str(selected["sourceSpace"])
        degree = int(selected["degreePerScale"])
        penalty = float(selected["ridgePenalty"])
        training_design = np.vstack(
            [designs[(source_space, degree, name)] for name in training_names]
        )
        training_output = np.vstack([outputs[name] for name in training_names])
        coefficients = ridge_solve(
            training_design.T @ training_design,
            training_design.T @ training_output,
            penalty=penalty,
        )
        holdout_errors = {}
        for name in (
            "finalOnGridShuffle",
            "finalOffGridOrdered",
            "finalOffGridShuffle",
        ):
            prediction = clipped_prediction(
                designs[(source_space, degree, name)],
                coefficients,
            )
            holdout_errors[name] = error_summary(outputs[name], prediction)

        result[appearance] = {
            "method": (
                "fixed isotropic Gaussian bank with a separate complete RGB "
                "polynomial at each scale; hyperparameters are selected by "
                "leave-one-layout-out validation over six on-grid and four "
                "off-grid training layouts"
            ),
            "trainingLayouts": training_names,
            "scalesPixels": list(CHART_SIGMAS),
            "candidateCount": len(candidates),
            "selected": selected,
            "bestCandidates": sorted(
                candidates,
                key=lambda candidate: (
                    float(candidate["pooledError"]["meanAbsoluteCodes"]),
                    float(candidate["worstLayoutMeanAbsoluteCodes"]),
                ),
            )[:8],
            "finalFitSamples": int(training_design.shape[0]),
            "finalHoldoutErrors": holdout_errors,
            "coefficients": coefficients.tolist(),
        }

    raw_context: JsonObject = {}
    for combination in (
        "dark/clear",
        "light/clear",
        "dark/regular",
        "light/regular",
    ):
        raw_context[combination] = context_delta_report(
            charts,
            combination=combination,
        )
    return {
        "trainingLayoutCount": len(training_names),
        "rawSameColorContextDeltas": raw_context,
        "regularModel": result,
    }


def local_polynomial_design(inputs: FloatArray, degree: int) -> FloatArray:
    normalized = (inputs - 128.0) / 8.0
    exponents = polynomial_exponents(degree)
    return np.column_stack(
        [
            np.prod(
                np.power(
                    normalized,
                    np.asarray(exponent, dtype=np.int64),
                ),
                axis=1,
            )
            for exponent in exponents
        ]
    )


def filtered_rgb(source: FloatArray, model: SpatialModel) -> FloatArray:
    working = to_working_space(source, model.source_space)
    filtered = np.empty_like(working)
    for channel in range(3):
        filtered[:, :, channel] = filter_image_periodic(
            working[:, :, channel],
            model,
        )
    return from_working_space(filtered, model.source_space)


def derived_small_amplitude(source64: FloatArray) -> FloatArray:
    return 128.0 + (source64 - 128.0) / 4.0


def centered_correlations(
    actual: FloatArray, predicted: FloatArray
) -> list[float | None]:
    result: list[float | None] = []
    for channel in range(3):
        actual_centered = actual[:, channel] - actual[:, channel].mean()
        predicted_centered = predicted[:, channel] - predicted[:, channel].mean()
        denominator = math.sqrt(
            float(np.dot(actual_centered, actual_centered))
            * float(np.dot(predicted_centered, predicted_centered))
        )
        result.append(
            float(np.dot(actual_centered, predicted_centered)) / denominator
            if denominator
            else None
        )
    return result


def prediction_report(actual: FloatArray, predicted: FloatArray) -> JsonObject:
    rounded = np.clip(np.rint(predicted), 0.0, 255.0)
    exact_pixels = np.all(rounded == actual, axis=1)
    return {
        "continuousError": error_summary(actual, predicted),
        "roundedError": error_summary(actual, rounded),
        "roundedExactPixelFraction": float(np.mean(exact_pixels)),
        "actualMeanCodes": actual.mean(axis=0).tolist(),
        "predictedMeanCodes": predicted.mean(axis=0).tolist(),
        "actualStandardDeviationCodes": actual.std(axis=0).tolist(),
        "predictedStandardDeviationCodes": predicted.std(axis=0).tolist(),
        "centeredCorrelationByChannel": centered_correlations(actual, predicted),
    }


def predict_local_polynomial(
    inputs: FloatArray,
    coefficients: FloatArray,
    *,
    degree: int,
    chunk_size: int = 100_000,
) -> FloatArray:
    predicted = np.empty_like(inputs)
    for start in range(0, inputs.shape[0], chunk_size):
        stop = min(inputs.shape[0], start + chunk_size)
        design = local_polynomial_design(inputs[start:stop], degree)
        predicted[start:stop] = np.clip(design @ coefficients, 0.0, 255.0)
    return predicted


def local_model_cross_validation(
    groups: dict[str, tuple[FloatArray, FloatArray]],
    *,
    degree: int,
    penalty: float,
) -> JsonObject:
    designs = {
        name: local_polynomial_design(inputs, degree)
        for name, (inputs, _) in groups.items()
    }
    moments = {
        name: (design.T @ design, design.T @ groups[name][1])
        for name, design in designs.items()
    }
    total_gram = sum((value[0] for value in moments.values()), start=0)
    total_rhs = sum((value[1] for value in moments.values()), start=0)
    actual_parts: list[FloatArray] = []
    prediction_parts: list[FloatArray] = []
    group_errors: JsonObject = {}
    for name, (_, actual) in groups.items():
        gram, rhs = moments[name]
        coefficients = ridge_solve(
            total_gram - gram,
            total_rhs - rhs,
            penalty=penalty,
        )
        prediction = np.clip(designs[name] @ coefficients, 0.0, 255.0)
        actual_parts.append(actual)
        prediction_parts.append(prediction)
        group_errors[name] = error_summary(actual, prediction)
    actual = np.vstack(actual_parts)
    prediction = np.vstack(prediction_parts)
    return {
        "pooledError": error_summary(actual, prediction),
        "worstProbeMeanAbsoluteCodes": max(
            float(record["meanAbsoluteCodes"]) for record in group_errors.values()
        ),
        "probeErrors": group_errors,
    }


def stochastic_groups(
    captures: CaptureSet,
    model: SpatialModel,
    appearance: str,
    *,
    role: str,
    sample_stride: int,
) -> tuple[dict[str, tuple[FloatArray, FloatArray]], int, JsonObject]:
    margin = max(512, math.ceil(4.0 * float(model.sigmas.max())))
    groups: dict[str, tuple[FloatArray, FloatArray]] = {}
    source_checks: JsonObject = {}
    for channel_kind in ("gray", "rgb"):
        background64 = f"noise-{channel_kind}-a064-{role}"
        background16 = f"noise-{channel_kind}-a016-{role}"
        source64 = captures.reference_image(background64)
        source16 = captures.reference_image(background16)
        derived16 = derived_small_amplitude(source64)
        source_checks[channel_kind] = {
            "smallAmplitudeDerivedExactly": bool(np.array_equal(source16, derived16)),
            "maximumDerivationErrorCodes": float(
                np.max(np.abs(source16 - derived16), initial=0.0)
            ),
        }
        filtered64 = filtered_rgb(source64, model)
        filtered16 = derived_small_amplitude(filtered64)
        del source64, source16, derived16
        region = (
            slice(margin, filtered64.shape[0] - margin, sample_stride),
            slice(margin, filtered64.shape[1] - margin, sample_stride),
        )
        for amplitude, filtered in ((16, filtered16), (64, filtered64)):
            background = f"noise-{channel_kind}-a{amplitude:03d}-{role}"
            output = captures.image(
                background,
                "circle-4000-center",
                "regular",
                appearance,
            )
            name = f"{channel_kind}-a{amplitude:03d}"
            groups[name] = (
                filtered[region].reshape(-1, 3),
                output[region].reshape(-1, 3),
            )
            del output
        del filtered16, filtered64
    return groups, margin, source_checks


def stochastic_model_validation(
    captures: CaptureSet,
    models: dict[str, SpatialModel],
) -> JsonObject:
    result: JsonObject = {}
    for appearance in APPEARANCES:
        model = models[appearance]
        training_groups, margin, source_checks = stochastic_groups(
            captures,
            model,
            appearance,
            role="train",
            sample_stride=4,
        )
        training_sample_count = sum(
            inputs.shape[0] for inputs, _ in training_groups.values()
        )
        candidates: list[JsonObject] = []
        for degree in range(1, 6):
            terms = len(polynomial_exponents(degree))
            for penalty in LOCAL_RIDGE_PENALTIES:
                validation = local_model_cross_validation(
                    training_groups,
                    degree=degree,
                    penalty=penalty,
                )
                candidates.append(
                    {
                        "degree": degree,
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
        degree = int(selected["degree"])
        penalty = float(selected["ridgePenalty"])
        training_design = np.vstack(
            [
                local_polynomial_design(inputs, degree)
                for inputs, _ in training_groups.values()
            ]
        )
        training_output = np.vstack([output for _, output in training_groups.values()])
        coefficients = ridge_solve(
            training_design.T @ training_design,
            training_design.T @ training_output,
            penalty=penalty,
        )
        constant = training_output.mean(axis=0)
        del training_design, training_output, training_groups

        holdout_groups, holdout_margin, holdout_source_checks = stochastic_groups(
            captures,
            model,
            appearance,
            role="holdout",
            sample_stride=1,
        )
        holdouts: JsonObject = {}
        for name, (inputs, actual) in holdout_groups.items():
            prediction = predict_local_polynomial(
                inputs,
                coefficients,
                degree=degree,
            )
            constant_prediction = np.broadcast_to(constant, actual.shape)
            holdouts[name] = {
                "model": prediction_report(actual, prediction),
                "constantBaseline": prediction_report(
                    actual,
                    constant_prediction,
                ),
            }
        result[appearance] = {
            "method": (
                "filter each RGB source channel with the frozen frequency-trained "
                "kernel, fit a local 3D polynomial only on the four stochastic "
                "training probes, and evaluate every central pixel of all four "
                "independent-seed holdouts"
            ),
            "spatialModel": model.as_json(),
            "boundaryExclusionPixels": margin,
            "holdoutBoundaryExclusionPixels": holdout_margin,
            "trainingSourceChecks": source_checks,
            "holdoutSourceChecks": holdout_source_checks,
            "candidateCount": len(candidates),
            "selected": selected,
            "bestCandidates": sorted(
                candidates,
                key=lambda candidate: (
                    float(candidate["pooledError"]["meanAbsoluteCodes"]),
                    float(candidate["worstProbeMeanAbsoluteCodes"]),
                ),
            )[:8],
            "trainingSamples": training_sample_count,
            "fitSampleStride": 4,
            "coefficients": coefficients.tolist(),
            "holdouts": holdouts,
        }
    return result


def gaussian_bank_features(
    captures: CaptureSet,
    channel_kind: str,
    *,
    role: str,
    stride: int,
    margin: int = 512,
) -> FloatArray:
    source = captures.reference_image(f"noise-{channel_kind}-a064-{role}")
    normalized = (source - 128.0) / 64.0
    height, width = normalized.shape[:2]
    vertical = np.fft.fftfreq(height)[:, np.newaxis]
    horizontal = np.fft.rfftfreq(width)[np.newaxis, :]
    squared_frequency = np.square(vertical) + np.square(horizontal)
    transforms = [np.fft.rfft2(normalized[:, :, channel]) for channel in range(3)]
    region = (
        slice(margin, height - margin, stride),
        slice(margin, width - margin, stride),
    )
    columns: list[FloatArray] = []
    for sigma in GAUSSIAN_BANK_SIGMAS:
        response = np.exp(-2.0 * math.pi**2 * sigma**2 * squared_frequency)
        for transformed in transforms:
            filtered = np.fft.irfft2(
                transformed * response,
                s=(height, width),
            )
            columns.append(filtered[region].reshape(-1))
    return np.column_stack(columns)


def prepare_centered_bank_validation(
    groups: dict[str, tuple[FloatArray, FloatArray]],
    *,
    feature_indexes: NDArray[np.int64],
    feature_scales: FloatArray,
) -> tuple[
    dict[str, tuple[FloatArray, FloatArray]],
    dict[str, tuple[FloatArray, FloatArray, FloatArray]],
]:
    moments: dict[str, tuple[FloatArray, FloatArray]] = {}
    centered: dict[str, tuple[FloatArray, FloatArray, FloatArray]] = {}
    for name, (features, output) in groups.items():
        selected = features[:, feature_indexes]
        feature_mean = selected.mean(axis=0)
        output_mean = output.mean(axis=0)
        design = (selected - feature_mean) / feature_scales
        target = output - output_mean
        centered[name] = (design, target, output_mean)
        moments[name] = (design.T @ design, design.T @ target)
    return moments, centered


def centered_bank_cross_validation(
    groups: dict[str, tuple[FloatArray, FloatArray]],
    moments: dict[str, tuple[FloatArray, FloatArray]],
    centered: dict[str, tuple[FloatArray, FloatArray, FloatArray]],
    *,
    penalty: float,
) -> JsonObject:
    total_gram = sum((value[0] for value in moments.values()), start=0)
    total_rhs = sum((value[1] for value in moments.values()), start=0)
    actual_parts: list[FloatArray] = []
    predicted_parts: list[FloatArray] = []
    group_errors: JsonObject = {}
    for name, (_, output) in groups.items():
        gram, rhs = moments[name]
        regularizer = np.eye(gram.shape[0], dtype=np.float64) * penalty
        coefficients = np.linalg.solve(
            total_gram - gram + regularizer,
            total_rhs - rhs,
        )
        design, _, output_mean = centered[name]
        prediction = np.clip(
            output_mean + design @ coefficients,
            0.0,
            255.0,
        )
        actual_parts.append(output)
        predicted_parts.append(prediction)
        group_errors[name] = error_summary(output, prediction)
    actual = np.vstack(actual_parts)
    predicted = np.vstack(predicted_parts)
    return {
        "pooledSpatialError": error_summary(actual, predicted),
        "worstProbeMeanAbsoluteCodes": max(
            float(record["meanAbsoluteCodes"]) for record in group_errors.values()
        ),
        "probeSpatialErrors": group_errors,
    }


def fit_centered_gaussian_bank(
    groups: dict[str, tuple[FloatArray, FloatArray]],
) -> tuple[JsonObject, FloatArray, dict[str, JsonObject]]:
    feature_count = groups[next(iter(groups))][0].shape[1]
    sum_squares = np.zeros(feature_count, dtype=np.float64)
    sample_count = 0
    for features, _ in groups.values():
        centered = features - features.mean(axis=0)
        sum_squares += np.sum(np.square(centered), axis=0)
        sample_count += features.shape[0]
    scales = np.sqrt(sum_squares / sample_count)
    scales = np.maximum(scales, 1e-12)

    candidates: list[JsonObject] = []
    for scale_count in GAUSSIAN_BANK_SCALE_COUNTS:
        feature_indexes = np.arange(scale_count * 3, dtype=np.int64)
        moments, centered = prepare_centered_bank_validation(
            groups,
            feature_indexes=feature_indexes,
            feature_scales=scales[feature_indexes],
        )
        for penalty in LOCAL_RIDGE_PENALTIES:
            validation = centered_bank_cross_validation(
                groups,
                moments,
                centered,
                penalty=penalty,
            )
            candidates.append(
                {
                    "scaleCount": scale_count,
                    "scalesPixels": list(GAUSSIAN_BANK_SIGMAS[:scale_count]),
                    "terms": int(feature_indexes.size),
                    "ridgePenalty": penalty,
                    **validation,
                }
            )
    selected = min(
        candidates,
        key=lambda candidate: (
            float(candidate["pooledSpatialError"]["meanAbsoluteCodes"]),
            float(candidate["worstProbeMeanAbsoluteCodes"]),
            int(candidate["terms"]),
            float(candidate["ridgePenalty"]),
        ),
    )
    scale_count = int(selected["scaleCount"])
    feature_indexes = np.arange(scale_count * 3, dtype=np.int64)
    selected_scales = scales[feature_indexes]
    total_gram = np.zeros(
        (feature_indexes.size, feature_indexes.size),
        dtype=np.float64,
    )
    total_rhs = np.zeros((feature_indexes.size, 3), dtype=np.float64)
    group_calibration: dict[str, JsonObject] = {}
    for name, (features, output) in groups.items():
        selected_features = features[:, feature_indexes]
        feature_mean = selected_features.mean(axis=0)
        output_mean = output.mean(axis=0)
        design = (selected_features - feature_mean) / selected_scales
        target = output - output_mean
        total_gram += design.T @ design
        total_rhs += design.T @ target
        group_calibration[name] = {
            "featureMean": feature_mean.tolist(),
            "outputMeanCodes": output_mean.tolist(),
        }
    penalty = float(selected["ridgePenalty"])
    standardized_coefficients = np.linalg.solve(
        total_gram + np.eye(total_gram.shape[0]) * penalty,
        total_rhs,
    )
    coefficients = standardized_coefficients / selected_scales[:, np.newaxis]
    return (
        {
            "selected": selected,
            "candidateCount": len(candidates),
            "bestCandidates": sorted(
                candidates,
                key=lambda candidate: (
                    float(candidate["pooledSpatialError"]["meanAbsoluteCodes"]),
                    float(candidate["worstProbeMeanAbsoluteCodes"]),
                ),
            )[:8],
            "featureIndexes": feature_indexes.tolist(),
            "coefficients": coefficients.tolist(),
        },
        coefficients,
        group_calibration,
    )


def gaussian_bank_holdout_predictions(
    captures: CaptureSet,
    *,
    channel_kind: str,
    coefficients_by_appearance: dict[str, FloatArray],
    calibration_by_appearance: dict[str, dict[int, JsonObject]],
    margin: int = 512,
) -> dict[str, dict[int, FloatArray]]:
    source = captures.reference_image(f"noise-{channel_kind}-a064-holdout")
    normalized = (source - 128.0) / 64.0
    height, width = normalized.shape[:2]
    vertical = np.fft.fftfreq(height)[:, np.newaxis]
    horizontal = np.fft.rfftfreq(width)[np.newaxis, :]
    squared_frequency = np.square(vertical) + np.square(horizontal)
    transforms = [np.fft.rfft2(normalized[:, :, channel]) for channel in range(3)]
    region = (
        slice(margin, height - margin),
        slice(margin, width - margin),
    )
    pixel_count = (height - 2 * margin) * (width - 2 * margin)
    predictions: dict[str, dict[int, FloatArray]] = {}
    scale_count = max(
        coefficients.shape[0] // 3
        for coefficients in coefficients_by_appearance.values()
    )
    for appearance in APPEARANCES:
        coefficients = coefficients_by_appearance[appearance]
        predictions[appearance] = {}
        for amplitude in (16, 64):
            calibration = calibration_by_appearance[appearance][amplitude]
            feature_mean = np.asarray(
                calibration["featureMean"],
                dtype=np.float64,
            )
            output_mean = np.asarray(
                calibration["outputMeanCodes"],
                dtype=np.float64,
            )
            baseline = output_mean - feature_mean @ coefficients
            predictions[appearance][amplitude] = np.broadcast_to(
                baseline,
                (pixel_count, 3),
            ).copy()
    feature_index = 0
    for sigma in GAUSSIAN_BANK_SIGMAS[:scale_count]:
        response = np.exp(-2.0 * math.pi**2 * sigma**2 * squared_frequency)
        for transformed in transforms:
            filtered = np.fft.irfft2(
                transformed * response,
                s=(height, width),
            )[region].reshape(-1)
            for appearance in APPEARANCES:
                if feature_index >= coefficients_by_appearance[appearance].shape[0]:
                    continue
                coefficients = coefficients_by_appearance[appearance][feature_index]
                predictions[appearance][64] += filtered[:, np.newaxis] * coefficients
                predictions[appearance][16] += (
                    filtered[:, np.newaxis] * coefficients / 4.0
                )
            feature_index += 1
    return predictions


def stochastic_gaussian_bank_validation(captures: CaptureSet) -> JsonObject:
    margin = 512
    width_points, height_points = captures.manifest["windowPoints"]
    scale = float(captures.manifest["backingScaleFactor"])
    width = round(float(width_points) * scale)
    height = round(float(height_points) * scale)
    training_features = {
        channel_kind: gaussian_bank_features(
            captures,
            channel_kind,
            role="train",
            stride=4,
            margin=margin,
        )
        for channel_kind in ("gray", "rgb")
    }
    fit_records: JsonObject = {}
    coefficients_by_appearance: dict[str, FloatArray] = {}
    calibrations_by_appearance: dict[str, dict[str, JsonObject]] = {}
    for appearance in APPEARANCES:
        groups: dict[str, tuple[FloatArray, FloatArray]] = {}
        for channel_kind, features64 in training_features.items():
            region = (
                slice(margin, height - margin, 4),
                slice(margin, width - margin, 4),
            )
            for amplitude, features in ((16, features64 / 4.0), (64, features64)):
                background = f"noise-{channel_kind}-a{amplitude:03d}-train"
                output = captures.image(
                    background,
                    "circle-4000-center",
                    "regular",
                    appearance,
                )[region].reshape(-1, 3)
                groups[f"{channel_kind}-a{amplitude:03d}"] = (
                    features,
                    output,
                )
        fit_record, coefficients, calibration = fit_centered_gaussian_bank(groups)
        fit_records[appearance] = fit_record
        coefficients_by_appearance[appearance] = coefficients
        calibrations_by_appearance[appearance] = calibration

    holdouts: dict[str, JsonObject] = {appearance: {} for appearance in APPEARANCES}
    for channel_kind in ("gray", "rgb"):
        predictions = gaussian_bank_holdout_predictions(
            captures,
            channel_kind=channel_kind,
            coefficients_by_appearance=coefficients_by_appearance,
            calibration_by_appearance={
                appearance: {
                    amplitude: calibrations_by_appearance[appearance][
                        f"{channel_kind}-a{amplitude:03d}"
                    ]
                    for amplitude in (16, 64)
                }
                for appearance in APPEARANCES
            },
            margin=margin,
        )
        region = (
            slice(margin, height - margin),
            slice(margin, width - margin),
        )
        for appearance in APPEARANCES:
            for amplitude in (16, 64):
                background = f"noise-{channel_kind}-a{amplitude:03d}-holdout"
                actual = captures.image(
                    background,
                    "circle-4000-center",
                    "regular",
                    appearance,
                )[region].reshape(-1, 3)
                training_calibration = calibrations_by_appearance[appearance][
                    f"{channel_kind}-a{amplitude:03d}"
                ]
                training_mean = np.asarray(
                    training_calibration["outputMeanCodes"],
                    dtype=np.float64,
                )
                prediction = predictions[appearance][amplitude]
                holdouts[appearance][f"{channel_kind}-a{amplitude:03d}"] = {
                    "model": prediction_report(actual, prediction),
                    "trainingMeanBaseline": prediction_report(
                        actual,
                        np.broadcast_to(training_mean, actual.shape),
                    ),
                    "holdoutMeanMinusTrainingMeanCodes": (
                        actual.mean(axis=0) - training_mean
                    ).tolist(),
                }
    return {
        "method": (
            "fit the centered output directly against an isotropic Gaussian "
            "bank spanning 1-128 pixels; select scale count and ridge penalty "
            "by leave-one-probe-out training-seed validation, then apply the "
            "frozen coefficients and training means to every holdout-seed pixel"
        ),
        "boundaryExclusionPixels": margin,
        "fitSampleStride": 4,
        "trainingSamplesPerAppearance": sum(
            features.shape[0] * 2 for features in training_features.values()
        ),
        "fits": fit_records,
        "holdouts": holdouts,
    }


def raw_inclusive_bank_scale_inputs(
    captures: CaptureSet,
    channel_kind: str,
    *,
    role: str,
    stride: int,
    margin: int,
) -> list[FloatArray]:
    source = captures.reference_image(f"noise-{channel_kind}-a064-{role}")
    height, width = source.shape[:2]
    vertical = np.fft.fftfreq(height)[:, np.newaxis]
    horizontal = np.fft.rfftfreq(width)[np.newaxis, :]
    squared_frequency = np.square(vertical) + np.square(horizontal)
    transforms = [np.fft.rfft2(source[:, :, channel]) for channel in range(3)]
    region = (
        slice(margin, height - margin, stride),
        slice(margin, width - margin, stride),
    )
    result: list[FloatArray] = []
    for sigma in RAW_INCLUSIVE_BANK_SIGMAS:
        response: float | FloatArray
        if sigma == 0.0:
            response = 1.0
        else:
            response = np.exp(-2.0 * math.pi**2 * sigma**2 * squared_frequency)
        result.append(
            np.column_stack(
                [
                    np.fft.irfft2(
                        transformed * response,
                        s=(height, width),
                    )[region].reshape(-1)
                    for transformed in transforms
                ]
            )
        )
    return result


def centered_polynomial_bank_cross_validation(
    groups: dict[str, tuple[FloatArray, FloatArray]],
    *,
    penalty: float,
) -> JsonObject:
    moments: dict[str, tuple[FloatArray, FloatArray]] = {}
    centered: dict[str, tuple[FloatArray, FloatArray, FloatArray]] = {}
    for name, (design, output) in groups.items():
        feature_mean = design.mean(axis=0)
        output_mean = output.mean(axis=0)
        centered_design = design - feature_mean
        centered_output = output - output_mean
        moments[name] = (
            centered_design.T @ centered_design,
            centered_design.T @ centered_output,
        )
        centered[name] = (centered_design, output, output_mean)

    total_gram = sum((moment[0] for moment in moments.values()), start=0)
    total_rhs = sum((moment[1] for moment in moments.values()), start=0)
    actual_parts: list[FloatArray] = []
    predicted_parts: list[FloatArray] = []
    probe_errors: JsonObject = {}
    for name, (gram, rhs) in moments.items():
        coefficients = np.linalg.solve(
            total_gram - gram + np.eye(gram.shape[0]) * penalty,
            total_rhs - rhs,
        )
        design, output, output_mean = centered[name]
        prediction = np.clip(output_mean + design @ coefficients, 0.0, 255.0)
        actual_parts.append(output)
        predicted_parts.append(prediction)
        probe_errors[name] = error_summary(output, prediction)
    actual = np.vstack(actual_parts)
    predicted = np.vstack(predicted_parts)
    return {
        "pooledSpatialError": error_summary(actual, predicted),
        "worstProbeMeanAbsoluteCodes": max(
            float(record["meanAbsoluteCodes"]) for record in probe_errors.values()
        ),
        "probeSpatialErrors": probe_errors,
    }


def raw_inclusive_polynomial_bank_validation(captures: CaptureSet) -> JsonObject:
    margin = 512
    stride = 8
    width_points, height_points = captures.manifest["windowPoints"]
    scale = float(captures.manifest["backingScaleFactor"])
    width = round(float(width_points) * scale)
    height = round(float(height_points) * scale)
    region = (
        slice(margin, height - margin, stride),
        slice(margin, width - margin, stride),
    )
    training_scales = {
        channel_kind: raw_inclusive_bank_scale_inputs(
            captures,
            channel_kind,
            role="train",
            stride=stride,
            margin=margin,
        )
        for channel_kind in ("gray", "rgb")
    }
    holdout_scales = {
        channel_kind: raw_inclusive_bank_scale_inputs(
            captures,
            channel_kind,
            role="holdout",
            stride=stride,
            margin=margin,
        )
        for channel_kind in ("gray", "rgb")
    }

    result: JsonObject = {}
    for appearance in APPEARANCES:
        candidates: list[JsonObject] = []
        designs_by_degree: dict[
            int,
            dict[str, tuple[FloatArray, FloatArray]],
        ] = {}
        for degree in (1, 2, 3):
            groups: dict[str, tuple[FloatArray, FloatArray]] = {}
            for channel_kind, scale_inputs in training_scales.items():
                for amplitude in (16, 64):
                    amplitude_scales = [
                        128.0 + (values - 128.0) * amplitude / 64.0
                        for values in scale_inputs
                    ]
                    design = per_scale_polynomial_design(
                        amplitude_scales,
                        degree=degree,
                    )[:, 1:]
                    background = f"noise-{channel_kind}-a{amplitude:03d}-train"
                    output = captures.image(
                        background,
                        "circle-4000-center",
                        "regular",
                        appearance,
                    )[region].reshape(-1, 3)
                    groups[f"{channel_kind}-a{amplitude:03d}"] = (
                        design,
                        output,
                    )
            designs_by_degree[degree] = groups
            for penalty in RAW_INCLUSIVE_BANK_RIDGE_PENALTIES:
                validation = centered_polynomial_bank_cross_validation(
                    groups,
                    penalty=penalty,
                )
                candidates.append(
                    {
                        "degreePerScale": degree,
                        "terms": int(next(iter(groups.values()))[0].shape[1]),
                        "ridgePenalty": penalty,
                        **validation,
                    }
                )

        selected = min(
            candidates,
            key=lambda candidate: (
                float(candidate["pooledSpatialError"]["meanAbsoluteCodes"]),
                float(candidate["worstProbeMeanAbsoluteCodes"]),
                int(candidate["terms"]),
                float(candidate["ridgePenalty"]),
            ),
        )
        degree = int(selected["degreePerScale"])
        penalty = float(selected["ridgePenalty"])
        groups = designs_by_degree[degree]
        term_count = int(selected["terms"])
        total_gram = np.zeros((term_count, term_count), dtype=np.float64)
        total_rhs = np.zeros((term_count, 3), dtype=np.float64)
        calibration: dict[str, tuple[FloatArray, FloatArray]] = {}
        for name, (design, output) in groups.items():
            feature_mean = design.mean(axis=0)
            output_mean = output.mean(axis=0)
            centered_design = design - feature_mean
            centered_output = output - output_mean
            total_gram += centered_design.T @ centered_design
            total_rhs += centered_design.T @ centered_output
            calibration[name] = (feature_mean, output_mean)
        coefficients = np.linalg.solve(
            total_gram + np.eye(term_count) * penalty,
            total_rhs,
        )

        holdouts: JsonObject = {}
        for channel_kind, scale_inputs in holdout_scales.items():
            for amplitude in (16, 64):
                name = f"{channel_kind}-a{amplitude:03d}"
                amplitude_scales = [
                    128.0 + (values - 128.0) * amplitude / 64.0
                    for values in scale_inputs
                ]
                design = per_scale_polynomial_design(
                    amplitude_scales,
                    degree=degree,
                )[:, 1:]
                feature_mean, output_mean = calibration[name]
                prediction = np.clip(
                    output_mean + (design - feature_mean) @ coefficients,
                    0.0,
                    255.0,
                )
                actual = captures.image(
                    f"noise-{channel_kind}-a{amplitude:03d}-holdout",
                    "circle-4000-center",
                    "regular",
                    appearance,
                )[region].reshape(-1, 3)
                holdouts[name] = prediction_report(actual, prediction)

        result[appearance] = {
            "selected": selected,
            "candidateCount": len(candidates),
            "bestCandidates": sorted(
                candidates,
                key=lambda candidate: (
                    float(candidate["pooledSpatialError"]["meanAbsoluteCodes"]),
                    float(candidate["worstProbeMeanAbsoluteCodes"]),
                ),
            )[:8],
            "coefficients": coefficients.tolist(),
            "holdouts": holdouts,
        }

    return {
        "method": (
            "include the unfiltered source and a dense 0.25-256 pixel "
            "isotropic Gaussian bank, select a separate complete RGB "
            "polynomial degree and ridge penalty by leave-one-training-probe-"
            "out validation, freeze the fit, then evaluate an independent "
            "seed on a deterministic one-in-eight central pixel grid"
        ),
        "scalesPixels": list(RAW_INCLUSIVE_BANK_SIGMAS),
        "boundaryExclusionPixels": margin,
        "fitAndHoldoutSampleStride": stride,
        "trainingSamplesPerAppearance": sum(
            next(iter(scale_inputs)).shape[0] * 2
            for scale_inputs in training_scales.values()
        ),
        "fits": result,
    }


def fit_report(
    captures: CaptureSet,
    measurements: JsonObject,
    spatial_report: JsonObject,
) -> JsonObject:
    models = frequency_models(spatial_report)
    return {
        "v210FitSchemaVersion": 1,
        "analysisImplementation": {
            "file": "analysis/liquid_glass_v210_fit.py",
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
            "frozenSpatialModelReport": {
                "spatialFitSchemaVersion": spatial_report.get(
                    "spatialFitSchemaVersion"
                ),
                "analysisImplementation": spatial_report.get("analysisImplementation"),
                "source": spatial_report.get("source"),
            },
        },
        "policy": {
            "productionShaderModified": False,
            "trainingEvidence": (
                "six on-grid layouts, four randomized off-grid layouts, and "
                "the train-seed stochastic probes"
            ),
            "finalHoldouts": (
                "legacy independent on-grid shuffle, legacy ordered/shuffled "
                "off-grid charts, and every holdout-seed stochastic pixel"
            ),
        },
        "colorContextModel": chart_model_validation(captures, measurements),
        "stochasticLocalModel": stochastic_model_validation(captures, models),
        "stochasticGaussianBankModel": stochastic_gaussian_bank_validation(captures),
        "stochasticRawInclusivePolynomialBankModel": (
            raw_inclusive_polynomial_bank_validation(captures)
        ),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fit and hold out the Liquid Glass v2.10 context probes.",
    )
    parser.add_argument("captures", type=Path)
    parser.add_argument("measurements", type=Path)
    parser.add_argument("spatial_report", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    measurements = json.loads(args.measurements.read_text(encoding="utf-8"))
    spatial_report = json.loads(args.spatial_report.read_text(encoding="utf-8"))
    if not isinstance(measurements, dict) or not isinstance(spatial_report, dict):
        raise ValueError("measurement and spatial reports must be JSON objects")
    captures = CaptureSet.open(args.captures)
    try:
        report = fit_report(captures, measurements, spatial_report)
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
    report["source"]["frozenSpatialModelReport"].update(
        {
            "file": args.spatial_report.name,
            "sha256": file_sha256(args.spatial_report),
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
