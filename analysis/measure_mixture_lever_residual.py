#!/usr/bin/env python3
"""Is what is left keyed on the mixture's own lever arm, `narrow - wide`?

Three readings point the same way once the chroma seam is corrected:

  * the deep-interior residual is largest where the wallpaper is locally
    FLAT (coded 2.18 rms at detail 0-3 against 1.37 at 8-16), so it is not
    a blur-kernel mismatch - a kernel error would grow with detail, not
    shrink;
  * a locally flat patch still sits in a structured surround, so its narrow
    field (sigma 14) equals the patch colour while its wide field (sigma
    330) does not.  Flatness at the narrow scale is exactly the condition
    under which the two fields disagree MOST;
  * every colour-law candidate has now failed to transport - a global
    chroma scale (captures want 0.979 to 1.058) and a chroma-magnitude
    dependent one (coded wants 0.897 light and 1.142 dark on the SAME
    content, while the saturated pairs, whose chroma is far more extreme,
    want 0.98 to 1.03).  A law that the most saturated content does not
    want is not a saturation law.

So this bins the deep residual by |narrow - wide| directly.  walle mixes
those two fields with one luma weight and one chroma weight per appearance;
if either is wrong the error must grow with the separation between them and
vanish where they agree, on EVERY capture, because the weights are material
constants.  A residual that is flat in the lever arm exonerates the mixture
and sends the search elsewhere.

Only depth >= 450 px is used so the screen-backdrop seam term, which is
already corrected and decays with depth, cannot contribute.

Usage: measure_mixture_lever_residual.py --captures <lgcap>=<workdir> ...
           [--cache dir] [--out json]
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
NARROW_SIGMA, WIDE_SIGMA = 14.188, 329.807
BINS = [(0, 4), (4, 9), (9, 16), (16, 26), (26, 40), (40, 60), (60, 90), (90, 140)]


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


def fields(capture: Path, name: str, cache: Path, extent: int):
    """(narrow, wide) in panel code space, cached to disk - the wide blur is
    2455 taps and gets recomputed otherwise."""
    narrow_path, wide_path = cache / f"{name}.narrow.npy", cache / f"{name}.npy"
    cache.mkdir(exist_ok=True)
    narrow = None
    if narrow_path.exists():
        narrow = np.load(narrow_path).astype(float)
    else:
        image = np.asarray(Image.open(capture / "reference" / f"{name}.png"
                                      ).convert("RGB")).astype(float) / 255.0
        panel = srgb_encode(np.clip(srgb_decode(image) @ TO_PANEL.T, 0, None))
        narrow = separable(panel, gaussian_taps(NARROW_SIGMA))
        np.save(narrow_path, narrow.astype(np.float32))
    # the wide field is 2455 taps; reuse whatever an earlier run left behind
    if wide_path.exists():
        return narrow, np.load(wide_path).astype(float)
    wide = separable(narrow, gaussian_taps(WIDE_SIGMA))
    np.save(wide_path, wide.astype(np.float32))
    return narrow, wide


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--captures", nargs="+", required=True)
    ap.add_argument("--cache", type=Path, required=True)
    ap.add_argument("--extent", type=int, default=2048)
    ap.add_argument("--min-depth", type=float, default=450.0)
    ap.add_argument("--stride", type=int, default=2)
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()

    extent = args.extent
    yy, xx = np.mgrid[0:extent, 0:extent].astype(float)
    distance = np.hypot(xx - CENTRE[0], yy - CENTRE[1])
    stride = np.zeros(distance.shape, bool)
    stride[::args.stride, ::args.stride] = True
    results = {}

    print(f"deep residual (depth >= {args.min_depth:.0f} px) against the mixture's "
          f"lever arm |narrow - wide|, in output codes")
    header = " ".join("%8s" % f"{lo}-{hi}" for lo, hi in BINS)
    print("   %-22s %s" % ("capture/appearance", header))
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
            narrow, wide = fields(capture, sweep["incomingBackground"],
                                  args.cache, extent)
            # lever arm in OUTPUT code units so the bins are readable
            lever = np.linalg.norm((narrow - wide) * 255.0, axis=2)
            acc = {b: [0.0, 0] for b in BINS}
            for index in range(6, 15):
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
                raw = np.fromfile(walle_path, dtype=np.uint8)
                walle = raw.reshape(extent, extent, 4)[..., [2, 1, 0]].astype(float)
                residual = apple - walle
                for lo, hi in BINS:
                    sel = ((mask == 255) & (depth >= args.min_depth)
                           & (lever >= lo) & (lever < hi) & stride)
                    n = int(sel.sum())
                    if n < 4000:
                        continue
                    e = acc[(lo, hi)]
                    e[0] += float((residual[sel] ** 2).sum())
                    e[1] += n * 3
            row, cells = [], {}
            for lo, hi in BINS:
                total, n = acc[(lo, hi)]
                if n:
                    value = sqrt(total / n)
                    row.append("%8.2f" % value)
                    cells[f"{lo}-{hi}"] = value
                else:
                    row.append("%8s" % ".")
            print("   %-22s %s" % (f"{capture.name}/{appearance}", " ".join(row)))
            results[f"{capture.name}/{appearance}"] = cells

    if args.out:
        args.out.write_text(json.dumps(results, indent=1))


if __name__ == "__main__":
    main()
