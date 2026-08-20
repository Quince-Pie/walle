#!/usr/bin/env python3
"""Apple's wide-field weight against depth, split into luma and chroma.

walle mixes the narrow and wide backdrops with two different weights,

    mix - N = (1 - wChroma) * D - (wLuma - wChroma) * luma(D) * 1,  D = W - N

whose luma and chroma parts separate exactly:

    luma(mix - N)          = (1 - wLuma)   * luma(D)
    chroma(mix - N)        = (1 - wChroma) * chroma(D)

so the two shipped constants are directly measurable as the projections of
Apple's implied backdrop onto luma(D) and chroma(D), per depth.  Measuring
the two together - as the earlier profile instrument did - reports a blend
of them that matches neither and cannot say which is wrong.

The error budget puts 52% of the total in `regular`'s 20-450 px band, so a
depth-dependent weight there is worth more than everything else on the
board combined.  This says whether the depth dependence is in the luma
weight, the chroma weight, or both, and how far each sits from the constant
walle ships.

Apple's frames are inverted through walle's certified transfer polynomial
to recover the mixed backdrop, as in the single-weight instrument; deep
bins are trustworthy, and near-edge bins carry whatever the rim, lens and
bleed add, which is the point.

Usage: measure_wide_field_weight_split.py --capture <lgcap dir>
           --work <scorer work dir> [--out json]
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
BINS = [(0, 30), (30, 60), (60, 100), (100, 150), (150, 220), (220, 320),
        (320, 460), (460, 650), (650, 900), (900, 1300)]


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
    ap.add_argument("--stride", type=int, default=3)
    ap.add_argument("--states", type=int, nargs="*", default=[11, 12, 13, 14])
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
        exps = np.array(record["termExponents"], float)
        coef = np.array(record["coefficients"], float)

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

        acc = {b: [0.0, 0.0, 0.0, 0.0, 0] for b in BINS}
        for index in args.states:
            mask_path = args.work / sequence / f"state-{index:04d}.r8"
            if not mask_path.exists():
                continue
            mask = np.fromfile(mask_path, dtype=np.uint8).reshape(EXTENT, EXTENT)
            if not (mask > 0).any():
                continue
            radius = distance[mask > 0].max()
            depth = radius - distance
            apple = np.asarray(Image.open(args.capture / frames[index]["file"]
                                          ).convert("RGB")).astype(float) / 255.0
            for lo, hi in BINS:
                sel = (mask == 255) & (depth >= lo) & (depth < hi)
                stride = np.zeros_like(sel)
                stride[::args.stride, ::args.stride] = True
                sel &= stride
                if int(sel.sum()) < 800:
                    continue
                # rail-clamped anchors carry no information about the law
                target = apple[sel]
                free = ((target > 1.5 / 255) & (target < 253.5 / 255)).all(axis=1)
                if free.sum() < 500:
                    continue
                target = target[free]
                m = seed[sel][free].copy()
                for _ in range(4):
                    base = transfer(m)
                    residual = base - target
                    jac = np.empty((len(m), 3, 3))
                    for c in range(3):
                        step = np.zeros(3)
                        step[c] = 1e-3
                        jac[:, :, c] = (transfer(m + step) - base) / 1e-3
                    m = np.clip(m - np.linalg.solve(jac, residual[..., None])[..., 0], 0, 1)
                implied = srgb_encode(np.clip(srgb_decode(m) @ TO_PANEL.T, 0, None))
                dn = implied - narrow[sel][free]
                db = wide[sel][free] - narrow[sel][free]
                dn_y, db_y = dn @ LUMA, db @ LUMA
                dn_c = dn - dn_y[..., None]
                db_c = db - db_y[..., None]
                a = acc[(lo, hi)]
                a[0] += float((dn_y * db_y).sum())
                a[1] += float((db_y * db_y).sum())
                a[2] += float((dn_c * db_c).sum())
                a[3] += float((db_c * db_c).sum())
                a[4] += int(free.sum())

        shipped_luma, shipped_chroma = 1 - MIX[appearance][0], 1 - MIX[appearance][1]
        print(f"== regular/{appearance}: wide-field weight vs depth   "
              f"(walle ships luma {shipped_luma:.4f}, chroma {shipped_chroma:.4f})")
        profile = []
        for lo, hi in BINS:
            ny, dy, nc, dc, n = acc[(lo, hi)]
            if not n or dy <= 0 or dc <= 0:
                continue
            lam_y, lam_c = ny / dy, nc / dc
            print(f"     depth {lo:4d}-{hi:4d} px ({lo/2:5.0f}-{hi/2:5.0f} pt):  "
                  f"luma {lam_y:+.4f} ({lam_y - shipped_luma:+.4f})   "
                  f"chroma {lam_c:+.4f} ({lam_c - shipped_chroma:+.4f})   n={n}")
            profile.append({"lo": lo, "hi": hi, "lumaWeight": round(lam_y, 5),
                            "chromaWeight": round(lam_c, 5), "n": n})
        results[appearance] = {"shippedLuma": shipped_luma,
                               "shippedChroma": shipped_chroma, "profile": profile}

    if args.out:
        args.out.write_text(json.dumps(results, indent=1))


if __name__ == "__main__":
    main()
