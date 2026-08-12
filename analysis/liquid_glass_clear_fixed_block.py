#!/usr/bin/env python3
"""Analyze v2.19 fixed-site clear-glass square-block tomography."""

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
from scipy.optimize import linprog, minimize_scalar
from scipy.special import erf

from liquid_glass_clear_filter_stage import code_image, impulse_origins
from liquid_glass_clear_fixed_impulse import (
    OUTPUT_QUANTIZERS,
    load_fixed_impulse_traces,
    predict_dense_samples,
    quantize_output,
    state_kernel_reports,
)
from liquid_glass_clear_state_fit import (
    STATE_THRESHOLDS,
    SampleGrid,
    state_masks,
)
from liquid_glass_spatial_fit import CaptureSet


type BoolArray = NDArray[np.bool_]
type FloatArray = NDArray[np.float64]
type IntArray = NDArray[np.int64]
type JsonObject = dict[str, Any]

RIG_VERSION = "2.19.0"
SCENE = "circle-4000-center"
CONTROL_SCENE = "circle-0500-center"
BASELINE = "gray-128"
BACKGROUND_TEMPLATE = (
    "clear-fixed-block-b{block_size:04d}-a{amplitude:03d}-train"
)
BLOCK_SIZES = (2, 4, 8, 16, 32, 64)
AMPLITUDES = (
    1,
    2,
    3,
    4,
    7,
    8,
    15,
    16,
    17,
    31,
    32,
    33,
    47,
    48,
    49,
    63,
    64,
    95,
    127,
)
CONTROL_AMPLITUDES = (1, 32, 64, 127)
BLOCK_SPACING = 162
BLOCK_OFFSET_X = 32
BLOCK_OFFSET_Y = 32
PATCH_MARGIN = 32
STATE_GUARD = 0.01
CORE_VALIDATION_AMPLITUDES = (2, 16, 64, 127)
CORE_SAMPLE_STRIDE = 8
CORE_SITES_PER_STATE = 2
ANCHOR_OFFSETS = ((0, 0), (0, 1), (1, 0), (1, 1))
AFFINE_FEASIBILITY_SAMPLES = 512
SCALE_CALIBRATION_AMPLITUDES = (
    16,
    17,
    31,
    32,
    33,
    47,
    48,
    49,
    63,
    64,
    95,
)


@dataclass(frozen=True, slots=True)
class QuantizedCounts:
    values: int
    exact: int
    nonzero: int
    exact_nonzero: int
    maximum: int
    absolute_sum: int
    squared_sum: int

    def __add__(self, other: "QuantizedCounts") -> "QuantizedCounts":
        return QuantizedCounts(
            values=self.values + other.values,
            exact=self.exact + other.exact,
            nonzero=self.nonzero + other.nonzero,
            exact_nonzero=self.exact_nonzero + other.exact_nonzero,
            maximum=max(self.maximum, other.maximum),
            absolute_sum=self.absolute_sum + other.absolute_sum,
            squared_sum=self.squared_sum + other.squared_sum,
        )

    def as_json(self) -> JsonObject:
        return {
            "observations": self.values,
            "exactFraction": self.exact / self.values,
            "nonzeroActualResponseValueCount": self.nonzero,
            "nonzeroActualResponseValueExactFraction": (
                self.exact_nonzero / self.nonzero
                if self.nonzero
                else None
            ),
            "meanAbsoluteErrorCodes": self.absolute_sum / self.values,
            "rootMeanSquareErrorCodes": (
                self.squared_sum / self.values
            )
            ** 0.5,
            "maximumAbsoluteErrorCodes": self.maximum,
        }


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def background_name(block_size: int, amplitude: int) -> str:
    if block_size not in BLOCK_SIZES or amplitude not in AMPLITUDES:
        raise ValueError(
            f"unsupported fixed block: size={block_size}, "
            f"amplitude={amplitude}"
        )
    return BACKGROUND_TEMPLATE.format(
        block_size=block_size,
        amplitude=amplitude,
    )


def fixed_block_origins(
    shape: tuple[int, int],
    *,
    margin: int = PATCH_MARGIN,
) -> IntArray:
    if margin < 0:
        raise ValueError("block margin must be nonnegative")
    origins = impulse_origins(
        shape,
        offset_x=BLOCK_OFFSET_X,
        offset_y=BLOCK_OFFSET_Y,
        spacing=BLOCK_SPACING,
    )
    height, width = shape
    maximum_size = max(BLOCK_SIZES)
    selected = (
        (origins[:, 0] >= margin)
        & (origins[:, 1] >= margin)
        & (
            origins[:, 0] + maximum_size + margin
            <= height
        )
        & (
            origins[:, 1] + maximum_size + margin
            <= width
        )
    )
    return origins[selected]


def reference_code_image(
    captures: CaptureSet,
    background: str,
) -> NDArray[np.uint8]:
    return captures.reference_image(background).astype(np.uint8)


def observed_source_code_image(
    captures: CaptureSet,
    background: str,
) -> NDArray[np.uint8]:
    key = (background, CONTROL_SCENE, "none", "dark")
    if key in captures.records:
        return captures.image(*key).astype(np.uint8)
    return reference_code_image(captures, background)


def effective_source_amplitudes(
    source: NDArray[np.uint8],
    origins: IntArray,
    unit_vectors: IntArray,
) -> FloatArray:
    if (
        source.ndim != 3
        or source.shape[2] != 3
        or origins.ndim != 2
        or origins.shape[1:] != (2,)
        or unit_vectors.shape != (origins.shape[0], 3)
    ):
        raise ValueError("invalid effective source-amplitude geometry")
    vectors = (
        source[origins[:, 0], origins[:, 1]].astype(np.int64)
        - 128
    )
    active = unit_vectors != 0
    if (
        np.any(vectors[~active] != 0)
        or np.any(vectors[active] == 0)
        or np.any(np.sign(vectors[active]) != unit_vectors[active])
    ):
        raise ValueError("observed source masks or signs differ")
    active_channels = np.count_nonzero(active, axis=1)
    if np.any(active_channels == 0):
        raise ValueError("observed source sites must have an active channel")
    return (
        np.abs(vectors).sum(axis=1, dtype=np.float64)
        / active_channels
    )


def aligned_square_exact(
    source: NDArray[np.uint8],
    origins: IntArray,
    block_size: int,
) -> bool:
    anchor = source[origins[:, 0], origins[:, 1]]
    for delta_y in range(block_size):
        for delta_x in range(block_size):
            if not np.array_equal(
                source[
                    origins[:, 0] + delta_y,
                    origins[:, 1] + delta_x,
                ],
                anchor,
            ):
                return False
    return True


