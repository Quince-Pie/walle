#!/usr/bin/env python3
"""Validate Apple's BGRA8Unorm binary16 fixed-function blend path."""

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


type JsonObject = dict[str, Any]
type HalfArray = NDArray[np.float16]
type UInt8Array = NDArray[np.uint8]
type UInt16Array = NDArray[np.uint16]
type UInt32Array = NDArray[np.uint32]

SOURCE_RECORD_COUNT = 0x7C00
ALPHA_COUNT = 0x3C01
DESTINATION_COUNT = 256
ALPHA_DESTINATION_RECORD_COUNT = ALPHA_COUNT * DESTINATION_COUNT
COMBINED_RECORD_COUNT = 2048 * 2048


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def half_round(value: NDArray[Any]) -> HalfArray:
    return np.asarray(value, dtype=np.float64).astype(np.float16)


def half_fma(
    left: NDArray[Any],
    right: NDArray[Any],
    addend: NDArray[Any],
) -> HalfArray:
    return (
        np.asarray(left, dtype=np.float64)
        * np.asarray(right, dtype=np.float64)
        + np.asarray(addend, dtype=np.float64)
    ).astype(np.float16)


def unorm8(value: NDArray[Any]) -> UInt8Array:
    return np.clip(
        np.rint(np.asarray(value, dtype=np.float64) * 255),
        0,
        255,
    ).astype(np.uint8)


def hash32(source: UInt32Array) -> UInt32Array:
    value = source.copy()
    value ^= value >> np.uint32(16)
    value *= np.uint32(0x7FEB352D)
    value ^= value >> np.uint32(15)
    value *= np.uint32(0x846CA68B)
    value ^= value >> np.uint32(16)
    return value


def rgba_prediction(rgb: UInt8Array) -> UInt8Array:
    output = np.empty((rgb.size, 4), dtype=np.uint8)
    output[:, 0] = rgb
    output[:, 1] = rgb
    output[:, 2] = rgb
    output[:, 3] = 255
    return output


def source_conversion_prediction() -> UInt8Array:
    bits = np.arange(SOURCE_RECORD_COUNT, dtype=np.uint16)
    source = bits.view(np.float16)
    return rgba_prediction(unorm8(source))


def alpha_destination_prediction(
    *,
    total_records: int,
) -> UInt8Array:
    index = np.arange(total_records, dtype=np.uint32)
    active = index < ALPHA_DESTINATION_RECORD_COUNT
    alpha_bits = np.where(active, index >> np.uint32(8), 0).astype(
        np.uint16
    )
    destination = np.where(active, index & np.uint32(255), 0).astype(
        np.uint8
    )
    alpha = alpha_bits.view(np.float16)
    destination_half = half_round(
        destination.astype(np.float64) / 255
    )
    factor = half_round(
        np.float64(1) - alpha.astype(np.float64)
    )
    blended = half_fma(
        destination_half,
        factor,
        np.zeros(total_records, dtype=np.float16),
    )
    return rgba_prediction(unorm8(blended))


def combined_prediction(*, total_records: int) -> UInt8Array:
    index = np.arange(total_records, dtype=np.uint32)
    source_bits = (
        hash32(index ^ np.uint32(0x243F6A88))
        & np.uint32(0x3FFF)
    ).astype(np.uint16)
    alpha_bits = (
        hash32(index ^ np.uint32(0x85A308D3))
        % np.uint32(ALPHA_COUNT)
    ).astype(np.uint16)
    destination = (
        hash32(index ^ np.uint32(0x13198A2E))
        >> np.uint32(24)
    ).astype(np.uint8)
    source = source_bits.view(np.float16)
    alpha = alpha_bits.view(np.float16)
    destination_half = half_round(
        destination.astype(np.float64) / 255
    )
    factor = half_round(
        np.float64(1) - alpha.astype(np.float64)
    )
    blended = half_fma(destination_half, factor, source)
    return rgba_prediction(unorm8(blended))


def comparison(
    measured: UInt8Array,
    predicted: UInt8Array,
) -> JsonObject:
    if measured.shape != predicted.shape:
        raise ValueError(
            f"blend shapes differ: {measured.shape} != {predicted.shape}"
        )
    delta = predicted.astype(np.int16) - measured.astype(np.int16)
    changed = delta != 0
    return {
        "exact": not bool(np.any(changed)),
        "observedBytes": int(delta.size),
        "mismatchedBytes": int(np.count_nonzero(changed)),
        "mismatchedPixels": int(
            np.count_nonzero(np.any(changed, axis=1))
        ),
        "maximumCodeDelta": int(np.abs(delta).max(initial=0)),
    }


def case_metadata(
    evidence: JsonObject,
    name: str,
) -> JsonObject:
    cases = evidence.get("cases")
    if not isinstance(cases, list):
        raise ValueError("half-blend case metadata is absent")
    record = next(
        (
            value
            for value in cases
            if isinstance(value, dict) and value.get("name") == name
        ),
        None,
    )
    if record is None:
        raise ValueError(f"half-blend case is absent: {name}")
    return record


