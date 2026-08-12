#!/usr/bin/env python3
"""Losslessly pack Apple sqrt/rsqrt corrections for GPU lookup."""

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray


type JsonObject = dict[str, Any]
type UInt8Array = NDArray[np.uint8]
type UInt32Array = NDArray[np.uint32]

MANTISSA_COUNT = 1 << 23
SQRT_MANTISSAS_PER_WORD = 8
RSQRT_MANTISSAS_PER_WORD = 16


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def pack_codes(codes: UInt8Array) -> tuple[UInt32Array, UInt32Array]:
    if codes.ndim != 1 or codes.size == 0 or codes.size % 16 != 0:
        raise ValueError(
            "intrinsic codes must be a non-empty one-dimensional multiple "
            "of 16"
        )
    sqrt_codes = (codes & np.uint8(0x0F)).astype(np.uint32).reshape(
        -1,
        SQRT_MANTISSAS_PER_WORD,
    )
    sqrt_shifts = np.arange(
        SQRT_MANTISSAS_PER_WORD,
        dtype=np.uint32,
    ) * np.uint32(4)
    sqrt_words = np.bitwise_or.reduce(
        sqrt_codes << sqrt_shifts[None, :],
        axis=1,
    ).astype(np.uint32)

    rsqrt_codes = (
        (codes >> np.uint8(4)) & np.uint8(0x03)
    ).astype(np.uint32).reshape(-1, RSQRT_MANTISSAS_PER_WORD)
    rsqrt_shifts = np.arange(
        RSQRT_MANTISSAS_PER_WORD,
        dtype=np.uint32,
    ) * np.uint32(2)
    rsqrt_words = np.bitwise_or.reduce(
        rsqrt_codes << rsqrt_shifts[None, :],
        axis=1,
    ).astype(np.uint32)
    return sqrt_words, rsqrt_words


def validate_lossless(
    codes: UInt8Array,
    sqrt_words: UInt32Array,
    rsqrt_words: UInt32Array,
) -> None:
    mantissas = np.arange(codes.size, dtype=np.uint32)
    recovered_sqrt = (
        sqrt_words[mantissas >> np.uint32(3)]
        >> ((mantissas & np.uint32(7)) * np.uint32(4))
    ) & np.uint32(0x0F)
    recovered_rsqrt = (
        rsqrt_words[mantissas >> np.uint32(4)]
        >> ((mantissas & np.uint32(15)) * np.uint32(2))
    ) & np.uint32(0x03)
    if not np.array_equal(
        recovered_sqrt.astype(np.uint8),
        codes & np.uint8(0x0F),
    ):
        raise ValueError("packed sqrt table is not lossless")
    if not np.array_equal(
        recovered_rsqrt.astype(np.uint8),
        (codes >> np.uint8(4)) & np.uint8(0x03),
    ):
        raise ValueError("packed rsqrt table is not lossless")


def circle_scale_reciprocal_bits(radius: float, codes_path: Path) -> int:
    """Return Apple's corrected reciprocal bits for one uniform radius."""

    circle_constant = np.array(
        [0x3FC3AB4B],
        dtype=np.uint32,
    ).view(np.float32)[0]
    circle_scale = np.float32(np.float32(radius) * circle_constant)
    scale_bits = int(
        np.array([circle_scale], dtype=np.float32).view(np.uint32)[0]
    )
    mantissa = scale_bits & 0x007FFFFF
    with codes_path.open("rb") as stream:
        stream.seek(mantissa)
        encoded = stream.read(1)
    if len(encoded) != 1:
        raise ValueError(
            f"{codes_path} has no reciprocal code for mantissa {mantissa}"
        )
    reciprocal = np.divide(
        np.float32(1.0),
        circle_scale,
        dtype=np.float32,
    )
    reciprocal_bits = int(
        np.array([reciprocal], dtype=np.float32).view(np.uint32)[0]
    )
    delta = ((encoded[0] >> 6) & 3) - 1
    return (reciprocal_bits + delta) & 0xFFFFFFFF


def pack_tables(
    source: Path,
    sqrt_output: Path,
    rsqrt_output: Path,
) -> JsonObject:
    codes = np.fromfile(source, dtype=np.uint8)
    if codes.shape != (MANTISSA_COUNT,):
        raise ValueError(
            f"intrinsic code count is {codes.size}; expected {MANTISSA_COUNT}"
        )
    sqrt_words, rsqrt_words = pack_codes(codes)
    validate_lossless(codes, sqrt_words, rsqrt_words)
    sqrt_output.parent.mkdir(parents=True, exist_ok=True)
    rsqrt_output.parent.mkdir(parents=True, exist_ok=True)
    sqrt_words.astype("<u4", copy=False).tofile(sqrt_output)
    rsqrt_words.astype("<u4", copy=False).tofile(rsqrt_output)
    packed_bytes = sqrt_output.stat().st_size + rsqrt_output.stat().st_size
    return {
        "liquidGlassPackedIntrinsicTablesSchemaVersion": 1,
        "source": {
            "path": str(source),
            "bytes": source.stat().st_size,
            "sha256": sha256_file(source),
        },
        "sqrtTable": {
            "path": str(sqrt_output),
            "bytes": sqrt_output.stat().st_size,
            "sha256": sha256_file(sqrt_output),
            "dimensionsR32UI": [2048, 512],
            "mantissasPerWord": SQRT_MANTISSAS_PER_WORD,
            "bitsPerMantissa": 4,
        },
        "rsqrtTable": {
            "path": str(rsqrt_output),
            "bytes": rsqrt_output.stat().st_size,
            "sha256": sha256_file(rsqrt_output),
            "dimensionsR32UI": [2048, 256],
            "mantissasPerWord": RSQRT_MANTISSAS_PER_WORD,
            "bitsPerMantissa": 2,
            "negativeExceptionMantissas": [651320, 8380416],
        },
        "reciprocal": {
            "gpuTableBytes": 0,
            "policy": (
                "look up the uniform circle-scale mantissa once on the CPU "
                "and upload the corrected reciprocal bits"
            ),
        },
        "gate": {
            "allMantissasValidated": MANTISSA_COUNT,
            "lossless": True,
            "packedGpuBytes": packed_bytes,
            "gpuByteReduction": source.stat().st_size - packed_bytes,
            "gpuByteReductionPercent": (
                100.0
                * (source.stat().st_size - packed_bytes)
                / source.stat().st_size
            ),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("sqrt_output", type=Path)
    parser.add_argument("rsqrt_output", type=Path)
    parser.add_argument("--report", type=Path)
    arguments = parser.parse_args()
    report = pack_tables(
        arguments.source,
        arguments.sqrt_output,
        arguments.rsqrt_output,
    )
    encoded = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if arguments.report is None:
        print(encoded, end="")
    else:
        arguments.report.write_text(encoded, encoding="utf-8")
        print(arguments.report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
