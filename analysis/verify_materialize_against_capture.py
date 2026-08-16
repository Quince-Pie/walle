#!/usr/bin/env python3
"""Render walle MID-MATERIALIZE and score it against the M1's own frames.

The materialize law was measured from those frames and then shipped, and a fit
is not the same claim as parity - the same gap the step-edge check closes for
the blur.  Two things in particular could be right in the fit and wrong in the
shader: the crossfade's form, which walle used to spread through three separate
lerps, and the ease, which is now per variant with a delay in front of it.

The geometry is arranged so that neither the rim nor the refraction is in
frame, because neither is what this measures.  walle's process capture puts its
centre at (512, 614.4) and its radius at 2164.1 * progress; at progress 1 the
element's rim is 2164 px from that centre and the frame's furthest corner is
2101, so every pixel is at least 63 px inside the boundary - beyond the 35.6 px
refraction band and far beyond the 2.2 px rim.  The material's clock is driven
separately, which is what leaves progress free to do that.

The backdrop is the capture's own first frame, which IS the sharp backdrop: at
clock zero the material contributes nothing.  It is placed in the middle of
walle's larger canvas and the border replicated outward, which is what both
pipelines do at a frame edge, so the convolution either one performs over the
compared region sees the same infinite field.
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
# walle's own materialize window: `time` runs 0..kFadeStart across it.
FADE_START = 0.66
# Apple's transition outlives the window the rig clocked - `end` is 1.02 to
# 1.035 - so walle plays the measured curve over its OWN materialize window and
# arrives by the end of it.  Comparing walle at its clock against the hardware
# at the RIG's clock therefore compares two different instants; the rig's clock
# has to be divided by `end` to name the same one.
MATERIALIZE = json.loads(
    (ROOT / "analysis/results/materialize_thickness.json").read_text(
        encoding="utf-8"))["variants"]
# Frames within this of a clock the harness already sampled, to keep the
# rendering count down while still covering the curve.
PROBE_CLOCKS = (0.15, 0.25, 0.35, 0.45, 0.55, 0.65, 0.75, 0.85, 0.95, 1.0)

type JsonObject = dict[str, object]


def scored_rows(sequence: JsonObject, height: int) -> slice:
    """Everything but the raster clock the rig draws into the frame.

    The materialize transition cannot be stepped, so the rig times it by
    rendering a clock into each frame and reading it back.  That band is the
    harness, not the material, and the manifest says where it is.
    """
    excluded = sequence.get("analysisExclusionPixels") or []
    top = max((int(r["y"]) + int(r["height"]) for r in excluded), default=0)
    return slice(top, height)


def canvas(field: np.ndarray, path: Path) -> tuple[int, int]:
    """The capture's backdrop in the middle of walle's canvas, edges copied."""
    height, width, _ = field.shape
    top, left = (CAPTURE_EXTENT - height) // 2, (CAPTURE_EXTENT - width) // 2
    padded = np.pad(field.astype(np.uint8),
                    ((top, CAPTURE_EXTENT - height - top),
                     (left, CAPTURE_EXTENT - width - left), (0, 0)), "edge")
    Image.fromarray(padded, "RGB").save(path)
    return top, left


def walle_clock(variant: str, clock: float) -> float:
    """The rig's clock, expressed on walle's own materialize window."""
    return clock / float(MATERIALIZE[variant]["endClock"])


def render(background: Path, variant: str, appearance: str, clock: float,
           work: Path) -> np.ndarray:
    out = work / f"walle-{background.stem}-{variant}-{appearance}-{clock:.4f}.bgra"
    environment = dict(os.environ)
    environment["APPEARANCE"] = appearance
    environment["TINT"] = "none"
    # Progress 1 puts the whole frame inside the element; the material's own
    # clock is the thing being swept.
    environment["PROGRESS"] = "1.0"
    environment["MATERIAL_PROGRESS"] = (
        f"{walle_clock(variant, clock) * FADE_START:.6f}")
    environment["BACKING_SCALE"] = "2"
    subprocess.run([str(RENDER), str(background), str(out), variant],
                   check=True, env=environment,
                   stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    raw = np.frombuffer(out.read_bytes(), dtype=np.uint8)
    return raw.reshape(CAPTURE_EXTENT, CAPTURE_EXTENT, 4)[:, :, [2, 1, 0]] \
        .astype(float)


def score(corpus: Path, sequence: JsonObject, work: Path) -> list[JsonObject]:
    frames = sequence["frames"]
    field = np.asarray(Image.open(corpus / str(frames[0]["file"]))
                       .convert("RGB")).astype(float)
    height, width, _ = field.shape
    source = work / f"{sequence['id']}.png"
    top, left = canvas(field, source)

    records = []
    for target in PROBE_CLOCKS:
        entry = min(frames,
                    key=lambda f: abs(float(f["presentationProgress"]) - target))
        clock = float(entry["presentationProgress"])
        if any(abs(clock - r["clock"]) < 1e-9 for r in records):
            continue
        reference = np.asarray(Image.open(corpus / str(entry["file"]))
                               .convert("RGB")).astype(float)
        rendered = render(source, sequence["overlay"], sequence["appearance"],
                          clock, work)[top:top + height, left:left + width]
        rows = scored_rows(sequence, height)
        delta = rendered[rows] - reference[rows]
        records.append({
            "clock": round(clock, 6),
            "frame": int(entry["index"]),
            "rootMeanSquareCodes": round(float(np.sqrt((delta**2).mean())), 3),
            "maximumCodes": round(float(np.abs(delta).max()), 3),
            "medianAbsoluteCodes": round(float(np.median(np.abs(delta))), 3),
        })
    return records


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()

    manifest = json.loads(
        (arguments.corpus / "manifest.json").read_text(encoding="utf-8"))
    report: JsonObject = {"schemaVersion": 1, "osBuild": manifest["osBuild"],
                          "classification": "walle mid-materialize", "records": []}
    worst = 0.0
    with tempfile.TemporaryDirectory() as name:
        work = Path(name)
        for sequence in manifest["dynamicSequences"]:
            if str(sequence["mode"]) != "materialize":
                continue
            records = score(arguments.corpus, sequence, work)
            if not records:
                continue
            report["records"].append({"id": sequence["id"],
                                      "frames": records})
            worst = max(worst, max(r["maximumCodes"] for r in records))
            print(f"  {sequence['id']:28s} "
                  + "  ".join(f"{r['clock']:.2f}:{r['rootMeanSquareCodes']:5.2f}"
                              for r in records))
            print(f"  {'':28s} worst "
                  + "  ".join(f"{r['clock']:.2f}:{r['maximumCodes']:5.1f}"
                              for r in records))
    if not report["records"]:
        print("  no materialize sequences in this corpus")
        return 1
    report["worstCodes"] = round(worst, 3)
    print(f"worst |walle - apple| = {worst:.2f} code values")
    if arguments.output is not None:
        arguments.output.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n",
            encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
