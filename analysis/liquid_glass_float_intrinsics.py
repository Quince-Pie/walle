#!/usr/bin/env python3
"""Validate and pack Apple's exhaustive binary32 fast-intrinsic tables."""

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray


type JsonObject = dict[str, Any]
type Int8Array = NDArray[np.int8]
type UInt8Array = NDArray[np.uint8]
type UInt32Array = NDArray[np.uint32]

MANTISSA_COUNT = 1 << 23
MANTISSA_MASK = MANTISSA_COUNT - 1
PAIR_RECORD_COUNT = MANTISSA_COUNT * 2
PAIR_RECORD_STRIDE = 2
EXPECTED_RIG = "metal-float-intrinsic-probe-1.0.0"
PAIR_FILE = "float-fast-sqrt-rsqrt-deltas-i8.bin"
RECIPROCAL_FILE = "float-fast-reciprocal-deltas-i8.bin"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def distribution(values: Int8Array) -> JsonObject:
    unique, counts = np.unique(values, return_counts=True)
    probabilities = counts.astype(np.float64) / values.size
    entropy = -float(
        np.sum(probabilities * np.log2(probabilities))
    )
    return {
        "counts": {
            str(int(value)): int(count)
            for value, count in zip(unique, counts, strict=True)
        },
        "entropyBitsPerValue": entropy,
        "minimum": int(unique[0]),
        "maximum": int(unique[-1]),
    }


def load_tables(
    capture: Path,
) -> tuple[JsonObject, Int8Array, Int8Array]:
    manifest = json.loads(
        (capture / "manifest.json").read_text(encoding="utf-8")
    )
    if manifest.get("schemaVersion") != 1:
        raise ValueError("float-intrinsic schema differs")
    if manifest.get("rigVersion") != EXPECTED_RIG:
        raise ValueError("float-intrinsic rig differs")
    validation = manifest.get("exponentInvarianceValidation")
    if not isinstance(validation, dict) or not validation.get("exact"):
        raise ValueError("float-intrinsic exponent gate did not pass")

    pair_path = capture / PAIR_FILE
    reciprocal_path = capture / RECIPROCAL_FILE
    pair_record = manifest.get("sqrtRsqrt")
    reciprocal_record = manifest.get("reciprocal")
    if not isinstance(pair_record, dict) or not isinstance(
        reciprocal_record, dict
    ):
        raise ValueError("float-intrinsic file metadata is missing")
    if pair_path.stat().st_size != PAIR_RECORD_COUNT * PAIR_RECORD_STRIDE:
        raise ValueError("sqrt/rsqrt table size differs")
    if reciprocal_path.stat().st_size != MANTISSA_COUNT:
        raise ValueError("reciprocal table size differs")
    if sha256_file(pair_path) != pair_record.get("fileSha256"):
        raise ValueError("sqrt/rsqrt table digest differs")
    if sha256_file(reciprocal_path) != reciprocal_record.get(
        "fileSha256"
    ):
        raise ValueError("reciprocal table digest differs")

    pair = np.memmap(
        pair_path,
        dtype=np.int8,
        mode="r",
        shape=(2, MANTISSA_COUNT, 2),
    )
    reciprocal = np.memmap(
        reciprocal_path,
        dtype=np.int8,
        mode="r",
        shape=(MANTISSA_COUNT,),
    )
    return manifest, pair, reciprocal


def pack_intrinsic_deltas(
    pair: Int8Array,
    reciprocal: Int8Array,
) -> tuple[UInt8Array, tuple[int, ...]]:
    if pair.shape != (2, MANTISSA_COUNT, 2):
        raise ValueError("sqrt/rsqrt table shape differs")
    if reciprocal.shape != (MANTISSA_COUNT,):
        raise ValueError("reciprocal table shape differs")
    sqrt_even = np.asarray(pair[0, :, 0], dtype=np.int16)
    sqrt_odd = np.asarray(pair[1, :, 0], dtype=np.int16)
    rsqrt_even = np.asarray(pair[0, :, 1], dtype=np.int16)
    rsqrt_odd = np.asarray(pair[1, :, 1], dtype=np.int16)
    reciprocal_wide = np.asarray(reciprocal, dtype=np.int16)
    if not np.all((-1 <= sqrt_even) & (sqrt_even <= 2)):
        raise ValueError("even sqrt deltas exceed two bits")
    if not np.all((-1 <= sqrt_odd) & (sqrt_odd <= 2)):
        raise ValueError("odd sqrt deltas exceed two bits")
    if not np.all((-1 <= reciprocal_wide) & (reciprocal_wide <= 2)):
        raise ValueError("reciprocal deltas exceed two bits")
    special_even = np.flatnonzero(rsqrt_even == -1)
    special_odd = np.flatnonzero(rsqrt_odd == -1)
    if not np.array_equal(special_even, special_odd):
        raise ValueError("rsqrt negative exceptions differ by parity")
    if not np.all(np.isin(rsqrt_even, (-1, 0, 1))):
        raise ValueError("even rsqrt deltas exceed one bit")
    if not np.all(np.isin(rsqrt_odd, (-1, 0, 1))):
        raise ValueError("odd rsqrt deltas exceed one bit")

    packed = np.asarray(
        (sqrt_even + 1)
        | ((sqrt_odd + 1) << 2)
        | ((rsqrt_even == 1).astype(np.int16) << 4)
        | ((rsqrt_odd == 1).astype(np.int16) << 5)
        | ((reciprocal_wide + 1) << 6),
        dtype=np.uint8,
    )
    return packed, tuple(int(value) for value in special_even)


