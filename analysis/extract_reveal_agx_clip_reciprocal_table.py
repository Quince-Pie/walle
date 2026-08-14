#!/usr/bin/env python3
"""Extract the direct AGX clipper reciprocal census from frozen chunk captures."""

from __future__ import annotations

import argparse
import hashlib
import json
import mmap
import os
import struct
from pathlib import Path


CHUNK_COUNT = 128
MANTISSA_COUNT = 1 << 23
RECORD_COUNT = 83_872
DISCOVERY_RECORD_COUNT = 8_193 * 8
USED_RECORD_COUNT = 1 << 16
VECTOR_COUNT = 101
VECTOR_BYTES = 16
RECORD_BYTES = VECTOR_COUNT * VECTOR_BYTES
RAW_BYTES = RECORD_COUNT * RECORD_BYTES
COEFFICIENT_VECTOR = 5
COEFFICIENT_COMPONENT = 2
WORD = struct.Struct("<I")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(4 * 1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def extract(scratch_root: Path, output: Path, manifest_output: Path) -> None:
    if output.exists() or manifest_output.exists():
        raise FileExistsError("an output already exists")
    table = bytearray(MANTISSA_COUNT * WORD.size)
    chunks: list[dict[str, object]] = []
    for low7 in range(CHUNK_COUNT):
        suffix = f"{low7:03d}"
        capture_root = scratch_root / f"capture-exhaustive-{suffix}"
        manifest_path = capture_root / "manifest.json"
        raw_path = capture_root / "reveal-agx-clip-weight-tomography.raw"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        compile_record = manifest.get("compile")
        capture_record = manifest.get("capture")
        if not isinstance(compile_record, dict) or not isinstance(capture_record, dict):
            raise ValueError(f"chunk {suffix} manifest shape differs")
        if compile_record.get("mantissaLow7") != low7:
            raise ValueError(f"chunk {suffix} identifies another mantissa residue")
        if raw_path.stat().st_size != RAW_BYTES:
            raise ValueError(f"chunk {suffix} raw size differs")
        raw_hash = sha256(raw_path)
        if capture_record.get("sha256") != raw_hash:
            raise ValueError(f"chunk {suffix} raw SHA-256 differs from its manifest")
        with raw_path.open("rb") as stream, mmap.mmap(
            stream.fileno(), DISCOVERY_RECORD_COUNT * RECORD_BYTES, access=mmap.ACCESS_READ
        ) as raw:
            for high16 in range(USED_RECORD_COUNT):
                source_offset = (
                    high16 * RECORD_BYTES
                    + COEFFICIENT_VECTOR * VECTOR_BYTES
                    + COEFFICIENT_COMPONENT * WORD.size
                )
                destination_offset = ((high16 << 7) | low7) * WORD.size
                table[destination_offset : destination_offset + WORD.size] = raw[
                    source_offset : source_offset + WORD.size
                ]
        chunks.append(
            {
                "mantissaLow7": low7,
                "manifestSha256": sha256(manifest_path),
                "rawSha256": raw_hash,
            }
        )
        print(f"extracted {suffix}", flush=True)

    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.{os.getpid()}.tmp")
    temporary.write_bytes(table)
    os.replace(temporary, output)
    report = {
        "schema": "walle-reveal-agx-direct-clip-reciprocal-table-v1",
        "authority": {
            "opensReferencePixels": False,
            "usesOnlyDirectUserClipCoefficientCaptures": True,
            "establishesFullPostClipTriangleSetupLaw": False,
        },
        "layout": {
            "index": "positive-normal-binary32 mantissa bits for exponent 143",
            "value": "little-endian binary32 bits for the generated m=1 clip weight",
            "entryCount": MANTISSA_COUNT,
            "bytesPerEntry": WORD.size,
        },
        "table": {
            "path": str(output),
            "bytes": output.stat().st_size,
            "sha256": sha256(output),
        },
        "chunks": chunks,
    }
    manifest_output.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("scratch_root", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("manifest", type=Path)
    arguments = parser.parse_args()
    extract(arguments.scratch_root, arguments.output, arguments.manifest)


if __name__ == "__main__":
    main()
