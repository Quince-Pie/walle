#!/usr/bin/env python3
"""How much of the screen-backdrop correction is walle actually applying?

Session 202 shipped the fact that Apple's filter blurs the COMPOSITED
SCREEN, so outside the growing disc the wide field still sees the outgoing
wallpaper.  The correction adds

    deltaWide = (1 - normalCdf(depth / sigmaWide)) * (wideOut - wideIn)

split into walle's luma and chroma mixture weights.  It moved coded 1.36 ->
1.30 and is in the shipped shader.

The residual says the direction is right and the magnitude is not.  On the
coded sweeps the surviving near-edge error is 81% a constant per-channel
offset, and that offset is PARALLEL to (wideOut - wideIn): correlation 0.92
between the residual's mean chroma direction and the two wallpapers'
difference.  On the natural sweeps both wallpapers are near-neutral - mean
chroma norms 1.8 and 1.9 against coded's 21.5 - so there is nothing for the
term to correct, and natural indeed shows no DC offset at all.  One
mechanism explains both captures.

This measures the gain rather than assuming it.  For each depth the
residual is regressed onto the correction's own basis vector, separately in
luma and chroma, giving the EXTRA gain needed on top of what already ships.
A result near zero would say the term is correctly scaled and the parallel
direction is a coincidence of this wallpaper pair; a consistent multiplier
across depths, appearances and both captures says it is short by that
factor.

Usage: measure_screen_backdrop_gain.py --capture <lgcap> --work <dir>
           [--out json]
"""
import argparse
import json
from math import ceil, erf, sqrt
from pathlib import Path

import numpy as np
from PIL import Image

CENTRE = (512.0, 614.4)
LUMA = np.array([0.2126, 0.7152, 0.0722])
TO_PANEL = np.array([[0.8225172, 0.1774401, -0.0000221],
                     [0.0331941, 0.9667933, -0.0000244],
                     [0.0171003, 0.0724382, 0.9108519]])
FROM_PANEL = np.linalg.inv(TO_PANEL)
MIX = {"light": (0.8846, 0.5420), "dark": (0.5164, 0.6120)}
NARROW_SIGMA, WIDE_SIGMA = 14.188, 329.807
BANDS = [(0, 20), (20, 50), (50, 120), (120, 250), (250, 450),
         (450, 700), (700, 1100), (1100, 1800)]


def srgb_decode(u):
    u = np.clip(u, 0, 1)
    return np.where(u <= 0.04045, u / 12.92, ((u + 0.055) / 1.055) ** 2.4)


def srgb_encode(v):
    v = np.clip(v, 0, 1)
    return np.where(v <= 0.0031308, 12.92 * v, 1.055 * np.power(v, 1 / 2.4) - 0.055)


def gaussian_taps(sigma):
    radius = max(1, int(ceil(sigma * sqrt(2.0 * np.log(1000.0)))))
    i = np.arange(-radius, radius + 1)
    w = np.exp(-(i * i) / (2 * sigma * sigma))
    return w / w.sum()


def separable(img, k):
    r = (len(k) - 1) // 2
    p = np.pad(img, ((r, r), (0, 0), (0, 0)), mode="edge")
    out = np.zeros_like(img)
    for t in range(len(k)):
        out += k[t] * p[t:t + img.shape[0]]
    p = np.pad(out, ((0, 0), (r, r), (0, 0)), mode="edge")
    out2 = np.zeros_like(img)
    for t in range(len(k)):
        out2 += k[t] * p[:, t:t + img.shape[1]]
    return out2


def load_bgra(path, extent):
    raw = np.fromfile(path, dtype=np.uint8)
    return raw.reshape(extent, extent, 4)[..., [2, 1, 0]].astype(np.float64)


