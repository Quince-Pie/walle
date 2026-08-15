#!/usr/bin/env python3
"""Measure the Liquid Glass element's refraction displacement, by phase.

walle carries a refraction band whose WIDTH was measured on this build - the
outer 0.25 R, from Apple's own inputOuterRefractionHeight - but whose
DISPLACEMENT PROFILE was never re-measured: its shape, its peak amplitude and
its dispersion were fitted from Human Interface Guidelines photographs, and the
shader says so.  This measures the real thing.

Method.  The capture rig draws four-step sine gratings: the same period at
phases 0, 1/4, 1/2 and 3/4.  At each pixel those four samples determine the
local phase of the grating, and the phase says where the material SAMPLED FROM:
a displacement of d pixels along the grating's axis shifts the phase by
2*pi*d/period.  Reading it along the element's horizontal diameter makes the
grating's axis and the circle's radial direction the same, so the number that
comes out is the radial displacement directly.

Two things this has to handle.  Clipping - `clear` passes white through at 255,
so the bright half of every grating pins, and a naive four-step decode reads
those pinned samples as real; here the three unknowns are fitted from whichever
samples are unclipped, needing three of the four.  And wrapping - a displacement
larger than half a period is indistinguishable from a smaller one, so the
periods are walked from longest to shortest, each one unwrapped against the
estimate the previous one gave.

The interior is the calibration: a material displaces nothing far from its own
rim, so the phase residual there is the grating's own zero and is subtracted.
"""

import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image

PERIODS = (1024, 512, 256, 128, 64)
CLIP_LOW, CLIP_HIGH = 0.5, 254.5
ROW_HALF_BAND = 6
# Scene name -> the element's half-width along the measured axis, in capture
# pixels at 2x backing scale.  A grating in x reads the horizontal half-extent,
# which for a circle is its radius and for a rect is half its width.
SCENES = {
    "circle-0128-center": 128.0,
    "circle-0256-center": 256.0,
    "circle-0500-center": 500.0,
    "circle-1000-center": 1000.0,
    "circle-1600-center": 1600.0,
    "rect-1600x0900-r000": 1600.0,
    "rect-1600x0900-r080": 1600.0,
    "rect-1600x0900-r240": 1600.0,
}

type JsonObject = dict[str, object]


def read_row(shots: Path, scene: str, period: int, phase: int, overlay: str,
             appearance: str) -> np.ndarray | None:
    path = (shots
            / f"sine-x-p{period:04d}-ph{phase}__{scene}__{overlay}__{appearance}.png")
    if not path.exists():
        return None
    pixels = np.asarray(Image.open(path).convert("RGB")).astype(float)
    height, _, _ = pixels.shape
    band = pixels[height // 2 - ROW_HALF_BAND:height // 2 + ROW_HALF_BAND]
    # The mean over channels is taken AFTER masking, so keep channels for now.
    return band.mean(axis=0)


def local_phase(samples: list[np.ndarray]) -> np.ndarray:
    """Grating phase per column, fitted from the unclipped samples only."""
    stacked = np.stack([sample.mean(axis=1) for sample in samples])
    valid = np.stack([
        ((sample > CLIP_LOW) & (sample < CLIP_HIGH)).all(axis=1)
        for sample in samples
    ])
    count = stacked.shape[1]
    phase = np.full(count, np.nan)
    angles = 2.0 * np.pi * np.arange(4) / 4.0
    for column in range(count):
        usable = valid[:, column]
        if usable.sum() < 3:
            continue
        design = np.column_stack([np.ones(int(usable.sum())),
                                  np.sin(angles[usable]),
                                  np.cos(angles[usable])])
        solution, *_ = np.linalg.lstsq(design, stacked[usable, column],
                                       rcond=None)
        # value = A + B sin(theta + k pi/2) expands to
        # A + (B cos theta) sin(k) + (B sin theta) cos(k)
        phase[column] = np.arctan2(solution[2], solution[1])
    return phase


def measure(shots: Path, scene: str, radius: float, overlay: str,
            appearance: str) -> JsonObject | None:
    columns = None
    displacement = None
    used = []
    for period in PERIODS:
        samples = [read_row(shots, scene, period, phase, overlay, appearance)
                   for phase in range(4)]
        if any(sample is None for sample in samples):
            continue
        phase = local_phase(samples)
        if columns is None:
            columns = np.arange(len(phase))
            displacement = np.zeros(len(phase))
        centre = len(phase) / 2.0
        distance = np.abs(columns - centre)
        # Far from the rim the material displaces nothing, so the residual
        # there is the grating's own zero.
        expected = 2.0 * np.pi * columns / period
        residual = np.angle(np.exp(1j * (phase - expected)))
        core = (distance < radius * 0.6) & np.isfinite(residual)
        if core.sum() < 64:
            continue
        zero = np.angle(np.exp(1j * residual[core]).mean())
        offset = np.angle(np.exp(1j * (residual - zero)))
        # Unwrap against what the previous, longer period already established.
        predicted = -displacement * 2.0 * np.pi / period
        turns = np.round((predicted - offset) / (2.0 * np.pi))
        refined = -(offset + 2.0 * np.pi * turns) * period / (2.0 * np.pi)
        finite = np.isfinite(refined)
        displacement = np.where(finite, refined, displacement)
        used.append(period)

    if displacement is None or not used:
        return None
    centre = len(displacement) / 2.0
    signed = columns - centre
    distance = np.abs(signed)
    # Positive radial displacement means the material sampled OUTWARD of the
    # pixel; negative means it reached inward, which magnifies the edge.
    radial = displacement * np.sign(signed)
    # Binned by the FRACTION of the half-extent, so profiles from elements of
    # different sizes are directly comparable.
    profile = []
    step = 0.01
    fraction = 0.80
    while fraction < 1.0:
        band = ((distance >= radius * fraction)
                & (distance < radius * (fraction + step))
                & np.isfinite(radial))
        if band.sum() >= 4:
            profile.append({
                "fractionLow": round(fraction, 4),
                "displacementPixels": round(float(np.median(radial[band])), 3),
                "displacementFraction": round(
                    float(np.median(radial[band])) / radius, 6),
                "spreadPixels": round(float(np.std(radial[band])), 3),
                "sampleCount": int(band.sum()),
            })
        fraction += step
    return {
        "scene": scene,
        "overlay": overlay,
        "appearance": appearance,
        "periodsUsed": used,
        "elementRadiusPixels": radius,
        "profile": profile,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--shots", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()

    records = [
        record
        for scene, radius in SCENES.items()
        for overlay in ("clear", "regular")
        for appearance in ("light", "dark")
        if (record := measure(arguments.shots, scene, radius, overlay, appearance))
        is not None
    ]
    for record in records:
        tail = record["profile"][-6:]
        print(f"  {record['scene']:22s} {record['overlay']:8s} "
              f"{record['appearance']:5s} R={record['elementRadiusPixels']:6.0f}  "
              + "  ".join(f"{b['fractionLow']:.2f}:{b['displacementFraction']:+.4f}R"
                          for b in tail))
    if arguments.output is not None:
        arguments.output.write_text(
            json.dumps({"schemaVersion": 1, "osBuild": "25G76",
                        "records": records}, indent=2, sort_keys=True) + "\n",
            encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