def validate_packed_table(
    packed: UInt8Array,
    pair: Int8Array,
    reciprocal: Int8Array,
    rsqrt_negative_exceptions: tuple[int, ...],
) -> None:
    if packed.shape != (MANTISSA_COUNT,):
        raise ValueError("packed intrinsic table shape differs")
    exceptions = np.zeros(MANTISSA_COUNT, dtype=bool)
    exceptions[np.asarray(rsqrt_negative_exceptions)] = True
    recovered = (
        (packed & np.uint8(0x03)).astype(np.int8) - 1,
        ((packed >> np.uint8(2)) & np.uint8(0x03)).astype(np.int8)
        - 1,
        ((packed >> np.uint8(4)) & np.uint8(0x01)).astype(np.int8),
        ((packed >> np.uint8(5)) & np.uint8(0x01)).astype(np.int8),
        ((packed >> np.uint8(6)) & np.uint8(0x03)).astype(np.int8)
        - 1,
    )
    recovered[2][exceptions] = -1
    recovered[3][exceptions] = -1
    expected = (
        pair[0, :, 0],
        pair[1, :, 0],
        pair[0, :, 1],
        pair[1, :, 1],
        reciprocal,
    )
    if not all(
        np.array_equal(candidate, reference)
        for candidate, reference in zip(
            recovered, expected, strict=True
        )
    ):
        raise ValueError("packed intrinsic table is not lossless")


def predicted_fast_bits(
    inputs: UInt32Array,
    pair: Int8Array,
    field: int,
) -> UInt32Array:
    exponent = (inputs >> np.uint32(23)) & np.uint32(0xFF)
    mantissa = inputs & np.uint32(MANTISSA_MASK)
    parity = exponent & np.uint32(1)
    values = inputs.view(np.float32)
    if field == 0:
        baseline = np.sqrt(values, dtype=np.float32)
    elif field == 1:
        baseline = np.asarray(
            1.0 / np.sqrt(values.astype(np.float64)),
            dtype=np.float32,
        )
    else:
        raise ValueError("intrinsic field must be sqrt or rsqrt")
    deltas = pair[
        parity.astype(np.intp),
        mantissa.astype(np.intp),
        field,
    ]
    return np.asarray(
        baseline.view(np.uint32).astype(np.int64)
        + deltas.astype(np.int64),
        dtype=np.uint32,
    )


def predicted_reciprocal_bits(
    inputs: UInt32Array,
    reciprocal: Int8Array,
) -> UInt32Array:
    mantissa = inputs & np.uint32(MANTISSA_MASK)
    values = inputs.view(np.float32)
    baseline = np.asarray(1.0 / values.astype(np.float64), dtype=np.float32)
    deltas = reciprocal[mantissa.astype(np.intp)]
    return np.asarray(
        baseline.view(np.uint32).astype(np.int64)
        + deltas.astype(np.int64),
        dtype=np.uint32,
    )


def load_uint_trace(path: Path) -> UInt32Array:
    values = np.fromfile(path, dtype="<u4")
    expected = 1024 * 1024 * 4
    if values.size != expected:
        raise ValueError(f"{path} uint trace size differs")
    return values.reshape(1024, 1024, 4)[112:912, 112:912]


