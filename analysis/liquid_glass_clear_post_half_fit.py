#!/usr/bin/env python3
"""Fit the stages after clear glass' identified 2x2 mean/round reduction."""

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

from liquid_glass_clear_dense_state_fit import (
    CachedAmplitude,
    amplitude_fold,
    bilinear_ring_features_at_factor,
    color_cross_validation_report,
    color_exact_error_report,
    color_statistics,
    cross_validation_report,
    exact_all_amplitude_report,
    exact_cross_validation_report,
    sum_color_folds,
    union_statistics,
)
from liquid_glass_clear_state_fit import (
    BASE_OUTPUT_CODE,
    BASE_SOURCE_CODE,
    PYRAMID_SCALES,
    STATE_THRESHOLDS,
    SampleGrid,
    bilinear_ring_features,
    half_grid_reduction,
    quantize_codes,
    sample_grid,
    solve_color_coefficients,
    square_ring_offsets,
    state_masks,
)
from liquid_glass_spatial_fit import CaptureSet


type BoolArray = NDArray[np.bool_]
type FloatArray = NDArray[np.float64]
type IntArray = NDArray[np.int64]
type JsonObject = dict[str, Any]

RIG_VERSION = "2.16.0"
SCENE = "circle-4000-center"
AMPLITUDES = tuple(range(1, 65))
SHIFT_AMPLITUDES = (1, 2, 3, 15, 16, 17, 31, 32, 33, 47, 48, 49, 63, 64)
SHIFT_PHASES = ("01", "10", "11")
DEFAULT_SAMPLE_STRIDE = 17
SAMPLE_MARGIN_PIXELS = 64
HALF_GRID_KERNEL_RADIUS = 6
INITIAL_REDUCTION_MODE = "half-even"
QUANTIZATION_MODES = ("floor", "half-up", "half-even", "ceil")
PYRAMID_FEATURES_PER_SCALE = 4


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


def aligned_background(amplitude: int) -> str:
    if amplitude not in AMPLITUDES:
        raise ValueError("invalid aligned-grid amplitude")
    return f"noise-rgb-a{amplitude:03d}-grid2-shift-00-train"


def shifted_background(amplitude: int, phase: str) -> str:
    if amplitude not in SHIFT_AMPLITUDES or phase not in SHIFT_PHASES:
        raise ValueError("invalid shifted-grid probe")
    return f"noise-rgb-a{amplitude:03d}-grid2-shift-{phase}-train"


def post_half_feature_names() -> tuple[str, ...]:
    return tuple(
        f"{name}/sigma-{sigma:g}"
        for name, _, sigmas in PYRAMID_SCALES
        for sigma in sigmas
    )


def post_half_pyramid_features(
    half_grid: FloatArray,
    grid: SampleGrid,
    *,
    mode: str,
) -> FloatArray:
    if (
        half_grid.ndim != 3
        or half_grid.shape[2] != 3
        or half_grid.shape[0] % 4
        or half_grid.shape[1] % 4
    ):
        raise ValueError("half-grid dimensions do not support the pyramid")
    full_height = half_grid.shape[0] * 2
    full_width = half_grid.shape[1] * 2
    result = np.empty(
        (grid.y.size, len(post_half_feature_names()), 3),
        dtype=np.float64,
    )
    feature = 0
    for (_, _, sigmas), half_factor in zip(
        PYRAMID_SCALES,
        (2, 4),
        strict=True,
    ):
        reduced = cv2.resize(
            half_grid.astype(np.float32),
            (
                half_grid.shape[1] // half_factor,
                half_grid.shape[0] // half_factor,
            ),
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
                (full_width, full_height),
                interpolation=cv2.INTER_LINEAR,
            )
            result[:, feature] = (
                reconstructed[grid.y, grid.x] - BASE_SOURCE_CODE
            )
            feature += 1
    return result


