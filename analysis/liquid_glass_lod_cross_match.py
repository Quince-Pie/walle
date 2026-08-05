#!/usr/bin/env python3
"""Test the default profile against Apple's cross-radius flat LOD catalog."""

import argparse
import hashlib
import json
import platform
import resource
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from liquid_glass_lod_sweep import (
    AMPLITUDES,
    BIN_COUNT,
    CHANNELS,
    EXPECTED_RIG,
    FLAT_RIG,
    PATCH_SIDE,
    PRODUCTION_STATE_INDEX,
    SITE_COUNT,
    STATE_COUNT,
    LodSweep,
)


type JsonObject = dict[str, Any]
type UInt8Array = NDArray[np.uint8]
type UInt16Array = NDArray[np.uint16]
type UInt64Array = NDArray[np.uint64]

SIGNATURE_BYTES = len(AMPLITUDES) * CHANNELS


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def response_signatures(stream: UInt8Array) -> UInt8Array:
    """Place the five native RGB responses in one exact spatial signature."""
    expected = (
        len(AMPLITUDES),
        STATE_COUNT,
        SITE_COUNT,
        PATCH_SIDE,
        PATCH_SIDE,
        CHANNELS,
    )
    if stream.shape != expected:
        raise ValueError(
            f"LOD response stream shape differs: {stream.shape!r}"
        )
    ordered = np.transpose(stream, (1, 2, 3, 4, 0, 5))
    return np.ascontiguousarray(ordered).reshape(
        STATE_COUNT,
        SITE_COUNT * PATCH_SIDE**2,
        SIGNATURE_BYTES,
    )


def exact_signature_words(signatures: UInt8Array) -> UInt64Array:
    """Pack every signature byte losslessly; this is not a hash."""
    if (
        signatures.ndim < 1
        or signatures.shape[-1] != SIGNATURE_BYTES
        or signatures.dtype != np.uint8
    ):
        raise ValueError("native LOD signature layout differs")
    padded = np.zeros(
        signatures.shape[:-1] + (16,),
        dtype=np.uint8,
    )
    padded[..., :SIGNATURE_BYTES] = signatures
    return padded.view(np.dtype("<u8")).reshape(
        signatures.shape[:-1] + (2,)
    )


@dataclass(frozen=True, slots=True)
class CandidateBounds:
    count: UInt16Array
    lower: UInt16Array
    upper: UInt16Array

    @property
    def matched(self) -> NDArray[np.bool_]:
        return self.count != 0

    @property
    def contiguous(self) -> NDArray[np.bool_]:
        width = (
            self.upper.astype(np.int32)
            - self.lower.astype(np.int32)
            + 1
        )
        return self.matched & (width == self.count)


def exact_catalog_candidates(
    default_words: UInt64Array,
    oracle_words: UInt64Array,
) -> CandidateBounds:
    """Find all flat-radius states with exactly the same 15 native bytes."""
    if (
        default_words.ndim != 3
        or oracle_words.ndim != 3
        or default_words.shape[2] != 2
        or oracle_words.shape[2] != 2
        or default_words.shape[1] != oracle_words.shape[1]
    ):
        raise ValueError("LOD exact-word catalog shapes differ")
    default_count, spatial_count, _ = default_words.shape
    catalog_count = oracle_words.shape[0]
    if catalog_count > np.iinfo(np.uint16).max:
        raise ValueError("LOD catalog is too large for exact bounds")

    count = np.zeros(
        (default_count, spatial_count),
        dtype=np.uint16,
    )
    lower = np.full(
        (default_count, spatial_count),
        catalog_count,
        dtype=np.uint16,
    )
    upper = np.full(
        (default_count, spatial_count),
        catalog_count,
        dtype=np.uint16,
    )
    for catalog_index in range(catalog_count):
        matches = (
            default_words[..., 0]
            == oracle_words[catalog_index, :, 0]
        ) & (
            default_words[..., 1]
            == oracle_words[catalog_index, :, 1]
        )
        first = matches & (count == 0)
        lower[first] = catalog_index
        upper[matches] = catalog_index
        count += matches
    return CandidateBounds(count=count, lower=lower, upper=upper)


def _difference_metrics(
    left: UInt8Array,
    right: UInt8Array,
) -> JsonObject:
    if left.shape != right.shape:
        raise ValueError("cross-corpus comparison shapes differ")
    changed = left != right
    changed_pixels = np.any(changed, axis=-1)
    distance = np.abs(
        left.astype(np.int16) - right.astype(np.int16)
    )
    return {
        "values": int(changed.size),
        "changedValues": int(np.count_nonzero(changed)),
        "exactValueFraction": float(np.mean(~changed)),
        "pixels": int(changed_pixels.size),
        "changedPixels": int(np.count_nonzero(changed_pixels)),
        "exactPixelFraction": float(np.mean(~changed_pixels)),
        "maximumAbsoluteCodes": int(distance.max(initial=0)),
        "exact": not bool(np.any(changed)),
    }


