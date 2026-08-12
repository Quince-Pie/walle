#!/usr/bin/env python3
"""Separate native WindowServer and canonical-sRGB Liquid Glass samples."""

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

from liquid_glass_face_stage import (
    STATE_PARAMETERS,
    half_fused_multiply_add,
    luminance_chroma_half_rgb_fma,
)
from liquid_glass_pair_sweep import (
    PairSweep,
    bytes_sha256,
    collect_samples,
    difference_metrics,
    prediction_metrics,
    unique_mapping,
)


type IntArray = NDArray[np.int64]
type JsonObject = dict[str, Any]

NATIVE_SCHEMA_VERSION = 1
NATIVE_RECORD_STRIDE = 3
RECOVERED_MATRIX_BITS = np.asarray(
    (
        (15425, 8564, 5314),
        (6795, 15432, 5207),
        (6763, 8581, 15423),
    ),
    dtype=np.uint16,
)
EXPECTED_BIAS_BITS = 11469


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def decode_rgb8_records(
    data: bytes,
    *,
    expected_records: int,
) -> IntArray:
    expected_bytes = expected_records * NATIVE_RECORD_STRIDE
    if len(data) != expected_bytes:
        raise ValueError(
            f"native RGB stream has {len(data)} bytes, "
            f"expected {expected_bytes}"
        )
    return np.frombuffer(data, dtype=np.uint8).reshape(
        expected_records,
        NATIVE_RECORD_STRIDE,
    ).astype(np.int64)


def read_native_samples(
    sweep: PairSweep,
    *,
    expected_records: int,
) -> tuple[IntArray, IntArray, JsonObject]:
    evidence = sweep.manifest.get("nativeCaptureEvidence")
    if not isinstance(evidence, dict):
        raise ValueError("pair sweep has no native capture evidence")
    if evidence.get("schemaVersion") != NATIVE_SCHEMA_VERSION:
        raise ValueError("unexpected native capture evidence schema")
    if evidence.get("recordFormat") != "RGB8":
        raise ValueError("unexpected native capture record format")
    if evidence.get("recordStrideBytes") != NATIVE_RECORD_STRIDE:
        raise ValueError("unexpected native capture record stride")
    if evidence.get("recordCount") != expected_records:
        raise ValueError("unexpected native capture record count")

    control_bytes = sweep.file_bytes(evidence["controlFile"])
    clear_bytes = sweep.file_bytes(evidence["clearFile"])
    for label, data in (
        ("control", control_bytes),
        ("clear", clear_bytes),
    ):
        if len(data) != evidence[f"{label}FileBytes"]:
            raise ValueError(f"native {label} byte count differs")
        if bytes_sha256(data) != evidence[f"{label}FileSha256"]:
            raise ValueError(f"native {label} hash differs")

    source: JsonObject = {
        "schemaVersion": evidence["schemaVersion"],
        "recordOrder": evidence.get("recordOrder"),
        "recordFormat": evidence["recordFormat"],
        "recordStrideBytes": evidence["recordStrideBytes"],
        "recordCount": evidence["recordCount"],
        "controlFile": evidence["controlFile"],
        "controlFileSha256": evidence["controlFileSha256"],
        "clearFile": evidence["clearFile"],
        "clearFileSha256": evidence["clearFileSha256"],
        "captureFormat": evidence.get("captureFormat"),
    }
    if icc_file := evidence.get("iccFile"):
        icc_bytes = sweep.file_bytes(icc_file)
        icc_hash = bytes_sha256(icc_bytes)
        if icc_hash != evidence.get("iccFileSha256"):
            raise ValueError("native capture ICC hash differs")
        source.update({
            "iccFile": icc_file,
            "iccFileBytes": len(icc_bytes),
            "iccFileSha256": icc_hash,
        })

    return (
        decode_rgb8_records(
            control_bytes,
            expected_records=expected_records,
        ),
        decode_rgb8_records(
            clear_bytes,
            expected_records=expected_records,
        ),
        source,
    )


def recovered_half_face(codes: IntArray) -> IntArray:
    normalized = (
        codes.astype(np.float32) / np.float32(255)
    ).astype(np.float16)
    matrix = RECOVERED_MATRIX_BITS.view(np.float16)
    bias = np.asarray(
        (EXPECTED_BIAS_BITS,),
        dtype=np.uint16,
    ).view(np.float16)[0]
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
        np.float16(0.97),
        np.zeros_like(value),
    )
    return np.clip(
        np.rint(value.astype(np.float32) * np.float32(255)),
        0,
        255,
    ).astype(np.int64)