def reduce_half_grid(
    half_grid: FloatArray,
    *,
    factor: int,
    mode: str,
) -> FloatArray:
    if (
        factor <= 0
        or half_grid.ndim != 3
        or half_grid.shape[2] != 3
        or half_grid.shape[0] % factor
        or half_grid.shape[1] % factor
    ):
        raise ValueError("invalid post-half reduction geometry")
    reduced = cv2.resize(
        half_grid.astype(np.float32),
        (
            half_grid.shape[1] // factor,
            half_grid.shape[0] // factor,
        ),
        interpolation=cv2.INTER_AREA,
    )
    return np.asarray(quantize_codes(reduced, mode), dtype=np.float64)


def union_feature_names(
    ring_squared_radii: tuple[int, ...],
) -> tuple[str, ...]:
    half = tuple(
        f"identified-half-even/r2-{squared_radius}"
        for squared_radius in ring_squared_radii
    )
    continuous = tuple(
        f"post-half-continuous/{name}"
        for name in post_half_feature_names()
    )
    residuals: list[str] = []
    for mode in QUANTIZATION_MODES:
        residuals.extend(
            f"post-half-quarter-{mode}-residual/{name}"
            for name in post_half_feature_names()[:PYRAMID_FEATURES_PER_SCALE]
        )
        residuals.extend(
            f"post-half-eighth-{mode}-residual/{name}"
            for name in post_half_feature_names()[PYRAMID_FEATURES_PER_SCALE:]
        )
        residuals.extend(
            f"post-half-eighth-spatial-{mode}-residual/r2-{squared_radius}"
            for squared_radius in ring_squared_radii
        )
    return (*half, *continuous, *residuals)


