#!/usr/bin/env python3
"""Generate a c-truthtable plan at a chosen displacement trailing-zero class.

Clone of analysis/generate_reveal_agx_c_truthtable4_plan.py with the anchor y
as a parameter.  Geometry is otherwise identical to tt4 so the selector stays
transparent: x-edge 2048, height 4096, det a power of two.  Only the anchor's
subpixel position moves, and it moves by at most two pixels, so every sampled
pixel stays where tt4's did.

Why the anchor sets tz.  With tiles 8192 subpixels apart,
`disp = 8192*ty - ay`; writing `ay = 2^k * odd` with k < 13 gives
`disp = 2^k * (2^(13-k)*ty - odd)` whose bracket is odd for every row, so
tz(disp) = k exactly and `d_o = 2^(13-k)*ty - odd` is odd.  That also fixes
the d_o range, which is what breaks the tz-vs-d_o confound: tz=9 lands on
bl(d_o) 5..10 (overlapping tt1's 4..12 at a different tz) and tz=5 lands on
9..14 (overlapping tt4's 8..13).

The value scale follows from the geometry: tt3 (z=13, scale -7) and tt4
(z=6, scale -14) share det, so `scale = z - 20` for anything cloned from tt4.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
import sys
from pathlib import Path

sys.path[:0] = ["/tmp/walle"]

ROOT = Path("/tmp/walle")
VERTEX = struct.Struct("<8I")
ROWS = range(17, 64)
COLUMN = 60
HEIGHT = 4096.0
X0, X1 = 512.0, 2560.0

# anchor y (pixels) -> ay = y*256 = 2^tz * odd
ANCHORS = {
    3: 511.96875,   # ay = 131064 = 8 * 16383
    4: 511.9375,    # ay = 131056 = 16 * 8191
    5: 511.875,     # ay = 131040 = 32 * 4095
    8: 511.0,       # ay = 130816 = 256 * 511
    9: 510.0,       # ay = 130560 = 512 * 255
}


def f32(value: float) -> int:
    word = struct.unpack("<I", struct.pack("<f", value))[0]
    if struct.unpack("<f", struct.pack("<I", word))[0] != value:
        raise ValueError(f"{value!r} is not exactly representable in f32")
    return word


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(4 * 1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def probe_words():
    words = [0x3F800000 + t for t in range(256)]
    words += [0x3F800000 + (t << 8) for t in range(1, 128)]
    return words


def interior_pixel(tile):
    return min(tile[0] * 32 + 16, 2046), max(tile[1] * 32 + 16, 516)


def check(anchor_y: float, want_tz: int) -> tuple[int, int, list[int]]:
    ay = anchor_y * 256.0
    if ay != int(ay):
        raise ValueError(f"anchor y {anchor_y} is not an integer subpixel")
    ay = int(ay)
    tz = (ay & -ay).bit_length() - 1
    if tz != want_tz:
        raise ValueError(f"ay={ay} has tz={tz}, wanted {want_tz}")
    d_os = []
    for ty in ROWS:
        disp = ty * 8192 - ay
        if disp == 0 or disp % (1 << tz):
            raise ValueError(f"row {ty}: disp={disp} not a clean 2^{tz} multiple")
        d_o = disp >> tz
        if d_o % 2 == 0:
            raise ValueError(f"row {ty}: d_o={d_o} is even (tz is wrong)")
        d_os.append(d_o)
    return ay, tz, d_os


def generate(out: Path, anchor_y: float, want_tz: int) -> dict:
    ay, tz, d_os = check(anchor_y, want_tz)
    if out.exists():
        raise FileExistsError(out)
    v0 = (f32(X0), f32(anchor_y))
    v1 = (f32(X1), f32(anchor_y))
    v2 = (f32(X1), f32(anchor_y + HEIGHT))

    vertices = bytearray()
    draws: list[dict] = []
    experiments: list[dict] = []
    for wi, word in enumerate(probe_words()):
        for ty in ROWS:
            px, py = interior_pixel((COLUMN, ty))
            record = len(draws)
            for vi, (vx, vy) in enumerate((v0, v1, v2)):
                val = word if vi == 2 else 0
                vertices.extend(VERTEX.pack(
                    vx, vy, 0, 0, val, val, 0,
                    0x3F800000 if vi == 2 else 0))
            experiments.append({
                "recordIndex": record, "inputOrdinal": record,
                "variant": "c-truthtable", "split": "discovery",
                "state": 0, "drawOrdinal": 0, "anchor": 0,
                "family": "word", "offset": wi, "word": word,
            })
            draws.append({
                "recordIndex": record, "targetIndex": 0,
                "targetRecordIndex": 0, "sampleRecordIndex": 0,
                "sampleOrdinal": 0, "patternIndex": record,
                "x": px, "y": py, "tileX": COLUMN, "tileY": ty,
            })

    out.mkdir(parents=True)
    vpath = out / "reveal-agx-setup-accumulator-vertices.bin"
    vpath.write_bytes(vertices)
    census = {"targetCount": 8, "patternCount": len(draws),
              "drawCount": len(draws),
              "coefficientTripleCount": len(draws) * 4}
    plan = {
        "schema": "walle-reveal-agx-setup-accumulator-plan-v1",
        "authority": {"opensReferencePixels": False,
                      "usesOutputFeedback": False,
                      "establishesCProductTruthTable": True},
        "target": {"width": 2048, "height": 2048},
        "vertexData": {
            "file": vpath.name, "bytes": len(vertices),
            "sha256": _sha256(vpath), "recordCount": len(draws),
            "verticesPerRecord": 3, "wordsPerVertex": 8,
            "layout": "positionXY,pad2,varyingRGBA; little-endian uint32",
        },
        "experiments": experiments, "draws": draws, "census": census,
    }
    ppath = out / "reveal-agx-setup-accumulator-plan.json"
    ppath.write_text(json.dumps(plan, indent=2, sort_keys=True) + "\n",
                     encoding="utf-8")
    info = {
        "anchorY": anchor_y, "anchorSubpixels": ay, "tz": tz,
        "valueScaleExponent": tz - 20,
        "dOddRange": [min(d_os), max(d_os)],
        "dOddBitLengths": [min(d.bit_length() for d in d_os),
                           max(d.bit_length() for d in d_os)],
        "rows": len(d_os), "words": len(probe_words()),
        "plan": {"file": ppath.name, "sha256": _sha256(ppath)},
        "vertexData": {"file": vpath.name, "sha256": _sha256(vpath)},
        "census": census,
    }
    (out / "manifest.json").write_text(
        json.dumps(info, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return info


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tz", type=int, required=True, choices=sorted(ANCHORS))
    ap.add_argument("--output", type=Path)
    a = ap.parse_args()
    out = a.output or (ROOT / "build" / "analysis-agx-basis"
                       / f"c-tzclass{a.tz}-plan-v1")
    info = generate(out, ANCHORS[a.tz], a.tz)
    print(json.dumps({k: info[k] for k in
                      ("anchorY", "anchorSubpixels", "tz",
                       "valueScaleExponent", "dOddRange", "dOddBitLengths",
                       "rows", "words")}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
