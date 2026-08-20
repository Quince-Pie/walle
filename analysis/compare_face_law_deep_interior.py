#!/usr/bin/env python3
"""Fitted polynomial vs Apple's decoded affine face law, deep inside the element.

walle ships a 35/56-term polynomial transfer, fitted on the sweeps.  Apple
ships three numbers.  `DesignLibrary` gives the face effect as a YCC
adjustment - (black, white, saturation) - under a global white-point shift
of 0.97, and on the rig's flat ladders that reading reproduces `clear`'s
constants from pixels to three decimals IN PANEL CODE SPACE with Rec.709
luma (fitted a=+0.0725 vs decoded 0.0727; b=+1.0438 vs 1.0427; luma residual
0.34 codes).  `regular` obeys the same form just as tightly - 1.12 codes
light, 0.54 dark - with its own effective constants.

This asks the only question that decides whether to ship it: deep inside
the element, where no rim, lens, shadow, highlight or bleed reaches and the
numpy replica of walle's bake is exact against the GPU (0.43-0.46 rms),
which law is closer to Apple?

The comparison is a true holdout in both directions.  The affine constants
come from the CHROMA FLAT LADDERS - a different capture, different content,
different geometry - and are tested here on the sweeps.  The polynomial was
fitted on the sweeps and is tested on its own training distribution, so the
contest is biased AGAINST the affine law, and any win it takes is real.

Usage: compare_face_law_deep_interior.py --capture <lgcap dir>
           --work <scorer work dir> [--min-depth 250] [--out json]
"""
import argparse
import json
from math import ceil, sqrt
from pathlib import Path

import numpy as np
from PIL import Image

EXTENT = 2048
CENTRE = (512.0, 614.4)
LUMA = np.array([0.2126, 0.7152, 0.0722])
TO_PANEL = np.array([[0.8225172, 0.1774401, -0.0000221],
                     [0.0331941, 0.9667933, -0.0000244],
                     [0.0171003, 0.0724382, 0.9108519]])
FROM_PANEL = np.linalg.inv(TO_PANEL)
MIX = {"light": (0.8846, 0.5420), "dark": (0.5164, 0.6120)}
NARROW_SIGMA, WIDE_SIGMA = 14.188, 329.807

# affine face law in panel code space, measured on the chroma flat ladders
# (a, b, saturation):  Y' = a + b*Y,  C' = saturation*C
FLAT_LAW = {
    ("regular", "light"): (0.7214, 0.2663, 0.771),
    ("regular", "dark"): (0.0970, 0.2727, 0.667),
    ("clear", "light"): (0.0725, 1.0438, 1.029),
    ("clear", "dark"): (0.0725, 1.0438, 1.029),
}
# the same law read straight out of DesignLibrary, nothing fitted
DECODED_LAW = {
    ("regular", "light"): (0.97 * 0.500, 0.97 * (1.030 - 0.500), 1.00),
    ("regular", "dark"): (0.97 * 0.200, 0.97 * (0.600 - 0.200), 1.00),
    ("clear", "light"): (0.97 * 0.075, 0.97 * (1.150 - 0.075), 1.06),
    ("clear", "dark"): (0.97 * 0.075, 0.97 * (1.150 - 0.075), 1.06),
}


def srgb_decode(u):
    u = np.clip(u, 0, 1)
    return np.where(u <= 0.04045, u / 12.92, ((u + 0.055) / 1.055) ** 2.4)


def srgb_encode(v):
    v = np.clip(v, 0, 1)
    return np.where(v <= 0.0031308, 12.92 * v, 1.055 * np.power(v, 1 / 2.4) - 0.055)


def gaussian_taps(sigma):
    radius = int(ceil(sigma * sqrt(2.0 * np.log(1000.0))))
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


