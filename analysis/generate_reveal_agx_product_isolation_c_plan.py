#!/usr/bin/env python3
"""First-product isolation ruler read through C words.

Triangles shaped like the o104 children but with the varying value on
exactly ONE non-anchor vertex, so each axis numerator is a SINGLE first
product (no join).  C words at power-of-two-displacement tiles then read
the product to sub-27-bit resolution through the (verified-clean)
selector stage.  The value word sweeps 128 keys and the geometry varies
the edge mantissa, mapping the truncating-array's +-1 pattern over
hundreds of (md, mev) operand pairs.
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
    ROOT / "build" / "analysis-agx-basis" / "product-isolation-c-plan-v3"
)
VERTEX: Final = struct.Struct("<8I")

# anchor v0 on the tile-aligned guard corner (value 0), v1 top-right
# (value 0), v2 top-left CARRIES the value.  Numerators: single product
# value x edge per axis.
# v0 and v1 share y=-512 so the valued vertex's axis-0 edge is zero:
# the child has EXACTLY one product (axis 1), read cleanly through C.
V0: Final = (1638.5, -512.0)
X1_VALUES: Final = (1865.5, 1857.25, 1841.125, 1823.0625, 1811.5,
                    1799.75, 1793.375, 1787.0625, 1781.5, 1775.25,
                    1769.125, 1763.0625, 1757.5, 1751.75, 1745.375,
                    1739.0625)
Y1: Final = -512.0
V2: Final = (512.0, 614.5)

W_BASE: Final = -0.8329024314880371
SWEEP: Final = tuple(range(-64, 64))
TILE_YS: Final = (0, 16, 3, 7)      # ty0/16 pow2 displacement, 3/7 cross-check


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
    base_bits = model.f32_bits(W_BASE)

    for gi, x1 in enumerate(X1_VALUES):
        verts = (V0, (x1, Y1), V2)
        tiles = []
        for ty in TILE_YS:
            for tx in range(30, 60):
                pixel = interior_pixel(verts, (tx, ty))
                if pixel:
                    tiles.append(((tx, ty), pixel))
                    break
        if not tiles:
            continue
        for start in range(0, len(SWEEP), 4):
            quad = SWEEP[start:start + 4]
            for (tile, pixel) in tiles:
                record = len(draws)
                for vi, v in enumerate(verts):
                    if vi == 2:
                        chans = [sweep_word(base_bits, k) for k in quad]
                    else:
                        chans = [0, 0, 0, 0]
                    vertices.extend(VERTEX.pack(
                        model.f32_bits(v[0]), model.f32_bits(v[1]),
                        0, 0, *chans))
                experiments.append({
                    "recordIndex": record,
                    "inputOrdinal": start // 4,
                    "variant": "product-isolation-c",
                    "split": "discovery",
                    "geometryIndex": gi,
                    "geometry": [list(v) for v in verts],
                    "offsets": list(quad),
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
            "establishesFirstProductSubBitLaw": True,
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
        "schema":
            "walle-reveal-agx-product-isolation-c-plan-manifest-v1",
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
