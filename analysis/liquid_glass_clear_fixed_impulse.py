#!/usr/bin/env python3
"""Analyze v2.18 fixed-site clear-glass impulse amplitude traces."""

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
from scipy.optimize import linprog

from liquid_glass_clear_filter_stage import (
    code_image,
    impulse_offsets,
    impulse_origins,
)
from liquid_glass_clear_state_fit import (
    STATE_THRESHOLDS,
    SampleGrid,
    sample_grid,
    state_masks,
)
from liquid_glass_spatial_fit import CaptureSet


type BoolArray = NDArray[np.bool_]
type FloatArray = NDArray[np.float64]
type IntArray = NDArray[np.int64]
type JsonObject = dict[str, Any]

RIG_VERSION = "2.18.0"
SCENE = "circle-4000-center"
BASELINE = "gray-128"
BACKGROUND_TEMPLATE = "clear-fixed-impulse-a{amplitude:03d}-train"
AMPLITUDES = tuple(range(1, 128))
CONTROL_AMPLITUDES = (
    1,
    2,
    3,
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
IMPULSE_SPACING = 66
IMPULSE_OFFSET_X = 32
IMPULSE_OFFSET_Y = 32
TRACE_RADIUS = 12
SUPPORT_RADIUS = 16
STATE_GUARD = 0.01
AFFINE_CHUNK_COLUMNS = 16_384
AFFINE_FEASIBILITY_SAMPLES = 512
KERNEL_FOLDS = 4
OUTPUT_QUANTIZERS = ("half-up", "half-even")
DENSE_VALIDATION_AMPLITUDES = (1, 2, 3, 8, 16, 32, 64)
DENSE_SAMPLE_MARGIN = 64
DENSE_SAMPLE_STRIDE = 17


@dataclass(frozen=True, slots=True)
class FixedImpulseTraces:
    codes: NDArray[np.uint8]
    offsets: IntArray
    origins: IntArray
    states: IntArray
    source_unit_vectors: IntArray
    support: JsonObject


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def background_name(amplitude: int) -> str:
    if amplitude not in AMPLITUDES:
        raise ValueError(f"unsupported amplitude: {amplitude}")
    return BACKGROUND_TEMPLATE.format(amplitude=amplitude)


def fixed_impulse_origins(
    shape: tuple[int, int],
    *,
    radius: int = SUPPORT_RADIUS,
) -> IntArray:
    origins = impulse_origins(
        shape,
        offset_x=IMPULSE_OFFSET_X,
        offset_y=IMPULSE_OFFSET_Y,
        spacing=IMPULSE_SPACING,
    )
    height, width = shape
    within = (
        (origins[:, 0] >= radius)
        & (origins[:, 0] + radius < height)
        & (origins[:, 1] >= radius)
        & (origins[:, 1] + radius < width)
    )
    return origins[within]


def reference_code_image(
    captures: CaptureSet,
    background: str,
) -> NDArray[np.uint8]:
    return captures.reference_image(background).astype(np.uint8)


def aligned_block_exact(
    source: NDArray[np.uint8],
    origins: IntArray,
) -> bool:
    anchor = source[origins[:, 0], origins[:, 1]]
    return all(
        np.array_equal(
            source[
                origins[:, 0] + delta_y,
                origins[:, 1] + delta_x,
            ],
            anchor,
        )
        for delta_y in range(2)
        for delta_x in range(2)
    )


def source_design_report(
    captures: CaptureSet,
    origins: IntArray,
) -> tuple[IntArray, JsonObject]:
    low = reference_code_image(captures, background_name(1))
    high = reference_code_image(captures, background_name(127))
    low_vectors = low[origins[:, 0], origins[:, 1]].astype(np.int64) - 128
    high_vectors = high[origins[:, 0], origins[:, 1]].astype(np.int64) - 128
    fixed = np.array_equal(high_vectors, 127 * low_vectors)
    masks = np.sum(
        (low_vectors != 0).astype(np.uint8)
        * np.array([1, 2, 4], dtype=np.uint8),
        axis=1,
    )
    reduced_x = origins[:, 1] // 2
    reduced_y = origins[:, 0] // 2
    phase_coverage = {}
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
        low_vectors,
        {
            "sites": int(origins.shape[0]),
            "allSitesHaveActiveChannel": bool(np.all(masks != 0)),
            "fixedMasksAndSignsAtAmplitudes1And127": bool(fixed),
            "amplitude1AlignedTwoByTwoBlocksExact": aligned_block_exact(
                low,
                origins,
            ),
            "amplitude127AlignedTwoByTwoBlocksExact": aligned_block_exact(
                high,
                origins,
            ),
            "channelMaskCounts": {
                str(mask): int(np.count_nonzero(masks == mask))
                for mask in range(1, 8)
            },
            "reducedGridPhaseCoverage": phase_coverage,
        },
    )


