#!/usr/bin/env python3
"""Fit clear Liquid Glass' quantized half-grid state filters."""

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

from liquid_glass_clear_geometry_fit import ShapeGeometry
from liquid_glass_clear_tomography import training_background
from liquid_glass_spatial_fit import CaptureSet


type BoolArray = NDArray[np.bool_]
type FloatArray = NDArray[np.float64]
type IntArray = NDArray[np.int64]
type JsonObject = dict[str, Any]

RIG_VERSION = "2.14.0"
SCENES = (
    "circle-4000-center",
    "circle-6000-upper-left",
    "rect-6000x4000-r000-center",
    "rect-4000x6000-r000-center",
)
TRAINING_SEEDS = tuple(range(4))
AMPLITUDES = (17, 31, 47, 64)
REDUCTION_MODES = (
    "continuous",
    "floor",
    "half-up",
    "half-even",
    "ceil",
)
STATE_THRESHOLDS = np.asarray(
    (
        0.0800000,
        0.1577545,
        0.2289485,
        0.3037185,
        0.3753005,
        0.4434995,
        0.5183790,
        0.5866120,
        0.6550380,
        0.7233850,
        0.7911125,
        0.8595865,
    ),
    dtype=np.float64,
)
BASE_SOURCE_CODE = 128.0
BASE_OUTPUT_CODE = 152.0
SAMPLE_MARGIN_PIXELS = 64
SAMPLE_STRIDE_PIXELS = 17
STATE_GUARD = 0.01
HALF_GRID_KERNEL_RADIUS = 6
RIDGE_PENALTY = 1e-3
PYRAMID_SCALES = (
    ("quarter", 4, (0.0, 0.5, 1.0, 1.5)),
    ("eighth", 8, (0.0, 0.5, 1.0, 2.0)),
)


@dataclass(frozen=True, slots=True)
class SampleGrid:
    y: IntArray
    x: IntArray


@dataclass(slots=True)
class NormalStatistics:
    xtx: FloatArray
    xty: FloatArray
    yty: float = 0.0
    observations: int = 0

    @classmethod
    def empty(cls, terms: int) -> "NormalStatistics":
        return cls(
            xtx=np.zeros((terms, terms), dtype=np.float64),
            xty=np.zeros(terms, dtype=np.float64),
        )

    def add(self, design: FloatArray, target: FloatArray) -> None:
        if (
            design.ndim != 2
            or target.ndim != 1
            or design.shape[0] != target.size
            or design.shape[1] != self.xty.size
        ):
            raise ValueError("normal-equation input shapes do not match")
        self.xtx += design.T @ design
        self.xty += design.T @ target
        self.yty += float(target @ target)
        self.observations += target.size

    def __add__(self, other: "NormalStatistics") -> "NormalStatistics":
        if self.xty.shape != other.xty.shape:
            raise ValueError("normal-equation term counts do not match")
        return NormalStatistics(
            xtx=self.xtx + other.xtx,
            xty=self.xty + other.xty,
            yty=self.yty + other.yty,
            observations=self.observations + other.observations,
        )

    def __sub__(self, other: "NormalStatistics") -> "NormalStatistics":
        if self.xty.shape != other.xty.shape:
            raise ValueError("normal-equation term counts do not match")
        return NormalStatistics(
            xtx=self.xtx - other.xtx,
            xty=self.xty - other.xty,
            yty=self.yty - other.yty,
            observations=self.observations - other.observations,
        )


