#!/usr/bin/env python3
"""Extract and validate the far-field tonal warp (session 195).

Follow-up to fit_backdrop_space_mixture.py: with the shipped mixture
constants HELD, the warp W in far = W^-1(wide(W(narrow))) is extracted
NONPARAMETRICALLY as a monotone piecewise-linear function (second-difference
smoothness lambda, endpoints pinned - W is only identified up to affine
maps, which the sandwich cancels).  Three verdicts this produced:

  * light regular's warp is NAMED: W(v) = 1 - (1-v)^3 (the same power law
    on the inverted signal / distance-from-white) matches the free
    extraction at its floor.
  * dark regular's warp has a robust plateau at u ~ 0.6-0.7 that no simple
    curve reproduces (power/sRGB/plate blends all fail); the extracted LUT
    itself generalizes.
  * the SLANT edge (12.8 deg, never fitted) is the holdout: light
    2.56 -> 0.96 (flipped cube), dark 4.35 -> 1.10 (extracted LUT),
    vs the fitted powers 0.99 / 2.67.

Usage: extract_far_field_warp.py --capture <lgcap-static dir>
           --flat-tables <flat-field-rounding json> [--lam 1.0] [--out json]
"""
import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image

import fit_backdrop_space_mixture as base

KNOTS = np.array([0.0, 0.05, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 0.95, 1.0])
SHIPPED = {"light": (0.8846, 14.188, 329.807), "dark": (0.5164, 14.188, 329.807)}


def fit_free_warp(axes, lv, out_tab, w, kw, narrow_full, pinit, lam):
    meas = np.concatenate([ax.measured for ax in axes])
    N = np.concatenate([ax.window(nf) for ax, nf in zip(axes, narrow_full)])

    def loss(vals):
        wk = np.concatenate([[0.0], vals, [1.0]])
        if np.any(np.diff(wk) <= 0):
            return 1e9, 1e9
        fcat = []
        for ax, nf in zip(axes, narrow_full):
            far_w = np.convolve(np.interp(nf, KNOTS, wk), kw, mode="same")
            fcat.append(np.interp(np.clip(ax.window(far_w), 0.0, 1.0), wk, KNOTS))
        u = np.clip(w * N + (1 - w) * np.concatenate(fcat), 0.0, 1.0)
        r = meas - np.interp(255.0 * u, lv, out_tab)
        data = float(np.sqrt((r * r).mean()))
        d2 = np.diff(np.diff(wk) / np.diff(KNOTS))
        return data + lam * float((d2 * d2).mean()), data

    vals = KNOTS[1:-1] ** pinit
    best, best_data = loss(vals)
    step = 0.05
    for _ in range(100):
        improved = False
        for i in range(len(vals)):
            for d in (+step, -step):
                trial = vals.copy()
                trial[i] += d
                v, dat = loss(trial)
                if v < best:
                    best, best_data, vals, improved = v, dat, trial, True
        if not improved:
            step *= 0.5
            if step < 0.0005:
                break
    return np.concatenate([[0.0], vals, [1.0]]), best_data


def warp_rms(axes, lv, out_tab, w, kw, narrow_full, fwd, inv):
    meas = np.concatenate([ax.measured for ax in axes])
    N = np.concatenate([ax.window(nf) for ax, nf in zip(axes, narrow_full)])
    fcat = []
    for ax, nf in zip(axes, narrow_full):
        far = np.convolve(fwd(np.clip(nf, 0.0, 1.0)), kw, mode="same")
        fcat.append(inv(np.clip(ax.window(far), 0.0, 1.0)))
    u = np.clip(w * N + (1 - w) * np.concatenate(fcat), 0.0, 1.0)
    r = meas - np.interp(255.0 * u, lv, out_tab)
    return float(np.sqrt((r * r).mean()))


def slant_line(none_path):
    """Sub-pixel edge line from the raw backdrop: per-row threshold
    crossings fitted to x = a*y + b (the PCA-on-boundary approach fails on
    this suite - it landed 700px off)."""
    px = np.asarray(Image.open(none_path).convert("L")).astype(float)
    h, _ = px.shape
    ys, xcs = [], []
    for y in range(0, h, 8):
        row = px[y]
        idx = np.where((row[:-1] < 127.5) & (row[1:] >= 127.5))[0]
        if len(idx) != 1:
            continue
        i = idx[0]
        frac = (127.5 - row[i]) / max(row[i + 1] - row[i], 1e-9)
        ys.append(float(y))
        xcs.append(i + frac)
    a, b = np.polyfit(np.array(ys), np.array(xcs), 1)
    norm = float(np.hypot(1.0, a))
    return 1.0 / norm, -a / norm, b / norm  # dist = nx*x + ny*y - c


