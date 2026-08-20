#!/usr/bin/env python3
"""Does Apple's reduced-resolution backdrop leave a resampling signature?

`backdropScale` is the one decoded parameter that differs between the
variants without being an obvious on/off switch: 0.25 for `regular`, 0.50
for `clear`.  Apple renders and blurs the backdrop at that fraction of the
element's resolution and samples it back up; walle blurs at full capture
resolution throughout.

Band-limiting alone cannot be the difference - a Gaussian of sigma 14.19
capture px already attenuates the reduced grid's Nyquist by seven orders of
magnitude - but the UPSAMPLE is not band-limiting, it is bilinear
interpolation, whose error is (h^2/8) * |f''| and does not vanish for a
smooth field.  That predicts three things the residual actually shows:

  * it scales as h^2, so `regular` (an 8x upsample in capture pixels)
    carries about four times `clear`'s (4x) - observed 2-3 rms against 0.43;
  * it is proportional to backdrop curvature, so it is large on the coded
    wallpapers' synthetic structure and small on smooth natural photographs
    - observed 7.41 against 2.17 near the boundary;
  * it is absent wherever the backdrop is locally flat, which is why every
    flat-ladder measurement in this campaign came out clean.

The test replaces the replica's full-resolution blur with Apple's pipeline -
downsample to the backdrop grid, blur there, bilinearly upsample - and
scores both against Apple through the identical transfer.  The comparison
is differential, so the replica's own error cancels, and it is run on the
natural capture as well, where the hypothesis predicts a SMALLER gain.

Usage: test_backdrop_scale_resampling.py --capture <lgcap> --work <dir>
           [--reduction 8] [--out json]
"""
import argparse
import json
from math import ceil, sqrt
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
BANDS = [(20, 50), (50, 120), (120, 250), (250, 450), (450, 700), (700, 1100)]


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


