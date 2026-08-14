#!/usr/bin/env python3
"""C tile-walk accumulator tomography on the s58 o4 child.

The dense capture shows hw C tiles walking with hidden sub-word state
(bounded sawtooth vs the exact internal-slope plane, drift -2u/row with
+(ulp-2) resync jumps).  To read the hidden accumulator bits, redraw the
same triangle many times with the value words key-shifted:

  family "seed":  anchor-vertex value words shifted by j in [-40, 40)
                  -> the whole walk translates by ~j*ulp(v_anchor); the
                  per-row exported word flips when the accumulator
                  crosses its export boundary, pinning acc(row) mod ulp
                  to ~0.1u resolution.
  family "slope": far-vertex value words shifted by k in [-20, 20)
                  -> numerator/step scan (fan around the anchor),
                  decoupling step-quantization state from seed state.

Channels R/G carry the shifted ch0/ch1; B/A carry the unshifted words
as in-draw controls.  One column (tx=60) x rows 19..63.
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
    ROOT / "build" / "analysis-agx-basis" / "c-walk-tomography-plan-v1"
)
VERTEX: Final = struct.Struct("<8I")
GEO: Final = Path("/tmp/walle/build/_childgeo_all_residual_states.txt")
STATE: Final = 58
ORDINAL: Final = 4
COLUMN: Final = 60
ROWS: Final = range(19, 64)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(4 * 1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def load_child():
    for line in GEO.read_text().splitlines():
        t = line.split()
        if t[0] == "CHILDSDF" and int(t[1]) == STATE and int(t[2]) == ORDINAL:
            return [[int(x, 16) for x in t[3 + 4 * v:7 + 4 * v]]
                    for v in range(3)]
    raise SystemExit("child not found")


def fixed(word: int) -> int:
    return int(round(model.bits_f32(word) * 256.0))


def shift_word(bits: int, offset: int) -> int:
    if offset == 0 or bits == 0:
        return bits
    return model.key_to_bits(model.ordered_key(bits) + offset)


def interior_pixel(fx, tile):
    det = ((fx[1][0] - fx[0][0]) * (fx[2][1] - fx[0][1])
           - (fx[1][1] - fx[0][1]) * (fx[2][0] - fx[0][0]))
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


def generate(output_directory: Path) -> dict:
    if output_directory.exists():
        raise FileExistsError(output_directory)
    verts = load_child()
    fx = [(fixed(v[0]), fixed(v[1])) for v in verts]
    anchor = min(range(3), key=lambda i: (fx[i][1], fx[i][0]))
    far = max(range(3), key=lambda i: fx[i][1])
    print(f"anchor v{anchor} at {fx[anchor]}, far v{far} at {fx[far]}",
          file=sys.stderr)

    variants = ([("seed", anchor, j) for j in range(-40, 40)]
                + [("slope", far, k) for k in range(-20, 20)])

    vertices = bytearray()
    draws: list[dict] = []
    experiments: list[dict] = []
    for family, vi_target, offset in variants:
        for ty in ROWS:
            pixel = interior_pixel(fx, (COLUMN, ty))
            if pixel is None:
                continue
            record = len(draws)
            for vi, vertex in enumerate(verts):
                ch0, ch1 = vertex[2], vertex[3]
                p0 = shift_word(ch0, offset) if vi == vi_target else ch0
                p1 = shift_word(ch1, offset) if vi == vi_target else ch1
                vertices.extend(VERTEX.pack(
                    vertex[0], vertex[1], 0, 0, p0, p1, ch0, ch1))
            experiments.append({
                "recordIndex": record,
                "inputOrdinal": record,
                "variant": "c-walk-tomography",
                "split": "discovery",
                "state": STATE,
                "drawOrdinal": ORDINAL,
                "anchor": anchor,
                "family": family,
                "offset": offset,
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
                "tileX": COLUMN,
                "tileY": ty,
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
            "establishesCWalkAccumulatorLaw": True,
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
        "schema": "walle-reveal-agx-c-walk-tomography-plan-manifest-v1",
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
