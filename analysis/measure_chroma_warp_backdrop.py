#!/usr/bin/env python3
"""Chroma warp, decided through MEASURED local Jacobians (session 195).

The trajectory flats pin the material transfer ON the color line, but any
per-channel far-field mechanism pushes the blurred backdrop OFF the line,
where the transfer's cross-channel terms are unknown - the diagonal
inversion fails by tens of ladder units.  The j-probe flats fix that: at
three anchors per line, +-24-code probes per channel measure the local 3x3
Jacobian of (material output) w.r.t. (backdrop codes), and each hypothesis's
predicted blurred backdrop is mapped FORWARD through the measured local
linearization, out_hyp = T(t*) + J(t*) (B_hyp - line(t*)) with t* the
hypothesis's own on-line projection, and scored against the measured edge
profile in OUTPUT codes.  Forward mode needs no inversion, so output
clipping (regular/light saturates red at some anchors - a zero Jacobian
row) weights those directions to zero instead of going singular.

Hypotheses: none (no warp), chan (per-channel power 0.40/1.34),
flip3 (light's flipped cube, per channel), luma (warp on the luma of the
narrow field only; chroma from the un-warped far field), lut (the
gray-extracted nonparametric warp knots, applied per channel).

Usage: measure_chroma_warp_backdrop.py --flats <lgcap-chroma dir>
           --probes <lgcap-chroma-jac dir> [--out json]
"""
import argparse
import collections
import json
from math import erf, sqrt
from pathlib import Path

import numpy as np
from PIL import Image

HALF = 1600
LADDER = list(range(0, 241, 16)) + [255]
ANCHORS = (64, 128, 192)
PROBE_DELTA = 24.0
LINES = {"rc": ((255, 0, 0), (0, 255, 255)), "il": ((255, 0, 0), (0, 76, 0))}
KLUMA = np.array([0.2126, 0.7152, 0.0722])
TO_PANEL = np.array([[0.8225172, 0.1774401, -0.0000221],
                     [0.0331941, 0.9667933, -0.0000244],
                     [0.0171003, 0.0724382, 0.9108519]])
CONST = {"light": dict(wL=0.8846, wC=0.5420, p=0.40),
         "dark":  dict(wL=0.5164, wC=0.6120, p=1.34)}
SN, SW = 14.188, 329.807
KNOTS_U = np.array([0.0, 0.05, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 0.95, 1.0])
try:
    KNOTS_W = {ap: np.load(f"/tmp/warp-knots-{ap}.npy") for ap in ("light", "dark")}
except FileNotFoundError:
    KNOTS_W = None


def srgb_decode(u):
    u = np.clip(u, 0, 1)
    return np.where(u <= 0.04045, u / 12.92, ((u + 0.055) / 1.055) ** 2.4)


def srgb_encode(v):
    v = np.clip(v, 0, None)
    return np.where(v <= 0.0031308, 12.92 * v, 1.055 * np.power(v, 1 / 2.4) - 0.055)


def gk(sigma):
    e = np.arange(-HALF, HALF + 2) - 0.5
    cdf = np.array([0.5 * (1 + erf(x / (sigma * sqrt(2)))) for x in e])
    return np.diff(cdf)


