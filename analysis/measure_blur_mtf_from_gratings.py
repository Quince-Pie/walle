#!/usr/bin/env python3
"""Measure the backdrop blur's response ONE FREQUENCY AT A TIME.

Everything else has measured this kernel indirectly.  A step edge fixes its
integral; a smooth backdrop fixes its gain in whatever band that backdrop
happens to carry.  For `clear` the two agree and the shipped kernel is exact
everywhere.  For `regular` they do not: every step edge - a 500 px circle, a
frame-filling rectangle, a frame-filling circle - says the near layer carries
0.90 of the blur in light, and the coded field says 0.54, and no change of
geometry, padding, layer count, mixing order or affine reconciles them.

Reading the response off a smooth backdrop directly does not work either.  Its
energy is concentrated in a narrow band, so dividing output by input returns
gains above one - impossible for a blur - wherever there is nothing to divide
by.

The rig's sine gratings have no such problem.  Each is a single frequency at
full contrast, so the amplitude ratio between the element's interior and the
backdrop outside it IS the kernel's gain at that period, at high signal to
noise, with nothing to assume about the backdrop's spectrum.

Two corrections the ratio needs.  The material's TRANSFER scales the grating as
well as the kernel, so the ratio is divided by the transfer's local slope at
the interior's own mean - which is measured, not fitted, here.  And the
REFRACTION displaces the backdrop within 35.6 px of the rim, so the interior is
read well inside that.
"""

import argparse
import json
import re
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
GRATING = re.compile(r"^sine-x-p(\d{4})-ph(\d)$")
# Element radius in capture pixels at 2x -> how far in to read.  The refraction
# band is 35.6 px and the rim 2.2, so 60 px of margin is generous.
SCENES = {"circle-0500-center": 500.0, "circle-1000-center": 1000.0}
MARGIN = 60.0
SIGMAS = {"regular": (14.188, 329.807), "clear": (0.7251, 4.1829)}
WEIGHT = {("regular", "light"): 0.8846, ("regular", "dark"): 0.5164,
          ("clear", "light"): 0.2174, ("clear", "dark"): 0.2174}

type JsonObject = dict[str, object]


def slope_at(record: JsonObject, level: float) -> float:
    exponents = [tuple(t) for t in record["termExponents"]]
    coefficients = np.array(record["coefficients"])

    def evaluate(value: float) -> np.ndarray:
        unit = np.clip(value, 0.0, 255.0) / 255.0
        out = np.zeros(3)
        for (i, j, k), row in zip(exponents, coefficients):
            out += (unit ** (i + j + k)) * row
        return out * 255.0
    return float((evaluate(level + 0.5) - evaluate(level - 0.5)).mean())


def transfer_rows(record: JsonObject, gray: np.ndarray) -> np.ndarray:
    """The measured transfer applied to a gray scanline, as a gray scanline."""
    exponents = [tuple(t) for t in record["termExponents"]]
    coefficients = np.array(record["coefficients"])
    unit = np.clip(gray, 0.0, 255.0) / 255.0
    out = np.zeros((len(unit), 3))
    for (i, j, k), row in zip(exponents, coefficients):
        out += np.outer(unit ** (i + j + k), row)
    return np.clip(out * 255.0, 0.0, 255.0).mean(axis=1)


def amplitude(row: np.ndarray, period: float) -> float:
    """The grating's amplitude in one strip, by projection onto its own tone.

    Projection rather than peak-to-peak: it rejects everything that is not at
    the grating's frequency, which is what makes this work at low contrast.
    """
    x = np.arange(len(row), dtype=float)
    angle = 2.0 * np.pi * x / period
    centred = row - row.mean()
    return float(2.0 * np.hypot(centred @ np.cos(angle),
                                centred @ np.sin(angle)) / len(row))


