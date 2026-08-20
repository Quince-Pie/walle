#!/usr/bin/env python3
"""The un-contaminated blur-mechanism fit (session 194).

The exact flat tables (flat-field-rounding-26.6.1.json) pin the settled
pipeline's total transfer T exactly at 17 gray levels, which retires the free
gain/offset that derive_material_blur_kernel.py and
fit_blur_chain_candidates.py gave every candidate - the affine freedom that
manufactured the mip chain's -38%/-52% "win" (falsified here: chain and
two-Gaussian tie at the edge once fitted honestly).

Stages, each stated with NO free gain and NO free offset:

1. REGISTRATION from the clear controls.  Clear's transfer is affine and its
   mixture tiny (0.2174 g(0.7251) + 0.7826 g(4.1829)), so the step position
   is measured to sub-pixel with no model freedom.  Measured: 0.00 on all
   four controls at the white noise floor (rms 0.427).

2. Model A (shipped): out = T(mix of blurred step), shift locked.  The
   residual is real structure: an appearance-dependent ASYMMETRY around the
   step (equivalent displacement, dark ~ +5px toward the bright side) that
   survives a smooth monotone interpolant of T (not a chordal artifact).

3. Gamma family: out = T(255 u^(1/gamma)) - a power warp between blur and
   transfer.  Fits the edges (rms ~1.5 both appearances) but is FALSIFIED by
   the checker interiors: a fine binary checker averages to u=0.5 in ANY
   pre-warp space, so the interior mean must read T(255*0.5^(1/gamma));
   measured means sit on model A's T(127.5) to a few tenths of a code.

4. CASCADE-WARP (survivor): the wide field is computed on the power-warped
   NARROW field, far = warp^-1( wide( warp( narrow ) ) ), warp(x) = x^p per
   channel.  Uniform fields are fixed points (checker-safe by construction);
   long-range gradients pick up the measured asymmetry.  Edge rms
   light 2.30 -> 0.70 (p=0.45), dark 4.31 -> 1.12 (p=1.55).

Usage: fit_backdrop_space_mixture.py --capture <lgcap-static dir>
           --flat-tables <flat-field-rounding json> [--out json]
"""
import argparse
import json
from math import erf, sqrt
from pathlib import Path

import numpy as np
from PIL import Image

HALF_WIDTH = 1600          # kernel half-width, capture px (sigma 330 -> 4.8 sigma)
ROW_HALF_BAND = 40
INTERIOR_RADIUS = 220

CLEAR_MIX = ((0.2174, 0.7251), (0.7826, 4.1829))  # (weight, sigma) capture px


def transfer_table(rows, overlay, appearance):
    pts = {}
    for r in rows:
        if (r["overlay"] == overlay and r["appearance"] == appearance
                and r["dominantFraction"] > 0.9999):
            level = int(r["background"].split("-")[1])
            pts[level] = r["dominantRGB"][0]
    levels = np.array(sorted(pts), float)
    outs = np.array([pts[int(l)] for l in levels], float)
    return levels, outs


def gaussian_kernel(sigma):
    sigma = max(abs(sigma), 1e-3)
    edges = np.arange(-HALF_WIDTH, HALF_WIDTH + 2) - 0.5
    cdf = np.array([0.5 * (1.0 + erf(e / (sigma * sqrt(2.0)))) for e in edges])
    kernel = np.diff(cdf)
    return kernel / kernel.sum()


def edge_measured(path, axis):
    """Interior band across the backdrop step, in OUTPUT codes."""
    pixels = np.asarray(Image.open(path).convert("RGB")).astype(float)
    if axis == "y":
        pixels = pixels.transpose(1, 0, 2)
    height, width, _ = pixels.shape
    cy, cx = height // 2, width // 2
    band = pixels[cy - ROW_HALF_BAND:cy + ROW_HALF_BAND,
                  cx - INTERIOR_RADIUS:cx + INTERIOR_RADIUS, :]
    return band.mean(axis=(0, 2)), cx - INTERIOR_RADIUS, width


