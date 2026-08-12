#!/usr/bin/env python3
"""Analyze v2.17 clear-glass filter-stage interventions."""

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

from liquid_glass_clear_grid_basis import (
    difference_report,
    signed_counts,
    stratified_difference,
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

RIG_VERSION = "2.17.0"
SCENE = "circle-4000-center"
CONTROL_SCENE = "circle-0500-center"
BASELINE = "gray-128"
RAMP_FORWARD = "clear-stage-grid2-ramp-forward"
RAMP_REVERSE = "clear-stage-grid2-ramp-reverse"
TIE_BACKGROUND = "clear-stage-cell2-tie-00"
IMPULSE_CHARTS = (
    ("00", 64, 64),
    ("01", 128, 96),
    ("02", 192, 160),
)
IMPULSE_SPACING = 256
IMPULSE_RADIUS = 16
INTERIOR_MARGIN = 512
STATE_GUARD = 0.01
QUANTIZATION_MODES = ("floor", "half-up", "half-even", "ceil")


@dataclass(frozen=True, slots=True)
class ImpulseChart:
    name: str
    source: FloatArray
    response: FloatArray
    states: IntArray
    eligible: BoolArray
    offsets: IntArray


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def code_image(
    captures: CaptureSet,
    background: str,
    *,
    scene: str = SCENE,
    overlay: str = "clear",
    appearance: str = "dark",
) -> NDArray[np.uint8]:
    record = captures.records[(background, scene, overlay, appearance)]
    with captures.image_file(str(record["file"])) as image:
        return np.asarray(image.convert("RGB"), dtype=np.uint8)


def control_code_image(
    captures: CaptureSet,
    background: str,
) -> NDArray[np.uint8]:
    return code_image(
        captures,
        background,
        scene=CONTROL_SCENE,
        overlay="none",
        appearance="dark",
    )


def source_control_report(
    captures: CaptureSet,
    backgrounds: tuple[str, ...],
) -> JsonObject:
    records = []
    for background in backgrounds:
        record = captures.records[
            (background, CONTROL_SCENE, "none", "dark")
        ]
        records.append(
            {
                "background": background,
                "stable": record.get("stable"),
                "stabilitySamples": record.get("stabilitySamples"),
                "sourceDiff": record.get("sourceDiff"),
            }
        )
    exact = {
        "changedPixels": 0,
        "maxChannelDelta": 0,
        "meanAbsoluteChannelDelta": 0,
    }
    return {
        "required": len(backgrounds),
        "available": len(records),
        "allStable": all(record["stable"] is True for record in records),
        "allPixelExact": all(
            record["sourceDiff"] == exact for record in records
        ),
        "records": records,
    }


def interior_codes(image: NDArray[np.uint8]) -> IntArray:
    if (
        image.ndim != 3
        or image.shape[2] != 3
        or image.shape[0] <= 2 * INTERIOR_MARGIN
        or image.shape[1] <= 2 * INTERIOR_MARGIN
    ):
        raise ValueError("image does not contain the required interior")
    return image[
        INTERIOR_MARGIN:-INTERIOR_MARGIN,
        INTERIOR_MARGIN:-INTERIOR_MARGIN,
    ].astype(np.int64)


def tie_rounding_report(
    captures: CaptureSet,
    baseline: NDArray[np.uint8],
) -> JsonObject:
    tie = code_image(captures, TIE_BACKGROUND)
    control = control_code_image(captures, TIE_BACKGROUND)
    if control.shape[0] % 2 or control.shape[1] % 2:
        raise ValueError("tie control must have even dimensions")
    cell_sums = control.astype(np.int16).reshape(
        control.shape[0] // 2,
        2,
        control.shape[1] // 2,
        2,
        3,
    ).sum(axis=(1, 3))
    unique_sums, sum_counts = np.unique(cell_sums, return_counts=True)
    observed_means = unique_sums.astype(np.float64) / 4.0
    designed_half_ties = np.array_equal(
        observed_means,
        np.array([127.5, 128.5]),
    )
    baseline_interior = interior_codes(baseline)
    tie_interior = interior_codes(tie)
    delta = tie_interior - baseline_interior
    mean_delta = float(delta.mean())
    exact = not np.any(delta)
    if not designed_half_ties:
        conclusion = "source-control-does-not-preserve-designed-half-ties"
    elif exact:
        conclusion = "half-even-equivalent"
    elif mean_delta > 0:
        conclusion = "half-up-or-half-away-equivalent"
    elif mean_delta < 0:
        conclusion = "half-down-equivalent"
    else:
        conclusion = "nonuniform-tie-rule"

    grid = sample_grid(
        baseline.shape[:2],
        margin=INTERIOR_MARGIN,
        stride=17,
    )
    states, eligible = state_masks(
        captures,
        grid,
        guard=STATE_GUARD,
    )[SCENE]
    sampled_tie = tie[grid.y, grid.x].astype(np.int64)
    sampled_baseline = baseline[grid.y, grid.x].astype(np.int64)
    return {
        "observedControlCellMeansCodes": observed_means.tolist(),
        "observedControlCellMeanCounts": {
            str(float(value / 4.0)): int(count)
            for value, count in zip(
                unique_sums,
                sum_counts,
                strict=True,
            )
        },
        "sourceContainsOnlyDesignedHalfTies": bool(designed_half_ties),
        "knownPriorConstraint": "nearest-integer 2x2 source-code mean",
        "observationalConclusion": conclusion,
        "fullInterior": difference_report(
            tie_interior.reshape(-1, 3),
            baseline_interior.reshape(-1, 3),
        ),
        "meanSignedDeltaCodes": mean_delta,
        "signedDeltaCounts": signed_counts(delta),
        "stratified": stratified_difference(
            sampled_tie,
            sampled_baseline,
            grid=grid,
            eligible=eligible,
            states=states,
        ),
    }


def slope_pair_report(
    forward_source: IntArray,
    reverse_source: IntArray,
    forward_output: IntArray,
    reverse_output: IntArray,
    *,
    channel: int,
    axis: int,
) -> JsonObject:
    if (
        forward_source.shape != reverse_source.shape
        or forward_source.shape != forward_output.shape
        or forward_source.shape != reverse_output.shape
        or forward_source.ndim != 3
        or forward_source.shape[2] != 3
        or channel not in range(3)
        or axis not in (0, 1)
    ):
        raise ValueError("invalid complementary-ramp inputs")
    left = [slice(None), slice(None)]
    right = [slice(None), slice(None)]
    left[axis] = slice(None, -2)
    right[axis] = slice(2, None)
    left_index = (*left, channel)
    right_index = (*right, channel)
    source_forward_step = (
        forward_source[right_index] - forward_source[left_index]
    )
    source_reverse_step = (
        reverse_source[right_index] - reverse_source[left_index]
    )
    eligible = (source_forward_step == 1) & (source_reverse_step == -1)
    output_forward_step = (
        forward_output[right_index] - forward_output[left_index]
    )
    output_reverse_step = (
        reverse_output[right_index] - reverse_output[left_index]
    )
    relation = (
        output_forward_step[eligible] + output_reverse_step[eligible]
    )
    return {
        "eligibleSteps": int(relation.size),
        "forwardOutputStepCounts": signed_counts(
            output_forward_step[eligible]
        ),
        "reverseOutputStepCounts": signed_counts(
            output_reverse_step[eligible]
        ),
        "complementaryStepSum": error_report(
            relation,
            np.zeros_like(relation),
        ),
    }


def complementary_ramp_report(
    captures: CaptureSet,
    baseline: NDArray[np.uint8],
) -> JsonObject:
    forward_source = interior_codes(
        control_code_image(captures, RAMP_FORWARD)
    )
    reverse_source = interior_codes(
        control_code_image(captures, RAMP_REVERSE)
    )
    source_sum = forward_source + reverse_source
    source_relation = source_sum - 256

    forward_full = code_image(captures, RAMP_FORWARD)
    reverse_full = code_image(captures, RAMP_REVERSE)
    forward = interior_codes(forward_full)
    reverse = interior_codes(reverse_full)
    baseline_interior = interior_codes(baseline)
    output_relation = forward + reverse - 2 * baseline_interior
    grid = sample_grid(
        baseline.shape[:2],
        margin=INTERIOR_MARGIN,
        stride=17,
    )
    states, eligible = state_masks(
        captures,
        grid,
        guard=STATE_GUARD,
    )[SCENE]
    sampled_pair = (
        forward_full[grid.y, grid.x].astype(np.int64)
        + reverse_full[grid.y, grid.x].astype(np.int64)
    )
    sampled_baseline_pair = (
        2 * baseline[grid.y, grid.x].astype(np.int64)
    )
    return {
        "sourcePointwiseSumCodes": 256,
        "sourceComplement": difference_report(
            source_relation.reshape(-1, 3),
            np.zeros_like(source_relation).reshape(-1, 3),
        ),
        "outputPairRelativeToTwiceGray128": difference_report(
            output_relation.reshape(-1, 3),
            np.zeros_like(output_relation).reshape(-1, 3),
        ),
        "outputPairSignedDeltaCounts": signed_counts(output_relation),
        "outputPairStratified": stratified_difference(
            sampled_pair,
            sampled_baseline_pair,
            grid=grid,
            eligible=eligible,
            states=states,
        ),
        "affineSlopeChecks": {
            "red-x": slope_pair_report(
                forward_source,
                reverse_source,
                forward,
                reverse,
                channel=0,
                axis=1,
            ),
            "green-y": slope_pair_report(
                forward_source,
                reverse_source,
                forward,
                reverse,
                channel=1,
                axis=0,
            ),
            "blue-x": slope_pair_report(
                forward_source,
                reverse_source,
                forward,
                reverse,
                channel=2,
                axis=1,
            ),
            "blue-y": slope_pair_report(
                forward_source,
                reverse_source,
                forward,
                reverse,
                channel=2,
                axis=0,
            ),
        },
    }


def impulse_origins(
    shape: tuple[int, int],
    *,
    offset_x: int,
    offset_y: int,
    spacing: int = IMPULSE_SPACING,
) -> IntArray:
    height, width = shape
    if (
        height <= 0
        or width <= 0
        or offset_x < 0
        or offset_y < 0
        or spacing <= 2
    ):
        raise ValueError("invalid impulse-lattice geometry")
    x = np.arange(offset_x, width, spacing, dtype=np.int64)
    y = np.arange(offset_y, height, spacing, dtype=np.int64)
    grid_y, grid_x = np.meshgrid(y, x, indexing="ij")
    return np.column_stack((grid_y.ravel(), grid_x.ravel()))


def impulse_offsets(radius: int = IMPULSE_RADIUS) -> IntArray:
    if radius < 0:
        raise ValueError("impulse radius must be nonnegative")
    values = np.arange(-radius, radius + 1, dtype=np.int64)
    y, x = np.meshgrid(values, values, indexing="ij")
    return np.column_stack((y.ravel(), x.ravel()))


def extract_impulse_chart(
    captures: CaptureSet,
    baseline: NDArray[np.uint8],
    *,
    chart: str,
    offset_x: int,
    offset_y: int,
    radius: int = IMPULSE_RADIUS,
) -> tuple[ImpulseChart, JsonObject]:
    background = f"clear-stage-grid2-impulse-lattice-{chart}"
    source_image = control_code_image(captures, background)
    output_image = code_image(captures, background)
    if source_image.shape != baseline.shape or output_image.shape != baseline.shape:
        raise ValueError("impulse images do not match the baseline")
    offsets = impulse_offsets(radius)
    origins = impulse_origins(
        baseline.shape[:2],
        offset_x=offset_x,
        offset_y=offset_y,
    )
    within = (
        (origins[:, 0] >= radius)
        & (origins[:, 0] + radius < baseline.shape[0])
        & (origins[:, 1] >= radius)
        & (origins[:, 1] + radius < baseline.shape[1])
    )
    origins = origins[within]
    source = source_image[origins[:, 0], origins[:, 1]].astype(
        np.float64
    ) - 128.0
    sample_y = origins[:, 0, np.newaxis] + offsets[np.newaxis, :, 0]
    sample_x = origins[:, 1, np.newaxis] + offsets[np.newaxis, :, 1]
    response = (
        output_image[sample_y, sample_x].astype(np.float64)
        - baseline[sample_y, sample_x].astype(np.float64)
    )
    grid = SampleGrid(y=origins[:, 0], x=origins[:, 1])
    states, eligible = state_masks(
        captures,
        grid,
        guard=STATE_GUARD,
    )[SCENE]

    block_exact = True
    for delta_y in range(2):
        for delta_x in range(2):
            block_exact &= np.array_equal(
                source_image[
                    origins[:, 0] + delta_y,
                    origins[:, 1] + delta_x,
                ],
                source_image[origins[:, 0], origins[:, 1]],
            )
    observed_amplitudes = sorted(
        {
            int(value)
            for value in np.unique(np.abs(source).astype(np.int64))
            if value
        }
    )
    return (
        ImpulseChart(
            name=chart,
            source=source,
            response=response,
            states=states,
            eligible=eligible,
            offsets=offsets,
        ),
        {
            "background": background,
            "sites": int(origins.shape[0]),
            "eligibleSites": int(np.count_nonzero(eligible)),
            "alignedTwoByTwoBlocksExact": bool(block_exact),
            "observedAbsoluteAmplitudesCodes": observed_amplitudes,
            "sitesByState": {
                str(state): int(
                    np.count_nonzero(eligible & (states == state))
                )
                for state in range(STATE_THRESHOLDS.size + 1)
                if np.any(eligible & (states == state))
            },
        },
    )


def quantize(values: FloatArray, mode: str) -> FloatArray:
    match mode:
        case "floor":
            return np.floor(values)
        case "half-up":
            return np.floor(values + 0.5)
        case "half-even":
            return np.rint(values)
        case "ceil":
            return np.ceil(values)
        case _:
            raise ValueError(f"unknown quantization mode: {mode}")


def error_report(predicted: FloatArray, actual: FloatArray) -> JsonObject:
    if predicted.shape != actual.shape:
        raise ValueError("prediction and target shapes differ")
    error = predicted - actual
    absolute = np.abs(error)
    integral = np.all(error == np.rint(error))
    return {
        "observations": int(error.size),
        "exactFraction": (
            float(np.count_nonzero(error == 0)) / error.size
            if error.size
            else None
        ),
        "meanAbsoluteCodes": (
            float(absolute.mean()) if absolute.size else None
        ),
        "rootMeanSquareCodes": (
            float(np.sqrt(np.square(error).mean())) if error.size else None
        ),
        "maximumAbsoluteCodes": (
            float(absolute.max(initial=0)) if absolute.size else None
        ),
        "signedErrorCounts": (
            signed_counts(error.astype(np.int64)) if integral else None
        ),
    }


def merge_errors(errors: list[FloatArray]) -> FloatArray:
    if not errors:
        return np.empty(0, dtype=np.float64)
    return np.concatenate(tuple(error.reshape(-1) for error in errors))


def impulse_cross_validation(charts: tuple[ImpulseChart, ...]) -> JsonObject:
    if len(charts) < 3:
        raise ValueError("three impulse charts are required")
    mode_errors: dict[str, list[FloatArray]] = {
        mode: [] for mode in QUANTIZATION_MODES
    }
    continuous_errors: list[FloatArray] = []
    records: JsonObject = {}
    for holdout in charts:
        fold_records: JsonObject = {}
        training_charts = tuple(
            chart for chart in charts if chart.name != holdout.name
        )
        for state in range(STATE_THRESHOLDS.size + 1):
            train_x = np.concatenate(
                tuple(
                    chart.source[
                        chart.eligible & (chart.states == state)
                    ]
                    for chart in training_charts
                ),
                axis=0,
            )
            train_y = np.concatenate(
                tuple(
                    chart.response[
                        chart.eligible & (chart.states == state)
                    ]
                    for chart in training_charts
                ),
                axis=0,
            )
            test_mask = holdout.eligible & (holdout.states == state)
            test_x = holdout.source[test_mask]
            test_y = holdout.response[test_mask]
            if (
                train_x.shape[0] < 6
                or test_x.shape[0] < 2
                or np.linalg.matrix_rank(train_x) < 3
            ):
                continue
            coefficients = np.linalg.lstsq(
                train_x,
                train_y.reshape(train_y.shape[0], -1),
                rcond=None,
            )[0]
            continuous = (test_x @ coefficients).reshape(test_y.shape)
            continuous_error = continuous - test_y
            continuous_errors.append(continuous_error)
            modes: JsonObject = {}
            for mode in QUANTIZATION_MODES:
                prediction = quantize(continuous, mode)
                error = prediction - test_y
                mode_errors[mode].append(error)
                modes[mode] = error_report(prediction, test_y)
            fold_records[str(state)] = {
                "trainingSites": int(train_x.shape[0]),
                "holdoutSites": int(test_x.shape[0]),
                "trainingRank": int(np.linalg.matrix_rank(train_x)),
                "continuous": error_report(continuous, test_y),
                "quantized": modes,
            }
        records[holdout.name] = fold_records

    continuous_error = merge_errors(continuous_errors)
    continuous_actual = np.zeros_like(continuous_error)
    aggregate_modes = {}
    for mode, errors in mode_errors.items():
        error = merge_errors(errors)
        aggregate_modes[mode] = error_report(
            error,
            np.zeros_like(error),
        )
    ranked_modes = sorted(
        aggregate_modes,
        key=lambda mode: (
            aggregate_modes[mode]["rootMeanSquareCodes"],
            -aggregate_modes[mode]["exactFraction"],
            mode,
        ),
    )
    return {
        "model": (
            "state-specific 3x3 cross-channel spatial impulse response; "
            "two charts fit, third chart held out"
        ),
        "radiusPixels": IMPULSE_RADIUS,
        "continuous": error_report(
            continuous_error,
            continuous_actual,
        ),
        "quantized": aggregate_modes,
        "rankedFinalQuantizers": ranked_modes,
        "folds": records,
    }


def impulse_support_report(
    charts: tuple[ImpulseChart, ...],
) -> JsonObject:
    offsets = charts[0].offsets
    radius = np.maximum(np.abs(offsets[:, 0]), np.abs(offsets[:, 1]))
    result: JsonObject = {}
    for distance in range(int(radius.max(initial=0)) + 1):
        selected_offsets = radius == distance
        values = np.concatenate(
            tuple(
                chart.response[
                    chart.eligible
                ][:, selected_offsets].reshape(-1)
                for chart in charts
            )
        )
        result[str(distance)] = {
            "observations": int(values.size),
            "nonzeroFraction": (
                float(np.count_nonzero(values)) / values.size
                if values.size
                else None
            ),
            "maximumAbsoluteCodes": float(
                np.abs(values).max(initial=0)
            ),
        }
    return result


def analyze(captures: CaptureSet) -> JsonObject:
    if captures.manifest.get("rigVersion") != RIG_VERSION:
        raise ValueError(
            f"expected rig {RIG_VERSION}, got "
            f"{captures.manifest.get('rigVersion')!r}"
        )
    baseline = code_image(captures, BASELINE)
    stage_backgrounds = (
        RAMP_FORWARD,
        RAMP_REVERSE,
        *(f"clear-stage-grid2-impulse-lattice-{chart}" for chart, _, _ in IMPULSE_CHARTS),
        TIE_BACKGROUND,
    )
    charts = []
    chart_inventory = []
    for chart, offset_x, offset_y in IMPULSE_CHARTS:
        decoded, inventory = extract_impulse_chart(
            captures,
            baseline,
            chart=chart,
            offset_x=offset_x,
            offset_y=offset_y,
        )
        charts.append(decoded)
        chart_inventory.append(inventory)
    chart_tuple = tuple(charts)
    artifact_hash = (
        file_sha256(captures.root) if captures.root.is_file() else None
    )
    return {
        "clearFilterStageSchemaVersion": 1,
        "analysisImplementation": {
            "file": "analysis/liquid_glass_clear_filter_stage.py",
            "sha256": file_sha256(Path(__file__).resolve()),
            "python": platform.python_version(),
            "numpy": np.__version__,
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
        "sourceControls": source_control_report(
            captures,
            tuple(stage_backgrounds),
        ),
        "tieRounding": tie_rounding_report(captures, baseline),
        "complementaryRamps": complementary_ramp_report(
            captures,
            baseline,
        ),
        "impulseInventory": chart_inventory,
        "impulseSupport": impulse_support_report(chart_tuple),
        "impulseLinearCrossValidation": impulse_cross_validation(
            chart_tuple
        ),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Analyze v2.17 clear filter-stage interventions."
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