def affine_face(panel, a, b, saturation):
    y = panel @ LUMA
    return (a + b * y)[..., None] + saturation * (panel - y[..., None])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--capture", type=Path, required=True)
    ap.add_argument("--work", type=Path, required=True)
    ap.add_argument("--min-depth", type=float, default=250.0)
    ap.add_argument("--states", type=int, nargs="*", default=[11, 12, 13, 14])
    ap.add_argument("--stride", type=int, default=3)
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()

    manifest = json.loads((args.capture / "manifest.json").read_text())
    matrices = json.loads(Path("analysis/results/material_matrices.json").read_text())
    yy, xx = np.mgrid[0:EXTENT, 0:EXTENT].astype(float)
    distance = np.hypot(xx - CENTRE[0], yy - CENTRE[1])
    kn, kw = gaussian_taps(NARROW_SIGMA), gaussian_taps(WIDE_SIGMA)
    results = {}

    for variant in ("regular", "clear"):
        for appearance in ("light", "dark"):
            sequence = f"sweep__wallpaper-transition__{variant}__{appearance}"
            sweep = next(s for s in manifest["sweepSequences"] if s["id"] == sequence)
            frames = [f for f in sweep["frames"] if f.get("stable", True)]
            incoming = np.asarray(Image.open(
                args.capture / "reference" / f"{sweep['incomingBackground']}.png"
            ).convert("RGB")).astype(float) / 255.0
            record = next(r for r in matrices["records"] if r["variant"] == variant
                          and r["appearance"] == appearance)
            exps = np.array(record["termExponents"], float)
            coef = np.array(record["coefficients"], float)

            def polynomial(m):
                u = np.clip(m, 0, 1.3)
                return np.prod(u[:, None, :] ** exps[None, :, :], axis=-1) @ coef

            panel = srgb_encode(np.clip(srgb_decode(incoming) @ TO_PANEL.T, 0, None))
            narrow = separable(panel, kn)
            wide = separable(narrow, kw)
            w_luma, w_chroma = MIX[appearance]
            mix = (w_chroma * narrow + (1 - w_chroma) * wide
                   + ((w_luma - w_chroma) * ((narrow - wide) @ LUMA))[..., None])
            mix = np.clip(mix, 0, 1)
            seed = srgb_encode(np.clip(srgb_decode(mix) @ FROM_PANEL.T, 0, 1))

            acc = {"poly": [0.0, 0], "flat": [0.0, 0], "decoded": [0.0, 0]}
            for index in args.states:
                mask_path = args.work / sequence / f"state-{index:04d}.r8"
                if not mask_path.exists():
                    continue
                mask = np.fromfile(mask_path, dtype=np.uint8).reshape(EXTENT, EXTENT)
                if not (mask > 0).any():
                    continue
                radius = distance[mask > 0].max()
                sel = (mask == 255) & ((radius - distance) >= args.min_depth)
                stride = np.zeros_like(sel)
                stride[::args.stride, ::args.stride] = True
                sel &= stride
                if int(sel.sum()) < 5000:
                    continue
                apple = np.asarray(Image.open(args.capture / frames[index]["file"]
                                              ).convert("RGB")).astype(float)
                target = apple[sel]
                predictions = {
                    "poly": np.clip(polynomial(seed[sel]) * 255.0, 0, 255),
                    "flat": np.clip(srgb_encode(np.clip(srgb_decode(np.clip(affine_face(
                        mix[sel], *FLAT_LAW[(variant, appearance)]), 0, 1)) @ FROM_PANEL.T,
                        0, 1)) * 255.0, 0, 255),
                    "decoded": np.clip(srgb_encode(np.clip(srgb_decode(np.clip(affine_face(
                        mix[sel], *DECODED_LAW[(variant, appearance)]), 0, 1)) @ FROM_PANEL.T,
                        0, 1)) * 255.0, 0, 255),
                }
                for name, pred in predictions.items():
                    error = pred - target
                    acc[name][0] += float((error ** 2).sum())
                    acc[name][1] += int(error.size)

            if not acc["poly"][1]:
                continue
            line = {name: sqrt(total / n) for name, (total, n) in acc.items() if n}
            print(f"== {variant}/{appearance}: deep interior (depth >= {args.min_depth:.0f} px), "
                  f"{acc['poly'][1] // 3} px")
            print(f"     walle polynomial        {line['poly']:6.3f} rms")
            print(f"     affine, flat-fitted     {line['flat']:6.3f} rms"
                  f"   ({line['flat'] - line['poly']:+.3f})")
            print(f"     affine, decoded as-is   {line['decoded']:6.3f} rms"
                  f"   ({line['decoded'] - line['poly']:+.3f})")
            results[f"{variant}/{appearance}"] = line

    if args.out:
        args.out.write_text(json.dumps(results, indent=1))


if __name__ == "__main__":
    main()
