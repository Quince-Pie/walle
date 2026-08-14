#!/usr/bin/env python3
"""Test affine endpoint-translation invariance in retained Metal clip records."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Final

import numpy as np
from numpy.typing import NDArray


ROOT: Final = Path(__file__).resolve().parent.parent
LG_ANALYSIS: Final = ROOT / "lg-test" / "Analysis"
sys.path.insert(0, str(LG_ANALYSIS))

import analyze_raster_clip_arithmetic_discriminator as clip_analysis  # noqa: E402
import analyze_raster_clip_boundary_tomography as boundary_analysis  # noqa: E402
import validate_raster_clip_arithmetic_discriminator as capture  # noqa: E402


type JsonObject = dict[str, object]
type RecordArray = NDArray[np.uint32]

EXPECTED_BYTES: Final = capture.RAW_BYTES
INTERPOLANT_WORDS: Final = np.asarray(
    [
        4 * vector + component
        for vector in (2, 3, 4, 6, 7, 8, 10, 11, 12, 14, 15, 16)
        for component in range(4)
    ],
    dtype=np.intp,
)
DERIVATIVE_WORDS: Final = np.asarray(
    [
        4 * (5 + 4 * (witness // 4)) + witness % 4
        for witness in range(capture.WITNESS_COUNT)
    ],
    dtype=np.intp,
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _load(path: Path) -> RecordArray:
    if path.stat().st_size != EXPECTED_BYTES:
        raise ValueError(f"{path} has the wrong byte length")
    return np.memmap(
        path,
        dtype="<u4",
        mode="r",
        shape=(capture.CASE_COUNT, capture.SAMPLE_COUNT, capture.RECORD_WORD_COUNT),
    )


def _offset_distribution(
    left: NDArray[np.uint32], right: NDArray[np.uint32]
) -> JsonObject:
    offsets, counts = np.unique(
        left.astype(np.int64) - right.astype(np.int64),
        return_counts=True,
    )
    return {
        str(int(offset)): int(count)
        for offset, count in zip(offsets, counts, strict=True)
    }


def _average_float_bits(
    left: NDArray[np.uint32], right: NDArray[np.uint32]
) -> NDArray[np.uint32]:
    left_values = np.ascontiguousarray(left).view(np.float32)
    right_values = np.ascontiguousarray(right).view(np.float32)
    summed = np.add(left_values, right_values, dtype=np.float32)
    averaged = np.multiply(summed, np.float32(0.5), dtype=np.float32)
    return averaged.view(np.uint32)


def _superposition_word_report(
    symmetric: RecordArray,
    zero_low: RecordArray,
    zero_high: RecordArray,
    words: NDArray[np.intp],
) -> JsonObject:
    observed = symmetric[:, :, words]
    predicted = _average_float_bits(
        zero_low[:, :, words],
        zero_high[:, :, words],
    )
    different = predicted != observed
    return {
        "wordCount": int(observed.size),
        "matchCount": int(observed.size - np.count_nonzero(different)),
        "mismatchCount": int(np.count_nonzero(different)),
        "mismatchRecordCount": int(np.count_nonzero(np.any(different, axis=2))),
        "predictedMinusObservedBitOffsetDistribution": _offset_distribution(
            predicted,
            observed,
        ),
    }


def _superposition_report(records: dict[str, RecordArray]) -> JsonObject:
    return {
        "identity": "symmetric == f32((zero-low + zero-high) * 0.5)",
        "interpolants": _superposition_word_report(
            records["symmetric"],
            records["zero-low"],
            records["zero-high"],
            INTERPOLANT_WORDS,
        ),
        "derivatives": _superposition_word_report(
            records["symmetric"],
            records["zero-low"],
            records["zero-high"],
            DERIVATIVE_WORDS,
        ),
    }


def _pair_report(
    left_name: str,
    left: RecordArray,
    right_name: str,
    right: RecordArray,
) -> JsonObject:
    left_derivatives = left[:, :, DERIVATIVE_WORDS]
    right_derivatives = right[:, :, DERIVATIVE_WORDS]
    different = left_derivatives != right_derivatives
    different_by_distance = different.reshape(
        capture.GROUP_COUNT,
        capture.DISTANCE_COUNT,
        capture.SAMPLE_COUNT,
        capture.WITNESS_COUNT,
    ).sum(axis=(0, 2, 3))
    selected_distances = sorted(
        {
            0,
            1,
            2,
            3,
            capture.DISTANCE_COUNT // 2,
            capture.DISTANCE_COUNT - 2,
            capture.DISTANCE_COUNT - 1,
        }
    )
    by_witness = []
    for witness in range(capture.WITNESS_COUNT):
        witness_different = different[:, :, witness]
        by_witness.append(
            {
                "witnessIndex": witness,
                "deltaBits": f"0x{capture.DELTA_BITS[witness]:08x}",
                "differentCount": int(np.count_nonzero(witness_different)),
                "offsetDistribution": _offset_distribution(
                    left_derivatives[:, :, witness],
                    right_derivatives[:, :, witness],
                ),
            }
        )
    return {
        "left": left_name,
        "right": right_name,
        "derivativeWordCount": int(left_derivatives.size),
        "differentDerivativeWordCount": int(np.count_nonzero(different)),
        "differentDerivativeRecordCount": int(
            np.count_nonzero(np.any(different, axis=2))
        ),
        "distanceCountWithAnyDifference": int(np.count_nonzero(different_by_distance)),
        "selectedDistanceDifferentCounts": {
            str(distance): int(different_by_distance[distance])
            for distance in selected_distances
        },
        "offsetDistribution": _offset_distribution(left_derivatives, right_derivatives),
        "byWitness": by_witness,
    }


def analyze(paths: dict[str, Path], *, recover_effective_deltas: bool) -> JsonObject:
    records = {name: _load(path) for name, path in paths.items()}
    recoveries: dict[str, JsonObject] = {}
    recovered_values: dict[str, dict[tuple[int, int], int]] = {}
    if recover_effective_deltas:
        _cases, groups = capture.case_catalog()
        selectors = boundary_analysis.load_fractional_selectors()
        for name, current in records.items():
            recovered, recovery = clip_analysis.recover_matched_scale_effective_deltas(
                current,
                groups,
                selectors,
            )
            recoveries[name] = recovery
            recovered_values[name] = {
                (distance, witness): observed
                for distance, witness, _source, _exact, observed in recovered
            }
    if recovered_values:
        shared_keys = set.intersection(
            *(set(values) for values in recovered_values.values())
        )
        shared_equal = sum(
            len({values[key] for values in recovered_values.values()}) == 1
            for key in shared_keys
        )
    else:
        shared_keys = set()
        shared_equal = 0
    return {
        "schemaVersion": 1,
        "classification": "output-blind affine endpoint-translation discriminator",
        "inputs": {
            name: {
                "path": str(path),
                "bytes": path.stat().st_size,
                "sha256": _sha256(path),
            }
            for name, path in paths.items()
        },
        "interpolantWords": [int(word) for word in INTERPOLANT_WORDS],
        "derivativeWords": [int(word) for word in DERIVATIVE_WORDS],
        "endpointImpulseSuperposition": _superposition_report(records),
        "pairs": [
            _pair_report(
                "symmetric", records["symmetric"], "zero-low", records["zero-low"]
            ),
            _pair_report(
                "symmetric", records["symmetric"], "zero-high", records["zero-high"]
            ),
            _pair_report(
                "zero-low", records["zero-low"], "zero-high", records["zero-high"]
            ),
        ],
        "effectiveDeltaRecovery": recoveries,
        "effectiveDeltaRecoveryRun": recover_effective_deltas,
        "sharedUniqueCoefficientCount": len(shared_keys),
        "sharedUniqueEqualAcrossModesCount": shared_equal,
        "sharedUniqueDifferentAcrossModesCount": len(shared_keys) - shared_equal,
        "interpretation": {
            "sameMathematicalDelta": True,
            "commonOffsetChangesOnly": True,
            "zeroDifferencesWouldSupportDeltaDomainSetup": True,
            "nonzeroDifferencesWouldSupportEndpointDomainRounding": True,
            "referencePixelsRead": False,
        },
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symmetric", type=Path, required=True)
    parser.add_argument("--zero-low", type=Path, required=True)
    parser.add_argument("--zero-high", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--skip-recovery", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    report = analyze(
        {
            "symmetric": args.symmetric,
            "zero-low": args.zero_low,
            "zero-high": args.zero_high,
        },
        recover_effective_deltas=not args.skip_recovery,
    )
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output is None:
        print(rendered, end="")
    else:
        args.output.write_text(rendered, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