def unit_step(width):
    pad = HALF_WIDTH
    b = np.zeros(width + 2 * pad)
    b[pad + width // 2:] = 1.0
    return b, pad


class EdgeAxis:
    def __init__(self, path, axis):
        self.measured, start, width = edge_measured(path, axis)
        self.backdrop, pad = unit_step(width)
        self.lo = pad + start

    def blurred(self, kernel):
        return np.convolve(self.backdrop, kernel, mode="same")

    def window(self, field):
        return field[self.lo:self.lo + self.measured.size]


def interior_mean(path):
    px = np.asarray(Image.open(path).convert("RGB")).astype(float)
    h, w, _ = px.shape
    cy, cx = h // 2, w // 2
    return float(px[cy - 150:cy + 150, cx - 150:cx + 150, :].mean())


def measure_registration(shots, flat_rows):
    kern = sum(w * gaussian_kernel(s) for w, s in CLEAR_MIX)
    results = []
    for ap in ("light", "dark"):
        lv, out_tab = transfer_table(flat_rows, "clear", ap)
        for a in ("x", "y"):
            path = shots / f"edge-{a}__circle-0500-center__clear__{ap}.png"
            if not path.exists():
                continue
            ax = EdgeAxis(path, a)
            blurred = ax.blurred(kern)
            grid = np.arange(blurred.size)
            xs = np.arange(ax.lo, ax.lo + ax.measured.size)
            best = None
            for sh in np.arange(-3.0, 3.01, 0.05):
                u = np.interp(xs + sh, grid, blurred)
                r = ax.measured - np.interp(255.0 * u, lv, out_tab)
                rms = float(np.sqrt((r * r).mean()))
                if best is None or rms < best[0]:
                    best = (rms, float(sh))
            results.append({"appearance": ap, "axis": a,
                            "shift": round(best[1], 2), "rms": round(best[0], 4)})
    return results


def fit_regular(shots, flat_rows, appearance):
    lv, out_tab = transfer_table(flat_rows, "regular", appearance)
    axes = [EdgeAxis(shots / f"edge-{a}__circle-0500-center__regular__{appearance}.png", a)
            for a in ("x", "y")]
    meas = np.concatenate([ax.measured for ax in axes])

    sn_grid = np.arange(10.0, 20.01, 0.5)
    sw_grid = (200.0, 250.0, 300.0, 360.0, 420.0, 500.0)
    w_grid = np.arange(0.35, 0.981, 0.01)
    gamma_grid = np.arange(0.60, 1.51, 0.02)
    p_grid = np.arange(0.40, 2.61, 0.05)

    best_a = best_g = best_c = None
    for sn in sn_grid:
        kn = gaussian_kernel(sn)
        narrow_full = [ax.blurred(kn) for ax in axes]
        N = np.concatenate([ax.window(nf) for ax, nf in zip(axes, narrow_full)])
        for sw in sw_grid:
            kw = gaussian_kernel(sw)
            Fv = np.concatenate([ax.window(ax.blurred(kw)) for ax in axes])
            for w in w_grid:
                u = np.clip(w * N + (1 - w) * Fv, 0.0, 1.0)
                r = meas - np.interp(255.0 * u, lv, out_tab)
                v = float(np.sqrt((r * r).mean()))
                if best_a is None or v < best_a["rms"]:
                    best_a = {"rms": v, "w": w, "narrowSigma": sn, "wideSigma": sw}
                for gamma in gamma_grid:
                    r = meas - np.interp(255.0 * u ** (1.0 / gamma), lv, out_tab)
                    v = float(np.sqrt((r * r).mean()))
                    if best_g is None or v < best_g["rms"]:
                        best_g = {"rms": v, "gamma": gamma, "w": w,
                                  "narrowSigma": sn, "wideSigma": sw}
            # cascade: far = (wide(narrow^p))^(1/p)
            for p in p_grid:
                fcat = []
                for ax, nf in zip(axes, narrow_full):
                    warped = np.clip(nf, 0.0, 1.0) ** p
                    far = np.convolve(warped, kw, mode="same")
                    fcat.append(np.clip(ax.window(far), 0.0, None) ** (1.0 / p))
                Fc = np.concatenate(fcat)
                for w in w_grid:
                    u = np.clip(w * N + (1 - w) * Fc, 0.0, 1.0)
                    r = meas - np.interp(255.0 * u, lv, out_tab)
                    v = float(np.sqrt((r * r).mean()))
                    if best_c is None or v < best_c["rms"]:
                        best_c = {"rms": v, "p": p, "w": w,
                                  "narrowSigma": sn, "wideSigma": sw}
    for d in (best_a, best_g, best_c):
        for k, val in d.items():
            d[k] = round(float(val), 4)

    # checker holdout: fine binary checkers average to u = 0.5 in any
    # pre-warp space; model A and cascade predict T(127.5), gamma predicts
    # T(255 * 0.5^(1/gamma)).
    T = lambda x: float(np.interp(x, lv, out_tab))
    checker = {"predictA": round(T(127.5), 2),
               "predictGamma": round(T(255.0 * 0.5 ** (1.0 / best_g["gamma"])), 2),
               "measured": {}}
    for cells in ("0004", "0008", "0016"):
        path = shots / f"checker-{cells}__circle-0500-center__regular__{appearance}.png"
        if path.exists():
            checker["measured"][cells] = round(interior_mean(path), 2)

    return {"modelA": best_a, "gammaFamily": best_g, "cascadeWarp": best_c,
            "checkerHoldout": checker}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--capture", required=True)
    parser.add_argument("--flat-tables", required=True)
    parser.add_argument("--out")
    args = parser.parse_args()
    shots = Path(args.capture) / "shots"
    flat_rows = json.loads(Path(args.flat_tables).read_text())

    registration = measure_registration(shots, flat_rows)
    print("registration (clear controls):")
    for r in registration:
        print(f"  {r['appearance']}/{r['axis']}: shift {r['shift']:+.2f} rms {r['rms']:.3f}")

    results = {"registration": registration, "regular": {}}
    for appearance in ("light", "dark"):
        fit = fit_regular(shots, flat_rows, appearance)
        results["regular"][appearance] = fit
        print(f"regular/{appearance}:")
        print(f"  model A     : {json.dumps(fit['modelA'])}")
        print(f"  gamma family: {json.dumps(fit['gammaFamily'])}")
        print(f"  cascade-warp: {json.dumps(fit['cascadeWarp'])}")
        print(f"  checker     : {json.dumps(fit['checkerHoldout'])}")

    if args.out:
        Path(args.out).write_text(json.dumps(results, indent=1))


if __name__ == "__main__":
    main()
