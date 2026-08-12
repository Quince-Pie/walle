#!/usr/bin/env python3
"""Test whether v2.16 clear amplitude traces have one final quantizer."""

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

RIG_VERSION = "2.16.0"
SCENE = "circle-4000-center"
AMPLITUDES = np.arange(65, dtype=np.int64)
DEFAULT_SAMPLE_STRIDE = 17
SAMPLE_MARGIN_PIXELS = 64


@dataclass(frozen=True, slots=True)
class Feasibility:
    lower: FloatArray
    upper: FloatArray
    lower_pairs: IntArray
    upper_pairs: IntArray

    @property
    def feasible(self) -> BoolArray:
        return self.upper > self.lower


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def aligned_background(amplitude: int) -> str:
    if amplitude not in range(1, 65):
        raise ValueError("invalid aligned-grid amplitude")
    return f"noise-rgb-a{amplitude:03d}-grid2-shift-00-train"


def fixed_intercept_intervals(
    deltas: IntArray,
    amplitudes: IntArray,
) -> Feasibility:
    if (
        deltas.ndim != 2
        or amplitudes.ndim != 1
        or deltas.shape[0] != amplitudes.size
        or amplitudes.size < 2
        or amplitudes[0] != 0
        or np.any(np.diff(amplitudes) <= 0)
        or np.any(deltas[0] != 0)
    ):
        raise ValueError("invalid amplitude traces")
    nonzero = amplitudes[1:, np.newaxis].astype(np.float64)
    lower_candidates = (deltas[1:] - 0.5) / nonzero
    upper_candidates = (deltas[1:] + 0.5) / nonzero
    lower_indices = np.argmax(lower_candidates, axis=0) + 1
    upper_indices = np.argmin(upper_candidates, axis=0) + 1
    traces = deltas.shape[1]
    columns = np.arange(traces, dtype=np.int64)
    lower = lower_candidates[lower_indices - 1, columns]
    upper = upper_candidates[upper_indices - 1, columns]
    zeros = np.zeros(traces, dtype=np.int64)
    return Feasibility(
        lower=lower,
        upper=upper,
        lower_pairs=np.column_stack((zeros, lower_indices)),
        upper_pairs=np.column_stack((zeros, upper_indices)),
    )


def free_intercept_intervals(
    deltas: IntArray,
    amplitudes: IntArray,
) -> Feasibility:
    if (
        deltas.ndim != 2
        or amplitudes.ndim != 1
        or deltas.shape[0] != amplitudes.size
        or amplitudes.size < 2
        or amplitudes[0] != 0
        or np.any(np.diff(amplitudes) <= 0)
        or np.any(deltas[0] != 0)
    ):
        raise ValueError("invalid amplitude traces")
    traces = deltas.shape[1]
    lower = np.full(traces, -np.inf, dtype=np.float64)
    upper = np.full(traces, np.inf, dtype=np.float64)
    lower_pairs = np.zeros((traces, 2), dtype=np.int64)
    upper_pairs = np.zeros((traces, 2), dtype=np.int64)
    for left in range(amplitudes.size - 1):
        for right in range(left + 1, amplitudes.size):
            amplitude_delta = float(amplitudes[right] - amplitudes[left])
            code_delta = deltas[right] - deltas[left]
            candidate_lower = (code_delta - 1.0) / amplitude_delta
            candidate_upper = (code_delta + 1.0) / amplitude_delta

            replace_lower = candidate_lower > lower
            lower[replace_lower] = candidate_lower[replace_lower]
            lower_pairs[replace_lower] = (left, right)

            replace_upper = candidate_upper < upper
            upper[replace_upper] = candidate_upper[replace_upper]
            upper_pairs[replace_upper] = (left, right)
    return Feasibility(
        lower=lower,
        upper=upper,
        lower_pairs=lower_pairs,
        upper_pairs=upper_pairs,
    )


