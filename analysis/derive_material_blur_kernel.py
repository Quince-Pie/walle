#!/usr/bin/env python3
"""Recover Apple's Liquid Glass backdrop blur kernel from a step edge.

Why a step edge.  walle ships the blur as a best-fit Gaussian - sigma 13.0 px
regular, 4.1 px clear - fitted to six sine-grating MTF points.  That fit is
known to be wrong in shape, not just in scale: the sigma implied per period
climbs monotonically with period, which is what happens when a heavier-tailed
kernel is forced into a Gaussian.  Six frequency samples cannot say what the
real shape is.  A step edge can: one capture carries every frequency at once.

The corpus does contain one direct kernel measurement - the v2.18 fixed-impulse
probe, which resolved clear as a mixture of exact bilinear 2x reconstruction
and an isotropic Gaussian of sigma 4.15 output pixels, to 0.003 codes.  Two
things stop that from being the answer here.  It was captured on 25E246 (macOS
26.4), the build whose material constants this machine has already been proven
not to match, and it never covered `regular` at all.  Its STRUCTURE is the most
valuable prior available, so it is one of the models fitted below.

WHY THIS DOES NOT SIMPLY NORMALISE THE EDGE PROFILE.  The obvious method -
scale the interior profile between its two plateaus and fit the result - is
wrong here, and measurably so.  `regular` carries a second, very wide layer
(Apple's own transition inputs give it a BLEED stage with a 160-unit blur
radius that `clear` does not have), and a kernel that wide never settles to a
plateau inside a 500 px element: the profile is still climbing at the edge of
the usable window, and normalising against it makes the same background read
MTF 0.625 at period 256 but 0.522 at period 512, which no real kernel can do.

So the model is forward instead.  The backdrop is known exactly - a full-frame
step - so each candidate kernel is convolved with that actual finite backdrop,
edges replicated the way the compositor clamps them, and the prediction is
compared to the measured interior with a free gain and offset standing in for
the material's affine transfer.  Nothing is assumed to converge.

The blur space is fitted rather than assumed.  If the compositor blurs in
linear light and the capture reports sRGB, a profile read in code space is
distorted by the encoding, and distorted DIFFERENTLY in light and dark because
the two sit at different output levels - which would fake exactly the kind of
appearance dependence seen here.  Both spaces are fitted and reported.
"""

import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image
from numeric_fit import erf, least_squares

ROOT = Path(__file__).resolve().parents[1]
SHAPE = "circle-0500-center"
# The capture is 512 points at 2x backing scale, so 1024 px, and the element is
# a 500 pt circle - radius 500 px.  The refraction band is the outer 0.25 R, so
# everything past 375 px is material geometry rather than backdrop.  350 px
# keeps a margin on that.
INTERIOR_RADIUS = 350
ROW_HALF_BAND = 120
# Every kstep background carries its step at the image centre, by construction.
STEP_LEVELS = {
    "kstep-x-064-192": (64.0, 192.0),
    "kstep-y-064-192": (64.0, 192.0),
    "kstep-x-000-255": (0.0, 255.0),
}
# Wide enough to hold a sigma-200 tail; the convolution is cheap either way.
HALF_WIDTH = 700

type JsonObject = dict[str, object]


def srgb_to_linear(value: np.ndarray) -> np.ndarray:
    value = np.asarray(value, dtype=float)
    return np.where(value <= 0.04045, value / 12.92,
                    ((value + 0.055) / 1.055) ** 2.4)


def linear_to_srgb(value: np.ndarray) -> np.ndarray:
    value = np.clip(np.asarray(value, dtype=float), 0.0, 1.0)
    return np.where(value <= 0.0031308, value * 12.92,
                    1.055 * value ** (1.0 / 2.4) - 0.055)


def edge_profile(path: Path, axis: str) -> tuple[np.ndarray, int, int]:
    """Interior profile across the step, with the window's placement."""
    pixels = np.asarray(Image.open(path).convert("RGB")).astype(float)
    if axis == "y":
        pixels = pixels.transpose(1, 0, 2)
    height, width, _ = pixels.shape
    center_y, center_x = height // 2, width // 2
    band = pixels[
        center_y - ROW_HALF_BAND:center_y + ROW_HALF_BAND,
        center_x - INTERIOR_RADIUS:center_x + INTERIOR_RADIUS,
        :,
    ]
    return band.mean(axis=(0, 2)), center_x - INTERIOR_RADIUS, width


