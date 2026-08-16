#!/usr/bin/env python3
"""Measure how Liquid Glass MATERIALIZES, for every material.

Everything else in this repo is measured at full thickness.  The curve that
gets there - walle's pow(clock, 2.36) - was fitted from twelve frames of ONE
material, `clear` in light, and the claim that the blur radius ramps with it
came from reading Apple's transition inputs rather than from a rendered frame.
Three of the four materials had never been seen mid-materialize at all.

The rig's materialize element is 1000 points wide in a 512 point window, so it
covers the frame completely: there is no rim, no shadow and no geometry in
these captures, only the material thickening over a structured backdrop.  That
makes the question a clean one.

TWO MODELS, and the coded field separates them.  Both agree at the endpoints:

  * a CROSSFADE - out(k) = lerp(backdrop, material, k) - keeps every spatial
    frequency and fades its contrast uniformly;
  * a RAMP - the blur radius itself growing with k - removes high frequencies
    progressively, so a pixel in fine detail reaches the material sooner than
    a pixel on a plateau.

So the per-pixel alpha, (frame - backdrop) / (final - backdrop), is the test.
Under a crossfade it is one number for the whole frame; under a ramp it varies
with local detail.  Its SPREAD across pixels is the discriminator, and its
median against the frame's own decoded clock is the thickness curve.

The clock is read from the manifest, not from the frame index: each frame
carries a raster clock rendered into it, so a host that misses a requested
instant still produces a usable sample - the frame says when it is.
"""

import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image

type JsonObject = dict[str, object]
# Pixels whose endpoints are too close together cannot report an alpha: the
# ratio's denominator is the whole signal, and below a few code values it is
# noise.  Also drop anything that clips at either end.
MINIMUM_SPAN = 24.0
CLIP_LOW, CLIP_HIGH = 0.5, 254.5


def read(root: Path, entry: JsonObject) -> np.ndarray:
    return np.asarray(
        Image.open(root / str(entry["file"])).convert("RGB")).astype(float)


