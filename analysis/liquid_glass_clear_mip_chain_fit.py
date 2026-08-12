#!/usr/bin/env python3
"""Fit clear glass with recursively quantized 2x2 reduction levels."""

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

from liquid_glass_clear_dense_state_fit import (
    CachedAmplitude,
    amplitude_fold,
    candidate_statistics,
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
MIP_MODES = ("continuous", "floor", "half-up", "half-even", "ceil")
MIP_DEPTHS = tuple(range(2, 7))


@dataclass(frozen=True, slots=True)
class Candidate:
    name: str
    feature_indices: IntArray
    mode: str
    maximum_depth: int


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


def reduce_mip_level(values: FloatArray, mode: str) -> FloatArray:
    if (
        values.ndim != 3
        or values.shape[2] != 3
        or values.shape[0] < 2
        or values.shape[1] < 2
    ):
        raise ValueError("invalid mip level")
    height = values.shape[0] // 2
    width = values.shape[1] // 2
    cropped = values[: height * 2, : width * 2]
    reduced = cropped.reshape(height, 2, width, 2, 3).mean(axis=(1, 3))
    return np.asarray(quantize_codes(reduced, mode), dtype=np.float64)


def sample_bilinear_normalized(
    values: FloatArray,
    *,
    grid: SampleGrid,
    full_shape: tuple[int, int],
) -> FloatArray:
    if (
        values.ndim != 3
        or values.shape[2] != 3
        or grid.y.ndim != 1
        or grid.x.ndim != 1
        or grid.y.shape != grid.x.shape
        or full_shape[0] <= 0
        or full_shape[1] <= 0
    ):
        raise ValueError("invalid normalized sampling geometry")
    source_y = (
        (grid.y.astype(np.float64) + 0.5)
        * values.shape[0]
        / full_shape[0]
        - 0.5
    )
    source_x = (
        (grid.x.astype(np.float64) + 0.5)
        * values.shape[1]
        / full_shape[1]
        - 0.5
    )
    y0 = np.floor(source_y).astype(np.int64)
    x0 = np.floor(source_x).astype(np.int64)
    if (
        y0.min() < 0
        or x0.min() < 0
        or y0.max() + 1 >= values.shape[0]
        or x0.max() + 1 >= values.shape[1]
    ):
        raise ValueError("sample grid exceeds mip bounds")
    fraction_y = (source_y - y0)[:, np.newaxis]
    fraction_x = (source_x - x0)[:, np.newaxis]
    return (
        (1.0 - fraction_y)
        * (1.0 - fraction_x)
        * values[y0, x0]
        + (1.0 - fraction_y)
        * fraction_x
        * values[y0, x0 + 1]
        + fraction_y
        * (1.0 - fraction_x)
        * values[y0 + 1, x0]
        + fraction_y * fraction_x * values[y0 + 1, x0 + 1]
    )


def union_feature_names(
    ring_squared_radii: tuple[int, ...],
) -> tuple[str, ...]:
    names = [
        f"identified-half-up/r2-{squared_radius}"
        for squared_radius in ring_squared_radii
    ]
    names.extend(
        f"sequential-{mode}/1x{2**depth}"
        for mode in MIP_MODES
        for depth in MIP_DEPTHS
    )
    return tuple(names)


def candidates(ring_count: int) -> tuple[Candidate, ...]:
    if ring_count <= 0:
        raise ValueError("ring count must be positive")
    base = np.arange(ring_count, dtype=np.int64)
    result = [
        Candidate(
            name="identified-half-only",
            feature_indices=base,
            mode="none",
            maximum_depth=1,
        )
    ]
    offset = ring_count
    for mode in MIP_MODES:
        mode_indices = np.arange(
            offset,
            offset + len(MIP_DEPTHS),
            dtype=np.int64,
        )
        offset += len(MIP_DEPTHS)
        for count, depth in enumerate(MIP_DEPTHS, start=1):
            result.append(
                Candidate(
                    name=f"sequential-{mode}-through-1x{2**depth}",
                    feature_indices=np.concatenate(
                        (base, mode_indices[:count])
                    ),
                    mode=mode,
                    maximum_depth=depth,
                )
            )
    return tuple(result)


def union_features(
    source: FloatArray,
    *,
    grid: SampleGrid,
    rings: tuple[tuple[int, tuple[tuple[int, int], ...]], ...],
) -> FloatArray:
    half = half_grid_reduction(source, INITIAL_REDUCTION_MODE)
    features = [
        bilinear_ring_features(
            half - BASE_SOURCE_CODE,
            grid,
            rings,
        )
    ]
    full_shape = source.shape[:2]
    for mode in MIP_MODES:
        level = half
        mode_features = np.empty(
            (grid.y.size, len(MIP_DEPTHS), 3),
            dtype=np.float64,
        )
        for feature, depth in enumerate(MIP_DEPTHS):
            level = reduce_mip_level(level, mode)
            if depth < 2:
                raise AssertionError("invalid mip depth")
            mode_features[:, feature] = (
                sample_bilinear_normalized(
                    level,
                    grid=grid,
                    full_shape=full_shape,
                )
                - BASE_SOURCE_CODE
            )
        features.append(mode_features)
    return np.concatenate(features, axis=1)


def load_cache(
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


def empty_error() -> dict[str, float]:
    return {
        "channels": 0.0,
        "exact": 0.0,
        "absolute": 0.0,
        "squared": 0.0,
        "maximum": 0.0,
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
    total = empty_error()
    by_phase = {phase: empty_error() for phase in SHIFT_PHASES}
    by_amplitude = {
        amplitude: empty_error() for amplitude in SHIFT_AMPLITUDES
    }
    terms = candidate.feature_indices.size * 3
    for amplitude in SHIFT_AMPLITUDES:
        for phase in SHIFT_PHASES:
            background = shifted_background(amplitude, phase)
            source = captures.reference_image(background)
            features = union_features(
                source,
                grid=grid,
                rings=rings,
            )[:, candidate.feature_indices]
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
                    .reshape(-1, terms)
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
    cache = load_cache(
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
    statistics_by_name = {}
    for candidate in model_candidates:
        statistics = candidate_statistics(union, candidate)
        statistics_by_name[candidate.name] = statistics
        ranked.append(
            {
                "name": candidate.name,
                "mode": candidate.mode,
                "maximumDepth": candidate.maximum_depth,
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
    selected_statistics = statistics_by_name[selected_name]
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
        "clearMipChainFitSchemaVersion": 1,
        "analysisImplementation": {
            "file": "analysis/liquid_glass_clear_mip_chain_fit.py",
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
            "sequentialMipModes": list(MIP_MODES),
            "sequentialMipDepths": list(MIP_DEPTHS),
        },
        "rankedCandidates": ranked,
        "selected": {
            "name": selected_name,
            "mode": selected_candidate.mode,
            "maximumDepth": selected_candidate.maximum_depth,
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
            "Fit recursively quantized mip-chain hypotheses on aligned v2.16 "
            "training fields and validate on shifted phases."
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