def source_design_report(
    captures: CaptureSet,
    origins: IntArray,
) -> tuple[IntArray, JsonObject]:
    canonical = reference_code_image(
        captures,
        background_name(2, 1),
    )
    unit_vectors = (
        canonical[origins[:, 0], origins[:, 1]].astype(np.int64)
        - 128
    )
    mask_weights = np.array([1, 2, 4], dtype=np.uint8)
    masks = np.sum(
        (unit_vectors != 0).astype(np.uint8) * mask_weights,
        axis=1,
    )
    per_size: JsonObject = {}
    fixed = True
    for block_size in BLOCK_SIZES:
        low = reference_code_image(
            captures,
            background_name(block_size, 1),
        )
        high = reference_code_image(
            captures,
            background_name(block_size, 127),
        )
        low_vectors = (
            low[origins[:, 0], origins[:, 1]].astype(np.int64)
            - 128
        )
        high_vectors = (
            high[origins[:, 0], origins[:, 1]].astype(np.int64)
            - 128
        )
        same = (
            np.array_equal(low_vectors, unit_vectors)
            and np.array_equal(high_vectors, 127 * unit_vectors)
        )
        fixed &= same
        per_size[str(block_size)] = {
            "fixedMasksAndSignsAtAmplitudes1And127": bool(same),
            "amplitude1AlignedSquareExact": aligned_square_exact(
                low,
                origins,
                block_size,
            ),
            "amplitude127AlignedSquareExact": aligned_square_exact(
                high,
                origins,
                block_size,
            ),
            "neutralGapPixels": BLOCK_SPACING - block_size,
        }

    reduced_x = origins[:, 1] // 2
    reduced_y = origins[:, 0] // 2
    phase_coverage: JsonObject = {}
    for modulus in (2, 4, 8, 16, 32):
        pairs = {
            (int(y % modulus), int(x % modulus))
            for y, x in zip(reduced_y, reduced_x, strict=True)
        }
        phase_coverage[str(modulus)] = {
            "observedPairs": len(pairs),
            "possiblePairs": modulus * modulus,
            "complete": len(pairs) == modulus * modulus,
        }
    return (
        unit_vectors,
        {
            "commonSites": int(origins.shape[0]),
            "sourceUnitVectorRank": int(
                np.linalg.matrix_rank(unit_vectors)
            ),
            "allSitesHaveActiveChannel": bool(np.all(masks != 0)),
            "fixedSitesMasksAndSignsAcrossAllSizes": bool(fixed),
            "channelMaskCounts": {
                str(mask): int(np.count_nonzero(masks == mask))
                for mask in range(1, 8)
            },
            "sourceSpacingPixels": BLOCK_SPACING,
            "reducedGridSpacingCells": BLOCK_SPACING // 2,
            "reducedGridPhaseCoverage": phase_coverage,
            "sizes": per_size,
        },
    )


def source_control_report(captures: CaptureSet) -> JsonObject:
    exact = {
        "changedPixels": 0,
        "maxChannelDelta": 0,
        "meanAbsoluteChannelDelta": 0,
    }
    records = []
    for block_size in BLOCK_SIZES:
        for amplitude in CONTROL_AMPLITUDES:
            background = background_name(block_size, amplitude)
            record = captures.records[
                (background, CONTROL_SCENE, "none", "dark")
            ]
            records.append(
                {
                    "blockSizePixels": block_size,
                    "amplitudeCodes": amplitude,
                    "stable": record.get("stable"),
                    "sourceDiff": record.get("sourceDiff"),
                }
            )
    return {
        "required": len(BLOCK_SIZES) * len(CONTROL_AMPLITUDES),
        "available": len(records),
        "allStable": all(record["stable"] is True for record in records),
        "allPixelExact": all(
            record["sourceDiff"] == exact for record in records
        ),
        "records": records,
    }


def metric_offsets(block_size: int) -> IntArray:
    if block_size not in BLOCK_SIZES:
        raise ValueError(f"unsupported block size: {block_size}")
    low_center = block_size // 2 - 1
    high_center = block_size // 2
    points = {
        (0, 0),
        (0, block_size - 1),
        (block_size - 1, 0),
        (block_size - 1, block_size - 1),
        (low_center, low_center),
        (low_center, high_center),
        (high_center, low_center),
        (high_center, high_center),
    }
    for distance in (1, 2, 4, 8, 12, 16, 24, 32):
        points.update(
            {
                (-distance, low_center),
                (block_size - 1 + distance, high_center),
                (low_center, -distance),
                (high_center, block_size - 1 + distance),
            }
        )
    return np.asarray(sorted(points), dtype=np.int64)


def nearest_line_slack(
    amplitudes: FloatArray,
    trace: NDArray[np.integer[Any]],
) -> JsonObject:
    if (
        amplitudes.ndim != 1
        or trace.ndim != 1
        or amplitudes.size != trace.size
        or amplitudes.size < 2
    ):
        raise ValueError("line trace dimensions do not match")
    y = trace.astype(np.float64)
    ones = np.ones_like(amplitudes)
    upper = y < 255
    lower = y > 0
    a_ub = np.vstack(
        (
            np.column_stack(
                (amplitudes[upper], ones[upper], -ones[upper])
            ),
            np.column_stack(
                (-amplitudes[lower], -ones[lower], -ones[lower])
            ),
        )
    )
    b_ub = np.concatenate(
        (
            y[upper] + 0.5,
            -y[lower] + 0.5,
        )
    )
    result = linprog(
        np.array([0.0, 0.0, 1.0]),
        A_ub=a_ub,
        b_ub=b_ub,
        bounds=((None, None), (None, None), (0.0, None)),
        method="highs",
    )
    if not result.success:
        raise RuntimeError(f"line feasibility failed: {result.message}")
    return {
        "minimumAdditionalHalfWidthCodes": float(result.x[2]),
        "closedNearestIntervalFeasible": bool(result.x[2] <= 1e-9),
        "slopeCodesPerSourceCode": float(result.x[0]),
        "interceptCodes": float(result.x[1]),
    }


def clamped_quantize_output(values: FloatArray, mode: str) -> IntArray:
    return np.clip(quantize_output(values, mode), 0, 255)


