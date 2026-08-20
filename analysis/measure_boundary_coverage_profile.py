#!/usr/bin/env python3
"""Measure Apple's boundary coverage ramp, on `clear`, where nothing else moves.

After the screen-composited backdrop landed, `clear`'s interior is at parity
(0.43 rms, flat from 8 px inward) while its ANTIALIASED BOUNDARY RING still
reads 9.13 rms - as bad as `regular`'s 8.34.  An error that is identical in
the variant that is otherwise exact and in the one that is not cannot be a
material error: it is the coverage geometry.

Coverage is separable from content without any model.  Outside the element
the frame IS the outgoing wallpaper, so

    D(r) = < |frame(r, theta) - outgoing(r, theta)| >_theta

is zero outside, rises across the ramp, and plateaus inside, with the
content averaged away by the angular mean.  Normalising by the plateau
gives the coverage ramp alpha(r) directly, for Apple and for walle, on the
same axis.  Their difference is the whole defect: a sub-pixel radius offset
shows as a shift, a different filter shows as a width or shape change.

Both are read at 0.25 px resolution and the shift is estimated by matching
the half-coverage crossing, which is insensitive to the ramp's shape.

Usage: measure_boundary_coverage_profile.py --capture <lgcap dir>
           --work <scorer work dir> [--variant clear] [--out json]
"""
import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image

CENTRE = (512.0, 614.4)
STEP = 0.25
SPAN = 14.0


def load_bgra(path: Path, extent: int) -> np.ndarray:
    raw = np.fromfile(path, dtype=np.uint8)
    return raw.reshape(extent, extent, 4)[..., [2, 1, 0]].astype(np.float64)


def ramp(frame, outgoing, distance, radius, sel):
    """Angular-mean departure-from-outgoing profile on a 0.25 px radial axis."""
    edges = np.arange(radius - SPAN, radius + SPAN + STEP, STEP)
    d = np.abs(frame - outgoing).mean(axis=2)[sel]
    r = distance[sel]
    index = np.digitize(r, edges) - 1
    total = np.bincount(index, weights=d, minlength=len(edges) - 1)
    count = np.bincount(index, minlength=len(edges) - 1).astype(float)
    valid = count[:len(edges) - 1] > 30
    profile = np.where(valid, total[:len(edges) - 1] / np.maximum(count[:len(edges) - 1], 1), np.nan)
    centres = 0.5 * (edges[:-1] + edges[1:])
    return centres, profile


def crossing(centres, profile, level):
    """Sub-pixel radius at which the profile falls through `level`, outward."""
    finite = np.isfinite(profile)
    c, p = centres[finite], profile[finite]
    above = np.where(p >= level)[0]
    if not len(above):
        return None
    i = above[-1]
    if i + 1 >= len(p):
        return None
    lo, hi = p[i], p[i + 1]
    if lo == hi:
        return float(c[i])
    return float(c[i] + (lo - level) / (lo - hi) * (c[i + 1] - c[i]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--capture", type=Path, required=True)
    ap.add_argument("--work", type=Path, required=True)
    ap.add_argument("--variant", default="clear")
    ap.add_argument("--appearance", default="light")
    ap.add_argument("--extent", type=int, default=2048)
    ap.add_argument("--states", type=int, nargs="*")
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()

    manifest = json.loads((args.capture / "manifest.json").read_text())
    sequence = f"sweep__wallpaper-transition__{args.variant}__{args.appearance}"
    sweep = next(s for s in manifest["sweepSequences"] if s["id"] == sequence)
    frames = [f for f in sweep["frames"] if f.get("stable", True)]
    outgoing = np.asarray(Image.open(
        args.capture / "reference" / f"{sweep['outgoingBackground']}.png").convert("RGB")).astype(float)

    yy, xx = np.mgrid[0:args.extent, 0:args.extent].astype(float)
    distance = np.hypot(xx - CENTRE[0], yy - CENTRE[1])

    print(f"== {args.variant}/{args.appearance}: boundary coverage ramp, "
          f"Apple vs walle (capture px)")
    print("   %-6s %9s | %9s %9s %8s | %7s %7s %7s" %
          ("state", "R(mask)", "R50 apple", "R50 walle", "shift",
           "w apple", "w walle", "ramp rms"))
    rows = []
    for index, frame in enumerate(frames):
        if args.states and index not in args.states:
            continue
        walle_path = args.work / sequence / f"composition-state-{index:04d}.bgra"
        mask_path = args.work / sequence / f"state-{index:04d}.r8"
        if not walle_path.exists() or not mask_path.exists():
            continue
        mask = np.fromfile(mask_path, dtype=np.uint8).reshape(args.extent, args.extent)
        if not (mask > 0).any():
            continue
        radius = float(distance[mask > 0].max())
        if radius < 200 or radius > args.extent * 0.95:
            continue
        sel = np.abs(distance - radius) < SPAN
        apple = np.asarray(Image.open(args.capture / frame["file"]).convert("RGB")).astype(float)
        walle = load_bgra(walle_path, args.extent)
        centres, pa = ramp(apple, outgoing, distance, radius, sel)
        _, pw = ramp(walle, outgoing, distance, radius, sel)
        inside = centres < radius - 6
        plateau_a = np.nanmean(pa[inside])
        plateau_w = np.nanmean(pw[inside])
        if not np.isfinite(plateau_a) or plateau_a < 4:
            continue
        na, nw = pa / plateau_a, pw / plateau_w
        r50a, r50w = crossing(centres, na, 0.5), crossing(centres, nw, 0.5)
        # 10-90 ramp width
        wa = (crossing(centres, na, 0.1) or 0) - (crossing(centres, na, 0.9) or 0)
        ww = (crossing(centres, nw, 0.1) or 0) - (crossing(centres, nw, 0.9) or 0)
        if r50a is None or r50w is None:
            continue
        shift = r50w - r50a
        band = np.isfinite(na) & np.isfinite(nw) & (np.abs(centres - radius) < 8)
        rms = float(np.sqrt(np.nanmean((na[band] - nw[band]) ** 2)) * 100)
        print("   %-6d %9.2f | %9.2f %9.2f %+8.3f | %7.2f %7.2f %7.1f%%" %
              (index, radius, r50a, r50w, shift, wa, ww, rms))
        rows.append({"state": index, "radius": radius, "r50Apple": r50a,
                     "r50Walle": r50w, "shift": shift, "widthApple": wa,
                     "widthWalle": ww, "rampRmsPercent": rms})

    if rows:
        shifts = np.array([r["shift"] for r in rows])
        print(f"   mean shift {shifts.mean():+.3f} px  (sd {shifts.std():.3f}) "
              f"- walle's half-coverage radius vs Apple's")
        print(f"   mean 10-90 width: apple {np.mean([r['widthApple'] for r in rows]):.2f} px, "
              f"walle {np.mean([r['widthWalle'] for r in rows]):.2f} px")
    if args.out:
        args.out.write_text(json.dumps(rows, indent=1))


if __name__ == "__main__":
    main()