@dataclass(slots=True)
class ColorNormalStatistics:
    xtx: FloatArray
    xty: FloatArray
    yty: float = 0.0
    observations: int = 0

    @classmethod
    def empty(cls, terms: int) -> "ColorNormalStatistics":
        return cls(
            xtx=np.zeros((terms, terms), dtype=np.float64),
            xty=np.zeros((terms, 3), dtype=np.float64),
        )

    def add(self, design: FloatArray, target: FloatArray) -> None:
        if (
            design.ndim != 2
            or target.ndim != 2
            or target.shape[1] != 3
            or design.shape[0] != target.shape[0]
            or design.shape[1] != self.xty.shape[0]
        ):
            raise ValueError("color normal-equation input shapes do not match")
        self.xtx += design.T @ design
        self.xty += design.T @ target
        self.yty += float(np.square(target).sum())
        self.observations += target.size

    def __add__(
        self,
        other: "ColorNormalStatistics",
    ) -> "ColorNormalStatistics":
        if self.xty.shape != other.xty.shape:
            raise ValueError("color normal-equation term counts do not match")
        return ColorNormalStatistics(
            xtx=self.xtx + other.xtx,
            xty=self.xty + other.xty,
            yty=self.yty + other.yty,
            observations=self.observations + other.observations,
        )

    def __sub__(
        self,
        other: "ColorNormalStatistics",
    ) -> "ColorNormalStatistics":
        if self.xty.shape != other.xty.shape:
            raise ValueError("color normal-equation term counts do not match")
        return ColorNormalStatistics(
            xtx=self.xtx - other.xtx,
            xty=self.xty - other.xty,
            yty=self.yty - other.yty,
            observations=self.observations - other.observations,
        )


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def sample_grid(
    shape: tuple[int, int],
    *,
    margin: int = SAMPLE_MARGIN_PIXELS,
    stride: int = SAMPLE_STRIDE_PIXELS,
) -> SampleGrid:
    height, width = shape
    if margin < 0 or stride <= 0 or height <= 2 * margin or width <= 2 * margin:
        raise ValueError("invalid state-fit sampling geometry")
    y = np.arange(margin, height - margin, stride, dtype=np.int64)
    x = np.arange(margin, width - margin, stride, dtype=np.int64)
    grid_y, grid_x = np.meshgrid(y, x, indexing="ij")
    return SampleGrid(y=grid_y.reshape(-1), x=grid_x.reshape(-1))


def half_grid_reduction(source: FloatArray, mode: str) -> FloatArray:
    if (
        source.ndim != 3
        or source.shape[2] != 3
        or source.shape[0] % 2
        or source.shape[1] % 2
    ):
        raise ValueError("source must be an even-sized RGB image")
    reduced = source.reshape(
        source.shape[0] // 2,
        2,
        source.shape[1] // 2,
        2,
        3,
    ).mean(axis=(1, 3))
    match mode:
        case "continuous":
            return reduced
        case "floor":
            return np.floor(reduced)
        case "half-up":
            return np.floor(reduced + 0.5)
        case "half-even":
            return np.rint(reduced)
        case "ceil":
            return np.ceil(reduced)
        case _:
            raise ValueError(f"unknown half-grid reduction mode: {mode}")


def square_ring_offsets(
    radius: int,
) -> tuple[tuple[int, tuple[tuple[int, int], ...]], ...]:
    if radius < 0:
        raise ValueError("kernel radius must be nonnegative")
    rings: dict[int, list[tuple[int, int]]] = {}
    for y in range(-radius, radius + 1):
        for x in range(-radius, radius + 1):
            rings.setdefault(x * x + y * y, []).append((y, x))
    return tuple(
        (squared_radius, tuple(rings[squared_radius]))
        for squared_radius in sorted(rings)
    )


def pyramid_feature_names() -> tuple[str, ...]:
    return tuple(
        f"{name}/sigma-{sigma:g}"
        for name, _, sigmas in PYRAMID_SCALES
        for sigma in sigmas
    )


def quantize_codes(values: FloatArray, mode: str) -> FloatArray:
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
            raise ValueError(f"unknown code quantization mode: {mode}")


def pyramid_features(
    source: FloatArray,
    grid: SampleGrid,
    *,
    mode: str,
) -> FloatArray:
    if (
        source.ndim != 3
        or source.shape[2] != 3
        or any(
            source.shape[0] % factor or source.shape[1] % factor
            for _, factor, _ in PYRAMID_SCALES
        )
    ):
        raise ValueError("source dimensions do not support the pyramid")
    working = source.astype(np.float32)
    result = np.empty(
        (grid.y.size, len(pyramid_feature_names()), 3),
        dtype=np.float64,
    )
    feature = 0
    for _, factor, sigmas in PYRAMID_SCALES:
        reduced = cv2.resize(
            working,
            (source.shape[1] // factor, source.shape[0] // factor),
            interpolation=cv2.INTER_AREA,
        )
        reduced = quantize_codes(reduced, mode)
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
                (source.shape[1], source.shape[0]),
                interpolation=cv2.INTER_LINEAR,
            )
            result[:, feature] = (
                reconstructed[grid.y, grid.x] - BASE_SOURCE_CODE
            )
            feature += 1
    return result


