#!/usr/bin/env python3
"""Generate a production-children setup capture plan for state 31.

Two record families sample the same tiles:

  * explicit-child records draw one post-guard clip child (including the
    child the CPU model currently drops because its cancelled-axis residual
    numerator underflows the slope product stage) with walle's interpolated
    clip-vertex varyings;
  * raw-base records draw the unclipped source triangle and let the hardware
    perform its own guard-band clip.

Equal exported coefficient triples between the two families at every tile
validate the clip fan and the clip-vertex varying interpolation in one step;
the exported A words on cancelled axes measure the small-product slope law
directly on production operands.
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
    ROOT / "build" / "analysis-agx-basis" / "production-children-plan-v1"
)
VERTEX: Final = struct.Struct("<8I")
SCRATCH: Final = Path(
    "/tmp/nix-shell.PFgUGF/claude-1000/-tmp-walle/"
    "4ccfbce8-33b2-4b5f-8e29-93486397c8a4/scratchpad")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(4 * 1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def load_triangles() -> list[dict]:
    """Parse CHILDSDF rows: base triangles (ordinal < 100) and postguard
    children (100 + raw index) with per-vertex position/sdf words."""
    triangles = {}
    for line in (SCRATCH / "childsdf31.txt").read_text().splitlines():
        if "CHILDSDF" not in line:
            continue
        t = line[line.index("CHILDSDF"):].split()
        ordinal = int(t[2])
        vertices = []
        for v in range(3):
            words = [int(w, 16) for w in t[3 + 4 * v:7 + 4 * v]]
            vertices.append(words)
        triangles[ordinal] = {"ordinal": ordinal, "vertices": vertices}
    # the dropped src-6 big child is RAWCHILD index 3 -> ordinal 103
    raw3 = {
        "ordinal": 103,
        "vertices": [
            [model.f32_bits(-512.0), model.f32_bits(1638.5),
             0xbf7a232c, 0x3f7a232c],
            [model.f32_bits(512.0), model.f32_bits(614.5),
             0x00000000, 0x00000000],
            [model.f32_bits(512.0), model.f32_bits(1663.5),
             0x00000000, 0x3f801f44],
        ],
    }
    # the src-0 sliver (RAWCHILD 0) is entirely off screen; skip it.
    triangles[103] = raw3
    return triangles


def f32_value(word: int) -> float:
    return model.bits_f32(word)


def fixed(word: int) -> int:
    return int(round(f32_value(word) * 256.0))


def interior_pixel(vertices, tile):
    """Best interior pixel of triangle ∩ tile, maximizing edge margin."""
    fx = [(fixed(v[0]), fixed(v[1])) for v in vertices]
    det = ((fx[1][0] - fx[0][0]) * (fx[2][1] - fx[0][1])
           - (fx[1][1] - fx[0][1]) * (fx[2][0] - fx[0][0]))
    if det == 0:
        return None
    orient = 1 if det > 0 else -1
    best = None
    x0, y0 = tile[0] * 32, tile[1] * 32
    for y in range(max(y0, 0), min(y0 + 32, 2048)):
        cy = 256 * y + 128
        for x in range(max(x0, 0), min(x0 + 32, 2048)):
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


def tile_grid(spec):
    for tx0, tx1, sx, ty0, ty1, sy in spec:
        for ty in range(ty0, ty1 + 1, sy):
            for tx in range(tx0, tx1 + 1, sx):
                yield tx, ty


# (triangle ordinal used for drawing, sampling-region ordinal, tile spec)
FAMILIES: Final = (
    # explicit dropped child vs raw base 6, every tile of the region
    (103, 103, ((0, 15, 1, 19, 51, 1),)),
    (6,   103, ((0, 15, 1, 19, 51, 1),)),
    # explicit sliver child (RAWCHILD 4 = walle 104) and raw base 6 bottom row
    (104, 104, ((0, 15, 1, 51, 51, 1),)),
    # src-0 big child vs raw base 0
    (101, 101, ((0, 15, 2, 0, 19, 2),)),
    (0,   101, ((0, 15, 2, 0, 19, 2),)),
    # src-1 child vs raw base 1
    (102, 102, ((0, 15, 2, 0, 19, 2),)),
    (1,   102, ((0, 15, 2, 0, 19, 2),)),
    # src-7 child vs raw base 7
    (105, 105, ((0, 15, 2, 19, 51, 2),)),
    (7,   105, ((0, 15, 2, 19, 51, 2),)),
    # unclipped bases at their miss rows plus sparse interior
    (2, 2, ((16, 48, 1, 19, 19, 1), (16, 48, 4, 0, 18, 4))),
    (3, 3, ((16, 48, 4, 0, 19, 4),)),
    (4, 4, ((16, 48, 4, 19, 51, 4),)),
    (5, 5, ((16, 48, 4, 19, 51, 4),)),
)


def generate(output_directory: Path) -> dict:
    if output_directory.exists():
        raise FileExistsError(output_directory)
    triangles = load_triangles()

    vertices = bytearray()
    draws: list[dict] = []
    experiments: list[dict] = []
    skipped = 0

    for draw_ordinal, region_ordinal, spec in FAMILIES:
        drawn = triangles[draw_ordinal]
        region = triangles[region_ordinal]
        for tx, ty in tile_grid(spec):
            pixel = interior_pixel(region["vertices"], (tx, ty))
            if pixel is None:
                skipped += 1
                continue
            record = len(draws)
            for vertex in drawn["vertices"]:
                # channels: R = sdf x, G = sdf y, B/A repeat R/G
                vertices.extend(VERTEX.pack(
                    vertex[0], vertex[1], 0, 0,
                    vertex[2], vertex[3], vertex[2], vertex[3]))
            experiments.append({
                "recordIndex": record,
                "inputOrdinal": record,
                "variant": "production-children",
                "split": "discovery",
                "drawOrdinal": draw_ordinal,
                "regionOrdinal": region_ordinal,
                "state": 31,
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
                "tileX": tx,
                "tileY": ty,
            })

    output_directory.mkdir(parents=True)
    vertex_path = output_directory / "reveal-agx-setup-accumulator-vertices.bin"
    vertex_path.write_bytes(vertices)
    census = {
        "targetCount": 8,
        "skippedTileCount": skipped,
        "patternCount": len(draws),
        "drawCount": len(draws),
        "coefficientTripleCount": len(draws) * 4,
    }
    plan = {
        "schema": "walle-reveal-agx-setup-accumulator-plan-v1",
        "authority": {
            "opensReferencePixels": False,
            "usesOutputFeedback": False,
            "capturesProductionChildCoefficients": True,
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
        "schema": "walle-reveal-agx-production-children-plan-manifest-v1",
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
