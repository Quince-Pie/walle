#!/usr/bin/env python3
"""The chroma-warp channel-space instrument (session 195).

The far-field tonal warp is measured exactly on gray edges, but gray content
cannot distinguish PER-CHANNEL from LUMA-ONLY application - and the two
sweep holdouts disagree (coded prefers full-channel, natural prefers luma).
The rig's chroma families decide it:

  chroma-rc : red (255,0,0) <-> cyan (0,255,255).  R falls while G and B
      rise across the same edge, so a per-channel warp acts with OPPOSITE
      polarity on R versus G/B, while a luma-only warp gives every channel
      the same luma-keyed field.
  chroma-il : red (255,0,0) <-> green (0,76,0) at equal luma.  A luma-only
      warp predicts NO far-field asymmetry here at all; a per-channel warp
      predicts full-strength opposite-polarity asymmetries.

Method, per line/appearance: the 17 trajectory flats pin the per-channel
transfer T_c(t) along the line exactly (single-code interiors); the edge
profile per channel is inverted through its own T_c to recover the
blurred-backdrop trajectory parameter t_c(x).  With no warp - or any warp
keyed on a channel-shared field - all channels recover THE SAME t(x)
(every channel is affine in t, so blurring keeps the backdrop on the line
up to the luma/chroma mixture-weight split).  A per-channel warp drives
the recovered channels apart with the polarity pattern above.  The
statistic reported per channel is the profile's asymmetry around the
registered edge (the same displacement/Jensen read that found the warp on
the gray edges), plus the direct channel-disagreement profile.

Usage: measure_chroma_warp_channels.py --capture <lgcap-chroma dir>
           [--overlay regular] [--out json]
"""
import argparse
import collections
import json
from math import erf, sqrt
from pathlib import Path

import numpy as np
from PIL import Image

HALF_WIDTH = 1600
ROW_HALF_BAND = 40
INTERIOR_RADIUS = 220
LADDER = list(range(0, 241, 16)) + [255]

LINES = {
    "rc": ((255, 0, 0), (0, 255, 255)),
    "il": ((255, 0, 0), (0, 76, 0)),
}


def line_color(t, a, b):
    return tuple((x * (255 - t) + y * t + 127) // 255 for x, y in zip(a, b))


def interior_dominant(path):
    px = np.asarray(Image.open(path).convert("RGB")).astype(np.int32)
    h, w, _ = px.shape
    cy, cx = h // 2, w // 2
    interior = px[cy - 150:cy + 150, cx - 150:cx + 150, :]
    counts = collections.Counter(map(tuple, interior.reshape(-1, 3)))
    dom, n = counts.most_common(1)[0]
    return dom, n / interior.reshape(-1, 3).shape[0]


def edge_profiles(path):
    px = np.asarray(Image.open(path).convert("RGB")).astype(float)
    h, w, _ = px.shape
    cy, cx = h // 2, w // 2
    band = px[cy - ROW_HALF_BAND:cy + ROW_HALF_BAND,
              cx - INTERIOR_RADIUS:cx + INTERIOR_RADIUS, :]
    return band.mean(axis=0), cx - INTERIOR_RADIUS, w  # (440, 3)


def gaussian_kernel(sigma):
    sigma = max(abs(sigma), 1e-3)
    edges = np.arange(-HALF_WIDTH, HALF_WIDTH + 2) - 0.5
    cdf = np.array([0.5 * (1.0 + erf(e / (sigma * sqrt(2.0)))) for e in edges])
    k = np.diff(cdf)
    return k / k.sum()


def asymmetry(profile, count):
    """Signed asymmetry of a monotone profile around the geometric center:
    mean of p(center+d) + p(center-d) - (p_lo + p_hi), over the transition
    half-width, normalized by the profile's span.  Zero for any symmetric
    (un-warped, registered) mixture; sign follows the direction the
    transition zone is pushed."""
    c = count // 2
    lo = profile[:20].mean()
    hi = profile[-20:].mean()
    span = hi - lo
    if abs(span) < 20:
        return None
    ds = np.arange(1, 160)
    fold = profile[c + ds] + profile[c - ds] - (lo + hi)
    return float(fold.mean() / span)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--capture", required=True)
    parser.add_argument("--overlay", default="regular")
    parser.add_argument("--out")
    args = parser.parse_args()
    shots = Path(args.capture) / "shots"

    results = {}
    for tag, (a, b) in LINES.items():
        for ap in ("light", "dark"):
            # per-channel transfer along the line, from the trajectory flats
            ts, outs = [], []
            impure = 0
            for t in LADDER:
                p = shots / f"chroma-{tag}-t{t:03d}__circle-0500-center__{args.overlay}__{ap}.png"
                if not p.exists():
                    continue
                dom, frac = interior_dominant(p)
                if frac < 0.9999:
                    impure += 1
                ts.append(t)
                outs.append(dom)
            ts = np.array(ts, float)
            outs = np.array(outs, float)  # (17, 3)

            prof, start, width = edge_profiles(
                shots / f"chroma-{tag}-edge-x__circle-0500-center__{args.overlay}__{ap}.png")
            count = prof.shape[0]

            # registration + raw-edge control from the none overlay
            nprof, _, _ = edge_profiles(
                shots / f"chroma-{tag}-edge-x__circle-0500-center__none__{ap}.png")

            key = f"{tag}/{ap}"
            entry = {"impureFlats": impure, "channels": {}}
            print(f"== chroma-{tag} {args.overlay}/{ap}  (impure flats: {impure})")
            t_hat = {}
            for c, name in enumerate("RGB"):
                span = outs[-1, c] - outs[0, c]
                if abs(span) < 8:
                    print(f"   {name}: uninformative (transfer span {span:+.0f})")
                    continue
                order = np.argsort(outs[:, c])
                t_c = np.interp(prof[:, c], outs[order, c], ts[order])
                t_hat[name] = t_c
                asym = asymmetry(t_c, count)
                raw_asym = asymmetry(np.interp(
                    nprof[:, c],
                    sorted([0.0, 255.0]),
                    sorted([0.0, 255.0]))
                    if False else nprof[:, c], count)
                entry["channels"][name] = {
                    "transferSpan": round(float(span), 1),
                    "asymmetry": None if asym is None else round(asym, 4),
                }
                print(f"   {name}: transfer span {span:+6.1f}  "
                      f"recovered-t asymmetry {asym if asym is None else f'{asym:+.4f}'}")
            # channel disagreement: rms of pairwise recovered-t differences
            names = list(t_hat)
            for i in range(len(names)):
                for j in range(i + 1, len(names)):
                    d = t_hat[names[i]] - t_hat[names[j]]
                    rms = float(np.sqrt((d * d).mean()))
                    entry["channels"][f"{names[i]}-{names[j]}"] = {"tDisagreeRms": round(rms, 2)}
                    print(f"   t^{names[i]} vs t^{names[j]}: disagree rms {rms:.2f} "
                          f"(of 255 ladder units)")
            results[key] = entry

    if args.out:
        Path(args.out).write_text(json.dumps(results, indent=1))


if __name__ == "__main__":
    main()