def bilinear_ring_features(
    half_grid: FloatArray,
    grid: SampleGrid,
    rings: tuple[tuple[int, tuple[tuple[int, int], ...]], ...],
) -> FloatArray:
    if (
        half_grid.ndim != 3
        or half_grid.shape[2] != 3
        or grid.y.ndim != 1
        or grid.x.ndim != 1
        or grid.y.shape != grid.x.shape
        or not rings
    ):
        raise ValueError("invalid half-grid feature geometry")

    source_y = (grid.y.astype(np.float64) + 0.5) / 2.0 - 0.5
    source_x = (grid.x.astype(np.float64) + 0.5) / 2.0 - 0.5
    y0 = np.floor(source_y).astype(np.int64)
    x0 = np.floor(source_x).astype(np.int64)
    fraction_y = source_y - y0
    fraction_x = source_x - x0
    w00 = ((1.0 - fraction_y) * (1.0 - fraction_x))[:, np.newaxis]
    w01 = ((1.0 - fraction_y) * fraction_x)[:, np.newaxis]
    w10 = (fraction_y * (1.0 - fraction_x))[:, np.newaxis]
    w11 = (fraction_y * fraction_x)[:, np.newaxis]

    maximum_offset = max(
        max(max(abs(y), abs(x)) for y, x in offsets)
        for _, offsets in rings
    )
    if (
        y0.min() - maximum_offset < 0
        or x0.min() - maximum_offset < 0
        or y0.max() + maximum_offset + 1 >= half_grid.shape[0]
        or x0.max() + maximum_offset + 1 >= half_grid.shape[1]
    ):
        raise ValueError("sample grid exceeds half-grid kernel bounds")

    result = np.empty((grid.y.size, len(rings), 3), dtype=np.float64)
    for ring_index, (_, offsets) in enumerate(rings):
        values = np.zeros((grid.y.size, 3), dtype=np.float64)
        for offset_y, offset_x in offsets:
            values += (
                w00 * half_grid[y0 + offset_y, x0 + offset_x]
                + w01 * half_grid[y0 + offset_y, x0 + offset_x + 1]
                + w10 * half_grid[y0 + offset_y + 1, x0 + offset_x]
                + w11
                * half_grid[y0 + offset_y + 1, x0 + offset_x + 1]
            )
        result[:, ring_index] = values / len(offsets)
    return result


def state_masks(
    captures: CaptureSet,
    grid: SampleGrid,
    *,
    guard: float = STATE_GUARD,
) -> dict[str, tuple[IntArray, BoolArray]]:
    if guard < 0.0:
        raise ValueError("state guard must be nonnegative")
    x = grid.x.astype(np.float64)
    y = grid.y.astype(np.float64)
    result: dict[str, tuple[IntArray, BoolArray]] = {}
    for scene in SCENES:
        coordinate = ShapeGeometry.from_capture_set(
            captures,
            scene,
        ).normalized_signed_distance(x, y)
        states = np.searchsorted(STATE_THRESHOLDS, coordinate).astype(np.int64)
        distance = np.min(
            np.abs(coordinate[:, np.newaxis] - STATE_THRESHOLDS[np.newaxis]),
            axis=1,
        )
        result[scene] = (
            states,
            (coordinate >= 0.0) & (coordinate <= 1.0) & (distance > guard),
        )
    return result


def solve_coefficients(
    statistics: NormalStatistics,
    *,
    penalty: float = RIDGE_PENALTY,
) -> FloatArray:
    if statistics.observations <= 0 or penalty < 0.0:
        raise ValueError("cannot fit empty or negatively regularized statistics")
    system = statistics.xtx.copy()
    system.flat[:: system.shape[0] + 1] += penalty
    try:
        return np.linalg.solve(system, statistics.xty)
    except np.linalg.LinAlgError:
        return np.linalg.lstsq(system, statistics.xty, rcond=None)[0]


def solve_color_coefficients(
    statistics: ColorNormalStatistics,
    *,
    penalty: float = RIDGE_PENALTY,
) -> FloatArray:
    if statistics.observations <= 0 or penalty < 0.0:
        raise ValueError(
            "cannot fit empty or negatively regularized color statistics"
        )
    system = statistics.xtx.copy()
    system.flat[:: system.shape[0] + 1] += penalty
    try:
        return np.linalg.solve(system, statistics.xty)
    except np.linalg.LinAlgError:
        return np.linalg.lstsq(system, statistics.xty, rcond=None)[0]


