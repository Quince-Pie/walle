#!/usr/bin/env python3
"""Refit the backdrop blur against a CODED FIELD, not a step edge.

A step edge is one feature.  It fixes a kernel's integral well and its shape
only as well as that integral constrains it, and for `regular` that turns out
not to be well enough: rendering walle over the rig's coded field and comparing
to the hardware's own frame leaves 7.7 code values rms and 30 at worst, while
`clear` over the same field is exact to one.  The error is not walle's - the
same law forward-modelled here reproduces walle's render to 0.57 rms - so it is
the kernel.

The coded field carries every spatial frequency at once and across the whole
frame, which is what a step edge cannot do at useful amplitude.  With the
backdrop known exactly, the transfer already measured from flat colours to 0.6
code values, and the material at full thickness, the only unknown left in

    out = transfer(kernel * backdrop)

is the kernel, and this fits it there.

Convolution is done in the Fourier domain against the analytic transform of
each Gaussian, so a candidate costs one multiply rather than a 2600-tap pass,
and the field is padded by replication first because that is what both
pipelines do at a frame edge.
"""

import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
PAD = 512
# Three parameters do not need a million pixels to pin down, and the transfer
# is a 56-term polynomial per pixel - so the search scores a fixed random
# subsample and only the winner is re-scored on the whole frame.  The sample is
# drawn once from a fixed seed, so every candidate is scored on the same pixels
# and the comparison between them is exact.
SEARCH_PIXELS = 40000
SEED = 20260815


def transfer_for(record) -> callable:
    exponents = [tuple(t) for t in record["termExponents"]]
    coefficients = np.array(record["coefficients"])

    def evaluate(values: np.ndarray) -> np.ndarray:
        """Values may be a whole frame or a flat list of pixels."""
        unit = np.clip(values, 0.0, 255.0) / 255.0
        out = np.zeros_like(unit)
        red, green, blue = unit[..., 0], unit[..., 1], unit[..., 2]
        for (i, j, k), row in zip(exponents, coefficients):
            out += (red**i * green**j * blue**k)[..., None] * row
        return np.clip(out * 255.0, 0.0, 255.0)
    return evaluate