def validate_sdf_traces(
    capture: Path,
    pair: Int8Array,
    reciprocal: Int8Array,
) -> JsonObject:
    sdf_float = load_uint_trace(
        capture
        / "carenderer-live-tree-glass-sdf-float-"
        "numeric-trace-rgba32ui.raw"
    )
    normal = load_uint_trace(
        capture
        / "carenderer-live-tree-glass-sdf-normal-"
        "numeric-trace-rgba32ui.raw"
    )
    geometry = load_uint_trace(
        capture
        / "carenderer-live-tree-glass-sdf-geometry-"
        "numeric-trace-rgba32ui.raw"
    )

    sqrt_prediction = predicted_fast_bits(
        sdf_float[:, :, 0], pair, 0
    )
    rsqrt_prediction = predicted_fast_bits(normal[:, :, 0], pair, 1)
    circle_scale = np.asarray([0x4418DDD3], dtype=np.uint32)
    reciprocal_bits = predicted_reciprocal_bits(
        circle_scale, reciprocal
    )[0]
    reciprocal_value = np.asarray(
        [reciprocal_bits], dtype=np.uint32
    ).view(np.float32)[0]
    normalized_prediction = np.asarray(
        geometry[:, :, :2].view(np.float32) * reciprocal_value,
        dtype=np.float32,
    ).view(np.uint32)
    sqrt_mismatches = int(
        np.count_nonzero(sqrt_prediction != sdf_float[:, :, 1])
    )
    rsqrt_mismatches = int(
        np.count_nonzero(rsqrt_prediction != normal[:, :, 1])
    )
    reciprocal_mismatches = int(
        np.count_nonzero(
            normalized_prediction != geometry[:, :, 2:4]
        )
    )
    return {
        "observedSqrtValues": int(sqrt_prediction.size),
        "sqrtMismatches": sqrt_mismatches,
        "observedRsqrtValues": int(rsqrt_prediction.size),
        "rsqrtMismatches": rsqrt_mismatches,
        "observedReciprocalProducts": int(
            normalized_prediction.size
        ),
        "reciprocalProductMismatches": reciprocal_mismatches,
        "circleScaleBits": f"0x{int(circle_scale[0]):08x}",
        "circleScaleReciprocalBits":
            f"0x{int(reciprocal_bits):08x}",
        "exact": (
            sqrt_mismatches == 0
            and rsqrt_mismatches == 0
            and reciprocal_mismatches == 0
        ),
    }


def analyze(
    intrinsic_capture: Path,
    sdf_capture: Path,
    packed_output: Path,
) -> JsonObject:
    manifest, pair, reciprocal = load_tables(intrinsic_capture)
    packed, exceptions = pack_intrinsic_deltas(pair, reciprocal)
    validate_packed_table(packed, pair, reciprocal, exceptions)
    packed_output.parent.mkdir(parents=True, exist_ok=True)
    packed.tofile(packed_output)
    trace_validation = validate_sdf_traces(
        sdf_capture, pair, reciprocal
    )
    if not trace_validation["exact"]:
        raise ValueError("exhaustive tables do not reproduce SDF traces")
    return {
        "schemaVersion": 1,
        "sources": {
            "intrinsicCapture": str(intrinsic_capture),
            "sdfCapture": str(sdf_capture),
            "ciCommit": manifest["ciCommit"],
        },
        "distributions": {
            "sqrtEvenExponent": distribution(pair[0, :, 0]),
            "sqrtOddExponent": distribution(pair[1, :, 0]),
            "rsqrtEvenExponent": distribution(pair[0, :, 1]),
            "rsqrtOddExponent": distribution(pair[1, :, 1]),
            "reciprocal": distribution(reciprocal),
        },
        "packedTable": {
            "file": str(packed_output),
            "fileBytes": packed_output.stat().st_size,
            "fileSha256": sha256_file(packed_output),
            "dimensionsR8UI": [4096, 2048],
            "layout": {
                "bits0To1": "even-exponent sqrt delta + 1",
                "bits2To3": "odd-exponent sqrt delta + 1",
                "bit4": "even-exponent rsqrt delta is +1",
                "bit5": "odd-exponent rsqrt delta is +1",
                "bits6To7": "reciprocal delta + 1",
            },
            "rsqrtNegativeExceptionMantissas": list(exceptions),
            "lossless": True,
        },
        "sdfTraceValidation": trace_validation,
        "gate": {
            "exponentInvarianceExact": True,
            "packedTableLossless": True,
            "sdfInputsBitExact": True,
        },
    }


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("intrinsic_capture", type=Path)
    parser.add_argument("sdf_capture", type=Path)
    parser.add_argument("--packed-output", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def main() -> int:
    arguments = parse_arguments()
    report = analyze(
        arguments.intrinsic_capture,
        arguments.sdf_capture,
        arguments.packed_output,
    )
    encoded = json.dumps(report, indent=2, sort_keys=True) + "\n"
    print(encoded, end="")
    if arguments.output is not None:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(encoded, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
