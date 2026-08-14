#!/usr/bin/env python3
"""Capture hardware setup coefficients at every residual tile of the
remaining AGX-residual states.

For each residual pixel (and its two sample partners) the plan draws every
model child whose bounds admit that tile — both as the explicit clip child
and as the raw source triangle (hardware performs its own guard clip) — and
samples one interior pixel of that child in that tile.  A handful of extra
tiles per child adds redundancy for law fitting.
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
    ROOT / "build" / "analysis-agx-basis" / "residual-states-plan-v1"
)
VERTEX: Final = struct.Struct("<8I")
SCRATCH: Final = Path(
    "/tmp/nix-shell.PFgUGF/claude-1000/-tmp-walle/"
    "4ccfbce8-33b2-4b5f-8e29-93486397c8a4/scratchpad")
STATES: Final = (33, 34, 35, 39, 40, 41, 42, 44, 45, 47, 58, 60)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(4 * 1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def load_children() -> dict:
    per_state: dict[int, list[dict]] = {}
    for line in (SCRATCH / "childgeo_states.txt").read_text().splitlines():
        if "CHILDSDF" not in line:
            continue
        t = line[line.index("CHILDSDF"):].split()
        state, ordinal = int(t[1]), int(t[2])
        vertices = []
        for v in range(3):
            vertices.append([int(w, 16) for w in t[3 + 4 * v:7 + 4 * v]])
        per_state.setdefault(state, []).append(
            {"ordinal": ordinal, "vertices": vertices})
    return per_state


def f32_value(word: int) -> float:
    return model.bits_f32(word)


def fixed(word: int) -> int:
    return int(round(f32_value(word) * 256.0))


def interior_pixel(vertices, tile):
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


def child_tiles(vertices, base_tiles):
    """base tiles plus a few spread interior tiles of the child."""
    fx = [(fixed(v[0]), fixed(v[1])) for v in vertices]
    xs = [p[0] // 256 for p in fx]
    ys = [p[1] // 256 for p in fx]
    tx0, tx1 = max(min(xs) // 32, 0), min(max(xs) // 32, 63)
    ty0, ty1 = max(min(ys) // 32, 0), min(max(ys) // 32, 63)
    extras = set()
    for fx_t in (0.25, 0.5, 0.75):
        for fy_t in (0.25, 0.5, 0.75):
            extras.add((tx0 + int((tx1 - tx0) * fx_t),
                        ty0 + int((ty1 - ty0) * fy_t)))
    return sorted(set(base_tiles) | extras)


def generate(output_directory: Path) -> dict:
    if output_directory.exists():
        raise FileExistsError(output_directory)
    per_state = load_children()
    residuals: dict[int, list[tuple[int, int]]] = {}
    for line in (ROOT / "build" / "_residual_list.txt").read_text().splitlines():
        t = line.split()
        state = int(t[0])
        if state in STATES:
            residuals.setdefault(state, []).append((int(t[1]), int(t[2])))

    vertices = bytearray()
    draws: list[dict] = []
    experiments: list[dict] = []
    skipped = 0

    for state in STATES:
        children = per_state.get(state, [])
        base_tiles = set()
        for x, y in residuals.get(state, []):
            for px, py in ((x, y), (x ^ 1, y), (x, y ^ 1)):
                base_tiles.add((px // 32, py // 32))
        by_ord = {c["ordinal"]: c for c in children}
        for child in children:
            ordinal = child["ordinal"]
            # draw explicit children and raw bases; for raw bases the
            # sampled pixel must lie inside the clipped region, which the
            # explicit child region provides.
            for tile in child_tiles(child["vertices"], sorted(base_tiles)):
                pixel = interior_pixel(child["vertices"], tile)
                if pixel is None:
                    skipped += 1
                    continue
                record = len(draws)
                for vertex in child["vertices"]:
                    vertices.extend(VERTEX.pack(
                        vertex[0], vertex[1], 0, 0,
                        vertex[2], vertex[3], vertex[2], vertex[3]))
                experiments.append({
                    "recordIndex": record,
                    "inputOrdinal": record,
                    "variant": "residual-states",
                    "split": "discovery",
                    "state": state,
                    "drawOrdinal": ordinal,
                    "regionOrdinal": ordinal,
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
        "schema": "walle-reveal-agx-residual-states-plan-manifest-v1",
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