def pair_counts(pairs: IntArray, selected: BoolArray) -> JsonObject:
    if pairs.ndim != 2 or pairs.shape[1] != 2 or selected.shape != pairs.shape[:1]:
        raise ValueError("pair-count inputs do not match")
    if not np.any(selected):
        return {}
    unique, counts = np.unique(pairs[selected], axis=0, return_counts=True)
    order = np.argsort(counts)[::-1]
    return {
        f"{int(unique[index, 0])}-{int(unique[index, 1])}": int(counts[index])
        for index in order[:20]
    }


def feasibility_report(
    feasibility: Feasibility,
    *,
    states: IntArray,
    eligible: BoolArray,
    channels: int,
) -> JsonObject:
    if channels <= 0:
        raise ValueError("channel count must be positive")
    trace_states = np.repeat(states, channels)
    trace_eligible = np.repeat(eligible, channels)
    feasible = feasibility.feasible
    violation = np.maximum(feasibility.lower - feasibility.upper, 0.0)

    def summarize(selected: BoolArray) -> JsonObject:
        count = int(np.count_nonzero(selected))
        accepted = selected & feasible
        rejected = selected & ~feasible
        widths = feasibility.upper[accepted] - feasibility.lower[accepted]
        return {
            "traces": count,
            "feasibleTraces": int(np.count_nonzero(accepted)),
            "feasibleFraction": (
                float(np.count_nonzero(accepted)) / count if count else None
            ),
            "minimumFeasibleSlopeIntervalWidth": (
                float(widths.min()) if widths.size else None
            ),
            "maximumSlopeIntervalViolation": (
                float(violation[rejected].max()) if np.any(rejected) else 0.0
            ),
            "constrainingLowerAmplitudePairs": pair_counts(
                feasibility.lower_pairs,
                rejected,
            ),
            "constrainingUpperAmplitudePairs": pair_counts(
                feasibility.upper_pairs,
                rejected,
            ),
        }

    return {
        "allEligible": summarize(trace_eligible),
        "byChannel": {
            str(channel): summarize(
                trace_eligible
                & (np.arange(feasible.size, dtype=np.int64) % channels == channel)
            )
            for channel in range(channels)
        },
        "byState": {
            str(state): summarize(
                trace_eligible & (trace_states == state)
            )
            for state in range(STATE_THRESHOLDS.size + 1)
            if np.any(eligible & (states == state))
        },
    }


