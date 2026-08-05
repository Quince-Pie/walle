#!/usr/bin/env python3
"""Validate Apple Metal's binary16 Liquid Glass dot-product arithmetic."""

import argparse
import hashlib
import json
import platform
import resource
import time
import zipfile
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray


type HalfArray = NDArray[np.float16]
type UInt16Array = NDArray[np.uint16]
type JsonObject = dict[str, Any]

EXPECTED_RECORD_COUNT = 458_752
EXPECTED_RECORD_STRIDE = 32
EXPECTED_MATRIX_BITS = np.asarray(
    (
        (15425, 8574, 5232),
        (6792, 15432, 5232),
        (6792, 8574, 15423),
    ),
    dtype=np.uint16,
)
EXPECTED_BIAS_BITS = 11469


def sha256_bytes(value: bytes | memoryview) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def half_values(bits: UInt16Array) -> HalfArray:
    return np.asarray(bits, dtype=np.uint16).view(np.float16)


def half_fma(
    left: HalfArray,
    right: np.float16,
    accumulator: HalfArray,
) -> HalfArray:
    return (
        left.astype(np.float64)
        * np.float64(right)
        + accumulator.astype(np.float64)
    ).astype(np.float16)


def half_add(
    left: HalfArray,
    right: HalfArray | np.float16,
) -> HalfArray:
    return (
        left.astype(np.float64)
        + np.asarray(right, dtype=np.float64)
    ).astype(np.float16)


def half_multiply(
    left: HalfArray,
    right: HalfArray | np.float16,
) -> HalfArray:
    return (
        left.astype(np.float64)
        * np.asarray(right, dtype=np.float64)
    ).astype(np.float16)


def rgb_ordered_fma_dot(
    inputs: HalfArray,
    matrix: HalfArray,
) -> HalfArray:
    columns: list[HalfArray] = []
    for row in matrix:
        accumulator = np.zeros(inputs.shape[0], dtype=np.float16)
        for channel in range(3):
            accumulator = half_fma(
                inputs[:, channel],
                row[channel],
                accumulator,
            )
        columns.append(accumulator)
    return np.stack(columns, axis=1)


def separate_multiply_add_dot(
    inputs: HalfArray,
    matrix: HalfArray,
) -> HalfArray:
    columns: list[HalfArray] = []
    for row in matrix:
        accumulator = half_multiply(inputs[:, 0], row[0])
        for channel in range(1, 3):
            accumulator = half_add(
                accumulator,
                half_multiply(inputs[:, channel], row[channel]),
            )
        columns.append(accumulator)
    return np.stack(columns, axis=1)


def mismatch_record(
    predicted: UInt16Array,
    measured: UInt16Array,
) -> JsonObject:
    changed = predicted != measured
    return {
        "values": int(changed.size),
        "mismatchedValues": int(np.count_nonzero(changed)),
        "mismatchedRecords": int(
            np.count_nonzero(np.any(changed, axis=1))
        ),
        "exactValueFraction": float(np.mean(~changed)),
        "exactRecordFraction": float(
            np.mean(~np.any(changed, axis=1))
        ),
    }


def read_evidence(path: Path) -> tuple[bytes, bytes]:
    if path.is_dir():
        return (
            (path / "runtime.json").read_bytes(),
            (path / "half-dot.bin").read_bytes(),
        )
    with zipfile.ZipFile(path) as archive:
        return (
            archive.read("runtime.json"),
            archive.read("half-dot.bin"),
        )