def source_control_report(captures: CaptureSet) -> JsonObject:
    exact = {
        "changedPixels": 0,
        "maxChannelDelta": 0,
        "meanAbsoluteChannelDelta": 0,
    }
    records = []
    for amplitude in CONTROL_AMPLITUDES:
        background = background_name(amplitude)
        record = captures.records[
            (background, "circle-0500-center", "none", "dark")
        ]
        records.append(
            {
                "amplitudeCodes": amplitude,
                "stable": record.get("stable"),
                "sourceDiff": record.get("sourceDiff"),
            }
        )
    return {
        "required": len(CONTROL_AMPLITUDES),
        "available": len(records),
        "allStable": all(record["stable"] is True for record in records),
        "allPixelExact": all(
            record["sourceDiff"] == exact for record in records
        ),
        "records": records,
    }


def load_fixed_impulse_traces(
    captures: CaptureSet,
) -> FixedImpulseTraces:
    baseline = code_image(captures, BASELINE)
    origins = fixed_impulse_origins(baseline.shape[:2])
    source_unit_vectors, design = source_design_report(captures, origins)
    grid = SampleGrid(y=origins[:, 0], x=origins[:, 1])
    states, eligible = state_masks(
        captures,
        grid,
        guard=STATE_GUARD,
    )[SCENE]
    origins = origins[eligible]
    states = states[eligible]
    source_unit_vectors = source_unit_vectors[eligible]

    support_offsets = impulse_offsets(SUPPORT_RADIUS)
    support_radius = np.maximum(
        np.abs(support_offsets[:, 0]),
        np.abs(support_offsets[:, 1]),
    )
    trace_selected = support_radius <= TRACE_RADIUS
    trace_offsets = support_offsets[trace_selected]
    sample_y = (
        origins[:, 0, np.newaxis]
        + support_offsets[np.newaxis, :, 0]
    )
    sample_x = (
        origins[:, 1, np.newaxis]
        + support_offsets[np.newaxis, :, 1]
    )
    baseline_patches = baseline[sample_y, sample_x]
    traces = np.empty(
        (
            len(AMPLITUDES) + 1,
            origins.shape[0],
            trace_offsets.shape[0],
            3,
        ),
        dtype=np.uint8,
    )
    traces[0] = baseline_patches[:, trace_selected]
    changed_by_radius = np.zeros(SUPPORT_RADIUS + 1, dtype=np.int64)
    observed_by_radius = np.zeros(SUPPORT_RADIUS + 1, dtype=np.int64)
    maximum_by_radius = np.zeros(SUPPORT_RADIUS + 1, dtype=np.int64)

    for amplitude in AMPLITUDES:
        output = code_image(captures, background_name(amplitude))
        patches = output[sample_y, sample_x]
        delta = (
            patches.astype(np.int16)
            - baseline_patches.astype(np.int16)
        )
        traces[amplitude] = patches[:, trace_selected]
        for radius in range(SUPPORT_RADIUS + 1):
            values = delta[:, support_radius == radius]
            changed_by_radius[radius] += np.count_nonzero(values)
            observed_by_radius[radius] += values.size
            maximum_by_radius[radius] = max(
                maximum_by_radius[radius],
                int(np.abs(values).max(initial=0)),
            )

    support = {
        str(radius): {
            "observations": int(observed_by_radius[radius]),
            "nonzeroFraction": (
                float(changed_by_radius[radius])
                / observed_by_radius[radius]
                if observed_by_radius[radius]
                else None
            ),
            "maximumAbsoluteCodes": int(maximum_by_radius[radius]),
        }
        for radius in range(SUPPORT_RADIUS + 1)
    }
    support["sourceDesignBeforeStateEligibility"] = design
    return FixedImpulseTraces(
        codes=traces,
        offsets=trace_offsets,
        origins=origins,
        states=states,
        source_unit_vectors=source_unit_vectors,
        support=support,
    )


def minimum_nearest_affine_slack(
    trace: NDArray[np.integer[Any]],
) -> JsonObject:
    if trace.ndim != 1 or trace.size < 2:
        raise ValueError("an amplitude trace must contain at least two codes")
    x = np.arange(trace.size, dtype=np.float64)
    y = trace.astype(np.float64)
    ones = np.ones_like(x)
    a_ub = np.vstack(
        (
            np.column_stack((x, ones, -ones)),
            np.column_stack((-x, -ones, -ones)),
        )
    )
    b_ub = np.concatenate((y + 0.5, -y + 0.5))
    result = linprog(
        np.array([0.0, 0.0, 1.0]),
        A_ub=a_ub,
        b_ub=b_ub,
        bounds=((None, None), (None, None), (0.0, None)),
        method="highs",
    )
    if not result.success:
        raise RuntimeError(f"affine feasibility solver failed: {result.message}")
    return {
        "minimumAdditionalHalfWidthCodes": float(result.x[2]),
        "closedNearestIntervalFeasible": bool(result.x[2] <= 1e-9),
        "slopeCodesPerAmplitude": float(result.x[0]),
        "interceptCodes": float(result.x[1]),
    }