def residual_sum_squares(
    statistics: NormalStatistics,
    coefficients: FloatArray,
) -> float:
    if coefficients.shape != statistics.xty.shape:
        raise ValueError("coefficient and statistic shapes do not match")
    value = (
        statistics.yty
        - 2.0 * float(coefficients @ statistics.xty)
        + float(coefficients @ statistics.xtx @ coefficients)
    )
    return max(0.0, value)


def color_residual_sum_squares(
    statistics: ColorNormalStatistics,
    coefficients: FloatArray,
) -> float:
    if coefficients.shape != statistics.xty.shape:
        raise ValueError("color coefficient and statistic shapes do not match")
    value = (
        statistics.yty
        - 2.0 * float(np.sum(coefficients * statistics.xty))
        + float(np.trace(coefficients.T @ statistics.xtx @ coefficients))
    )
    return max(0.0, value)


def empty_statistics(
    terms: int,
) -> dict[str, dict[int, dict[int, NormalStatistics]]]:
    return {
        mode: {
            seed: {
                state: NormalStatistics.empty(terms)
                for state in range(STATE_THRESHOLDS.size + 1)
            }
            for seed in TRAINING_SEEDS
        }
        for mode in REDUCTION_MODES
    }


def gather_statistics(
    captures: CaptureSet,
    *,
    grid: SampleGrid,
    rings: tuple[tuple[int, tuple[tuple[int, int], ...]], ...],
    masks: dict[str, tuple[IntArray, BoolArray]],
) -> dict[str, dict[int, dict[int, NormalStatistics]]]:
    terms = 2 * len(rings) + len(pyramid_feature_names())
    statistics = empty_statistics(terms)
    for seed in TRAINING_SEEDS:
        for amplitude in AMPLITUDES:
            background = training_background(seed, amplitude)
            if "holdout" in background:
                raise AssertionError("protected holdout entered state fitting")
            source = captures.reference_image(background)
            sampled_outputs = {
                scene: captures.image(
                    background,
                    scene,
                    "clear",
                    "dark",
                )[grid.y, grid.x]
                for scene in SCENES
            }
            for mode in REDUCTION_MODES:
                features = source_features(
                    source,
                    mode=mode,
                    grid=grid,
                    rings=rings,
                )
                for scene in SCENES:
                    states, eligible = masks[scene]
                    target = sampled_outputs[scene] - BASE_OUTPUT_CODE
                    for state in range(STATE_THRESHOLDS.size + 1):
                        selected = eligible & (states == state)
                        if not np.any(selected):
                            continue
                        design = (
                            features[selected]
                            .transpose(0, 2, 1)
                            .reshape(-1, terms)
                        )
                        statistics[mode][seed][state].add(
                            design,
                            target[selected].reshape(-1),
                        )
    return statistics


def source_features(
    source: FloatArray,
    *,
    mode: str,
    grid: SampleGrid,
    rings: tuple[tuple[int, tuple[tuple[int, int], ...]], ...],
) -> FloatArray:
    continuous = half_grid_reduction(source, "continuous")
    quantization_residual = (
        half_grid_reduction(source, mode) - continuous
    )
    return np.concatenate(
        (
            bilinear_ring_features(
                continuous - BASE_SOURCE_CODE,
                grid,
                rings,
            ),
            bilinear_ring_features(
                quantization_residual,
                grid,
                rings,
            ),
            pyramid_features(source, grid, mode="continuous"),
        ),
        axis=1,
    )


