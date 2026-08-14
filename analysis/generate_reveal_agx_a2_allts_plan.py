#!/usr/bin/env python3
"""A2 transfer planes for ALL triangles + basis decomposition probes.

For the six residual states (42, 31, 33, 40, 41, 58, 60): draw every
non-degenerate transfer triangle with all-1.0 varyings and read the
LDCF plane (A, B, C) at up to 12 interior on-screen tiles.  For state
42 triangles 0 and 2, additionally draw the three BASIS configs
(value 1.0 at one vertex, 0 at the others) at the same tiles - direct
hardware ground truth for the basis-form join rule.
"""
from __future__ import annotations

import hashlib
import json
import struct
import sys
from pathlib import Path
from typing import Final

sys.path[:0] = ["/tmp/walle", "/tmp/walle/analysis"]

import _sweep_fused_join_lattice as m  # noqa: E402

ROOT: Final = Path("/tmp/walle")
OUT: Final = ROOT / "build" / "analysis-agx-basis" / "a2-allts-plan-v1"
TRACE: Final = Path("/tmp/walle-analysis/A2-geometry-sweep-v74")
VERTEX: Final = struct.Struct("<8I")
STATES: Final = (42, 31, 33, 40, 41, 58, 60)
ONE: Final = 0x3F800000


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(4 * 1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def load_mesh(state: int):
    tr = json.load(open(TRACE / f"state-{state}" /
                        "reveal-mask-trace.json"))["nativeScale"]["A2Geometry"]
    vs = bytes.fromhex(tr["vertexStreamHex"])
    verts = [struct.unpack_from("<II", vs, i * 48)
             for i in range(tr["vertexCount"])]
    idx = tr["indices"]
    tris = [tuple(idx[i:i + 3]) for i in range(0, len(idx), 3)]
    return verts, tris


def inside(px, py, pos):
    xs = [m.bits_f32(p[0]) for p in pos]
    ys = [m.bits_f32(p[1]) for p in pos]
    det = ((xs[1] - xs[0]) * (ys[2] - ys[0])
           - (ys[1] - ys[0]) * (xs[2] - xs[0]))
    if det == 0:
        return False
    sgn = 1 if det > 0 else -1
    for e in range(3):
        ax, ay = xs[e], ys[e]
        bx, by = xs[(e + 1) % 3], ys[(e + 1) % 3]
        cross = sgn * ((bx - ax) * (py + 0.5 - ay)
                       - (by - ay) * (px + 0.5 - ax))
        if cross < 4.0:
            return False
    return True


def pick_tiles(pos, want=12):
    xs = [m.bits_f32(p[0]) for p in pos]
    ys = [m.bits_f32(p[1]) for p in pos]
    tx0 = max(0, int(min(xs) // 32))
    tx1 = min(63, int(max(xs) // 32))
    ty0 = max(0, int(min(ys) // 32))
    ty1 = min(63, int(max(ys) // 32))
    found = []
    rows = list(range(ty0, ty1 + 1))
    prefer = rows[:2] + rows[-2:] + rows[len(rows) // 2:len(rows) // 2 + 1]
    seen = set()
    for ty in prefer + rows:
        if ty in seen:
            continue
        seen.add(ty)
        row_hits = 0
        for tx in range(tx0, tx1 + 1):
            px, py = tx * 32 + 15, ty * 32 + 15
            if inside(px, py, pos):
                found.append((tx, ty, px, py))
                row_hits += 1
                if row_hits >= 2:
                    break
        if len(found) >= want:
            break
    return found[:want]


def generate(output_directory: Path) -> dict:
    if output_directory.exists():
        raise FileExistsError(output_directory)
    vertices = bytearray()
    draws = []
    experiments = []

    def emit(state, tindex, pos, vwords, family, tiles):
        for tx, ty, px, py in tiles:
            record = len(draws)
            for (vx, vy), val in zip(pos, vwords):
                vertices.extend(VERTEX.pack(vx, vy, 0, 0,
                                            val, val, val, val))
            experiments.append({
                "recordIndex": record,
                "inputOrdinal": record,
                "variant": "a2-allts",
                "split": "discovery",
                "state": state,
                "drawOrdinal": tindex,
                "anchor": 0,
                "family": family,
                "offset": tindex,
            })
            draws.append({
                "recordIndex": record,
                "targetIndex": 0,
                "targetRecordIndex": 0,
                "sampleRecordIndex": 0,
                "sampleOrdinal": 0,
                "patternIndex": record,
                "x": px,
                "y": py,
                "tileX": tx,
                "tileY": ty,
            })

    for state in STATES:
        verts, tris = load_mesh(state)
        for tindex, tri in enumerate(tris):
            pos = [verts[v] for v in tri]
            if len({pos[0], pos[1], pos[2]}) < 3:
                continue
            tiles = pick_tiles(pos)
            if not tiles:
                continue
            emit(state, tindex, pos, (ONE, ONE, ONE), "one", tiles)
            if state == 42 and tindex in (0, 2, 6):
                for b in range(3):
                    vwords = tuple(ONE if i == b else 0 for i in range(3))
                    emit(state, tindex, pos, vwords, f"basis{b}", tiles)

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
            "establishesA2AllTrianglePlanes": True,
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
        "schema": "walle-reveal-agx-a2-allts-manifest-v1",
        "generator": {
            "path": "analysis/generate_reveal_agx_a2_allts_plan.py",
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
    print(json.dumps(generate(OUT)["census"], indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
