#!/usr/bin/env python3
"""Prove that the nominal fixed-radius curve does not use fixed mip endpoints."""

import argparse
import hashlib
import json
import platform
import resource
import time
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from liquid_glass_fixed_resource_lod import FixedResourceSweep
from liquid_glass_sampler_probe import half_round_ties_up


type JsonObject = dict[str, Any]
type UInt8Array = NDArray[np.uint8]

HALF_ONE_BITS = 0x3C00
RADIUS_ONE_GRID_STATES = 38
WITNESS_AMPLITUDE_INDEX = 4
WITNESS_AMPLITUDE_CODES = 127
WITNESS_SITE_INDEX = 0
WITNESS_PATCH_Y = 60
WITNESS_PATCH_X = 34
WITNESS_CHANNEL_INDEX = 0
LOD_DENOMINATOR = 64


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def native_rgb8(values: NDArray[np.float16]) -> UInt8Array:
    return np.rint(
        values.astype(np.float32) * np.float32(255)
    ).clip(0, 255).astype(np.uint8)


def half_endpoint_candidate_count(observations: UInt8Array) -> int:
    if observations.shape != (RADIUS_ONE_GRID_STATES,):
        raise ValueError("fixed-endpoint witness shape differs")
    bits = np.arange(HALF_ONE_BITS + 1, dtype=np.uint16)
    values = bits.view(np.float16)
    native = native_rgb8(values)
    first_candidates = values[native == observations[0]]
    numerators = np.arange(
        RADIUS_ONE_GRID_STATES,
        dtype=np.float64,
    )
    candidates = 0
    second = values.astype(np.float64)
    for first in first_candidates:
        exact = (
            (
                LOD_DENOMINATOR - numerators[:, np.newaxis]
            )
            * float(first)
            + numerators[:, np.newaxis] * second[np.newaxis]
        ) / LOD_DENOMINATOR
        predicted = native_rgb8(half_round_ties_up(exact))
        candidates += int(np.count_nonzero(
            np.all(
                predicted == observations[:, np.newaxis],
                axis=0,
            )
        ))
    return candidates


def rgba8_endpoint_candidate_count(
    observations: UInt8Array,
) -> int:
    if observations.shape != (RADIUS_ONE_GRID_STATES,):
        raise ValueError("fixed-endpoint witness shape differs")
    first_values = (
        np.arange(255 * 16 + 1, dtype=np.float64) / 16
    )
    second_values = (
        np.arange(255 * 64 + 1, dtype=np.float64) / 64
    )

    def convert(exact_codes: NDArray[np.float64]) -> UInt8Array:
        fixed_codes = np.floor(
            exact_codes * 16 + 0.5
        ) / 16
        sampled = (
            fixed_codes / 255
        ).astype(np.float16)
        return native_rgb8(sampled)

    first_candidates = first_values[
        convert(first_values) == observations[0]
    ]
    numerators = np.arange(
        RADIUS_ONE_GRID_STATES,
        dtype=np.float64,
    )
    candidates = 0
    for first in first_candidates:
        exact = (
            (
                LOD_DENOMINATOR - numerators[:, np.newaxis]
            )
            * first
            + numerators[:, np.newaxis]
            * second_values[np.newaxis]
        ) / LOD_DENOMINATOR
        predicted = convert(exact)
        candidates += int(np.count_nonzero(
            np.all(
                predicted == observations[:, np.newaxis],
                axis=0,
            )
        ))
    return candidates


def analyze(path: Path) -> JsonObject:
    started = time.perf_counter()
    sweep = FixedResourceSweep.open(path)
    witness = sweep.identity[
        WITNESS_AMPLITUDE_INDEX,
        :RADIUS_ONE_GRID_STATES,
        WITNESS_SITE_INDEX,
        WITNESS_PATCH_Y,
        WITNESS_PATCH_X,
        WITNESS_CHANNEL_INDEX,
    ]
    half_candidates = half_endpoint_candidate_count(witness)
    rgba8_candidates = rgba8_endpoint_candidate_count(witness)
    return {
        "liquidGlassResourceConfoundAnalysisSchemaVersion": 1,
        "analysisImplementation": {
            "file": "analysis/liquid_glass_resource_confound.py",
            "sha256": sha256_file(Path(__file__).resolve()),
            "python": platform.python_version(),
            "numpy": np.__version__,
        },
        "source": {
            "path": str(path),
            "sha256": sha256_file(path),
            "ciCommit": sweep.manifest["ciCommit"],
        },
        "witness": {
            "amplitudeIndex": WITNESS_AMPLITUDE_INDEX,
            "amplitudeCodes": WITNESS_AMPLITUDE_CODES,
            "siteIndex": WITNESS_SITE_INDEX,
            "patchY": WITNESS_PATCH_Y,
            "patchX": WITNESS_PATCH_X,
            "channelIndex": WITNESS_CHANNEL_INDEX,
            "lodNumerators": list(
                range(RADIUS_ONE_GRID_STATES)
            ),
            "nativeRgb8Sequence": witness.astype(int).tolist(),
        },
        "exhaustiveFixedEndpointTests": {
            "binary16TextureEndpointPairs": {
                "candidatePairs": half_candidates,
                "endpointDomain":
                    "every nonnegative binary16 value through one",
                "sampler":
                    "measured binary16 interpolation with midpoint "
                    "ties upward at LOD numerators 0 through 37/64",
            },
            "rgba8UnormTextureEndpointPairs": {
                "candidatePairs": rgba8_candidates,
                "levelZeroDomain":
                    "every 1/16-code spatial endpoint",
                "levelOneDomain":
                    "every 1/64-code spatial endpoint",
                "sampler":
                    "measured fused RGBA8 spatial/mip interpolation "
                    "and one 1/16-code ties-up quantization",
            },
        },
        "conclusion": {
            "fixedBinary16EndpointPairExists":
                half_candidates != 0,
            "fixedRgba8EndpointPairExists":
                rgba8_candidates != 0,
            "nominalFixedResourceCurveHasStateDependentSourcePath":
                half_candidates == 0 and rgba8_candidates == 0,
            "interpretation":
                "The witness exhausts both measured sampler-format "
                "models. Its changing response cannot be produced by "
                "one fixed pair of mip samples while only LOD changes.",
            "productionShaderAuthorized": False,
        },
        "resourceMeasurements": {
            "analysisSeconds": time.perf_counter() - started,
            "maximumResidentSetKiB":
                resource.getrusage(
                    resource.RUSAGE_SELF
                ).ru_maxrss,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Falsify a fixed-mip-endpoint interpretation of Apple's "
            "nominal fixed-radius LOD curve."
        )
    )
    parser.add_argument("fixed_resource_sweep", type=Path)
    parser.add_argument("-o", "--output", type=Path)
    arguments = parser.parse_args()
    result = analyze(arguments.fixed_resource_sweep)
    encoded = json.dumps(
        result,
        indent=2,
        sort_keys=True,
        allow_nan=False,
    ) + "\n"
    if arguments.output:
        arguments.output.write_text(encoded, encoding="utf-8")
        print(arguments.output)
    else:
        print(encoded, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