class Field:
    """The padded backdrop, ready to convolve with any Gaussian."""

    def __init__(self, backdrop: np.ndarray):
        self.shape = backdrop.shape
        padded = np.pad(backdrop, ((PAD, PAD), (PAD, PAD), (0, 0)), "edge")
        self.padded_shape = padded.shape[:2]
        self.spectrum = np.fft.rfft2(padded, axes=(0, 1))
        height, width = self.padded_shape
        self.wy = np.fft.fftfreq(height)[:, None]
        self.wx = np.fft.rfftfreq(width)[None, :]

    def blurred(self, sigma: float) -> np.ndarray:
        # A Gaussian's transform is a Gaussian, so no kernel is ever built.
        gain = np.exp(-2.0 * np.pi**2 * sigma**2
                      * (self.wy**2 + self.wx**2))[:, :, None]
        out = np.fft.irfft2(self.spectrum * gain, s=self.padded_shape,
                            axes=(0, 1))
        return out[PAD:PAD + self.shape[0], PAD:PAD + self.shape[1]]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--material", type=Path,
                        default=ROOT / "analysis/results/material_matrices.json")
    parser.add_argument("--rows", type=int, default=8,
                        help="skip this many rows of the rig's raster clock")
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()

    manifest = json.loads(
        (arguments.corpus / "manifest.json").read_text(encoding="utf-8"))
    material = json.loads(arguments.material.read_text(encoding="utf-8"))
    records = []
    for sequence in manifest["dynamicSequences"]:
        if str(sequence["mode"]) != "materialize":
            continue
        variant, appearance = sequence["overlay"], sequence["appearance"]
        record = next((r for r in material["records"]
                       if r["variant"] == variant
                       and r["appearance"] == appearance), None)
        if record is None:
            continue
        transfer = transfer_for(record)
        frames = sequence["frames"]
        backdrop = np.asarray(
            Image.open(arguments.corpus / str(frames[0]["file"]))
            .convert("RGB")).astype(float)
        measured = np.asarray(
            Image.open(arguments.corpus / str(frames[-1]["file"]))
            .convert("RGB")).astype(float)[arguments.rows:]
        field = Field(backdrop)
        cache: dict[float, np.ndarray] = {}

        def blurred(sigma: float) -> np.ndarray:
            key = round(sigma, 4)
            if key not in cache:
                cache[key] = field.blurred(key)
            return cache[key]

        rows = slice(arguments.rows, None)
        generator = np.random.default_rng(SEED)
        count = measured.shape[0] * measured.shape[1]
        picked = generator.choice(count, size=min(SEARCH_PIXELS, count),
                                  replace=False)
        target = measured.reshape(-1, 3)[picked]

        def mix(weight: float, narrow: float, wide: float) -> np.ndarray:
            weight = float(np.clip(weight, 0.0, 1.0))
            return (weight * blurred(max(narrow, 0.05))
                    + (1.0 - weight) * blurred(max(wide, 0.05)))

        def score(weight: float, narrow: float, wide: float) -> float:
            sampled = mix(weight, narrow, wide)[rows].reshape(-1, 3)[picked]
            return float(np.sqrt(((transfer(sampled) - target)**2).mean()))

        def score_all(weight: float, narrow: float, wide: float) -> float:
            error = transfer(mix(weight, narrow, wide)[rows]) - measured
            return float(np.sqrt((error**2).mean()))

        shipped = ({"regular": (0.8846 if appearance == "light" else 0.5164,
                                14.188, 329.807),
                    "clear": (0.2174, 0.7251, 4.1829)}[variant])
        best = (score(*shipped), *shipped)
        grid = ([(w, n, d)
                 for w in np.arange(0.20, 1.001, 0.05)
                 for n in np.arange(4.0, 30.01, 2.0)
                 for d in np.arange(40.0, 400.01, 40.0)]
                if variant == "regular" else
                [(w, n, d)
                 for w in np.arange(0.05, 0.61, 0.05)
                 for n in np.arange(0.2, 3.01, 0.2)
                 for d in np.arange(2.0, 9.01, 0.5)])
        for candidate in grid:
            value = score(*candidate)
            if value < best[0]:
                best = (value, *candidate)
        for step in ((0.02, 1.0, 20.0), (0.005, 0.25, 5.0),
                     (0.001, 0.05, 1.0)):
            improved = True
            while improved:
                improved = False
                value, weight, narrow, wide = best
                for dw in (-step[0], 0.0, step[0]):
                    for dn in (-step[1], 0.0, step[1]):
                        for dd in (-step[2], 0.0, step[2]):
                            trial = score(weight + dw, narrow + dn, wide + dd)
                            if trial < best[0] - 1e-6:
                                best = (trial, weight + dw, narrow + dn,
                                        wide + dd)
                                improved = True

        entry = {
            "variant": variant, "appearance": appearance,
            "shippedWeight": round(shipped[0], 4),
            "shippedNarrowSigma": round(shipped[1], 4),
            "shippedWideSigma": round(shipped[2], 4),
            "shippedRootMeanSquareCodes": round(score_all(*shipped), 3),
            "weight": round(float(best[1]), 4),
            "narrowSigmaPixels": round(float(best[2]), 4),
            "wideSigmaPixels": round(float(best[3]), 4),
            "rootMeanSquareCodes": round(
                score_all(best[1], best[2], best[3]), 3),
            "searchRootMeanSquareCodes": round(best[0], 3),
            "searchPixels": int(len(picked)),
        }
        records.append(entry)
        print(f"  {variant:8s} {appearance:5s}  shipped w={shipped[0]:.4f} "
              f"sN={shipped[1]:8.4f} sW={shipped[2]:8.3f} -> "
              f"{entry['shippedRootMeanSquareCodes']:6.3f} rms")
        print(f"  {'':14s}  refit   w={entry['weight']:.4f} "
              f"sN={entry['narrowSigmaPixels']:8.4f} "
              f"sW={entry['wideSigmaPixels']:8.3f} -> "
              f"{entry['rootMeanSquareCodes']:6.3f} rms")

    if not records:
        print("  no materialize sequences in this corpus")
        return 1
    if arguments.output is not None:
        arguments.output.write_text(
            json.dumps({"schemaVersion": 1, "osBuild": manifest["osBuild"],
                        "classification": "blur kernel from the coded field",
                        "records": records}, indent=2, sort_keys=True) + "\n",
            encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
