#!/usr/bin/env python3
"""Test exact point-stage hypotheses on uniform v2.19 clear-glass blocks."""

import argparse
import hashlib
import json
import platform
from collections import defaultdict
from dataclasses import dataclass
from importlib.metadata import version as package_version
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray
from scipy.optimize import linprog

from liquid_glass_clear_filter_stage import code_image
from liquid_glass_clear_fixed_block import (
    AMPLITUDES,
    BASELINE,
    background_name,
    block_site_states,
    fixed_block_origins,
    observed_source_code_image,
)
from liquid_glass_spatial_fit import CaptureSet


type FloatArray = NDArray[np.float64]
type IntArray = NDArray[np.int64]
type JsonObject = dict[str, Any]

RIG_VERSION = "2.19.0"
UNIFORM_BLOCK_SIZE = 64
UNIFORM_CENTER_OFFSETS = np.asarray(
    ((31, 31), (31, 32), (32, 31), (32, 32)),
    dtype=np.int64,
)
INTERVAL_EPSILON = 1e-10

# Input rows by output columns. This compact candidate was selected from the
# same uniform-color population; it is reported as a baseline, not accepted
# as an exact model.
POINT_MATRIX_NUMERATOR = np.asarray(
    (
        (2115, 5, 6),
        (22, 2128, 21),
        (3, 3, 2111),
    ),
    dtype=np.int64,
)
POINT_BIAS_NUMERATOR = np.asarray(
    (38637, 38920, 38956),
    dtype=np.int64,
)
POINT_DENOMINATOR = 2048


@dataclass(frozen=True, slots=True)
class UniformSamples:
    inputs: IntArray
    outputs: IntArray
    amplitudes: IntArray
    observations: int
    selected_sites: int
    conflicting_inputs: int
    baseline_codes: IntArray


@dataclass(frozen=True, slots=True)
class IntervalFit:
    coefficients: FloatArray
    minimum_extra_half_width: float


@dataclass(frozen=True, slots=True)
class CategoricalDesign:
    features: FloatArray
    labels: tuple[tuple[int, int], ...]


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def collect_uniform_samples(captures: CaptureSet) -> UniformSamples:
    baseline = code_image(captures, BASELINE)
    origins = fixed_block_origins(baseline.shape[:2])
    _, eligible = block_site_states(
        captures,
        origins,
        UNIFORM_BLOCK_SIZE,
    )
    origins = origins[eligible]
    output_sets: defaultdict[
        tuple[int, int, int],
        set[tuple[int, int, int]],
    ] = defaultdict(set)
    observations = 0

    for amplitude in AMPLITUDES:
        background = background_name(UNIFORM_BLOCK_SIZE, amplitude)
        source = observed_source_code_image(captures, background)
        output = code_image(captures, background)
        for origin_y, origin_x in origins:
            source_code = tuple(
                int(value) for value in source[origin_y, origin_x]
            )
            for offset_y, offset_x in UNIFORM_CENTER_OFFSETS:
                output_sets[source_code].add(
                    tuple(
                        int(value)
                        for value in output[
                            origin_y + offset_y,
                            origin_x + offset_x,
                        ]
                    )
                )
                observations += 1

    conflicting = sum(len(values) != 1 for values in output_sets.values())
    ordered_inputs = sorted(output_sets)
    inputs = np.asarray(ordered_inputs, dtype=np.int64)
    outputs = np.asarray(
        [
            min(output_sets[source_code])
            for source_code in ordered_inputs
        ],
        dtype=np.int64,
    )
    amplitudes = np.max(np.abs(inputs - 128), axis=1)
    baseline_samples = baseline[
        origins[:, 0, np.newaxis] + UNIFORM_CENTER_OFFSETS[:, 0],
        origins[:, 1, np.newaxis] + UNIFORM_CENTER_OFFSETS[:, 1],
    ].reshape(-1, 3)
    baseline_codes = np.unique(baseline_samples, axis=0)
    return UniformSamples(
        inputs=inputs,
        outputs=outputs,
        amplitudes=amplitudes,
        observations=observations,
        selected_sites=int(origins.shape[0]),
        conflicting_inputs=conflicting,
        baseline_codes=baseline_codes.astype(np.int64),
    )


