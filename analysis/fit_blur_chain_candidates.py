#!/usr/bin/env python3
"""Fit downsample-chain blur mechanisms against the fresh step-edge captures.

The mechanism hypothesis (campaign 2 of the 1.43->0.000 road): Apple's
"bleed" blur is not a true Gaussian but a mip-style chain - K rounds of
2x downsample behind a small prefilter, an optional blur on the coarse grid,
and bilinear upsampling back.  Each candidate chain's 1D effective kernel is
synthesized by literally running the chain on an impulse, then scored with
the same forward-convolution, free gain/offset method as
derive_material_blur_kernel.py, against the same profiles, next to the
shipped two-Gaussian baseline.

Usage: fit_blur_chain_candidates.py --capture <lgcap-static dir> [--out json]
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

HALF_WIDTH = 700
ROW_HALF_BAND = 40
INTERIOR_RADIUS = 220

STEPS = {
    "kstep-x-000-255": (0.0, 255.0),
    "kstep-x-064-192": (64.0, 192.0),
    "kstep-y-064-192": (64.0, 192.0),
}


def edge_profile(path, axis):
    pixels = np.asarray(Image.open(path).convert("RGB")).astype(float)
    if axis == "y":
        pixels = pixels.transpose(1, 0, 2)
    height, width, _ = pixels.shape
    cy, cx = height // 2, width // 2
    band = pixels[cy - ROW_HALF_BAND:cy + ROW_HALF_BAND,
                  cx - INTERIOR_RADIUS:cx + INTERIOR_RADIUS, :]
    return band.mean(axis=(0, 2)), cx - INTERIOR_RADIUS, width


def gaussian_kernel(sigma):
    from math import erf, sqrt
    sigma = max(abs(sigma), 1e-3)
    edges = np.arange(-HALF_WIDTH, HALF_WIDTH + 2) - 0.5
    cdf = np.array([0.5 * (1.0 + erf(e / (sigma * sqrt(2.0)))) for e in edges])
    return np.diff(cdf)


def chain_kernel(levels, prefilter, coarse_sigma):
    """Impulse response of: levels x (prefilter+decimate2) -> coarse gaussian
    -> levels x (zero-stuff + bilinear)."""
    n = 4 * HALF_WIDTH + 1
    signal = np.zeros(n)
    signal[n // 2] = 1.0
    pf = {"tent": np.array([1.0, 2.0, 1.0]) / 4.0,
          "gauss5": np.array([1.0, 4.0, 6.0, 4.0, 1.0]) / 16.0}[prefilter]
    for _ in range(levels):
        signal = np.convolve(signal, pf, mode="same")
        signal = signal[::2] * 2.0
        signal /= signal.sum()
    if coarse_sigma > 0.05:
        signal = np.convolve(signal, gaussian_kernel(coarse_sigma)[
            HALF_WIDTH - 50:HALF_WIDTH + 51], mode="same")
        signal /= signal.sum()
    up = np.array([0.5, 1.0, 0.5])
    for _ in range(levels):
        stuffed = np.zeros(signal.size * 2)
        stuffed[::2] = signal
        signal = np.convolve(stuffed, up, mode="same")
    signal /= signal.sum()
    center = int(np.argmax(signal))
    lo = max(0, center - HALF_WIDTH)
    hi = min(signal.size, center + HALF_WIDTH + 1)
    kernel = np.zeros(2 * HALF_WIDTH + 1)
    kernel[HALF_WIDTH - (center - lo):HALF_WIDTH + (hi - center)] = signal[lo:hi]
    return kernel / kernel.sum()


def apply_kernel(kernel, backdrop, start, count):
    blurred = np.convolve(backdrop, kernel, mode="same")
    return blurred[start:start + count]


def affine_fit(prediction, measured):
    A = np.stack([prediction, np.ones_like(prediction)], axis=1)
    (gain, offset), *_ = np.linalg.lstsq(A, measured, rcond=None)
    residual = measured - (gain * prediction + offset)
    return residual, float(gain), float(offset)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--capture", required=True)
    parser.add_argument("--overlay", default="regular")
    parser.add_argument("--appearance", default="light")
    parser.add_argument("--out")
    args = parser.parse_args()
    shots = Path(args.capture) / "shots"

    results = []
    for step_name, (low, high) in STEPS.items():
        axis = "y" if "-y-" in step_name else "x"
        matches = sorted(shots.glob(
            f"{step_name}__*__{args.overlay}__{args.appearance}.png"))
        if not matches:
            continue
        measured, start, width = edge_profile(matches[0], axis)
        count = measured.size
        backdrop = np.full(2 * HALF_WIDTH + width, low)
        backdrop[backdrop.size // 2:] = high
        # trim so 'same' convolution aligns: backdrop indexed so the step sits
        # at the image center; profile start maps into it
        pad = HALF_WIDTH
        backdrop = np.full(width + 2 * pad, low)
        backdrop[pad + width // 2:] = high

        def scored(kernel):
            prediction = apply_kernel(kernel, backdrop, pad + start, count)
            residual, gain, offset = affine_fit(prediction, measured)
            return float(np.sqrt((residual ** 2).mean())), gain, offset

        # baseline: shipped two-Gaussian (regular light @2x: 14.188/329.807,
        # weights 0.8846) - at capture pixels
        base = 0.8846 * gaussian_kernel(14.188) + 0.1154 * gaussian_kernel(329.807)
        rms, gain, offset = scored(base)
        results.append({"step": step_name, "model": "shipped-twoGaussian",
                        "rms": round(rms, 4)})

        for levels in (3, 4, 5, 6):
            for pf in ("tent", "gauss5"):
                best = None
                for coarse in (0.0, 0.5, 1.0, 1.5, 2.0, 3.0):
                    k = chain_kernel(levels, pf, coarse)
                    rms, gain, offset = scored(k)
                    if best is None or rms < best[0]:
                        best = (rms, coarse)
                results.append({
                    "step": step_name,
                    "model": f"chain-L{levels}-{pf}",
                    "coarseSigma": best[1],
                    "rms": round(best[0], 4),
                })
        # mixture: narrow gaussian + chain (the bleed as a chain)
        for levels in (4, 5, 6):
            best = None
            for w in (0.80, 0.8846, 0.92):
                for ns in (10.0, 14.188, 18.0):
                    k = w * gaussian_kernel(ns) + (1 - w) * chain_kernel(levels, "tent", 1.0)
                    rms, _, _ = scored(k)
                    if best is None or rms < best[0]:
                        best = (rms, w, ns)
            results.append({
                "step": step_name,
                "model": f"narrowGauss+chain-L{levels}",
                "narrowWeight": best[1], "narrowSigma": best[2],
                "rms": round(best[0], 4),
            })

    for r in results:
        print(json.dumps(r))
    if args.out:
        Path(args.out).write_text(json.dumps(results, indent=1))


if __name__ == "__main__":
    main()
