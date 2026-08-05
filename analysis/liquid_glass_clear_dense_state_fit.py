#!/usr/bin/env python3
"""Fit v2.15 clear states with quantized multiscale training evidence."""

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

from liquid_glass_clear_amplitude_sweep import (
    AMPLITUDES,
    RIG_VERSION,
    dense_training_background,
)
from liquid_glass_clear_state_fit import (
    BASE_OUTPUT_CODE,
    BASE_SOURCE_CODE,
    HALF_GRID_KERNEL_RADIUS,
    RIDGE_PENALTY,
    STATE_THRESHOLDS,
    ColorNormalStatistics,
    NormalStatistics,
    SampleGrid,
    bilinear_ring_features,
    color_residual_sum_squares,
    half_grid_reduction,
    pyramid_feature_names,
    pyramid_features,
    quantize_codes,
    residual_sum_squares,
    sample_grid,
    solve_coefficients,
    solve_color_coefficients,
    square_ring_offsets,
    state_masks,
)
from liquid_glass_spatial_fit import CaptureSet


type BoolArray = NDArray[np.bool_]
type FloatArray = NDArray[np.float64]
type IntArray = NDArray[np.int64]
type JsonObject = dict[str, Any]

SCENE = "circle-4000-center"
DEFAULT_SAMPLE_STRIDE = 17
AMPLITUDE_FOLDS = 8
QUANTIZATION_MODES = ("floor", "half-up", "half-even", "ceil")
PYRAMID_FEATURES_PER_SCALE = len(pyramid_feature_names()) // 2
EIGHTH_GRID_KERNEL_RADIUS = 6


@dataclass(frozen=True, slots=True)
class CachedAmplitude:
    amplitude: int
    fold: int
    features: FloatArray
    target: FloatArray