def quantize_output(values: FloatArray, mode: str) -> IntArray:
    match mode:
        case "half-up":
            return np.floor(values + 0.5).astype(np.int64)
        case "half-even":
            return np.rint(values).astype(np.int64)
        case _:
            raise ValueError(f"unknown output quantizer: {mode}")


def quantized_affine_report(
    traces: NDArray[np.integer[Any]],
    *,
    feasibility_samples: int = AFFINE_FEASIBILITY_SAMPLES,
) -> JsonObject:
    if traces.ndim != 2 or traces.shape[0] < 2:
        raise ValueError("traces must have shape (amplitude, observation)")
    x = np.arange(traces.shape[0], dtype=np.float64)
    centered_x = x - x.mean()
    denominator = float(centered_x @ centered_x)
    total_values = traces.size
    total_exact = 0
    active_values = 0
    active_exact = 0
    exact_traces = 0
    active_traces = 0
    active_exact_traces = 0
    maximum_error = 0
    signed_errors: dict[int, int] = {}
    residue_observations = np.zeros(8, dtype=np.int64)
    residue_exact = np.zeros(8, dtype=np.int64)
    residue_active_observations = np.zeros(8, dtype=np.int64)
    residue_active_exact = np.zeros(8, dtype=np.int64)
    failed_indices: list[int] = []
    chunk_count = (
        traces.shape[1] + AFFINE_CHUNK_COLUMNS - 1
    ) // AFFINE_CHUNK_COLUMNS
    failed_samples_per_chunk = max(
        1,
        (feasibility_samples + chunk_count - 1) // chunk_count,
    )

    for start in range(0, traces.shape[1], AFFINE_CHUNK_COLUMNS):
        stop = min(start + AFFINE_CHUNK_COLUMNS, traces.shape[1])
        values = traces[:, start:stop].astype(np.float64)
        means = values.mean(axis=0)
        slopes = centered_x @ values / denominator
        intercepts = means - slopes * x.mean()
        predicted = np.rint(
            x[:, np.newaxis] * slopes[np.newaxis]
            + intercepts[np.newaxis]
        )
        error = predicted.astype(np.int64) - values.astype(np.int64)
        exact = error == 0
        active = values != values[0:1]
        trace_exact = np.all(exact, axis=0)
        trace_active = np.any(active, axis=0)
        total_exact += int(np.count_nonzero(exact))
        active_values += int(np.count_nonzero(active))
        active_exact += int(np.count_nonzero(exact & active))
        exact_traces += int(np.count_nonzero(trace_exact))
        active_traces += int(np.count_nonzero(trace_active))
        active_exact_traces += int(
            np.count_nonzero(trace_active & trace_exact)
        )
        actual_codes = values.astype(np.int64)
        for residue in range(8):
            selected_residue = actual_codes % 8 == residue
            selected_active = selected_residue & active
            residue_observations[residue] += np.count_nonzero(
                selected_residue
            )
            residue_exact[residue] += np.count_nonzero(
                exact & selected_residue
            )
            residue_active_observations[residue] += np.count_nonzero(
                selected_active
            )
            residue_active_exact[residue] += np.count_nonzero(
                exact & selected_active
            )
        maximum_error = max(
            maximum_error,
            int(np.abs(error).max(initial=0)),
        )
        unique, counts = np.unique(error, return_counts=True)
        for value, count in zip(unique, counts, strict=True):
            key = int(value)
            signed_errors[key] = signed_errors.get(key, 0) + int(count)
        failed = np.flatnonzero(trace_active & ~trace_exact) + start
        if failed.size > failed_samples_per_chunk:
            selected = np.linspace(
                0,
                failed.size - 1,
                failed_samples_per_chunk,
                dtype=np.int64,
            )
            failed = failed[selected]
        failed_indices.extend(int(index) for index in failed)

    if len(failed_indices) > feasibility_samples:
        selected = np.linspace(
            0,
            len(failed_indices) - 1,
            feasibility_samples,
            dtype=np.int64,
        )
        failed_indices = [failed_indices[index] for index in selected]
    feasibility = [
        {
            "observationIndex": index,
            **minimum_nearest_affine_slack(traces[:, index]),
        }
        for index in failed_indices
    ]
    feasible = sum(
        bool(record["closedNearestIntervalFeasible"])
        for record in feasibility
    )
    return {
        "model": (
            "one continuous affine amplitude trace followed by one "
            "nearest-code quantizer"
        ),
        "observations": int(total_values),
        "exactValueFraction": float(total_exact / total_values),
        "nonzeroActualResponseValueCount": int(active_values),
        "nonzeroActualResponseValueExactFraction": (
            float(active_exact / active_values) if active_values else None
        ),
        "traceCount": int(traces.shape[1]),
        "activeTraceCount": int(active_traces),
        "exactTraceFraction": float(exact_traces / traces.shape[1]),
        "activeTraceExactFraction": (
            float(active_exact_traces / active_traces)
            if active_traces
            else None
        ),
        "maximumAbsoluteErrorCodes": maximum_error,
        "signedErrorCounts": {
            str(value): count
            for value, count in sorted(signed_errors.items())
        },
        "exactByActualOutputCodeModulo8": {
            str(residue): {
                "observations": int(residue_observations[residue]),
                "exactFraction": (
                    float(
                        residue_exact[residue]
                        / residue_observations[residue]
                    )
                    if residue_observations[residue]
                    else None
                ),
                "nonzeroActualResponseValueCount": int(
                    residue_active_observations[residue]
                ),
                "nonzeroActualResponseValueExactFraction": (
                    float(
                        residue_active_exact[residue]
                        / residue_active_observations[residue]
                    )
                    if residue_active_observations[residue]
                    else None
                ),
            }
            for residue in range(8)
        },
        "failedTraceFeasibilitySample": {
            "sampled": len(feasibility),
            "closedNearestIntervalFeasible": feasible,
            "infeasible": len(feasibility) - feasible,
            "records": feasibility,
        },
    }