def line_color(t, a, b):
    return np.array([(x * (255 - t) + y * t + 127) // 255 for x, y in zip(a, b)],
                    float)


def dominant(path):
    px = np.asarray(Image.open(path).convert("RGB")).astype(np.int32)
    h, w, _ = px.shape
    cy, cx = h // 2, w // 2
    interior = px[cy - 150:cy + 150, cx - 150:cx + 150].reshape(-1, 3)
    return np.array(collections.Counter(map(tuple, interior)).most_common(1)[0][0],
                    float)


def edge_profiles(path):
    px = np.asarray(Image.open(path).convert("RGB")).astype(float)
    h, w, _ = px.shape
    cy, cx = h // 2, w // 2
    return px[cy - 40:cy + 40, cx - 220:cx + 220, :].mean(axis=0), cx - 220, w


def warp_apply(v, p, flip):
    v = np.clip(v, 0, 1)
    return 1 - np.power(1 - v, p) if flip else np.power(v, p)


def warp_invert(v, p, flip):
    v = np.clip(v, 0, 1)
    return 1 - np.power(1 - v, 1 / p) if flip else np.power(v, 1 / p)


KN, KW = gk(SN), gk(SW)
FROM_PANEL = np.linalg.inv(TO_PANEL)


def predict_backdrop(tag, ap, hyp, count, start, width):
    """Predicted blurred backdrop (sRGB codes, (count,3)) for a hypothesis."""
    a, b = LINES[tag]
    pad = HALF
    n = width + 2 * pad
    lo_c, hi_c = line_color(0, a, b), line_color(255, a, b)
    B = np.tile(lo_c, (n, 1))
    B[pad + width // 2:] = hi_c
    P = srgb_encode(np.clip(srgb_decode(B / 255.0) @ TO_PANEL.T, 0, None))
    c0 = CONST[ap]
    N = np.stack([np.convolve(P[:, c], KN, mode="same") for c in range(3)], axis=1)
    if hyp == "none":
        F = np.stack([np.convolve(N[:, c], KW, mode="same") for c in range(3)], axis=1)
        mixed = (c0["wC"] * N + (1 - c0["wC"]) * F
                 + ((c0["wL"] - c0["wC"]) * ((N - F) @ KLUMA))[:, None])
    elif hyp in ("chan", "flip3"):
        p, flip = (3.0, True) if (hyp == "flip3" and ap == "light") else (c0["p"], False)
        Wn = warp_apply(N, p, flip)
        F = warp_invert(np.stack([np.convolve(Wn[:, c], KW, mode="same")
                                  for c in range(3)], axis=1), p, flip)
        mixed = (c0["wC"] * N + (1 - c0["wC"]) * F
                 + ((c0["wL"] - c0["wC"]) * ((N - F) @ KLUMA))[:, None])
    elif hyp == "lut":
        wk = KNOTS_W[ap]
        Wn = np.interp(np.clip(N, 0, 1), KNOTS_U, wk)
        F = np.stack([np.convolve(Wn[:, c], KW, mode="same") for c in range(3)], axis=1)
        F = np.interp(np.clip(F, 0, 1), wk, KNOTS_U)
        mixed = (c0["wC"] * N + (1 - c0["wC"]) * F
                 + ((c0["wL"] - c0["wC"]) * ((N - F) @ KLUMA))[:, None])
    elif hyp == "luma":
        p, flip = c0["p"], False
        yw = warp_apply(N @ KLUMA, p, flip)
        Fu = np.stack([np.convolve(N[:, c], KW, mode="same") for c in range(3)], axis=1)
        yfw = warp_invert(np.convolve(yw, KW, mode="same"), p, flip)
        scal = ((c0["wL"] - c0["wC"]) * (N @ KLUMA)
                + (1 - c0["wL"]) * yfw - (1 - c0["wC"]) * (Fu @ KLUMA))
        mixed = c0["wC"] * N + (1 - c0["wC"]) * Fu + scal[:, None]
    out = srgb_encode(np.clip(srgb_decode(np.clip(mixed, 0, 1)) @ FROM_PANEL.T,
                              0, None)) * 255.0
    return out[pad + start:pad + start + count]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--flats", required=True, help="lgcap-chroma dir (T tables + edges)")
    parser.add_argument("--probes", required=True, help="lgcap-chroma-jac dir (j-probe flats)")
    parser.add_argument("--overlay", default="regular")
    parser.add_argument("--out")
    args = parser.parse_args()
    fshots = Path(args.flats) / "shots"
    pshots = Path(args.probes) / "shots"

    results = {}
    for tag, (a, b) in LINES.items():
        for ap in ("light", "dark"):
            # on-line transfer
            ts = np.array(LADDER, float)
            T = np.stack([dominant(
                fshots / f"chroma-{tag}-t{t:03d}__circle-0500-center__{args.overlay}__{ap}.png")
                for t in LADDER])
            # Jacobians at the anchors
            Js = {}
            for t in ANCHORS:
                J = np.zeros((3, 3))
                base = line_color(t, a, b)
                for ci, cname in enumerate("RGB"):
                    hi = dominant(pshots / f"chroma-{tag}-j{t:03d}-{cname}p__circle-0500-center__{args.overlay}__{ap}.png")
                    lo = dominant(pshots / f"chroma-{tag}-j{t:03d}-{cname}m__circle-0500-center__{args.overlay}__{ap}.png")
                    # actual probe distance may clip at gamut edges
                    dp = min(255, base[ci] + PROBE_DELTA) - max(0, base[ci] - PROBE_DELTA)
                    J[:, ci] = (hi - lo) / dp
                Js[t] = J
            def J_at(t):
                t = float(np.clip(t, ANCHORS[0], ANCHORS[-1]))
                if t <= ANCHORS[1]:
                    w = (t - ANCHORS[0]) / (ANCHORS[1] - ANCHORS[0])
                    return (1 - w) * Js[ANCHORS[0]] + w * Js[ANCHORS[1]]
                w = (t - ANCHORS[1]) / (ANCHORS[2] - ANCHORS[1])
                return (1 - w) * Js[ANCHORS[1]] + w * Js[ANCHORS[2]]

            prof, start, width = edge_profiles(
                fshots / f"chroma-{tag}-edge-x__circle-0500-center__{args.overlay}__{ap}.png")
            count = prof.shape[0]
            line0 = line_color(0, a, b)
            line_dir = (line_color(255, a, b) - line0)

            entry = {}
            print(f"== chroma-{tag} {args.overlay}/{ap} (forward through measured J)")
            for hyp in ("none", "chan", "luma", "flip3", "lut"):
                if hyp == "flip3" and ap != "light":
                    continue
                B_hyp = predict_backdrop(tag, ap, hyp, count, start, width)
                # linearize each pixel around the hypothesis's own on-line point
                t_star = np.clip((B_hyp - line0) @ line_dir / (line_dir @ line_dir)
                                 * 255.0, 0.0, 255.0)
                out_hyp = np.zeros_like(B_hyp)
                for i in range(count):
                    Tt = np.array([np.interp(t_star[i], ts, T[:, c]) for c in range(3)])
                    out_hyp[i] = Tt + J_at(t_star[i]) @ (B_hyp[i] - line_color(t_star[i], a, b))
                r = prof - out_hyp
                per = np.sqrt((r * r).mean(axis=0))
                total = float(np.sqrt((r * r).mean()))
                entry[hyp] = {"rms": round(total, 3),
                              "perChannel": [round(float(v), 3) for v in per]}
                print(f"   {hyp:6s}: rms {total:6.2f}   "
                      f"R {per[0]:6.2f}  G {per[1]:6.2f}  B {per[2]:6.2f}")
            results[f"{tag}/{ap}"] = entry

    if args.out:
        Path(args.out).write_text(json.dumps(results, indent=1))


if __name__ == "__main__":
    main()
