#!/usr/bin/env python3
"""Measure Apple's edgeBleed profile on flat backgrounds, content removed.

`DesignLibrary` says `regular` composites an `edgeBleed` layer that `clear`
does not have (opacity 0.5 light / 0.8 dark against 0.0), carrying its OWN
tone curve rather than sharing the face's:

                    black   white   saturation    luma law
      face  light   0.500   1.030      1.00      0.97*(0.500 + 0.530*Y)
      bleed light   0.900   1.000      1.20      0.97*(0.900 + 0.100*Y)
      face  dark    0.200   0.600      1.00      0.97*(0.200 + 0.400*Y)
      bleed dark    0.000   0.500      1.00      0.97*(0.000 + 0.500*Y)

Two tone curves blended over a band is not what walle does - it mixes one
wide field into one narrow field with global luma/chroma weights and runs a
single transfer over the result - and the difference has the exact shape of
the surviving residual: `regular`-only, decaying inward, stronger in dark
(opacity 0.8 vs 0.5), and scaling with the element rather than sitting at a
fixed pixel width.

A flat background makes the profile directly readable.  Blurring a constant
returns the constant, so BOTH layers see the same colour and every
geometric mechanism - refraction, lensing, displacement - maps the flat to
itself.  The only thing that can vary with depth is the blend weight.  So

    Y_out(d) = face(Y) + w(d) * (bleed(Y) - face(Y))

and w(d) is recovered per depth as a plain ratio, independently at each of
the 17 ladder levels of each of the four chroma lines.  Levels agreeing on
w(d) is the check that the two-layer reading is right at all; a single
level could be explained by anything.

Usage: measure_bleed_profile_on_flats.py [--variant regular] [--out json]
"""
import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image

LUMA = np.array([0.2126, 0.7152, 0.0722])
TO_PANEL = np.array([[0.8225172, 0.1774401, -0.0000221],
                     [0.0331941, 0.9667933, -0.0000244],
                     [0.0171003, 0.0724382, 0.9108519]])
FROM_PANEL = np.linalg.inv(TO_PANEL)
SHIFT = 0.97
FACE = {("regular", "light"): (0.500, 1.030, 1.00),
        ("regular", "dark"): (0.200, 0.600, 1.00),
        ("clear", "light"): (0.075, 1.150, 1.06),
        ("clear", "dark"): (0.075, 1.150, 1.06)}
BLEED = {("regular", "light"): (0.900, 1.000, 1.20),
         ("regular", "dark"): (0.000, 0.500, 1.00),
         ("clear", "light"): (0.750, 1.000, 1.20),
         ("clear", "dark"): (0.750, 1.000, 1.20)}
CAPTURES = ["/tmp/lgcap-chroma-1024", "/tmp/lgcap-chroma-iso-1024"]


def srgb_decode(u):
    u = np.clip(u, 0, 1)
    return np.where(u <= 0.04045, u / 12.92, ((u + 0.055) / 1.055) ** 2.4)


def to_panel_code(rgb):
    return srgb_encode(np.clip(srgb_decode(rgb) @ TO_PANEL.T, 0, 1))


def srgb_encode(v):
    v = np.clip(v, 0, 1)
    return np.where(v <= 0.0031308, 12.92 * v, 1.055 * np.power(v, 1 / 2.4) - 0.055)


def ycc_luma(y, black, white):
    return SHIFT * (black + (white - black) * y)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--variant", default="regular")
    ap.add_argument("--captures", nargs="*", default=CAPTURES)
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()

    bins = np.array([0, 6, 12, 20, 30, 42, 56, 72, 92, 116, 145, 180, 220, 265, 315, 380, 460])
    results = {}
    for appearance in ("light", "dark"):
        fb, fw, _ = FACE[(args.variant, appearance)]
        bb, bw, _ = BLEED[(args.variant, appearance)]
        rows = []
        for capture in args.captures:
            shots = Path(capture) / "shots"
            if not shots.is_dir():
                continue
            for path in sorted(shots.glob(
                    f"*__circle-0500-center__{args.variant}__{appearance}.png")):
                if "edge" in path.name.split("__")[0]:
                    continue
                control = shots / path.name.replace(f"__{args.variant}__", "__none__")
                if not control.exists():
                    continue
                px = np.asarray(Image.open(path).convert("RGB")).astype(float) / 255.0
                ctl = np.asarray(Image.open(control).convert("RGB")).astype(float) / 255.0
                h, w, _ = px.shape
                background = ctl[h // 2, w // 2]
                # element boundary: outermost pixel differing from the control
                changed = np.abs(px - ctl).max(axis=2) > (2.0 / 255.0)
                if not changed.any():
                    continue
                yy, xx = np.mgrid[0:h, 0:w].astype(float)
                distance = np.hypot(xx - w / 2.0, yy - h / 2.0)
                radius = distance[changed].max()
                depth = radius - distance
                y_in = float((to_panel_code(background[None, :]) @ LUMA)[0])
                face = ycc_luma(y_in, fb, fw)
                bleed = ycc_luma(y_in, bb, bw)
                if abs(bleed - face) < 0.02:
                    continue                       # the two layers agree here
                y_out = to_panel_code(px.reshape(-1, 3)) @ LUMA
                weight = (y_out - face) / (bleed - face)
                index = np.digitize(depth.ravel(), bins) - 1
                profile = np.full(len(bins) - 1, np.nan)
                for b in range(len(bins) - 1):
                    sel = index == b
                    if int(sel.sum()) > 400:
                        profile[b] = np.median(weight[sel])
                rows.append({"tag": path.name.split("__")[0], "radius": radius,
                             "yIn": y_in, "separation": bleed - face, "profile": profile})
        if not rows:
            continue
        stack = np.vstack([r["profile"] for r in rows])
        median = np.nanmedian(stack, axis=0)
        spread = np.nanstd(stack, axis=0)
        radius = float(np.median([r["radius"] for r in rows]))
        print(f"== {args.variant}/{appearance}: bleed blend weight w(depth), "
              f"{len(rows)} flat levels, element radius {radius:.0f} px")
        print(f"   (face and bleed luma laws separate by "
              f"{np.median([abs(r['separation']) for r in rows]):.3f} on average)")
        for b in range(len(bins) - 1):
            if not np.isfinite(median[b]):
                continue
            print(f"     depth {bins[b]:4d}-{bins[b+1]:4d} px  "
                  f"({bins[b]/2:5.1f}-{bins[b+1]/2:5.1f} pt)   w = {median[b]:+.4f}"
                  f"   (level spread {spread[b]:.4f})")
        results[appearance] = {
            "radius": radius, "bins": bins.tolist(),
            "weight": [None if not np.isfinite(v) else round(float(v), 5) for v in median],
            "spread": [None if not np.isfinite(v) else round(float(v), 5) for v in spread],
            "levels": len(rows),
        }

    if args.out:
        args.out.write_text(json.dumps(results, indent=1))


if __name__ == "__main__":
    main()