def gather_color_statistics(
    captures: CaptureSet,
    *,
    mode: str,
    grid: SampleGrid,
    rings: tuple[tuple[int, tuple[tuple[int, int], ...]], ...],
    masks: dict[str, tuple[IntArray, BoolArray]],
) -> dict[int, dict[int, ColorNormalStatistics]]:
    feature_terms = 2 * len(rings) + len(pyramid_feature_names())
    color_terms = feature_terms * 3
    statistics = {
        seed: {
            state: ColorNormalStatistics.empty(color_terms)
            for state in range(STATE_THRESHOLDS.size + 1)
        }
        for seed in TRAINING_SEEDS
    }
    for seed in TRAINING_SEEDS:
        for amplitude in AMPLITUDES:
            background = training_background(seed, amplitude)
            if "holdout" in background:
                raise AssertionError("protected holdout entered color fitting")
            source = captures.reference_image(background)
            features = source_features(
                source,
                mode=mode,
                grid=grid,
                rings=rings,
            )
            sampled_outputs = {
                scene: captures.image(
                    background,
                    scene,
                    "clear",
                    "dark",
                )[grid.y, grid.x]
                - BASE_OUTPUT_CODE
                for scene in SCENES
            }
            for scene in SCENES:
                states, eligible = masks[scene]
                for state in range(STATE_THRESHOLDS.size + 1):
                    selected = eligible & (states == state)
                    if not np.any(selected):
                        continue
                    design = (
                        features[selected]
                        .transpose(0, 2, 1)
                        .reshape(-1, color_terms)
                    )
                    statistics[seed][state].add(
                        design,
                        sampled_outputs[scene][selected],
                    )
    return statistics


def summed_statistics(
    by_seed: dict[int, dict[int, NormalStatistics]],
    state: int,
) -> NormalStatistics:
    iterator = iter(TRAINING_SEEDS)
    total = by_seed[next(iterator)][state]
    for seed in iterator:
        total = total + by_seed[seed][state]
    return total


def summed_color_statistics(
    by_seed: dict[int, dict[int, ColorNormalStatistics]],
    state: int,
) -> ColorNormalStatistics:
    iterator = iter(TRAINING_SEEDS)
    total = by_seed[next(iterator)][state]
    for seed in iterator:
        total = total + by_seed[seed][state]
    return total


def cross_validation_report(
    by_seed: dict[int, dict[int, NormalStatistics]],
) -> JsonObject:
    state_records: list[JsonObject] = []
    total_sse = 0.0
    total_observations = 0
    for state in range(STATE_THRESHOLDS.size + 1):
        total = summed_statistics(by_seed, state)
        state_sse = 0.0
        state_observations = 0
        folds: list[JsonObject] = []
        for held_seed in TRAINING_SEEDS:
            held = by_seed[held_seed][state]
            if held.observations == 0:
                continue
            coefficients = solve_coefficients(total - held)
            sse = residual_sum_squares(held, coefficients)
            state_sse += sse
            state_observations += held.observations
            folds.append(
                {
                    "heldSeed": held_seed,
                    "observations": held.observations,
                    "rootMeanSquareCodes": (
                        sse / held.observations
                    )
                    ** 0.5,
                }
            )
        total_sse += state_sse
        total_observations += state_observations
        state_records.append(
            {
                "state": state,
                "observations": state_observations,
                "rootMeanSquareCodes": (
                    (state_sse / state_observations) ** 0.5
                    if state_observations
                    else None
                ),
                "folds": folds,
            }
        )
    return {
        "selectionMetric": "leave-one-training-seed-out RMS output-code error",
        "observations": total_observations,
        "rootMeanSquareCodes": (
            (total_sse / total_observations) ** 0.5
            if total_observations
            else None
        ),
        "states": state_records,
    }


def color_cross_validation_report(
    by_seed: dict[int, dict[int, ColorNormalStatistics]],
) -> JsonObject:
    state_records: list[JsonObject] = []
    total_sse = 0.0
    total_observations = 0
    for state in range(STATE_THRESHOLDS.size + 1):
        total = summed_color_statistics(by_seed, state)
        state_sse = 0.0
        state_observations = 0
        folds: list[JsonObject] = []
        for held_seed in TRAINING_SEEDS:
            held = by_seed[held_seed][state]
            if held.observations == 0:
                continue
            coefficients = solve_color_coefficients(total - held)
            sse = color_residual_sum_squares(held, coefficients)
            state_sse += sse
            state_observations += held.observations
            folds.append(
                {
                    "heldSeed": held_seed,
                    "observations": held.observations,
                    "rootMeanSquareCodes": (
                        sse / held.observations
                    )
                    ** 0.5,
                }
            )
        total_sse += state_sse
        total_observations += state_observations
        state_records.append(
            {
                "state": state,
                "observations": state_observations,
                "rootMeanSquareCodes": (
                    (state_sse / state_observations) ** 0.5
                    if state_observations
                    else None
                ),
                "folds": folds,
            }
        )
    return {
        "selectionMetric": "leave-one-training-seed-out RMS output-code error",
        "observations": total_observations,
        "rootMeanSquareCodes": (
            (total_sse / total_observations) ** 0.5
            if total_observations
            else None
        ),
        "states": state_records,
    }