def box_down(img, factor):
    h, w, c = img.shape
    return img.reshape(h // factor, factor, w // factor, factor, c).mean(axis=(1, 3))


def bilinear_up(img, factor, height, width):
    """Bilinear upsample with reduced-grid samples at pixel centres."""
    h, w, c = img.shape
    ty = (np.arange(height) + 0.5) / factor - 0.5
    tx = (np.arange(width) + 0.5) / factor - 0.5
    y0 = np.clip(np.floor(ty).astype(int), 0, h - 1)
    x0 = np.clip(np.floor(tx).astype(int), 0, w - 1)
    y1 = np.clip(y0 + 1, 0, h - 1)
    x1 = np.clip(x0 + 1, 0, w - 1)
    fy = np.clip(ty - y0, 0, 1)[:, None, None]
    fx = np.clip(tx - x0, 0, 1)[None, :, None]
    top = img[y0][:, x0] * (1 - fx) + img[y0][:, x1] * fx
    bottom = img[y1][:, x0] * (1 - fx) + img[y1][:, x1] * fx
    return top * (1 - fy) + bottom * fy


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--capture", type=Path, required=True)
    ap.add_argument("--work", type=Path, required=True)
    ap.add_argument("--extent", type=int, default=2048)
    ap.add_argument("--reduction", type=int, default=8)
    ap.add_argument("--states", type=int, nargs="*", default=[9, 10, 11, 12, 13])
    ap.add_argument("--stride", type=int, default=3)
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()

    extent = args.extent
    factor = args.reduction
    manifest = json.loads((args.capture / "manifest.json").read_text())
    matrices = json.loads(Path("analysis/results/material_matrices.json").read_text())
    yy, xx = np.mgrid[0:extent, 0:extent].astype(float)
    distance = np.hypot(xx - CENTRE[0], yy - CENTRE[1])
    results = {}

    for appearance in ("light", "dark"):
        sequence = "sweep__wallpaper-transition__regular__" + appearance
        sweep = next((s for s in manifest["sweepSequences"] if s["id"] == sequence), None)
        if sweep is None:
            continue
        frames = [f for f in sweep["frames"] if f.get("stable", True)]
        incoming = np.asarray(Image.open(
            args.capture / "reference" / f"{sweep['incomingBackground']}.png"
        ).convert("RGB")).astype(float) / 255.0
        record = next(r for r in matrices["records"]
                      if r["variant"] == "regular" and r["appearance"] == appearance)
        exps = np.array(record["termExponents"], float)
        coef = np.array(record["coefficients"], float)

        def transfer(m):
            u = np.clip(m, 0, 1.3)
            return np.prod(u[:, None, :] ** exps[None, :, :], axis=-1) @ coef

        panel = srgb_encode(np.clip(srgb_decode(incoming) @ TO_PANEL.T, 0, None))
        w_luma, w_chroma = MIX[appearance]

        def finish(narrow):
            wide = separable(narrow, gaussian_taps(WIDE_SIGMA))
            mix = (w_chroma * narrow + (1 - w_chroma) * wide
                   + ((w_luma - w_chroma) * ((narrow - wide) @ LUMA))[..., None])
            return srgb_encode(np.clip(srgb_decode(np.clip(mix, 0, 1)) @ FROM_PANEL.T, 0, 1))

        # (a) as walle bakes it: blur at full capture resolution
        seed_full = finish(separable(panel, gaussian_taps(NARROW_SIGMA)))
        # (b) as Apple's backdropScale implies: reduce, blur there, upsample
        reduced = separable(box_down(panel, factor), gaussian_taps(NARROW_SIGMA / factor))
        seed_reduced = finish(bilinear_up(reduced, factor, extent, extent))

        acc = {b: [0.0, 0.0, 0] for b in BANDS}
        for index in args.states:
            mask_path = args.work / sequence / f"state-{index:04d}.r8"
            if not mask_path.exists() or index >= len(frames):
                continue
            mask = np.fromfile(mask_path, dtype=np.uint8).reshape(extent, extent)
            if not (mask > 0).any():
                continue
            radius = distance[mask > 0].max()
            depth = radius - distance
            apple = np.asarray(Image.open(args.capture / frames[index]["file"]
                                          ).convert("RGB")).astype(float)
            for lo, hi in BANDS:
                sel = (mask == 255) & (depth >= lo) & (depth < hi)
                stride = np.zeros_like(sel)
                stride[::args.stride, ::args.stride] = True
                sel &= stride
                if int(sel.sum()) < 2000:
                    continue
                target = apple[sel]
                for slot, seed in ((0, seed_full), (1, seed_reduced)):
                    pred = np.clip(transfer(seed[sel]) * 255.0, 0, 255)
                    acc[(lo, hi)][slot] += float(((pred - target) ** 2).sum())
                acc[(lo, hi)][2] += int(sel.sum()) * 3

        print(f"== regular/{appearance}: full-resolution blur vs "
              f"backdropScale 1/{factor} resample")
        rows = []
        ta = tb = tn = 0.0
        for lo, hi in BANDS:
            a, b, n = acc[(lo, hi)]
            if not n:
                continue
            ra, rb = sqrt(a / n), sqrt(b / n)
            ta, tb, tn = ta + a, tb + b, tn + n
            print(f"     depth {lo:4d}-{hi:4d} px:  full {ra:6.3f}   "
                  f"reduced {rb:6.3f}   ({rb - ra:+.3f})")
            rows.append({"lo": lo, "hi": hi, "full": ra, "reduced": rb})
        if tn:
            print(f"     ALL BANDS          :  full {sqrt(ta/tn):6.3f}   "
                  f"reduced {sqrt(tb/tn):6.3f}   ({sqrt(tb/tn) - sqrt(ta/tn):+.3f})")
            results[appearance] = {"bands": rows, "full": sqrt(ta / tn),
                                   "reduced": sqrt(tb / tn)}

    if args.out:
        args.out.write_text(json.dumps(results, indent=1))


if __name__ == "__main__":
    main()
