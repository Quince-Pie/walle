#!/usr/bin/env python3
"""Fit the backdrop blur against BOTH instruments at once.

A step edge and a smooth coded field constrain a kernel differently, and for
`regular` they disagree.  Fitted to the step edge alone the kernel is 1.88 code
values there and 7.70 on the field; refitted on the field it is 4.38 there and
7.90 back on the step edge.  No two-Gaussian kernel is good at both, which is
not a tuning problem - it says the family is wrong.

Why the two instruments pull apart.  The field is SMOOTH: its content lives at
periods of a hundred pixels and more, so a narrow layer passes it through
almost unchanged while a wide one averages it to the frame's mean.  What the
field measures, then, is almost purely the SPLIT between near and far - the
weight.  A step edge measures the kernel's integral, which is most sensitive to
the near layer's shape and says comparatively little about how much weight sits
in the far one.  Two measurements of the same weight that disagree mean the
kernel has more structure than two layers can hold.

So this fits three, and scores every candidate on both instruments at once.  If
three layers satisfy both, the kernel is still a convolution and the two-layer
model was simply too coarse.  If nothing does, the mechanism is not
shift-invariant - a mip cascade is not a convolution - and that is worth
knowing rather than papering over.

Neither instrument can be dropped.  The field cannot see the near layer (it has
no content there) and the step edge cannot pin the far one (a 330 px sigma in a
1024 px frame refits anywhere from 94 to 220 with no change in error).
"""

import argparse
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "analysis"))
from numeric_fit import erf  # noqa: E402

PAD = 512
SEARCH_PIXELS = 40000
SEED = 20260815
STEP_WINDOW = np.arange(512 - 120, 512 + 121)
STEP_CENTRES = (STEP_WINDOW + 0.5) - 512.0

type JsonObject = dict[str, object]


def transfer_for(record):
    exponents = [tuple(t) for t in record["termExponents"]]
    coefficients = np.array(record["coefficients"])

    def evaluate(values: np.ndarray) -> np.ndarray:
        unit = np.clip(values, 0.0, 255.0) / 255.0
        out = np.zeros(unit.shape[:-1] + (3,)) if unit.ndim > 1 \
            else np.zeros((len(unit), 3))
        if unit.ndim == 1:
            for (i, j, k), row in zip(exponents, coefficients):
                out += np.outer(unit ** (i + j + k), row)
        else:
            red, green, blue = unit[..., 0], unit[..., 1], unit[..., 2]
            for (i, j, k), row in zip(exponents, coefficients):
                out += (red**i * green**j * blue**k)[..., None] * row
        return np.clip(out * 255.0, 0.0, 255.0)
    return evaluate


class Field:
    def __init__(self, backdrop: np.ndarray):
        self.shape = backdrop.shape
        padded = np.pad(backdrop, ((PAD, PAD), (PAD, PAD), (0, 0)), "edge")
        self.padded_shape = padded.shape[:2]
        self.spectrum = np.fft.rfft2(padded, axes=(0, 1))
        height, width = self.padded_shape
        self.squared = (np.fft.fftfreq(height)[:, None]**2
                        + np.fft.rfftfreq(width)[None, :]**2)

    def blurred(self, sigma: float) -> np.ndarray:
        gain = np.exp(-2.0 * np.pi**2 * sigma**2 * self.squared)[:, :, None]
        out = np.fft.irfft2(self.spectrum * gain, s=self.padded_shape,
                            axes=(0, 1))
        return out[PAD:PAD + self.shape[0], PAD:PAD + self.shape[1]]


def phi(z):
    return 0.5 * (1.0 + erf(z / np.sqrt(2.0)))


def load_steps(variant: str, appearance: str):
    out = []
    for low, high in ((0, 255), (64, 192)):
        path = (ROOT / "artifacts-gap/shots"
                / f"kstep-x-{low:03d}-{high:03d}__circle-0500-center"
                  f"__{variant}__{appearance}.png")
        if path.exists():
            pixels = np.asarray(Image.open(path).convert("RGB")).astype(float)
            out.append((low, high,
                        pixels[512 - 8:512 + 8].mean(axis=0)[:, 0]))
    return out


