#!/usr/bin/env python3
"""Dense-tile capture for the nine hard setup channels.

The nine (state, ordinal) post-guard children whose slope/C words deviate
from the recovered setup law by 1-2 low bits are drawn at EVERY tile whose
interior admits an authenticated pixel, with channels R/G carrying the
canonical varyings and B/A carrying an anchor-word +1-key perturbation.
The dense C-tile coverage plus perturbed twins overdetermines the failing
join/mid/constant chain.
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
    ROOT / "build" / "analysis-agx-basis" / "nine-children-dense-plan-v1"
)
VERTEX: Final = struct.Struct("<8I")
SCRATCH: Final = Path(
    "/tmp/nix-shell.PFgUGF/claude-1000/-tmp-walle/"
    "4ccfbce8-33b2-4b5f-8e29-93486397c8a4/scratchpad")
NINE: Final = ((39, 101), (40, 104), (41, 104), (42, 104), (58, 101),
               (58, 104), (58, 109), (60, 106), (60, 109))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(4 * 1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def load_children() -> dict:
    out = {}
    for line in (SCRATCH / "childgeo_states.txt").read_text().splitlines():
        if "CHILDSDF" not in line:
            continue
        t = line[line.index("CHILDSDF"):].split()
        state, ordinal = int(t[1]), int(t[2])
        if (state, ordinal) in NINE:
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


def anchor_index(vertices):
    fx = [(fixed(v[0]), fixed(v[1])) for v in vertices]
    return min(range(3), key=lambda i: (fx[i][1], fx[i][0]))


def _perturb(bits: int, offset: int) -> int:
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

    for (state, ordinal), verts in sorted(children.items()):
        an = anchor_index(verts)
        fx = [(fixed(v[0]), fixed(v[1])) for v in verts]
        xs = [p[0] // 256 for p in fx]
        ys = [p[1] // 256 for p in fx]
        tx0, tx1 = max(min(xs) // 32, 0), min(max(xs) // 32 + 1, 63)
        ty0, ty1 = max(min(ys) // 32, 0), min(max(ys) // 32 + 1, 63)
        kept = 0
        for ty in range(ty0, ty1 + 1):
            for tx in range(tx0, tx1 + 1):
                pixel = interior_pixel(verts, (tx, ty))
                if pixel is None:
                    continue
                record = len(draws)
                for vi, vertex in enumerate(verts):
                    ch0, ch1 = vertex[2], vertex[3]
                    p0 = _perturb(ch0, 1) if vi == an else ch0
                    p1 = _perturb(ch1, 1) if vi == an else ch1
                    vertices.extend(VERTEX.pack(
                        vertex[0], vertex[1], 0, 0, ch0, ch1, p0, p1))
                experiments.append({
                    "recordIndex": record,
                    "inputOrdinal": record,
                    "variant": "nine-children-dense",
                    "split": "discovery",
                    "state": state,
                    "drawOrdinal": ordinal,
                    "anchor": an,
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
                kept += 1
        print(f"state {state} ord {ordinal}: {kept} tiles", file=sys.stderr)

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
        "schema": "walle-reveal-agx-nine-children-dense-plan-manifest-v1",
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