def fit_impulse_kernel(
    codes: NDArray[np.uint8],
    source_unit_vectors: IntArray,
    selected_sites: BoolArray,
) -> FloatArray:
    if (
        codes.ndim != 4
        or codes.shape[0] != len(AMPLITUDES) + 1
        or codes.shape[1] != source_unit_vectors.shape[0]
        or codes.shape[3] != 3
        or source_unit_vectors.shape[1:] != (3,)
        or selected_sites.shape != (codes.shape[1],)
        or np.count_nonzero(selected_sites) < 3
    ):
        raise ValueError("invalid impulse-kernel fit inputs")
    amplitudes = np.arange(codes.shape[0], dtype=np.float64)
    design = (
        amplitudes[:, np.newaxis, np.newaxis]
        * source_unit_vectors[selected_sites][np.newaxis]
    ).reshape(-1, 3)
    baseline = codes[0, selected_sites].astype(np.float64)
    target = (
        codes[:, selected_sites].astype(np.float64)
        - baseline[np.newaxis]
    ).reshape(design.shape[0], -1)
    if np.linalg.matrix_rank(design) < 3:
        raise ValueError("impulse-kernel design is not full rank")
    return np.linalg.lstsq(design, target, rcond=None)[0]


def kernel_prediction_report(
    codes: NDArray[np.uint8],
    source_unit_vectors: IntArray,
    fold_ids: IntArray,
) -> JsonObject:
    if (
        codes.ndim != 4
        or codes.shape[1] != source_unit_vectors.shape[0]
        or fold_ids.shape != (codes.shape[1],)
    ):
        raise ValueError("invalid kernel cross-validation inputs")
    counts: dict[str, dict[str, int]] = {
        mode: {
            "values": 0,
            "exactValues": 0,
            "nonzeroValues": 0,
            "exactNonzeroValues": 0,
            "traces": 0,
            "activeTraces": 0,
            "exactTraces": 0,
            "exactActiveTraces": 0,
            "maximumError": 0,
        }
        for mode in OUTPUT_QUANTIZERS
    }
    fold_records = []
    amplitudes = np.arange(codes.shape[0], dtype=np.float64)

    for fold in sorted(int(value) for value in np.unique(fold_ids)):
        test_sites = fold_ids == fold
        train_sites = ~test_sites
        coefficients = fit_impulse_kernel(
            codes,
            source_unit_vectors,
            train_sites,
        )
        design = (
            amplitudes[:, np.newaxis, np.newaxis]
            * source_unit_vectors[test_sites][np.newaxis]
        ).reshape(-1, 3)
        continuous_response = (design @ coefficients).reshape(
            codes.shape[0],
            np.count_nonzero(test_sites),
            codes.shape[2],
            3,
        )
        baseline = codes[0, test_sites].astype(np.float64)
        actual = codes[:, test_sites].astype(np.int64)
        actual_response = actual - actual[0:1]
        active_values = actual_response != 0
        active_traces = np.any(active_values, axis=0)
        modes: JsonObject = {}
        for mode in OUTPUT_QUANTIZERS:
            predicted = quantize_output(
                baseline[np.newaxis] + continuous_response,
                mode,
            )
            error = predicted - actual
            exact = error == 0
            exact_traces = np.all(exact, axis=0)
            record = counts[mode]
            record["values"] += error.size
            record["exactValues"] += int(np.count_nonzero(exact))
            record["nonzeroValues"] += int(np.count_nonzero(active_values))
            record["exactNonzeroValues"] += int(
                np.count_nonzero(exact & active_values)
            )
            record["traces"] += exact_traces.size
            record["activeTraces"] += int(np.count_nonzero(active_traces))
            record["exactTraces"] += int(np.count_nonzero(exact_traces))
            record["exactActiveTraces"] += int(
                np.count_nonzero(exact_traces & active_traces)
            )
            record["maximumError"] = max(
                record["maximumError"],
                int(np.abs(error).max(initial=0)),
            )
            modes[mode] = {
                "exactValueFraction": float(np.count_nonzero(exact))
                / error.size,
                "maximumAbsoluteErrorCodes": int(
                    np.abs(error).max(initial=0)
                ),
            }
        fold_records.append(
            {
                "fold": fold,
                "trainingSites": int(np.count_nonzero(train_sites)),
                "holdoutSites": int(np.count_nonzero(test_sites)),
                "trainingSourceRank": int(
                    np.linalg.matrix_rank(
                        source_unit_vectors[train_sites]
                    )
                ),
                "modes": modes,
            }
        )

    aggregate: JsonObject = {}
    for mode, record in counts.items():
        aggregate[mode] = {
            "observations": record["values"],
            "exactValueFraction": float(
                record["exactValues"] / record["values"]
            ),
            "nonzeroActualResponseValueCount": record["nonzeroValues"],
            "nonzeroActualResponseValueExactFraction": (
                float(
                    record["exactNonzeroValues"]
                    / record["nonzeroValues"]
                )
                if record["nonzeroValues"]
                else None
            ),
            "traceCount": record["traces"],
            "activeTraceCount": record["activeTraces"],
            "exactTraceFraction": float(
                record["exactTraces"] / record["traces"]
            ),
            "activeTraceExactFraction": (
                float(
                    record["exactActiveTraces"]
                    / record["activeTraces"]
                )
                if record["activeTraces"]
                else None
            ),
            "maximumAbsoluteErrorCodes": record["maximumError"],
        }
    return {
        "model": (
            "state-specific, translation-invariant 3x3 cross-channel "
            "impulse kernel; three phase folds fit and one phase fold held out"
        ),
        "quantized": aggregate,
        "folds": fold_records,
    }


