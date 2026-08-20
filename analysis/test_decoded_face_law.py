#!/usr/bin/env python3
"""Test Apple's DECODED face law against the rig's flat ladders.

`DesignLibrary`'s `Material.Context` parameters give the face effect as a
YCC adjustment with three numbers and a global white-point shift:

    faceEffects.ycc = (black, white, saturation)
    sdrAdjustment.faceDimming.whitePointShift = 0.97

                clear   regular/light   regular/dark
      black     0.075       0.500           0.200
      white     1.150       1.030           0.600
      sat       1.060       1.000           1.000

Read as `Y -> whitePointShift * (black + (white - black) * Y)` this
reproduces `clear`'s independently MEASURED law, 0.97 * (0.075 + 1.075 * in),
exactly - including the 0.97, which is the white-point shift and not a
fitted constant.  If the same reading holds for `regular`, walle can retire
a 35/56-term fitted polynomial for three decoded numbers.

The law is stated in "YCC" without naming the space, so every plausible one
is tried here and scored the same way on the same data.  Levels whose
output sits at a rail are excluded from the off-rail figure, because 8-bit
clamping hides arbitrary amounts of disagreement there.

Usage: test_decoded_face_law.py [--out json]
"""
import argparse
import json
from pathlib import Path

import numpy as np

REC709 = np.array([0.2126, 0.7152, 0.0722])
REC601 = np.array([0.299, 0.587, 0.114])
TO_PANEL = np.array([[0.8225172, 0.1774401, -0.0000221],
                     [0.0331941, 0.9667933, -0.0000244],
                     [0.0171003, 0.0724382, 0.9108519]])
FROM_PANEL = np.linalg.inv(TO_PANEL)

YCC = {
    ("clear", "light"): (0.075, 1.150, 1.06),
    ("clear", "dark"): (0.075, 1.150, 1.06),
    ("regular", "light"): (0.500, 1.030, 1.00),
    ("regular", "dark"): (0.200, 0.600, 1.00),
}
WHITE_POINT_SHIFT = 0.97


def srgb_decode(u):
    u = np.clip(u, 0, 1)
    return np.where(u <= 0.04045, u / 12.92, ((u + 0.055) / 1.055) ** 2.4)


def srgb_encode(v):
    v = np.clip(v, 0, 1)
    return np.where(v <= 0.0031308, 12.92 * v, 1.055 * np.power(v, 1 / 2.4) - 0.055)


def apply_ycc(rgb, black, white, saturation, luma):
    """Y -> shift * (black + (white-black) * Y); chroma scaled by saturation."""
    y = rgb @ luma
    y2 = WHITE_POINT_SHIFT * (black + (white - black) * y)
    return y2[..., None] + saturation * (rgb - y[..., None])


SPACES = {
    "code/709": (lambda x: x, lambda x: x, REC709),
    "code/601": (lambda x: x, lambda x: x, REC601),
    "linear/709": (srgb_decode, srgb_encode, REC709),
    "linear/601": (srgb_decode, srgb_encode, REC601),
    "panel-code/709": (lambda x: srgb_encode(np.clip(srgb_decode(x) @ TO_PANEL.T, 0, 1)),
                       lambda x: srgb_encode(np.clip(srgb_decode(x) @ FROM_PANEL.T, 0, 1)),
                       REC709),
    "panel-linear/709": (lambda x: np.clip(srgb_decode(x) @ TO_PANEL.T, 0, 1),
                         lambda x: srgb_encode(np.clip(x @ FROM_PANEL.T, 0, 1)),
                         REC709),
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--flats", type=Path,
                    default=Path("analysis/results/flat-ladder-levels.json"))
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()

    data = json.loads(args.flats.read_text())
    results = {}
    for key, entry in data.items():
        variant, appearance = key.split("/")
        black, white, saturation = YCC[(variant, appearance)]
        bg = np.array([lv["background"] for lv in entry["levels"]], float) / 255.0
        got = np.array([lv["interior"] for lv in entry["levels"]], float)
        railed = (got <= 0.5) | (got >= 254.5)
        print(f"== {key}: {len(bg)} flat levels   "
              f"(black {black}, white {white}, sat {saturation})")
        best = None
        for name, (fwd, inv, luma) in SPACES.items():
            out = np.clip(inv(apply_ycc(fwd(bg), black, white, saturation, luma)) * 255.0, 0, 255)
            error = out - got
            free = error[~railed]
            rms = float(np.sqrt((free ** 2).mean())) if free.size else float("nan")
            exact = int((np.abs(error) < 0.5).all(axis=1).sum())
            print(f"     {name:18s} off-rail rms {rms:7.2f}   "
                  f"all-rail-inclusive rms {np.sqrt((error ** 2).mean()):7.2f}   "
                  f"exact {exact:3d}/{len(bg)}")
            if best is None or rms < best[1]:
                best = (name, rms, exact)
        print(f"   best: {best[0]} at {best[1]:.2f} codes off-rail, {best[2]} levels exact")
        results[key] = {"best": best[0], "rms": best[1], "exact": best[2]}

    if args.out:
        args.out.write_text(json.dumps(results, indent=1))


if __name__ == "__main__":
    main()