def gaussian_kernel(sigma: float) -> np.ndarray:
    """Pixel-integrated 1D Gaussian: each tap is its area, not its height."""
    sigma = max(abs(sigma), 1e-3)
    edges = np.arange(-HALF_WIDTH, HALF_WIDTH + 2) - 0.5
    return np.diff(0.5 * (1.0 + erf(edges / (sigma * np.sqrt(2.0)))))


def triangle_kernel(cell: float) -> np.ndarray:
    """Pixel-integrated triangle: the x-marginal of bilinear reconstruction."""
    cell = max(abs(cell), 1e-3)
    edges = np.arange(-HALF_WIDTH, HALF_WIDTH + 2) - 0.5
    t = np.clip(edges / cell, -1.0, 1.0)
    return np.diff(np.where(t < 0.0, 0.5 * (1.0 + t) ** 2,
                            1.0 - 0.5 * (1.0 - t) ** 2))


def apply(kernel: np.ndarray, backdrop: np.ndarray, start: int, count: int
          ) -> np.ndarray:
    """Convolve with the real finite backdrop, replicating at its edges."""
    padded = np.pad(backdrop, len(kernel) // 2, mode="edge")
    return np.convolve(padded, kernel, mode="valid")[start:start + count]


def fit_models(measured: np.ndarray, backdrop: np.ndarray, start: int,
               space: str, to_codes) -> list[JsonObject]:
    """Fit candidate kernels forward against the true backdrop."""
    count = len(measured)
    measured_codes = to_codes(measured)
    models: list[JsonObject] = []

    def affine_residual(predicted: np.ndarray) -> tuple[np.ndarray, float, float]:
        """Absorb the material's gain and offset, which are not the kernel."""
        design = np.column_stack([predicted, np.ones(count)])
        solution, *_ = np.linalg.lstsq(design, measured, rcond=None)
        return design @ solution - measured, float(solution[0]), float(solution[1])

    def record(name: str, parameters: JsonObject, kernel: np.ndarray) -> None:
        residual, gain, offset = affine_residual(
            apply(kernel, backdrop, start, count))
        # Report in capture code values by mapping the fitted profile back
        # through the same encoding the measurement came in, rather than
        # rescaling by a local slope.
        codes = to_codes(residual + measured) - measured_codes
        models.append({
            "model": name,
            "space": space,
            **parameters,
            "transferGain": round(gain, 5),
            "transferOffset": round(offset, 4),
            "maximumResidualCodes": round(float(np.abs(codes).max()), 4),
            "rootMeanSquareResidualCodes": round(
                float(np.sqrt((codes**2).mean())), 4),
        })

    def residual_for(build):
        return lambda p: affine_residual(
            apply(build(p), backdrop, start, count))[0]

    # (a) one Gaussian - what walle ships.
    single = lambda p: gaussian_kernel(p[0])
    solution = least_squares(residual_for(single), [8.0])
    record("gaussian", {"sigmaPixels": round(abs(float(solution[0])), 4)},
           single(solution))

    # (b) the impulse probe's structure, weights and scales left free so that
    # "did 26.4's structure survive" is answered by the data.
    def mixture(p):
        weight = float(np.clip(p[0], 0.0, 1.0))
        return (weight * triangle_kernel(p[1])
                + (1.0 - weight) * gaussian_kernel(p[2]))

    solution = least_squares(residual_for(mixture), [0.3, 2.0, 4.2])
    record("bilinearPlusGaussian", {
        "bilinearWeight": round(float(np.clip(solution[0], 0.0, 1.0)), 4),
        "bilinearCellPixels": round(abs(float(solution[1])), 4),
        "gaussianSigmaPixels": round(abs(float(solution[2])), 4),
    }, mixture(solution))

    # (c) two Gaussians - a narrow material blur plus the wide bleed layer that
    # only `regular` carries.
    def two_gaussian(p):
        weight = float(np.clip(p[0], 0.0, 1.0))
        return (weight * gaussian_kernel(p[1])
                + (1.0 - weight) * gaussian_kernel(p[2]))

    solution = least_squares(residual_for(two_gaussian), [0.85, 13.0, 160.0])
    record("twoGaussian", {
        "narrowWeight": round(float(np.clip(solution[0], 0.0, 1.0)), 4),
        "narrowSigmaPixels": round(abs(float(solution[1])), 4),
        "wideSigmaPixels": round(abs(float(solution[2])), 4),
    }, two_gaussian(solution))

    # (d) both structures at once: bilinear reconstruction, a narrow Gaussian
    # and the wide bleed.  The most general of the three, so it only earns its
    # extra parameters if it beats them.
    def full(p):
        first = float(np.clip(p[0], 0.0, 1.0))
        second = float(np.clip(p[1], 0.0, 1.0 - first))
        return (first * triangle_kernel(p[2])
                + second * gaussian_kernel(p[3])
                + (1.0 - first - second) * gaussian_kernel(p[4]))

    solution = least_squares(residual_for(full), [0.2, 0.65, 2.0, 13.0, 160.0])
    first = float(np.clip(solution[0], 0.0, 1.0))
    second = float(np.clip(solution[1], 0.0, 1.0 - first))
    record("bilinearNarrowWide", {
        "bilinearWeight": round(first, 4),
        "narrowWeight": round(second, 4),
        "wideWeight": round(1.0 - first - second, 4),
        "bilinearCellPixels": round(abs(float(solution[2])), 4),
        "narrowSigmaPixels": round(abs(float(solution[3])), 4),
        "wideSigmaPixels": round(abs(float(solution[4])), 4),
    }, full(solution))
    return models


def analyse(shots: Path, background: str, axis: str) -> list[JsonObject]:
    low, high = STEP_LEVELS[background]
    results: list[JsonObject] = []
    for overlay in ("none", "regular", "clear"):
        for appearance in ("light", "dark"):
            path = shots / f"{background}__{SHAPE}__{overlay}__{appearance}.png"
            if not path.exists():
                continue
            profile, start, width = edge_profile(path, axis)
            entry: JsonObject = {
                "background": background,
                "axis": axis,
                "overlay": overlay,
                "appearance": appearance,
                "windowStartPixels": start,
                "windowPixels": len(profile),
                "measuredEndsCodes": [round(float(profile[0]), 3),
                                      round(float(profile[-1]), 3)],
                "fits": [],
            }
            step = np.where(np.arange(width) < width // 2, low, high) / 255.0
            for space in ("srgbCode", "linearLight"):
                if space == "linearLight":
                    backdrop = srgb_to_linear(step)
                    target = srgb_to_linear(profile / 255.0)
                    to_codes = lambda v: linear_to_srgb(v) * 255.0
                else:
                    backdrop, target = step, profile / 255.0
                    to_codes = lambda v: v * 255.0
                entry["fits"].extend(
                    fit_models(target, backdrop, start, space, to_codes))
            results.append(entry)
    return results


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--shots", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()

    records = [
        record
        for background, axis in (("kstep-x-064-192", "x"),
                                 ("kstep-y-064-192", "y"),
                                 ("kstep-x-000-255", "x"))
        for record in analyse(arguments.shots, background, axis)
    ]
    report = {
        "schemaVersion": 2,
        "classification":
            "Liquid Glass backdrop blur kernel, measured from a step edge",
        "osBuild": "25G76",
        "method":
            "forward convolution against the true finite backdrop, free affine transfer",
        "records": records,
    }
    encoded = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if arguments.output is not None:
        arguments.output.write_text(encoded, encoding="utf-8")
    for record in records:
        best = min(record["fits"], key=lambda f: f["rootMeanSquareResidualCodes"])
        print(
            f"{record['background']:16s} {record['overlay']:8s} "
            f"{record['appearance']:5s} {best['space']:11s} "
            f"{best['model']:20s} rms={best['rootMeanSquareResidualCodes']:7.3f} "
            f"max={best['maximumResidualCodes']:7.3f} codes"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