def state_trace_reports(traces: FixedImpulseTraces) -> JsonObject:
    result: JsonObject = {}
    center_index = int(
        np.flatnonzero(
            (traces.offsets[:, 0] == 0)
            & (traces.offsets[:, 1] == 0)
        )[0]
    )
    for state in range(STATE_THRESHOLDS.size + 1):
        selected = traces.states == state
        if not np.any(selected):
            continue
        values = traces.codes[:, selected, center_index, :].reshape(
            traces.codes.shape[0],
            -1,
        )
        result[str(state)] = {
            "sites": int(np.count_nonzero(selected)),
            "center": quantized_affine_report(
                values,
                feasibility_samples=32,
            ),
        }
    return result


def state_kernel_reports(
    traces: FixedImpulseTraces,
) -> tuple[JsonObject, dict[int, FloatArray]]:
    result: JsonObject = {}
    kernels: dict[int, FloatArray] = {}
    center_index = int(
        np.flatnonzero(
            (traces.offsets[:, 0] == 0)
            & (traces.offsets[:, 1] == 0)
        )[0]
    )
    reduced_y = traces.origins[:, 0] // 2
    reduced_x = traces.origins[:, 1] // 2
    fold_ids = (
        reduced_y + 3 * reduced_x
    ) % KERNEL_FOLDS
    for state in range(STATE_THRESHOLDS.size + 1):
        selected = traces.states == state
        if np.count_nonzero(selected) < 4:
            continue
        state_codes = traces.codes[:, selected]
        state_vectors = traces.source_unit_vectors[selected]
        state_folds = fold_ids[selected]
        if (
            len(np.unique(state_folds)) != KERNEL_FOLDS
            or np.linalg.matrix_rank(state_vectors) < 3
        ):
            continue
        coefficients = fit_impulse_kernel(
            state_codes,
            state_vectors,
            np.ones(state_vectors.shape[0], dtype=np.bool_),
        ).reshape(3, traces.offsets.shape[0], 3)
        kernels[state] = coefficients
        result[str(state)] = {
            "sites": int(np.count_nonzero(selected)),
            "centerCoefficientMatrixInputByOutput": coefficients[
                :,
                center_index,
                :,
            ].tolist(),
            "kernelCoefficientSumsInputByOutput": coefficients.sum(
                axis=1
            ).tolist(),
            "heldPhaseCrossValidation": kernel_prediction_report(
                state_codes,
                state_vectors,
                state_folds,
            ),
        }
    return result, kernels