def _candidate_summary(
    bounds: CandidateBounds,
    states: list[JsonObject],
) -> tuple[list[JsonObject], JsonObject]:
    reports: list[JsonObject] = []
    noncontiguous = bounds.matched & ~bounds.contiguous
    for state_index, state in enumerate(states):
        counts = bounds.count[state_index]
        matched = counts != 0
        unique = counts == 1
        current_noncontiguous = noncontiguous[state_index]
        reports.append({
            "index": state_index,
            "name": state["name"],
            "requestedBlurRadius":
                state["requestedBlurRadius"],
            "requestedBlurRadiusFloat32Bits":
                state["requestedBlurRadiusFloat32Bits"],
            "targetLodNumerator":
                state["targetLodNumerator"],
            "spatialSignatures": int(counts.size),
            "unmatchedSignatures":
                int(np.count_nonzero(~matched)),
            "uniqueBinSignatures":
                int(np.count_nonzero(unique)),
            "ambiguousBinSignatures":
                int(np.count_nonzero(counts > 1)),
            "noncontiguousCandidateSignatures":
                int(np.count_nonzero(current_noncontiguous)),
            "candidateCountMinimum":
                int(counts.min()),
            "candidateCountMaximum":
                int(counts.max(initial=0)),
            "candidateLowerMinimum": (
                int(bounds.lower[state_index, matched].min())
                if np.any(matched)
                else None
            ),
            "candidateUpperMaximum": (
                int(bounds.upper[state_index, matched].max())
                if np.any(matched)
                else None
            ),
        })
    return reports, {
        "spatialSignatures": int(bounds.count.size),
        "unmatchedSignatures":
            int(np.count_nonzero(~bounds.matched)),
        "uniqueBinSignatures":
            int(np.count_nonzero(bounds.count == 1)),
        "ambiguousBinSignatures":
            int(np.count_nonzero(bounds.count > 1)),
        "noncontiguousCandidateSignatures":
            int(np.count_nonzero(noncontiguous)),
        "allSignaturesMatched": bool(np.all(bounds.matched)),
        "allCandidateSetsContiguous": not bool(
            np.any(noncontiguous)
        ),
    }


def _production_site_histograms(
    bounds: CandidateBounds,
) -> list[JsonObject]:
    count = bounds.count[PRODUCTION_STATE_INDEX].reshape(
        SITE_COUNT,
        PATCH_SIDE,
        PATCH_SIDE,
    )
    lower = bounds.lower[PRODUCTION_STATE_INDEX].reshape(
        SITE_COUNT,
        PATCH_SIDE,
        PATCH_SIDE,
    )
    result: list[JsonObject] = []
    for site_index in range(SITE_COUNT):
        unique = count[site_index] == 1
        histogram = np.bincount(
            lower[site_index, unique],
            minlength=BIN_COUNT,
        )
        result.append({
            "siteIndex": site_index,
            "spatialSignatures": int(unique.size),
            "uniqueBinSignatures":
                int(np.count_nonzero(unique)),
            "uniqueBinHistogram": {
                str(index): int(value)
                for index, value in enumerate(histogram)
                if value
            },
        })
    return result


def _write_candidate_map(
    path: Path,
    bounds: CandidateBounds,
) -> JsonObject:
    shape = (
        STATE_COUNT,
        SITE_COUNT,
        PATCH_SIDE,
        PATCH_SIDE,
    )
    with path.open("wb") as stream:
        np.savez_compressed(
            stream,
            candidate_count=bounds.count.reshape(shape),
            candidate_lower=bounds.lower.reshape(shape),
            candidate_upper=bounds.upper.reshape(shape),
        )
    return {
        "path": str(path),
        "sha256": sha256_file(path),
        "arrays": {
            "candidate_count": list(shape),
            "candidate_lower": list(shape),
            "candidate_upper": list(shape),
        },
        "dtype": "uint16",
        "unmatchedSentinel": BIN_COUNT,
    }


