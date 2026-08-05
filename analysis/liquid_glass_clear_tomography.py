#!/usr/bin/env python3
"""Constrain clear Liquid Glass quantization from training amplitude ladders."""

import argparse
import hashlib
import json
import platform
from importlib.metadata import version as package_version
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from liquid_glass_spatial_fit import CaptureSet


type BoolArray = NDArray[np.bool_]
type FloatArray = NDArray[np.float64]
type JsonObject = dict[str, Any]

RIG_VERSION = "2.14.0"
SCENES = (
    "circle-4000-center",
    "circle-6000-upper-left",
    "rect-6000x4000-r000-center",
    "rect-4000x6000-r000-center",
)
TRAINING_SEEDS = range(4)
AMPLITUDES = (0, 17, 31, 47, 64)
BOUNDARY_EXCLUSION_PIXELS = 512
# Five is coprime to both measured reconstruction periods (2 and 4), so the
# sampled lattice visits every absolute output phase instead of aliasing one.
DEFAULT_SAMPLE_STRIDE = 5


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def training_background(seed: int, amplitude: int) -> str:
    if seed not in TRAINING_SEEDS:
        raise ValueError(f"invalid training seed index: {seed}")
    if amplitude not in AMPLITUDES[1:]:
        raise ValueError(f"invalid training amplitude: {amplitude}")
    if amplitude == 64:
        return f"noise-rgb-a064-kernel-train-{seed:02d}"
    return f"noise-rgb-a{amplitude:03d}-tomography-train-{seed:02d}"


def sample_region(
    shape: tuple[int, int],
    *,
    stride: int,
) -> tuple[slice, slice]:
    height, width = shape
    margin = BOUNDARY_EXCLUSION_PIXELS
    if stride <= 0 or height <= 2 * margin or width <= 2 * margin:
        raise ValueError("invalid tomography sample geometry")
    return (
        slice(margin, height - margin, stride),
        slice(margin, width - margin, stride),
    )


def affine_slope_intervals(
    outputs: FloatArray,
    amplitudes: tuple[int, ...] = AMPLITUDES,
) -> tuple[FloatArray, FloatArray, BoolArray]:
    if (
        outputs.ndim < 2
        or outputs.shape[0] != len(amplitudes)
        or tuple(sorted(amplitudes)) != amplitudes
    ):
        raise ValueError("outputs must follow strictly increasing amplitudes")
    lower = np.full(outputs.shape[1:], -np.inf, dtype=np.float64)
    upper = np.full(outputs.shape[1:], np.inf, dtype=np.float64)
    for left in range(len(amplitudes)):
        for right in range(left + 1, len(amplitudes)):
            amplitude_delta = amplitudes[right] - amplitudes[left]
            lower = np.maximum(
                lower,
                (
                    outputs[right]
                    - 0.5
                    - (outputs[left] + 0.5)
                )
                / amplitude_delta,
            )
            upper = np.minimum(
                upper,
                (
                    outputs[right]
                    + 0.5
                    - (outputs[left] - 0.5)
                )
                / amplitude_delta,
            )
    feasible = lower <= upper
    return lower, upper, feasible


def endpoint_scaling_error(
    actual: FloatArray,
    endpoint: FloatArray,
    *,
    amplitude: int,
    base_code: int = 152,
) -> JsonObject:
    if actual.shape != endpoint.shape or amplitude not in AMPLITUDES[1:-1]:
        raise ValueError("invalid endpoint-scaling inputs")
    continuous = base_code + (endpoint - base_code) * (amplitude / 64.0)
    predicted = np.rint(continuous)
    delta = np.abs(predicted - actual)
    return {
        "channels": int(delta.size),
        "exactChannels": int(np.count_nonzero(delta == 0.0)),
        "absoluteErrorSum": float(delta.sum()),
        "maximumAbsoluteCodes": float(delta.max(initial=0.0)),
    }


def merge_error_records(records: list[JsonObject]) -> JsonObject:
    channels = sum(int(record["channels"]) for record in records)
    exact = sum(int(record["exactChannels"]) for record in records)
    error_sum = sum(float(record["absoluteErrorSum"]) for record in records)
    return {
        "channels": channels,
        "exactChannelFraction": exact / channels if channels else None,
        "meanAbsoluteCodes": error_sum / channels if channels else None,
        "maximumAbsoluteCodes": max(
            (
                float(record["maximumAbsoluteCodes"])
                for record in records
            ),
            default=0.0,
        ),
    }


def interval_summary(
    feasible_parts: list[BoolArray],
    width_parts: list[FloatArray],
) -> JsonObject:
    feasible = np.concatenate([part.reshape(-1) for part in feasible_parts])
    widths = np.concatenate([part.reshape(-1) for part in width_parts])
    valid_widths = widths[feasible]
    return {
        "channels": int(feasible.size),
        "feasibleChannels": int(np.count_nonzero(feasible)),
        "feasibleChannelFraction": float(np.mean(feasible)),
        "feasibleSlopeIntervalWidth": {
            "median": (
                float(np.median(valid_widths))
                if valid_widths.size
                else None
            ),
            "p95": (
                float(np.quantile(valid_widths, 0.95))
                if valid_widths.size
                else None
            ),
            "maximum": (
                float(valid_widths.max(initial=0.0))
                if valid_widths.size
                else None
            ),
        },
    }


