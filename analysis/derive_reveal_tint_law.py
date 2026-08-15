#!/usr/bin/env python3
"""Derive Apple's Liquid Glass `.tint()` law from M1 captures.

Why this exists: `shaders/frag.glsl` records that on macOS 26.4 (25E246)
`.tint()` measured hue-free - blue and orange produced identical pixels in
both appearances - and it was therefore deliberately not modelled.  That is
no longer true.  Re-measured on macOS 26.6.1 (25G76) with the same capture
harness, blue and orange tints differ across the entire element: over a
gray-128 background the interior reads (0, 129, 249) for `.tint(.blue)` and
(255, 123, 0) for `.tint(.orange)`, and the differing region is 787,030 px,
which is the area of the 500 pt circle at 2x scale.  Apple changed tint
between those builds, so walle's material model is no longer 1:1.

What the captures establish, per appearance:

  * the tinted interior is FLAT (per-pixel std 0 over a flat background), so
    like `regular` and `clear` there is no spatial transmission of backdrop
    detail - only the mega-blurred backdrop contributes;
  * the interior still depends on the background, weakly and linearly, so
    tint is a base colour plus a low-transmission term rather than an opaque
    replacement;
  * six backgrounds (three grays plus the R/G/B primaries) span the input
    space, so the 3x3 transmission matrix and the base colour solve exactly.

The model fitted here is

    out = clamp(base + T @ background)

with `base` the interior over black.  Light-appearance residuals are larger
than dark because the blue channel saturates at 255 over bright backgrounds,
which breaks linearity; the dark fit, where nothing clips in G, lands at
0.57 code values.

This derives the law for ONE tint colour (`.blue`).  Generalising to an
arbitrary tint colour needs captures at more tint colours than the harness
currently hardcodes (tintedBlue / tintedOrange / clearTintedBlue).

LARGER FINDING - the untinted materials changed too.  The same sweep, read
off the gray backgrounds (interior mean, all channels equal):

    material          bg 0    bg 128   bg 255
    clear   light       19      152      255
    clear   dark        19      152      255
    regular light      179      219      250
    regular dark        15       60       94

`shaders/frag.glsl` records 26.4 as: regular is a fully opaque platter,
sRGB 0.980 light / 0.078 dark, "transmission MTF 0.0000 even 3 px inside
the rim" for every background gray; clear is 0.494*sat(blur) + ADD with
ADD 0.267 / sat 1.102 (light) and 0.016 / 0.85 (dark), mapping white to
0.761.  Neither holds on 26.6.1: regular now transmits about 28% (light)
and 31% (dark), and clear passes white through at 255 while measuring
IDENTICALLY in both appearances.  The shipped material constants are
therefore stale against this machine, and re-deriving them from these
captures is a prerequisite for material parity - a larger gap than tint.

Note the linear base+transmission model fitted below suits the tints (0.57
to 6.8 code residual) but NOT regular/clear (17 to 18.5), whose measured
law applies a saturation matrix and clamps in sRGB; over the saturated
primaries that clipping dominates.  Read their gray-sweep samples directly
rather than the fitted coefficients.
"""

import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SHOTS = ROOT / "artifacts-tint/shots"
SHAPE = "circle-0500-center"
# Background name -> its sRGB code triple, as rendered by the harness.
BACKGROUNDS = {
    "gray-000": (0, 0, 0),
    "gray-128": (128, 128, 128),
    "gray-255": (255, 255, 255),
    "red-255": (255, 0, 0),
    "green-255": (0, 255, 0),
    "blue-255": (0, 0, 255),
}
# Well inside the rim of the 500 pt circle at 2x, so no edge pixels leak in.
INTERIOR_RADIUS = 400

type JsonObject = dict[str, object]


def interior_mean(path: Path) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    pixels = np.asarray(Image.open(path).convert("RGB")).astype(float)
    height, width, _ = pixels.shape
    y, x = np.mgrid[0:height, 0:width]
    inside = ((x - width // 2) ** 2 + (y - height // 2) ** 2) < INTERIOR_RADIUS**2
    disc = pixels[inside]
    return disc.mean(axis=0), disc.std(axis=0), pixels[0, 0]


def solve(shots: Path, overlay: str, appearance: str) -> JsonObject | None:
    inputs: list[list[float]] = []
    outputs: list[np.ndarray] = []
    samples: list[JsonObject] = []
    for name, code in BACKGROUNDS.items():
        path = shots / f"{name}__{SHAPE}__{overlay}__{appearance}.png"
        if not path.exists():
            continue
        mean, deviation, background = interior_mean(path)
        inputs.append([*code, 1.0])
        outputs.append(mean)
        samples.append(
            {
                "background": name,
                "backgroundCode": list(code),
                "capturedBackgroundPixel": [int(v) for v in background],
                "interiorMean": [round(float(v), 3) for v in mean],
                "interiorStd": [round(float(v), 3) for v in deviation],
            }
        )
    if len(inputs) < 4:
        return None
    matrix = np.array(inputs)
    measured = np.array(outputs)
    solution, *_ = np.linalg.lstsq(matrix, measured, rcond=None)
    residual = np.abs(matrix @ solution - measured)
    return {
        "overlay": overlay,
        "appearance": appearance,
        "sampleCount": len(inputs),
        # transmission[i][j]: contribution of input channel i to output j.
        "transmission": [[round(float(v), 6) for v in row] for row in solution[:3]],
        "base": [round(float(v), 4) for v in solution[3]],
        "maximumResidualCodes": round(float(residual.max()), 4),
        "meanResidualCodes": round(float(residual.mean()), 4),
        "samples": samples,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--shots", type=Path, default=DEFAULT_SHOTS)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()

    fits = [
        fit
        for overlay in ("tintedBlue", "tintedOrange", "regular", "clear")
        for appearance in ("light", "dark")
        if (fit := solve(arguments.shots, overlay, appearance)) is not None
    ]
    report = {
        "schemaVersion": 1,
        "classification": "Apple Liquid Glass tint law re-measured on macOS 26.6.1",
        "osBuild": "25G76",
        "priorMeasurement": {
            "osBuild": "25E246",
            "finding": "tint measured hue-free; blue and orange identical",
            "stillHolds": False,
        },
        "model": "out = clamp(base + transmission @ background)",
        "generalTintColourLawEstablished": False,
        "fits": fits,
    }
    encoded = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if arguments.output is not None:
        arguments.output.write_text(encoded, encoding="utf-8")
    for fit in fits:
        print(
            f"{fit['overlay']:13s} {fit['appearance']:5s} "
            f"base={fit['base']} maxResidual={fit['maximumResidualCodes']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
