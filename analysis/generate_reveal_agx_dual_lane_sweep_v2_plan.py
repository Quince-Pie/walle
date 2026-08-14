#!/usr/bin/env python3
"""Dense edge-pair sweep for the fused dual-lane gradient array.

Thousands of triangles with finely swept apex offsets (1-subpixel steps),
one authenticated interior pixel per geometry, one-hot basis varyings:
each record's exported A/B words are the hardware barycentric gradients
for a known (edge_x, edge_y, selector) triple.  The sweep spans zero,
near-zero, and general edge coefficients on both axes so the partner-lane
leakage and lane biases are overdetermined.
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
    ROOT / "build" / "analysis-agx-basis" / "dual-lane-sweep-plan-v2"
)
VERTEX: Final = struct.Struct("<8I")
ONE: Final = 0x3F800000


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(4 * 1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def f32(value: float) -> float:
    import numpy as np
    return float(np.float32(value))


def geometries():
    """Yield vertex triples in pixels (must be exact f32 multiples of 1/256)."""
    out = []
    # Family A: off-screen-shifted right triangles (same edges as sweep-1's
    # on-screen family) - trigger test for the production anomalies.
    for base in (1024.0, 1421.0):
        for height in (512.0, 1150.5):
            for shift in ((-1200.0, 0.0), (0.0, -1200.0), (-1200.0, -1200.0),
                          (1400.0, 0.0), (0.0, 1400.0)):
                x0, y0 = 640.0 + shift[0], 640.0 + shift[1]
                for dx256 in (-3, 0, 3):
                    for dy256 in (-1, 0, 1):
                        apex = (x0 + base + dx256 / 256.0,
                                y0 + height + dy256 / 256.0)
                        out.append(((x0, y0), (x0 + base, y0), apex))
    # Family B: production-like exact replicas shifted variants
    prod_like = [
        ((512.0, 614.5), (512.0, 1663.5), (-537.0, 1663.5)),
        ((1638.5, -512.0), (1865.5, -512.0), (1865.5, 614.5)),
        ((1865.5, -739.0), (1865.5, 614.5), (512.0, 614.5)),
        ((512.0, 614.5), (512.0, 1765.0), (-638.5, 1765.0)),
    ]
    for tri in prod_like:
        out.append(tri)
        # on-screen shifted twin (same edges, positive coords)
        minx = min(p[0] for p in tri); miny = min(p[1] for p in tri)
        sx = 8.0 - minx if minx < 8.0 else 0.0
        sy = 8.0 - miny if miny < 8.0 else 0.0
        out.append(tuple((p[0] + sx, p[1] + sy) for p in tri))
    # Family C: dense-mantissa slivers, many more, on-screen.
    for k in range(360):
        x0 = 300.0 + (k % 13) + (k % 3) / 256.0
        y0 = 300.0 + (k % 17) + (k % 5) / 256.0
        w = 401.0 + k * 2.0 + (k % 7) / 256.0 + (k % 29) / 128.0
        h = 700.0 - k + (k % 11) / 256.0 + (k % 23) / 64.0
        out.append(((x0, y0), (x0 + w, y0 + h / 2), (x0 + w / 3, y0 + h)))
    return out


def interior_pixel(fx):
    det = ((fx[1][0] - fx[0][0]) * (fx[2][1] - fx[0][1])
           - (fx[1][1] - fx[0][1]) * (fx[2][0] - fx[0][0]))
    if det == 0:
        return None
    orient = 1 if det > 0 else -1
    cx = sum(p[0] for p in fx) // 3
    cy = sum(p[1] for p in fx) // 3
    x, y = cx // 256, cy // 256
    for radius in range(0, 40):
        for oy in range(-radius, radius + 1):
            for ox in range(-radius, radius + 1):
                px, py = x + ox, y + oy
                if not (0 <= px < 2048 and 0 <= py < 2048):
                    continue
                sx, sy = 256 * px + 128, 256 * py + 128
                good = True
                for e in range(3):
                    ax, ay = fx[e]
                    bx, by = fx[(e + 1) % 3]
                    cross = orient * ((bx - ax) * (sy - ay)
                                      - (by - ay) * (sx - ax))
                    if cross < 512:
                        good = False
                        break
                if good:
                    return px, py
    return None


def generate(output_directory: Path) -> dict:
    if output_directory.exists():
        raise FileExistsError(output_directory)
    vertices = bytearray()
    draws: list[dict] = []
    experiments: list[dict] = []
    dropped = 0
    for verts in geometries():
        fx = [(round(f32(v[0]) * 256), round(f32(v[1]) * 256)) for v in verts]
        pixel = interior_pixel(fx)
        if pixel is None:
            dropped += 1
            continue
        record = len(draws)
        for vi, v in enumerate(verts):
            basis = [ONE if vi == 0 else 0,
                     ONE if vi == 1 else 0,
                     ONE if vi == 2 else 0,
                     ONE]
            vertices.extend(VERTEX.pack(
                model.f32_bits(f32(v[0])), model.f32_bits(f32(v[1])),
                0, 0, *basis))
        experiments.append({
            "recordIndex": record,
            "variant": "dual-lane-sweep-v2",
            "positions": [[f32(v[0]), f32(v[1])] for v in verts],
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
            "tileX": pixel[0] // 32,
            "tileY": pixel[1] // 32,
        })
    print(f"{len(draws)} draws, {dropped} dropped", file=sys.stderr)
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
            "establishesDualLaneGradientLaw": True,
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
        "schema": "walle-reveal-agx-dual-lane-sweep-plan-manifest-v1",
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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=OUTPUT_DEFAULT)
    arguments = parser.parse_args()
    manifest = generate(arguments.output)
    print(json.dumps(manifest["census"], indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