def clipped_affine_continuous_fit(
    amplitudes: FloatArray,
    traces: NDArray[np.integer[Any]],
) -> FloatArray:
    if (
        amplitudes.ndim != 1
        or traces.ndim != 2
        or traces.shape[0] != amplitudes.size
        or amplitudes.size < 2
    ):
        raise ValueError("clipped affine fit dimensions do not match")
    values = traces.astype(np.float64)
    x = amplitudes[:, np.newaxis]
    uncensored = (values > 0) & (values < 255)
    weights = uncensored.astype(np.float64)
    count = weights.sum(axis=0)
    sum_x = (weights * x).sum(axis=0)
    sum_y = (weights * values).sum(axis=0)
    sum_xx = (weights * np.square(x)).sum(axis=0)
    sum_xy = (weights * x * values).sum(axis=0)
    denominator = count * sum_xx - np.square(sum_x)
    usable = denominator > 0

    design = np.column_stack(
        (amplitudes, np.ones_like(amplitudes))
    )
    fallback = np.linalg.lstsq(design, values, rcond=None)[0]
    slopes = fallback[0]
    intercepts = fallback[1]
    slopes[usable] = (
        count[usable] * sum_xy[usable]
        - sum_x[usable] * sum_y[usable]
    ) / denominator[usable]
    intercepts[usable] = (
        sum_y[usable] - slopes[usable] * sum_x[usable]
    ) / count[usable]
    return x * slopes[np.newaxis] + intercepts[np.newaxis]


def quantized_line_report(
    amplitudes: NDArray[np.integer[Any]],
    traces: NDArray[np.integer[Any]],
    *,
    feasibility_samples: int = AFFINE_FEASIBILITY_SAMPLES,
) -> JsonObject:
    if (
        amplitudes.ndim != 1
        or traces.ndim != 2
        or traces.shape[0] != amplitudes.size
        or amplitudes.size < 2
    ):
        raise ValueError("quantized line trace dimensions do not match")
    x = amplitudes.astype(np.float64)
    continuous = clipped_affine_continuous_fit(x, traces)
    actual = traces.astype(np.int64)
    actual_response = actual - actual[0:1]
    active_values = actual_response != 0
    active_traces = np.any(active_values, axis=0)
    modes: JsonObject = {}
    for mode in OUTPUT_QUANTIZERS:
        predicted = clamped_quantize_output(continuous, mode)
        error = predicted - actual
        exact = error == 0
        exact_traces = np.all(exact, axis=0)
        residue_records: JsonObject = {}
        for residue in range(8):
            selected = actual % 8 == residue
            selected_active = selected & active_values
            observations = int(np.count_nonzero(selected))
            active_observations = int(np.count_nonzero(selected_active))
            residue_records[str(residue)] = {
                "observations": observations,
                "exactFraction": (
                    float(np.count_nonzero(exact & selected))
                    / observations
                    if observations
                    else None
                ),
                "nonzeroActualResponseValueCount": active_observations,
                "nonzeroActualResponseValueExactFraction": (
                    float(np.count_nonzero(exact & selected_active))
                    / active_observations
                    if active_observations
                    else None
                ),
            }
        modes[mode] = {
            "observations": int(error.size),
            "exactValueFraction": float(np.count_nonzero(exact))
            / error.size,
            "nonzeroActualResponseValueCount": int(
                np.count_nonzero(active_values)
            ),
            "nonzeroActualResponseValueExactFraction": (
                float(np.count_nonzero(exact & active_values))
                / np.count_nonzero(active_values)
                if np.any(active_values)
                else None
            ),
            "traceCount": int(error.shape[1]),
            "activeTraceCount": int(np.count_nonzero(active_traces)),
            "exactTraceFraction": float(np.count_nonzero(exact_traces))
            / exact_traces.size,
            "activeTraceExactFraction": (
                float(
                    np.count_nonzero(exact_traces & active_traces)
                )
                / np.count_nonzero(active_traces)
                if np.any(active_traces)
                else None
            ),
            "meanAbsoluteErrorCodes": float(np.abs(error).mean()),
            "maximumAbsoluteErrorCodes": int(
                np.abs(error).max(initial=0)
            ),
            "exactByActualOutputCodeModulo8": residue_records,
        }

    active_indices = np.flatnonzero(active_traces)
    if active_indices.size > feasibility_samples:
        selection = np.linspace(
            0,
            active_indices.size - 1,
            feasibility_samples,
            dtype=np.int64,
        )
        active_indices = active_indices[selection]
    feasibility = [
        nearest_line_slack(x, actual[:, index])
        for index in active_indices
    ]
    feasible = sum(
        bool(record["closedNearestIntervalFeasible"])
        for record in feasibility
    )
    return {
        "model": (
            "one code-space affine response fitted from uncensored samples "
            "per pixel/channel, followed by one final nearest-code "
            "quantizer and uint8 clamp"
        ),
        "amplitudesCodes": amplitudes.tolist(),
        "modes": modes,
        "nearestIntervalFeasibility": {
            "population": int(np.count_nonzero(active_traces)),
            "sampled": len(feasibility),
            "closedNearestIntervalFeasible": feasible,
            "infeasible": len(feasibility) - feasible,
            "maximumAdditionalHalfWidthCodes": max(
                (
                    float(record["minimumAdditionalHalfWidthCodes"])
                    for record in feasibility
                ),
                default=0.0,
            ),
        },
    }


def summary(values: FloatArray) -> JsonObject:
    flat = values[np.isfinite(values)]
    if not flat.size:
        return {
            "count": 0,
            "mean": None,
            "median": None,
            "p05": None,
            "p95": None,
            "minimum": None,
            "maximum": None,
        }
    return {
        "count": int(flat.size),
        "mean": float(flat.mean()),
        "median": float(np.median(flat)),
        "p05": float(np.percentile(flat, 5)),
        "p95": float(np.percentile(flat, 95)),
        "minimum": float(flat.min(initial=np.inf)),
        "maximum": float(flat.max(initial=-np.inf)),
    }


def gaussian_square_center_response(
    block_sizes: FloatArray,
    sigma_output_pixels: float,
) -> FloatArray:
    if (
        block_sizes.ndim != 1
        or np.any(block_sizes <= 0)
        or not np.isfinite(sigma_output_pixels)
        or sigma_output_pixels <= 0
    ):
        raise ValueError("invalid Gaussian square-response geometry")
    extent = (
        block_sizes
        / (2.0 * np.sqrt(2.0) * sigma_output_pixels)
    )
    return np.square(erf(extent))


def nonnegative_fit(
    design: FloatArray,
    curves: FloatArray,
) -> tuple[FloatArray, FloatArray]:
    if (
        design.ndim != 2
        or curves.ndim != 2
        or design.shape[0] != curves.shape[1]
        or design.shape[1] == 0
        or design.shape[1] > 8
    ):
        raise ValueError("invalid nonnegative curve-fit design")
    coefficients = np.zeros(
        (curves.shape[0], design.shape[1]),
        dtype=np.float64,
    )
    predicted = np.zeros_like(curves)
    best_error = np.full(curves.shape[0], np.inf)
    for mask in range(1, 1 << design.shape[1]):
        selected = np.asarray(
            tuple(
                bool(mask & (1 << index))
                for index in range(design.shape[1])
            )
        )
        subset = design[:, selected]
        fitted = np.linalg.lstsq(
            subset,
            curves.T,
            rcond=None,
        )[0].T
        feasible = np.all(fitted >= -1e-12, axis=1)
        candidate = fitted @ subset.T
        error = np.square(candidate - curves).sum(axis=1)
        improved = feasible & (error < best_error)
        if not np.any(improved):
            continue
        coefficients[improved] = 0.0
        coefficients[np.ix_(improved, selected)] = fitted[improved]
        predicted[improved] = candidate[improved]
        best_error[improved] = error[improved]
    if np.any(~np.isfinite(best_error)):
        raise RuntimeError("no nonnegative scale fit was feasible")
    return coefficients, predicted