def reconstruction_bases(
    offsets: IntArray,
    *,
    gaussian_sigma_half_grid: float,
) -> tuple[FloatArray, FloatArray]:
    if (
        offsets.ndim != 2
        or offsets.shape[1:] != (2,)
        or gaussian_sigma_half_grid <= 0.0
    ):
        raise ValueError("invalid reconstruction-basis geometry")
    radius = int(np.abs(offsets).max(initial=0))
    half_size = 4 * radius + 51
    if half_size % 2 == 0:
        half_size += 1
    center = half_size // 2
    impulse = np.zeros((half_size, half_size), dtype=np.float64)
    impulse[center, center] = 1.0
    sharp_full = cv2.resize(
        impulse,
        None,
        fx=2,
        fy=2,
        interpolation=cv2.INTER_LINEAR,
    )
    blurred_half = cv2.GaussianBlur(
        impulse,
        (0, 0),
        sigmaX=gaussian_sigma_half_grid,
        sigmaY=gaussian_sigma_half_grid,
        borderType=cv2.BORDER_CONSTANT,
    )
    blur_full = cv2.resize(
        blurred_half,
        None,
        fx=2,
        fy=2,
        interpolation=cv2.INTER_LINEAR,
    )
    origin = 2 * center
    y = origin + offsets[:, 0]
    x = origin + offsets[:, 1]
    return sharp_full[y, x], blur_full[y, x]


def bilinear_gaussian_core_candidate(
    kernels: dict[int, FloatArray],
    offsets: IntArray,
) -> JsonObject:
    states = sorted(kernels)
    if len(states) < 2:
        raise ValueError("at least two state kernels are required")
    target = np.stack(
        tuple(
            np.mean(
                np.stack(
                    tuple(
                        kernels[state][channel, :, channel]
                        for channel in range(3)
                    )
                ),
                axis=0,
            )
            for state in states
        )
    )
    normalized_state = np.asarray(states, dtype=np.float64) / 12.0
    best: tuple[
        float,
        float,
        float,
        float,
        FloatArray,
    ] | None = None
    for sigma in np.linspace(0.25, 8.0, 311):
        sharp, blur = reconstruction_bases(
            offsets,
            gaussian_sigma_half_grid=float(sigma),
        )
        design = np.stack(
            (
                np.broadcast_to(sharp, target.shape),
                normalized_state[:, np.newaxis] * sharp[np.newaxis],
                np.broadcast_to(blur, target.shape),
                normalized_state[:, np.newaxis] * blur[np.newaxis],
            ),
            axis=2,
        ).reshape(-1, 4)
        coefficients = np.linalg.lstsq(
            design,
            target.reshape(-1),
            rcond=None,
        )[0]
        predicted = (design @ coefficients).reshape(target.shape)
        error = predicted - target
        rmse = float(np.sqrt(np.square(error).mean()))
        mae = float(np.abs(error).mean())
        maximum = float(np.abs(error).max(initial=0))
        score = (rmse, mae, maximum, float(sigma), coefficients)
        if best is None or score[:4] < best[:4]:
            best = score
    assert best is not None
    rmse, mae, maximum, sigma, coefficients = best
    sharp_start = float(coefficients[0])
    sharp_end = float(coefficients[0] + coefficients[1])
    blur_start = float(coefficients[2])
    blur_end = float(coefficients[2] + coefficients[3])
    return {
        "model": (
            "state-linear mixture of exact bilinear 2x reconstruction and "
            "one isotropic Gaussian on the identified half grid"
        ),
        "states": states,
        "gaussianSigmaHalfGridCells": sigma,
        "gaussianSigmaOutputPixels": 2.0 * sigma,
        "weights": {
            "state0": {
                "bilinear": sharp_start,
                "gaussian": blur_start,
                "sum": sharp_start + blur_start,
            },
            "state12": {
                "bilinear": sharp_end,
                "gaussian": blur_end,
                "sum": sharp_end + blur_end,
            },
            "linearDeltaState0To12": {
                "bilinear": float(coefficients[1]),
                "gaussian": float(coefficients[3]),
            },
        },
        "coefficientErrorCodesPerSourceCode": {
            "meanAbsolute": mae,
            "rootMeanSquare": rmse,
            "maximumAbsolute": maximum,
        },
    }


