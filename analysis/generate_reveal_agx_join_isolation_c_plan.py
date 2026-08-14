#!/usr/bin/env python3
"""Join isolation ruler read through C words.

Two-product numerators with both first products exactly known (the
product stage is proven exact by product-isolation-c v3: 2048/2048).
The anchor sits on a tile-aligned x so disp_x = 0 kills the axis-0 mid
term, and pow2 y-displacements make the mid product exact: the C word
reads the JOINED, NORMALIZED numerator at sub-27-bit resolution.
(w1, w2) pairs sweep the join's tie/boundary space densely, in both
same-sign and opposite-sign (cancelling) configurations.
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
    ROOT / "build" / "analysis-agx-basis" / "join-isolation-c-plan-v1"
)
VERTEX: Final = struct.Struct("<8I")

V0: Final = (1632.0, -512.0)      # anchor, value 0, x tile-aligned (51*32)
GEOS: Final = (                   # (x1, x2, w2_sign)
    (1865.5, 512.0, +1), (1841.25, 500.5, +1), (1811.125, 486.25, +1),
    (1793.0625, 473.125, +1), (1775.5, 455.75, +1), (1757.25, 444.375, +1),
    (1745.125, 429.0625, +1), (1739.5, 412.5, +1),
    (1865.5, 512.0, -1), (1841.25, 500.5, -1), (1811.125, 486.25, -1),
    (1793.0625, 473.125, -1), (1775.5, 455.75, -1), (1757.25, 444.375, -1),
    (1745.125, 429.0625, -1), (1739.5, 412.5, -1),
)
Y_BASE: Final = -512.0
Y_APEX: Final = 614.5

W1_BASE: Final = -0.8329024314880371
W2_BASE_POS: Final = 0.7137019634246826
W2_BASE_NEG: Final = -0.7137019634246826
SWEEP1: Final = tuple(range(-8, 8))
SWEEP2: Final = tuple(range(-8, 8))
TILE_YS: Final = (0, 16)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(4 * 1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def fixed(v: float) -> int:
    return int(round(v * 256.0))


def interior_pixel(verts, tile):
    fx = [(fixed(v[0]), fixed(v[1])) for v in verts]
    det = ((fx[1][0] - fx[0][0]) * (fx[2][1] - fx[0][1])
           - (fx[1][1] - fx[0][1]) * (fx[2][0] - fx[0][0]))
    if det == 0:
        return None
    orient = 1 if det > 0 else -1
    best = None
    x0, y0 = tile[0] * 32, tile[1] * 32
    for y in range(max(y0, 0), min(y0 + 32, 2048), 2):
        cy = 256 * y + 128
        for x in range(max(x0, 0), min(x0 + 32, 2048), 2):
            cx = 256 * x + 128
            margin = None
            for e in range(3):
                ax, ay = fx[e]
                bx, by = fx[(e + 1) % 3]
                cross = orient * ((bx - ax) * (cy - ay)
                                  - (by - ay) * (cx - ax))
                margin = cross if margin is None else min(margin, cross)
                if margin <= 0:
                    break
            if margin is not None and margin > 0 \
                    and (best is None or margin > best[0]):
                best = (margin, x, y)
    if best is None or best[0] < 512:
        return None
    return best[1], best[2]


def sweep_word(bits: int, offset: int) -> int:
    return model.key_to_bits(model.ordered_key(bits) + offset)


def generate(output_directory: Path) -> dict:
    if output_directory.exists():
        raise FileExistsError(output_directory)
    vertices = bytearray()
    draws: list[dict] = []
    experiments: list[dict] = []
    w1b = model.f32_bits(W1_BASE)

    for gi, (x1, x2, w2sign) in enumerate(GEOS):
        verts = (V0, (x1, Y_BASE), (x2, Y_APEX))
        w2b = model.f32_bits(W2_BASE_POS if w2sign > 0 else W2_BASE_NEG)
        tiles = []
        for ty in TILE_YS:
            for tx in range(30, 60):
                pixel = interior_pixel(verts, (tx, ty))
                if pixel:
                    tiles.append(((tx, ty), pixel))
                    break
        if not tiles:
            continue
        pairs = [(k1, k2) for k1 in SWEEP1 for k2 in SWEEP2]
        for start in range(0, len(pairs), 4):
            quad = pairs[start:start + 4]
            for (tile, pixel) in tiles:
                record = len(draws)
                for vi, v in enumerate(verts):
                    if vi == 1:
                        chans = [sweep_word(w1b, k1) for (k1, k2) in quad]
                    elif vi == 2:
                        chans = [sweep_word(w2b, k2) for (k1, k2) in quad]
                    else:
                        chans = [0, 0, 0, 0]
                    vertices.extend(VERTEX.pack(
                        model.f32_bits(v[0]), model.f32_bits(v[1]),
                        0, 0, *chans))
                experiments.append({
                    "recordIndex": record,
                    "inputOrdinal": start // 4,
                    "variant": "join-isolation-c",
                    "split": "discovery",
                    "geometryIndex": gi,
                    "geometry": [list(v) for v in verts],
                    "pairs": [list(p) for p in quad],
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
        "patternCount": len(draws),
        "drawCount": len(draws),
        "coefficientTripleCount": len(draws) * 4,
    }
    plan = {
        "schema": "walle-reveal-agx-setup-accumulator-plan-v1",
        "authority": {
            "opensReferencePixels": False,
            "usesOutputFeedback": False,
            "establishesJoinNormalizeLaw": True,
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
        "schema": "walle-reveal-agx-join-isolation-c-plan-manifest-v1",
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