def candidates(ring_count: int) -> tuple[Candidate, ...]:
    if ring_count <= 0:
        raise ValueError("ring count must be positive")
    base_end = ring_count + len(post_half_feature_names())
    base = np.arange(base_end, dtype=np.int64)
    result = [Candidate("post-half-continuous", base)]
    residual = base_end
    for mode in QUANTIZATION_MODES:
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
        spatial = np.arange(
            residual,
            residual + ring_count,
            dtype=np.int64,
        )
        residual += ring_count
        components = (
            ("quarter", quarter),
            ("eighth", eighth),
            ("eighth-spatial", spatial),
        )
        for mask in range(1, 1 << len(components)):
            selected = tuple(
                (name, indices)
                for index, (name, indices) in enumerate(components)
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


def union_features(
    source: FloatArray,
    *,
    grid: SampleGrid,
    rings: tuple[tuple[int, tuple[tuple[int, int], ...]], ...],
) -> FloatArray:
    half_grid = half_grid_reduction(source, INITIAL_REDUCTION_MODE)
    half = bilinear_ring_features(
        half_grid - BASE_SOURCE_CODE,
        grid,
        rings,
    )
    continuous = post_half_pyramid_features(
        half_grid,
        grid,
        mode="continuous",
    )
    continuous_eighth = reduce_half_grid(
        half_grid,
        factor=4,
        mode="continuous",
    )
    residuals = []
    for mode in QUANTIZATION_MODES:
        quantized = post_half_pyramid_features(
            half_grid,
            grid,
            mode=mode,
        )
        residual = quantized - continuous
        residuals.extend(
            (
                residual[:, :PYRAMID_FEATURES_PER_SCALE],
                residual[:, PYRAMID_FEATURES_PER_SCALE:],
            )
        )
        quantized_eighth = reduce_half_grid(
            half_grid,
            factor=4,
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
    return np.concatenate((half, continuous, *residuals), axis=1)


def load_aligned_cache(
    captures: CaptureSet,
    *,
    grid: SampleGrid,
    rings: tuple[tuple[int, tuple[tuple[int, int], ...]], ...],
) -> list[CachedAmplitude]:
    cache = []
    for amplitude in AMPLITUDES:
        background = aligned_background(amplitude)
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


def select_statistics(
    statistics: dict[int, dict[int, Any]],
    candidate: Candidate,
) -> dict[int, dict[int, Any]]:
    return {
        fold: {
            state: type(record)(
                xtx=record.xtx[
                    np.ix_(
                        candidate.feature_indices,
                        candidate.feature_indices,
                    )
                ],
                xty=record.xty[candidate.feature_indices],
                yty=record.yty,
                observations=record.observations,
            )
            for state, record in states.items()
        }
        for fold, states in statistics.items()
    }


def accumulate_error(
    accumulator: dict[str, float],
    delta: FloatArray,
) -> None:
    accumulator["channels"] += delta.size
    accumulator["exact"] += int(np.count_nonzero(delta == 0.0))
    accumulator["absolute"] += float(delta.sum())
    accumulator["squared"] += float(np.square(delta).sum())
    accumulator["maximum"] = max(
        accumulator["maximum"],
        float(delta.max(initial=0.0)),
    )


def summarize_error(accumulator: dict[str, float]) -> JsonObject:
    channels = int(accumulator["channels"])
    return {
        "channels": channels,
        "exactChannelFraction": (
            int(accumulator["exact"]) / channels if channels else None
        ),
        "meanAbsoluteCodes": (
            accumulator["absolute"] / channels if channels else None
        ),
        "rootMeanSquareCodes": (
            (accumulator["squared"] / channels) ** 0.5
            if channels
            else None
        ),
        "maximumAbsoluteCodes": accumulator["maximum"],
    }


def shifted_validation(
    captures: CaptureSet,
    *,
    grid: SampleGrid,
    rings: tuple[tuple[int, tuple[tuple[int, int], ...]], ...],
    states: IntArray,
    eligible: BoolArray,
    candidate: Candidate,
    statistics: dict[int, dict[int, Any]],
) -> JsonObject:
    coefficients = {
        state: solve_color_coefficients(sum_color_folds(statistics, state))
        for state in range(STATE_THRESHOLDS.size + 1)
    }
    empty = {
        "channels": 0,
        "exact": 0,
        "absolute": 0.0,
        "squared": 0.0,
        "maximum": 0.0,
    }
    total = empty.copy()
    by_phase = {phase: empty.copy() for phase in SHIFT_PHASES}
    by_amplitude = {amplitude: empty.copy() for amplitude in SHIFT_AMPLITUDES}
    for amplitude in SHIFT_AMPLITUDES:
        for phase in SHIFT_PHASES:
            background = shifted_background(amplitude, phase)
            source = captures.reference_image(background)
            features = union_features(source, grid=grid, rings=rings)[
                :, candidate.feature_indices
            ]
            actual = captures.image(
                background,
                SCENE,
                "clear",
                "dark",
            )[grid.y, grid.x]
            for state in range(STATE_THRESHOLDS.size + 1):
                selected = eligible & (states == state)
                design = (
                    features[selected]
                    .transpose(0, 2, 1)
                    .reshape(-1, candidate.feature_indices.size * 3)
                )
                predicted = np.floor(
                    design @ coefficients[state]
                    + BASE_OUTPUT_CODE
                    + 0.5
                )
                delta = np.abs(predicted - actual[selected])
                for accumulator in (
                    total,
                    by_phase[phase],
                    by_amplitude[amplitude],
                ):
                    accumulate_error(accumulator, delta)
    return {
        "fitInputs": "phase-00 amplitudes 1 through 64",
        "validationInputs": (
            "phases 01/10/11 at fourteen fixed boundary amplitudes"
        ),
        "all": summarize_error(total),
        "byPhase": {
            phase: summarize_error(accumulator)
            for phase, accumulator in by_phase.items()
        },
        "byAmplitude": {
            str(amplitude): summarize_error(accumulator)
            for amplitude, accumulator in by_amplitude.items()
        },
    }


def build_report(
    captures: CaptureSet,
    *,
    stride: int = DEFAULT_SAMPLE_STRIDE,
) -> JsonObject:
    if captures.manifest.get("rigVersion") != RIG_VERSION:
        raise ValueError(f"expected Liquid Glass rig {RIG_VERSION}")
    sample = captures.reference_image(aligned_background(1))
    grid = sample_grid(
        sample.shape[:2],
        margin=SAMPLE_MARGIN_PIXELS,
        stride=stride,
    )
    rings = square_ring_offsets(HALF_GRID_KERNEL_RADIUS)
    ring_squared_radii = tuple(squared for squared, _ in rings)
    names = union_feature_names(ring_squared_radii)
    model_candidates = candidates(len(rings))
    cache = load_aligned_cache(
        captures,
        grid=grid,
        rings=rings,
    )
    states, eligible = state_masks(captures, grid)[SCENE]
    union = union_statistics(
        cache,
        states=states,
        eligible=eligible,
        union_terms=len(names),
    )

    ranked = []
    for candidate in model_candidates:
        statistics = select_statistics(union, candidate)
        ranked.append(
            {
                "name": candidate.name,
                "features": [
                    names[index] for index in candidate.feature_indices
                ],
                "featureTerms": int(candidate.feature_indices.size),
                **cross_validation_report(statistics),
            }
        )
    ranked.sort(
        key=lambda record: (
            float(record["rootMeanSquareCodes"]),
            int(record["featureTerms"]),
            str(record["name"]),
        )
    )
    selected_name = str(ranked[0]["name"])
    selected_candidate = next(
        candidate
        for candidate in model_candidates
        if candidate.name == selected_name
    )
    selected_statistics = select_statistics(union, selected_candidate)
    selected_color_statistics = color_statistics(
        cache,
        states=states,
        eligible=eligible,
        candidate=selected_candidate,
    )
    protected = sorted(
        {
            str(record.get("background"))
            for record in captures.manifest.get("captures", [])
            if "holdout" in str(record.get("background"))
            and (
                "-tomography-" in str(record.get("background"))
                or "-sweep-" in str(record.get("background"))
            )
        }
    )
    return {
        "clearPostHalfFitSchemaVersion": 1,
        "analysisImplementation": {
            "file": "analysis/liquid_glass_clear_post_half_fit.py",
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
            "identifiedFirstStage": (
                "top-left-aligned 2x2 arithmetic mean followed by "
                "nearest-half-even integer quantization"
            ),
            "scene": SCENE,
            "sampleMarginPixels": SAMPLE_MARGIN_PIXELS,
            "sampleStridePixels": stride,
            "sampledPixels": int(grid.y.size),
            "eligibleSampledPixels": int(np.count_nonzero(eligible)),
            "kernelRadiusHalfGridPixels": HALF_GRID_KERNEL_RADIUS,
            "quantizationModesTestedAfterFirstStage": list(
                QUANTIZATION_MODES
            ),
        },
        "rankedCandidates": ranked,
        "selected": {
            "name": selected_name,
            "heldAmplitudeExactError": exact_cross_validation_report(
                cache,
                states=states,
                eligible=eligible,
                candidate=selected_candidate,
                statistics=selected_statistics,
            ),
            "allAmplitudeExactError": exact_all_amplitude_report(
                cache,
                states=states,
                eligible=eligible,
                candidate=selected_candidate,
                statistics=selected_statistics,
            ),
            "fullRgbCoupling": {
                "heldAmplitudeContinuousError": (
                    color_cross_validation_report(
                        selected_color_statistics
                    )
                ),
                "heldAmplitudeExactError": color_exact_error_report(
                    cache,
                    states=states,
                    eligible=eligible,
                    candidate=selected_candidate,
                    statistics=selected_color_statistics,
                    cross_validate=True,
                ),
                "allAmplitudeExactError": color_exact_error_report(
                    cache,
                    states=states,
                    eligible=eligible,
                    candidate=selected_candidate,
                    statistics=selected_color_statistics,
                    cross_validate=False,
                ),
                "shiftedPhaseValidation": shifted_validation(
                    captures,
                    grid=grid,
                    rings=rings,
                    states=states,
                    eligible=eligible,
                    candidate=selected_candidate,
                    statistics=selected_color_statistics,
                ),
            },
        },
        "policy": {
            "fitInputs": "v2.16 phase-00 amplitudes 1 through 64",
            "validationInputs": (
                "v2.16 shifted phases 01/10/11 at fixed boundary amplitudes"
            ),
            "protectedBackgrounds": protected,
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
            "Fit post-half-grid clear stages on aligned v2.16 training "
            "fields and validate on shifted phases."
        )
    )
    parser.add_argument("artifact", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument(
        "--stride",
        type=int,
        default=DEFAULT_SAMPLE_STRIDE,
        help="fit sampling stride in pixels",
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