def measure(shots: list[Path], scene: str, radius: float, period: int,
            variant: str, appearance: str, record: JsonObject
            ) -> JsonObject | None:
    name = (f"sine-x-p{period:04d}-ph0__{scene}__{variant}"
            f"__{appearance}.png")
    inside_path = next((d / name for d in shots if (d / name).exists()), None)
    plain = name.replace(f"__{variant}__", "__none__")
    outside_path = next((d / plain for d in shots if (d / plain).exists()), None)
    if inside_path is None or outside_path is None:
        return None

    element = np.asarray(Image.open(inside_path).convert("RGB")).astype(float)
    backdrop = np.asarray(Image.open(outside_path).convert("RGB")).astype(float)
    height, width, _ = element.shape
    half = int(radius - MARGIN)
    if half < period:
        return None
    rows = slice(height // 2 - 8, height // 2 + 8)
    columns = slice(width // 2 - half, width // 2 + half)

    interior = element[rows, columns].mean(axis=2).mean(axis=0)
    source = backdrop[rows, columns].mean(axis=2).mean(axis=0)
    if interior.min() <= 0.5 or interior.max() >= 254.5:
        return None
    inside = amplitude(interior, period)
    outside = amplitude(source, period)
    if outside < 4.0:
        return None

    # Dividing by the transfer's local SLOPE is not good enough for `regular`:
    # its transfer is strongly curved and a grating swings across a wide range
    # of it, so the linearisation biases the ratio and returns gains above one.
    # Forward-modelling the whole thing instead has no such problem - the
    # grating goes through the kernel and then through the measured transfer,
    # and the only free number left is the weight.
    frequency = 1.0 / period
    weight, (narrow, wide) = WEIGHT[(variant, appearance)], SIGMAS[variant]
    near = np.exp(-2.0 * np.pi**2 * narrow**2 * frequency**2)
    far = np.exp(-2.0 * np.pi**2 * wide**2 * frequency**2)
    level = float(source.mean())
    swing = source - level

    def predicted(w: float) -> np.ndarray:
        blurred = level + swing * (w * near + (1.0 - w) * far)
        return transfer_rows(record, blurred)

    def residual(w: float) -> float:
        return float(np.sqrt(((predicted(w) - interior)**2).mean()))

    best = min(((residual(w), w) for w in np.arange(0.05, 1.0001, 0.005)))
    model = weight * near + (1.0 - weight) * far
    return {
        "scene": scene, "period": period, "variant": variant,
        "appearance": appearance,
        "backdropAmplitudeCodes": round(outside, 3),
        "interiorAmplitudeCodes": round(inside, 4),
        "weightFromThisPeriod": round(float(best[1]), 4),
        "residualAtThisWeight": round(float(best[0]), 3),
        "residualAtShippedWeight": round(residual(weight), 3),
        "measuredGain": round(float(best[1] * near + (1.0 - best[1]) * far), 4),
        "twoGaussianGain": round(float(model), 4),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--shots", type=Path, nargs="+", required=True)
    parser.add_argument("--material", type=Path,
                        default=ROOT / "analysis/results/material_matrices.json")
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()

    material = json.loads(arguments.material.read_text(encoding="utf-8"))
    records = {(r["variant"], r["appearance"]): r for r in material["records"]}
    periods = sorted({int(match.group(1))
                      for directory in arguments.shots
                      for path in directory.glob("sine-x-p*-ph0__*.png")
                      if (match := GRATING.match(path.name.split("__")[0]))})
    results = [
        entry
        for scene, radius in SCENES.items()
        for variant in ("regular", "clear")
        for appearance in ("light", "dark")
        for period in periods
        if (entry := measure(arguments.shots, scene, radius, period, variant,
                             appearance, records[(variant, appearance)]))
        is not None
    ]
    if not results:
        print("  no gratings measurable in this corpus")
        return 1

    print(f"  {'scene':22s} {'material':16s} "
          + "".join(f"{p:>13d}" for p in periods) + "   period px")
    for scene in SCENES:
        for variant in ("regular", "clear"):
            for appearance in ("light", "dark"):
                row = {e["period"]: e for e in results
                       if e["scene"] == scene and e["variant"] == variant
                       and e["appearance"] == appearance}
                if not row:
                    continue
                print(f"  {scene:22s} {variant + '/' + appearance:16s} "
                      + "".join(
                          "            -" if p not in row
                          else f"{row[p]['weightFromThisPeriod']:6.3f}"
                               f"{row[p]['residualAtShippedWeight']:7.2f}"
                          for p in periods))
    print("\n  weight this period wants, and the residual at the SHIPPED"
          " weight in code values")
    if arguments.output is not None:
        arguments.output.write_text(
            json.dumps({"schemaVersion": 1, "osBuild": "25G76",
                        "classification": "blur response by frequency",
                        "records": results}, indent=2, sort_keys=True) + "\n",
            encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
