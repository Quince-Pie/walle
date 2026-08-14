#!/usr/bin/env python3
"""C-product truth-table capture.

Synthetic child engineered so every chain stage is a clean power of 2:
  v0 = (512, 614.5)  value 0   (anchor; C = pure product law)
  v1 = (2560, 614.5) value 0
  v2 = (2560, 2662.5) value = probe word
  => x-edge for the y-numerator = 2048.0, det = 2^22 (selector clean),
     A = 0 exactly; C(tile row ty) = Q(delta * 2048 * didx(ty)) with
     didx = ty*8192 - 157312 subpixels (trailing-zeros = 7, bit length
     11..19 over rows 19..63).

Probe words: one-hot mantissa 1+2^-j (j = 1..23), dense tails
(1 + (2^m - 1)*2^-23), and half-scale twins (0.5*(...)) to move the
product's binade.  Reading the exported C words against the exact
product reveals the truncation/rounding rule bit by bit.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
import sys
from pathlib import Path
from typing import Final

sys.path[:0] = ["/tmp/walle"]
import _sweep_fused_join_lattice as model

ROOT: Final = Path(__file__).resolve().parent.parent
OUTPUT_DEFAULT: Final = (
    ROOT / "build" / "analysis-agx-basis" / "c-truthtable4-plan-v1"
)
VERTEX: Final = struct.Struct("<8I")
ROWS: Final = range(17, 64)
COLUMN: Final = 60

V0 = (0x44000000, 0x43FFE000)   # (512, 511.75): d_o = 128*ty-2047 (8-13 bits)
V1 = (0x45200000, 0x43FFE000)   # (2560, 511.75)
V2 = (0x45200000, 0x458FFE00)   # (2560, 4607.75): height 4096, det = 2^39


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(4 * 1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def probe_words():
    words = [0x3F800000 + t for t in range(256)]           # low-byte scan
    words += [0x3F800000 + (t << 8) for t in range(1, 128)]  # bits 8..14 scan
    return words


def interior_pixel(tile):
    # column 60 rows 19..63 are interior for this triangle except the
    # top-edge tile; sample near the tile centre, nudged below the top
    # edge (y >= 615) and left of x=2048.
    x = min(tile[0] * 32 + 16, 2046)
    y = max(tile[1] * 32 + 16, 516)
    return x, y


def generate(output_directory: Path) -> dict:
    if output_directory.exists():
        raise FileExistsError(output_directory)
    vertices = bytearray()
    draws: list[dict] = []
    experiments: list[dict] = []
    for wi, word in enumerate(probe_words()):
        for ty in ROWS:
            px, py = interior_pixel((COLUMN, ty))
            record = len(draws)
            for vi, (vx, vy) in enumerate((V0, V1, V2)):
                val = word if vi == 2 else 0
                # channels: R = probe, G = probe (twin), B = 0-control, A = 1.0
                vertices.extend(VERTEX.pack(
                    vx, vy, 0, 0, val, val, 0,
                    0x3F800000 if vi == 2 else 0))
            experiments.append({
                "recordIndex": record,
                "inputOrdinal": record,
                "variant": "c-truthtable",
                "split": "discovery",
                "state": 0,
                "drawOrdinal": 0,
                "anchor": 0,
                "family": "word",
                "offset": wi,
                "word": word,
            })
            draws.append({
                "recordIndex": record,
                "targetIndex": 0,
                "targetRecordIndex": 0,
                "sampleRecordIndex": 0,
                "sampleOrdinal": 0,
                "patternIndex": record,
                "x": px,
                "y": py,
                "tileX": COLUMN,
                "tileY": ty,
            })

    output_directory.mkdir(parents=True)
    vertex_path = output_directory / "reveal-agx-setup-accumulator-vertices.bin"
    vertex_path.write_bytes(vertices)
    census = {
        "targetCount": 8,
        "patternCount": len(draws),
        "drawCount": len(draws),
        "coefficientTripleCount": len(draws) * 4,
    }
    plan = {
        "schema": "walle-reveal-agx-setup-accumulator-plan-v1",
        "authority": {
            "opensReferencePixels": False,
            "usesOutputFeedback": False,
            "establishesCProductTruthTable": True,
        },
        "target": {"width": 2048, "height": 2048},
        "vertexData": {
            "file": vertex_path.name,
            "bytes": len(vertices),
            "sha256": _sha256(vertex_path),
            "recordCount": len(draws),
            "verticesPerRecord": 3,
            "wordsPerVertex": 8,
            "layout": "positionXY,pad2,varyingRGBA; little-endian uint32",
        },
        "experiments": experiments,
        "draws": draws,
        "census": census,
    }
    plan_path = output_directory / "reveal-agx-setup-accumulator-plan.json"
    plan_path.write_text(json.dumps(plan, indent=2, sort_keys=True) + "\n",
                         encoding="utf-8")
    manifest = {
        "schema": "walle-reveal-agx-c-truthtable-plan-manifest-v1",
        "generator": {
            "path": Path(__file__).relative_to(ROOT).as_posix(),
            "sha256": _sha256(Path(__file__)),
        },
        "plan": {"file": plan_path.name, "sha256": _sha256(plan_path)},
        "vertexData": {"file": vertex_path.name,
                       "sha256": _sha256(vertex_path)},
        "census": census,
    }
    (output_directory / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT_DEFAULT)
    arguments = parser.parse_args()
    print(json.dumps(generate(arguments.output)["census"], indent=2,
                     sort_keys=True))


if __name__ == "__main__":
    main()
