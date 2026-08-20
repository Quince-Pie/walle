#!/usr/bin/env python3
"""Does the screen-backdrop seam's CHROMA half want its own kernel?

The seam correction ships one chroma weight per appearance (1.00 light, 0.60
dark) against the luma half's mixture weight, and it moved every referee
number.  The residual left behind says the weight is not constant in depth:
`dark` improved near the boundary and regressed in the middle bands
(250-450 px went 2.664 -> 2.838), exactly as the per-band optima predicted -
they fall from 0.95 at the boundary to 0.03 by 250 px, so a constant
over-corrects inward.

A weight that decays faster than the shipped profile means the chroma half
is carried by a NARROWER kernel than the luma half.  That is a real claim
about the filter, not a fudge factor, and it is falsifiable: the absolute
required correction

    m(depth) = weight(depth) * (1 - Phi(depth / sigmaWide))

should collapse onto one curve g * (1 - Phi(depth / sigmaChroma)) with the
SAME g and sigmaChroma on every wallpaper pair, because those are properties
of the material rather than of the content.

The rig supplies the pairs to test that with.  `sat-red` and `sat-blue` are
the same two saturated wallpapers in OPPOSITE order, so (wideOut - wideIn)
reverses sign between them while the material does not - a law survives the
reversal and a constant tuned to one pair's colour direction does not.  The
coded pair is a third, independent direction.

Reports the fitted (g, sigma) per appearance per capture, plus the raw
profile, so agreement across captures can be read directly rather than
asserted.

Usage: fit_screen_backdrop_chroma_kernel.py --captures <lgcap>=<workdir> ...
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
MIX = {"light": (0.8846, 0.5420), "dark": (0.5164, 0.6120)}
NARROW_SIGMA, WIDE_SIGMA = 14.188, 329.807
BANDS = [(0, 20), (20, 50), (50, 100), (100, 170), (170, 260),
         (260, 380), (380, 540), (540, 760)]


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


def phi(z):
    return 0.5 * (1.0 + np.vectorize(erf)(z / sqrt(2.0)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--captures", nargs="+", required=True,
                    help="lgcapdir=workdir pairs, rendered with the LEGACY weight")
    ap.add_argument("--extent", type=int, default=2048)
    ap.add_argument("--states", type=int, nargs="*",
                    default=[6, 7, 8, 9, 10, 11, 12, 13, 14])
    ap.add_argument("--stride", type=int, default=3)
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()

    extent = args.extent
    yy, xx = np.mgrid[0:extent, 0:extent].astype(float)
    distance = np.hypot(xx - CENTRE[0], yy - CENTRE[1])
    stride_mask = np.zeros(distance.shape, bool)
    stride_mask[::args.stride, ::args.stride] = True
    kn, kw = gaussian_taps(NARROW_SIGMA), gaussian_taps(WIDE_SIGMA)
    centres = np.array([(lo + hi) / 2.0 for lo, hi in BANDS])
    results = {}

    for spec in args.captures:
        capture, work = spec.split("=", 1)
        capture, work = Path(capture), Path(work)
        manifest = json.loads((capture / "manifest.json").read_text())
        for appearance in ("light", "dark"):
            sequence = "sweep__wallpaper-transition__regular__" + appearance
            sweep = next((s for s in manifest["sweepSequences"]
                          if s["id"] == sequence), None)
            if sweep is None:
                continue
            frames = [f for f in sweep["frames"] if f.get("stable", True)]
            fields = {}
            for role in ("incomingBackground", "outgoingBackground"):
                img = np.asarray(Image.open(
                    capture / "reference" / f"{sweep[role]}.png"
                ).convert("RGB")).astype(float) / 255.0
                panel = srgb_encode(np.clip(srgb_decode(img) @ TO_PANEL.T, 0, None))
                fields[role] = separable(separable(panel, kn), kw)
            basis = (fields["outgoingBackground"]
                     - fields["incomingBackground"]) * 255.0
            basis_c = basis - (basis @ LUMA)[..., None]
            shipped = 1.0 - MIX[appearance][1]

            acc = {b: [0.0, 0.0, 0] for b in BANDS}
            for index in args.states:
                walle_path = work / sequence / f"composition-state-{index:04d}.bgra"
                mask_path = work / sequence / f"state-{index:04d}.r8"
                if not walle_path.exists() or index >= len(frames):
                    continue
                mask = np.fromfile(mask_path, dtype=np.uint8).reshape(extent, extent)
                if not (mask > 0).any():
                    continue
                radius = distance[mask > 0].max()
                depth = radius - distance
                outside = 1.0 - phi(depth / WIDE_SIGMA)
                apple = np.asarray(Image.open(capture / frames[index]["file"]
                                              ).convert("RGB")).astype(float)
                raw = np.fromfile(walle_path, dtype=np.uint8)
                walle = raw.reshape(extent, extent, 4)[..., [2, 1, 0]].astype(float)
                residual = apple - walle
                residual_c = residual - (residual @ LUMA)[..., None]
                for lo, hi in BANDS:
                    sel = ((mask == 255) & (depth >= lo) & (depth < hi) & stride_mask)
                    if int(sel.sum()) < 1500:
                        continue
                    b = basis_c[sel] * outside[sel][..., None]
                    r = residual_c[sel]
                    e = acc[(lo, hi)]
                    e[0] += float((b * r).sum())
                    e[1] += float((b * b).sum())
                    e[2] += int(sel.sum())

            depths, magnitude = [], []
            for i, (lo, hi) in enumerate(BANDS):
                num, den, n = acc[(lo, hi)]
                if not n or den <= 0:
                    continue
                total = shipped + num / den
                depths.append(centres[i])
                magnitude.append(total * float(
                    (1.0 - phi(np.array([centres[i]]) / WIDE_SIGMA))[0]))
            if len(depths) < 4:
                continue
            depths = np.array(depths)
            magnitude = np.array(magnitude)
            # grid search the chroma kernel; g is linear given sigma
            best = None
            for sigma in np.arange(40.0, 700.0, 5.0):
                model = 1.0 - phi(depths / sigma)
                g = float((model * magnitude).sum() / (model * model).sum())
                resid = float(np.sqrt((((g * model) - magnitude) ** 2).mean()))
                if best is None or resid < best[2]:
                    best = (g, sigma, resid)
            g, sigma, resid = best
            key = f"{capture.name}/{appearance}"
            print(f"== {key}")
            print(f"     required magnitude m(depth): "
                  + "  ".join(f"{d:.0f}:{m:+.3f}" for d, m in zip(depths, magnitude)))
            print(f"     best fit  g = {g:.3f}   sigmaChroma = {sigma:.0f} px "
                  f"(luma kernel {WIDE_SIGMA:.0f})   residual {resid:.4f}")
            results[key] = {"g": g, "sigmaChroma": sigma, "residual": resid,
                            "depths": depths.tolist(),
                            "magnitude": magnitude.tolist()}

    if args.out:
        args.out.write_text(json.dumps(results, indent=1))


if __name__ == "__main__":
    main()
