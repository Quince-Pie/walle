#!/usr/bin/env python3
"""Single-clip interpolation ruler v4: product isolation.

v5: four e-sweep points per record with s = 0 isolate the t-hat product
with dense mantissa coverage, pinning t-hat per geometry exactly as v4
pinned u-hat.

With e - s an exact power of two, v = s + t_q * 2^k involves no product
rounding, so each solved hardware word reads the quantized interpolation
parameter t_q directly.  Sweeping s in ulp steps walks the value across
knife positions and pins t_q to full precision per geometry.
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
    ROOT / "build" / "analysis-agx-basis" / "single-clip-ruler-v5c-plan-v1"
)
VERTEX: Final = struct.Struct("<8I")

Y_INSIDE: Final = (613.0, 800.0, 987.0, 1201.0, 645.0, 731.0, 1043.0, 1387.0)
Y_OUTSIDE: Final = (-651.0, -739.0, -911.0, -1207.0, -523.0, -587.0,
                    -1033.0, -1499.0)
X0: Final = 900.0
X1: Final = 1250.0
X2: Final = 1100.0
V1_VALUE: Final = 0.25

# s sweeps in 1-ulp steps; e = s + 2 exactly (delta = 2^1, exact for all s
# near -1 with |s| < 2, since both live on the 2^-24-aligned grid).
S_OFFSETS: Final = tuple(range(0, 64, 1))
S_BASE: Final = -1.0
E_VALUE: Final = 0.75


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(4 * 1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _perturb(bits: int, offset: int) -> int:
    return model.key_to_bits(model.ordered_key(bits) + offset)


def interior_pixels3(v0, v1, v2):
    fx = [(round(v[0]*256), round(v[1]*256)) for v in (v0, v1, v2)]
    det = ((fx[1][0]-fx[0][0])*(fx[2][1]-fx[0][1])
           - (fx[1][1]-fx[0][1])*(fx[2][0]-fx[0][0]))
    orient = 1 if det > 0 else -1
    out = []
    for band in ((40, 200), (200, 360), (360, 560)):
        found = None
        for y in range(band[0], band[1], 16):
            cy = 256*y + 128
            for x in range(960, 1240, 8):
                cx = 256*x + 128
                ok = True
                for e in range(3):
                    ax, ay = fx[e]; bx, by = fx[(e+1) % 3]
                    if orient*((bx-ax)*(cy-ay)-(by-ay)*(cx-ax)) <= 4096:
                        ok = False
                        break
                if ok:
                    found = (x, y)
                    break
            if found:
                break
        if found:
            out.append(found)
    return out


def generate(output_directory: Path) -> dict:
    if output_directory.exists():
        raise FileExistsError(output_directory)

    values = []
    for base in (0.5000152587890625, 0.75, 1.25, 1.8999996185302734):
        for js in range(0, 16):
            e = model.bits_f32(_perturb(model.f32_bits(base), 5 * js))
            values.append(e)

    vertices = bytearray()
    draws: list[dict] = []
    experiments: list[dict] = []
    geometries = []
    for y_in in Y_INSIDE:
        for y_out in Y_OUTSIDE:
            geometries.append(((X0, y_out), (X1, -512.0), (X2, y_in)))

    for geometry_index, geometry in enumerate(geometries):
        v0, v1, v2 = geometry
        pixels = interior_pixels3(v0, v1, v2)
        if not pixels:
            continue
        for start in range(0, len(values), 4):
            quad = values[start:start+4]
            pairs = [(0.0, e) for e in quad]
          # one record per sampled tile
            record = None
            for pixel in pixels:
                record = len(draws)
                tile = (pixel[0]//32, pixel[1]//32)
                for vertex_index in range(3):
                    channels = [model.f32_bits((pe, V1_VALUE, ps)[vertex_index])
                                for (ps, pe) in pairs]
                    vertices.extend(VERTEX.pack(
                        model.f32_bits(geometry[vertex_index][0]),
                        model.f32_bits(geometry[vertex_index][1]),
                        0, 0, *channels))
                experiments.append({
                    "recordIndex": record,
                    "inputOrdinal": start // 4,
                    "variant": "single-clip-ruler-v5c",
                    "split": "discovery",
                    "geometryIndex": geometry_index,
                    "geometry": [list(v) for v in geometry],
                    "pairs": [[ps, pe] for (ps, pe) in pairs],
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
        "sweepCount": len(values),
        "patternCount": len(draws),
        "drawCount": len(draws),
        "coefficientTripleCount": len(draws) * 4,
    }
    plan = {
        "schema": "walle-reveal-agx-setup-accumulator-plan-v1",
        "authority": {
            "opensReferencePixels": False,
            "usesOutputFeedback": False,
            "establishesClipInterpolationLaw": True,
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
        "v1Value": V1_VALUE,
        "experiments": experiments,
        "draws": draws,
        "census": census,
    }
    plan_path = output_directory / "reveal-agx-setup-accumulator-plan.json"
    plan_path.write_text(json.dumps(plan, indent=2, sort_keys=True) + "\n",
                         encoding="utf-8")
    manifest = {
        "schema": "walle-reveal-agx-single-clip-ruler-v5c-plan-manifest-v1",
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
