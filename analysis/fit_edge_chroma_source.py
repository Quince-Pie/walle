#!/usr/bin/env python3
"""What is the near-edge chroma tint made of?

The surviving `regular` residual is a luma-neutral chromatic offset that
decays inward - +10.0 codes of red at the boundary, 0.6 at 1100 px, and
identically zero for `clear`.  Fitted as a free per-depth tint it explains
the chroma with R^2 0.91-0.99, but a free tint is three numbers per depth
bin with no mechanism behind them, and a fixed RGB vector cannot be right:
it would be an artefact of THIS wallpaper's average colour.

The mechanism that predicts a decaying tint is the edgeBleed layer, which
composites the WIDE-blurred backdrop - whose chroma is the region's average
chroma - with a weight that falls off away from the boundary.  That model
says the correction is not a constant but

    residual_chroma(d) = k(d) * chroma(wide field)

with a single scalar k per depth, and it makes a prediction a fitted tint
does not: k(d) is a property of the material, so the SAME k must work on a
different wallpaper whose average chroma points somewhere else.

Four candidate sources are regressed against the same residual - a free
tint, and scalar multiples of the wide, narrow and mixed backdrops' chroma -
and each is scored on the coded sweeps and again on the natural-wallpaper
holdout.  A source that only works where it was fitted is the wallpaper's
colour, not Apple's law.

Usage: fit_edge_chroma_source.py --capture <lgcap> --work <work dir>
           [--holdout-capture <lgcap>] [--holdout-work <dir>] [--out json]
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
BANDS = [(0, 20), (20, 50), (50, 120), (120, 250), (250, 450),
         (450, 700), (700, 1100), (1100, 1800)]


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


def load_bgra(path, extent):
    raw = np.fromfile(path, dtype=np.uint8)
    return raw.reshape(extent, extent, 4)[..., [2, 1, 0]].astype(np.float64)


def chroma(rgb):
    return rgb - (rgb @ LUMA)[..., None]


def gather(capture, work, appearance, states, stride, extent):
    """Residual chroma and candidate source chromas, per depth band."""
    manifest = json.loads((capture / "manifest.json").read_text())
    sequence = "sweep__wallpaper-transition__regular__" + appearance
    sweep = next((s for s in manifest["sweepSequences"] if s["id"] == sequence), None)
    if sweep is None:
        return {}
    frames = [f for f in sweep["frames"] if f.get("stable", True)]
    incoming = np.asarray(Image.open(
        capture / "reference" / f"{sweep['incomingBackground']}.png"
    ).convert("RGB")).astype(float) / 255.0
    scale = extent / 2048.0
    panel = srgb_encode(np.clip(srgb_decode(incoming) @ TO_PANEL.T, 0, None))
    narrow = separable(panel, gaussian_taps(NARROW_SIGMA * scale))
    wide = separable(narrow, gaussian_taps(WIDE_SIGMA * scale))
    w_luma, w_chroma = MIX[appearance]
    mix = (w_chroma * narrow + (1 - w_chroma) * wide
           + ((w_luma - w_chroma) * ((narrow - wide) @ LUMA))[..., None])
    # sources in output (sRGB code) space, scaled to codes
    sources = {
        "wide": chroma(srgb_encode(np.clip(srgb_decode(np.clip(wide, 0, 1))
                                           @ FROM_PANEL.T, 0, 1)) * 255.0),
        "narrow": chroma(srgb_encode(np.clip(srgb_decode(np.clip(narrow, 0, 1))
                                             @ FROM_PANEL.T, 0, 1)) * 255.0),
        "mix": chroma(srgb_encode(np.clip(srgb_decode(np.clip(mix, 0, 1))
                                          @ FROM_PANEL.T, 0, 1)) * 255.0),
    }
    yy, xx = np.mgrid[0:extent, 0:extent].astype(float)
    distance = np.hypot(xx - CENTRE[0] * scale, yy - CENTRE[1] * scale)
    stride_mask = np.zeros(distance.shape, bool)
    stride_mask[::stride, ::stride] = True

    out = {b: {"r": [], **{k: [] for k in sources}} for b in BANDS}
    for index in states:
        walle_path = work / sequence / f"composition-state-{index:04d}.bgra"
        mask_path = work / sequence / f"state-{index:04d}.r8"
        if not walle_path.exists() or index >= len(frames):
            continue
        mask = np.fromfile(mask_path, dtype=np.uint8).reshape(extent, extent)
        if not (mask > 0).any():
            continue
        radius = distance[mask > 0].max()
        depth = radius - distance
        apple = np.asarray(Image.open(capture / frames[index]["file"]
                                      ).convert("RGB")).astype(float)
        residual = chroma(apple) - chroma(load_bgra(walle_path, extent))
        for lo, hi in BANDS:
            sel = ((mask == 255) & (depth >= lo * scale)
                   & (depth < hi * scale) & stride_mask)
            if int(sel.sum()) < 2000:
                continue
            out[(lo, hi)]["r"].append(residual[sel])
            for name, field in sources.items():
                out[(lo, hi)][name].append(field[sel])
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--capture", type=Path, required=True)
    ap.add_argument("--work", type=Path, required=True)
    ap.add_argument("--holdout-capture", type=Path)
    ap.add_argument("--holdout-work", type=Path)
    ap.add_argument("--extent", type=int, default=2048)
    ap.add_argument("--holdout-extent", type=int, default=1024)
    ap.add_argument("--states", type=int, nargs="*",
                    default=[6, 7, 8, 9, 10, 11, 12, 13, 14])
    ap.add_argument("--stride", type=int, default=3)
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()

    results = {}
    for appearance in ("light", "dark"):
        fit = gather(args.capture, args.work, appearance, args.states,
                     args.stride, args.extent)
        hold = ({} if not args.holdout_capture else
                gather(args.holdout_capture, args.holdout_work, appearance,
                       args.states, args.stride, args.holdout_extent))
        print(f"== regular/{appearance}: source of the near-edge chroma tint")
        print("   %-14s %28s | %28s" % ("", "fitted on coded (rms codes)",
                                        "applied to natural holdout"))
        print("   %-14s %6s %6s %6s %6s | %6s %6s %6s %6s" %
              ("depth (px)", "none", "tint", "k*wide", "k*mix",
               "none", "tint", "k*wide", "k*mix"))
        rows = []
        for lo, hi in BANDS:
            if not fit.get((lo, hi), {}).get("r"):
                continue
            r = np.concatenate(fit[(lo, hi)]["r"])
            base = sqrt((r ** 2).mean())
            tint = r.mean(axis=0)
            models = {"tint": sqrt(((r - tint) ** 2).mean())}
            ks = {}
            for name in ("wide", "mix"):
                s = np.concatenate(fit[(lo, hi)][name])
                k = float((s * r).sum() / (s * s).sum()) if (s * s).sum() > 0 else 0.0
                ks[name] = k
                models[name] = sqrt(((r - k * s) ** 2).mean())
            cells = [base, models["tint"], models["wide"], models["mix"]]
            hcells = [float("nan")] * 4
            if hold.get((lo, hi), {}).get("r"):
                hr = np.concatenate(hold[(lo, hi)]["r"])
                hcells[0] = sqrt((hr ** 2).mean())
                hcells[1] = sqrt(((hr - tint) ** 2).mean())
                for i, name in enumerate(("wide", "mix")):
                    hs = np.concatenate(hold[(lo, hi)][name])
                    hcells[2 + i] = sqrt(((hr - ks[name] * hs) ** 2).mean())
            print("   %-14s %6.2f %6.2f %6.2f %6.2f | %6.2f %6.2f %6.2f %6.2f" %
                  (f"{lo}-{hi}", *cells, *hcells))
            rows.append({"lo": lo, "hi": hi, "kWide": ks["wide"], "kMix": ks["mix"],
                         "tint": tint.tolist(), "fit": cells, "holdout": hcells})
        results[appearance] = rows

    if args.out:
        args.out.write_text(json.dumps(results, indent=1))


if __name__ == "__main__":
    main()
