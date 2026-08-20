#!/usr/bin/env python3
"""Where does the transition score actually live?

Every mechanism campaign so far has been aimed by a residual PROFILE - rms
against depth, or against angle - and a profile says where the error is
LARGE, not where the score is.  Those are different questions whenever the
large-error region is small: the antialiased boundary ring reads 8-11 rms
in every variant, five times the interior, and contributes about six per
cent of the total because it is one pixel wide.

This reports the budget instead: each cell's share of total squared error,
which is what the referee sums.  A mechanism can only be worth its risk if
the cells it touches add up to something.  Cells are (sequence x depth
band), plus the boundary ring broken out separately, since the ring is
geometry and the interior is material.

Prints the cells in descending order of contribution, with the running
cumulative share, so the answer to "what would I have to fix to halve the
score" is read straight off the table.

Usage: error_budget.py --capture <lgcap dir> --work <scorer work dir>
           [--out json]
"""
import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image

CENTRE = (512.0, 614.4)
BANDS = [(0, 8), (8, 20), (20, 50), (50, 120), (120, 250),
         (250, 450), (450, 700), (700, 1100), (1100, 4000)]


def load_bgra(path: Path, extent: int) -> np.ndarray:
    raw = np.fromfile(path, dtype=np.uint8)
    return raw.reshape(extent, extent, 4)[..., [2, 1, 0]].astype(np.float64)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--capture", type=Path, required=True)
    ap.add_argument("--work", type=Path, required=True)
    ap.add_argument("--extent", type=int, default=2048)
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()

    manifest = json.loads((args.capture / "manifest.json").read_text())
    yy, xx = np.mgrid[0:args.extent, 0:args.extent].astype(float)
    distance = np.hypot(xx - CENTRE[0], yy - CENTRE[1])

    cells = {}
    grand_total, grand_count = 0.0, 0
    for sweep in manifest["sweepSequences"]:
        sequence = sweep["id"]
        if not (args.work / sequence).is_dir():
            continue
        frames = [f for f in sweep["frames"] if f.get("stable", True)]
        for index, frame in enumerate(frames):
            walle_path = args.work / sequence / f"composition-state-{index:04d}.bgra"
            mask_path = args.work / sequence / f"state-{index:04d}.r8"
            if not walle_path.exists() or not mask_path.exists():
                continue
            mask = np.fromfile(mask_path, dtype=np.uint8).reshape(args.extent, args.extent)
            if not (mask > 0).any():
                continue
            radius = float(distance[mask > 0].max())
            depth = radius - distance
            apple = np.asarray(Image.open(args.capture / frame["file"]
                                          ).convert("RGB")).astype(float)
            walle = load_bgra(walle_path, args.extent)
            squared = ((apple - walle) ** 2).sum(axis=2)
            # the scorer counts every pixel of the frame, inside and out
            grand_total += float(squared.sum())
            grand_count += squared.size * 3

            ring = (mask > 0) & (mask < 255)
            key = (sequence, "boundary ring")
            total, count = cells.get(key, (0.0, 0))
            cells[key] = (total + float(squared[ring].sum()), count + int(ring.sum()) * 3)

            outside = mask == 0
            key = (sequence, "outside element")
            total, count = cells.get(key, (0.0, 0))
            cells[key] = (total + float(squared[outside].sum()), count + int(outside.sum()) * 3)

            solid = mask == 255
            for lo, hi in BANDS:
                sel = solid & (depth >= lo) & (depth < hi)
                n = int(sel.sum())
                if not n:
                    continue
                key = (sequence, f"depth {lo}-{hi}")
                total, count = cells.get(key, (0.0, 0))
                cells[key] = (total + float(squared[sel].sum()), count + n * 3)

    if not grand_count:
        print("no renders found in", args.work)
        return
    print(f"total rms {np.sqrt(grand_total / grand_count):.4f} codes "
          f"over {grand_count // 3} pixel samples")
    print()
    print("   %-46s %8s %9s %8s %8s" % ("cell", "rms", "share", "cumul", "Mpx"))
    order = sorted(cells.items(), key=lambda kv: -kv[1][0])
    cumulative = 0.0
    rows = []
    for (sequence, band), (total, count) in order:
        if not count:
            continue
        share = total / grand_total
        cumulative += share
        label = sequence.replace("sweep__wallpaper-transition__", "")
        print("   %-46s %8.3f %8.1f%% %7.1f%% %8.2f" %
              (f"{label}  {band}", np.sqrt(total / count), share * 100,
               cumulative * 100, count / 3e6))
        rows.append({"sequence": sequence, "band": band,
                     "rms": np.sqrt(total / count), "share": share,
                     "pixels": count // 3})
        if cumulative > 0.97:
            break
    if args.out:
        args.out.write_text(json.dumps(
            {"totalRms": np.sqrt(grand_total / grand_count), "cells": rows}, indent=1))


if __name__ == "__main__":
    main()
