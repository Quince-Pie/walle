#!/usr/bin/env python3
"""Score walle against the M1 over a STRUCTURED background, not a flat one.

The flat-background check closes the loop on the colour transfer, and only on
that: over a flat field the blur is the identity and the refraction displaces
one constant into another, so neither can be wrong there.  Both are fitted from
captures and neither had ever been rendered back through walle's own pipeline
and compared - which is exactly the gap that check was built to catch for the
transfer.  The shader can carry a perfect kernel and still apply it in the wrong
space, at the wrong scale, or in the wrong order against the refraction.

A step edge through the element's centre is the whole edge-spread function on
one scanline, and the rim half of that scanline is where the refraction lives,
so one background exercises all three stages at once.  Flat backdrops are read
the same way and on the same scanline, which is the cleanest look at the EDGE:
the body is a constant there, so every code value that is not that constant
belongs to the rim or to the shadow outside it.

The two geometries LINE UP EXACTLY, which is what makes this a pixel comparison
rather than a resampling:

  * the capture's `circle-0500-center` is a 500 px radius centred at x = 512.0
    in a 1024 px frame, so the element covers columns 12 through 1011;
  * walle's process capture puts its centre at x = 512.0 with a radius of
    2164.1045 * progress, so progress = 500 / 2164.1045 puts the element on the
    same columns;
  * the harness's step falls at x = w/2, which is 512.0 - the element's centre -
    and walle's background is built with its step at 512.0 too.

The frames differ in size, and that does not matter here.  Both sides of the
step are constant out to their own frame edge and both pipelines replicate the
edge, so the effective infinite backdrop either convolution sees is identical.

The scanline is read as a band of rows about each centre.  Walle's centre row
sits at y = 614.4 and the capture's at 512.0, so the bands are not at the same
sub-pixel offset; over a 500 px radius that moves the rim by 0.02 px, which is
below what any of this resolves.
"""

import argparse
import json
import os
import subprocess
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
RENDER = ROOT / "analysis/render_walle_over_background.sh"
CAPTURE_EXTENT = 2048
CENTER_X, CENTER_Y = 512.0, 614.4
RADIUS_PER_PROGRESS = 2164.1045
# kFadeStart in the shader: thickness is pow(t / kFadeStart, ease) * (1 - tFade),
# which is exactly one here and nowhere else.
FULL_THICKNESS_PROGRESS = 0.66
# The capture side: scene name -> (frame extent, element radius), both in
# capture pixels at 2x backing scale.
SCENES = {"circle-0500-center": (1024, 500.0)}
ROW_HALF_BAND = 8
# name -> the two levels the backdrop takes left and right of the element's
# centre.  Equal levels make it flat, which is the cleanest read of the RIM:
# the body is a constant, so every code value that is not that constant belongs
# to the edge.
BACKDROPS = {
    "kstep-x-064-192": (64, 192),
    "kstep-x-000-255": (0, 255),
    "gray-000": (0, 0),
    "gray-128": (128, 128),
    "gray-192": (192, 192),
    "gray-255": (255, 255),
}

type JsonObject = dict[str, object]


def step_background(path: Path, low: int, high: int) -> None:
    """walle's backdrop: the same backdrop, with any step on the element's
    centre."""
    pixels = np.full((CAPTURE_EXTENT, CAPTURE_EXTENT, 3), high, dtype=np.uint8)
    pixels[:, :int(CENTER_X)] = low
    Image.fromarray(pixels, "RGB").save(path)


