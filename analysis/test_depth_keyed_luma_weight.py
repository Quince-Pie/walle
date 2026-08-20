#!/usr/bin/env python3
"""Does a depth-keyed wide-field LUMA weight beat walle's constant?

The split-weight instrument measured Apple's wide-field weights against
depth by inverting Apple's frames through walle's own transfer polynomial.
The chroma weight came out near-constant; the luma weight did not:

    depth (px)      light luma           dark luma
       100-150   0.039 (ships 0.1154)  0.262 (ships 0.4836)
       320-460   0.058                 0.413
      900-1300   0.093                 0.440

That deficit sits exactly where the error budget puts 52% of the total, and
it reproduces across states whose boundaries are 400 px apart, so it is
keyed on depth rather than on position in the wallpaper.

Because the profile was recovered THROUGH walle's transfer, replaying it
with that same transfer is self-consistent - which is the trap that broke
the three previous mixture swaps, where a mixture was changed underneath a
transfer fitted to the old one.  Here nothing is refitted; the mixture is
being restored to what Apple's own pixels say it was.

The profile is measured on states 11-14 and scored here on EARLIER states
and on the natural-wallpaper capture, neither of which it saw.  The
comparison is differential - constant weight versus profiled weight through
the identical replica - so the replica's own error cancels.

Usage: test_depth_keyed_luma_weight.py --capture <lgcap dir>
           --work <scorer work dir> [--states ...] [--out json]
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

# pooled measurement from states 11-14 (depth centre px -> wide luma weight);
# the 0-30 bin is excluded, it carries the rim and lens
PROFILE = {
    "light": ([45, 80, 125, 185, 270, 390, 555, 775, 1100],
              [0.0626, 0.0487, 0.0391, 0.0446, 0.0509, 0.0581, 0.0743, 0.0873, 0.0929]),
    "dark": ([45, 80, 125, 185, 270, 390, 555, 775, 1100],
             [0.2798, 0.2831, 0.2615, 0.2931, 0.3481, 0.4134, 0.4341, 0.4116, 0.4399]),
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--capture", type=Path, required=True)
    ap.add_argument("--work", type=Path, required=True)
    ap.add_argument("--extent", type=int, default=2048)
    ap.add_argument("--states", type=int, nargs="*", default=[8, 9, 10])
    ap.add_argument("--stride", type=int, default=3)
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()

    extent = args.extent
    manifest = json.loads((args.capture / "manifest.json").read_text())
    matrices = json.loads(Path("analysis/results/material_matrices.json").read_text())
    yy, xx = np.mgrid[0:extent, 0:extent].astype(float)
    scale = extent / 2048.0
    distance = np.hypot(xx - CENTRE[0] * scale, yy - CENTRE[1] * scale)
    kn = gaussian_taps(NARROW_SIGMA * scale)
    kw = gaussian_taps(WIDE_SIGMA * scale)
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
        narrow = separable(panel, kn)
        wide = separable(narrow, kw)
        w_luma, w_chroma = MIX[appearance]
        delta = wide - narrow
        delta_y = delta @ LUMA
        # constant-weight mixture, exactly as baked today
        base = narrow + (1 - w_chroma) * delta - ((w_luma - w_chroma) * delta_y)[..., None]

        depths, weights = PROFILE[appearance]
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
                sel = (mask == 255) & (depth >= lo * scale) & (depth < hi * scale)
                stride = np.zeros_like(sel)
                stride[::args.stride, ::args.stride] = True
                sel &= stride
                if int(sel.sum()) < 2000:
                    continue
                target = apple[sel]
                # profiled weight: luma part re-weighted per pixel.  PROFILE
                # holds the WIDE weight lambda = 1 - wLuma, and the constant
                # mixture already carries (1 - wLuma) * delta_y, so the shift
                # is the difference of the two wide weights - not of a wide
                # weight against a narrow one.
                w_pixel = np.interp(depth[sel] / scale, depths, weights)
                shifted = base[sel] + ((w_pixel - (1.0 - w_luma)) * delta_y[sel])[..., None]
                for name, mixed in (("const", base[sel]), ("profile", shifted)):
                    seed = srgb_encode(np.clip(
                        srgb_decode(np.clip(mixed, 0, 1)) @ FROM_PANEL.T, 0, 1))
                    pred = np.clip(transfer(seed) * 255.0, 0, 255)
                    err = pred - target
                    slot = 0 if name == "const" else 1
                    acc[(lo, hi)][slot] += float((err ** 2).sum())
                acc[(lo, hi)][2] += int(sel.sum()) * 3

        print(f"== regular/{appearance}: constant vs depth-keyed luma weight "
              f"(states {args.states})")
        rows = []
        tc = tp = tn = 0.0
        for lo, hi in BANDS:
            c, p, n = acc[(lo, hi)]
            if not n:
                continue
            rc, rp = sqrt(c / n), sqrt(p / n)
            tc += c
            tp += p
            tn += n
            print(f"     depth {lo:4d}-{hi:4d} px:  constant {rc:6.3f}   "
                  f"profiled {rp:6.3f}   ({rp - rc:+.3f})")
            rows.append({"lo": lo, "hi": hi, "constant": rc, "profiled": rp})
        if tn:
            print(f"     ALL BANDS          :  constant {sqrt(tc/tn):6.3f}   "
                  f"profiled {sqrt(tp/tn):6.3f}   ({sqrt(tp/tn) - sqrt(tc/tn):+.3f})")
            results[appearance] = {"bands": rows, "constant": sqrt(tc / tn),
                                   "profiled": sqrt(tp / tn)}

    if args.out:
        args.out.write_text(json.dumps(results, indent=1))


if __name__ == "__main__":
    main()
