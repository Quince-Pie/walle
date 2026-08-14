#!/usr/bin/env python3
"""Freeze the exact six-level static regular holdout pyramid for Walle."""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
from pathlib import Path
from typing import Any


type JsonObject = dict[str, Any]

EXPECTED_RUNTIME_SHA256 = "7b847a04ac51c4474485d97ae0144fe1d984d08a2781faa88343c1fcb815622c"
EXPECTED_VALIDATION_SHA256 = "3280f6ae19b7dd8fef9a2c85a1fa15daada3172ad6403ac9a87f55825723e507"
EXPECTED_CANONICAL_RESULT_SHA256 = (
    "dc030ff6806c6c5aef92c7276a2bd2c093e963775d9a2007c5439daa41435533"
)
EXPECTED_STREAM_SHA256 = "59fdd7866c923fac6c88bc921fb87065fca789bb1b9a1503f7c89d48fbe00956"
MAGIC = b"WLGSPV1\0"

LEVELS = (
    (
        "sdf-generator-carenderer-live-tree-texture-003-pf80-320x320.raw",
        320,
        320,
        "cea1d0a3fa59bc04ef6755860338ecbce514c424722f85115f8176d85e9816ad",
    ),
    (
        "sdf-generator-carenderer-live-tree-texture-003-pf80-320x320-mip-01.raw",
        160,
        160,
        "af814768131772676fb07c567d6aa4fdd563aceaca1c4d35e9726d85ba716dc1",
    ),
    (
        "sdf-generator-carenderer-live-tree-texture-003-pf80-320x320-mip-02.raw",
        80,
        80,
        "25530014edd8dbe501df1a3de0a5e7b59fc0e9b5363bf649ddb1103e894176ff",
    ),
    (
        "sdf-generator-carenderer-live-tree-texture-003-pf80-320x320-mip-03.raw",
        40,
        40,
        "8a5842e476f69bff33deba92ce8f21d1fe3bc549d70fa4c699825aad58663ede",
    ),
    (
        "sdf-generator-carenderer-live-tree-texture-003-pf80-320x320-mip-04.raw",
        20,
        20,
        "ac774d9a3cf4bf63833c387e088829f0a5cd9a5034485e32f9df50f0ee761225",
    ),
    (
        "sdf-generator-carenderer-live-tree-texture-003-pf80-320x320-mip-05.raw",
        10,
        10,
        "6b2a1ad3d2d825a0ff80a3303d581a6cfb0d27d970daffcd639ffd2764242db1",
    ),
)


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1 << 20):
            digest.update(block)
    return digest.hexdigest()


def generate(
    capture: Path,
    canonical_result: Path,
    output: Path,
    manifest_path: Path,
) -> JsonObject:
    runtime = capture / "runtime.json"
    validation = capture / "validation.json"
    expected_files = (
        (runtime, EXPECTED_RUNTIME_SHA256),
        (validation, EXPECTED_VALIDATION_SHA256),
        (canonical_result, EXPECTED_CANONICAL_RESULT_SHA256),
    )
    for path, expected in expected_files:
        if sha256_file(path) != expected:
            raise ValueError("evidence SHA-256 differs for " + str(path))

    level_payloads = []
    level_records = []
    for filename, width, height, expected_hash in LEVELS:
        path = capture / filename
        payload = path.read_bytes()
        if len(payload) != width * height * 4:
            raise ValueError("mip byte count differs for " + filename)
        if sha256_bytes(payload) != expected_hash:
            raise ValueError("mip SHA-256 differs for " + filename)
        level_payloads.append(payload)
        level_records.append(
            {
                "source": filename,
                "width": width,
                "height": height,
                "byteCount": len(payload),
                "sha256": expected_hash,
            }
        )

    stream = b"".join(level_payloads)
    if sha256_bytes(stream) != EXPECTED_STREAM_SHA256:
        raise ValueError("pyramid stream SHA-256 differs")
    header_size = struct.calcsize("<8s4I") + len(LEVELS) * struct.calcsize("<4I")
    descriptors = bytearray()
    offset = header_size
    for record in level_records:
        descriptors.extend(
            struct.pack(
                "<4I",
                record["width"],
                record["height"],
                offset,
                record["byteCount"],
            )
        )
        offset += record["byteCount"]
    fixture = (
        struct.pack("<8s4I", MAGIC, 1, len(LEVELS), header_size, len(stream))
        + descriptors
        + stream
    )
    output.write_bytes(fixture)
    manifest: JsonObject = {
        "walleStaticRegularPyramidFixtureManifestSchemaVersion": 1,
        "classification": (
            "physical-Retina prospective static regular producer-geometry "
            "holdout, composed independently from the diagnostic wallpaper "
            "through the transferred crop, copy-base, and mip kernels"
        ),
        "fixture": {
            "path": output.name,
            "magic": "WLGSPV1",
            "version": 1,
            "headerSize": header_size,
            "levelCount": len(LEVELS),
            "payloadByteCount": len(stream),
            "byteCount": len(fixture),
            "sha256": sha256_bytes(fixture),
        },
        "levels": level_records,
        "streamSHA256": EXPECTED_STREAM_SHA256,
        "evidenceSHA256": {
            "runtime": EXPECTED_RUNTIME_SHA256,
            "validation": EXPECTED_VALIDATION_SHA256,
            "canonicalResult": EXPECTED_CANONICAL_RESULT_SHA256,
            "generator": sha256_file(Path(__file__).resolve()),
        },
        "geometry": {
            "name": "circle-377-fractional-holdout",
            "diameter": 377,
            "center": [301.25, 699.75],
            "window": [1024, 1024],
        },
    }
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--capture", type=Path, required=True)
    parser.add_argument("--canonical-result", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    arguments = parser.parse_args()
    try:
        result = generate(
            arguments.capture,
            arguments.canonical_result,
            arguments.output,
            arguments.manifest,
        )
    except (OSError, ValueError) as error:
        print("fixture generation failed: " + str(error))
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
