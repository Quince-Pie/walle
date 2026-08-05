#!/usr/bin/env python3
"""Cross-validate compact transfer representations from Apple measurements."""

import argparse
import hashlib
import json
import platform
from importlib.metadata import version as package_version
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray
from scipy.interpolate import RegularGridInterpolator


type JsonObject = dict[str, Any]
type FloatArray = NDArray[np.float64]

COMBINATIONS = (
    "dark/clear",
    "light/clear",
    "dark/regular",
    "light/regular",
)
TONE_KNOT_STRIDES = (1, 2, 4, 8, 16, 32)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def error_summary(actual: FloatArray, predicted: FloatArray) -> JsonObject:
    absolute = np.abs(actual - predicted).reshape(-1)
    return {
        "meanAbsoluteCodes": float(absolute.mean()),
        "p95AbsoluteCodes": float(np.percentile(absolute, 95)),
        "maximumAbsoluteCodes": float(absolute.max(initial=0.0)),
    }


def knot_indexes(length: int, stride: int) -> NDArray[np.int64]:
    indexes = np.arange(0, length, stride, dtype=np.int64)
    if indexes[-1] != length - 1:
        indexes = np.append(indexes, length - 1)
    return indexes


def tone_cross_validation(record: JsonObject) -> JsonObject:
    inputs = np.asarray(record["inputCodes"], dtype=np.float64)
    horizontal = np.asarray(
        record["orientationOutputCodes"]["x"],
        dtype=np.float64,
    )
    vertical = np.asarray(
        record["orientationOutputCodes"]["y"],
        dtype=np.float64,
    )
    if inputs.shape != (256,) or horizontal.shape != inputs.shape:
        raise ValueError("dense tone curve must contain 256 horizontal samples")
    if vertical.shape != inputs.shape:
        raise ValueError("dense tone curve must contain 256 vertical samples")

    fits: JsonObject = {}
    for stride in TONE_KNOT_STRIDES:
        indexes = knot_indexes(inputs.size, stride)
        predicted = np.interp(inputs, inputs[indexes], horizontal[indexes])
        direct_holdout_error = np.abs(horizontal - vertical)
        holdout_error = np.abs(predicted - vertical)
        representation_penalty = np.maximum(
            holdout_error - direct_holdout_error,
            0.0,
        )
        fits[str(stride)] = {
            "knotCount": int(indexes.size),
            "trainingApproximation": error_summary(horizontal, predicted),
            "orientationHoldout": error_summary(vertical, predicted),
            "holdoutPenaltyBeyondFullLookup": {
                "meanAbsoluteCodes": float(representation_penalty.mean()),
                "p95AbsoluteCodes": float(np.percentile(representation_penalty, 95)),
                "maximumAbsoluteCodes": float(representation_penalty.max(initial=0.0)),
            },
        }
    return {
        "orientationFloor": error_summary(vertical, horizontal),
        "representations": fits,
        "selected": {
            "knotStride": 1,
            "knotCount": 256,
            "reason": (
                "zero representation error; compression is not permitted "
                "under the no-quality-regression policy"
            ),
        },
    }


def color_cube(
    *,
    levels: FloatArray,
    inputs: FloatArray,
    outputs: FloatArray,
) -> FloatArray:
    expected = levels.size**3
    if inputs.shape != (expected, 3) or outputs.shape != (expected, 3):
        raise ValueError(
            f"dense color grid must contain {expected} RGB input/output samples"
        )
    cube = np.empty((levels.size, levels.size, levels.size, 3), dtype=np.float64)
    index_for_code = {float(code): index for index, code in enumerate(levels)}
    occupied = np.zeros(cube.shape[:3], dtype=np.bool_)
    for source, target in zip(inputs, outputs, strict=True):
        try:
            red, green, blue = (
                index_for_code[float(source[channel])] for channel in range(3)
            )
        except KeyError as error:
            raise ValueError(f"color input is outside grid: {source}") from error
        if occupied[red, green, blue]:
            raise ValueError(f"duplicate dense color input: {source}")
        cube[red, green, blue] = target
        occupied[red, green, blue] = True
    if not occupied.all():
        raise ValueError("dense color grid is incomplete")
    return cube