def feature_matrix(inputs: IntArray, family: str) -> FloatArray:
    if inputs.ndim != 2 or inputs.shape[1] != 3:
        raise ValueError("point-stage inputs must have shape (samples, 3)")
    normalized = (inputs.astype(np.float64) - 128.0) / 127.0
    red, green, blue = normalized.T
    linear = (red, green, blue)
    diagonal_quadratic = (
        red * red,
        green * green,
        blue * blue,
    )
    cross_quadratic = (
        red * green,
        red * blue,
        green * blue,
    )
    cubic = (
        red**3,
        green**3,
        blue**3,
        red * red * green,
        red * red * blue,
        green * green * red,
        green * green * blue,
        blue * blue * red,
        blue * blue * green,
        red * green * blue,
    )
    if family == "linear":
        columns = linear
    elif family == "diagonal-quadratic":
        columns = (*linear, *diagonal_quadratic)
    elif family == "full-quadratic":
        columns = (
            *linear,
            *diagonal_quadratic,
            *cross_quadratic,
        )
    elif family == "full-cubic":
        columns = (
            *linear,
            *diagonal_quadratic,
            *cross_quadratic,
            *cubic,
        )
    else:
        raise ValueError(f"unknown point-stage feature family: {family}")
    return np.column_stack(
        (*columns, np.ones(inputs.shape[0], dtype=np.float64))
    )


def categorical_additive_design(inputs: IntArray) -> CategoricalDesign:
    if inputs.ndim != 2 or inputs.shape[1] != 3:
        raise ValueError("point-stage inputs must have shape (samples, 3)")
    columns: list[FloatArray] = []
    labels: list[tuple[int, int]] = []
    for channel in range(3):
        for code in sorted(
            int(value) for value in np.unique(inputs[:, channel])
        ):
            if code == 128:
                continue
            columns.append(
                (inputs[:, channel] == code).astype(np.float64)
            )
            labels.append((channel, code))
    return CategoricalDesign(
        features=np.column_stack(
            (
                *columns,
                np.ones(inputs.shape[0], dtype=np.float64),
            )
        ),
        labels=tuple(labels),
    )


def minimum_interval_fit(
    features: FloatArray,
    outputs: IntArray,
    quantizer: str,
) -> IntervalFit:
    if (
        features.ndim != 2
        or outputs.ndim != 1
        or features.shape[0] != outputs.size
        or not outputs.size
    ):
        raise ValueError("interval-fit samples do not align")
    if np.any((outputs < 0) | (outputs > 255)):
        raise ValueError("interval-fit outputs must be uint8 codes")
    if quantizer == "floor":
        lower = outputs.astype(np.float64)
        upper = lower + 1.0 - INTERVAL_EPSILON
    elif quantizer == "nearest":
        lower = outputs.astype(np.float64) - 0.5
        upper = outputs.astype(np.float64) + 0.5
    else:
        raise ValueError(f"unknown point-stage quantizer: {quantizer}")

    rows: list[FloatArray] = []
    limits: list[float] = []
    for row, value in zip(
        features[outputs < 255],
        upper[outputs < 255],
        strict=True,
    ):
        rows.append(np.concatenate((row, np.asarray((-1.0,)))))
        limits.append(float(value))
    for row, value in zip(
        features[outputs > 0],
        lower[outputs > 0],
        strict=True,
    ):
        rows.append(np.concatenate((-row, np.asarray((-1.0,)))))
        limits.append(float(-value))

    result = linprog(
        np.concatenate(
            (
                np.zeros(features.shape[1], dtype=np.float64),
                np.ones(1, dtype=np.float64),
            )
        ),
        A_ub=np.asarray(rows, dtype=np.float64),
        b_ub=np.asarray(limits, dtype=np.float64),
        bounds=(
            *((None, None) for _ in range(features.shape[1])),
            (0.0, None),
        ),
        method="highs",
    )
    if not result.success:
        raise RuntimeError(
            f"point-stage interval solve failed: {result.message}"
        )
    return IntervalFit(
        coefficients=result.x[:-1],
        minimum_extra_half_width=float(result.x[-1]),
    )