def source_sign_identity(captures: CaptureSet, *, stride: int) -> JsonObject:
    differing = 0
    compared = 0
    per_seed: JsonObject = {}
    for seed in TRAINING_SEEDS:
        images = {
            amplitude: captures.reference_image(
                training_background(seed, amplitude)
            )
            for amplitude in AMPLITUDES[1:]
        }
        region = sample_region(
            next(iter(images.values())).shape[:2],
            stride=stride,
        )
        signs = {
            amplitude: image[region] > 128.0
            for amplitude, image in images.items()
        }
        endpoint = signs[64]
        seed_differences: JsonObject = {}
        for amplitude in AMPLITUDES[1:-1]:
            changed = int(np.count_nonzero(signs[amplitude] != endpoint))
            count = int(endpoint.size)
            differing += changed
            compared += count
            seed_differences[str(amplitude)] = {
                "comparedChannels": count,
                "differingChannels": changed,
            }
        per_seed[str(seed)] = seed_differences
    return {
        "comparedChannels": compared,
        "differingChannels": differing,
        "exact": differing == 0,
        "perSeed": per_seed,
    }


def contributing_odd_half_cells(
    source_signs: BoolArray,
    *,
    y_coordinates: NDArray[np.int64],
    x_coordinates: NDArray[np.int64],
) -> NDArray[np.uint8]:
    height, width = source_signs.shape[:2]
    if (
        source_signs.ndim != 3
        or height % 2
        or width % 2
        or y_coordinates.ndim != 1
        or x_coordinates.ndim != 1
    ):
        raise ValueError("invalid half-grid contributor geometry")
    half_odd = (
        source_signs.reshape(height // 2, 2, width // 2, 2, 3)
        .sum(axis=(1, 3))
        .astype(np.uint8)
        % 2
    )
    half_y = np.floor((y_coordinates + 0.5) / 2.0 - 0.5).astype(np.int64)
    half_x = np.floor((x_coordinates + 0.5) / 2.0 - 0.5).astype(np.int64)
    if (
        half_y.min(initial=0) < 0
        or half_x.min(initial=0) < 0
        or half_y.max(initial=0) + 1 >= half_odd.shape[0]
        or half_x.max(initial=0) + 1 >= half_odd.shape[1]
    ):
        raise ValueError("sample coordinates exceed half-grid interpolation bounds")
    return (
        half_odd[half_y[:, np.newaxis], half_x[np.newaxis, :]]
        + half_odd[half_y[:, np.newaxis], half_x[np.newaxis, :] + 1]
        + half_odd[half_y[:, np.newaxis] + 1, half_x[np.newaxis, :]]
        + half_odd[
            half_y[:, np.newaxis] + 1,
            half_x[np.newaxis, :] + 1,
        ]
    )


def half_grid_quantization_signature(
    captures: CaptureSet,
    *,
    stride: int,
) -> JsonObject:
    sample = captures.reference_image(training_background(0, 17))
    region = sample_region(sample.shape[:2], stride=stride)
    y_coordinates = np.arange(sample.shape[0], dtype=np.int64)[region[0]]
    x_coordinates = np.arange(sample.shape[1], dtype=np.int64)[region[1]]
    counts = np.zeros((5, 2), dtype=np.int64)

    for seed in TRAINING_SEEDS:
        source_signs = (
            captures.reference_image(training_background(seed, 17)) > 128.0
        )
        categories = contributing_odd_half_cells(
            source_signs,
            y_coordinates=y_coordinates,
            x_coordinates=x_coordinates,
        )
        for scene in SCENES:
            outputs = [
                captures.image(
                    "gray-128",
                    scene,
                    "clear",
                    "dark",
                )[region]
            ]
            outputs.extend(
                captures.image(
                    training_background(seed, amplitude),
                    scene,
                    "clear",
                    "dark",
                )[region]
                for amplitude in AMPLITUDES[1:]
            )
            _, _, feasible = affine_slope_intervals(np.stack(outputs))
            for category in range(5):
                selected = categories == category
                counts[category, 0] += np.count_nonzero(selected)
                counts[category, 1] += np.count_nonzero(selected & ~feasible)

    records = [
        {
            "oddContributingHalfGridCells": category,
            "channels": int(channels),
            "infeasibleChannels": int(infeasible),
            "infeasibleChannelFraction": (
                infeasible / channels if channels else None
            ),
        }
        for category, (channels, infeasible) in enumerate(counts)
    ]
    return {
        "reduction": "2x2 source-code mean",
        "reconstruction": "half-pixel bilinear from four half-grid cells",
        "oddAmplitudeHalfCodeCondition": (
            "a 2x2 cell has one or three high source bits"
        ),
        "records": records,
        "monotoneInfeasibility": all(
            float(left["infeasibleChannelFraction"])
            < float(right["infeasibleChannelFraction"])
            for left, right in zip(records, records[1:], strict=False)
        ),
    }


def training_tomography_report(
    captures: CaptureSet,
    *,
    stride: int = DEFAULT_SAMPLE_STRIDE,
) -> JsonObject:
    if captures.manifest.get("rigVersion") != RIG_VERSION:
        raise ValueError(f"expected Liquid Glass rig {RIG_VERSION}")

    scene_records: JsonObject = {}
    pooled_errors: dict[int, list[JsonObject]] = {
        amplitude: [] for amplitude in AMPLITUDES[1:-1]
    }
    pooled_feasible: list[BoolArray] = []
    pooled_widths: list[FloatArray] = []

    for scene in SCENES:
        base = captures.image(
            "gray-128",
            scene,
            "clear",
            "dark",
        )
        region = sample_region(base.shape[:2], stride=stride)
        base = base[region]
        unique_base_codes = np.unique(base)
        scene_errors: dict[int, list[JsonObject]] = {
            amplitude: [] for amplitude in AMPLITUDES[1:-1]
        }
        scene_feasible: list[BoolArray] = []
        scene_widths: list[FloatArray] = []

        for seed in TRAINING_SEEDS:
            images = [base]
            for amplitude in AMPLITUDES[1:]:
                background = training_background(seed, amplitude)
                if "holdout" in background:
                    raise AssertionError("protected holdout entered training")
                images.append(
                    captures.image(
                        background,
                        scene,
                        "clear",
                        "dark",
                    )[region]
                )
            outputs = np.stack(images)
            lower, upper, feasible = affine_slope_intervals(outputs)
            width = np.maximum(0.0, upper - lower)
            scene_feasible.append(feasible)
            scene_widths.append(width)
            pooled_feasible.append(feasible)
            pooled_widths.append(width)
            for index, amplitude in enumerate(AMPLITUDES[1:-1], start=1):
                error = endpoint_scaling_error(
                    outputs[index],
                    outputs[-1],
                    amplitude=amplitude,
                )
                scene_errors[amplitude].append(error)
                pooled_errors[amplitude].append(error)

        scene_records[scene] = {
            "uniformGray128OutputCodes": unique_base_codes.tolist(),
            "sampledChannelsPerAmplitude": int(base.size * len(TRAINING_SEEDS)),
            "endpointScaling": {
                str(amplitude): merge_error_records(scene_errors[amplitude])
                for amplitude in AMPLITUDES[1:-1]
            },
            "singleAffineFilterPlusFinalQuantization": interval_summary(
                scene_feasible,
                scene_widths,
            ),
        }

    holdout_backgrounds = sorted(
        {
            str(record.get("background"))
            for record in captures.manifest.get("captures", [])
            if "tomography-holdout-" in str(record.get("background"))
            and record.get("overlay") == "clear"
        }
    )
    return {
        "sampleStridePixels": stride,
        "boundaryExclusionPixels": BOUNDARY_EXCLUSION_PIXELS,
        "trainingSeeds": list(TRAINING_SEEDS),
        "amplitudesCodes": list(AMPLITUDES),
        "sourceSignIdentity": source_sign_identity(captures, stride=stride),
        "halfGridQuantizationSignature": half_grid_quantization_signature(
            captures,
            stride=stride,
        ),
        "scenes": scene_records,
        "pooled": {
            "endpointScaling": {
                str(amplitude): merge_error_records(pooled_errors[amplitude])
                for amplitude in AMPLITUDES[1:-1]
            },
            "singleAffineFilterPlusFinalQuantization": interval_summary(
                pooled_feasible,
                pooled_widths,
            ),
        },
        "protectedHoldouts": {
            "backgrounds": holdout_backgrounds,
            "backgroundCount": len(holdout_backgrounds),
            "outputsDecodedByThisAnalysis": False,
        },
    }


def build_report(
    captures: CaptureSet,
    *,
    stride: int = DEFAULT_SAMPLE_STRIDE,
) -> JsonObject:
    return {
        "clearTomographySchemaVersion": 1,
        "analysisImplementation": {
            "file": "analysis/liquid_glass_clear_tomography.py",
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
            "references": len(captures.manifest.get("references", [])),
            "staticCaptures": len(captures.manifest.get("captures", [])),
        },
        "trainingTomography": training_tomography_report(
            captures,
            stride=stride,
        ),
        "interpretation": (
            "The amplitude response is geometry-invariant but is not exactly "
            "one encoded-code affine filter followed by a single final "
            "half-code quantizer. Infeasibility rises monotonically with the "
            "number of half-code 2x2 reductions contributing to an output, "
            "identifying quantization at or immediately after the measured "
            "half-resolution reduction. This does not yet authorize a "
            "renderer change."
        ),
        "policy": {
            "fitInputs": "four training seeds only",
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
            "Measure training-only clear amplitude tomography without opening "
            "the protected output images."
        )
    )
    parser.add_argument("artifact", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument(
        "--stride",
        type=int,
        default=DEFAULT_SAMPLE_STRIDE,
        help="central-region sampling stride in pixels",
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
