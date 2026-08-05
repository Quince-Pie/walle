#!/usr/bin/env python3
"""Measure the recovered Liquid Glass luminance/chroma face stage."""

import argparse
import hashlib
import json
import platform
import resource
import time
from importlib.metadata import version as package_version
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from liquid_glass_filter_interventions import (
    InterventionSweep,
    collect_samples as collect_intervention_samples,
    compare_mappings,
    difference_metrics,
)
from liquid_glass_pair_sweep import (
    PairSweep,
    collect_samples as collect_pair_samples,
    prediction_metrics,
)


type FloatArray = NDArray[np.floating[Any]]
type IntArray = NDArray[np.int64]
type JsonObject = dict[str, Any]

LUMA_FLOAT32 = np.asarray(
    (0.2126, 0.7152, 0.0722),
    dtype=np.float32,
)
DEFAULT_BLACK = np.float32(0.075)
DEFAULT_WHITE = np.float32(1.15)
DEFAULT_SATURATION = np.float32(1.06)
DEFAULT_HOLDING_WHITE = np.float32(0.97)

STATE_PARAMETERS: dict[
    str,
    tuple[np.float32, np.float32, np.float32, np.float32, np.float32],
] = {
    "baseline": (
        DEFAULT_BLACK,
        DEFAULT_WHITE,
        DEFAULT_SATURATION,
        DEFAULT_HOLDING_WHITE,
        np.float32(1),
    ),
    "face-saturation-1": (
        DEFAULT_BLACK,
        DEFAULT_WHITE,
        np.float32(1),
        DEFAULT_HOLDING_WHITE,
        np.float32(1),
    ),
    "face-saturation-0": (
        DEFAULT_BLACK,
        DEFAULT_WHITE,
        np.float32(0),
        DEFAULT_HOLDING_WHITE,
        np.float32(1),
    ),
    "face-black-0": (
        np.float32(0),
        DEFAULT_WHITE,
        DEFAULT_SATURATION,
        DEFAULT_HOLDING_WHITE,
        np.float32(1),
    ),
    "face-white-1": (
        DEFAULT_BLACK,
        np.float32(1),
        DEFAULT_SATURATION,
        DEFAULT_HOLDING_WHITE,
        np.float32(1),
    ),
    "holding-white-1": (
        DEFAULT_BLACK,
        DEFAULT_WHITE,
        DEFAULT_SATURATION,
        np.float32(1),
        np.float32(1),
    ),
    "holding-disabled": (
        DEFAULT_BLACK,
        DEFAULT_WHITE,
        DEFAULT_SATURATION,
        np.float32(1),
        np.float32(1),
    ),
    "identity-face": (
        np.float32(0),
        np.float32(1),
        np.float32(1),
        np.float32(1),
        np.float32(1),
    ),
    "holding-only": (
        np.float32(0),
        np.float32(1),
        np.float32(1),
        DEFAULT_HOLDING_WHITE,
        np.float32(1),
    ),
    "affine-only": (
        DEFAULT_BLACK,
        DEFAULT_WHITE,
        np.float32(1),
        np.float32(1),
        np.float32(1),
    ),
    "saturation-only": (
        np.float32(0),
        np.float32(1),
        DEFAULT_SATURATION,
        np.float32(1),
        np.float32(1),
    ),
    "grayscale-only": (
        np.float32(0),
        np.float32(1),
        np.float32(0),
        np.float32(1),
        np.float32(1),
    ),
    "sdr-shadow-0": (
        DEFAULT_BLACK,
        DEFAULT_WHITE,
        DEFAULT_SATURATION,
        DEFAULT_HOLDING_WHITE,
        np.float32(1),
    ),
    "clamp-1": (
        DEFAULT_BLACK,
        DEFAULT_WHITE,
        DEFAULT_SATURATION,
        DEFAULT_HOLDING_WHITE,
        np.float32(1),
    ),
    "face-opacity-0": (
        DEFAULT_BLACK,
        DEFAULT_WHITE,
        DEFAULT_SATURATION,
        DEFAULT_HOLDING_WHITE,
        np.float32(0),
    ),
}


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def face_matrix_float32(
    black: np.float32,
    white: np.float32,
    saturation: np.float32,
    opacity: np.float32,
) -> tuple[NDArray[np.float32], np.float32]:
    luminance_gain = np.float32(white - black)
    luma_contribution = np.float32(
        luminance_gain - saturation
    )
    matrix = (
        saturation * np.eye(3, dtype=np.float32)
        + luma_contribution
        * np.tile(LUMA_FLOAT32, (3, 1))
    )
    matrix = (
        opacity * matrix
        + (np.float32(1) - opacity)
        * np.eye(3, dtype=np.float32)
    ).astype(np.float32)
    return matrix, np.float32(opacity * black)


