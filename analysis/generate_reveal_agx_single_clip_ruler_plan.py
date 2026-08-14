#!/usr/bin/env python3
"""Generate a single-clip-vertex interpolation ruler.

One triangle vertex sits exactly on the y-low guard edge, so the hardware
clip produces exactly one new vertex Q on the crossing edge.  The clipped
child then has a single unknown varying, and each record's exported A/B/C
words pin Q's interpolated word uniquely.  Sweeping the edge endpoints'
varyings in ulp steps maps the interpolator's rounding exactly.
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
    ROOT / "build" / "analysis-agx-basis" / "single-clip-ruler-plan-v1"
)
VERTEX: Final = struct.Struct("<8I")

# v0 outside (below y=-512), v1 exactly on the guard, v2 inside.
GEOMETRIES: Final = (
    # (v0, v1, v2, sample pixel, sample tile)
    (((1200.0, -739.0), (1400.0, -512.0), (1000.0, 800.0)),
     (1150, 200), (35, 6)),
    (((900.0, -651.0), (1250.0, -512.0), (1100.0, 613.0)),
     (1090, 200), (34, 6)),
)

# varying sweeps: v2 = start (inside), v0 = end (outside), v1 fixed
S_OFFSETS: Final = tuple(range(0, 48))
E_OFFSETS: Final = (0, -1, -3, -9, -27, 7, 63, 255)
S_BASES: Final = (-1.0, -0.75)
E_BASE: Final = 0.5
V1_VALUE: Final = 0.25


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

    pairs = []
    for s_base in S_BASES:
        for js in S_OFFSETS:
            s = model.bits_f32(_perturb(model.f32_bits(s_base), js))
            for ke in E_OFFSETS:
                e = model.bits_f32(_perturb(model.f32_bits(E_BASE), ke))
                pairs.append((s, e))

    vertices = bytearray()
    draws: list[dict] = []
    experiments: list[dict] = []

    for geometry_index, (geometry, pixel, tile) in enumerate(GEOMETRIES):
        for start in range(0, len(pairs), 4):
            chunk = pairs[start:start + 4]
            while len(chunk) < 4:
                chunk = chunk + [chunk[-1]]
            record = len(draws)
            for vertex_index in range(3):
                channels = []
                for (s, e) in chunk:
                    value = (e, V1_VALUE, s)[vertex_index]
                    channels.append(model.f32_bits(value))
                vertices.extend(VERTEX.pack(
                    model.f32_bits(geometry[vertex_index][0]),
                    model.f32_bits(geometry[vertex_index][1]),
                    0, 0, *channels))
            experiments.append({
                "recordIndex": record,
                "inputOrdinal": record,
                "variant": "single-clip-ruler",
                "split": "discovery",
                "geometryIndex": geometry_index,
                "pairs": [[s, e] for (s, e) in chunk],
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
        "pairCount": len(pairs),
        "geometryCount": len(GEOMETRIES),
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
        "geometries": [
            {"vertices": [list(v) for v in geometry],
             "pixel": list(pixel), "tile": list(tile)}
            for geometry, pixel, tile in GEOMETRIES
        ],
        "v1Value": V1_VALUE,
        "experiments": experiments,
        "draws": draws,
        "census": census,
    }
    plan_path = output_directory / "reveal-agx-setup-accumulator-plan.json"
    plan_path.write_text(json.dumps(plan, indent=2, sort_keys=True) + "\n",
                         encoding="utf-8")
    manifest = {
        "schema": "walle-reveal-agx-single-clip-ruler-plan-manifest-v1",
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
