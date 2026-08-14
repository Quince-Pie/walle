#!/usr/bin/env python3
"""Generate a clip-interpolation ruler probe.

Replicates the state-40 source-2 triangle (one vertex beyond the y-low guard,
two guard crossings with awkward rational t) and sweeps the outside vertex's
varying in single-ulp steps.  The exported coefficients of the hardware clip
children then pin each interpolated clip-vertex varying word, mapping the
interpolator's rounding as a function of the exact fraction.
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
    ROOT / "build" / "analysis-agx-basis" / "clip-interp-ruler-plan-v1"
)
VERTEX: Final = struct.Struct("<8I")

GEOMETRY: Final = ((1865.5, -739.0), (1865.5, 614.5), (512.0, 614.5))
# sample tiles inside the clipped region (from the residual-states plan)
TILES: Final = ((54, 0), (56, 6))
PIXELS: Final = ((1728, 0), (1792, 192))

# outer-vertex varying sweeps: coarse (fraction ruler) and fine (ulp ruler)
BASES: Final = (-1.0, -0.75)
COARSE: Final = tuple(range(0, 256))          # ulp offsets, coarse map
FINE_BASE: Final = -0.9999999403953552
FINE: Final = tuple(range(-128, 128))
# inner vertex varyings per channel set
INNER: Final = ((0.0, 1.0009765625), (0.0, 0.99951171875),
                (-0.0009765625, 1.0), (0.0, 1.0))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(4 * 1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _perturb(bits: int, offset: int) -> int:
    return model.key_to_bits(model.ordered_key(bits) + offset)


def generate(output_directory: Path) -> dict:
    if output_directory.exists():
        raise FileExistsError(output_directory)

    sweeps = []
    for base in BASES:
        for j in COARSE:
            sweeps.append(model.bits_f32(_perturb(model.f32_bits(base), j)))
    for j in FINE:
        sweeps.append(model.bits_f32(_perturb(model.f32_bits(FINE_BASE), j)))

    vertices = bytearray()
    draws: list[dict] = []
    experiments: list[dict] = []

    for index, v0 in enumerate(sweeps):
        # channels: R,G,B,A carry the same v0 with the four INNER pairs
        record_words = []
        for vertex_index in range(3):
            channels = []
            for inner in INNER:
                value = (v0, inner[0], inner[1])[vertex_index]
                channels.append(model.f32_bits(value))
            record_words.append(channels)
        for tile, pixel in zip(TILES, PIXELS):
            record = len(draws)
            for vertex_index in range(3):
                vertices.extend(VERTEX.pack(
                    model.f32_bits(GEOMETRY[vertex_index][0]),
                    model.f32_bits(GEOMETRY[vertex_index][1]),
                    0, 0, *record_words[vertex_index]))
            experiments.append({
                "recordIndex": record,
                "inputOrdinal": index,
                "variant": "clip-interp-ruler",
                "split": "discovery",
                "outerWord": f"0x{model.f32_bits(v0):08x}",
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
        "sweepCount": len(sweeps),
        "patternCount": len(draws),
        "drawCount": len(draws),
        "coefficientTripleCount": len(draws) * 4,
    }
    plan = {
        "schema": "walle-reveal-agx-setup-accumulator-plan-v1",
        "authority": {
            "opensReferencePixels": False,
            "usesOutputFeedback": False,
            "establishesClipInterpolationLaw": True,
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
        "geometry": [list(v) for v in GEOMETRY],
        "innerSets": [list(v) for v in INNER],
        "experiments": experiments,
        "draws": draws,
        "census": census,
    }
    plan_path = output_directory / "reveal-agx-setup-accumulator-plan.json"
    plan_path.write_text(json.dumps(plan, indent=2, sort_keys=True) + "\n",
                         encoding="utf-8")
    manifest = {
        "schema": "walle-reveal-agx-clip-interp-ruler-plan-manifest-v1",
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