def scale_candidate_designs(
    block_sizes: FloatArray,
    *,
    gaussian_components: int,
) -> tuple[tuple[tuple[float, ...], FloatArray], ...]:
    sharp = np.ones_like(block_sizes, dtype=np.float64)
    sharp[block_sizes == min(BLOCK_SIZES)] = 0.5625
    if gaussian_components == 0:
        return (((), sharp[:, np.newaxis]),)
    narrow = np.geomspace(0.5, 32.0, 80)
    if gaussian_components == 1:
        return tuple(
            (
                (float(sigma),),
                np.column_stack(
                    (
                        sharp,
                        gaussian_square_center_response(
                            block_sizes,
                            float(sigma),
                        ),
                    )
                ),
            )
            for sigma in narrow
        )
    if gaussian_components != 2:
        raise ValueError("only zero, one, or two Gaussian scales are supported")
    first = np.geomspace(0.5, 12.0, 40)
    second = np.geomspace(2.0, 64.0, 52)
    return tuple(
        (
            (float(sigma_a), float(sigma_b)),
            np.column_stack(
                (
                    sharp,
                    gaussian_square_center_response(
                        block_sizes,
                        float(sigma_a),
                    ),
                    gaussian_square_center_response(
                        block_sizes,
                        float(sigma_b),
                    ),
                )
            ),
        )
        for sigma_a in first
        for sigma_b in second
        if sigma_b >= 1.5 * sigma_a
    )


def one_gaussian_design(
    block_sizes: FloatArray,
    sigma_output_pixels: float,
) -> FloatArray:
    sharp = np.ones_like(block_sizes, dtype=np.float64)
    sharp[block_sizes == min(BLOCK_SIZES)] = 0.5625
    return np.column_stack(
        (
            sharp,
            gaussian_square_center_response(
                block_sizes,
                sigma_output_pixels,
            ),
        )
    )


def optimized_one_gaussian_fit(
    curves: FloatArray,
    block_sizes: FloatArray,
) -> tuple[float, tuple[float, ...], FloatArray, FloatArray, FloatArray]:
    def objective(log_sigma: float) -> float:
        design = one_gaussian_design(
            block_sizes,
            float(np.exp(log_sigma)),
        )
        _, predicted = nonnegative_fit(design, curves)
        return float(np.square(predicted - curves).sum())

    result = minimize_scalar(
        objective,
        bounds=(float(np.log(0.5)), float(np.log(32.0))),
        method="bounded",
        options={"xatol": 1e-11},
    )
    if not result.success:
        raise RuntimeError(
            f"one-Gaussian scale optimization failed: {result.message}"
        )
    sigma = float(np.exp(result.x))
    design = one_gaussian_design(block_sizes, sigma)
    coefficients, predicted = nonnegative_fit(design, curves)
    return (
        float(np.square(predicted - curves).sum()),
        (sigma,),
        design,
        coefficients,
        predicted,
    )


def error_metrics(actual: FloatArray, predicted: FloatArray) -> JsonObject:
    error = predicted - actual
    absolute = np.abs(error)
    return {
        "observations": int(error.size),
        "meanAbsoluteCodesPerSourceCode": float(absolute.mean()),
        "rootMeanSquareCodesPerSourceCode": float(
            np.sqrt(np.square(error).mean())
        ),
        "maximumAbsoluteCodesPerSourceCode": float(
            absolute.max(initial=0.0)
        ),
    }


def shared_scale_fit(
    curves: FloatArray,
    block_sizes: FloatArray,
    *,
    gaussian_components: int,
) -> JsonObject:
    if (
        curves.ndim != 2
        or block_sizes.ndim != 1
        or curves.shape[1] != block_sizes.size
        or curves.shape[0] == 0
    ):
        raise ValueError("invalid shared-scale curve matrix")
    candidates = scale_candidate_designs(
        block_sizes,
        gaussian_components=gaussian_components,
    )
    best: tuple[
        float,
        tuple[float, ...],
        FloatArray,
        FloatArray,
        FloatArray,
    ] | None = None
    for scales, design in candidates:
        coefficients, predicted = nonnegative_fit(design, curves)
        score = float(np.square(predicted - curves).sum())
        if best is None or score < best[0]:
            best = (score, scales, design, coefficients, predicted)
    if gaussian_components == 1:
        refined = optimized_one_gaussian_fit(curves, block_sizes)
        if best is None or refined[0] < best[0]:
            best = refined
    assert best is not None
    _, scales, design, coefficients, predicted = best

    held_predictions = np.empty_like(curves)
    held_scales: JsonObject = {}
    for held_index, held_size in enumerate(block_sizes):
        training = np.arange(block_sizes.size) != held_index
        fold_best: tuple[
            float,
            tuple[float, ...],
            FloatArray,
            FloatArray,
        ] | None = None
        for candidate_scales, candidate_design in candidates:
            fold_coefficients, fold_prediction = nonnegative_fit(
                candidate_design[training],
                curves[:, training],
            )
            score = float(
                np.square(
                    fold_prediction - curves[:, training]
                ).sum()
            )
            if fold_best is None or score < fold_best[0]:
                fold_best = (
                    score,
                    candidate_scales,
                    fold_coefficients,
                    candidate_design,
                )
        if gaussian_components == 1:
            (
                refined_score,
                refined_scales,
                _,
                refined_coefficients,
                _,
            ) = optimized_one_gaussian_fit(
                curves[:, training],
                block_sizes[training],
            )
            if fold_best is None or refined_score < fold_best[0]:
                refined_design = one_gaussian_design(
                    block_sizes,
                    refined_scales[0],
                )
                fold_best = (
                    refined_score,
                    refined_scales,
                    refined_coefficients,
                    refined_design,
                )
        assert fold_best is not None
        (
            _,
            fold_scale_values,
            fold_coefficients,
            fold_design,
        ) = fold_best
        held_predictions[:, held_index] = (
            fold_coefficients @ fold_design[held_index]
        )
        held_scales[str(int(held_size))] = list(fold_scale_values)

    return {
        "model": (
            "nonnegative exact half-grid sharp basis plus "
            f"{gaussian_components} continuous square-Gaussian "
            "center-response components; scales shared across curves"
        ),
        "gaussianSigmaOutputPixels": list(scales),
        "training": error_metrics(curves, predicted),
        "leaveOneBlockSizeOut": {
            **error_metrics(curves, held_predictions),
            "selectedScalesByHeldBlockSize": held_scales,
        },
        "componentWeightSummaries": [
            summary(coefficients[:, index])
            for index in range(coefficients.shape[1])
        ],
        "componentWeightsByCurve": coefficients.tolist(),
        "predictedResponseByCurveAndBlockSize": predicted.tolist(),
        "designByBlockSize": {
            str(int(block_size)): design[index].tolist()
            for index, block_size in enumerate(block_sizes)
        },
    }


