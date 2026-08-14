#!/usr/bin/env python3
"""Generate a first-product cancellation-residual probe.

Clip-vertex children carry two first products that nearly cancel; the
surviving residual is far below the p27 grid and the exported slope word is
that residual times the P25 selector, computed exactly when the product is
narrow.  Sweeping vC = vB + k ulp against non-power-of-two edge factors
measures the wide-grid structure of each truncated first product directly.

Geometry template (all vertices on screen, no guard clipping):
  A = (x0, y0), B = (x0, y0 + H), C = (x0 - W, y0 + H)
so both x-numerator first products share the same |edge| = H and the
residual is the hardware difference of the two truncated products.
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
    ROOT / "build" / "analysis-agx-basis" / "first-cancellation-plan-v1"
)
VERTEX: Final = struct.Struct("<8I")

X0: Final = 1200.0
Y0: Final = 100.0
W: Final = 1024.0
HEIGHTS: Final = (1049.0, 525.0, 787.0, 1024.0)

A_OFFSETS: Final = (0, -1, -21, -4096)
D_MANTISSAS: Final = (1, 3, 5, 11, 21, 45, 173, 341, 1365, 2731, 4095,
                      -3, -45, -341)
K_OFFSETS: Final = tuple(range(-8, 9))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(4 * 1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _perturb(bits: int, offset: int) -> int:
    return model.key_to_bits(model.ordered_key(bits) + offset)


def interior(height: float):
    """A pixel and tile strictly inside the triangle."""
    x = int(X0 - W * 0.35)
    y = int(Y0 + height * 0.7)
    return (x, y), (x // 32, y // 32)


def generate(output_directory: Path) -> dict:
    if output_directory.exists():
        raise FileExistsError(output_directory)

    values = []
    for a_offset in A_OFFSETS:
        a = model.bits_f32(_perturb(model.f32_bits(-1.0), a_offset))
        for mantissa in D_MANTISSAS:
            d1 = mantissa * 2.0 ** -12
            b = model.f32(a + d1)
            if b == a:
                continue
            for k in K_OFFSETS:
                c = model.bits_f32(_perturb(model.f32_bits(b), k))
                values.append({"a": a, "b": b, "c": c, "k": k,
                               "aOffset": a_offset, "dMantissa": mantissa})

    vertices = bytearray()
    draws: list[dict] = []
    experiments: list[dict] = []

    for height_index, height in enumerate(HEIGHTS):
        geometry = ((X0, Y0), (X0, Y0 + height), (X0 - W, Y0 + height))
        pixel, tile = interior(height)
        for start in range(0, len(values), 4):
            chunk = values[start:start + 4]
            while len(chunk) < 4:
                chunk = chunk + [chunk[-1]]
            record = len(draws)
            for vertex_index in range(3):
                channels = [model.f32_bits(
                    (entry["a"], entry["b"], entry["c"])[vertex_index])
                    for entry in chunk]
                vertices.extend(VERTEX.pack(
                    model.f32_bits(geometry[vertex_index][0]),
                    model.f32_bits(geometry[vertex_index][1]),
                    0, 0, *channels))
            experiments.append({
                "recordIndex": record,
                "inputOrdinal": record,
                "variant": "first-cancellation",
                "split": "discovery",
                "heightIndex": height_index,
                "height": height,
                "channels": chunk,
            })
            draws.append({
                "recordIndex": record,
                "targetIndex": 0,
                "targetRecordIndex": 0,
                "sampleRecordIndex": 0,
                "sampleOrdinal": 0,
                "patternIndex": record,
                "x": pixel[0],
                "y": pixel[1],
                "tileX": tile[0],
                "tileY": tile[1],
            })

    output_directory.mkdir(parents=True)
    vertex_path = output_directory / "reveal-agx-setup-accumulator-vertices.bin"
    vertex_path.write_bytes(vertices)
    census = {
        "targetCount": 8,
        "experimentValueCount": len(values),
        "heightCount": len(HEIGHTS),
        "patternCount": len(draws),
        "drawCount": len(draws),
        "coefficientTripleCount": len(draws) * 4,
    }
    plan = {
        "schema": "walle-reveal-agx-setup-accumulator-plan-v1",
        "authority": {
            "opensReferencePixels": False,
            "usesOutputFeedback": False,
            "establishesFirstCancellationLaw": True,
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
        "schema": "walle-reveal-agx-first-cancellation-plan-manifest-v1",
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
