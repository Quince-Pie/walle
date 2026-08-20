#!/usr/bin/env python3
"""The surviving `regular` residual is chroma, not luma.  Measure its law.

Per-channel means of (Apple - walle) inside the element, pooled over the
mid and late coded states:

    regular/light  depth  20- 50   R +10.66  G -3.49  B -0.20   luma -0.24
                   depth 120-250   R  +5.71  G -1.90  B +0.48   luma -0.11
                   depth 700-1100  R  +0.46  G -0.22  B +0.89   luma  0.00
    clear/light    every depth     R  -0.16  G  0.00  B -0.05   luma -0.04

walle's luma is already at parity - the mean luma error is under a quarter
of a code at every depth - and the whole surviving error is a chromatic
shift that decays inward.  `clear`, which carries neither the edgeBleed
layer (opacity 0 against 0.5/0.8) nor its saturation of 1.2, is flat.

That makes the correction unusually safe: chroma is orthogonal to every
luma law walle has fitted - the transfer, the mixture weights, the tonal
warp - so unlike the mixture swaps that failed, changing it cannot move a
law that something else was fitted against.

This measures the law rather than assuming it, separating the two
candidates that produce a chromatic mean shift:

    saturation   apple_chroma = s * walle_chroma       (s != 1, t = 0)
    tint         apple_chroma = walle_chroma + t       (s = 1, t != 0)

by fitting BOTH jointly per depth bin.  A saturation reading has t near
zero with s carrying the signal; a tint has the reverse.  The fit is
per-channel-vector least squares over the pixels of the bin, and the
variance explained is reported so a law that fits the mean but not the
pixels cannot pass.

Usage: measure_chroma_deficit_profile.py --capture <lgcap dir>
           --work <scorer work dir> [--out json]
"""
import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image

CENTRE = (512.0, 614.4)
LUMA = np.array([0.2126, 0.7152, 0.0722])
BANDS = [(0, 20), (20, 50), (50, 120), (120, 250), (250, 450),
         (450, 700), (700, 1100), (1100, 1800)]


def load_bgra(path: Path, extent: int) -> np.ndarray:
    raw = np.fromfile(path, dtype=np.uint8)
    return raw.reshape(extent, extent, 4)[..., [2, 1, 0]].astype(np.float64)


def chroma(rgb):
    return rgb - (rgb @ LUMA)[..., None]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--capture", type=Path, required=True)
    ap.add_argument("--work", type=Path, required=True)
    ap.add_argument("--variant", default="regular")
    ap.add_argument("--extent", type=int, default=2048)
    ap.add_argument("--states", type=int, nargs="*",
                    default=[6, 7, 8, 9, 10, 11, 12, 13, 14])
    ap.add_argument("--stride", type=int, default=2)
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()

    extent = args.extent
    manifest = json.loads((args.capture / "manifest.json").read_text())
    yy, xx = np.mgrid[0:extent, 0:extent].astype(float)
    scale = extent / 2048.0
    distance = np.hypot(xx - CENTRE[0] * scale, yy - CENTRE[1] * scale)
    stride_mask = np.zeros(distance.shape, bool)
    stride_mask[::args.stride, ::args.stride] = True
    results = {}

    for appearance in ("light", "dark"):
        sequence = f"sweep__wallpaper-transition__{args.variant}__{appearance}"
        sweep = next((s for s in manifest["sweepSequences"] if s["id"] == sequence), None)
        if sweep is None:
            continue
        frames = [f for f in sweep["frames"] if f.get("stable", True)]
        # accumulate the normal equations for [s, t] per band, per channel
        acc = {b: {"ww": 0.0, "wa": 0.0, "w": np.zeros(3), "a": np.zeros(3),
                   "aa": 0.0, "n": 0} for b in BANDS}
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
            apple = np.asarray(Image.open(args.capture / frames[index]["file"]
                                          ).convert("RGB")).astype(float)
            walle = load_bgra(walle_path, extent)
            ca, cw = chroma(apple), chroma(walle)
            for lo, hi in BANDS:
                sel = ((mask == 255) & (depth >= lo * scale)
                       & (depth < hi * scale) & stride_mask)
                n = int(sel.sum())
                if n < 2000:
                    continue
                w, a = cw[sel], ca[sel]
                e = acc[(lo, hi)]
                e["ww"] += float((w * w).sum())
                e["wa"] += float((w * a).sum())
                e["aa"] += float((a * a).sum())
                e["w"] += w.sum(axis=0)
                e["a"] += a.sum(axis=0)
                e["n"] += n

        print(f"== {args.variant}/{appearance}: chroma law of the residual "
              f"(apple_chroma vs walle_chroma)")
        print("   %-16s %8s %8s %26s %9s" %
              ("depth (px)", "scale s", "R2", "tint t (R,G,B codes)", "meanChroma"))
        rows = []
        for lo, hi in BANDS:
            e = acc[(lo, hi)]
            if not e["n"]:
                continue
            n = e["n"]
            # joint least squares for a = s*w + t, t a per-channel constant
            mw, ma = e["w"] / n, e["a"] / n
            cov = e["wa"] / n - float(mw @ ma)
            var = e["ww"] / n - float(mw @ mw)
            s = cov / var if var > 0 else float("nan")
            t = ma - s * mw
            residual = (e["aa"] / n - 2 * s * e["wa"] / n + s * s * e["ww"] / n
                        - 2 * float(t @ ma) + 2 * s * float(t @ mw) + float(t @ t))
            total = e["aa"] / n - float(ma @ ma)
            r2 = 1 - residual / total if total > 0 else float("nan")
            print("   %-16s %8.4f %8.4f   (%+6.2f,%+6.2f,%+6.2f) %9.2f" %
                  (f"{lo}-{hi}", s, r2, t[0], t[1], t[2],
                   float(np.sqrt(mw @ mw))))
            rows.append({"lo": lo, "hi": hi, "scale": s, "r2": r2,
                         "tint": t.tolist(), "n": n})
        results[appearance] = rows

    if args.out:
        args.out.write_text(json.dumps(results, indent=1))


if __name__ == "__main__":
    main()