def settled_frame(corpus: Path, sequence) -> Path:
    """The frame after the animation has finished, if the rig captured one.

    The last ANIMATION frame is not the settled material - for `regular` in
    light the two differ by 4.1 code values rms - so fitting against it charges
    the kernel for the tail of a transition.  Against the settled frame the
    shipped kernel drops from 1.53 to 0.58 for `clear`, which is the same 0.58
    the flat backgrounds already report, and from 4.57 to 2.62 for `regular` in
    dark.  It is what says the remaining problem is `regular` in LIGHT alone.
    """
    post = corpus / "dynamic" / str(sequence["id"]) / "post-settle.png"
    return (post if post.exists()
            else corpus / str(sequence["frames"][-1]["file"]))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--material", type=Path,
                        default=ROOT / "analysis/results/material_matrices.json")
    parser.add_argument("--rows", type=int, default=8)
    parser.add_argument("--candidates", type=int, default=4000)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()

    manifest = json.loads(
        (arguments.corpus / "manifest.json").read_text(encoding="utf-8"))
    material = json.loads(arguments.material.read_text(encoding="utf-8"))
    shipped = {"regular": {"light": (0.8846, 14.188, 329.807),
                           "dark": (0.5164, 14.188, 329.807)},
               "clear": {"light": (0.2174, 0.7251, 4.1829),
                         "dark": (0.2174, 0.7251, 4.1829)}}
    records = []

    for sequence in manifest["dynamicSequences"]:
        if str(sequence["mode"]) != "materialize":
            continue
        variant, appearance = sequence["overlay"], sequence["appearance"]
        record = next((r for r in material["records"]
                       if r["variant"] == variant
                       and r["appearance"] == appearance), None)
        steps = load_steps(variant, appearance)
        if record is None or not steps:
            continue
        transfer = transfer_for(record)
        frames = sequence["frames"]
        backdrop = np.asarray(Image.open(
            arguments.corpus / str(frames[0]["file"])).convert("RGB")
        ).astype(float)
        measured = np.asarray(Image.open(
            settled_frame(arguments.corpus, sequence)).convert("RGB")
        ).astype(float)[arguments.rows:]
        field = Field(backdrop)
        rows = slice(arguments.rows, None)
        generator = np.random.default_rng(SEED)
        total = measured.shape[0] * measured.shape[1]
        picked = generator.choice(total, size=min(SEARCH_PIXELS, total),
                                  replace=False)
        target = measured.reshape(-1, 3)[picked]

        # Only the sampled pixels are kept per sigma.  Mixing layers is linear,
        # so a layer's whole contribution to the score lives at those pixels,
        # and caching the full frame instead is 100 MB a sigma.
        cache: dict[float, np.ndarray] = {}

        # Quantised, because a fresh sigma costs a 3072-square transform and a
        # random search hands out three of them per candidate - which is a
        # cache that never hits and hours of FFT.  A twentieth of a pixel near
        # in and two pixels far out is finer than either instrument resolves.
        def quantise(sigma: float) -> float:
            sigma = max(float(sigma), 0.05)
            step = 0.05 if sigma < 4.0 else (0.5 if sigma < 40.0 else 2.0)
            return round(round(sigma / step) * step, 3)

        def sampled(sigma):
            key = quantise(sigma)
            if key not in cache:
                blurred = field.blurred(key)
                cache[key] = blurred[rows].reshape(-1, 3)[picked]
            return cache[key]

        def field_score(layers) -> float:
            mixed = sum(w * sampled(s) for w, s in layers)
            return float(np.sqrt(((transfer(mixed) - target)**2).mean()))

        # The step edge is scored on the SAME quantised sigmas, so the two
        # instruments always describe one kernel rather than two near ones.

        def step_score(layers) -> float:
            errors = []
            for low, high, profile in steps:
                fraction = sum(w * phi(STEP_CENTRES / quantise(s))
                               for w, s in layers)
                errors.append(transfer(low + (high - low) * fraction)[:, 0]
                              - profile[STEP_WINDOW])
            error = np.concatenate(errors)
            return float(np.sqrt((error**2).mean()))

        def both(layers) -> float:
            return field_score(layers) + step_score(layers)

        def three(p):
            first = float(np.clip(p[0], 0.0, 1.0))
            second = float(np.clip(p[1], 0.0, 1.0 - first))
            return ((first, abs(p[2])), (second, abs(p[3])),
                    (1.0 - first - second, abs(p[4])))

        base = shipped[variant][appearance]
        two = ((base[0], base[1]), (1.0 - base[0], base[2]))
        best = None
        wide = 400.0 if variant == "regular" else 12.0
        narrow = 2.0 if variant == "regular" else 0.2
        for _ in range(arguments.candidates):
            sigmas = np.sort(generator.uniform(0.0, 1.0, 3)) ** 2
            guess = [generator.uniform(0.05, 0.7), generator.uniform(0.05, 0.7),
                     narrow + sigmas[0] * wide, narrow + sigmas[1] * wide,
                     narrow + sigmas[2] * wide]
            value = both(three(guess))
            if best is None or value < best[0]:
                best = (value, list(guess))
        for scale in (1.0, 0.3, 0.1, 0.03):
            improved = True
            while improved:
                improved = False
                for index in range(5):
                    step = (0.02 if index < 2 else 0.05 * wide) * scale
                    for delta in (-step, step):
                        trial = list(best[1])
                        trial[index] += delta
                        value = both(three(trial))
                        if value < best[0] - 1e-6:
                            best = (value, trial)
                            improved = True

        layers = three(best[1])
        entry = {
            "variant": variant, "appearance": appearance,
            "twoLayerFieldCodes": round(field_score(two), 3),
            "twoLayerStepCodes": round(step_score(two), 3),
            "threeLayerFieldCodes": round(field_score(layers), 3),
            "threeLayerStepCodes": round(step_score(layers), 3),
            "layers": [{"weight": round(float(w), 4),
                        "sigmaPixels": round(float(s), 4)} for w, s in layers],
        }
        records.append(entry)
        print(f"  {variant:8s} {appearance:5s}  two layers  field "
              f"{entry['twoLayerFieldCodes']:6.3f}  step "
              f"{entry['twoLayerStepCodes']:6.3f}")
        print(f"  {'':14s}  three       field "
              f"{entry['threeLayerFieldCodes']:6.3f}  step "
              f"{entry['threeLayerStepCodes']:6.3f}   "
              + "  ".join(f"{w:.3f}@{s:.2f}" for w, s in layers))

    if not records:
        print("  nothing to fit in this corpus")
        return 1
    if arguments.output is not None:
        arguments.output.write_text(
            json.dumps({"schemaVersion": 1, "osBuild": manifest["osBuild"],
                        "classification": "blur kernel, both instruments",
                        "records": records}, indent=2, sort_keys=True) + "\n",
            encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