def exact_error_report(
    captures: CaptureSet,
    *,
    mode: str,
    statistics: dict[int, dict[int, NormalStatistics]],
    grid: SampleGrid,
    rings: tuple[tuple[int, tuple[tuple[int, int], ...]], ...],
    masks: dict[str, tuple[IntArray, BoolArray]],
) -> JsonObject:
    totals = {
        "channels": 0,
        "pixels": 0,
        "absolute": 0.0,
        "squared": 0.0,
        "maximum": 0.0,
        "exactChannels": 0,
        "exactPixels": 0,
    }
    continuous_errors: list[FloatArray] = []
    coefficients = {
        held_seed: {
            state: solve_coefficients(
                summed_statistics(statistics, state)
                - statistics[held_seed][state]
            )
            for state in range(STATE_THRESHOLDS.size + 1)
        }
        for held_seed in TRAINING_SEEDS
    }

    for held_seed in TRAINING_SEEDS:
        for amplitude in AMPLITUDES:
            background = training_background(held_seed, amplitude)
            if "holdout" in background:
                raise AssertionError("protected holdout entered state evaluation")
            source = captures.reference_image(background)
            half = half_grid_reduction(source, mode) - BASE_SOURCE_CODE
            features = np.concatenate(
                (
                    bilinear_ring_features(half, grid, rings),
                    pyramid_features(source, grid),
                ),
                axis=1,
            )
            for scene in SCENES:
                output = captures.image(
                    background,
                    scene,
                    "clear",
                    "dark",
                )[grid.y, grid.x]
                states, eligible = masks[scene]
                for state in range(STATE_THRESHOLDS.size + 1):
                    selected = eligible & (states == state)
                    if not np.any(selected):
                        continue
                    predicted_delta = np.einsum(
                        "mrc,r->mc",
                        features[selected],
                        coefficients[held_seed][state],
                        optimize=True,
                    )
                    predicted = BASE_OUTPUT_CODE + predicted_delta
                    actual = output[selected]
                    absolute = np.abs(predicted - actual)
                    rounded_delta = np.rint(predicted) - actual
                    continuous_errors.append(absolute.reshape(-1))
                    totals["channels"] += absolute.size
                    totals["pixels"] += absolute.shape[0]
                    totals["absolute"] += float(absolute.sum())
                    totals["squared"] += float(np.square(absolute).sum())
                    totals["maximum"] = max(
                        float(totals["maximum"]),
                        float(absolute.max(initial=0.0)),
                    )
                    totals["exactChannels"] += int(
                        np.count_nonzero(rounded_delta == 0.0)
                    )
                    totals["exactPixels"] += int(
                        np.count_nonzero(
                            np.all(rounded_delta == 0.0, axis=1)
                        )
                    )

    errors = np.concatenate(continuous_errors)
    channels = int(totals["channels"])
    pixels = int(totals["pixels"])
    return {
        "crossValidation": "leave one training seed out",
        "continuous": {
            "meanAbsoluteCodes": float(totals["absolute"]) / channels,
            "rootMeanSquareCodes": (
                float(totals["squared"]) / channels
            )
            ** 0.5,
            "p95AbsoluteCodes": float(np.quantile(errors, 0.95)),
            "maximumAbsoluteCodes": float(totals["maximum"]),
        },
        "rounded": {
            "exactChannelFraction": int(totals["exactChannels"]) / channels,
            "exactPixelFraction": int(totals["exactPixels"]) / pixels,
        },
        "channels": channels,
        "pixels": pixels,
    }


