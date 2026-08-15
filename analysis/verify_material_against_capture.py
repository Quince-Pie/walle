#!/usr/bin/env python3
"""Render walle's material and score it against the M1 captures.

Every material law in this repo is fitted from captures, and a fit residual is
not the same claim as parity: the shader can carry a perfect law and still be
wrong about the space it applies it in, the order of its stages, or which
appearance it selected.  So this closes the loop the only way that counts -
render walle over the SAME background, read its interior the SAME way, and
report the difference in code values.

walle's process capture puts the element at (512, 614.4) with a radius of
2164.1045 * progress, so at the default progress the interior around the centre
is far inside both the rim and the 0.25 R refraction band.  The capture's
element is a 500 pt circle at 2x; both are read as a disc mean, and since both
materials are measured to be spatially flat over a flat background the two
discs are directly comparable despite the different geometry.
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
SHAPE = "circle-0500-center"
# walle's reveal-capture geometry, from walle.c.
CAPTURE_EXTENT = 2048
CENTER = (512.0, 614.4)
INTERIOR_RADIUS = 300
# The capture harness's own element, at 2x backing scale.
APPLE_INTERIOR_RADIUS = 400

type JsonObject = dict[str, object]


def apple_interior(shots: Path, background: str, overlay: str,
                   appearance: str) -> np.ndarray | None:
    path = shots / f"{background}__{SHAPE}__{overlay}__{appearance}.png"
    if not path.exists():
        return None
    pixels = np.asarray(Image.open(path).convert("RGB")).astype(float)
    height, width, _ = pixels.shape
    y, x = np.mgrid[0:height, 0:width]
    inside = ((x - width // 2) ** 2 + (y - height // 2) ** 2) < APPLE_INTERIOR_RADIUS**2
    return pixels[inside].mean(axis=0)


def walle_interior(path: Path) -> np.ndarray:
    raw = np.fromfile(path, dtype=np.uint8)
    frame = raw.reshape(CAPTURE_EXTENT, CAPTURE_EXTENT, 4)[..., [2, 1, 0]]
    y, x = np.mgrid[0:CAPTURE_EXTENT, 0:CAPTURE_EXTENT]
    inside = ((x - CENTER[0]) ** 2 + (y - CENTER[1]) ** 2) < INTERIOR_RADIUS**2
    return frame[inside].astype(float).mean(axis=0)


def render(background: Path, variant: str, appearance: str, tint: str,
           work: Path) -> np.ndarray:
    output = work / f"{background.stem}-{variant}-{appearance}-{tint}.bgra"
    environment = dict(os.environ, APPEARANCE=appearance, TINT=tint)
    subprocess.run([str(RENDER), str(background), str(output), variant],
                   check=True, env=environment, capture_output=True)
    return walle_interior(output)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--shots", type=Path, required=True)
    parser.add_argument("--cases", type=Path, required=True,
                        help="JSON list of {background, overlay, variant, tint}")
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()

    cases = json.loads(arguments.cases.read_text(encoding="utf-8"))
    records: list[JsonObject] = []
    with tempfile.TemporaryDirectory() as directory:
        work = Path(directory)
        for case in cases:
            level = case["backgroundLevel"]
            background = work / f"bg-{case['background']}.png"
            if not background.exists():
                Image.new("RGB", (2048, 2048), tuple(level)).save(background)
            for appearance in ("light", "dark"):
                expected = apple_interior(arguments.shots, case["background"],
                                          case["overlay"], appearance)
                if expected is None:
                    continue
                measured = render(background, case["variant"], appearance,
                                  case.get("tint", "none"), work)
                delta = measured - expected
                records.append({
                    "background": case["background"],
                    "overlay": case["overlay"],
                    "variant": case["variant"],
                    "tint": case.get("tint", "none"),
                    "appearance": appearance,
                    "apple": [round(float(v), 2) for v in expected],
                    "walle": [round(float(v), 2) for v in measured],
                    "maximumDeltaCodes": round(float(np.abs(delta).max()), 3),
                })
                print(f"  {case['variant']:8s} {appearance:5s} "
                      f"{case['overlay']:14s} {case['background']:10s} "
                      f"apple {np.round(expected, 1)} walle {np.round(measured, 1)} "
                      f"max {records[-1]['maximumDeltaCodes']:6.2f}")

    worst = max((r["maximumDeltaCodes"] for r in records), default=0.0)
    report = {
        "schemaVersion": 1,
        "classification": "walle's material rendered and scored against the M1",
        "osBuild": "25G76",
        "worstDeltaCodes": round(float(worst), 3),
        "records": records,
    }
    if arguments.output is not None:
        arguments.output.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"worst |walle - apple| = {worst:.2f} code values over {len(records)} cases")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