def interpolate_color_grid(
    *,
    levels: FloatArray,
    inputs: FloatArray,
    outputs: FloatArray,
    query: FloatArray,
) -> FloatArray:
    cube = color_cube(levels=levels, inputs=inputs, outputs=outputs)
    interpolator = RegularGridInterpolator(
        (levels, levels, levels),
        cube,
        method="linear",
        bounds_error=True,
    )
    return np.asarray(interpolator(query), dtype=np.float64)


def color_cross_validation(
    *,
    levels: FloatArray,
    inputs: FloatArray,
    outputs: FloatArray,
    tone_curve: FloatArray,
) -> JsonObject:
    if tone_curve.shape != (256,):
        raise ValueError("tone curve must contain 256 samples")
    training_indexes = np.arange(0, levels.size, 2, dtype=np.int64)
    if training_indexes[-1] != levels.size - 1:
        training_indexes = np.append(training_indexes, levels.size - 1)
    training_levels = levels[training_indexes]
    input_indexes = np.searchsorted(levels, inputs)
    withheld = np.any(input_indexes % 2 == 1, axis=1)
    actual = outputs[withheld]

    def interpolate(values: FloatArray) -> FloatArray:
        cube = color_cube(levels=levels, inputs=inputs, outputs=values)
        training_outputs = cube[
            np.ix_(
                training_indexes,
                training_indexes,
                training_indexes,
            )
        ]
        training_inputs = np.asarray(
            [
                [red, green, blue]
                for red in training_levels
                for green in training_levels
                for blue in training_levels
            ],
            dtype=np.float64,
        )
        return interpolate_color_grid(
            levels=training_levels,
            inputs=training_inputs,
            outputs=training_outputs.reshape(-1, 3),
            query=inputs[withheld],
        )

    direct_prediction = interpolate(outputs)
    tone_base = np.interp(
        inputs.reshape(-1),
        np.arange(256, dtype=np.float64),
        tone_curve,
    ).reshape(inputs.shape)
    residual = outputs - tone_base
    residual_prediction = interpolate(residual)
    hybrid_prediction = tone_base[withheld] + residual_prediction
    return {
        "trainingGridLevels": training_levels.tolist(),
        "trainingSamples": int(training_indexes.size**3),
        "withheldSamples": int(withheld.sum()),
        "directTrilinear": {
            "withheldError": error_summary(actual, direct_prediction),
        },
        "tonePlusTrilinearResidual": {
            "withheldError": error_summary(actual, hybrid_prediction),
            "fullGridResidualRangeCodes": {
                "minimum": float(residual.min()),
                "maximum": float(residual.max()),
            },
        },
        "fullGridRepresentation": {
            "gridLevels": levels.tolist(),
            "samples": int(levels.size**3),
            "trainingErrorCodes": {
                "meanAbsoluteCodes": 0.0,
                "p95AbsoluteCodes": 0.0,
                "maximumAbsoluteCodes": 0.0,
            },
        },
    }


def independent_color_validation(
    *,
    dense_levels: FloatArray,
    dense_inputs: FloatArray,
    dense_outputs: FloatArray,
    tone_curve: FloatArray,
    validation_inputs: FloatArray,
    validation_outputs: FloatArray,
) -> JsonObject:
    direct = interpolate_color_grid(
        levels=dense_levels,
        inputs=dense_inputs,
        outputs=dense_outputs,
        query=validation_inputs,
    )
    dense_tone_base = np.interp(
        dense_inputs.reshape(-1),
        np.arange(256, dtype=np.float64),
        tone_curve,
    ).reshape(dense_inputs.shape)
    validation_tone_base = np.interp(
        validation_inputs.reshape(-1),
        np.arange(256, dtype=np.float64),
        tone_curve,
    ).reshape(validation_inputs.shape)
    residual = dense_outputs - dense_tone_base
    residual_prediction = interpolate_color_grid(
        levels=dense_levels,
        inputs=dense_inputs,
        outputs=residual,
        query=validation_inputs,
    )
    hybrid = validation_tone_base + residual_prediction
    on_grid = np.all(np.isin(validation_inputs, dense_levels), axis=1)
    achromatic = np.all(
        validation_inputs == validation_inputs[:, :1],
        axis=1,
    )

    def subset(mask: NDArray[np.bool_]) -> JsonObject:
        if not mask.any():
            return {
                "samples": 0,
                "directTrilinearError": None,
                "tonePlusTrilinearResidualError": None,
            }
        return {
            "samples": int(mask.sum()),
            "directTrilinearError": error_summary(
                validation_outputs[mask],
                direct[mask],
            ),
            "tonePlusTrilinearResidualError": error_summary(
                validation_outputs[mask],
                hybrid[mask],
            ),
        }

    return {
        "all": subset(np.ones(validation_inputs.shape[0], dtype=np.bool_)),
        "onGrid": subset(on_grid),
        "offGrid": subset(~on_grid),
        "offGridCrossChannel": subset(~on_grid & ~achromatic),
    }