def to_panel(rgb01):
    return srgb_encode(np.clip(srgb_decode(rgb01) @ TO_PANEL.T, 0, None))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--capture", type=Path, required=True)
    ap.add_argument("--work", type=Path, required=True)
    ap.add_argument("--extent", type=int, default=2048)
    ap.add_argument("--states", type=int, nargs="*",
                    default=[6, 7, 8, 9, 10, 11, 12, 13, 14])
    ap.add_argument("--stride", type=int, default=3)
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()

    extent = args.extent
    manifest = json.loads((args.capture / "manifest.json").read_text())
    yy, xx = np.mgrid[0:extent, 0:extent].astype(float)
    distance = np.hypot(xx - CENTRE[0], yy - CENTRE[1])
    stride_mask = np.zeros(distance.shape, bool)
    stride_mask[::args.stride, ::args.stride] = True
    kn, kw = gaussian_taps(NARROW_SIGMA), gaussian_taps(WIDE_SIGMA)
    results = {}

    for appearance in ("light", "dark"):
        sequence = "sweep__wallpaper-transition__regular__" + appearance
        sweep = next((s for s in manifest["sweepSequences"] if s["id"] == sequence), None)
        if sweep is None:
            continue
        frames = [f for f in sweep["frames"] if f.get("stable", True)]
        wide = {}
        for role in ("incomingBackground", "outgoingBackground"):
            img = np.asarray(Image.open(
                args.capture / "reference" / f"{sweep[role]}.png"
            ).convert("RGB")).astype(float) / 255.0
            wide[role] = separable(separable(to_panel(img), kn), kw)
        # the correction's basis, in panel code space, before the depth factor
        basis = wide["outgoingBackground"] - wide["incomingBackground"]
        w_luma, w_chroma = MIX[appearance]
        shipped_luma, shipped_chroma = 1 - w_luma, 1 - w_chroma

        acc = {b: [0.0, 0.0, 0.0, 0.0, 0] for b in BANDS}
        for index in args.states:
            walle_path = args.work / sequence / f"composition-state-{index:04d}.bgra"
            mask_path = args.work / sequence / f"state-{index:04d}.r8"
            if not walle_path.exists() or index >= len(frames):
                continue
            mask = np.fromfile(mask_path, dtype=np.uint8).reshape(extent, extent)
            if not (mask > 0).any():
                continue
            radius = distance[mask > 0].max()
            depth = radius - distance
            outside = 1.0 - 0.5 * (1.0 + np.vectorize(erf)(
                depth / (WIDE_SIGMA * sqrt(2.0))))
            apple = np.asarray(Image.open(args.capture / frames[index]["file"]
                                          ).convert("RGB")).astype(float)
            residual = apple - load_bgra(walle_path, extent)
            for lo, hi in BANDS:
                sel = ((mask == 255) & (depth >= lo) & (depth < hi) & stride_mask)
                if int(sel.sum()) < 2000:
                    continue
                # basis vector actually available at these pixels, in codes
                b = (basis[sel] * outside[sel][..., None]) * 255.0
                r = residual[sel]
                by, ry = b @ LUMA, r @ LUMA
                bc, rc = b - by[..., None], r - ry[..., None]
                e = acc[(lo, hi)]
                e[0] += float((by * ry).sum())
                e[1] += float((by * by).sum())
                e[2] += float((bc * rc).sum())
                e[3] += float((bc * bc).sum())
                e[4] += int(sel.sum())

        print(f"== regular/{appearance}: EXTRA gain the residual wants on the "
              f"screen-backdrop term")
        print(f"   (shipped: luma {shipped_luma:.4f}, chroma {shipped_chroma:.4f})")
        print("   %-14s %12s %12s | %12s %12s" %
              ("depth (px)", "extra luma", "total luma", "extra chroma", "total chroma"))
        rows = []
        for lo, hi in BANDS:
            ny, dy, nc, dc, n = acc[(lo, hi)]
            if not n or dy <= 0 or dc <= 0:
                continue
            gy, gc = ny / dy, nc / dc
            print("   %-14s %12.4f %12.4f | %12.4f %12.4f" %
                  (f"{lo}-{hi}", gy, shipped_luma + gy, gc, shipped_chroma + gc))
            rows.append({"lo": lo, "hi": hi, "extraLuma": gy, "extraChroma": gc,
                         "totalLuma": shipped_luma + gy,
                         "totalChroma": shipped_chroma + gc, "n": n})
        results[appearance] = {"shippedLuma": shipped_luma,
                               "shippedChroma": shipped_chroma, "bands": rows}

    if args.out:
        args.out.write_text(json.dumps(results, indent=1))


if __name__ == "__main__":
    main()
