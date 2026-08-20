#!/usr/bin/env python3
"""Measure Apple's wide-field weight as a function of depth inside the edge.

walle composites its wide ("bleed") layer with ONE global weight per
appearance - 0.1154 light, 0.4836 dark - fitted from step edges.  Apple's
Parameters say the layer is an `edgeBleed` instead: height 224 pt,
blurRadius 160 pt (= 320 capture px, walle's fitted wide sigma to 3%),
opacity 0.5 light / 0.8 dark, zero for `clear`.  The residual agrees: it
decays from the boundary inward for `regular` and is flat for `clear`.

This measures the weight directly rather than assuming a shape.  Apple's
settled frames are inverted through walle's own (certified) transfer
polynomial to recover the MIXED BACKDROP Apple must have had, and the
wide-field weight is then the projection

    lambda(depth) = <(M_apple - N) . (B - N)> / <(B - N) . (B - N)>

per depth bin, where N is the narrow-blurred backdrop and B the wide one,
both computed in panel code space by the numpy replica of walle's bake
(validated exact against the GPU at 0.43-0.46 rms in session 199).

A flat lambda(depth) would say walle's global mixture is right and the
edge-localised residual comes from somewhere else.  A decaying one gives
the bleed's profile directly, and turns the edgeBleed campaign from a
search into an implementation.

Usage: measure_wide_field_weight_profile.py --capture <lgcap dir>
           --work <scorer work dir with renders> [--stride 4] [--out json]
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
BINS = [(0, 60), (60, 140), (140, 260), (260, 420), (420, 620), (620, 900), (900, 1300)]


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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--capture", type=Path, required=True)
    ap.add_argument("--work", type=Path, required=True)
    ap.add_argument("--stride", type=int, default=4)
    ap.add_argument("--states", type=int, nargs="*", default=[12, 14, 16])
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()

    manifest = json.loads((args.capture / "manifest.json").read_text())
    matrices = json.loads(Path("analysis/results/material_matrices.json").read_text())
    yy, xx = np.mgrid[0:EXTENT, 0:EXTENT].astype(float)
    distance = np.hypot(xx - CENTRE[0], yy - CENTRE[1])
    kn, kw = gaussian_taps(NARROW_SIGMA), gaussian_taps(WIDE_SIGMA)
    results = {}

    for appearance in ("light", "dark"):
        sequence = "sweep__wallpaper-transition__regular__" + appearance
        sweep = next(s for s in manifest["sweepSequences"] if s["id"] == sequence)
        frames = [f for f in sweep["frames"] if f.get("stable", True)]
        incoming = np.asarray(Image.open(
            args.capture / "reference" / f"{sweep['incomingBackground']}.png"
        ).convert("RGB")).astype(float) / 255.0
        record = next(r for r in matrices["records"]
                      if r["variant"] == "regular" and r["appearance"] == appearance)
        exps = np.array(record["termExponents"])
        coef = np.array(record["coefficients"])

        def transfer(m):
            u = np.clip(m, 0, 1.3)
            return np.prod(u[:, None, :] ** exps[None, :, :], axis=-1) @ coef

        panel = srgb_encode(np.clip(srgb_decode(incoming) @ TO_PANEL.T, 0, None))
        narrow = separable(panel, kn)
        wide = separable(narrow, kw)
        w_luma, w_chroma = MIX[appearance]
        seed_mix = (w_chroma * narrow + (1 - w_chroma) * wide
                    + ((w_luma - w_chroma) * ((narrow - wide) @ LUMA))[..., None])
        seed = srgb_encode(np.clip(srgb_decode(np.clip(seed_mix, 0, 1)) @ FROM_PANEL.T, 0, 1))

        acc = {b: [0.0, 0.0, 0] for b in BINS}
        for index in args.states:
            frame = args.capture / frames[index]["file"]
            apple = np.asarray(Image.open(frame).convert("RGB")).astype(float) / 255.0
            mask = np.fromfile(args.work / sequence / f"state-{index:04d}.r8",
                               dtype=np.uint8).reshape(EXTENT, EXTENT)
            if not (mask > 0).any():
                continue
            radius = distance[mask > 0].max()
            depth = radius - distance
            for lo, hi in BINS:
                sel = (mask == 255) & (depth >= lo) & (depth < hi)
                stride = np.zeros_like(sel)
                stride[::args.stride, ::args.stride] = True
                sel &= stride
                if int(sel.sum()) < 800:
                    continue
                target = apple[sel]
                m = seed[sel].copy()
                for _ in range(4):          # Newton on the transfer
                    base = transfer(m)
                    residual = base - target
                    jac = np.empty((len(m), 3, 3))
                    for c in range(3):
                        step = np.zeros(3)
                        step[c] = 1e-3
                        jac[:, :, c] = (transfer(m + step) - base) / 1e-3
                    m = np.clip(m - np.linalg.solve(jac, residual[..., None])[..., 0], 0, 1)
                implied = srgb_encode(np.clip(srgb_decode(m) @ TO_PANEL.T, 0, None))
                dn = (implied - narrow[sel]).reshape(-1)
                db = (wide[sel] - narrow[sel]).reshape(-1)
                acc[(lo, hi)][0] += float((dn * db).sum())
                acc[(lo, hi)][1] += float((db * db).sum())
                acc[(lo, hi)][2] += int(sel.sum())

        shipped = 1 - MIX[appearance][1]
        print(f"regular/{appearance}: wide-field weight vs depth "
              f"(walle ships a constant {shipped:.4f})")
        profile = []
        for lo, hi in BINS:
            num, den, n = acc[(lo, hi)]
            if n and den > 0:
                lam = num / den
                profile.append({"lo": lo, "hi": hi, "lambda": round(lam, 4), "n": n})
                print(f"    depth {lo:4d}-{hi:4d} px ({lo/2:4.0f}-{hi/2:4.0f} pt): "
                      f"lambda = {lam:.4f}   (n={n})")
        results[appearance] = profile

    if args.out:
        args.out.write_text(json.dumps(results, indent=1))


if __name__ == "__main__":
    main()