def analyze(
    default_path: Path,
    flat_path: Path,
    *,
    map_output: Path | None = None,
) -> JsonObject:
    started = time.perf_counter()
    default = LodSweep.open(default_path)
    flat = LodSweep.open(flat_path)
    if default.manifest["rigVersion"] != EXPECTED_RIG:
        raise ValueError("first LOD corpus is not the default profile")
    if flat.manifest["rigVersion"] != FLAT_RIG:
        raise ValueError("second LOD corpus is not the flat profile")
    for key in ("osVersion", "architecture", "sourceDesign", "lodDesign"):
        if default.manifest.get(key) != flat.manifest.get(key):
            raise ValueError(
                f"default and flat LOD corpus metadata differ: {key}"
            )

    controls = _difference_metrics(default.control, flat.control)
    radius_zero = _difference_metrics(
        default.identity[:, 0],
        flat.identity[:, 0],
    )
    flat_production_duplicate = _difference_metrics(
        flat.identity[:, PRODUCTION_STATE_INDEX],
        flat.identity[:, 37],
    )

    default_signatures = response_signatures(default.identity)
    flat_signatures = response_signatures(flat.identity)
    default_words = exact_signature_words(default_signatures)
    flat_words = exact_signature_words(
        flat_signatures[:BIN_COUNT]
    )
    bounds = exact_catalog_candidates(
        default_words,
        flat_words,
    )
    states = default.manifest["lodDesign"]["states"]
    state_reports, totals = _candidate_summary(bounds, states)
    map_record = (
        _write_candidate_map(map_output, bounds)
        if map_output is not None
        else None
    )
    elapsed = time.perf_counter() - started
    exact_explanation = (
        "Each key contains all 15 native RGB8 values from amplitudes "
        "0, 1, 8, 32, and 127. The bytes are packed losslessly into "
        "two uint64 words. Candidate equality is therefore direct "
        "byte equality, not hashing, fitting, or tolerance."
    )
    return {
        "liquidGlassLodCrossMatchSchemaVersion": 1,
        "analysisImplementation": {
            "file":
                "analysis/liquid_glass_lod_cross_match.py",
            "sha256": sha256_file(Path(__file__).resolve()),
            "python": platform.python_version(),
            "numpy": np.__version__,
        },
        "sources": {
            "default": {
                "path": str(default_path),
                "sha256": (
                    sha256_file(default_path)
                    if default_path.is_file()
                    else None
                ),
                "ciCommit": default.manifest["ciCommit"],
                "rigVersion": default.manifest["rigVersion"],
            },
            "flat": {
                "path": str(flat_path),
                "sha256": (
                    sha256_file(flat_path)
                    if flat_path.is_file()
                    else None
                ),
                "ciCommit": flat.manifest["ciCommit"],
                "rigVersion": flat.manifest["rigVersion"],
            },
            "osVersion": default.manifest["osVersion"],
            "architecture": default.manifest["architecture"],
            "sourceAndLodDesignExact": True,
        },
        "crossRunControls": {
            "sourceControls": controls,
            "requestedRadiusZero": radius_zero,
            "flatProductionVsGridBin37":
                flat_production_duplicate,
        },
        "exactCrossRadiusCatalogMatching": {
            "key": exact_explanation,
            "flatRequestedRadiusStates": BIN_COUNT,
            "limitation": (
                "The flat states remove spatial SDF conditioning but "
                "do not hold Apple's upstream mip resource fixed across "
                "requested radii. Exact matches are reported directly; "
                "an unmatched signature falsifies the hypothesis that "
                "the catalog is a bitwise-complete LOD oracle."
            ),
            "signatureAmplitudes": list(AMPLITUDES),
            "nativeChannelsPerAmplitude": CHANNELS,
            "totals": totals,
            "states": state_reports,
            "productionRadiusOneBySite":
                _production_site_histograms(bounds),
            "candidateMap": map_record,
        },
        "resourceMeasurements": {
            "analysisSeconds": elapsed,
            "maximumResidentSetKiB":
                resource.getrusage(
                    resource.RUSAGE_SELF
                ).ru_maxrss,
        },
        "conclusion": {
            "crossRunSourceControlsExact": controls["exact"],
            "radiusZeroCrossRunExact": radius_zero["exact"],
            "flatProductionAndGridBin37Exact":
                flat_production_duplicate["exact"],
            "defaultProfileExplainedByCrossRadiusFlatCatalog":
                totals["allSignaturesMatched"],
            "productionShaderAuthorized": False,
            "requiredGate":
                "zero unequal channels on protected Apple captures",
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Bit-match the default SDF-conditioned Liquid Glass "
            "LOD corpus against the cross-radius flat-profile catalog."
        )
    )
    parser.add_argument("default_lod_sweep", type=Path)
    parser.add_argument("flat_lod_sweep", type=Path)
    parser.add_argument("--map-output", type=Path)
    parser.add_argument("-o", "--output", type=Path)
    arguments = parser.parse_args()
    result = analyze(
        arguments.default_lod_sweep,
        arguments.flat_lod_sweep,
        map_output=arguments.map_output,
    )
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