def center_scale_tomography(sizes: JsonObject) -> JsonObject:
    labels = []
    curves = []
    for amplitude in SCALE_CALIBRATION_AMPLITUDES:
        for state in range(STATE_THRESHOLDS.size + 1):
            values = []
            for block_size in BLOCK_SIZES:
                record = (
                    sizes[str(block_size)]["responses"][str(amplitude)][
                        "byState"
                    ].get(str(state))
                )
                if record is None:
                    break
                value = record["centerSignedGainPerSourceCode"]["mean"]
                if value is None:
                    break
                values.append(float(value))
            if len(values) != len(BLOCK_SIZES):
                continue
            labels.append(
                {
                    "amplitudeCodes": amplitude,
                    "state": state,
                }
            )
            curves.append(values)
    curve_matrix = np.asarray(curves, dtype=np.float64)
    block_sizes = np.asarray(BLOCK_SIZES, dtype=np.float64)
    if not curves:
        return {
            "available": False,
            "curveCount": 0,
            "curveLabels": [],
            "blockSizesPixels": list(BLOCK_SIZES),
        }
    return {
        "available": True,
        "curveCount": len(labels),
        "curveLabels": labels,
        "blockSizesPixels": list(BLOCK_SIZES),
        "sharpOnly": shared_scale_fit(
            curve_matrix,
            block_sizes,
            gaussian_components=0,
        ),
        "sharpPlusOneGaussian": shared_scale_fit(
            curve_matrix,
            block_sizes,
            gaussian_components=1,
        ),
        "sharpPlusTwoGaussians": shared_scale_fit(
            curve_matrix,
            block_sizes,
            gaussian_components=2,
        ),
    }


def singular_energy_report(curves: FloatArray) -> JsonObject:
    if curves.ndim != 2 or not curves.size:
        raise ValueError("scale curves must be a nonempty matrix")
    singular = np.linalg.svd(curves, compute_uv=False)
    energy = np.square(singular)
    total = float(energy.sum())
    fractions = energy / total if total else np.zeros_like(energy)
    return {
        "curveCount": int(curves.shape[0]),
        "blockSizeCount": int(curves.shape[1]),
        "singularValues": singular.tolist(),
        "energyFractions": fractions.tolist(),
        "rankOneEnergyFraction": float(fractions[:1].sum()),
        "rankTwoEnergyFraction": float(fractions[:2].sum()),
        "rankThreeEnergyFraction": float(fractions[:3].sum()),
    }


def selected_scale_curves(
    responses: FloatArray,
    selected: BoolArray,
) -> FloatArray:
    if (
        responses.ndim != 3
        or selected.shape != responses.shape[1:]
    ):
        raise ValueError("scale-response selection geometry differs")
    return np.moveaxis(responses, 0, -1)[selected]


def anchored_scale_tomography(
    captures: CaptureSet,
    baseline: NDArray[np.uint8],
    origins: IntArray,
    unit_vectors: IntArray,
) -> JsonObject:
    anchors = np.asarray(ANCHOR_OFFSETS, dtype=np.int64)
    sample_y = origins[:, 0, np.newaxis] + anchors[np.newaxis, :, 0]
    sample_x = origins[:, 1, np.newaxis] + anchors[np.newaxis, :, 1]
    grid = SampleGrid(y=sample_y.reshape(-1), x=sample_x.reshape(-1))
    flat_states, flat_eligible = state_masks(
        captures,
        grid,
        guard=STATE_GUARD,
    )[SCENE]
    states = flat_states.reshape(sample_y.shape)
    eligible = flat_eligible.reshape(sample_y.shape)
    active_channels = np.count_nonzero(unit_vectors, axis=1)
    if np.any(active_channels == 0):
        raise ValueError("anchored source sites must have an active channel")
    baseline_samples = baseline[sample_y, sample_x]

    codes = np.empty(
        (
            len(AMPLITUDES),
            len(BLOCK_SIZES),
            origins.shape[0],
            anchors.shape[0],
            3,
        ),
        dtype=np.uint8,
    )
    projected = np.empty(codes.shape[:-1], dtype=np.float64)
    for amplitude_index, amplitude in enumerate(AMPLITUDES):
        for size_index, block_size in enumerate(BLOCK_SIZES):
            output = code_image(
                captures,
                background_name(block_size, amplitude),
            )
            samples = output[sample_y, sample_x]
            codes[amplitude_index, size_index] = samples
            delta = (
                samples.astype(np.int64)
                - baseline_samples.astype(np.int64)
            )
            source = observed_source_code_image(
                captures,
                background_name(block_size, amplitude),
            )
            source_amplitudes = effective_source_amplitudes(
                source,
                origins,
                unit_vectors,
            )
            projected[amplitude_index, size_index] = (
                np.einsum(
                    "sac,sc->sa",
                    delta,
                    unit_vectors,
                    optimize=True,
                )
                / active_channels[:, np.newaxis]
                / source_amplitudes[:, np.newaxis]
            )

    state_counts = {
        str(state): int(np.count_nonzero(eligible & (states == state)))
        for state in range(STATE_THRESHOLDS.size + 1)
        if np.any(eligible & (states == state))
    }
    amplitude_records: JsonObject = {}
    for amplitude_index, amplitude in enumerate(AMPLITUDES):
        curves = selected_scale_curves(
            projected[amplitude_index],
            eligible,
        )
        record: JsonObject = {
            "normalizedResponseByBlockSize": {
                str(block_size): summary(curves[:, size_index])
                for size_index, block_size in enumerate(BLOCK_SIZES)
            },
            "scaleCurveEnergy": singular_energy_report(curves),
            "byState": {},
            "adjacentBlockSizeIncrements": {},
        }
        for state in range(STATE_THRESHOLDS.size + 1):
            selected = eligible & (states == state)
            if not np.any(selected):
                continue
            state_curves = selected_scale_curves(
                projected[amplitude_index],
                selected,
            )
            record["byState"][str(state)] = {
                "observations": int(state_curves.shape[0]),
                "meanNormalizedResponseByBlockSize": {
                    str(block_size): float(state_curves[:, size_index].mean())
                    for size_index, block_size in enumerate(BLOCK_SIZES)
                },
                "scaleCurveEnergy": singular_energy_report(state_curves),
            }
        for size_index in range(1, len(BLOCK_SIZES)):
            lower = BLOCK_SIZES[size_index - 1]
            upper = BLOCK_SIZES[size_index]
            increment = (
                projected[amplitude_index, size_index]
                - projected[amplitude_index, size_index - 1]
            )[eligible]
            lower_codes = codes[amplitude_index, size_index - 1][eligible]
            upper_codes = codes[amplitude_index, size_index][eligible]
            code_delta = (
                upper_codes.astype(np.int64)
                - lower_codes.astype(np.int64)
            )
            record["adjacentBlockSizeIncrements"][f"{lower}->{upper}"] = {
                "normalizedProjectedResponse": summary(increment),
                "unchangedChannelValueFraction": float(
                    np.count_nonzero(code_delta == 0) / code_delta.size
                ),
                "maximumAbsoluteChannelDeltaCodes": int(
                    np.abs(code_delta).max(initial=0)
                ),
            }
        amplitude_records[str(amplitude)] = record

    return {
        "model": (
            "the same four absolute output pixels at every fixed source "
            "site while only the down/right square extent changes; source "
            "mask, sign, optical state, and absolute grid phase stay fixed"
        ),
        "anchorOffsetsFromCommonTopLeftPixels": anchors.tolist(),
        "eligibleSiteAnchorPairs": int(np.count_nonzero(eligible)),
        "sitesByState": state_counts,
        "amplitudes": amplitude_records,
    }