def amplitude_subspace_report(
    deltas: IntArray,
    *,
    eligible: BoolArray,
    channels: int,
    maximum_rank: int = 8,
) -> JsonObject:
    if (
        deltas.ndim != 2
        or channels <= 0
        or deltas.shape[1] % channels
        or eligible.shape != (deltas.shape[1] // channels,)
        or maximum_rank <= 0
    ):
        raise ValueError("invalid amplitude-subspace inputs")
    selected = np.repeat(eligible, channels)
    values = deltas[:, selected].astype(np.float64)
    covariance = values @ values.T / values.shape[1]
    eigenvalues, eigenvectors = np.linalg.eigh(covariance)
    order = np.argsort(eigenvalues)[::-1]
    eigenvalues = np.maximum(eigenvalues[order], 0.0)
    eigenvectors = eigenvectors[:, order]
    total_energy = float(eigenvalues.sum())
    ranks = min(maximum_rank, eigenvectors.shape[1])
    projections = eigenvectors[:, :ranks].T @ values
    records = []
    reconstruction = np.zeros_like(values)
    for rank in range(1, ranks + 1):
        reconstruction += (
            eigenvectors[:, rank - 1, np.newaxis]
            * projections[rank - 1, np.newaxis]
        )
        rounded = np.floor(reconstruction + 0.5)
        absolute = np.abs(rounded - values)
        records.append(
            {
                "rank": rank,
                "exactChannelFraction": float(np.mean(absolute == 0.0)),
                "meanAbsoluteCodes": float(absolute.mean()),
                "rootMeanSquareCodes": float(
                    np.sqrt(np.square(absolute).mean())
                ),
                "maximumAbsoluteCodes": float(absolute.max(initial=0.0)),
                "continuousRootMeanSquareCodes": float(
                    np.sqrt(np.square(reconstruction - values).mean())
                ),
                "cumulativeEnergyFraction": (
                    float(eigenvalues[:rank].sum()) / total_energy
                    if total_energy
                    else 0.0
                ),
            }
        )
    vectors = eigenvectors[:, :ranks].copy()
    for column in range(ranks):
        largest = int(np.argmax(np.abs(vectors[:, column])))
        if vectors[largest, column] < 0.0:
            vectors[:, column] *= -1.0
        scale = float(np.max(np.abs(vectors[:, column])))
        if scale:
            vectors[:, column] /= scale
    return {
        "traces": int(values.shape[1]),
        "leadingEigenvalues": eigenvalues[:ranks].tolist(),
        "leadingEnergyFractions": (
            (eigenvalues[:ranks] / total_energy).tolist()
            if total_energy
            else [0.0] * ranks
        ),
        "leadingNormalizedAmplitudeVectors": vectors.T.tolist(),
        "roundedReconstructionByRank": records,
    }


def load_traces(
    captures: CaptureSet,
    *,
    grid: SampleGrid,
) -> tuple[IntArray, IntArray]:
    baseline = np.asarray(
        captures.image(
            "gray-128",
            SCENE,
            "clear",
            "dark",
        )[grid.y, grid.x],
        dtype=np.int64,
    )
    traces = np.empty(
        (AMPLITUDES.size, grid.y.size, baseline.shape[1]),
        dtype=np.int64,
    )
    traces[0] = baseline
    for amplitude in AMPLITUDES[1:]:
        traces[amplitude] = captures.image(
            aligned_background(int(amplitude)),
            SCENE,
            "clear",
            "dark",
        )[grid.y, grid.x]
    deltas = traces - traces[0]
    return traces, deltas


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
    states, eligible = state_masks(captures, grid)[SCENE]
    traces, deltas = load_traces(captures, grid=grid)
    flattened = deltas.reshape(AMPLITUDES.size, -1)
    fixed = fixed_intercept_intervals(flattened, AMPLITUDES)
    free = free_intercept_intervals(flattened, AMPLITUDES)
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
        "clearAmplitudeAffinitySchemaVersion": 1,
        "analysisImplementation": {
            "file": "analysis/liquid_glass_clear_amplitude_affinity.py",
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
        "sampling": {
            "scene": SCENE,
            "marginPixels": SAMPLE_MARGIN_PIXELS,
            "stridePixels": stride,
            "sampledPixels": int(grid.y.size),
            "eligibleSampledPixels": int(np.count_nonzero(eligible)),
            "channelsPerPixel": int(traces.shape[2]),
            "amplitudes": AMPLITUDES.tolist(),
            "baselineMinimumCodes": traces[0].min(axis=0).tolist(),
            "baselineMaximumCodes": traces[0].max(axis=0).tolist(),
        },
        "hypotheses": {
            "zeroIntercept": {
                "description": (
                    "one slope per output channel, exact gray baseline, "
                    "then one final nearest-integer quantizer"
                ),
                **feasibility_report(
                    fixed,
                    states=states,
                    eligible=eligible,
                    channels=traces.shape[2],
                ),
            },
            "freeSubcodeIntercept": {
                "description": (
                    "one slope and one shared subcode intercept per output "
                    "channel, then one final nearest-integer quantizer"
                ),
                **feasibility_report(
                    free,
                    states=states,
                    eligible=eligible,
                    channels=traces.shape[2],
                ),
            },
        },
        "amplitudeSubspace": amplitude_subspace_report(
            flattened,
            eligible=eligible,
            channels=traces.shape[2],
        ),
        "policy": {
            "fitInputs": (
                "v2.16 aligned grid2 amplitudes 1 through 64 and gray-128"
            ),
            "protectedBackgrounds": protected,
            "protectedHoldoutOutputsDecoded": False,
            "productionShaderModified": False,
        },
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Test whether aligned v2.16 clear amplitude traces are exactly "
            "affine before one final nearest-integer quantizer."
        )
    )
    parser.add_argument("artifact", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument(
        "--stride",
        type=int,
        default=DEFAULT_SAMPLE_STRIDE,
        help="analysis sampling stride in pixels",
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