def mapping_record(
    inputs: IntArray,
    outputs: IntArray,
) -> JsonObject:
    _, _, record = unique_mapping(inputs, outputs)
    return record


def analyze(pair_path: Path) -> JsonObject:
    started = time.perf_counter()
    with PairSweep.open(pair_path) as sweep:
        samples, canonical_controls = collect_samples(sweep)
        native_control, native_clear, native_source = (
            read_native_samples(
                sweep,
                expected_records=samples.requested.shape[0],
            )
        )

    expected_requested = luminance_chroma_half_rgb_fma(
        samples.requested,
        *STATE_PARAMETERS["baseline"],
    )
    expected_native_input = luminance_chroma_half_rgb_fma(
        native_control,
        *STATE_PARAMETERS["baseline"],
    )
    recovered_requested = recovered_half_face(samples.requested)
    recovered_native_input = recovered_half_face(native_control)
    recovered_native_metrics = prediction_metrics(
        recovered_native_input,
        native_clear,
    )
    elapsed = time.perf_counter() - started

    return {
        "liquidGlassNativeCaptureAnalysisSchemaVersion": 1,
        "analysisImplementation": {
            "file": "analysis/liquid_glass_native_capture.py",
            "sha256": file_sha256(Path(__file__).resolve()),
            "python": platform.python_version(),
            "numpy": package_version("numpy"),
        },
        "source": {
            "pairSweep": {
                "path": str(pair_path),
                "sha256": file_sha256(pair_path),
            },
            "nativeCaptureEvidence": native_source,
        },
        "measurements": {
            "observations": int(samples.requested.shape[0]),
            "sourceToNativeControl": difference_metrics(
                samples.requested,
                native_control,
            ),
            "nativeToCanonicalControl": difference_metrics(
                native_control,
                samples.inputs,
            ),
            "nativeToCanonicalClear": difference_metrics(
                native_clear,
                samples.outputs,
            ),
            "sourceToCanonicalControl": {
                key: value
                for key, value in canonical_controls.items()
                if key != "patterns"
            },
            "nativeControlToCanonicalMapping":
                mapping_record(native_control, samples.inputs),
            "nativeClearToCanonicalMapping":
                mapping_record(native_clear, samples.outputs),
        },
        "nativeClearPredictions": {
            "parameterDerivedMatrixFromRequested":
                prediction_metrics(
                    expected_requested,
                    native_clear,
                ),
            "parameterDerivedMatrixFromNativeControl":
                prediction_metrics(
                    expected_native_input,
                    native_clear,
                ),
            "recoveredMatrixFromRequested":
                prediction_metrics(
                    recovered_requested,
                    native_clear,
                ),
            "recoveredMatrixFromNativeControl":
                recovered_native_metrics,
        },
        "canonicalClearPredictions": {
            "parameterDerivedMatrixFromRequested":
                prediction_metrics(
                    expected_requested,
                    samples.outputs,
                ),
            "recoveredMatrixFromRequested":
                prediction_metrics(
                    recovered_requested,
                    samples.outputs,
                ),
        },
        "recoveredMatrix": {
            "status": (
                "bit-exact for the measured native point stage; "
                "the complete production shader is not authorized"
            ),
            "rowsBinary16Bits":
                RECOVERED_MATRIX_BITS.astype(int).tolist(),
            "biasBinary16Bits": EXPECTED_BIAS_BITS,
        },
        "resourceMeasurements": {
            "analysisSeconds": elapsed,
            "maximumResidentSetKiB":
                resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        },
        "conclusion": {
            "nativeAndCanonicalStagesSeparated": True,
            "nativePointStageBitExact": (
                recovered_native_metrics["missedInputColors"] == 0
            ),
            "productionShaderAuthorized": False,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Analyze native WindowServer and canonical-sRGB pair samples."
        )
    )
    parser.add_argument("pair_sweep", type=Path)
    parser.add_argument("-o", "--output", type=Path)
    arguments = parser.parse_args()
    result = analyze(arguments.pair_sweep)
    encoded = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if arguments.output:
        arguments.output.write_text(encoded)
    else:
        print(encoded, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
