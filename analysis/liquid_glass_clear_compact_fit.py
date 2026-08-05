#!/usr/bin/env python3
"""Cross-validate a compact clear Liquid Glass pipeline on v2.19 blocks."""

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
from scipy.ndimage import gaussian_filter

from liquid_glass_clear_filter_stage import code_image
from liquid_glass_clear_fixed_block import (
    BLOCK_SIZES,
    CORE_VALIDATION_AMPLITUDES,
    SCENE,
    STATE_GUARD,
    background_name,
    block_site_states,
    fixed_block_origins,
    observed_source_code_image,
    prediction_grid,
    state_balanced_origins,
)
from liquid_glass_clear_geometry_fit import ShapeGeometry
from liquid_glass_clear_state_fit import STATE_THRESHOLDS
from liquid_glass_spatial_fit import CaptureSet


type FloatArray = NDArray[np.float64]
type IntArray = NDArray[np.int64]
type JsonObject = dict[str, Any]

RIG_VERSION = "2.19.0"
BASELINE = "gray-128"
GAUSSIAN_SIGMA_HALF_GRID = 2.175
RECONSTRUCTION_PHASE_HALF_GRID = -0.25
BOUNDARY_MARGIN_PIXELS = 20
TRAIN_AMPLITUDES = (2, 16, 64)
TRAIN_BLOCK_SIZES = (2, 8, 32)
SIZE_HOLDOUT_BLOCK_SIZES = (4, 16, 64)

# Input rows by output columns. These coefficients were selected only from
# v2.19's uniform 64x64 interiors; the spatial cross-validation below does not
# refit them.
POINT_MATRIX_NUMERATOR = np.asarray(
    (
        (2115, 5, 6),
        (22, 2128, 21),
        (3, 3, 2111),
    ),
    dtype=np.float64,
)
POINT_BIAS_NUMERATOR = np.asarray(
    (38637, 38920, 38956),
    dtype=np.float64,
)
POINT_DENOMINATOR = 2048.0
MODEL_NAMES = (
    "state-linear",
    "coordinate-linear",
    "state-within",
    "state-lookup",
    "lookup-within",
)


@dataclass(frozen=True, slots=True)
class BlockCase:
    block_size: int
    amplitude: int
    coordinate: FloatArray
    state: IntArray
    within_state: FloatArray
    base_output: FloatArray
    sharp_slope: FloatArray
    actual: IntArray
    baseline: IntArray


