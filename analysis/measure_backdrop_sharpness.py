#!/usr/bin/env python3
"""Does Apple's backdrop keep more detail than walle's fully blurred one?

`blur.opacities` decodes as (1.0, 0.5, 0.5, 1.0, 1.0) on every case and both
variants, paired with `blur.distances` of (-size/2, -1, 0, 0).  Read as a
depth-keyed profile - the shape `tinting.distances` and
`refraction.outerDistances` also have - that says the blur layer does not
run at full strength everywhere.  A blur composited at opacity below one is
the blurred field mixed back with the SHARP one, which leaves more
high-frequency detail in the backdrop than walle's single fully-blurred
field can carry.

That is worth testing because it is the right shape for the unexplained
floor.  The surviving residual is flat in the mixture's lever arm, flat in
local detail, unexplained by every colour law tried, and present at every
depth - which is what a systematic sharpness difference looks like, since
it rides on whatever structure the wallpaper happens to have.

The test needs no model.  A Laplacian high-pass measures how much detail
survives into the frame, and Apple's and walle's frames are measured the
same way at the same pixels, so the transfer, the mixture and the material
cancel out of the COMPARISON even though none of them is known exactly.
`clear`, whose interior is already at the dither floor, is the control: if
the instrument reports a sharpness gap there too, it is measuring noise
rather than blur.

Reports high-pass energy for both, and their ratio, against depth.  A ratio
above one says Apple keeps detail walle has blurred away.

Usage: measure_backdrop_sharpness.py --capture <lgcap> --work <dir>
           [--variant regular] [--out json]
"""
import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image

CENTRE = (512.0, 614.4)
LUMA = np.array([0.2126, 0.7152, 0.0722])
BANDS = [(20, 60), (60, 140), (140, 260), (260, 450), (450, 750), (750, 1200)]


def load_bgra(path, extent):
    raw = np.fromfile(path, dtype=np.uint8)
    return raw.reshape(extent, extent, 4)[..., [2, 1, 0]].astype(np.float64)


def highpass(plane):
    """4-neighbour Laplacian; zero on any locally linear field, so it sees
    detail rather than gradient."""
    p = np.pad(plane, 1, mode="edge")
    return (4.0 * p[1:-1, 1:-1] - p[:-2, 1:-1] - p[2:, 1:-1]
            - p[1:-1, :-2] - p[1:-1, 2:])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--capture", type=Path, required=True)
    ap.add_argument("--work", type=Path, required=True)
    ap.add_argument("--variants", nargs="*", default=["regular", "clear"])
    ap.add_argument("--extent", type=int, default=2048)
    ap.add_argument("--states", type=int, nargs="*", default=[8, 10, 12])
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()

    extent = args.extent
    manifest = json.loads((args.capture / "manifest.json").read_text())
    yy, xx = np.mgrid[0:extent, 0:extent].astype(float)
    distance = np.hypot(xx - CENTRE[0], yy - CENTRE[1])
    results = {}

    header = " ".join("%15s" % f"{lo}-{hi}" for lo, hi in BANDS)
    print("high-pass energy inside the element, apple / walle (ratio > 1 = Apple "
          "keeps detail walle blurred away)")
    print("   %-22s %s" % ("variant/appearance", header))
    for variant in args.variants:
        for appearance in ("light", "dark"):
            sequence = f"sweep__wallpaper-transition__{variant}__{appearance}"
            sweep = next((s for s in manifest["sweepSequences"]
                          if s["id"] == sequence), None)
            if sweep is None:
                continue
            frames = [f for f in sweep["frames"] if f.get("stable", True)]
            acc = {b: [0.0, 0.0, 0] for b in BANDS}
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
                ha = highpass(apple @ LUMA)
                hw = highpass(walle @ LUMA)
                for lo, hi in BANDS:
                    sel = (mask == 255) & (depth >= lo) & (depth < hi)
                    n = int(sel.sum())
                    if n < 20000:
                        continue
                    e = acc[(lo, hi)]
                    e[0] += float((ha[sel] ** 2).sum())
                    e[1] += float((hw[sel] ** 2).sum())
                    e[2] += n
            row, cells = [], {}
            for lo, hi in BANDS:
                a, w, n = acc[(lo, hi)]
                if not n or w <= 0:
                    row.append("%15s" % ".")
                    continue
                ra, rw = np.sqrt(a / n), np.sqrt(w / n)
                row.append("%6.2f/%5.2f=%.2f" % (ra, rw, ra / rw))
                cells[f"{lo}-{hi}"] = {"apple": ra, "walle": rw, "ratio": ra / rw}
            print("   %-22s %s" % (f"{variant}/{appearance}", " ".join(row)))
            results[f"{variant}/{appearance}"] = cells

    if args.out:
        args.out.write_text(json.dumps(results, indent=1))


if __name__ == "__main__":
    main()