def interval_family_report(
    samples: UniformSamples,
    family: str,
    quantizer: str,
) -> JsonObject:
    features = feature_matrix(samples.inputs, family)
    fits = [
        minimum_interval_fit(
            features,
            samples.outputs[:, channel],
            quantizer,
        )
        for channel in range(3)
    ]
    slacks = [
        fit.minimum_extra_half_width
        for fit in fits
    ]
    return {
        "features": int(features.shape[1]),
        "quantizer": quantizer,
        "minimumExtraHalfWidthCodesByChannel": slacks,
        "allChannelsMathematicallyFeasible": all(
            slack <= 1e-9 for slack in slacks
        ),
        "coefficientsByOutputChannel": [
            fit.coefficients.tolist() for fit in fits
        ],
    }


def prediction_metrics(
    predicted: IntArray,
    actual: IntArray,
) -> JsonObject:
    if predicted.shape != actual.shape:
        raise ValueError("point-stage prediction and actual values differ")
    error = predicted - actual
    absolute = np.abs(error)
    exact = error == 0
    return {
        "channelValues": int(error.size),
        "exactFraction": float(np.count_nonzero(exact)) / error.size,
        "perChannelExactFraction": [
            float(np.count_nonzero(exact[:, channel]))
            / exact.shape[0]
            for channel in range(3)
        ],
        "meanAbsoluteErrorCodes": float(absolute.mean()),
        "maximumAbsoluteErrorCodes": int(absolute.max(initial=0)),
        "missedInputColors": int(
            np.count_nonzero(np.any(error != 0, axis=1))
        ),
    }


def fixed_candidate_report(samples: UniformSamples) -> JsonObject:
    numerator = (
        samples.inputs @ POINT_MATRIX_NUMERATOR
        + POINT_BIAS_NUMERATOR
    )
    predicted = np.clip(
        np.floor_divide(numerator, POINT_DENOMINATOR),
        0,
        255,
    ).astype(np.int64)
    misses = np.any(predicted != samples.outputs, axis=1)
    return {
        "matrixNumeratorInputRowsOutputColumns":
            POINT_MATRIX_NUMERATOR.tolist(),
        "biasNumerator": POINT_BIAS_NUMERATOR.tolist(),
        "denominator": POINT_DENOMINATOR,
        "quantizer": "integer-floor-and-clamp",
        "metrics": prediction_metrics(predicted, samples.outputs),
        "misses": [
            {
                "input": source.tolist(),
                "actual": actual.tolist(),
                "predicted": estimate.tolist(),
            }
            for source, actual, estimate in zip(
                samples.inputs[misses],
                samples.outputs[misses],
                predicted[misses],
                strict=True,
            )
        ],
    }


