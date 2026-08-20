#!/usr/bin/env python3
"""Solve `regular`'s material law on flats, the way `clear`'s was solved.

`clear`'s law came out exact from the rig's flat ladders - out = 0.97 *
(0.075 + 1.075 * in) reproduced 15 of 17 levels to the code - and `clear`'s
interior is now at the dither floor (0.43 rms) at every depth.  `regular`
never got the same treatment: it ships a 35/56-term fitted polynomial, and
its interior reads 1.2-4.4 rms - three to ten times `clear`'s - even 450 px
deep, where no edge mechanism reaches.

A flat background is the cleanest possible probe of that law.  Blurring a
constant returns the constant, so the narrow field, the wide field and
their mixture all collapse to the background colour; a refraction lobe
displaces a constant field into itself; and at the centre of a 500 pt
circle no rim, highlight or shadow reaches.  Whatever code appears there is
the material law applied to the background, with every other mechanism
algebraically removed.

The rig's four chroma lines (rc, il, i5, i9) sample 17 levels each, so the
ladders sweep saturated primaries, an isoluminant pair and a desaturating
line - enough to separate a per-channel law from a luma-keyed one.  The
background is read from the frame's own corner rather than assumed, and the
`none` overlay is used as the control that the corner IS the background.

Reports Apple's flat law against walle's shipped transfer polynomial, per
channel, so the gap that the sweeps see as `regular` interior error is
stated in codes on inputs where nothing else can be blamed for it.

Usage: measure_regular_flat_law.py [--variant regular] [--out json]
           [--captures dir ...]
"""
import argparse
import collections
import json
from pathlib import Path

import numpy as np
from PIL import Image

DEFAULT_CAPTURES = ["/tmp/lgcap-chroma-1024", "/tmp/lgcap-chroma-iso-1024"]
TO_PANEL = np.array([[0.8225172, 0.1774401, -0.0000221],
                     [0.0331941, 0.9667933, -0.0000244],
                     [0.0171003, 0.0724382, 0.9108519]])


def dominant(px, cy, cx, half):
    patch = px[cy - half:cy + half, cx - half:cx + half, :].reshape(-1, 3)
    counts = collections.Counter(map(tuple, patch))
    colour, n = counts.most_common(1)[0]
    return np.array(colour, float), n / patch.shape[0]


def harvest(captures, variant, appearance):
    """(background, interior, purity, tag) for every flat ladder shot."""
    rows = []
    for capture in captures:
        shots = Path(capture) / "shots"
        if not shots.is_dir():
            continue
        for path in sorted(shots.glob(f"*__circle-0500-center__{variant}__{appearance}.png")):
            name = path.name.split("__")[0]
            if "edge" in name:                       # edge shots are not flats
                continue
            control = shots / path.name.replace(f"__{variant}__", "__none__")
            if not control.exists():
                continue
            px = np.asarray(Image.open(path).convert("RGB")).astype(np.int32)
            ctl = np.asarray(Image.open(control).convert("RGB")).astype(np.int32)
            h, w, _ = px.shape
            background, bg_purity = dominant(ctl, h // 2, w // 2, 150)
            corner, corner_purity = dominant(px, 80, 80, 60)
            if corner_purity < 0.999 or np.abs(corner - background).max() > 1:
                continue                              # corner must BE the background
            interior, purity = dominant(px, h // 2, w // 2, 120)
            rows.append({"tag": name, "background": background,
                         "interior": interior, "purity": purity})
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--captures", nargs="*", default=DEFAULT_CAPTURES)
    ap.add_argument("--variant", default="regular")
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()

    matrices = json.loads(Path("analysis/results/material_matrices.json").read_text())
    results = {}
    for appearance in ("light", "dark"):
        rows = harvest(args.captures, args.variant, appearance)
        if not rows:
            print(f"== {args.variant}/{appearance}: no flat ladder shots found")
            continue
        record = next(r for r in matrices["records"]
                      if r["variant"] == args.variant and r["appearance"] == appearance)
        exps = np.array(record["termExponents"], float)
        coef = np.array(record["coefficients"], float)

        def transfer(m):
            u = np.clip(m, 0, 1.3)
            return np.prod(u[:, None, :] ** exps[None, :, :], axis=-1) @ coef

        bg = np.array([r["background"] for r in rows]) / 255.0
        got = np.array([r["interior"] for r in rows])
        # the shipped path writes 8-bit, so the polynomial's excursions past
        # the rails are clamped before anyone sees them; comparing unclamped
        # invents error that the display cannot show
        predicted = np.clip(transfer(bg) * 255.0, 0.0, 255.0)
        error = predicted - got
        railed = (got <= 0.5) | (got >= 254.5)

        print(f"== {args.variant}/{appearance}: {len(rows)} flat ladder levels "
              f"({int(railed.sum())}/{railed.size} channel readings at a rail)")
        print(f"   walle transfer vs Apple, per channel (codes, output-clamped):")
        for c, name in enumerate("RGB"):
            e = error[:, c]
            free = e[~railed[:, c]]
            print(f"     {name}: mean {e.mean():+6.2f}  rms {np.sqrt((e * e).mean()):5.2f}  "
                  f"max |{np.abs(e).max():5.2f}|  exact {(np.abs(e) < 0.5).sum():3d}/{len(e)}"
                  f"   | off-rail rms {np.sqrt((free * free).mean()) if free.size else 0:5.2f}"
                  f" ({free.size})")
        overall = np.sqrt((error ** 2).mean())
        free_all = error[~railed]
        exact = int((np.abs(error) < 0.5).all(axis=1).sum())
        print(f"   overall rms {overall:.2f} codes ({np.sqrt((free_all ** 2).mean()):.2f} "
              f"off-rail); {exact}/{len(rows)} levels exact in all 3")

        worst = np.argsort(-np.where(railed, 0.0, np.abs(error)).max(axis=1))[:8]
        print("   worst levels:")
        for i in worst:
            b = rows[i]["background"].astype(int)
            print(f"     {rows[i]['tag']:22s} in ({b[0]:3d},{b[1]:3d},{b[2]:3d}) "
                  f"-> apple ({got[i][0]:3.0f},{got[i][1]:3.0f},{got[i][2]:3.0f}) "
                  f"walle ({predicted[i][0]:6.1f},{predicted[i][1]:6.1f},{predicted[i][2]:6.1f}) "
                  f"err ({error[i][0]:+5.1f},{error[i][1]:+5.1f},{error[i][2]:+5.1f})")

        results[appearance] = {
            "levels": [{"tag": r["tag"], "background": r["background"].tolist(),
                        "interior": r["interior"].tolist(), "purity": round(r["purity"], 5)}
                       for r in rows],
            "walleRms": float(overall), "walleExact": exact,
        }

    if args.out:
        args.out.write_text(json.dumps(results, indent=1))


if __name__ == "__main__":
    main()
