#!/usr/bin/env python3
"""Read the mixture error as a surface instead of guessing its form.

The deep residual is flat where `narrow` and `wide` agree and about three
times larger where they disagree, on every real capture.  Four candidate
laws for that have now been proposed and falsified, each by assuming a
shape and fitting its constants.  This stops assuming.

walle's backdrop is a function of exactly two fields.  So bin the deep
interior on the plane (luma(narrow), luma(wide)) and report the mean
residual in each cell.  The surface says what is wrong without a model:

  * a residual that varies only ALONG the diagonal, where the two fields
    agree, is the transfer - it is a function of the backdrop level alone;
  * a residual that varies only ACROSS the diagonal is the mixture weight -
    it is a function of narrow minus wide;
  * curvature across the diagonal is a nonlinear mixture, which no reweighting
    of two fields can fix;
  * a surface that is flat in both directions means the mixture and the
    transfer are both right and the error is somewhere else entirely.

Cells are reported with their sample counts, because the corners of this
plane are sparse - the two fields are strongly correlated by construction -
and a mean over a hundred pixels is not evidence.

Usage: measure_mixture_surface.py --captures <lgcap>=<workdir> ...
           --cache <dir> [--out json]
"""
import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image

CENTRE = (512.0, 614.4)
LUMA = np.array([0.2126, 0.7152, 0.0722])
EDGES = np.array([0.0, 0.30, 0.42, 0.52, 0.60, 0.68, 0.76, 0.86, 1.01])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--captures", nargs="+", required=True)
    ap.add_argument("--cache", type=Path, required=True)
    ap.add_argument("--extent", type=int, default=2048)
    ap.add_argument("--min-depth", type=float, default=450.0)
    ap.add_argument("--stride", type=int, default=2)
    ap.add_argument("--min-samples", type=int, default=3000)
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()

    extent = args.extent
    yy, xx = np.mgrid[0:extent, 0:extent].astype(float)
    distance = np.hypot(xx - CENTRE[0], yy - CENTRE[1])
    stride = np.zeros(distance.shape, bool)
    stride[::args.stride, ::args.stride] = True
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
            name = sweep["incomingBackground"]
            narrow = np.load(args.cache / f"{name}.narrow.npy").astype(float) @ LUMA
            wide = np.load(args.cache / f"{name}.npy").astype(float) @ LUMA
            ni = np.digitize(narrow, EDGES) - 1
            wi = np.digitize(wide, EDGES) - 1

            bins = len(EDGES) - 1
            total = np.zeros((bins, bins))
            count = np.zeros((bins, bins), dtype=np.int64)
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
                sel = (mask == 255) & (depth >= args.min_depth) & stride
                if not sel.any():
                    continue
                apple = np.asarray(Image.open(capture / frames[index]["file"]
                                              ).convert("RGB")).astype(float)
                raw = np.fromfile(walle_path, dtype=np.uint8)
                walle = raw.reshape(extent, extent, 4)[..., [2, 1, 0]].astype(float)
                residual = ((apple - walle) @ LUMA)[sel]
                a, b = ni[sel], wi[sel]
                ok = (a >= 0) & (a < bins) & (b >= 0) & (b < bins)
                np.add.at(total, (a[ok], b[ok]), residual[ok])
                np.add.at(count, (a[ok], b[ok]), 1)

            key = f"{capture.name}/{appearance}"
            print(f"== {key}: mean luma residual on (narrow, wide), depth >= "
                  f"{args.min_depth:.0f} px")
            print("        wide ->  " + " ".join(
                "%7.2f" % (0.5 * (EDGES[j] + EDGES[j + 1])) for j in range(bins)))
            grid = []
            for i in range(bins):
                cells, row = [], []
                for j in range(bins):
                    if count[i, j] >= args.min_samples:
                        value = total[i, j] / count[i, j]
                        cells.append("%7.2f" % value)
                        row.append(round(float(value), 3))
                    else:
                        cells.append("      .")
                        row.append(None)
                print("   narrow %5.2f  %s" %
                      (0.5 * (EDGES[i] + EDGES[i + 1]), " ".join(cells)))
                grid.append(row)
            # separate the two directions: along the diagonal vs across it
            diag = [grid[i][i] for i in range(bins) if grid[i][i] is not None]
            off = [grid[i][j] for i in range(bins) for j in range(bins)
                   if i != j and grid[i][j] is not None]
            if diag:
                print("   along the diagonal (narrow == wide): "
                      f"{min(diag):+.2f} .. {max(diag):+.2f}  "
                      f"(spread {max(diag) - min(diag):.2f})")
            if off:
                print("   off the diagonal                   : "
                      f"{min(off):+.2f} .. {max(off):+.2f}  "
                      f"(spread {max(off) - min(off):.2f})")
            results[key] = {"edges": EDGES.tolist(), "grid": grid,
                            "counts": count.tolist()}

    if args.out:
        args.out.write_text(json.dumps(results, indent=1))


if __name__ == "__main__":
    main()