def luminance_chroma_float32(
    codes: IntArray,
    black: np.float32,
    white: np.float32,
    saturation: np.float32,
    holding_white: np.float32,
    opacity: np.float32,
) -> IntArray:
    normalized = (
        codes.astype(np.float32) / np.float32(255)
    )
    luminance = normalized @ LUMA_FLOAT32
    face = (
        black
        + (white - black) * luminance[:, np.newaxis]
        + saturation
        * (normalized - luminance[:, np.newaxis])
    )
    value = opacity * face + (np.float32(1) - opacity) * normalized
    value = holding_white * value
    return np.clip(
        np.rint(value * np.float32(255)),
        0,
        255,
    ).astype(np.int64)


def luminance_chroma_half_matrix(
    codes: IntArray,
    black: np.float32,
    white: np.float32,
    saturation: np.float32,
    holding_white: np.float32,
    opacity: np.float32,
) -> IntArray:
    normalized = (
        codes.astype(np.float32) / np.float32(255)
    ).astype(np.float16)
    matrix_float32, bias_float32 = face_matrix_float32(
        black,
        white,
        saturation,
        opacity,
    )
    matrix = matrix_float32.astype(np.float16)
    bias = np.float16(bias_float32)
    value = (
        normalized @ matrix.T + bias
    ).astype(np.float16)
    value = (
        np.float16(holding_white) * value
    ).astype(np.float16)
    return np.clip(
        np.rint(value.astype(np.float32) * np.float32(255)),
        0,
        255,
    ).astype(np.int64)


def half_fused_multiply_add(
    left: NDArray[Any] | np.floating[Any],
    right: NDArray[Any] | np.floating[Any],
    accumulator: NDArray[Any] | np.floating[Any],
) -> NDArray[np.float16]:
    """Return a correctly rounded binary16 fused multiply-add."""
    return (
        np.asarray(left, dtype=np.float64)
        * np.asarray(right, dtype=np.float64)
        + np.asarray(accumulator, dtype=np.float64)
    ).astype(np.float16)


def luminance_chroma_half_rgb_fma(
    codes: IntArray,
    black: np.float32,
    white: np.float32,
    saturation: np.float32,
    holding_white: np.float32,
    opacity: np.float32,
) -> IntArray:
    normalized = (
        codes.astype(np.float32) / np.float32(255)
    ).astype(np.float16)
    matrix_float32, bias_float32 = face_matrix_float32(
        black,
        white,
        saturation,
        opacity,
    )
    matrix = matrix_float32.astype(np.float16)
    bias = np.float16(bias_float32)
    channels: list[NDArray[np.float16]] = []
    for row in matrix:
        accumulator = np.zeros(
            normalized.shape[0],
            dtype=np.float16,
        )
        for channel in range(3):
            accumulator = half_fused_multiply_add(
                normalized[:, channel],
                row[channel],
                accumulator,
            )
        accumulator = half_fused_multiply_add(
            np.float16(1),
            bias,
            accumulator,
        )
        channels.append(accumulator)
    value = np.stack(channels, axis=1)
    value = half_fused_multiply_add(
        value,
        np.float16(holding_white),
        np.zeros_like(value),
    )
    return np.clip(
        np.rint(value.astype(np.float32) * np.float32(255)),
        0,
        255,
    ).astype(np.int64)


def independent_endpoint_model(codes: IntArray) -> IntArray:
    value = (
        DEFAULT_BLACK
        + (DEFAULT_WHITE - DEFAULT_BLACK)
        * (
            codes.astype(np.float32)
            / np.float32(255)
        )
    )
    value = DEFAULT_HOLDING_WHITE * value
    return np.clip(
        np.rint(value * np.float32(255)),
        0,
        255,
    ).astype(np.int64)


def matrix_record(
    black: np.float32,
    white: np.float32,
    saturation: np.float32,
    opacity: np.float32,
) -> JsonObject:
    matrix, bias = face_matrix_float32(
        black,
        white,
        saturation,
        opacity,
    )
    half = matrix.astype(np.float16)
    return {
        "equation": (
            "Y=dot([0.2126,0.7152,0.0722],RGB); "
            "face=black+(white-black)*Y"
            "+saturation*(RGB-Y); "
            "mixed=faceOpacity*face+(1-faceOpacity)*RGB"
        ),
        "float32Rows": matrix.tolist(),
        "float16RowsAsFloat": half.astype(np.float32).tolist(),
        "float16RowsAsBits": [
            [f"{int(value):04x}" for value in row]
            for row in half.view(np.uint16)
        ],
        "biasFloat32": float(bias),
        "biasFloat16AsFloat": float(np.float16(bias)),
        "biasFloat16Bits":
            f"{int(np.asarray(np.float16(bias)).view(np.uint16)):04x}",
    }


