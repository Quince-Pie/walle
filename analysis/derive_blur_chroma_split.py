#!/usr/bin/env python3
"""The backdrop blur treats CHROMA differently from LUMA.

This is why one instrument disagreed with three others for so long, and why
none of the obvious explanations held.

`regular`'s near/far weight reads 0.90 on every gray instrument in the corpus -
step edges at three geometries, and sine gratings at four periods and two
element sizes - and 0.54 on the coded field.  Capture path, element size, frame
size, padding rule, layer count, mixing order and a free affine were each
tested and none of them accounts for it.  Splitting the coded field's own
residual does:

    component      weight it wants    rms at the shipped 0.8846
    luma only          0.850                1.153
    chroma only        0.550                7.909

Every gray backdrop is structurally blind to this.  A step edge and a sine
grating have zero chroma, so they measure the luma weight and nothing else, and
they agree with each other and with the shipped number because the shipped
number IS the luma weight.  The coded field carries more chroma than luma - 36
code values of it against 29 - so it reads mostly the chroma weight.  uv-map
carries neither at the scales that matter: its local variation about a 129 px
mean is 0.23 code values, so its apparent opinion was noise.

The model, then, is one linear operator that mixes its two Gaussians
differently in the two subspaces:

    blurred = wLuma * near(L) + (1 - wLuma) * far(L)
            + wChroma * near(C) + (1 - wChroma) * far(C)

with L the luma part of the backdrop and C what is left.  Both parts still go
through the same two radii, which the gray instruments already pinned; only the
mixture differs.  `clear` is expected to be indifferent - its radii are 0.73
and 4.18, so there is little to tell apart - and that is the control.
"""

import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
PAD = 1024
SEARCH_PIXELS = 60000
SEED = 20260815
LUMA = np.array([0.2126, 0.7152, 0.0722])
SCENE = "circle-1000-center"
BACKGROUND = "dynamic-coded-field"
SIGMAS = {"regular": (14.188, 329.807), "clear": (0.7251, 4.1829)}
SHIPPED = {("regular", "light"): 0.8846, ("regular", "dark"): 0.5164,
           ("clear", "light"): 0.2174, ("clear", "dark"): 0.2174}

type JsonObject = dict[str, object]


def transfer_for(record) -> callable:
    exponents = [tuple(t) for t in record["termExponents"]]
    coefficients = np.array(record["coefficients"])

    def evaluate(values: np.ndarray) -> np.ndarray:
        unit = np.clip(values, 0.0, 255.0) / 255.0
        out = np.zeros(unit.shape[:-1] + (3,))
        red, green, blue = unit[..., 0], unit[..., 1], unit[..., 2]
        for (i, j, k), row in zip(exponents, coefficients):
            out += (red**i * green**j * blue**k)[..., None] * row
        return np.clip(out * 255.0, 0.0, 255.0)
    return evaluate


def blurred(backdrop: np.ndarray, sigma: float) -> np.ndarray:
    padded = np.pad(backdrop, ((PAD, PAD), (PAD, PAD), (0, 0)), "edge")
    spectrum = np.fft.rfft2(padded, axes=(0, 1))
    height, width = padded.shape[:2]
    squared = (np.fft.fftfreq(height)[:, None]**2
               + np.fft.rfftfreq(width)[None, :]**2)
    gain = np.exp(-2.0 * np.pi**2 * sigma**2 * squared)[:, :, None]
    out = np.fft.irfft2(spectrum * gain, s=(height, width), axes=(0, 1))
    return out[PAD:PAD + backdrop.shape[0], PAD:PAD + backdrop.shape[1]]