@dataclass(frozen=True, slots=True)
class Candidate:
    name: str
    feature_indices: IntArray


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def amplitude_fold(amplitude: int) -> int:
    if amplitude not in AMPLITUDES[1:]:
        raise ValueError(f"invalid dense-state amplitude: {amplitude}")
    return min(amplitude // 8, AMPLITUDE_FOLDS - 1)


def union_feature_names(
    ring_squared_radii: tuple[int, ...],
) -> tuple[str, ...]:
    half = tuple(
        f"half-continuous/r2-{squared_radius}"
        for squared_radius in ring_squared_radii
    )
    continuous_pyramid = tuple(
        f"continuous/{name}" for name in pyramid_feature_names()
    )
    residuals: list[str] = []
    for mode in QUANTIZATION_MODES:
        residuals.extend(
            f"half-{mode}-residual/r2-{squared_radius}"
            for squared_radius in ring_squared_radii
        )
        residuals.extend(
            f"{scale}-{mode}-residual/{name}"
            for scale, names in (
                (
                    "quarter",
                    pyramid_feature_names()[:PYRAMID_FEATURES_PER_SCALE],
                ),
                (
                    "eighth",
                    pyramid_feature_names()[PYRAMID_FEATURES_PER_SCALE:],
                ),
            )
            for name in names
        )
        residuals.extend(
            f"eighth-spatial-{mode}-residual/r2-{squared_radius}"
            for squared_radius in ring_squared_radii
        )
    return (*half, *continuous_pyramid, *residuals)


def candidates(
    ring_count: int,
) -> tuple[Candidate, ...]:
    if ring_count <= 0:
        raise ValueError("ring count must be positive")
    base_end = ring_count + len(pyramid_feature_names())
    base = np.arange(base_end, dtype=np.int64)
    result = [Candidate("continuous", base)]
    residual = base_end
    for mode in QUANTIZATION_MODES:
        half = np.arange(
            residual,
            residual + ring_count,
            dtype=np.int64,
        )
        residual += ring_count
        quarter = np.arange(
            residual,
            residual + PYRAMID_FEATURES_PER_SCALE,
            dtype=np.int64,
        )
        residual += PYRAMID_FEATURES_PER_SCALE
        eighth = np.arange(
            residual,
            residual + PYRAMID_FEATURES_PER_SCALE,
            dtype=np.int64,
        )
        residual += PYRAMID_FEATURES_PER_SCALE
        eighth_spatial = np.arange(
            residual,
            residual + ring_count,
            dtype=np.int64,
        )
        residual += ring_count
        scales = (
            ("half", half),
            ("quarter", quarter),
            ("eighth", eighth),
            ("eighth-spatial", eighth_spatial),
        )
        for mask in range(1, 1 << len(scales)):
            selected = tuple(
                (name, indices)
                for index, (name, indices) in enumerate(scales)
                if mask & (1 << index)
            )
            result.append(
                Candidate(
                    f"{'-and-'.join(name for name, _ in selected)}-{mode}",
                    np.concatenate(
                        (base, *(indices for _, indices in selected))
                    ),
                )
            )
    return tuple(result)


def grid_reduction(
    source: FloatArray,
    *,
    factor: int,
    mode: str,
) -> FloatArray:
    if (
        source.ndim != 3
        or source.shape[2] != 3
        or factor <= 0
        or source.shape[0] % factor
        or source.shape[1] % factor
    ):
        raise ValueError("source dimensions do not support grid reduction")
    reduced = cv2.resize(
        source.astype(np.float32),
        (source.shape[1] // factor, source.shape[0] // factor),
        interpolation=cv2.INTER_AREA,
    )
    return np.asarray(quantize_codes(reduced, mode), dtype=np.float64)


def bilinear_ring_features_at_factor(
    reduced: FloatArray,
    grid: SampleGrid,
    rings: tuple[tuple[int, tuple[tuple[int, int], ...]], ...],
    *,
    factor: int,
) -> FloatArray:
    if (
        reduced.ndim != 3
        or reduced.shape[2] != 3
        or grid.y.ndim != 1
        or grid.x.ndim != 1
        or grid.y.shape != grid.x.shape
        or not rings
        or factor <= 0
    ):
        raise ValueError("invalid reduced-grid feature geometry")

    source_y = (grid.y.astype(np.float64) + 0.5) / factor - 0.5
    source_x = (grid.x.astype(np.float64) + 0.5) / factor - 0.5
    y0 = np.floor(source_y).astype(np.int64)
    x0 = np.floor(source_x).astype(np.int64)
    fraction_y = source_y - y0
    fraction_x = source_x - x0
    weights = (
        ((1.0 - fraction_y) * (1.0 - fraction_x))[:, np.newaxis],
        ((1.0 - fraction_y) * fraction_x)[:, np.newaxis],
        (fraction_y * (1.0 - fraction_x))[:, np.newaxis],
        (fraction_y * fraction_x)[:, np.newaxis],
    )

    maximum_offset = max(
        max(max(abs(y), abs(x)) for y, x in offsets)
        for _, offsets in rings
    )
    if (
        y0.min() - maximum_offset < 0
        or x0.min() - maximum_offset < 0
        or y0.max() + maximum_offset + 1 >= reduced.shape[0]
        or x0.max() + maximum_offset + 1 >= reduced.shape[1]
    ):
        raise ValueError("sample grid exceeds reduced-grid kernel bounds")

    result = np.empty((grid.y.size, len(rings), 3), dtype=np.float64)
    w00, w01, w10, w11 = weights
    for ring_index, (_, offsets) in enumerate(rings):
        values = np.zeros((grid.y.size, 3), dtype=np.float64)
        for offset_y, offset_x in offsets:
            values += (
                w00 * reduced[y0 + offset_y, x0 + offset_x]
                + w01 * reduced[y0 + offset_y, x0 + offset_x + 1]
                + w10 * reduced[y0 + offset_y + 1, x0 + offset_x]
                + w11
                * reduced[y0 + offset_y + 1, x0 + offset_x + 1]
            )
        result[:, ring_index] = values / len(offsets)
    return result


def union_features(
    source: FloatArray,
    *,
    grid: SampleGrid,
    rings: tuple[tuple[int, tuple[tuple[int, int], ...]], ...],
) -> FloatArray:
    continuous_half = half_grid_reduction(source, "continuous")
    half = bilinear_ring_features(
        continuous_half - BASE_SOURCE_CODE,
        grid,
        rings,
    )
    continuous_pyramid = pyramid_features(
        source,
        grid,
        mode="continuous",
    )
    residuals = []
    continuous_eighth = grid_reduction(
        source,
        factor=8,
        mode="continuous",
    )
    for mode in QUANTIZATION_MODES:
        quantized_half = half_grid_reduction(source, mode)
        residuals.append(
            bilinear_ring_features(
                quantized_half - continuous_half,
                grid,
                rings,
            )
        )
        quantized = pyramid_features(source, grid, mode=mode)
        residual = quantized - continuous_pyramid
        residuals.extend(
            (
                residual[:, :PYRAMID_FEATURES_PER_SCALE],
                residual[:, PYRAMID_FEATURES_PER_SCALE:],
            )
        )
        quantized_eighth = grid_reduction(
            source,
            factor=8,
            mode=mode,
        )
        residuals.append(
            bilinear_ring_features_at_factor(
                quantized_eighth - continuous_eighth,
                grid,
                rings,
                factor=8,
            )
        )
    return np.concatenate(
        (half, continuous_pyramid, *residuals),
        axis=1,
    )


def load_cache(
    captures: CaptureSet,
    *,
    grid: SampleGrid,
    rings: tuple[tuple[int, tuple[tuple[int, int], ...]], ...],
) -> list[CachedAmplitude]:
    cache = []
    for amplitude in AMPLITUDES[1:]:
        background = dense_training_background(amplitude)
        if "holdout" in background:
            raise AssertionError("protected holdout entered dense-state fitting")
        source = captures.reference_image(background)
        features = union_features(source, grid=grid, rings=rings)
        target = (
            captures.image(
                background,
                SCENE,
                "clear",
                "dark",
            )[grid.y, grid.x]
            - BASE_OUTPUT_CODE
        )
        cache.append(
            CachedAmplitude(
                amplitude=amplitude,
                fold=amplitude_fold(amplitude),
                features=features,
                target=target,
            )
        )
    return cache


def union_statistics(
    cache: list[CachedAmplitude],
    *,
    states: IntArray,
    eligible: BoolArray,
    union_terms: int,
) -> dict[int, dict[int, NormalStatistics]]:
    statistics = {
        fold: {
            state: NormalStatistics.empty(union_terms)
            for state in range(STATE_THRESHOLDS.size + 1)
        }
        for fold in range(AMPLITUDE_FOLDS)
    }
    for fold in range(AMPLITUDE_FOLDS):
        fold_records = [record for record in cache if record.fold == fold]
        if not fold_records:
            raise ValueError(f"amplitude fold {fold} is empty")
        for state in range(STATE_THRESHOLDS.size + 1):
            selected = eligible & (states == state)
            if not np.any(selected):
                continue
            design = np.concatenate(
                [
                    record.features[selected]
                    .transpose(0, 2, 1)
                    .reshape(-1, union_terms)
                    for record in fold_records
                ]
            )
            target = np.concatenate(
                [
                    record.target[selected].reshape(-1)
                    for record in fold_records
                ]
            )
            statistics[fold][state].add(design, target)
    return statistics


def select_statistics(
    statistics: NormalStatistics,
    feature_indices: IntArray,
) -> NormalStatistics:
    if feature_indices.ndim != 1 or feature_indices.size == 0:
        raise ValueError("candidate feature indices must be a nonempty vector")
    return NormalStatistics(
        xtx=statistics.xtx[np.ix_(feature_indices, feature_indices)],
        xty=statistics.xty[feature_indices],
        yty=statistics.yty,
        observations=statistics.observations,
    )


def candidate_statistics(
    union: dict[int, dict[int, NormalStatistics]],
    candidate: Candidate,
) -> dict[int, dict[int, NormalStatistics]]:
    return {
        fold: {
            state: select_statistics(
                union[fold][state],
                candidate.feature_indices,
            )
            for state in range(STATE_THRESHOLDS.size + 1)
        }
        for fold in range(AMPLITUDE_FOLDS)
    }


def sum_folds(
    statistics: dict[int, dict[int, NormalStatistics]],
    state: int,
) -> NormalStatistics:
    iterator = iter(range(AMPLITUDE_FOLDS))
    total = statistics[next(iterator)][state]
    for fold in iterator:
        total = total + statistics[fold][state]
    return total


def cross_validation_report(
    statistics: dict[int, dict[int, NormalStatistics]],
) -> JsonObject:
    state_records = []
    pooled_sse = 0.0
    pooled_observations = 0
    for state in range(STATE_THRESHOLDS.size + 1):
        total = sum_folds(statistics, state)
        folds = []
        state_sse = 0.0
        state_observations = 0
        for held_fold in range(AMPLITUDE_FOLDS):
            held = statistics[held_fold][state]
            if held.observations == 0:
                continue
            coefficients = solve_coefficients(total - held)
            sse = residual_sum_squares(held, coefficients)
            folds.append(
                {
                    "heldFold": held_fold,
                    "amplitudes": [
                        amplitude
                        for amplitude in AMPLITUDES[1:]
                        if amplitude_fold(amplitude) == held_fold
                    ],
                    "observations": held.observations,
                    "rootMeanSquareCodes": (sse / held.observations) ** 0.5,
                }
            )
            state_sse += sse
            state_observations += held.observations
        pooled_sse += state_sse
        pooled_observations += state_observations
        full_coefficients = solve_coefficients(total)
        state_records.append(
            {
                "state": state,
                "observations": state_observations,
                "rootMeanSquareCodes": (
                    (state_sse / state_observations) ** 0.5
                    if state_observations
                    else None
                ),
                "allAmplitudeRootMeanSquareCodes": (
                    residual_sum_squares(total, full_coefficients)
                    / total.observations
                )
                ** 0.5,
                "folds": folds,
            }
        )
    return {
        "selectionMetric": (
            "eight-fold contiguous-amplitude held-out RMS output-code error"
        ),
        "observations": pooled_observations,
        "rootMeanSquareCodes": (
            (pooled_sse / pooled_observations) ** 0.5
            if pooled_observations
            else None
        ),
        "states": state_records,
    }


def exact_cross_validation_report(
    cache: list[CachedAmplitude],
    *,
    states: IntArray,
    eligible: BoolArray,
    candidate: Candidate,
    statistics: dict[int, dict[int, NormalStatistics]],
) -> JsonObject:
    coefficients = {
        held_fold: {
            state: solve_coefficients(
                sum_folds(statistics, state)
                - statistics[held_fold][state]
            )
            for state in range(STATE_THRESHOLDS.size + 1)
        }
        for held_fold in range(AMPLITUDE_FOLDS)
    }
    totals = {
        "channels": 0,
        "exact": 0,
        "absolute": 0.0,
        "squared": 0.0,
        "maximum": 0.0,
    }
    by_fold = {
        fold: {
            "channels": 0,
            "exact": 0,
            "absolute": 0.0,
            "squared": 0.0,
            "maximum": 0.0,
        }
        for fold in range(AMPLITUDE_FOLDS)
    }
    for record in cache:
        fold = record.fold
        features = record.features[:, candidate.feature_indices]
        for state in range(STATE_THRESHOLDS.size + 1):
            selected = eligible & (states == state)
            if not np.any(selected):
                continue
            design = (
                features[selected]
                .transpose(0, 2, 1)
                .reshape(-1, candidate.feature_indices.size)
            )
            actual = record.target[selected].reshape(-1)
            predicted = design @ coefficients[fold][state]
            rounded = np.floor(predicted + BASE_OUTPUT_CODE + 0.5)
            actual_codes = actual + BASE_OUTPUT_CODE
            delta = np.abs(rounded - actual_codes)
            for accumulator in (totals, by_fold[fold]):
                accumulator["channels"] += delta.size
                accumulator["exact"] += int(np.count_nonzero(delta == 0.0))
                accumulator["absolute"] += float(delta.sum())
                accumulator["squared"] += float(np.square(delta).sum())
                accumulator["maximum"] = max(
                    float(accumulator["maximum"]),
                    float(delta.max(initial=0.0)),
                )

    def summarize(accumulator: dict[str, float]) -> JsonObject:
        channels = int(accumulator["channels"])
        return {
            "channels": channels,
            "exactChannelFraction": (
                int(accumulator["exact"]) / channels if channels else None
            ),
            "meanAbsoluteCodes": (
                float(accumulator["absolute"]) / channels if channels else None
            ),
            "rootMeanSquareCodes": (
                (float(accumulator["squared"]) / channels) ** 0.5
                if channels
                else None
            ),
            "maximumAbsoluteCodes": float(accumulator["maximum"]),
        }

    return {
        **summarize(totals),
        "folds": [
            {
                "heldFold": fold,
                "amplitudes": [
                    amplitude
                    for amplitude in AMPLITUDES[1:]
                    if amplitude_fold(amplitude) == fold
                ],
                **summarize(by_fold[fold]),
            }
            for fold in range(AMPLITUDE_FOLDS)
        ],
    }


def exact_all_amplitude_report(
    cache: list[CachedAmplitude],
    *,
    states: IntArray,
    eligible: BoolArray,
    candidate: Candidate,
    statistics: dict[int, dict[int, NormalStatistics]],
) -> JsonObject:
    coefficients = {
        state: solve_coefficients(sum_folds(statistics, state))
        for state in range(STATE_THRESHOLDS.size + 1)
    }
    channels = 0
    exact = 0
    absolute = 0.0
    squared = 0.0
    maximum = 0.0
    for record in cache:
        features = record.features[:, candidate.feature_indices]
        for state in range(STATE_THRESHOLDS.size + 1):
            selected = eligible & (states == state)
            if not np.any(selected):
                continue
            design = (
                features[selected]
                .transpose(0, 2, 1)
                .reshape(-1, candidate.feature_indices.size)
            )
            actual = record.target[selected].reshape(-1) + BASE_OUTPUT_CODE
            predicted = np.floor(
                design @ coefficients[state] + BASE_OUTPUT_CODE + 0.5
            )
            delta = np.abs(predicted - actual)
            channels += delta.size
            exact += int(np.count_nonzero(delta == 0.0))
            absolute += float(delta.sum())
            squared += float(np.square(delta).sum())
            maximum = max(maximum, float(delta.max(initial=0.0)))
    return {
        "channels": channels,
        "exactChannelFraction": exact / channels if channels else None,
        "meanAbsoluteCodes": absolute / channels if channels else None,
        "rootMeanSquareCodes": (
            (squared / channels) ** 0.5 if channels else None
        ),
        "maximumAbsoluteCodes": maximum,
    }


def final_coefficients(
    statistics: dict[int, dict[int, NormalStatistics]],
    feature_names: tuple[str, ...],
) -> list[JsonObject]:
    records = []
    for state in range(STATE_THRESHOLDS.size + 1):
        coefficients = solve_coefficients(sum_folds(statistics, state))
        records.append(
            {
                "state": state,
                "coefficients": {
                    name: float(value)
                    for name, value in zip(
                        feature_names,
                        coefficients,
                        strict=True,
                    )
                },
            }
        )
    return records


def color_statistics(
    cache: list[CachedAmplitude],
    *,
    states: IntArray,
    eligible: BoolArray,
    candidate: Candidate,
) -> dict[int, dict[int, ColorNormalStatistics]]:
    color_terms = candidate.feature_indices.size * 3
    statistics = {
        fold: {
            state: ColorNormalStatistics.empty(color_terms)
            for state in range(STATE_THRESHOLDS.size + 1)
        }
        for fold in range(AMPLITUDE_FOLDS)
    }
    for fold in range(AMPLITUDE_FOLDS):
        fold_records = [record for record in cache if record.fold == fold]
        for state in range(STATE_THRESHOLDS.size + 1):
            selected = eligible & (states == state)
            if not np.any(selected):
                continue
            design = np.concatenate(
                [
                    record.features[selected][
                        :, candidate.feature_indices, :
                    ]
                    .transpose(0, 2, 1)
                    .reshape(-1, color_terms)
                    for record in fold_records
                ]
            )
            target = np.concatenate(
                [record.target[selected] for record in fold_records]
            )
            statistics[fold][state].add(design, target)
    return statistics


def sum_color_folds(
    statistics: dict[int, dict[int, ColorNormalStatistics]],
    state: int,
) -> ColorNormalStatistics:
    iterator = iter(range(AMPLITUDE_FOLDS))
    total = statistics[next(iterator)][state]
    for fold in iterator:
        total = total + statistics[fold][state]
    return total


def color_cross_validation_report(
    statistics: dict[int, dict[int, ColorNormalStatistics]],
) -> JsonObject:
    state_records = []
    pooled_sse = 0.0
    pooled_observations = 0
    for state in range(STATE_THRESHOLDS.size + 1):
        total = sum_color_folds(statistics, state)
        state_sse = 0.0
        state_observations = 0
        folds = []
        for held_fold in range(AMPLITUDE_FOLDS):
            held = statistics[held_fold][state]
            if held.observations == 0:
                continue
            coefficients = solve_color_coefficients(total - held)
            sse = color_residual_sum_squares(held, coefficients)
            folds.append(
                {
                    "heldFold": held_fold,
                    "observations": held.observations,
                    "rootMeanSquareCodes": (sse / held.observations) ** 0.5,
                }
            )
            state_sse += sse
            state_observations += held.observations
        pooled_sse += state_sse
        pooled_observations += state_observations
        full_coefficients = solve_color_coefficients(total)
        state_records.append(
            {
                "state": state,
                "observations": state_observations,
                "rootMeanSquareCodes": (
                    (state_sse / state_observations) ** 0.5
                    if state_observations
                    else None
                ),
                "allAmplitudeRootMeanSquareCodes": (
                    color_residual_sum_squares(total, full_coefficients)
                    / total.observations
                )
                ** 0.5,
                "folds": folds,
            }
        )
    return {
        "selectionMetric": (
            "eight-fold contiguous-amplitude held-out RMS output-code error"
        ),
        "observations": pooled_observations,
        "rootMeanSquareCodes": (
            (pooled_sse / pooled_observations) ** 0.5
            if pooled_observations
            else None
        ),
        "states": state_records,
    }


def color_exact_error_report(
    cache: list[CachedAmplitude],
    *,
    states: IntArray,
    eligible: BoolArray,
    candidate: Candidate,
    statistics: dict[int, dict[int, ColorNormalStatistics]],
    cross_validate: bool,
) -> JsonObject:
    color_terms = candidate.feature_indices.size * 3
    if cross_validate:
        coefficients = {
            fold: {
                state: solve_color_coefficients(
                    sum_color_folds(statistics, state)
                    - statistics[fold][state]
                )
                for state in range(STATE_THRESHOLDS.size + 1)
            }
            for fold in range(AMPLITUDE_FOLDS)
        }
    else:
        full = {
            state: solve_color_coefficients(
                sum_color_folds(statistics, state)
            )
            for state in range(STATE_THRESHOLDS.size + 1)
        }
        coefficients = {
            fold: full for fold in range(AMPLITUDE_FOLDS)
        }

    channels = 0
    exact = 0
    absolute = 0.0
    squared = 0.0
    maximum = 0.0
    for record in cache:
        fold = record.fold
        for state in range(STATE_THRESHOLDS.size + 1):
            selected = eligible & (states == state)
            if not np.any(selected):
                continue
            design = (
                record.features[selected][
                    :, candidate.feature_indices, :
                ]
                .transpose(0, 2, 1)
                .reshape(-1, color_terms)
            )
            actual = record.target[selected] + BASE_OUTPUT_CODE
            predicted = np.floor(
                design @ coefficients[fold][state]
                + BASE_OUTPUT_CODE
                + 0.5
            )
            delta = np.abs(predicted - actual)
            channels += delta.size
            exact += int(np.count_nonzero(delta == 0.0))
            absolute += float(delta.sum())
            squared += float(np.square(delta).sum())
            maximum = max(maximum, float(delta.max(initial=0.0)))
    return {
        "channels": channels,
        "exactChannelFraction": exact / channels if channels else None,
        "meanAbsoluteCodes": absolute / channels if channels else None,
        "rootMeanSquareCodes": (
            (squared / channels) ** 0.5 if channels else None
        ),
        "maximumAbsoluteCodes": maximum,
    }


def final_color_coefficients(
    statistics: dict[int, dict[int, ColorNormalStatistics]],
    feature_names: tuple[str, ...],
) -> list[JsonObject]:
    input_names = tuple(
        f"input-{channel}/{name}"
        for channel in ("red", "green", "blue")
        for name in feature_names
    )
    return [
        {
            "state": state,
            "outputs": {
                output: {
                    name: float(value)
                    for name, value in zip(
                        input_names,
                        coefficients[:, output_index],
                        strict=True,
                    )
                }
                for output_index, output in enumerate(
                    ("red", "green", "blue")
                )
            },
        }
        for state in range(STATE_THRESHOLDS.size + 1)
        for coefficients in (
            solve_color_coefficients(
                sum_color_folds(statistics, state)
            ),
        )
    ]


def build_report(
    captures: CaptureSet,
    *,
    stride: int = DEFAULT_SAMPLE_STRIDE,
) -> JsonObject:
    if captures.manifest.get("rigVersion") != RIG_VERSION:
        raise ValueError(f"expected Liquid Glass rig {RIG_VERSION}")
    sample = captures.reference_image(dense_training_background(1))
    grid = sample_grid(sample.shape[:2], stride=stride)
    rings = square_ring_offsets(HALF_GRID_KERNEL_RADIUS)
    ring_squared_radii = tuple(squared for squared, _ in rings)
    names = union_feature_names(ring_squared_radii)
    model_candidates = candidates(len(rings))
    cache = load_cache(
        captures,
        grid=grid,
        rings=rings,
    )
    scene_states, eligible = state_masks(captures, grid)[SCENE]
    union = union_statistics(
        cache,
        states=scene_states,
        eligible=eligible,
        union_terms=len(names),
    )

    reports = []
    selected_statistics: dict[int, dict[int, NormalStatistics]] | None = None
    for candidate in model_candidates:
        statistics = candidate_statistics(union, candidate)
        report = {
            "name": candidate.name,
            "features": [
                names[index] for index in candidate.feature_indices
            ],
            "featureTerms": int(candidate.feature_indices.size),
            **cross_validation_report(statistics),
        }
        reports.append(report)
    reports.sort(
        key=lambda report: (
            float(report["rootMeanSquareCodes"]),
            int(report["featureTerms"]),
            str(report["name"]),
        )
    )
    selected_name = str(reports[0]["name"])
    selected_candidate = next(
        candidate
        for candidate in model_candidates
        if candidate.name == selected_name
    )
    selected_statistics = candidate_statistics(union, selected_candidate)
    selected_feature_names = tuple(
        names[index] for index in selected_candidate.feature_indices
    )
    selected_color_statistics = color_statistics(
        cache,
        states=scene_states,
        eligible=eligible,
        candidate=selected_candidate,
    )
    identified_stage_candidates = []
    for stage_name in (
        "half-floor",
        "half-half-up",
        "half-half-even",
        "half-ceil",
    ):
        stage_candidate = next(
            candidate
            for candidate in model_candidates
            if candidate.name == stage_name
        )
        stage_statistics = candidate_statistics(union, stage_candidate)
        stage_color_statistics = color_statistics(
            cache,
            states=scene_states,
            eligible=eligible,
            candidate=stage_candidate,
        )
        identified_stage_candidates.append(
            {
                "name": stage_name,
                "features": [
                    names[index]
                    for index in stage_candidate.feature_indices
                ],
                "continuousError": cross_validation_report(
                    stage_statistics
                ),
                "heldAmplitudeExactError": exact_cross_validation_report(
                    cache,
                    states=scene_states,
                    eligible=eligible,
                    candidate=stage_candidate,
                    statistics=stage_statistics,
                ),
                "allAmplitudeExactError": exact_all_amplitude_report(
                    cache,
                    states=scene_states,
                    eligible=eligible,
                    candidate=stage_candidate,
                    statistics=stage_statistics,
                ),
                "fullRgbCoupling": {
                    "heldAmplitudeContinuousError": (
                        color_cross_validation_report(
                            stage_color_statistics
                        )
                    ),
                    "heldAmplitudeExactError": color_exact_error_report(
                        cache,
                        states=scene_states,
                        eligible=eligible,
                        candidate=stage_candidate,
                        statistics=stage_color_statistics,
                        cross_validate=True,
                    ),
                    "allAmplitudeExactError": color_exact_error_report(
                        cache,
                        states=scene_states,
                        eligible=eligible,
                        candidate=stage_candidate,
                        statistics=stage_color_statistics,
                        cross_validate=False,
                    ),
                },
            }
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
        "clearDenseStateFitSchemaVersion": 1,
        "analysisImplementation": {
            "file": "analysis/liquid_glass_clear_dense_state_fit.py",
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
        },
        "model": {
            "scene": SCENE,
            "channelPoliciesTested": (
                "shared-channel-independent",
                "full-rgb-matrix-per-feature",
            ),
            "kernelRadiusHalfGridPixels": HALF_GRID_KERNEL_RADIUS,
            "stateThresholds": STATE_THRESHOLDS.tolist(),
            "sampleStridePixels": stride,
            "sampledPixels": int(grid.y.size),
            "eligibleSampledPixels": int(np.count_nonzero(eligible)),
            "ridgePenalty": RIDGE_PENALTY,
            "amplitudeFolds": [
                {
                    "fold": fold,
                    "amplitudes": [
                        amplitude
                        for amplitude in AMPLITUDES[1:]
                        if amplitude_fold(amplitude) == fold
                    ],
                }
                for fold in range(AMPLITUDE_FOLDS)
            ],
        },
        "rankedCandidates": reports,
        "identifiedFirstStageCandidates": identified_stage_candidates,
        "selected": {
            "name": selected_name,
            "heldAmplitudeExactError": exact_cross_validation_report(
                cache,
                states=scene_states,
                eligible=eligible,
                candidate=selected_candidate,
                statistics=selected_statistics,
            ),
            "allAmplitudeExactError": exact_all_amplitude_report(
                cache,
                states=scene_states,
                eligible=eligible,
                candidate=selected_candidate,
                statistics=selected_statistics,
            ),
            "coefficients": final_coefficients(
                selected_statistics,
                selected_feature_names,
            ),
            "fullRgbCoupling": {
                "heldAmplitudeContinuousError": (
                    color_cross_validation_report(
                        selected_color_statistics
                    )
                ),
                "heldAmplitudeExactError": color_exact_error_report(
                    cache,
                    states=scene_states,
                    eligible=eligible,
                    candidate=selected_candidate,
                    statistics=selected_color_statistics,
                    cross_validate=True,
                ),
                "allAmplitudeExactError": color_exact_error_report(
                    cache,
                    states=scene_states,
                    eligible=eligible,
                    candidate=selected_candidate,
                    statistics=selected_color_statistics,
                    cross_validate=False,
                ),
                "coefficients": final_color_coefficients(
                    selected_color_statistics,
                    selected_feature_names,
                ),
            },
        },
        "policy": {
            "fitInputs": (
                "train-00 amplitudes 1 through 64 under circle-4000-center"
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
            "Fit quantized multiscale clear-state candidates on the v2.15 "
            "training sweep without opening protected output images."
        )
    )
    parser.add_argument("artifact", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument(
        "--stride",
        type=int,
        default=DEFAULT_SAMPLE_STRIDE,
        help="state-fit sampling stride in pixels",
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