def context_repeat_validation(
    *,
    fitting_inputs: FloatArray,
    fitting_outputs: FloatArray,
    repeat_inputs: FloatArray,
    repeat_outputs: FloatArray,
) -> JsonObject:
    if fitting_inputs.shape != repeat_inputs.shape:
        raise ValueError("context repeat and fitting chart sizes differ")
    fitting_by_input = {
        tuple(source.tolist()): target
        for source, target in zip(
            fitting_inputs,
            fitting_outputs,
            strict=True,
        )
    }
    if len(fitting_by_input) != fitting_inputs.shape[0]:
        raise ValueError("fitting chart inputs are not unique")
    repeat_keys = [tuple(source.tolist()) for source in repeat_inputs]
    if len(set(repeat_keys)) != repeat_inputs.shape[0]:
        raise ValueError("context-repeat chart inputs are not unique")
    try:
        reordered = np.asarray(
            [fitting_by_input[key] for key in repeat_keys],
            dtype=np.float64,
        )
    except KeyError as error:
        raise ValueError(
            f"context-repeat chart contains a different input: {error.args[0]}"
        ) from error
    return {
        "samples": int(repeat_inputs.shape[0]),
        "sameInputDifferentPositionAndNeighborhoodError": error_summary(
            repeat_outputs,
            reordered,
        ),
    }


