#!/usr/bin/env python3
"""Campaign 1 completion: Apple's exact output behavior on FLAT fields.

For every tone-family static shot: is the settled material interior a single
code (deterministic rounding, no dither), and what value?  Emits the
level->output table per variant/appearance - the exact samples that pin the
rounding rule and calibrate the transfer laws at every gray level at once.

Usage: measure_flat_field_rounding.py --capture <lgcap-static dir>
"""
import argparse
import collections
import json
from pathlib import Path

import numpy as np
from PIL import Image


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--capture", required=True)
    parser.add_argument("--out")
    args = parser.parse_args()
    shots = Path(args.capture) / "shots"

    rows = []
    for path in sorted(shots.glob("gray-*__*__*__*.png")):
        parts = path.stem.split("__")
        if len(parts) != 4:
            continue
        background, element, overlay, appearance = parts
        pixels = np.asarray(Image.open(path).convert("RGB")).astype(np.int32)
        h, w, _ = pixels.shape
        cy, cx = h // 2, w // 2
        interior = pixels[cy - 150:cy + 150, cx - 150:cx + 150, :]
        counts = collections.Counter(map(tuple, interior.reshape(-1, 3)))
        top = counts.most_common(3)
        total = interior.shape[0] * interior.shape[1]
        dominant, dominant_n = top[0]
        rows.append({
            "background": background,
            "overlay": overlay,
            "appearance": appearance,
            "dominantRGB": [int(v) for v in dominant],
            "dominantFraction": round(dominant_n / total, 5),
            "distinctColors": len(counts),
            "runnersUp": [[[int(v) for v in c], round(n / total, 5)] for c, n in top[1:]],
        })

    for r in rows:
        print(json.dumps(r))
    if args.out:
        Path(args.out).write_text(json.dumps(rows, indent=1))


if __name__ == "__main__":
    main()
