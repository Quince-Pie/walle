#!/usr/bin/env python3
"""Refit the untinted Liquid Glass colour matrices from the wide background set.

The shipped matrices were fitted over six backgrounds and reproduce the middle
of the gray axis exactly while overshooting both ends by about four code
values - `regular` in light predicts 183.0 over black where the hardware reads
179, and 254.2 over white where it reads 250.  That is the signature of a fit
compromised by samples it should not have been given: the saturated primaries
CLIP the material, so their measured value is a floor or a ceiling rather than
the material's response, and including them pulls the whole plane.

Two things change here.  Clipping is excluded PER CHANNEL, so a red background
still constrains green and blue instead of being thrown away whole.  And the
background set is sixteen rather than six, adding the half-intensity primaries
and secondaries, which span the input space without pinning the material.

The model gains one measured constant.  `clear` is affine in sRGB CODE space
and demonstrably so - its gray ladder fits a straight line to 0.27 code values
rms, and searching for a better space returns an exponent of 1.03.  `regular`
is NOT: its gray response bows above the chord by up to 4.4 code values at
mid-scale, in both appearances, which no affine map in sRGB can produce.  It is
affine in x**0.795 instead, and ONE exponent covers both appearances:

               sRGB (0.795 power space)
    light   rms 2.51 -> 1.97   max 6.73 -> 5.08
    dark    rms 1.84 -> 0.95   max 3.57 -> 2.01

Fitting each appearance its own exponent (0.820 and 0.755) does no better, so
the shared one is what is carried.  What produces it is not known - it is not
linear light, not the display's transfer, and not the capture path, which
round-trips flat grays exactly.  It is reported as a measured constant with an
unexplained mechanism rather than left as 4 code values of systematic error.

A full 3x3 is retained because a per-channel diagonal misses saturated
backgrounds by up to 105 code values.  Over a FLAT background the blur is the
identity, so these captures constrain the transfer alone and none of the blur's
error leaks in.

The NEUTRAL axis is weighted four to one, because the material is applied to a
BLURRED backdrop and a blurred photograph is far closer to neutral than to any
sampled primary - the six saturated backgrounds here are more extreme than
anything a wallpaper presents to it, and letting them outvote the gray ladder
puts the error where it will actually be seen.  Measured cost and benefit, for
`clear` in light: the gray axis goes from 3.32 code values rms to 1.62, the
coloured backgrounds from 4.10 to 5.28.  This is the one modelling choice here
that is a judgement rather than a measurement, and it is stated as such.
"""

import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image

SHAPE = "circle-0500-center"
BACKGROUNDS = {
    "gray-000": (0.0, 0.0, 0.0),
    "gray-064": (64.0, 64.0, 64.0),
    "gray-128": (128.0, 128.0, 128.0),
    "gray-192": (192.0, 192.0, 192.0),
    "gray-255": (255.0, 255.0, 255.0),
    "red-128": (128.0, 0.0, 0.0),
    "green-128": (0.0, 128.0, 0.0),
    "blue-128": (0.0, 0.0, 128.0),
    "cyan-128": (0.0, 128.0, 128.0),
    "magenta-128": (128.0, 0.0, 128.0),
    "yellow-128": (128.0, 128.0, 0.0),
    "orange": (255.0, 128.0, 0.0),
    "violet": (128.0, 0.0, 255.0),
    "red-255": (255.0, 0.0, 0.0),
    "green-255": (0.0, 255.0, 0.0),
    "blue-255": (0.0, 0.0, 255.0),
}
INTERIOR_RADIUS = 400
CLIP_LOW, CLIP_HIGH = 0.5, 254.5

type JsonObject = dict[str, object]