def color_exact_error_report(
    captures: CaptureSet,
    *,
    mode: str,
    statistics: dict[int, dict[int, ColorNormalStatistics]],
    grid: SampleGrid,
    rings: tuple[tuple[int, tuple[tuple[int, int], ...]], ...],
    masks: dict[str, tuple[IntArray, BoolArray]],
) -> JsonObject:
    totals = {
        "channels": 0,
        "pixels": 0,
        "absolute": 0.0,
        "squared": 0.0,
        "maximum": 0.0,
        "exactChannels": 0,
        "exactPixels": 0,
    }
    continuous_errors: list[FloatArray] = []
    coefficients = {
        held_seed: {
            state: solve_color_coefficients(
                summed_color_statistics(statistics, state)
                - statistics[held_seed][state]
            )
            for state in range(STATE_THRESHOLDS.size + 1)
        }
        for held_seed in TRAINING_SEEDS
    }

    for held_seed in TRAINING_SEEDS:
        for amplitude in AMPLITUDES:
            background = training_background(held_seed, amplitude)
            if "holdout" in background:
                raise AssertionError(
                    "protected holdout entered color evaluation"
                )
            source = captures.reference_image(background)
            features = source_features(
                source,
                mode=mode,
                grid=grid,
                rings=rings,
            )
            for scene in SCENES:
                output = captures.image(
                    background,
                    scene,
                    "clear",
                    "dark",
                )[grid.y, grid.x]
                states, eligible = masks[scene]
                for state in range(STATE_THRESHOLDS.size + 1):
                    selected = eligible & (states == state)
                    if not np.any(selected):
                        continue
                    design = (
                        features[selected]
                        .transpose(0, 2, 1)
                        .reshape(features[selected].shape[0], -1)
                    )
                    predicted = (
                        BASE_OUTPUT_CODE
                        + design @ coefficients[held_seed][state]
                    )
                    actual = output[selected]
                    absolute = np.abs(predicted - actual)
                    rounded_delta = np.rint(predicted) - actual
                    continuous_errors.append(absolute.reshape(-1))
                    totals["channels"] += absolute.size
                    totals["pixels"] += absolute.shape[0]
                    totals["absolute"] += float(absolute.sum())
                    totals["squared"] += float(np.square(absolute).sum())
                    totals["maximum"] = max(
                        float(totals["maximum"]),
                        float(absolute.max(initial=0.0)),
                    )
                    totals["exactChannels"] += int(
                        np.count_nonzero(rounded_delta == 0.0)
                    )
                    totals["exactPixels"] += int(
                        np.count_nonzero(
                            np.all(rounded_delta == 0.0, axis=1)
                        )
                    )

    errors = np.concatenate(continuous_errors)
    channels = int(totals["channels"])
    pixels = int(totals["pixels"])
    return {
        "crossValidation": "leave one training seed out",
        "continuous": {
            "meanAbsoluteCodes": float(totals["absolute"]) / channels,
            "rootMeanSquareCodes": (
                float(totals["squared"]) / channels
            )
            ** 0.5,
            "p95AbsoluteCodes": float(np.quantile(errors, 0.95)),
            "maximumAbsoluteCodes": float(totals["maximum"]),
        },
        "rounded": {
            "exactChannelFraction": int(totals["exactChannels"]) / channels,
            "exactPixelFraction": int(totals["exactPixels"]) / pixels,
        },
        "channels": channels,
        "pixels": pixels,
    }


def final_coefficients(
    by_seed: dict[int, dict[int, NormalStatistics]],
    feature_names: tuple[str, ...],
) -> list[JsonObject]:
    records: list[JsonObject] = []
    for state in range(STATE_THRESHOLDS.size + 1):
        total = summed_statistics(by_seed, state)
        records.append(
            {
                "state": state,
                "observations": total.observations,
                "coefficientsByFeature": {
                    name: float(coefficient)
                    for name, coefficient in zip(
                        feature_names,
                        solve_coefficients(total),
                        strict=True,
                    )
                },
            }
        )
    return records


def final_color_coefficients(
    by_seed: dict[int, dict[int, ColorNormalStatistics]],
    feature_names: tuple[str, ...],
) -> list[JsonObject]:
    color_feature_names = tuple(
        f"{channel}/{name}"
        for channel in ("red", "green", "blue")
        for name in feature_names
    )
    records: list[JsonObject] = []
    for state in range(STATE_THRESHOLDS.size + 1):
        total = summed_color_statistics(by_seed, state)
        coefficients = solve_color_coefficients(total)
        records.append(
            {
                "state": state,
                "observations": total.observations,
                "coefficientsByInputFeature": {
                    name: {
                        output: float(value)
                        for output, value in zip(
                            ("red", "green", "blue"),
                            row,
                            strict=True,
                        )
                    }
                    for name, row in zip(
                        color_feature_names,
                        coefficients,
                        strict=True,
                    )
                },
            }
        )
    return records


