#!/usr/bin/env python3
"""Model-free clipped-plane readback.

Triangles crossing the guard band (y < -512, NDC +1.5) carry one-hot
basis varyings; the residual-value variant probe records
interpolate_at_center() + the four partner offsets at ~40 interior
pixels per geometry.  The RTZ-quantized samples overconstrain each
clipped sub-triangle's wide plane, recovering the fused clip+setup
output (and thus t_hw) to ~2^-30 without modeling the setup chain.

Denominators (y_in - y_out) cover powers of two (calibration: t exact),
v6-overlapping values (cross-checks), and shared-den pairs from two
numerators (num-dependence probes).
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
    ROOT / "build" / "analysis-agx-basis" / "t-readback-plan-v1"
)
VERTEX: Final = struct.Struct("<8I")
ONE: Final = 0x3F800000


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(4 * 1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def geometries():
    out = []
    dens = [1024, 2048, 1136, 1264, 1328, 1384, 1421, 1520, 1600, 1808,
            2065, 2113]
    for den in dens:
        for y_in in (613.0, 800.0):
            y_out = y_in - den
            if y_out >= -512.0:
                continue
            # V0 crossing source (carries basis0), V2 below guard band,
            # V1 on-screen apex
            out.append(((640.0, y_in), (1600.0, 913.0), (704.0, y_out)))
            out.append(((640.0, y_in), (1600.0, 913.0), (1216.0, y_out)))
    return out


def interior_pixels(fx, count):
    det = ((fx[1][0] - fx[0][0]) * (fx[2][1] - fx[0][1])
           - (fx[1][1] - fx[0][1]) * (fx[2][0] - fx[0][0]))
    if det == 0:
        return []
    orient = 1 if det > 0 else -1
    xs = [p[0] // 256 for p in fx]
    ys = [p[1] // 256 for p in fx]
    x0, x1 = max(min(xs), 1), min(max(xs), 2046)
    y0, y1 = max(min(ys), 1), min(max(ys), 2046)
    picks = []
    if x1 <= x0 or y1 <= y0:
        return []
    import random
    rng = random.Random(1234)
    tries = 0
    while len(picks) < count and tries < 20000:
        tries += 1
        px = rng.randrange(x0, x1)
        py = rng.randrange(y0, y1)
        sx, sy = 256 * px + 128, 256 * py + 128
        ok = True
        for e in range(3):
            ax, ay = fx[e]
            bx, by = fx[(e + 1) % 3]
            cross = orient * ((bx - ax) * (sy - ay) - (by - ay) * (sx - ax))
            if cross < 1024:
                ok = False
                break
        if ok and (px, py) not in picks:
            picks.append((px, py))
    return picks


def generate(output_directory: Path) -> dict:
    if output_directory.exists():
        raise FileExistsError(output_directory)
    vertices = bytearray()
    draws: list[dict] = []
    experiments: list[dict] = []
    for gi, verts in enumerate(geometries()):
        fx = [(round(v[0] * 256), round(v[1] * 256)) for v in verts]
        pixels = interior_pixels(fx, 40)
        if len(pixels) < 12:
            print(f"geometry {gi}: only {len(pixels)} pixels", file=sys.stderr)
            continue
        for px, py in pixels:
            record = len(draws)
            for vi, v in enumerate(verts):
                basis = [ONE if vi == 0 else 0,
                         ONE if vi == 1 else 0,
                         ONE if vi == 2 else 0,
                         ONE]
                vertices.extend(VERTEX.pack(
                    model.f32_bits(v[0]), model.f32_bits(v[1]),
                    0, 0, *basis))
            experiments.append({
                "recordIndex": record,
                "variant": "t-readback",
                "geometryIndex": gi,
                "positions": [[v[0], v[1]] for v in verts],
                "x": px,
                "y": py,
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
                "tileX": px // 32,
                "tileY": py // 32,
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
            "establishesClipPlaneReadback": True,
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
        "schema": "walle-reveal-agx-t-readback-plan-manifest-v1",
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