def analyze(path: Path) -> JsonObject:
    started = time.perf_counter()
    runtime_bytes, binary_bytes = read_evidence(path)
    runtime = json.loads(runtime_bytes)
    metadata = runtime.get("halfDotEvidence")
    if not isinstance(metadata, dict) or "error" in metadata:
        raise ValueError(f"half-dot evidence is unavailable: {metadata!r}")
    if metadata.get("schemaVersion") != 1:
        raise ValueError("unexpected half-dot evidence schema")
    if metadata.get("recordCount") != EXPECTED_RECORD_COUNT:
        raise ValueError("unexpected half-dot record count")
    if metadata.get("recordStrideBytes") != EXPECTED_RECORD_STRIDE:
        raise ValueError("unexpected half-dot record stride")
    if metadata.get("matrixRowsBinary16Bits") != (
        EXPECTED_MATRIX_BITS.astype(int).tolist()
    ):
        raise ValueError("half-dot matrix differs")
    if metadata.get("biasBinary16Bits") != EXPECTED_BIAS_BITS:
        raise ValueError("half-dot bias differs")
    expected_bytes = EXPECTED_RECORD_COUNT * EXPECTED_RECORD_STRIDE
    if len(binary_bytes) != expected_bytes:
        raise ValueError(
            f"half-dot binary has {len(binary_bytes)} bytes, "
            f"expected {expected_bytes}"
        )

    records = np.frombuffer(binary_bytes, dtype="<u2").reshape(-1, 16)
    inputs = half_values(records[:, 0:3])
    measured_dot = records[:, 4:7]
    measured_biased = records[:, 8:11]
    measured_held = records[:, 12:15]
    matrix = half_values(EXPECTED_MATRIX_BITS)
    bias = half_values(np.asarray(
        (EXPECTED_BIAS_BITS,),
        dtype=np.uint16,
    ))[0]
    holding_bits = np.unique(records[:, 15])
    if holding_bits.size != 1:
        raise ValueError("holding constant varies between records")
    holding = half_values(holding_bits)[0]

    predicted_dot = rgb_ordered_fma_dot(inputs, matrix)
    predicted_biased = half_add(predicted_dot, bias)
    predicted_held = half_multiply(predicted_biased, holding)
    separate_dot = separate_multiply_add_dot(inputs, matrix)

    padding_valid = (
        np.all(records[:, 3] == 0)
        and np.all(records[:, 7] == 0)
        and np.all(records[:, 11] == EXPECTED_BIAS_BITS)
        and np.all(records[:, 15] == holding_bits[0])
    )
    fma_metrics = mismatch_record(
        predicted_dot.view(np.uint16),
        measured_dot,
    )
    bias_metrics = mismatch_record(
        predicted_biased.view(np.uint16),
        measured_biased,
    )
    holding_metrics = mismatch_record(
        predicted_held.view(np.uint16),
        measured_held,
    )
    separate_metrics = mismatch_record(
        separate_dot.view(np.uint16),
        measured_dot,
    )
    exact = (
        fma_metrics["mismatchedValues"] == 0
        and bias_metrics["mismatchedValues"] == 0
        and holding_metrics["mismatchedValues"] == 0
    )
    elapsed = time.perf_counter() - started
    return {
        "liquidGlassHalfDotAnalysisSchemaVersion": 1,
        "analysisImplementation": {
            "file": "analysis/liquid_glass_half_dot.py",
            "sha256": sha256_file(Path(__file__).resolve()),
            "python": platform.python_version(),
            "numpy": np.__version__,
        },
        "source": {
            "path": str(path),
            "sha256": sha256_file(path) if path.is_file() else None,
            "runtimeJsonSha256": sha256_bytes(runtime_bytes),
            "halfDotBinarySha256": sha256_bytes(binary_bytes),
            "osVersion": runtime.get("osVersion"),
            "metalDevice": runtime.get("metalDevice"),
        },
        "measurements": {
            "records": int(records.shape[0]),
            "dotValues": int(measured_dot.size),
            "uniqueInputTriples": int(
                np.unique(records[:, 0:3], axis=0).shape[0]
            ),
            "recordPaddingAndConstantsValid": bool(padding_valid),
            "rgbOrderedCorrectlyRoundedHalfFma": fma_metrics,
            "separateHalfMultiplyThenAdd": separate_metrics,
            "separateHalfBiasAddition": bias_metrics,
            "separateHalfHoldingMultiplication": holding_metrics,
        },
        "recoveredSemantics": {
            "dot": (
                "acc=half_fma(R,row.x,half(0)); "
                "acc=half_fma(G,row.y,acc); "
                "acc=half_fma(B,row.z,acc)"
            ),
            "bias": "correctly rounded binary16 addition",
            "holding": "correctly rounded binary16 multiplication",
        },
        "resourceMeasurements": {
            "analysisSeconds": elapsed,
            "maximumResidentSetKiB":
                resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        },
        "conclusion": {
            "appleHalfArithmeticBitExact": bool(exact),
            "separateMultiplyAddRejected": bool(
                separate_metrics["mismatchedValues"] > 0
            ),
            "productionShaderAuthorized": False,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate raw Apple Metal half-dot evidence."
    )
    parser.add_argument("evidence", type=Path)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    report = analyze(arguments.evidence)
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if arguments.output is None:
        print(rendered, end="")
    else:
        arguments.output.write_text(rendered, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
