#!/usr/bin/env python3
"""Measure the Liquid Glass element's refraction displacement, by phase.

walle's refraction was fitted from Human Interface Guidelines photographs, and
the shader said so.  This measures the real thing, and the answer is that the
band is ABSOLUTE: binned by DISTANCE INSIDE THE RIM rather than by fraction of
the radius, elements of radius 128, 256, 500 and 1000 capture pixels all give
the same curve, spread 1.87 px across an eightfold size range and both
variants.  Reading it as a fraction of the radius - which one element cannot
distinguish - is what made the first measurement wrong everywhere else.

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

Read `regular` in DARK with suspicion.  That material's transfer has a gain of
0.31, so it compresses the grating threefold before the phase is decoded, and
its rows wander by up to 9 px where every other combination agrees to under 2.
`clear` at the same size and appearance is exact, so the wander is the decode's,
not the material's.
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
    # Binned by ABSOLUTE distance inside the rim, which is the variable the
    # profile actually depends on - see the module docstring.
    profile = []
    for inside in range(1, 46):
        band = (np.abs(distance - (radius - inside)) < 0.75) & np.isfinite(radial)
        if band.sum() >= 2:
            profile.append({
                "pixelsInsideRim": inside,
                "displacementPixels": round(float(np.median(radial[band])), 3),
                "sampleCount": int(band.sum()),
            })
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
    parser.add_argument("--shots", type=Path, nargs="+", required=True,
                        help="one directory per element size")
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()

    records = [
        record
        for shots in arguments.shots
        for scene, radius in SCENES.items()
        for overlay in ("clear", "regular")
        for appearance in ("light", "dark")
        if (record := measure(shots, scene, radius, overlay, appearance))
        is not None
    ]
    probe = (3, 6, 10, 15, 20, 25, 30, 35)
    print(f"  {'scene':22s} {'variant':8s} {'appear':6s} {'R':>6}  "
          + "".join(f"{u:>8}" for u in probe) + "   px inside the rim")
    for record in records:
        table = {b["pixelsInsideRim"]: b["displacementPixels"]
                 for b in record["profile"]}
        print(f"  {record['scene']:22s} {record['overlay']:8s} "
              f"{record['appearance']:6s} {record['elementRadiusPixels']:6.0f}  "
              + "".join(f"{table.get(u, float('nan')):8.2f}" for u in probe))
    # The fitted law, for comparison, in the same units.
    amplitude, width, power, offset = 26.48219, 35.5796, 1.09134, 12.6207
    print(f"  {'fitted law':22s} {'':8s} {'':6s} {'':6s}  "
          + "".join(f"{amplitude * max(width - u, 0.0) ** power / (u + offset):8.2f}"
                    for u in probe))
    if arguments.output is not None:
        arguments.output.write_text(
            json.dumps({"schemaVersion": 1, "osBuild": "25G76",
                        "records": records}, indent=2, sort_keys=True) + "\n",
            encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