def render(background: Path, variant: str, appearance: str, radius: float,
           work: Path) -> np.ndarray:
    out = work / f"walle-{background.stem}-{variant}-{appearance}.bgra"
    environment = dict(os.environ)
    environment["APPEARANCE"] = appearance
    environment["TINT"] = "none"
    environment["PROGRESS"] = f"{radius / RADIUS_PER_PROGRESS:.10f}"
    # One progress normally drives both the radius and the material's clock,
    # which would make an element this small only part-way materialized: at the
    # progress that puts the rim at 500 px the material is 0.43 thick, and the
    # capture's is 1.  Driven apart, the geometry matches AND the material is
    # the one the capture shows.
    environment["MATERIAL_PROGRESS"] = f"{FULL_THICKNESS_PROGRESS}"
    # The material's radii are absolute in device pixels at a 2x backing scale,
    # which is what the corpus was captured at.  A capture at scale 1 applies
    # them at half the pixel width - correct for a 1x display, and half as
    # blurry as the frame being compared against.
    environment["BACKING_SCALE"] = "2"
    subprocess.run([str(RENDER), str(background), str(out), variant],
                   check=True, env=environment,
                   stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    raw = np.frombuffer(out.read_bytes(), dtype=np.uint8)
    frame = raw.reshape(CAPTURE_EXTENT, CAPTURE_EXTENT, 4)
    return frame[:, :, [2, 1, 0]].astype(float)


def band(pixels: np.ndarray, centre: float, left: int, right: int
         ) -> np.ndarray:
    row = int(centre)
    strip = pixels[row - ROW_HALF_BAND:row + ROW_HALF_BAND, left:right]
    return strip.mean(axis=0)


def compare(shots: list[Path], scene: str, background: str, variant: str,
            appearance: str, work: Path) -> JsonObject | None:
    name = f"{background}__{scene}__{variant}__{appearance}.png"
    capture = next((d / name for d in shots if (d / name).exists()), None)
    if capture is None:
        return None
    extent, radius = SCENES[scene]
    low, high = BACKDROPS[background]
    source = work / f"{background}.png"
    if not source.exists():
        step_background(source, low, high)

    apple = np.asarray(Image.open(capture).convert("RGB")).astype(float)
    if apple.shape[0] != extent:
        return None
    left, right = int(CENTER_X - radius), int(CENTER_X + radius)
    reference = band(apple, extent / 2.0, left, right)
    rendered = band(render(source, variant, appearance, radius, work),
                    CENTER_Y, left, right)

    delta = rendered - reference
    magnitude = np.abs(delta).max(axis=1)
    # The rim band is where the refraction acts; the middle is the blur and the
    # transfer.  Scored apart because they fail for different reasons.
    inside = np.arange(len(magnitude)) - (len(magnitude) - 1) / 2.0
    rim = np.abs(np.abs(inside) - radius) < 40.0
    core = np.abs(inside) < radius - 40.0
    return {
        "scene": scene,
        "background": background,
        "variant": variant,
        "appearance": appearance,
        "scoredColumns": int(len(magnitude)),
        "rootMeanSquareCodes": round(float(np.sqrt((delta**2).mean())), 3),
        "maximumCodes": round(float(magnitude.max()), 3),
        "rimMaximumCodes": round(float(magnitude[rim].max()) if rim.any()
                                 else 0.0, 3),
        "coreMaximumCodes": round(float(magnitude[core].max()) if core.any()
                                  else 0.0, 3),
        "worstColumn": int(np.argmax(magnitude)),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--shots", type=Path, nargs="+", required=True,
                        help="capture directories, searched in order")
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()

    with tempfile.TemporaryDirectory() as name:
        work = Path(name)
        records = [
            record
            for scene in SCENES
            for background in BACKDROPS
            for variant in ("regular", "clear")
            for appearance in ("light", "dark")
            if (record := compare(arguments.shots, scene, background, variant,
                                  appearance, work)) is not None
        ]
    if not records:
        print("  no structured backgrounds found in this corpus")
        return 1
    print(f"  {'background':18s} {'variant':8s} {'appear':6s} "
          f"{'rms':>7s} {'max':>7s} {'rim':>7s} {'core':>7s}")
    for record in records:
        print(f"  {record['background']:18s} {record['variant']:8s} "
              f"{record['appearance']:6s} "
              f"{record['rootMeanSquareCodes']:7.3f} "
              f"{record['maximumCodes']:7.3f} "
              f"{record['rimMaximumCodes']:7.3f} "
              f"{record['coreMaximumCodes']:7.3f}")
    worst = max(float(r["maximumCodes"]) for r in records)
    print(f"worst |walle - apple| = {worst:.2f} code values over "
          f"{len(records)} scanlines")
    if arguments.output is not None:
        arguments.output.write_text(
            json.dumps({"schemaVersion": 1, "osBuild": "25G76",
                        "classification": "walle over a step edge, end to end",
                        "worstCodes": round(worst, 3),
                        "records": records}, indent=2, sort_keys=True) + "\n",
            encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