def fit_report(captures: CaptureSet) -> JsonObject:
    if captures.manifest.get("rigVersion") != RIG_VERSION:
        raise ValueError(f"expected Liquid Glass rig {RIG_VERSION}")
    sample = captures.reference_image(training_background(0, 17))
    grid = sample_grid(sample.shape[:2])
    rings = square_ring_offsets(HALF_GRID_KERNEL_RADIUS)
    feature_names = (
        *(
            f"half-continuous/ring-{squared_radius}"
            for squared_radius, _ in rings
        ),
        *(
            f"half-quantization-residual/ring-{squared_radius}"
            for squared_radius, _ in rings
        ),
        *pyramid_feature_names(),
    )
    masks = state_masks(captures, grid)
    statistics = gather_statistics(
        captures,
        grid=grid,
        rings=rings,
        masks=masks,
    )
    candidates = [
        {
            "reductionMode": mode,
            **cross_validation_report(statistics[mode]),
        }
        for mode in REDUCTION_MODES
    ]
    ranked = sorted(
        candidates,
        key=lambda candidate: float(candidate["rootMeanSquareCodes"]),
    )
    selected_mode = str(ranked[0]["reductionMode"])
    color_statistics = gather_color_statistics(
        captures,
        mode=selected_mode,
        grid=grid,
        rings=rings,
        masks=masks,
    )
    shared_channel = next(
        candidate
        for candidate in ranked
        if candidate["reductionMode"] == selected_mode
    )
    full_color = color_cross_validation_report(color_statistics)
    channel_candidates = sorted(
        (
            {
                "channelPolicy": "shared-channel-independent",
                **shared_channel,
            },
            {
                "channelPolicy": "full-rgb-matrix-per-feature",
                "reductionMode": selected_mode,
                **full_color,
            },
        ),
        key=lambda candidate: float(candidate["rootMeanSquareCodes"]),
    )
    selected_channel_policy = str(channel_candidates[0]["channelPolicy"])
    if selected_channel_policy == "full-rgb-matrix-per-feature":
        selected_error = color_exact_error_report(
            captures,
            mode=selected_mode,
            statistics=color_statistics,
            grid=grid,
            rings=rings,
            masks=masks,
        )
        selected_coefficients = final_color_coefficients(
            color_statistics,
            feature_names,
        )
    else:
        selected_error = exact_error_report(
            captures,
            mode=selected_mode,
            statistics=statistics[selected_mode],
            grid=grid,
            rings=rings,
            masks=masks,
        )
        selected_coefficients = final_coefficients(
            statistics[selected_mode],
            feature_names,
        )
    return {
        "clearStateFitSchemaVersion": 1,
        "analysisImplementation": {
            "file": "analysis/liquid_glass_clear_state_fit.py",
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
            "sourceReduction": (
                "2x2 RGB code mean with an independently fitted "
                "tie-quantization residual path"
            ),
            "reconstruction": "half-pixel bilinear",
            "kernel": (
                "one fourfold-symmetric square-supported half-grid kernel "
                "per measured geometry state"
            ),
            "kernelRadiusHalfGridPixels": HALF_GRID_KERNEL_RADIUS,
            "features": list(feature_names),
            "kernelTerms": len(feature_names),
            "baseSourceCode": BASE_SOURCE_CODE,
            "baseOutputCode": BASE_OUTPUT_CODE,
            "channelPoliciesTested": [
                "shared-channel-independent",
                "full-rgb-matrix-per-feature",
            ],
            "stateThresholds": STATE_THRESHOLDS.tolist(),
            "stateGuardNormalizedDistance": STATE_GUARD,
            "sampleMarginPixels": SAMPLE_MARGIN_PIXELS,
            "sampleStridePixels": SAMPLE_STRIDE_PIXELS,
            "ridgePenalty": RIDGE_PENALTY,
        },
        "reductionCandidates": ranked,
        "channelCandidates": channel_candidates,
        "selected": {
            "reductionMode": selected_mode,
            "channelPolicy": selected_channel_policy,
            "leaveOneSeedOutError": selected_error,
            "coefficients": selected_coefficients,
        },
        "policy": {
            "fitInputs": "four training seeds at amplitudes 17/31/47/64",
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
            "Fit training-only quantized half-grid filters for clear Liquid Glass."
        )
    )
    parser.add_argument("artifact", type=Path)
    parser.add_argument("--report", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    captures = CaptureSet.open(args.artifact)
    try:
        report = fit_report(captures)
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