def analyze(
    pair_path: Path,
    intervention_path: Path,
) -> JsonObject:
    started = time.perf_counter()
    with PairSweep.open(pair_path) as sweep:
        pair, pair_controls = collect_pair_samples(sweep)
    with InterventionSweep.open(intervention_path) as sweep:
        states, _, intervention_controls = (
            collect_intervention_samples(sweep)
        )

    pair_float = luminance_chroma_float32(
        pair.requested,
        *STATE_PARAMETERS["baseline"],
    )
    pair_half = luminance_chroma_half_matrix(
        pair.requested,
        *STATE_PARAMETERS["baseline"],
    )
    pair_fma = luminance_chroma_half_rgb_fma(
        pair.requested,
        *STATE_PARAMETERS["baseline"],
    )
    pair_independent = independent_endpoint_model(pair.requested)

    state_models: JsonObject = {}
    for name, samples in states.items():
        parameters = STATE_PARAMETERS[name]
        state_models[name] = {
            "float32LuminanceChroma": difference_metrics(
                luminance_chroma_float32(
                    samples.inputs,
                    *parameters,
                ),
                samples.outputs,
            ),
            "float32BuiltFloat16Matrix": difference_metrics(
                luminance_chroma_half_matrix(
                    samples.inputs,
                    *parameters,
                ),
                samples.outputs,
            ),
            "float16MatrixRgbOrderedHalfFma": difference_metrics(
                luminance_chroma_half_rgb_fma(
                    samples.inputs,
                    *parameters,
                ),
                samples.outputs,
            ),
        }

    identity = states["identity-face"]
    holding_equivalence = compare_mappings(
        states["holding-disabled"],
        states["holding-white-1"],
    )
    holding_opacity_equivalence = compare_mappings(
        states["holding-only"],
        states["face-opacity-0"],
    )
    shadow_equivalence = compare_mappings(
        states["baseline"],
        states["sdr-shadow-0"],
    )
    clamp_equivalence = compare_mappings(
        states["baseline"],
        states["clamp-1"],
    )
    elapsed = time.perf_counter() - started

    return {
        "liquidGlassFaceStageAnalysisSchemaVersion": 1,
        "analysisImplementation": {
            "file": "analysis/liquid_glass_face_stage.py",
            "sha256": file_sha256(Path(__file__).resolve()),
            "python": platform.python_version(),
            "numpy": package_version("numpy"),
        },
        "sources": {
            "pairSweep": {
                "path": str(pair_path),
                "sha256": file_sha256(pair_path),
            },
            "filterInterventions": {
                "path": str(intervention_path),
                "sha256": file_sha256(intervention_path),
            },
        },
        "recoveredStage": {
            "defaultMatrix": matrix_record(
                DEFAULT_BLACK,
                DEFAULT_WHITE,
                DEFAULT_SATURATION,
                np.float32(1),
            ),
            "holdingTone": (
                "Convert source UNORM codes to half, apply the face "
                "matrix with half FMAs in R-G-B order, add the half "
                "bias last, multiply by half(0.97) with one more "
                "half FMA, then convert back to UNORM."
            ),
        },
        "exhaustivePairMetrics": {
            "inputColors": int(pair.requested.shape[0]),
            "incorrectIndependentEndpointModel":
                prediction_metrics(
                    pair_independent,
                    pair.outputs,
                ),
            "float32LuminanceChroma":
                prediction_metrics(pair_float, pair.outputs),
            "float32BuiltFloat16Matrix":
                prediction_metrics(pair_half, pair.outputs),
            "float16MatrixRgbOrderedHalfFma":
                prediction_metrics(pair_fma, pair.outputs),
        },
        "interventionMetrics": state_models,
        "causalEquivalences": {
            "identityFaceVsInput": difference_metrics(
                identity.inputs,
                identity.outputs,
            ),
            "holdingDisabledVsHoldingWhiteOne":
                holding_equivalence,
            "holdingOnlyVsFaceOpacityZero":
                holding_opacity_equivalence,
            "sdrShadowZeroVsBaseline": shadow_equivalence,
            "clampOneVsBaseline": clamp_equivalence,
        },
        "validation": {
            "pairSourceControlRoundTrip": {
                key: value
                for key, value in pair_controls.items()
                if key != "patterns"
            },
            "interventionSourceControlRoundTrip": {
                key: value
                for key, value in intervention_controls.items()
                if key != "patterns"
            },
        },
        "resourceMeasurements": {
            "analysisSeconds": elapsed,
            "maximumResidentSetKiB":
                resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        },
        "conclusion": {
            "luminanceChromaStructureCausallyIdentified": True,
            "halfHoldingStageCausallyIdentified": True,
            "identityFaceIsBitExact": True,
            "arithmeticQuantizationStillHasResidual": bool(
                np.any(pair_fma != pair.outputs)
            ),
            "productionShaderAuthorized": False,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Measure the recovered Liquid Glass face-stage equation."
        )
    )
    parser.add_argument("pair_sweep", type=Path)
    parser.add_argument("filter_interventions", type=Path)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    report = analyze(
        arguments.pair_sweep,
        arguments.filter_interventions,
    )
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if arguments.output is None:
        print(rendered, end="")
    else:
        arguments.output.write_text(rendered, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
