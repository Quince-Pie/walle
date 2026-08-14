#!/usr/bin/env python3
"""Selector-stage isolation ruler read through C words.

Single-product children (product stage proven exact by
product-isolation-c v3) with anchor value 0 and pow2 y-displacement
tiles: the C word is the selector-stage output of an EXACTLY KNOWN
numerator.  The apex height y2 sweeps the determinant - and therefore
the P25 selector key - without touching the numerator edges, mapping the
sel stage across hundreds of (jidx, sel) operand pairs at sub-bit
resolution.
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
    ROOT / "build" / "analysis-agx-basis" / "sel-isolation-c-plan-v1"
)
VERTEX: Final = struct.Struct("<8I")

V0: Final = (1638.5, -512.0)          # anchor, value 0
X1_VALUES: Final = (1865.5, 1841.25, 1793.375, 1745.125)
Y1: Final = -512.0                    # same y as anchor: axis0 numerator 0
X2: Final = 512.0
Y2_BASE: Final = 614.5
Y2_STEP: Final = 3.25
Y2_COUNT: Final = 128

W_BASE: Final = -0.8329024314880371
W_OFFSETS: Final = (0, 7, 19, 38)     # four jidx variants per record
TILE_YS: Final = (0, 16)              # pow2 displacement from anchor


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
    w_words = [sweep_word(model.f32_bits(W_BASE), k) for k in W_OFFSETS]

    for gi, x1 in enumerate(X1_VALUES):
        for yi in range(Y2_COUNT):
            y2 = Y2_BASE + Y2_STEP * yi
            verts = (V0, (x1, Y1), (X2, y2))
            for ty in TILE_YS:
                found = None
                for tx in range(30, 60):
                    pixel = interior_pixel(verts, (tx, ty))
                    if pixel:
                        found = ((tx, ty), pixel)
                        break
                if not found:
                    continue
                tile, pixel = found
                record = len(draws)
                for vi, v in enumerate(verts):
                    chans = w_words if vi == 2 else [0, 0, 0, 0]
                    vertices.extend(VERTEX.pack(
                        model.f32_bits(v[0]), model.f32_bits(v[1]),
                        0, 0, *chans))
                experiments.append({
                    "recordIndex": record,
                    "inputOrdinal": yi,
                    "variant": "sel-isolation-c",
                    "split": "discovery",
                    "geometryIndex": gi,
                    "geometry": [list(v) for v in verts],
                    "offsets": list(W_OFFSETS),
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
            "establishesSelectorStageLaw": True,
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
        "schema": "walle-reveal-agx-sel-isolation-c-plan-manifest-v1",
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