def fit_report(measurements: JsonObject) -> JsonObject:
    dense_tone = measurements.get("denseToneTransfer")
    dense_color = measurements.get("denseColorTransfer")
    if not isinstance(dense_tone, dict) or not dense_tone.get("available"):
        raise ValueError("dense tone measurements are unavailable")
    if not isinstance(dense_color, dict) or not dense_color.get("available"):
        raise ValueError("dense color measurements are unavailable")

    levels = np.asarray(dense_color["gridLevels"], dtype=np.float64)
    inputs = np.asarray(dense_color["inputCodes"], dtype=np.float64)
    if levels.shape != (9,):
        raise ValueError("dense color transfer must use nine grid levels")

    tone: JsonObject = {}
    color: JsonObject = {}
    sparse_validation: JsonObject = {}
    dense_holdout_validation: JsonObject = {}
    context_validation: JsonObject = {}
    context_holdout_validation: JsonObject = {}
    holdout_context_validation: JsonObject = {}
    sparse = measurements.get("sparseColorTransfer")
    dense_holdout = measurements.get("denseColorHoldout")
    context_repeat = measurements.get("denseColorContextRepeat")
    context_holdout = measurements.get("denseColorContextHoldout")
    holdout_context_repeat = measurements.get("denseColorHoldoutContextRepeat")
    context_training = measurements.get("denseColorContextTraining")
    holdout_context_training = measurements.get("denseColorHoldoutContextTraining")
    stochastic_probes = measurements.get("stochasticProbeStatistics")
    for combination in COMBINATIONS:
        tone_record = dense_tone.get(combination)
        color_record = dense_color.get(combination)
        if not isinstance(tone_record, dict):
            raise ValueError(f"missing tone measurements for {combination}")
        if not isinstance(color_record, dict):
            raise ValueError(f"missing color measurements for {combination}")
        tone[combination] = tone_cross_validation(tone_record)
        color[combination] = color_cross_validation(
            levels=levels,
            inputs=inputs,
            outputs=np.asarray(color_record["outputCodes"], dtype=np.float64),
            tone_curve=np.asarray(
                tone_record["outputCodes"],
                dtype=np.float64,
            ),
        )
        if isinstance(sparse, dict):
            sparse_record = sparse.get(combination)
            if (
                isinstance(sparse_record, dict)
                and "inputCodes" in sparse_record
                and "outputCodes" in sparse_record
            ):
                sparse_validation[combination] = independent_color_validation(
                    dense_levels=levels,
                    dense_inputs=inputs,
                    dense_outputs=np.asarray(
                        color_record["outputCodes"],
                        dtype=np.float64,
                    ),
                    tone_curve=np.asarray(
                        tone_record["outputCodes"],
                        dtype=np.float64,
                    ),
                    validation_inputs=np.asarray(
                        sparse_record["inputCodes"],
                        dtype=np.float64,
                    ),
                    validation_outputs=np.asarray(
                        sparse_record["outputCodes"],
                        dtype=np.float64,
                    ),
                )
        if isinstance(dense_holdout, dict) and dense_holdout.get("available"):
            holdout_record = dense_holdout.get(combination)
            if not isinstance(holdout_record, dict):
                raise ValueError(f"missing dense holdout for {combination}")
            holdout_levels = np.arange(16, 241, 32, dtype=np.float64)
            holdout_inputs = np.asarray(
                dense_holdout["inputCodes"],
                dtype=np.float64,
            )
            holdout_outputs = np.asarray(
                holdout_record["outputCodes"],
                dtype=np.float64,
            )
            color_cube(
                levels=holdout_levels,
                inputs=holdout_inputs,
                outputs=holdout_outputs,
            )
            dense_holdout_validation[combination] = independent_color_validation(
                dense_levels=levels,
                dense_inputs=inputs,
                dense_outputs=np.asarray(
                    color_record["outputCodes"],
                    dtype=np.float64,
                ),
                tone_curve=np.asarray(
                    tone_record["outputCodes"],
                    dtype=np.float64,
                ),
                validation_inputs=holdout_inputs,
                validation_outputs=holdout_outputs,
            )
        if isinstance(context_repeat, dict) and context_repeat.get("available"):
            repeat_record = context_repeat.get(combination)
            if not isinstance(repeat_record, dict):
                raise ValueError(f"missing context repeat for {combination}")
            context_validation[combination] = context_repeat_validation(
                fitting_inputs=inputs,
                fitting_outputs=np.asarray(
                    color_record["outputCodes"],
                    dtype=np.float64,
                ),
                repeat_inputs=np.asarray(
                    context_repeat["inputCodes"],
                    dtype=np.float64,
                ),
                repeat_outputs=np.asarray(
                    repeat_record["outputCodes"],
                    dtype=np.float64,
                ),
            )
        if isinstance(context_holdout, dict) and context_holdout.get("available"):
            holdout_record = context_holdout.get(combination)
            if not isinstance(holdout_record, dict):
                raise ValueError(f"missing context holdout for {combination}")
            context_holdout_validation[combination] = context_repeat_validation(
                fitting_inputs=inputs,
                fitting_outputs=np.asarray(
                    color_record["outputCodes"],
                    dtype=np.float64,
                ),
                repeat_inputs=np.asarray(
                    context_holdout["inputCodes"],
                    dtype=np.float64,
                ),
                repeat_outputs=np.asarray(
                    holdout_record["outputCodes"],
                    dtype=np.float64,
                ),
            )
        if (
            isinstance(dense_holdout, dict)
            and dense_holdout.get("available")
            and isinstance(holdout_context_repeat, dict)
            and holdout_context_repeat.get("available")
        ):
            ordered_record = dense_holdout.get(combination)
            repeat_record = holdout_context_repeat.get(combination)
            if not isinstance(ordered_record, dict) or not isinstance(
                repeat_record, dict
            ):
                raise ValueError(f"missing off-grid context repeat for {combination}")
            holdout_context_validation[combination] = context_repeat_validation(
                fitting_inputs=np.asarray(
                    dense_holdout["inputCodes"],
                    dtype=np.float64,
                ),
                fitting_outputs=np.asarray(
                    ordered_record["outputCodes"],
                    dtype=np.float64,
                ),
                repeat_inputs=np.asarray(
                    holdout_context_repeat["inputCodes"],
                    dtype=np.float64,
                ),
                repeat_outputs=np.asarray(
                    repeat_record["outputCodes"],
                    dtype=np.float64,
                ),
            )

    clear_shared = (
        dense_tone["dark/clear"]["outputCodes"]
        == dense_tone["light/clear"]["outputCodes"]
        and dense_color["dark/clear"]["outputCodes"]
        == dense_color["light/clear"]["outputCodes"]
    )
    unique_transfer_count = 3 if clear_shared else 4
    tone_bytes = unique_transfer_count * 256 * 4
    color_bytes = unique_transfer_count * 9**3 * 3 * 4
    independent_cross_channel_samples = max(
        (
            int(record["offGridCrossChannel"]["samples"])
            for record in sparse_validation.values()
        ),
        default=0,
    )
    dense_holdout_samples = (
        int(dense_holdout.get("sampleCount", 0))
        if (isinstance(dense_holdout, dict) and dense_holdout.get("available"))
        else 0
    )
    context_repeat_samples = (
        int(context_repeat.get("sampleCount", 0))
        if (isinstance(context_repeat, dict) and context_repeat.get("available"))
        else 0
    )
    context_holdout_samples = (
        int(context_holdout.get("sampleCount", 0))
        if (isinstance(context_holdout, dict) and context_holdout.get("available"))
        else 0
    )
    holdout_context_repeat_samples = (
        int(holdout_context_repeat.get("sampleCount", 0))
        if (
            isinstance(holdout_context_repeat, dict)
            and holdout_context_repeat.get("available")
        )
        else 0
    )
    context_training_charts = (
        int(context_training.get("availableChartCount", 0))
        if isinstance(context_training, dict)
        else 0
    )
    holdout_context_training_charts = (
        int(holdout_context_training.get("availableChartCount", 0))
        if isinstance(holdout_context_training, dict)
        else 0
    )
    stochastic_probe_count = (
        int(stochastic_probes.get("availableProbeCount", 0))
        if isinstance(stochastic_probes, dict)
        else 0
    )
    context_training_complete = (
        isinstance(context_training, dict)
        and context_training.get("available") is True
        and context_training.get("requiredChartCount") == 4
        and context_training_charts == 4
    )
    holdout_context_training_complete = (
        isinstance(holdout_context_training, dict)
        and holdout_context_training.get("available") is True
        and holdout_context_training.get("requiredChartCount") == 4
        and holdout_context_training_charts == 4
    )
    stochastic_probes_complete = (
        isinstance(stochastic_probes, dict)
        and stochastic_probes.get("available") is True
        and stochastic_probes.get("requiredProbeCount") == 8
        and stochastic_probe_count == 8
    )
    pointwise_regular_maximum = max(
        (
            float(
                sparse_validation[combination]["onGrid"]["directTrilinearError"][
                    "maximumAbsoluteCodes"
                ]
            )
            for combination in ("dark/regular", "light/regular")
            if combination in sparse_validation
        ),
        default=0.0,
    )
    context_regular_maximum = max(
        (
            float(
                record[combination]["sameInputDifferentPositionAndNeighborhoodError"][
                    "maximumAbsoluteCodes"
                ]
            )
            for record in (
                context_validation,
                context_holdout_validation,
                holdout_context_validation,
            )
            for combination in ("dark/regular", "light/regular")
            if combination in record
        ),
        default=0.0,
    )
    phase_response = measurements.get("phaseResponse")
    giant_phase = (
        phase_response.get("scenes", {}).get("circle-4000-center", {})
        if isinstance(phase_response, dict)
        else {}
    )
    expected_phase_periods = {"32", "64", "128", "256", "512", "1024"}
    regular_giant_phase_complete = all(
        expected_phase_periods
        <= set(
            giant_phase.get(combination, {}).get(axis, {})
            if isinstance(giant_phase, dict)
            else {}
        )
        for combination in ("dark/regular", "light/regular")
        for axis in ("x", "y")
    )
    spatial_capture_coverage_complete = (
        dense_holdout_samples == 512
        and context_repeat_samples == 729
        and context_holdout_samples == 729
        and holdout_context_repeat_samples == 512
        and regular_giant_phase_complete
    )
    model_identification_coverage_complete = (
        spatial_capture_coverage_complete
        and context_training_complete
        and holdout_context_training_complete
        and stochastic_probes_complete
    )
    pointwise_rejected_maximum = max(
        pointwise_regular_maximum,
        context_regular_maximum,
    )
    return {
        "fitSchemaVersion": 3,
        "analysisImplementation": {
            "file": "analysis/liquid_glass_transfer_fit.py",
            "sha256": file_sha256(Path(__file__).resolve()),
            "python": platform.python_version(),
            "numpy": package_version("numpy"),
            "scipy": package_version("scipy"),
        },
        "source": {
            "artifact": measurements.get("artifact"),
            "analysisImplementation": measurements.get("analysisImplementation"),
        },
        "tone": tone,
        "color": color,
        "existingSparseValidation": sparse_validation,
        "denseOffGridValidation": (
            {
                "available": True,
                "samples": dense_holdout_samples,
                "combinations": dense_holdout_validation,
            }
            if dense_holdout_samples
            else {
                "available": False,
                "reason": ("artifact predates v2.8 color-cube-holdout-8 captures"),
            }
        ),
        "contextRepeatValidation": (
            {
                "available": True,
                "samples": context_repeat_samples,
                "combinations": context_validation,
            }
            if context_repeat_samples
            else {
                "available": False,
                "reason": ("artifact predates v2.8 color-cube-9-permuted captures"),
            }
        ),
        "contextHoldoutValidation": (
            {
                "available": True,
                "samples": context_holdout_samples,
                "combinations": context_holdout_validation,
            }
            if context_holdout_samples
            else {
                "available": False,
                "reason": (
                    "artifact predates the v2.9 independently shuffled "
                    "color-cube context"
                ),
            }
        ),
        "offGridContextRepeatValidation": (
            {
                "available": True,
                "samples": holdout_context_repeat_samples,
                "combinations": holdout_context_validation,
            }
            if holdout_context_repeat_samples
            else {
                "available": False,
                "reason": (
                    "artifact predates the v2.9 independently shuffled off-grid context"
                ),
            }
        ),
        "captureSufficiency": {
            "existingSparseOffGridCrossChannelSamples": (
                independent_cross_channel_samples
            ),
            "denseOffGridCrossChannelSamples": dense_holdout_samples,
            "contextRepeatSamples": context_repeat_samples,
            "contextHoldoutSamples": context_holdout_samples,
            "offGridContextRepeatSamples": holdout_context_repeat_samples,
            "randomizedOnGridTrainingContexts": context_training_charts,
            "randomizedOffGridTrainingContexts": (holdout_context_training_charts),
            "smallSignalStochasticProbes": stochastic_probe_count,
            "regularGiantPhasePeriodsComplete": regular_giant_phase_complete,
            "spatialCaptureCoverageComplete": spatial_capture_coverage_complete,
            "modelIdentificationCoverageComplete": (
                model_identification_coverage_complete
            ),
            "pointwiseColorLutRejectedByMaximumCodes": (pointwise_rejected_maximum),
            "pointwiseColorLutRejected": pointwise_rejected_maximum > 0,
            "colorTransferCertificationReady": False,
            "reason": (
                (
                    "capture coverage is complete, but no context-aware model "
                    "has passed both independent spatial holdouts"
                    if model_identification_coverage_complete
                    else (
                        "v2.10 randomized training contexts and small-signal "
                        "gray/RGB stochastic train/holdout probes are required"
                    )
                )
                if spatial_capture_coverage_complete
                else (
                    "v2.9 independently shuffled fitting/off-grid contexts and "
                    "the complete giant-circle regular MTF are still required"
                )
            ),
        },
        "representation": {
            "clearTransferSharedAcrossAppearances": clear_shared,
            "uniqueAppearanceMaterialTransfers": unique_transfer_count,
            "fullToneLut": {
                "format": "R32F",
                "dimensions": [256, 1],
                "bytes": tone_bytes,
            },
            "fullColorObservationTable": {
                "format": "RGB32F",
                "dimensions": [9, 9, 9],
                "bytes": color_bytes,
                "deployableAsPointwiseLut": False,
                "reason": (
                    "regular flat-field and chart observations disagree; "
                    "geometry, neighborhood, and/or position must be "
                    "disambiguated first"
                ),
            },
            "combinedBytes": tone_bytes + color_bytes,
            "policy": (
                "retain full observations; never deploy them as a pointwise "
                "LUT until context dependence is explained and held out"
            ),
        },
        "sparseAffineDiagnostic": {
            combination: {
                key: value
                for key, value in record.items()
                if key in {"model", "sampleCount", "fitErrorCodes"}
            }
            for combination, record in (
                sparse.items() if isinstance(sparse, dict) else ()
            )
            if isinstance(record, dict)
        },
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Cross-validate Apple Liquid Glass transfer tables.",
    )
    parser.add_argument("measurements", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    measurements = json.loads(args.measurements.read_text(encoding="utf-8"))
    if not isinstance(measurements, dict):
        raise ValueError("measurement report must be a JSON object")
    report = fit_report(measurements)
    report["source"].update(
        {
            "measurementReportFile": args.measurements.name,
            "measurementReportSha256": file_sha256(args.measurements),
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
