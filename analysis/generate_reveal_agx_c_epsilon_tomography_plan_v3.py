#!/usr/bin/env python3
"""Wide-product epsilon tomography v2: single-binade lever.

Same as v1 but eps = (64+j)*2^k, j = 0..63: the eps f32 word stays in
one binade; v3 samples column 48 where the net lever is exactly
eps/2 (anchor eps minus x-part eps/2; x displacement index 8 = 2^3),
so every eps term is power-of-2 aligned to the accumulator grid:
step = 4v = 2^(bl-28) exactly (one 28-bit-acc quantum), span 4G.

Same triangle as c-truthtable4 (V0 (512,511.75), V1 (2560,511.75),
V2 (2560,4607.75); det 2^39; probe value w on V2 drives the wide
y-product P = dm * d_o, d_o = 128*ty - 2047).  New: V0 (the anchor)
carries a small exact value eps = j * 2^k.  Because x2-x0 = x1-x0,
eps cancels out of the y-gradient B; it only adds an exact narrow
x-axis term to C (x displacement index 11 at column 60, products
<= 24 bits).  Scanning j and reading where the exported C word flips
granule reads the wide term's internal value on the accumulator grid,
per cell - the same lever that exposed the storage jam (later-72).

Per (row, t): k is chosen so the lever step is ~G/24 of that cell's
24-bit granule; j = -24..24 covers +-1G.  t values include the killer
0xC00 block; t=0 columns are exact controls that calibrate the lever.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
import sys
from pathlib import Path
from typing import Final

sys.path[:0] = ["/tmp/walle", "/tmp/walle/analysis"]

ROOT: Final = Path(__file__).resolve().parent.parent
OUTPUT_DEFAULT: Final = (
    ROOT / "build" / "analysis-agx-basis" / "c-epsilon-tomography-plan-v3"
)
VERTEX: Final = struct.Struct("<8I")
ROWS: Final = range(17, 64)
COLUMN: Final = 48

V0 = (0x44000000, 0x43FFE000)   # (512, 511.75)
V1 = (0x45200000, 0x43FFE000)   # (2560, 511.75)
V2 = (0x45200000, 0x458FFE00)   # (2560, 4607.75)

T_VALUES: Final = (0, 1, 2, 3, 5, 8, 21, 255, 1027, 3072)
J_RANGE: Final = range(0, 64)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(4 * 1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def f32_word(sign: int, mant: int, exp: int) -> int:
    """sign * mant * 2^exp as f32 word (mant normalised up to 24 bits)."""
    if mant == 0:
        return 0
    bl = mant.bit_length()
    mant <<= 24 - bl
    exp -= 24 - bl
    e = exp + 23 + 127
    assert 1 <= e <= 254, (sign, mant, exp)
    return ((1 << 31) if sign < 0 else 0) | (e << 23) | (mant & 0x7FFFFF)


def interior_pixel(tile):
    x = min(tile[0] * 32 + 16, 2046)
    y = max(tile[1] * 32 + 16, 516)
    return x, y


def generate(output_directory: Path) -> dict:
    if output_directory.exists():
        raise FileExistsError(output_directory)
    vertices = bytearray()
    draws: list[dict] = []
    experiments: list[dict] = []
    for t in T_VALUES:
        word = 0x3F800000 + t
        dm = 0x800000 + t
        for ty in ROWS:
            d_o = 128 * ty - 2047
            if d_o <= 0:
                continue
            P = dm * d_o
            bl = P.bit_length()
            if bl < 31:
                continue
            # w value = dm * 2^-23; granule G_val = 2^(bl-24) * 2^(-23-14).
            # eps lever at column 60 ~= 0.3125 * eps; want step ~ G_val/24
            # -> eps_step = G_val/24/0.3125 ~= G_val * 2^-3 (0.125 ~ 1/7.5,
            # conservative fine side: use 2^-3 -> step ~ 0.04 G).
            k = bl - 64
            for j in J_RANGE:
                eps_word = f32_word(1, 64 + j, k)
                px, py = interior_pixel((COLUMN, ty))
                record = len(draws)
                for vi, (vx, vy) in enumerate((V0, V1, V2)):
                    if vi == 0:
                        r = g = eps_word
                        a = 0
                    elif vi == 2:
                        r = g = word
                        a = 0x3F800000
                    else:
                        r = g = 0
                        a = 0
                    vertices.extend(VERTEX.pack(vx, vy, 0, 0, r, g, 0, a))
                experiments.append({
                    "recordIndex": record,
                    "inputOrdinal": record,
                    "variant": "c-epsilon-tomography",
                    "split": "discovery",
                    "state": 0,
                    "drawOrdinal": 0,
                    "anchor": 0,
                    "family": f"t{t}",
                    "offset": j,
                    "word": word,
                    "epsWord": eps_word,
                    "epsK": k,
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
            "establishesCEpsilonTomographyV3": True,
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
        "schema": "walle-reveal-agx-c-epsilon-tomography-manifest-v1",
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
