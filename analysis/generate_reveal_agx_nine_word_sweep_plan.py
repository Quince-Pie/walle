#!/usr/bin/env python3
"""Word-sweep ruler over the hard setup children.

For each hard child, the non-zero varying word is swept over +-32 ordered
keys (four variants per record via the RGBA channels).  Each variant's
A/B/C words across six tiles read the first-product/join datapath at a
different operand, mapping the +-1 boundary behaviour that the canonical
words happen to sit on.
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
    ROOT / "build" / "analysis-agx-basis" / "nine-word-sweep-plan-v1"
)
VERTEX: Final = struct.Struct("<8I")
SCRATCH: Final = Path(
    "/tmp/nix-shell.PFgUGF/claude-1000/-tmp-walle/"
    "4ccfbce8-33b2-4b5f-8e29-93486397c8a4/scratchpad")
# (state, ordinal, channel): the failing channel of each hard child
TARGETS: Final = (((40, 104), 1), ((41, 104), 1), ((42, 104), 1),
                  ((58, 104), 1), ((39, 101), 1), ((58, 101), 1),
                  ((60, 106), 0))
SWEEP: Final = tuple(range(-32, 32))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(4 * 1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def load_children() -> dict:
    keys = {t[0] for t in TARGETS}
    out = {}
    for line in (SCRATCH / "childgeo_states.txt").read_text().splitlines():
        if "CHILDSDF" not in line:
            continue
        t = line[line.index("CHILDSDF"):].split()
        state, ordinal = int(t[1]), int(t[2])
        if (state, ordinal) in keys:
            out[(state, ordinal)] = [
                [int(x, 16) for x in t[3 + 4 * v:7 + 4 * v]]
                for v in range(3)]
    return out


def fixed(word: int) -> int:
    return int(round(model.bits_f32(word) * 256.0))


def interior_pixel(vertices, tile):
    fx = [(fixed(v[0]), fixed(v[1])) for v in vertices]
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


def spread_tiles(vertices, count=6):
    fx = [(fixed(v[0]), fixed(v[1])) for v in vertices]
    xs = [p[0] // 256 for p in fx]
    ys = [p[1] // 256 for p in fx]
    tx0, tx1 = max(min(xs) // 32, 0), min(max(xs) // 32, 63)
    ty0, ty1 = max(min(ys) // 32, 0), min(max(ys) // 32, 63)
    out = []
    for f in (0.05, 0.2, 0.4, 0.6, 0.8, 0.95):
        tile = (tx0 + int((tx1 - tx0) * 0.5), ty0 + int((ty1 - ty0) * f))
        if interior_pixel(vertices, tile):
            out.append(tile)
    for f in (0.1, 0.3, 0.5, 0.7, 0.9):
        if len(out) >= count:
            break
        tile = (tx0 + int((tx1 - tx0) * f), ty0 + int((ty1 - ty0) * 0.5))
        if interior_pixel(vertices, tile) and tile not in out:
            out.append(tile)
    return out[:count]


def sweep_word(bits: int, offset: int) -> int:
    if bits == 0:
        return bits
    return model.key_to_bits(model.ordered_key(bits) + offset)


def generate(output_directory: Path) -> dict:
    if output_directory.exists():
        raise FileExistsError(output_directory)
    children = load_children()
    vertices = bytearray()
    draws: list[dict] = []
    experiments: list[dict] = []

    for (key, ch) in TARGETS:
        state, ordinal = key
        verts = children[key]
        tiles = spread_tiles(verts)
        # the swept vertex: the one with the non-zero channel word
        swept = max(range(3), key=lambda v: abs(model.bits_f32(verts[v][2+ch])))
        for start in range(0, len(SWEEP), 4):
            quad = SWEEP[start:start+4]
            for tile in tiles:
                pixel = interior_pixel(verts, tile)
                if pixel is None:
                    continue
                record = len(draws)
                for vi, vertex in enumerate(verts):
                    base = vertex[2 + ch]
                    chans = [sweep_word(base, k) if vi == swept else base
                             for k in quad]
                    vertices.extend(VERTEX.pack(
                        vertex[0], vertex[1], 0, 0, *chans))
                experiments.append({
                    "recordIndex": record,
                    "inputOrdinal": start // 4,
                    "variant": "nine-word-sweep",
                    "split": "discovery",
                    "state": state,
                    "drawOrdinal": ordinal,
                    "channel": ch,
                    "sweptVertex": swept,
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
            "establishesDegenerateChildSetupLaw": True,
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
        "schema": "walle-reveal-agx-nine-word-sweep-plan-manifest-v1",
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