def slant_profile(shot_path, nx, ny, c, rmax=400.0, span=220.0):
    px = np.asarray(Image.open(shot_path).convert("RGB")).astype(float).mean(axis=2)
    h, w = px.shape
    ys, xs = np.mgrid[0:h, 0:w]
    cy, cx = h // 2, w // 2
    sel = ((xs - cx) ** 2 + (ys - cy) ** 2 < rmax * rmax)
    dist = nx * xs + ny * ys - c
    sel &= np.abs(dist) < span
    bins = np.floor((dist[sel] + span)).astype(int)
    n = int(2 * span)
    sums = np.bincount(bins, weights=px[sel], minlength=n)
    cnts = np.bincount(bins, minlength=n)
    ok = cnts > 20
    centers = (np.arange(n) + 0.5) - span
    return centers[ok], sums[ok] / cnts[ok]


def slant_eval(shots, appearance, lv, out_tab, w, sn, sw, forms):
    nx, ny, c = slant_line(
        shots / f"edge-slant__circle-0500-center__none__{appearance}.png")
    centers, meas = slant_profile(
        shots / f"edge-slant__circle-0500-center__regular__{appearance}.png",
        nx, ny, c)
    pad = base.HALF_WIDTH
    n1 = int(2 * 260 + 2 * pad)
    grid = np.arange(n1, dtype=float) - (n1 // 2)
    step = (grid >= 0).astype(float)
    N = np.clip(np.convolve(step, base.gaussian_kernel(sn), mode="same"), 0, 1)
    kw = base.gaussian_kernel(sw)
    out = {}
    for name, (fwd, inv) in forms.items():
        far = inv(np.clip(np.convolve(fwd(N), kw, mode="same"), 0, 1))
        u = np.clip(w * N + (1 - w) * far, 0, 1)
        pred = np.interp(centers, grid, np.interp(255.0 * u, lv, out_tab))
        out[name] = round(float(np.sqrt(((meas - pred) ** 2).mean())), 4)
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--capture", required=True)
    parser.add_argument("--flat-tables", required=True)
    parser.add_argument("--lam", type=float, default=1.0)
    parser.add_argument("--out")
    args = parser.parse_args()
    shots = Path(args.capture) / "shots"
    flat_rows = json.loads(Path(args.flat_tables).read_text())

    results = {}
    for ap, pinit in (("light", 0.40), ("dark", 1.34)):
        lv, out_tab = base.transfer_table(flat_rows, "regular", ap)
        w, sn, sw = SHIPPED[ap]
        kn, kw = base.gaussian_kernel(sn), base.gaussian_kernel(sw)
        per_axis, knots_by_axis = {}, {}
        for a in ("x", "y"):
            ax = base.EdgeAxis(
                shots / f"edge-{a}__circle-0500-center__regular__{ap}.png", a)
            nf = [np.clip(ax.blurred(kn), 0.0, 1.0)]
            wk, rms = fit_free_warp([ax], lv, out_tab, w, kw, nf, pinit, args.lam)
            per_axis[a], knots_by_axis[a] = rms, wk
        axes = [base.EdgeAxis(
            shots / f"edge-{a}__circle-0500-center__regular__{ap}.png", a)
            for a in ("x", "y")]
        nfs = [np.clip(ax.blurred(kn), 0.0, 1.0) for ax in axes]
        wk, rms_joint = fit_free_warp(axes, lv, out_tab, w, kw, nfs, pinit, args.lam)
        consistency = float(np.abs(knots_by_axis["x"] - knots_by_axis["y"]).max())

        forms = {
            "no-warp": (lambda x: x, lambda y: y),
            f"power-{pinit}": (lambda x, p=pinit: np.power(np.clip(x, 0, 1), p),
                               lambda y, p=pinit: np.power(np.clip(y, 0, None), 1 / p)),
            "extracted-LUT": (lambda x, k=wk: np.interp(x, KNOTS, k),
                              lambda y, k=wk: np.interp(y, k, KNOTS)),
        }
        if ap == "light":
            forms["flipped-cube"] = (
                lambda x: 1 - np.power(1 - np.clip(x, 0, 1), 3.0),
                lambda y: 1 - np.power(1 - np.clip(y, 0, 1), 1 / 3.0))
        edge = {name: round(warp_rms(axes, lv, out_tab, w, kw, nfs, f, i), 4)
                for name, (f, i) in forms.items()}
        slant = slant_eval(shots, ap, lv, out_tab, w, sn, sw, forms)
        results[ap] = {"lam": args.lam, "knots": [round(float(v), 4) for v in wk],
                       "jointRms": round(rms_joint, 4),
                       "axisConsistency": round(consistency, 4),
                       "edgeRms": edge, "slantRms": slant}
        print(f"regular/{ap}: joint rms {rms_joint:.3f}  |Wx-Wy| {consistency:.3f}")
        print(f"  W: " + " ".join(f"{v:.3f}" for v in wk))
        print(f"  edge : {json.dumps(edge)}")
        print(f"  slant: {json.dumps(slant)}")

    if args.out:
        Path(args.out).write_text(json.dumps(results, indent=1))


if __name__ == "__main__":
    main()
