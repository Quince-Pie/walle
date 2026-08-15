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

SHAPE = "circle-0500-center"
PERIODS = (1024, 512, 256, 128, 64)
CLIP_LOW, CLIP_HIGH = 0.5, 254.5
# The element is a 500 pt circle at 2x backing scale.
ELEMENT_RADIUS = 500.0
ROW_HALF_BAND = 6

type JsonObject = dict[str, object]


def read_row(shots: Path, period: int, phase: int, overlay: str,
             appearance: str) -> np.ndarray | None:
    path = shots / f"sine-x-p{period:04d}-ph{phase}__{SHAPE}__{overlay}__{appearance}.png"
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


def measure(shots: Path, overlay: str, appearance: str) -> JsonObject | None:
    columns = None
    displacement = None
    used = []
    for period in PERIODS:
        samples = [read_row(shots, period, phase, overlay, appearance)
                   for phase in range(4)]
        if any(sample is None for sample in samples):
            continue
        phase = local_phase(samples)
        if columns is None:
            columns = np.arange(len(phase))
            displacement = np.zeros(len(phase))
        centre = len(phase) / 2.0
        radius = np.abs(columns - centre)
        # Far from the rim the material displaces nothing, so the residual
        # there is the grating's own zero.
        expected = 2.0 * np.pi * columns / period
        residual = np.angle(np.exp(1j * (phase - expected)))
        core = (radius < ELEMENT_RADIUS * 0.6) & np.isfinite(residual)
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
    radius = np.abs(signed)
    # Positive radial displacement means the material sampled OUTWARD of the
    # pixel; negative means it reached inward, which magnifies the edge.
    radial = displacement * np.sign(signed)
    profile = []
    for low in range(0, int(ELEMENT_RADIUS), 25):
        band = (radius >= low) & (radius < low + 25) & np.isfinite(radial)
        if band.sum() < 8:
            continue
        profile.append({
            "radiusLow": low,
            "radiusHigh": low + 25,
            "radialDisplacementPixels": round(float(np.median(radial[band])), 3),
            "spreadPixels": round(float(np.std(radial[band])), 3),
            "sampleCount": int(band.sum()),
        })
    return {
        "overlay": overlay,
        "appearance": appearance,
        "periodsUsed": used,
        "elementRadiusPixels": ELEMENT_RADIUS,
        "profile": profile,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--shots", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()

    records = [
        record
        for overlay in ("clear", "regular")
        for appearance in ("light", "dark")
        if (record := measure(arguments.shots, overlay, appearance)) is not None
    ]
    for record in records:
        print(f"  {record['overlay']:8s} {record['appearance']:5s} "
              f"periods {record['periodsUsed']}")
        for band in record["profile"]:
            print(f"      r {band['radiusLow']:3d}..{band['radiusHigh']:3d}  "
                  f"displacement {band['radialDisplacementPixels']:+8.3f} px  "
                  f"spread {band['spreadPixels']:7.3f}")
    if arguments.output is not None:
        arguments.output.write_text(
            json.dumps({"schemaVersion": 1, "osBuild": "25G76",
                        "records": records}, indent=2, sort_keys=True) + "\n",
            encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