def spatial_color_separability(
    kernels: dict[int, FloatArray],
) -> JsonObject:
    records: JsonObject = {}
    for state, kernel in sorted(kernels.items()):
        if kernel.ndim != 3 or kernel.shape[0] != 3 or kernel.shape[2] != 3:
            raise ValueError("invalid color impulse kernel")
        matrix = kernel.transpose(1, 0, 2).reshape(kernel.shape[1], 9)
        _, singular_values, right = np.linalg.svd(
            matrix,
            full_matrices=False,
        )
        energy = np.square(singular_values)
        color = right[0].reshape(3, 3)
        diagonal_mean = float(np.mean(np.diag(color)))
        if diagonal_mean < 0.0:
            color = -color
            diagonal_mean = -diagonal_mean
        color /= diagonal_mean
        off_diagonal = kernel[
            np.arange(3)[:, np.newaxis],
            :,
            np.asarray(
                tuple(
                    output
                    for input_channel in range(3)
                    for output in range(3)
                    if output != input_channel
                )
            ).reshape(3, 2),
        ]
        records[str(state)] = {
            "spatialTimesColorRankOneEnergyFraction": float(
                energy[0] / energy.sum()
            ),
            "rankTwoEnergyFraction": float(
                energy[:2].sum() / energy.sum()
            ),
            "normalizedColorMatrixInputByOutput": color.tolist(),
            "offDiagonal": {
                "rootMeanSquare": float(
                    np.sqrt(np.square(off_diagonal).mean())
                ),
                "maximumAbsolute": float(
                    np.abs(off_diagonal).max(initial=0)
                ),
            },
        }
    return {
        "model": (
            "one shared spatial kernel multiplied by one 3x3 color matrix"
        ),
        "records": records,
    }


def predict_dense_samples(
    half_grid_source: FloatArray,
    baseline: NDArray[np.uint8],
    grid: SampleGrid,
    states: IntArray,
    kernels: dict[int, FloatArray],
    offsets: IntArray,
) -> FloatArray:
    if (
        half_grid_source.ndim != 3
        or half_grid_source.shape[2] != 3
        or baseline.ndim != 3
        or baseline.shape[2] != 3
        or baseline.shape[0] != 2 * half_grid_source.shape[0]
        or baseline.shape[1] != 2 * half_grid_source.shape[1]
        or grid.y.shape != grid.x.shape
        or states.shape != grid.y.shape
        or offsets.ndim != 2
        or offsets.shape[1:] != (2,)
    ):
        raise ValueError("invalid dense impulse prediction geometry")
    result = baseline[grid.y, grid.x].astype(np.float64)
    source_delta = half_grid_source - 128.0
    for state in sorted(kernels):
        selected_state = states == state
        if not np.any(selected_state):
            continue
        kernel = kernels[state]
        if kernel.shape != (3, offsets.shape[0], 3):
            raise ValueError("kernel and offset geometry differ")
        state_indices = np.flatnonzero(selected_state)
        y = grid.y[state_indices]
        x = grid.x[state_indices]
        for offset_index, (offset_y, offset_x) in enumerate(offsets):
            matching_phase = (
                ((y - offset_y) % 2 == 0)
                & ((x - offset_x) % 2 == 0)
            )
            if not np.any(matching_phase):
                continue
            indices = state_indices[matching_phase]
            source_y = (grid.y[indices] - offset_y) // 2
            source_x = (grid.x[indices] - offset_x) // 2
            if (
                source_y.min(initial=0) < 0
                or source_x.min(initial=0) < 0
                or source_y.max(initial=-1) >= half_grid_source.shape[0]
                or source_x.max(initial=-1) >= half_grid_source.shape[1]
            ):
                raise ValueError("dense prediction samples exceed source")
            result[indices] += (
                source_delta[source_y, source_x]
                @ kernel[:, offset_index, :]
            )
    return result


