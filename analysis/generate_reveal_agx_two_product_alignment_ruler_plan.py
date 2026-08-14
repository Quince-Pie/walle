#!/usr/bin/env python3.14
"""Generate the exponent-alignment M1 AGX two-product ruler plan."""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
from pathlib import Path
from typing import Final

import generate_reveal_agx_two_product_ruler_plan as ruler


type JsonObject = dict[str, object]

ROOT: Final = Path(__file__).resolve().parent.parent
OUTPUT_DEFAULT: Final = (
    ROOT / "build" / "analysis-agx-basis" / "two-product-alignment-ruler-plan-v1"
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(4 * 1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def generate(output_directory: Path) -> JsonObject:
    offset = -1.0 / 256.0
    fixed = -struct.unpack("<f", struct.pack("<I", 0x3F800001))[0]
    ruler.OUTPUT_DEFAULT = OUTPUT_DEFAULT
    ruler.GEOMETRY = (
        (offset, offset),
        (1024.0 + offset, offset),
        (offset, 1024.0 + offset),
    )
    ruler.PIXEL = (272, 272)
    ruler.TILE = (8, 8)
    ruler.RULER_DIFFERENCE = fixed
    ruler.EXPECTED_FIXED_MIDDLE_TERM = (-1, 67_109_896, -8)
    ruler.VARIABLE_HIGH_BITS = 0x3F7FE000
    ruler.generate(output_directory)
    manifest_path = output_directory / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["schema"] = "walle-reveal-agx-two-product-alignment-ruler-plan-manifest-v1"
    manifest["generator"] = {
        "path": Path(__file__).relative_to(ROOT).as_posix(),
        "bytes": Path(__file__).stat().st_size,
        "sha256": _sha256(Path(__file__)),
    }
    shared = Path(ruler.__file__)
    manifest["sharedGenerator"] = {
        "path": shared.relative_to(ROOT).as_posix(),
        "bytes": shared.stat().st_size,
        "sha256": _sha256(shared),
    }
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT_DEFAULT)
    arguments = parser.parse_args()
    result = generate(arguments.output)
    print(json.dumps(result["census"], indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