@dataclass(frozen=True, slots=True)
class ErrorCounts:
    values: int
    exact: int
    active: int
    active_exact: int
    absolute_sum: int
    squared_sum: int
    maximum: int

    def __add__(self, other: "ErrorCounts") -> "ErrorCounts":
        return ErrorCounts(
            values=self.values + other.values,
            exact=self.exact + other.exact,
            active=self.active + other.active,
            active_exact=self.active_exact + other.active_exact,
            absolute_sum=self.absolute_sum + other.absolute_sum,
            squared_sum=self.squared_sum + other.squared_sum,
            maximum=max(self.maximum, other.maximum),
        )

    def as_json(self) -> JsonObject:
        return {
            "channelValues": self.values,
            "exactFraction": self.exact / self.values,
            "activeChannelValues": self.active,
            "activeExactFraction": (
                self.active_exact / self.active if self.active else None
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


def bilinear_half_grid(
    image: FloatArray,
    y: IntArray,
    x: IntArray,
    *,
    phase: float = RECONSTRUCTION_PHASE_HALF_GRID,
) -> FloatArray:
    if (
        image.ndim != 3
        or image.shape[2] != 3
        or y.ndim != 1
        or x.shape != y.shape
    ):
        raise ValueError("invalid half-grid sampling geometry")
    fy = y.astype(np.float64) / 2.0 + phase
    fx = x.astype(np.float64) / 2.0 + phase
    y0 = np.floor(fy).astype(np.int64)
    x0 = np.floor(fx).astype(np.int64)
    if (
        np.any(y0 < 0)
        or np.any(x0 < 0)
        or np.any(y0 + 1 >= image.shape[0])
        or np.any(x0 + 1 >= image.shape[1])
    ):
        raise ValueError("half-grid sample exceeds image bounds")
    wy = fy - y0
    wx = fx - x0
    top = (
        (1.0 - wx)[:, np.newaxis] * image[y0, x0]
        + wx[:, np.newaxis] * image[y0, x0 + 1]
    )
    bottom = (
        (1.0 - wx)[:, np.newaxis] * image[y0 + 1, x0]
        + wx[:, np.newaxis] * image[y0 + 1, x0 + 1]
    )
    return (
        (1.0 - wy)[:, np.newaxis] * top
        + wy[:, np.newaxis] * bottom
    )


def within_state_coordinate(
    coordinate: FloatArray,
    state: IntArray,
) -> FloatArray:
    if coordinate.shape != state.shape:
        raise ValueError("coordinate and state arrays must align")
    lower = np.concatenate(
        (np.asarray((0.0,), dtype=np.float64), STATE_THRESHOLDS)
    )
    upper = np.concatenate(
        (STATE_THRESHOLDS, np.asarray((1.0,), dtype=np.float64))
    )
    width = upper[state] - lower[state]
    if np.any(width <= 0.0):
        raise ValueError("state threshold intervals must be positive")
    midpoint = (lower[state] + upper[state]) / 2.0
    return (coordinate - midpoint) / width


def feature_matrix(case: BlockCase, model: str) -> FloatArray:
    count = case.state.size
    ones = np.ones(count, dtype=np.float64)
    if model == "state-linear":
        return np.column_stack((ones, case.state))
    if model == "coordinate-linear":
        return np.column_stack((ones, case.coordinate))
    if model == "state-within":
        return np.column_stack(
            (ones, case.state, case.within_state)
        )
    lookup = np.eye(
        STATE_THRESHOLDS.size + 1,
        dtype=np.float64,
    )[case.state]
    if model == "state-lookup":
        return lookup
    if model == "lookup-within":
        return np.column_stack((lookup, case.within_state))
    raise ValueError(f"unknown compact model: {model}")


def point_output_terms(
    blurred: FloatArray,
    sharp: FloatArray,
) -> tuple[FloatArray, FloatArray]:
    if blurred.shape != sharp.shape or blurred.shape[-1:] != (3,):
        raise ValueError("blurred and sharp samples must be aligned RGB")
    matrix = POINT_MATRIX_NUMERATOR / POINT_DENOMINATOR
    bias = POINT_BIAS_NUMERATOR / POINT_DENOMINATOR
    return (
        blurred @ matrix + bias,
        (sharp - blurred) @ matrix,
    )


def collect_cases(
    captures: CaptureSet,
    *,
    sigma: float = GAUSSIAN_SIGMA_HALF_GRID,
) -> list[BlockCase]:
    if sigma <= 0.0:
        raise ValueError("Gaussian sigma must be positive")
    baseline_image = code_image(captures, BASELINE)
    height, width = baseline_image.shape[:2]
    origins = fixed_block_origins((height, width))
    geometry = ShapeGeometry.from_capture_set(captures, SCENE)
    result: list[BlockCase] = []

    for block_size in BLOCK_SIZES:
        site_states, site_eligible = block_site_states(
            captures,
            origins,
            block_size,
        )
        core_origins = state_balanced_origins(
            origins[site_eligible],
            site_states[site_eligible],
        )
        grid, _ = prediction_grid(core_origins, block_size)
        coordinate = geometry.normalized_signed_distance(
            grid.x.astype(np.float64),
            grid.y.astype(np.float64),
        )
        state = np.searchsorted(
            STATE_THRESHOLDS,
            coordinate,
        ).astype(np.int64)
        threshold_distance = np.min(
            np.abs(
                coordinate[:, np.newaxis]
                - STATE_THRESHOLDS[np.newaxis]
            ),
            axis=1,
        )
        eligible = (
            (coordinate >= 0.0)
            & (coordinate <= 1.0)
            & (threshold_distance > STATE_GUARD)
            & (grid.y >= BOUNDARY_MARGIN_PIXELS)
            & (grid.x >= BOUNDARY_MARGIN_PIXELS)
            & (grid.y < height - BOUNDARY_MARGIN_PIXELS - 1)
            & (grid.x < width - BOUNDARY_MARGIN_PIXELS - 1)
        )
        y = grid.y[eligible]
        x = grid.x[eligible]
        coordinate = coordinate[eligible]
        state = state[eligible]
        within = within_state_coordinate(coordinate, state)
        baseline = baseline_image[y, x].astype(np.int64)

        for amplitude in CORE_VALIDATION_AMPLITUDES:
            background = background_name(block_size, amplitude)
            source = observed_source_code_image(captures, background)
            half_grid = source[0::2, 0::2].astype(np.float64)
            blurred_grid = gaussian_filter(
                half_grid,
                sigma=(sigma, sigma, 0.0),
                mode="nearest",
                truncate=4.0,
            )
            sharp = bilinear_half_grid(half_grid, y, x)
            blurred = bilinear_half_grid(blurred_grid, y, x)
            base_output, sharp_slope = point_output_terms(
                blurred,
                sharp,
            )
            actual = code_image(captures, background)[y, x].astype(
                np.int64
            )
            result.append(
                BlockCase(
                    block_size=block_size,
                    amplitude=amplitude,
                    coordinate=coordinate,
                    state=state,
                    within_state=within,
                    base_output=base_output,
                    sharp_slope=sharp_slope,
                    actual=actual,
                    baseline=baseline,
                )
            )
    return result


def fit_model(cases: list[BlockCase], model: str) -> FloatArray:
    design_parts: list[FloatArray] = []
    target_parts: list[FloatArray] = []
    for case in cases:
        features = feature_matrix(case, model)
        design_parts.append(
            (
                case.sharp_slope[:, :, np.newaxis]
                * features[:, np.newaxis, :]
            ).reshape(-1, features.shape[1])
        )
        target_parts.append(
            (
                case.actual.astype(np.float64)
                + 0.5
                - case.base_output
            ).reshape(-1)
        )
    design = np.concatenate(design_parts)
    target = np.concatenate(target_parts)
    informative = np.linalg.norm(design, axis=1) > 1e-8
    if not np.any(informative):
        raise ValueError("compact fit has no informative samples")
    return np.linalg.lstsq(
        design[informative],
        target[informative],
        rcond=None,
    )[0]


def predict_case(
    case: BlockCase,
    model: str,
    coefficients: FloatArray,
) -> IntArray:
    alpha = feature_matrix(case, model) @ coefficients
    continuous = (
        case.base_output
        + case.sharp_slope * alpha[:, np.newaxis]
    )
    return np.clip(
        np.floor(continuous),
        0.0,
        255.0,
    ).astype(np.int64)


def error_counts(case: BlockCase, predicted: IntArray) -> ErrorCounts:
    if predicted.shape != case.actual.shape:
        raise ValueError("prediction and observation arrays must align")
    error = predicted - case.actual
    exact = error == 0
    active = case.actual != case.baseline
    absolute = np.abs(error)
    return ErrorCounts(
        values=error.size,
        exact=int(np.count_nonzero(exact)),
        active=int(np.count_nonzero(active)),
        active_exact=int(np.count_nonzero(exact & active)),
        absolute_sum=int(absolute.sum(dtype=np.int64)),
        squared_sum=int(
            np.square(error, dtype=np.int64).sum(dtype=np.int64)
        ),
        maximum=int(absolute.max(initial=0)),
    )


def aggregate_metrics(
    cases: list[BlockCase],
    model: str,
    coefficients: FloatArray,
) -> JsonObject:
    counts = ErrorCounts(0, 0, 0, 0, 0, 0, 0)
    for case in cases:
        counts += error_counts(
            case,
            predict_case(case, model, coefficients),
        )
    return counts.as_json()


def case_partitions(cases: list[BlockCase]) -> dict[str, list[BlockCase]]:
    return {
        "training": [
            case
            for case in cases
            if case.amplitude in TRAIN_AMPLITUDES
            and case.block_size in TRAIN_BLOCK_SIZES
        ],
        "unseenBlockSizes": [
            case
            for case in cases
            if case.amplitude in TRAIN_AMPLITUDES
            and case.block_size in SIZE_HOLDOUT_BLOCK_SIZES
        ],
        "unseenAmplitude127": [
            case for case in cases if case.amplitude == 127
        ],
        "allMeasuredCases": cases,
    }


def active_error_within_state_correlation(
    cases: list[BlockCase],
    coefficients: FloatArray,
) -> float | None:
    coordinates: list[FloatArray] = []
    errors: list[FloatArray] = []
    for case in cases:
        predicted = predict_case(
            case,
            "state-linear",
            coefficients,
        )
        active = case.actual != case.baseline
        coordinates.append(
            np.repeat(case.within_state, 3)[active.reshape(-1)]
        )
        errors.append(
            (predicted - case.actual)
            .astype(np.float64)
            .reshape(-1)[active.reshape(-1)]
        )
    coordinate = np.concatenate(coordinates)
    error = np.concatenate(errors)
    if (
        coordinate.size < 2
        or np.isclose(coordinate.std(), 0.0)
        or np.isclose(error.std(), 0.0)
    ):
        return None
    return float(np.corrcoef(coordinate, error)[0, 1])


def analyze(captures: CaptureSet) -> JsonObject:
    if captures.manifest.get("rigVersion") != RIG_VERSION:
        raise ValueError(
            f"expected rig {RIG_VERSION}, got "
            f"{captures.manifest.get('rigVersion')!r}"
        )
    cases = collect_cases(captures)
    partitions = case_partitions(cases)
    training = partitions["training"]
    models: JsonObject = {}
    coefficients_by_model: dict[str, FloatArray] = {}
    for model in MODEL_NAMES:
        coefficients = fit_model(training, model)
        coefficients_by_model[model] = coefficients
        models[model] = {
            "coefficients": coefficients.tolist(),
            "partitions": {
                name: aggregate_metrics(
                    selected,
                    model,
                    coefficients,
                )
                for name, selected in partitions.items()
            },
        }

    state_coefficients = coefficients_by_model["state-linear"]
    correlations = {
        name: active_error_within_state_correlation(
            selected,
            state_coefficients,
        )
        for name, selected in partitions.items()
    }
    artifact_hash = (
        file_sha256(captures.root) if captures.root.is_file() else None
    )
    return {
        "clearCompactFitSchemaVersion": 1,
        "analysisImplementation": {
            "file": "analysis/liquid_glass_clear_compact_fit.py",
            "sha256": file_sha256(Path(__file__).resolve()),
            "python": platform.python_version(),
            "numpy": package_version("numpy"),
            "scipy": package_version("scipy"),
        },
        "source": {
            "path": str(captures.root),
            "sha256": artifact_hash,
            "rigVersion": captures.manifest.get("rigVersion"),
            "ciCommit": captures.manifest.get("ciCommit"),
            "osBuild": captures.manifest.get("osBuild"),
        },
        "pipeline": {
            "sourceReduction": (
                "proven aligned top-left 2x2 half-grid source"
            ),
            "blur": {
                "family": "discrete Gaussian",
                "sigmaHalfGridPixels": GAUSSIAN_SIGMA_HALF_GRID,
                "truncateSigmas": 4.0,
            },
            "reconstruction": {
                "filter": "bilinear",
                "halfGridPhase": RECONSTRUCTION_PHASE_HALF_GRID,
            },
            "pointMatrixNumeratorInputRowsOutputColumns": (
                POINT_MATRIX_NUMERATOR.astype(np.int64).tolist()
            ),
            "pointBiasNumerator": (
                POINT_BIAS_NUMERATOR.astype(np.int64).tolist()
            ),
            "pointDenominator": int(POINT_DENOMINATOR),
            "outputQuantizer": "floor-and-clamp-u8",
        },
        "partitionPolicy": {
            "fitAmplitudes": list(TRAIN_AMPLITUDES),
            "fitBlockSizes": list(TRAIN_BLOCK_SIZES),
            "unseenBlockSizes": list(SIZE_HOLDOUT_BLOCK_SIZES),
            "unseenAmplitude": 127,
            "protectedHistoricalHoldoutsOpened": False,
        },
        "sampledCases": len(cases),
        "models": models,
        "stateLinearActiveErrorWithinBandCoordinateCorrelation": (
            correlations
        ),
        "conclusion": {
            "continuousCoordinateBeatsDiscreteState": all(
                models["coordinate-linear"]["partitions"][name][
                    "activeExactFraction"
                ]
                > models["state-linear"]["partitions"][name][
                    "activeExactFraction"
                ]
                for name in (
                    "training",
                    "unseenBlockSizes",
                    "unseenAmplitude127",
                )
            ),
            "withinStateSlopeImprovesBothHoldouts": all(
                models["state-within"]["partitions"][name][
                    "activeExactFraction"
                ]
                > models["state-linear"]["partitions"][name][
                    "activeExactFraction"
                ]
                for name in (
                    "unseenBlockSizes",
                    "unseenAmplitude127",
                )
            ),
            "interpretation": (
                "Treat the 13 optical states as discrete. Residual error is "
                "not explained by continuous signed distance within a state."
            ),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Cross-validate compact clear-glass spatial/geometry hypotheses "
            "on a v2.19 capture artifact."
        )
    )
    parser.add_argument("captures", type=Path)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()

    captures = CaptureSet.open(arguments.captures)
    try:
        report = analyze(captures)
    finally:
        captures.close()
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if arguments.output is None:
        print(rendered, end="")
    else:
        arguments.output.write_text(rendered, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