def split(image: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    luma = (image @ LUMA)[..., None]
    return luma, image - luma


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--shots", type=Path, required=True)
    parser.add_argument("--material", type=Path,
                        default=ROOT / "analysis/results/material_matrices.json")
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()

    material = json.loads(arguments.material.read_text(encoding="utf-8"))
    records = {(r["variant"], r["appearance"]): r for r in material["records"]}
    results = []
    print(f"  {'material':16s} {'wLuma':>7s} {'wChroma':>8s} {'rms':>7s} "
          f"{'max':>7s} {'one weight':>11s}   coded field, {SCENE}")
    for variant in ("regular", "clear"):
        for appearance in ("light", "dark"):
            base = arguments.shots / f"{BACKGROUND}__{SCENE}"
            backdrop_path = Path(f"{base}__none__{appearance}.png")
            measured_path = Path(f"{base}__{variant}__{appearance}.png")
            if not backdrop_path.exists() or not measured_path.exists():
                print(f"  {variant}/{appearance}: missing")
                continue
            backdrop = np.asarray(
                Image.open(backdrop_path).convert("RGB")).astype(float)
            measured = np.asarray(
                Image.open(measured_path).convert("RGB")).astype(float)
            transfer = transfer_for(records[(variant, appearance)])
            narrow, wide = SIGMAS[variant]
            near_luma, near_chroma = split(blurred(backdrop, narrow))
            far_luma, far_chroma = split(blurred(backdrop, wide))

            generator = np.random.default_rng(SEED)
            total = measured.shape[0] * measured.shape[1]
            picked = generator.choice(total, size=min(SEARCH_PIXELS, total),
                                      replace=False)

            def flat(image, channels=3):
                return image.reshape(-1, channels)[picked]

            parts = (flat(near_luma, 1), flat(far_luma, 1),
                     flat(near_chroma), flat(far_chroma))
            target = flat(measured)

            def score(luma_weight, chroma_weight, whole=False):
                if whole:
                    mixed = (luma_weight * near_luma
                             + (1.0 - luma_weight) * far_luma
                             + chroma_weight * near_chroma
                             + (1.0 - chroma_weight) * far_chroma)
                    error = transfer(mixed) - measured
                else:
                    mixed = (luma_weight * parts[0]
                             + (1.0 - luma_weight) * parts[1]
                             + chroma_weight * parts[2]
                             + (1.0 - chroma_weight) * parts[3])
                    error = transfer(mixed) - target
                return (float(np.sqrt((error**2).mean())),
                        float(np.abs(error).max()))

            best = None
            for luma_weight in np.arange(0.30, 1.001, 0.02):
                for chroma_weight in np.arange(0.20, 1.001, 0.02):
                    value = score(luma_weight, chroma_weight)[0]
                    if best is None or value < best[0]:
                        best = (value, luma_weight, chroma_weight)
            for step in (0.01, 0.005, 0.002):
                improved = True
                while improved:
                    improved = False
                    value, luma_weight, chroma_weight = best
                    for dl in (-step, 0.0, step):
                        for dc in (-step, 0.0, step):
                            trial = score(luma_weight + dl,
                                          chroma_weight + dc)[0]
                            if trial < best[0] - 1e-9:
                                best = (trial, luma_weight + dl,
                                        chroma_weight + dc)
                                improved = True
            _, luma_weight, chroma_weight = best
            rms, worst = score(luma_weight, chroma_weight, whole=True)
            single = SHIPPED[(variant, appearance)]
            one = score(single, single, whole=True)[0]
            results.append({
                "variant": variant, "appearance": appearance,
                "lumaWeight": round(float(luma_weight), 4),
                "chromaWeight": round(float(chroma_weight), 4),
                "narrowSigmaPixels": narrow, "wideSigmaPixels": wide,
                "rootMeanSquareCodes": round(rms, 3),
                "maximumCodes": round(worst, 3),
                "singleWeight": single,
                "singleWeightRootMeanSquareCodes": round(one, 3),
            })
            print(f"  {variant + '/' + appearance:16s} {luma_weight:7.3f} "
                  f"{chroma_weight:8.3f} {rms:7.3f} {worst:7.1f} {one:11.3f}")

    if not results:
        return 1
    if arguments.output is not None:
        arguments.output.write_text(
            json.dumps({"schemaVersion": 1, "osBuild": "25G76",
                        "classification": "blur weights, luma and chroma",
                        "records": results}, indent=2, sort_keys=True) + "\n",
            encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