def categorical_additive_report(samples: UniformSamples) -> JsonObject:
    design = categorical_additive_design(samples.inputs)
    interval_slacks = {
        quantizer: [
            minimum_interval_fit(
                design.features,
                samples.outputs[:, channel],
                quantizer,
            ).minimum_extra_half_width
            for channel in range(3)
        ]
        for quantizer in ("floor", "nearest")
    }
    coefficients = np.linalg.lstsq(
        design.features,
        samples.outputs.astype(np.float64),
        rcond=None,
    )[0]
    continuous = design.features @ coefficients
    predicted = np.clip(
        np.rint(continuous),
        0,
        255,
    ).astype(np.int64)
    rank_one: JsonObject = {}
    for channel in range(3):
        selected = np.asarray(
            [
                label_channel == channel
                for label_channel, _ in design.labels
            ],
            dtype=np.bool_,
        )
        contributions = coefficients[:-1][selected]
        _, singular_values, right = np.linalg.svd(
            contributions,
            full_matrices=False,
        )
        direction = right[0]
        if direction[channel] < 0.0:
            direction = -direction
        direction = direction / direction[channel]
        energy = np.square(singular_values)
        rank_one[str(channel)] = {
            "inputCodes": int(contributions.shape[0]),
            "singularValues": singular_values.tolist(),
            "rankOneEnergyFraction": float(energy[0] / energy.sum()),
            "outputDirectionNormalizedToOwnChannel": (
                direction.tolist()
            ),
        }
    return {
        "model": (
            "one free scalar contribution per observed input-channel code, "
            "summed across the three input channels before final quantization"
        ),
        "features": int(design.features.shape[1]),
        "matrixRank": int(np.linalg.matrix_rank(design.features)),
        "intervalMinimumExtraHalfWidthCodesByChannel": (
            interval_slacks
        ),
        "allChannelsIntervalFeasible": {
            quantizer: all(slack <= 1e-9 for slack in slacks)
            for quantizer, slacks in interval_slacks.items()
        },
        "leastSquaresNearestMetrics": prediction_metrics(
            predicted,
            samples.outputs,
        ),
        "inputChannelContributionRank": rank_one,
        "interpretation": (
            "This flexible categorical fit is a structural diagnostic, not "
            "a deployable LUT. Near-rank-one RGB contributions support "
            "per-channel nonlinear transfer curves followed by one fixed "
            "cross-channel matrix."
        ),
    }


def analyze(captures: CaptureSet) -> JsonObject:
    if captures.manifest.get("rigVersion") != RIG_VERSION:
        raise ValueError(
            f"expected rig {RIG_VERSION}, got "
            f"{captures.manifest.get('rigVersion')!r}"
        )
    samples = collect_uniform_samples(captures)
    if samples.conflicting_inputs:
        raise ValueError(
            "uniform point-stage inputs map to multiple output colors"
        )
    families = (
        "linear",
        "diagonal-quadratic",
        "full-quadratic",
        "full-cubic",
    )
    interval_fits = {
        family: {
            quantizer: interval_family_report(
                samples,
                family,
                quantizer,
            )
            for quantizer in ("floor", "nearest")
        }
        for family in families
    }
    artifact_hash = (
        file_sha256(captures.root) if captures.root.is_file() else None
    )
    return {
        "clearPointStageSchemaVersion": 1,
        "analysisImplementation": {
            "file": "analysis/liquid_glass_clear_point_stage.py",
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
        "sampleDesign": {
            "blockSizePixels": UNIFORM_BLOCK_SIZE,
            "centerOffsets": UNIFORM_CENTER_OFFSETS.tolist(),
            "amplitudesCodes": list(AMPLITUDES),
            "selectedSites": samples.selected_sites,
            "observations": samples.observations,
            "distinctInputColors": int(samples.inputs.shape[0]),
            "conflictingInputColors": samples.conflicting_inputs,
            "baselineOutputColors": samples.baseline_codes.tolist(),
        },
        "intervalFits": interval_fits,
        "categoricalAdditiveDiagnostic": (
            categorical_additive_report(samples)
        ),
        "fixedPointCandidate": fixed_candidate_report(samples),
        "conclusion": {
            "uniformInputHasUniqueOutput": (
                samples.conflicting_inputs == 0
            ),
            "singleAffineCodeSpaceStageRejected": not any(
                interval_fits["linear"][quantizer][
                    "allChannelsMathematicallyFeasible"
                ]
                for quantizer in ("floor", "nearest")
            ),
            "fullCubicCodeSpaceStageRejected": not any(
                interval_fits["full-cubic"][quantizer][
                    "allChannelsMathematicallyFeasible"
                ]
                for quantizer in ("floor", "nearest")
            ),
            "interpretation": (
                "The point transform is deterministic but cannot be one "
                "code-space polynomial through degree three followed by one "
                "final floor or nearest-code quantizer. A nonlinear transfer "
                "or an intermediate discrete stage remains required."
            ),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Test exact point-stage hypotheses on uniform v2.19 "
            "clear-glass blocks."
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