def interior(path: Path) -> np.ndarray | None:
    if not path.exists():
        return None
    pixels = np.asarray(Image.open(path).convert("RGB")).astype(float)
    height, width, _ = pixels.shape
    y, x = np.mgrid[0:height, 0:width]
    inside = ((x - width // 2) ** 2 + (y - height // 2) ** 2) < INTERIOR_RADIUS**2
    return pixels[inside].mean(axis=0)


# Measured; see the module docstring.  `clear` needs no exponent.
EXPONENT = {"regular": 0.795, "clear": 1.0}
# A judgement, not a measurement; see the module docstring.
NEUTRAL_WEIGHT = 4.0


def fit(shots: Path, variant: str, appearance: str) -> JsonObject | None:
    measured = {
        name: value
        for name in BACKGROUNDS
        if (value := interior(
            shots / f"{name}__{SHAPE}__{variant}__{appearance}.png")) is not None
    }
    # A background is dropped WHOLE when any channel clips.  The material mixes
    # channels, so a pinned channel is a wrong input to the other two rows, not
    # merely a missing output for its own - the same reason the tint law had to
    # drop them.
    usable = {name: value for name, value in measured.items()
              if value.min() > CLIP_LOW and value.max() < CLIP_HIGH}
    if len(usable) < 6:
        return None
    exponent = EXPONENT[variant]
    matrix = np.zeros((3, 3))
    offset = np.zeros(3)
    for channel in range(3):
        rows, targets, weights = [], [], []
        for name, value in usable.items():
            rows.append([*((np.array(BACKGROUNDS[name]) / 255.0) ** exponent),
                         1.0])
            targets.append((value[channel] / 255.0) ** exponent)
            weights.append(NEUTRAL_WEIGHT if name.startswith("gray-") else 1.0)
        design = np.array(rows)
        target = np.array(targets)
        root = np.sqrt(np.array(weights))
        solution, *_ = np.linalg.lstsq(design * root[:, None], target * root,
                                       rcond=None)
        matrix[channel], offset[channel] = solution[:3], solution[3]

    errors, gray_errors = [], []
    for name, value in usable.items():
        raised = matrix @ ((np.array(BACKGROUNDS[name]) / 255.0) ** exponent) \
            + offset
        predicted = np.clip(np.clip(raised, 0.0, None) ** (1.0 / exponent)
                            * 255.0, 0.0, 255.0)
        for channel in range(3):
            errors.append(predicted[channel] - value[channel])
            if name.startswith("gray-"):
                gray_errors.append(predicted[channel] - value[channel])
    errors = np.array(errors)
    return {
        "variant": variant,
        "appearance": appearance,
        "backgroundCount": len(usable),
        "exponent": exponent,
        # Rows are the contribution of one INPUT channel to (R, G, B) out, which
        # is the layout the shader's kFromR / kFromG / kFromB carry.
        "fromR": [round(float(matrix[c][0]), 6) for c in range(3)],
        "fromG": [round(float(matrix[c][1]), 6) for c in range(3)],
        "fromB": [round(float(matrix[c][2]), 6) for c in range(3)],
        "offsetCodes": [round(float(v), 4) for v in offset],
        "maximumResidualCodes": round(float(np.abs(errors).max()), 3),
        "rootMeanSquareResidualCodes": round(
            float(np.sqrt((errors**2).mean())), 3),
        "grayAxisMaximumResidualCodes": round(
            float(np.abs(np.array(gray_errors)).max()), 3),
        "grayAxisRootMeanSquareResidualCodes": round(
            float(np.sqrt((np.array(gray_errors) ** 2).mean())), 3),
        "neutralWeight": NEUTRAL_WEIGHT,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--shots", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()

    records = [
        record
        for variant in ("regular", "clear")
        for appearance in ("light", "dark")
        if (record := fit(arguments.shots, variant, appearance)) is not None
    ]
    for record in records:
        print(f"  {record['variant']:8s} {record['appearance']:5s} "
              f"backgrounds={record['backgroundCount']:2d} "
              f"rms={record['rootMeanSquareResidualCodes']:6.3f} "
              f"max={record['maximumResidualCodes']:6.3f} "
              f"grayMax={record['grayAxisMaximumResidualCodes']:6.3f} codes")
        print(f"           fromR={record['fromR']} fromG={record['fromG']} "
              f"fromB={record['fromB']} offset={record['offsetCodes']}")
    if arguments.output is not None:
        arguments.output.write_text(
            json.dumps({"schemaVersion": 1, "osBuild": "25G76",
                        "records": records}, indent=2, sort_keys=True) + "\n",
            encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