def measure(root: Path, sequence: JsonObject) -> JsonObject | None:
    frames = sequence["frames"]
    if len(frames) < 8:
        return None
    backdrop = read(root, frames[0])
    final = read(root, frames[-1])
    span = final - backdrop
    usable = ((np.abs(span) > MINIMUM_SPAN)
              & (backdrop > CLIP_LOW) & (backdrop < CLIP_HIGH)
              & (final > CLIP_LOW) & (final < CLIP_HIGH))
    if usable.sum() < 1000:
        return None

    # One alpha per frame by least squares over every usable pixel, rather
    # than a median of per-pixel ratios: the ratios are quantised by the 8-bit
    # output wherever the endpoints are close together, and averaging the
    # ratios keeps that quantisation while averaging the PRODUCTS removes it.
    numerator = span[usable]
    denominator = float(numerator @ numerator)
    samples = []
    for entry in frames:
        clock = float(entry["presentationProgress"])
        difference = (read(root, entry) - backdrop)[usable]
        alpha = float(difference @ numerator) / denominator
        # What a single alpha cannot explain.  A crossfade has one alpha for
        # the whole frame; a ramping blur radius does not, because a pixel in
        # fine detail would reach the material sooner than one on a plateau.
        residual = difference - alpha * numerator
        samples.append({
            "clock": round(clock, 6),
            "seconds": round(float(entry["actualSeconds"]), 6),
            "alpha": round(alpha, 5),
            "crossfadeResidualCodes": round(
                float(np.sqrt((residual**2).mean())), 4),
            "crossfadeWorstCodes": round(float(np.abs(residual).max()), 3),
        })

    # The curve.  A bare power in the clock is what walle carries; the
    # captures want a DELAY before it starts, and adding one takes the fit from
    # 1.6 percent of full scale to 0.6 - which is the crossfade's own floor, so
    # there is nothing left in the curve to describe.
    middle = [s for s in samples
              if 0.05 < s["clock"] < 0.98 and 0.02 < s["alpha"] < 0.98]
    exponent = None
    if len(middle) >= 6:
        clocks = np.log(np.array([s["clock"] for s in middle]))
        alphas = np.log(np.array([s["alpha"] for s in middle]))
        exponent = float((clocks @ alphas) / (clocks @ clocks))
    residual = None
    if exponent is not None:
        error = np.array([s["alpha"] - s["clock"] ** exponent
                          for s in middle])
        residual = round(float(np.abs(error).max()), 5)

    clock = np.array([s["clock"] for s in samples])
    alpha = np.array([s["alpha"] for s in samples])
    delayed = None
    for guess_delay in np.arange(0.0, 0.351, 0.005):
        for guess_power in np.arange(1.0, 5.001, 0.02):
            eased = np.clip((clock - guess_delay) / (1.0 - guess_delay),
                            0.0, 1.0) ** guess_power
            value = float(np.sqrt(((eased - alpha) ** 2).mean()))
            if delayed is None or value < delayed[0]:
                delayed = (value, guess_delay, guess_power, eased)
    return {
        "id": sequence["id"],
        "overlay": sequence["overlay"],
        "appearance": sequence["appearance"],
        "background": sequence["background"],
        "frameCount": len(frames),
        "usablePixels": int(usable.sum()),
        "exponent": round(exponent, 4) if exponent is not None else None,
        "maximumResidualOfAlpha": residual,
        "delaySeconds": round(delayed[1], 4),
        "delayedExponent": round(delayed[2], 4),
        "delayedRootMeanSquareOfAlpha": round(delayed[0], 5),
        "delayedMaximumOfAlpha": round(
            float(np.abs(delayed[3] - alpha).max()), 5),
        "worstCrossfadeResidualCodes": round(
            float(max(s["crossfadeResidualCodes"] for s in samples)), 4),
        "samples": samples,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus", type=Path, required=True,
                        help="a capture directory holding manifest.json")
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()

    manifest = json.loads(
        (arguments.corpus / "manifest.json").read_text(encoding="utf-8"))
    records = [
        record
        for sequence in manifest["dynamicSequences"]
        if str(sequence["mode"]) == "materialize"
        and (record := measure(arguments.corpus, sequence)) is not None
    ]
    if not records:
        print("  no materialize sequences in this corpus")
        return 1

    probe = (0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9)
    print("  A single alpha per frame is what a crossfade means; `xfade` is "
          "what it cannot explain,\n  in code values, over the whole "
          "animation.\n")
    print(f"  {'material':28s} {'n':>3s} {'exp':>6s} {'|res|':>6s} "
          f"{'xfade':>6s} {'delay':>6s} {'n2':>5s} {'|res2|':>7s}   "
          "alpha at clock " + " ".join(f"{p:5.1f}" for p in probe))
    for record in records:
        table = {s["clock"]: s["alpha"] for s in record["samples"]}

        def nearest(target: float) -> float:
            key = min(table, key=lambda c: abs(c - target))
            return table[key]

        print(f"  {record['id']:28s} {record['frameCount']:3d} "
              f"{record['exponent']:6.3f} {record['maximumResidualOfAlpha']:6.4f} "
              f"{record['worstCrossfadeResidualCodes']:6.3f} "
              f"{record['delaySeconds']:6.3f} {record['delayedExponent']:5.2f} "
              f"{record['delayedMaximumOfAlpha']:7.4f}   "
              + "               " + " ".join(f"{nearest(p):5.3f}"
                                             for p in probe))
    # A timing curve should not depend on the appearance, and the separate
    # fits agree that it does not - the exponent lands on the same value in
    # light and dark for both variants.  So the shipped numbers are fitted
    # JOINTLY over both appearances, which also averages away the clock
    # decode's own jitter in the delay.
    variants: JsonObject = {}
    for variant in sorted({r["overlay"] for r in records}):
        picked = [r for r in records if r["overlay"] == variant]
        clock = np.concatenate([[s["clock"] for s in r["samples"]]
                                for r in picked])
        alpha = np.concatenate([[s["alpha"] for s in r["samples"]]
                                for r in picked])
        best = None
        for delay in np.arange(0.0, 0.351, 0.0025):
            for power in np.arange(1.0, 5.001, 0.01):
                eased = np.clip((clock - delay) / (1.0 - delay),
                                0.0, 1.0) ** power
                value = float(np.sqrt(((eased - alpha) ** 2).mean()))
                if best is None or value < best[0]:
                    best = (value, delay, power, eased)
        variants[variant] = {
            "sequenceCount": len(picked),
            "sampleCount": int(len(clock)),
            "delay": round(best[1], 4),
            "exponent": round(best[2], 4),
            "rootMeanSquareOfAlpha": round(best[0], 5),
            "maximumOfAlpha": round(float(np.abs(best[3] - alpha).max()), 5),
        }
        print(f"  joint {variant:8s} delay {best[1]:.4f} exponent "
              f"{best[2]:.3f}   rms {best[0]:.5f} max "
              f"{np.abs(best[3] - alpha).max():.5f} of full scale")

    if arguments.output is not None:
        arguments.output.write_text(
            json.dumps({"schemaVersion": 1, "osBuild": manifest["osBuild"],
                        "classification": "Liquid Glass materialize thickness",
                        "model": ("out = lerp(sharp backdrop, finished "
                                  "material, alpha); alpha = clamp((clock - "
                                  "delay) / (1 - delay)) ** exponent"),
                        "variants": variants,
                        "records": records}, indent=2, sort_keys=True) + "\n",
            encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
