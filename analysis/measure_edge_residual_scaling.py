#!/usr/bin/env python3
"""Does the near-edge residual scale with the element, or is it a fixed rim?

Apple's decoded `Material.Context` table says `refraction` has TWO lobes:

    innerHeight = min(20, size * 0.03125)      innerAmount = -3 * innerHeight
    outerHeight = size * 0.125                 outerAmount =  1.6 * outerHeight
    outerOpacity = 0.30 for `regular`, 0.00 for `clear`

The inner lobe saturates at 20 pt and is what walle already models as its
lens band.  The outer lobe does NOT saturate - it grows without limit with
the element - and it is switched OFF for `clear` by its opacity.  walle
models no such thing.

That is exactly the shape of the residual left after the screen-composited
backdrop landed: `clear` is at parity everywhere (|mean| <= 0.33 codes),
while `regular` is systematically too dark over a band inside the boundary.

This instrument decides between the two readings WITHOUT fitting anything:

  * a fixed-width edge feature (rim, AA, inner lens) has the SAME residual
    profile in raw capture pixels at every state of the sweep, because the
    element radius is the only thing changing;
  * the outer refraction lobe's band is `size/8` points, so its profile
    collapses only after depth is divided by the element radius.

Both collapses are scored the same way - the residual profiles of every
state are resampled onto a common axis and the spread between states is
reported - so the comparison is symmetric and cannot flatter either model.

Usage: measure_edge_residual_scaling.py --capture <lgcap dir>
           --work <scorer work dir> [--variant regular] [--out json]
"""
import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image

LUMA = np.array([0.2126, 0.7152, 0.0722])
CENTRE = (512.0, 614.4)


def load_bgra(path: Path, extent: int) -> np.ndarray:
    raw = np.fromfile(path, dtype=np.uint8)
    return raw.reshape(extent, extent, 4)[..., [2, 1, 0]].astype(np.float64)


def profile_on(axis, depth, residual, weights_min=2000):
    """Mean residual on the given bin edges, or NaN where too few samples."""
    out = np.full(len(axis) - 1, np.nan)
    index = np.digitize(depth, axis) - 1
    for b in range(len(axis) - 1):
        sel = index == b
        n = int(sel.sum())
        if n >= weights_min:
            out[b] = residual[sel].mean()
    return out


def spread(profiles):
    """Mean across bins of the between-state standard deviation."""
    stack = np.vstack(profiles)
    with np.errstate(invalid="ignore"):
        valid = np.isfinite(stack).sum(axis=0) >= 3
        if not valid.any():
            return float("nan"), 0
        sd = np.nanstd(stack[:, valid], axis=0)
    return float(np.nanmean(sd)), int(valid.sum())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--capture", type=Path, required=True)
    ap.add_argument("--work", type=Path, required=True)
    ap.add_argument("--variant", default="regular")
    ap.add_argument("--extent", type=int, default=2048)
    ap.add_argument("--stride", type=int, default=2)
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()

    manifest = json.loads((args.capture / "manifest.json").read_text())
    yy, xx = np.mgrid[0:args.extent, 0:args.extent].astype(float)
    distance = np.hypot(xx - CENTRE[0], yy - CENTRE[1])
    stride = np.zeros(distance.shape, bool)
    stride[::args.stride, ::args.stride] = True

    # raw axis in capture px; normalised axis in units of element radius
    raw_axis = np.array([0, 8, 16, 24, 32, 48, 64, 88, 120, 160, 210, 270, 340, 420, 520])
    rel_axis = np.linspace(0.0, 0.60, 15)

    results = {}
    for appearance in ("light", "dark"):
        sequence = f"sweep__wallpaper-transition__{args.variant}__{appearance}"
        sweep = next(s for s in manifest["sweepSequences"] if s["id"] == sequence)
        frames = [f for f in sweep["frames"] if f.get("stable", True)]
        raws, rels, radii, states = [], [], [], []
        print(f"== {args.variant}/{appearance}: mean residual (apple - walle) in luma codes")
        for index, frame in enumerate(frames):
            walle_path = args.work / sequence / f"composition-state-{index:04d}.bgra"
            mask_path = args.work / sequence / f"state-{index:04d}.r8"
            if not walle_path.exists() or not mask_path.exists():
                continue
            mask = np.fromfile(mask_path, dtype=np.uint8).reshape(args.extent, args.extent)
            solid = (mask == 255) & stride
            if int(solid.sum()) < 40000:
                continue
            radius = float(distance[mask > 0].max())
            if radius < 220:
                continue
            apple = np.asarray(Image.open(args.capture / frame["file"]).convert("RGB")).astype(float)
            walle = load_bgra(walle_path, args.extent)
            residual = ((apple - walle) @ LUMA)[solid]
            depth = (radius - distance)[solid]
            raws.append(profile_on(raw_axis, depth, residual))
            rels.append(profile_on(rel_axis, depth / radius, residual))
            radii.append(radius)
            states.append(index)
            head = "  ".join(f"{v:+5.2f}" if np.isfinite(v) else "    ." for v in raws[-1][:8])
            print(f"   state {index:2d}  R={radius:7.1f} px ({radius/2:6.1f} pt)   {head}")
        if len(raws) < 3:
            continue
        raw_spread, raw_bins = spread(raws)
        rel_spread, rel_bins = spread(rels)
        # the residual has to actually vary with depth for the test to mean
        # anything: report the depth swing it is being asked to explain
        swing = float(np.nanmean([np.nanmax(p) - np.nanmin(p) for p in raws]))
        print(f"   raw-depth axis  : between-state spread {raw_spread:.3f} codes "
              f"over {raw_bins} bins")
        print(f"   depth/R axis    : between-state spread {rel_spread:.3f} codes "
              f"over {rel_bins} bins")
        print(f"   (mean within-state depth swing {swing:.2f} codes; "
              f"radii {min(radii):.0f}-{max(radii):.0f} px)")
        verdict = ("fixed-width edge feature" if raw_spread < rel_spread * 0.9 else
                   "element-scaled band" if rel_spread < raw_spread * 0.9 else
                   "undecided")
        print(f"   => {verdict}")
        results[appearance] = {
            "states": states, "radii": radii,
            "rawSpread": raw_spread, "relSpread": rel_spread,
            "depthSwing": swing, "verdict": verdict,
            "rawAxis": raw_axis.tolist(),
            "rawProfiles": [[None if not np.isfinite(v) else round(v, 3) for v in p] for p in raws],
            "relAxis": rel_axis.tolist(),
            "relProfiles": [[None if not np.isfinite(v) else round(v, 3) for v in p] for p in rels],
        }

    if args.out:
        args.out.write_text(json.dumps(results, indent=1))


if __name__ == "__main__":
    main()
