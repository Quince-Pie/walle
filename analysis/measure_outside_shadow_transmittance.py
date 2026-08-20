#!/usr/bin/env python3
"""Apple's shadow outside the element, read as a transmittance profile.

Outside the reveal there is no material, no lens, no refraction and no
bleed - the screen is the outgoing wallpaper with the element's shadow laid
over it.  That makes the shadow the only unknown in the region, and it can
be read without a model as the ratio

    T(x) = frame(x) / outgoing(x)

per channel, binned by distance OUTSIDE the boundary.  `clear` carries
shadow.opacity 0.0 and sits at the dither floor there (0.380 rms), which is
the control that the method is sound; `regular` carries 0.25 and reads
0.939 light / 0.740 dark, together 6.1% of the whole budget - the largest
single cell left that is not interior material.

Apple's decoded shadow parameters for the reveal's size are
shadow.opacity 0.25, offset.height 8 pt (walle ships this since session
200), shadowRadius 24, blurRadius and an ycc of its own whose saturation
differs by appearance (1.80 light against 1.00 dark) - so the shadow is NOT
a neutral darkening and a per-channel read is required rather than a luma
one.

Reports Apple's and walle's transmittance side by side against depth
outside the boundary, per channel, so a difference in amplitude, in reach,
or in colour shows up as its own number.

Usage: measure_outside_shadow_transmittance.py --capture <lgcap>
           --work <work dir> [--variant regular] [--out json]
"""
import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image

CENTRE = (512.0, 614.4)
BANDS = [(0, 4), (4, 10), (10, 18), (18, 30), (30, 46), (46, 70),
         (70, 110), (110, 170), (170, 260)]


def load_bgra(path, extent):
    raw = np.fromfile(path, dtype=np.uint8)
    return raw.reshape(extent, extent, 4)[..., [2, 1, 0]].astype(np.float64)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--capture", type=Path, required=True)
    ap.add_argument("--work", type=Path, required=True)
    ap.add_argument("--variant", default="regular")
    ap.add_argument("--extent", type=int, default=2048)
    ap.add_argument("--states", type=int, nargs="*", default=[6, 8, 10, 12])
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()

    extent = args.extent
    manifest = json.loads((args.capture / "manifest.json").read_text())
    yy, xx = np.mgrid[0:extent, 0:extent].astype(float)
    distance = np.hypot(xx - CENTRE[0], yy - CENTRE[1])
    results = {}

    for appearance in ("light", "dark"):
        sequence = f"sweep__wallpaper-transition__{args.variant}__{appearance}"
        sweep = next((s for s in manifest["sweepSequences"]
                      if s["id"] == sequence), None)
        if sweep is None:
            continue
        frames = [f for f in sweep["frames"] if f.get("stable", True)]
        outgoing = np.asarray(Image.open(
            args.capture / "reference" / f"{sweep['outgoingBackground']}.png"
        ).convert("RGB")).astype(float)
        # only pixels bright enough for a ratio to be meaningful
        usable = outgoing.min(axis=2) > 24.0

        acc = {b: [np.zeros(3), np.zeros(3), 0] for b in BANDS}
        for index in args.states:
            walle_path = args.work / sequence / f"composition-state-{index:04d}.bgra"
            mask_path = args.work / sequence / f"state-{index:04d}.r8"
            if not walle_path.exists() or index >= len(frames):
                continue
            mask = np.fromfile(mask_path, dtype=np.uint8).reshape(extent, extent)
            if not (mask > 0).any():
                continue
            radius = distance[mask > 0].max()
            out_depth = distance - radius          # positive OUTSIDE
            apple = np.asarray(Image.open(args.capture / frames[index]["file"]
                                          ).convert("RGB")).astype(float)
            walle = load_bgra(walle_path, extent)
            for lo, hi in BANDS:
                sel = ((mask == 0) & (out_depth >= lo) & (out_depth < hi) & usable)
                n = int(sel.sum())
                if n < 3000:
                    continue
                ta = (apple[sel] / outgoing[sel]).sum(axis=0)
                tw = (walle[sel] / outgoing[sel]).sum(axis=0)
                e = acc[(lo, hi)]
                e[0] += ta
                e[1] += tw
                e[2] += n

        print(f"== {args.variant}/{appearance}: shadow transmittance outside the "
              f"boundary (1.0 = untouched)")
        print("   %-12s %25s | %25s | %s" %
              ("outside px", "apple  R/G/B", "walle  R/G/B", "walle-apple"))
        rows = []
        for lo, hi in BANDS:
            sa, sw, n = acc[(lo, hi)]
            if not n:
                continue
            a, w = sa / n, sw / n
            print("   %-12s %8.4f%8.4f%8.4f | %8.4f%8.4f%8.4f | %+7.4f" %
                  (f"{lo}-{hi}", *a, *w, float((w - a).mean())))
            rows.append({"lo": lo, "hi": hi, "apple": a.tolist(),
                         "walle": w.tolist(), "n": n})
        results[appearance] = rows

    if args.out:
        args.out.write_text(json.dumps(results, indent=1))


if __name__ == "__main__":
    main()