def dense_aligned_generalization(
    captures: CaptureSet,
    kernels: dict[int, FloatArray],
    offsets: IntArray,
) -> JsonObject:
    baseline = code_image(captures, BASELINE)
    grid = sample_grid(
        baseline.shape[:2],
        margin=DENSE_SAMPLE_MARGIN,
        stride=DENSE_SAMPLE_STRIDE,
    )
    states, eligible = state_masks(
        captures,
        grid,
        guard=STATE_GUARD,
    )[SCENE]
    grid = SampleGrid(y=grid.y[eligible], x=grid.x[eligible])
    states = states[eligible]
    baseline_samples = baseline[grid.y, grid.x].astype(np.int64)
    totals: dict[str, dict[str, int]] = {
        mode: {
            "values": 0,
            "exact": 0,
            "nonzero": 0,
            "exactNonzero": 0,
            "maximum": 0,
        }
        for mode in OUTPUT_QUANTIZERS
    }
    records: JsonObject = {}
    for amplitude in DENSE_VALIDATION_AMPLITUDES:
        background = (
            f"noise-rgb-a{amplitude:03d}-grid2-shift-00-train"
        )
        source = reference_code_image(captures, background)
        half_grid = source[0::2, 0::2].astype(np.float64)
        actual_image = code_image(captures, background)
        actual = actual_image[grid.y, grid.x].astype(np.int64)
        actual_response = actual - baseline_samples
        nonzero = actual_response != 0
        continuous = predict_dense_samples(
            half_grid,
            baseline,
            grid,
            states,
            kernels,
            offsets,
        )
        modes: JsonObject = {}
        for mode in OUTPUT_QUANTIZERS:
            predicted = quantize_output(continuous, mode)
            error = predicted - actual
            exact = error == 0
            total = totals[mode]
            total["values"] += error.size
            total["exact"] += int(np.count_nonzero(exact))
            total["nonzero"] += int(np.count_nonzero(nonzero))
            total["exactNonzero"] += int(
                np.count_nonzero(exact & nonzero)
            )
            total["maximum"] = max(
                total["maximum"],
                int(np.abs(error).max(initial=0)),
            )
            modes[mode] = {
                "observations": int(error.size),
                "exactFraction": float(np.count_nonzero(exact))
                / error.size,
                "nonzeroActualResponseValueCount": int(
                    np.count_nonzero(nonzero)
                ),
                "nonzeroActualResponseValueExactFraction": (
                    float(np.count_nonzero(exact & nonzero))
                    / np.count_nonzero(nonzero)
                    if np.any(nonzero)
                    else None
                ),
                "maximumAbsoluteErrorCodes": int(
                    np.abs(error).max(initial=0)
                ),
            }
        records[str(amplitude)] = modes
    aggregate = {
        mode: {
            "observations": total["values"],
            "exactFraction": float(total["exact"] / total["values"]),
            "nonzeroActualResponseValueCount": total["nonzero"],
            "nonzeroActualResponseValueExactFraction": (
                float(total["exactNonzero"] / total["nonzero"])
                if total["nonzero"]
                else None
            ),
            "maximumAbsoluteErrorCodes": total["maximum"],
        }
        for mode, total in totals.items()
    }
    return {
        "model": (
            "all-site v2.18 empirical impulse kernels convolved over inherited "
            "dense aligned 2x2 RGB fields"
        ),
        "sampledPixels": int(grid.y.size),
        "sampleStridePixels": DENSE_SAMPLE_STRIDE,
        "amplitudesCodes": list(DENSE_VALIDATION_AMPLITUDES),
        "quantized": aggregate,
        "records": records,
    }


def analyze(captures: CaptureSet) -> JsonObject:
    if captures.manifest.get("rigVersion") != RIG_VERSION:
        raise ValueError(
            f"expected rig {RIG_VERSION}, got "
            f"{captures.manifest.get('rigVersion')!r}"
        )
    traces = load_fixed_impulse_traces(captures)
    all_traces = traces.codes.reshape(traces.codes.shape[0], -1)
    kernel_reports, kernels = state_kernel_reports(traces)
    artifact_hash = (
        file_sha256(captures.root) if captures.root.is_file() else None
    )
    return {
        "clearFixedImpulseSchemaVersion": 1,
        "analysisImplementation": {
            "file": "analysis/liquid_glass_clear_fixed_impulse.py",
            "sha256": file_sha256(Path(__file__).resolve()),
            "python": platform.python_version(),
            "numpy": np.__version__,
            "scipy": package_version("scipy"),
            "opencv": cv2.__version__,
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
        "sourceControls": source_control_report(captures),
        "eligibleSites": int(traces.origins.shape[0]),
        "sitesByState": {
            str(state): int(np.count_nonzero(traces.states == state))
            for state in range(STATE_THRESHOLDS.size + 1)
            if np.any(traces.states == state)
        },
        "sourceUnitVectorRank": int(
            np.linalg.matrix_rank(traces.source_unit_vectors)
        ),
        "support": traces.support,
        "allTapAmplitudeTraces": quantized_affine_report(all_traces),
        "centerAmplitudeTracesByState": state_trace_reports(traces),
        "stateKernelModels": kernel_reports,
        "bilinearGaussianCoreCandidate": (
            bilinear_gaussian_core_candidate(
                kernels,
                traces.offsets,
            )
        ),
        "spatialColorSeparability": spatial_color_separability(kernels),
        "denseAlignedGeneralization": dense_aligned_generalization(
            captures,
            kernels,
            traces.offsets,
        ),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Analyze v2.18 fixed-site impulse amplitude traces."
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