def read_case(
    artifact: Path,
    metadata: JsonObject,
) -> UInt8Array:
    width = int(metadata["width"])
    height = int(metadata["height"])
    path = artifact / str(metadata["outputFile"])
    values = np.fromfile(path, dtype=np.uint8)
    expected = width * height * 4
    if values.size != expected:
        raise ValueError(
            f"{path} has {values.size} bytes; expected {expected}"
        )
    if metadata.get("outputBytes") != expected:
        raise ValueError(f"metadata byte count differs for {path.name}")
    return values.reshape(-1, 4)


def analyze(artifact: Path) -> JsonObject:
    started = time.perf_counter()
    runtime_path = artifact / "runtime.json"
    runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
    if int(runtime.get("schemaVersion", 0)) < 62:
        raise ValueError("expected introspection schema 62 or newer")
    evidence = runtime.get("halfBlendEvidence")
    if not isinstance(evidence, dict) or "error" in evidence:
        raise ValueError(f"half-blend evidence is unavailable: {evidence!r}")
    if evidence.get("schemaVersion") != 1:
        raise ValueError("unexpected half-blend evidence schema")
    if evidence.get("pixelFormat") != 80:
        raise ValueError("half-blend target is not BGRA8Unorm")

    source_meta = case_metadata(evidence, "source-conversion")
    alpha_meta = case_metadata(evidence, "alpha-destination")
    combined_meta = case_metadata(evidence, "combined-hash")
    source = read_case(artifact, source_meta)
    alpha = read_case(artifact, alpha_meta)
    combined = read_case(artifact, combined_meta)
    if source_meta.get("recordCount") != SOURCE_RECORD_COUNT:
        raise ValueError("unexpected source-conversion record count")
    if (
        alpha_meta.get("recordCount")
        != ALPHA_DESTINATION_RECORD_COUNT
    ):
        raise ValueError("unexpected alpha-destination record count")
    if combined_meta.get("recordCount") != COMBINED_RECORD_COUNT:
        raise ValueError("unexpected combined record count")

    source_metrics = comparison(
        source,
        source_conversion_prediction(),
    )
    alpha_metrics = comparison(
        alpha,
        alpha_destination_prediction(total_records=alpha.shape[0]),
    )
    combined_metrics = comparison(
        combined,
        combined_prediction(total_records=combined.shape[0]),
    )
    exact = all(
        metrics["exact"]
        for metrics in (
            source_metrics,
            alpha_metrics,
            combined_metrics,
        )
    )
    elapsed = time.perf_counter() - started
    return {
        "liquidGlassHalfBlendAnalysisSchemaVersion": 1,
        "analysisImplementation": {
            "file": "analysis/liquid_glass_half_blend.py",
            "sha256": sha256_file(Path(__file__).resolve()),
            "python": platform.python_version(),
            "numpy": np.__version__,
        },
        "source": {
            "artifact": str(artifact),
            "runtimeJsonSha256": sha256_file(runtime_path),
            "osVersion": runtime.get("osVersion"),
            "metalDevice": runtime.get("metalDevice"),
            "rawFiles": {
                str(metadata["name"]): {
                    "file": metadata["outputFile"],
                    "sha256": sha256_file(
                        artifact / str(metadata["outputFile"])
                    ),
                }
                for metadata in (source_meta, alpha_meta, combined_meta)
            },
        },
        "measurements": {
            "sourceConversion": source_metrics,
            "alphaDestinationGrid": alpha_metrics,
            "combinedHashGrid": combined_metrics,
            "totalObservedBytes": sum(
                int(metrics["observedBytes"])
                for metrics in (
                    source_metrics,
                    alpha_metrics,
                    combined_metrics,
                )
            ),
        },
        "recoveredSemantics": {
            "destinationConversion":
                "binary16_RNE(destination_unorm8_code / 255)",
            "destinationFactor":
                "binary16_RNE(binary16(1) - source_alpha_binary16)",
            "blend":
                "binary16_RNE_FMA(destination, factor, source)",
            "targetConversion":
                "clamp and binary16-to-UNORM8 round-to-nearest",
        },
        "resourceMeasurements": {
            "analysisSeconds": elapsed,
            "maximumResidentSetKiB":
                resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        },
        "conclusion": {
            "appleHalfBlendBitExact": bool(exact),
            "productionShaderAuthorized": False,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate raw Apple binary16 fixed-function blending."
    )
    parser.add_argument("artifact", type=Path)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    report = analyze(arguments.artifact)
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if arguments.output is None:
        print(rendered, end="")
    else:
        arguments.output.write_text(rendered, encoding="utf-8")
    return (
        0
        if report["conclusion"]["appleHalfBlendBitExact"]
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