def block_site_states(
    captures: CaptureSet,
    origins: IntArray,
    block_size: int,
) -> tuple[IntArray, BoolArray]:
    center = block_size // 2
    grid = SampleGrid(
        y=origins[:, 0] + center,
        x=origins[:, 1] + center,
    )
    return state_masks(
        captures,
        grid,
        guard=STATE_GUARD,
    )[SCENE]


def center_trace_reports_by_state(
    amplitudes: IntArray,
    traces: NDArray[np.uint8],
    offsets: IntArray,
    states: IntArray,
    block_size: int,
) -> JsonObject:
    if (
        traces.ndim != 4
        or traces.shape[0] != amplitudes.size
        or traces.shape[1] != states.size
        or traces.shape[2] != offsets.shape[0]
    ):
        raise ValueError("invalid center-trace partition geometry")
    center_values = (block_size // 2 - 1, block_size // 2)
    center = np.isin(offsets[:, 0], center_values) & np.isin(
        offsets[:, 1],
        center_values,
    )
    result: JsonObject = {}
    for state in range(STATE_THRESHOLDS.size + 1):
        selected = states == state
        if not np.any(selected):
            continue
        values = traces[:, selected][:, :, center].reshape(
            amplitudes.size,
            -1,
        )
        result[str(state)] = {
            "sites": int(np.count_nonzero(selected)),
            "traces": quantized_line_report(
                amplitudes,
                values,
                feasibility_samples=64,
            ),
        }
    return result


def response_record(
    delta: IntArray,
    projected: FloatArray,
    states: IntArray,
    block_size: int,
    amplitude: int,
    source_amplitudes: FloatArray | None = None,
) -> JsonObject:
    axis = np.arange(
        -PATCH_MARGIN,
        block_size + PATCH_MARGIN,
        dtype=np.int64,
    )
    relative_y, relative_x = np.meshgrid(axis, axis, indexing="ij")
    inside = (
        (relative_y >= 0)
        & (relative_y < block_size)
        & (relative_x >= 0)
        & (relative_x < block_size)
    )
    low_center = block_size // 2 - 1
    high_center = block_size // 2
    center = (
        np.isin(relative_y, (low_center, high_center))
        & np.isin(relative_x, (low_center, high_center))
    )
    outside_distance = np.maximum.reduce(
        (
            np.maximum(-relative_y, 0),
            np.maximum(relative_y - block_size + 1, 0),
            np.maximum(-relative_x, 0),
            np.maximum(relative_x - block_size + 1, 0),
        )
    )
    if source_amplitudes is None:
        source_amplitudes = np.full(
            projected.shape[0],
            amplitude,
            dtype=np.float64,
        )
    if (
        source_amplitudes.shape != (projected.shape[0],)
        or np.any(source_amplitudes <= 0)
    ):
        raise ValueError("invalid per-site source amplitudes")
    normalized = (
        projected
        / source_amplitudes[:, np.newaxis, np.newaxis]
    )
    integrated = (
        projected.sum(axis=(1, 2))
        / (source_amplitudes * block_size * block_size)
    )
    center_gain = normalized[:, center].mean(axis=1)
    inside_gain = normalized[:, inside].mean(axis=1)
    outside_absolute = (
        np.abs(projected[:, ~inside]).sum(axis=1)
        / (source_amplitudes * block_size * block_size)
    )
    by_state: JsonObject = {}
    for state in range(STATE_THRESHOLDS.size + 1):
        selected = states == state
        if not np.any(selected):
            continue
        by_state[str(state)] = {
            "sites": int(np.count_nonzero(selected)),
            "integratedSignedGainPerInputPixel": summary(
                integrated[selected]
            ),
            "centerSignedGainPerSourceCode": summary(
                center_gain[selected]
            ),
            "insideMeanSignedGainPerSourceCode": summary(
                inside_gain[selected]
            ),
        }

    support: JsonObject = {}
    for distance in range(PATCH_MARGIN + 1):
        selected = outside_distance == distance
        values = delta[:, selected]
        support[str(distance)] = {
            "observations": int(values.size),
            "nonzeroFraction": float(np.count_nonzero(values))
            / values.size,
            "maximumAbsoluteCodes": int(
                np.abs(values).max(initial=0)
            ),
        }
    return {
        "integratedSignedGainPerInputPixel": summary(integrated),
        "centerSignedGainPerSourceCode": summary(center_gain),
        "insideMeanSignedGainPerSourceCode": summary(inside_gain),
        "outsideAbsoluteGainPerInputPixel": summary(outside_absolute),
        "changedChannelValueFraction": float(np.count_nonzero(delta))
        / delta.size,
        "maximumAbsoluteResponseCodes": int(
            np.abs(delta).max(initial=0)
        ),
        "supportByOutsideChebyshevDistancePixels": support,
        "byState": by_state,
    }


def prediction_grid(
    origins: IntArray,
    block_size: int,
) -> tuple[SampleGrid, BoolArray]:
    critical = np.asarray(
        (
            -PATCH_MARGIN,
            -16,
            -12,
            -8,
            -4,
            -2,
            -1,
            0,
            1,
            block_size // 2 - 1,
            block_size // 2,
            block_size - 2,
            block_size - 1,
            block_size,
            block_size + 1,
            block_size + 3,
            block_size + 7,
            block_size + 11,
            block_size + 15,
            block_size + PATCH_MARGIN - 1,
        ),
        dtype=np.int64,
    )
    regular = np.arange(
        -PATCH_MARGIN,
        block_size + PATCH_MARGIN,
        CORE_SAMPLE_STRIDE,
        dtype=np.int64,
    )
    axis = np.unique(np.concatenate((critical, regular)))
    relative_y, relative_x = np.meshgrid(axis, axis, indexing="ij")
    local_y = relative_y.reshape(-1)
    local_x = relative_x.reshape(-1)
    y = (
        origins[:, 0, np.newaxis] + local_y[np.newaxis]
    ).reshape(-1)
    x = (
        origins[:, 1, np.newaxis] + local_x[np.newaxis]
    ).reshape(-1)
    inside = np.tile(
        (local_y >= 0)
        & (local_y < block_size)
        & (local_x >= 0)
        & (local_x < block_size),
        origins.shape[0],
    )
    return SampleGrid(y=y, x=x), inside


def source_convolution_eligible(
    grid: SampleGrid,
    half_grid_shape: tuple[int, int],
    offsets: IntArray,
) -> BoolArray:
    if (
        grid.y.shape != grid.x.shape
        or len(half_grid_shape) != 2
        or min(half_grid_shape) <= 0
        or offsets.ndim != 2
        or offsets.shape[1:] != (2,)
        or offsets.shape[0] == 0
    ):
        raise ValueError("invalid source-convolution geometry")
    maximum_y = 2 * (half_grid_shape[0] - 1)
    maximum_x = 2 * (half_grid_shape[1] - 1)
    return (
        (grid.y - int(offsets[:, 0].max()) >= 0)
        & (grid.y - int(offsets[:, 0].min()) <= maximum_y)
        & (grid.x - int(offsets[:, 1].max()) >= 0)
        & (grid.x - int(offsets[:, 1].min()) <= maximum_x)
    )


def state_balanced_origins(
    origins: IntArray,
    states: IntArray,
    *,
    sites_per_state: int = CORE_SITES_PER_STATE,
) -> IntArray:
    if (
        origins.ndim != 2
        or origins.shape[1:] != (2,)
        or states.shape != (origins.shape[0],)
        or sites_per_state <= 0
    ):
        raise ValueError("invalid state-balanced origin geometry")
    selected = []
    for state in sorted(int(value) for value in np.unique(states)):
        indices = np.flatnonzero(states == state)
        if indices.size > sites_per_state:
            positions = np.linspace(
                0,
                indices.size - 1,
                sites_per_state,
                dtype=np.int64,
            )
            indices = indices[positions]
        selected.extend(int(index) for index in indices)
    return origins[np.asarray(selected, dtype=np.int64)]


def quantized_counts(
    actual: IntArray,
    predicted: IntArray,
    baseline: IntArray,
) -> QuantizedCounts:
    error = predicted - actual
    exact = error == 0
    nonzero = actual != baseline
    absolute = np.abs(error)
    return QuantizedCounts(
        values=error.size,
        exact=int(np.count_nonzero(exact)),
        nonzero=int(np.count_nonzero(nonzero)),
        exact_nonzero=int(np.count_nonzero(exact & nonzero)),
        maximum=int(absolute.max(initial=0)),
        absolute_sum=int(absolute.sum(dtype=np.int64)),
        squared_sum=int(
            np.square(error, dtype=np.int64).sum(dtype=np.int64)
        ),
    )


def core_prediction_case(
    captures: CaptureSet,
    baseline: NDArray[np.uint8],
    actual_image: NDArray[np.uint8],
    origins: IntArray,
    block_size: int,
    amplitude: int,
    kernels: dict[int, FloatArray],
    offsets: IntArray,
) -> tuple[JsonObject, dict[str, QuantizedCounts]]:
    grid, inside = prediction_grid(origins, block_size)
    background = background_name(block_size, amplitude)
    source = observed_source_code_image(captures, background)
    half_grid = source[0::2, 0::2].astype(np.float64)
    states, eligible = state_masks(
        captures,
        grid,
        guard=STATE_GUARD,
    )[SCENE]
    eligible &= np.isin(states, np.asarray(sorted(kernels)))
    eligible &= source_convolution_eligible(
        grid,
        half_grid.shape[:2],
        offsets,
    )
    grid = SampleGrid(y=grid.y[eligible], x=grid.x[eligible])
    states = states[eligible]
    inside = inside[eligible]
    continuous = predict_dense_samples(
        half_grid,
        baseline,
        grid,
        states,
        kernels,
        offsets,
    )
    actual = actual_image[grid.y, grid.x].astype(np.int64)
    base = baseline[grid.y, grid.x].astype(np.int64)
    reports: JsonObject = {}
    counts: dict[str, QuantizedCounts] = {}
    for mode in OUTPUT_QUANTIZERS:
        predicted = clamped_quantize_output(continuous, mode)
        total = quantized_counts(actual, predicted, base)
        interior = quantized_counts(
            actual[inside],
            predicted[inside],
            base[inside],
        )
        exterior = quantized_counts(
            actual[~inside],
            predicted[~inside],
            base[~inside],
        )
        counts[mode] = total
        reports[mode] = {
            **total.as_json(),
            "inside": interior.as_json(),
            "outside": exterior.as_json(),
        }
    return (
        {
            "sampledPixels": int(grid.y.size),
            "states": {
                str(state): int(np.count_nonzero(states == state))
                for state in sorted(int(value) for value in np.unique(states))
            },
            "quantized": reports,
        },
        counts,
    )


def analyze_block_size(
    captures: CaptureSet,
    baseline: NDArray[np.uint8],
    origins: IntArray,
    unit_vectors: IntArray,
    block_size: int,
    kernels: dict[int, FloatArray],
    kernel_offsets: IntArray,
) -> tuple[JsonObject, dict[str, QuantizedCounts]]:
    states, state_eligible = block_site_states(
        captures,
        origins,
        block_size,
    )
    selected_origins = origins[state_eligible]
    selected_vectors = unit_vectors[state_eligible]
    selected_states = states[state_eligible]
    core_origins = state_balanced_origins(
        selected_origins,
        selected_states,
    )
    active_channels = np.count_nonzero(
        selected_vectors,
        axis=1,
    ).astype(np.float64)

    axis = np.arange(
        -PATCH_MARGIN,
        block_size + PATCH_MARGIN,
        dtype=np.int64,
    )
    sample_y = (
        selected_origins[:, 0, np.newaxis, np.newaxis]
        + axis[np.newaxis, :, np.newaxis]
    )
    sample_x = (
        selected_origins[:, 1, np.newaxis, np.newaxis]
        + axis[np.newaxis, np.newaxis, :]
    )
    baseline_patches = baseline[sample_y, sample_x]

    points = metric_offsets(block_size)
    point_y = (
        selected_origins[:, 0, np.newaxis]
        + points[np.newaxis, :, 0]
    )
    point_x = (
        selected_origins[:, 1, np.newaxis]
        + points[np.newaxis, :, 1]
    )
    amplitudes = np.asarray((0, *AMPLITUDES), dtype=np.int64)
    traces = np.empty(
        (
            amplitudes.size,
            selected_origins.shape[0],
            points.shape[0],
            3,
        ),
        dtype=np.uint8,
    )
    traces[0] = baseline[point_y, point_x]

    records: JsonObject = {}
    prediction_records: JsonObject = {}
    prediction_counts = {
        mode: QuantizedCounts(0, 0, 0, 0, 0, 0, 0)
        for mode in OUTPUT_QUANTIZERS
    }
    for amplitude_index, amplitude in enumerate(AMPLITUDES, start=1):
        background = background_name(block_size, amplitude)
        output = code_image(captures, background)
        source = observed_source_code_image(captures, background)
        source_amplitudes = effective_source_amplitudes(
            source,
            selected_origins,
            selected_vectors,
        )
        traces[amplitude_index] = output[point_y, point_x]
        patches = output[sample_y, sample_x]
        delta = (
            patches.astype(np.int64)
            - baseline_patches.astype(np.int64)
        )
        projected = (
            np.einsum(
                "shwc,sc->shw",
                delta,
                selected_vectors,
                optimize=True,
            )
            / active_channels[:, np.newaxis, np.newaxis]
        )
        records[str(amplitude)] = response_record(
            delta,
            projected,
            selected_states,
            block_size,
            amplitude,
            source_amplitudes,
        )
        if amplitude in CORE_VALIDATION_AMPLITUDES:
            prediction, counts = core_prediction_case(
                captures,
                baseline,
                output,
                core_origins,
                block_size,
                amplitude,
                kernels,
                kernel_offsets,
            )
            prediction_records[str(amplitude)] = prediction
            for mode in OUTPUT_QUANTIZERS:
                prediction_counts[mode] = (
                    prediction_counts[mode] + counts[mode]
                )

    return (
        {
            "eligibleSites": int(selected_origins.shape[0]),
            "sitesByState": {
                str(state): int(
                    np.count_nonzero(selected_states == state)
                )
                for state in range(STATE_THRESHOLDS.size + 1)
                if np.any(selected_states == state)
            },
            "metricOffsetsPixels": points.tolist(),
            "selectedPixelAmplitudeTraces": quantized_line_report(
                amplitudes,
                traces.reshape(amplitudes.size, -1),
            ),
            "centerAmplitudeTracesByState": center_trace_reports_by_state(
                amplitudes,
                traces,
                points,
                selected_states,
                block_size,
            ),
            "responses": records,
            "v218EmpiricalImpulseCoreGeneralization": {
                "model": (
                    "state-specific v2.18 radius-12 empirical impulse "
                    "kernels convolved over v2.19 aligned square sources"
                ),
                "sampleStridePixels": CORE_SAMPLE_STRIDE,
                "amplitudesCodes": list(CORE_VALIDATION_AMPLITUDES),
                "quantized": {
                    mode: counts.as_json()
                    for mode, counts in prediction_counts.items()
                },
                "records": prediction_records,
            },
        },
        prediction_counts,
    )


def analyze(captures: CaptureSet) -> JsonObject:
    if captures.manifest.get("rigVersion") != RIG_VERSION:
        raise ValueError(
            f"expected rig {RIG_VERSION}, got "
            f"{captures.manifest.get('rigVersion')!r}"
        )
    baseline = code_image(captures, BASELINE)
    origins = fixed_block_origins(baseline.shape[:2])
    unit_vectors, design = source_design_report(captures, origins)

    impulse_traces = load_fixed_impulse_traces(captures)
    _, kernels = state_kernel_reports(impulse_traces)
    aggregate = {
        mode: QuantizedCounts(0, 0, 0, 0, 0, 0, 0)
        for mode in OUTPUT_QUANTIZERS
    }
    sizes: JsonObject = {}
    for block_size in BLOCK_SIZES:
        report, counts = analyze_block_size(
            captures,
            baseline,
            origins,
            unit_vectors,
            block_size,
            kernels,
            impulse_traces.offsets,
        )
        sizes[str(block_size)] = report
        for mode in OUTPUT_QUANTIZERS:
            aggregate[mode] = aggregate[mode] + counts[mode]

    artifact_hash = (
        file_sha256(captures.root) if captures.root.is_file() else None
    )
    return {
        "clearFixedBlockSchemaVersion": 1,
        "analysisImplementation": {
            "file": "analysis/liquid_glass_clear_fixed_block.py",
            "sha256": file_sha256(Path(__file__).resolve()),
            "python": platform.python_version(),
            "numpy": np.__version__,
            "scipy": package_version("scipy"),
            "Pillow": package_version("Pillow"),
        },
        "source": {
            "artifact": captures.root.name,
            "artifactSha256": artifact_hash,
            "rigVersion": captures.manifest.get("rigVersion"),
            "osVersion": captures.manifest.get("osVersion"),
            "osBuild": captures.manifest.get("osBuild"),
            "ciCommit": captures.manifest.get("ciCommit"),
        },
        "policy": {
            "trainingOnly": True,
            "protectedHoldoutOutputsDecoded": False,
            "productionShaderModified": False,
        },
        "sourceDesign": design,
        "sourceControls": source_control_report(captures),
        "v218EmpiricalImpulseCoreGeneralization": {
            "quantized": {
                mode: counts.as_json()
                for mode, counts in aggregate.items()
            }
        },
        "centerScaleTomography": center_scale_tomography(sizes),
        "anchoredScaleTomography": anchored_scale_tomography(
            captures,
            baseline,
            origins,
            unit_vectors,
        ),
        "sizes": sizes,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Analyze v2.19 fixed-site square-block tomography."
    )
    parser.add_argument("artifact", type=Path)
    parser.add_argument("--report", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    captures = CaptureSet.open(args.artifact)
    try:
        report = analyze(captures)
    finally:
        captures.close()
    encoded = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.report is None:
        print(encoded, end="")
    else:
        args.report.write_text(encoded, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
