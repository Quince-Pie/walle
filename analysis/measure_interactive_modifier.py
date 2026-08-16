#!/usr/bin/env python3
"""Decide whether `.interactive(true)` changes a STATIC Liquid Glass element.

walle does not model Apple's `interactive(Bool)` modifier.  That is defensible
- a wallpaper has nothing to interact with - but it was an assumption, and an
assumption about a shipped modifier is a parity gap until something measures
it.  This measures it.

The rig captures `.regular` and `.regular.interactive(true)` over the same
backgrounds in the same run, so the two frames differ in nothing but the
modifier.  Three statistics decide the question:

  * the interior mean, which moves if the modifier changes the transfer;
  * the whole-frame maximum absolute difference, which moves if it changes
    anything anywhere - a rim, a shadow, a highlight;
  * the count of differing pixels, which separates "identical" from "identical
    except for a handful of dither codes".

If the frames are byte-identical then the modifier is inert at rest, walle's
omission is correct, and this is a measurement rather than an assumption.  If
they differ, the difference map says where, and the gap is real.
"""

import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image

SHAPE = "circle-0500-center"
INTERIOR_RADIUS = 400
PAIRS = (("regular", "interactiveRegular"), ("clear", "interactiveClear"))

type JsonObject = dict[str, object]


def load(path: Path) -> np.ndarray:
    return np.asarray(Image.open(path).convert("RGB")).astype(int)


def interior_mask(shape: tuple[int, int]) -> np.ndarray:
    height, width = shape
    y, x = np.mgrid[0:height, 0:width]
    return ((x - width // 2) ** 2 + (y - height // 2) ** 2) < INTERIOR_RADIUS**2


def compare(shots: Path, base: str, modified: str, appearance: str
            ) -> list[JsonObject]:
    records: list[JsonObject] = []
    for path in sorted(shots.glob(f"*__{SHAPE}__{base}__{appearance}.png")):
        background = path.name.split("__")[0]
        other = shots / f"{background}__{SHAPE}__{modified}__{appearance}.png"
        if not other.exists():
            continue
        left, right = load(path), load(other)
        if left.shape != right.shape:
            continue
        delta = right - left
        inside = interior_mask(left.shape[:2])
        records.append({
            "background": background,
            "variant": base,
            "appearance": appearance,
            "differingPixels": int((delta != 0).any(axis=2).sum()),
            "totalPixels": int(delta.shape[0] * delta.shape[1]),
            "maximumAbsoluteDelta": int(np.abs(delta).max()),
            "interiorMeanDelta": [round(float(v), 4)
                                  for v in delta[inside].mean(axis=0)],
        })
    return records


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--shots", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()

    records = [
        record
        for base, modified in PAIRS
        for appearance in ("light", "dark")
        for record in compare(arguments.shots, base, modified, appearance)
    ]
    if not records:
        print("  no interactive() pairs found in this corpus")
        return 1

    print(f"  {'background':14s} {'variant':8s} {'appear':6s} "
          f"{'differing':>10s} {'maxDelta':>9s}   interior mean delta")
    for record in records:
        print(f"  {record['background']:14s} {record['variant']:8s} "
              f"{record['appearance']:6s} {record['differingPixels']:10d} "
              f"{record['maximumAbsoluteDelta']:9d}   "
              + " ".join(f"{v:+7.4f}" for v in record["interiorMeanDelta"]))

    identical = sum(1 for r in records if r["differingPixels"] == 0)
    worst = max(int(r["maximumAbsoluteDelta"]) for r in records)
    verdict = ("inert at rest - byte-identical in every pair"
               if identical == len(records) else
               f"CHANGES the element - worst delta {worst} codes")
    print(f"\n  byte-identical {identical}/{len(records)} pairs: {verdict}")
    if arguments.output is not None:
        arguments.output.write_text(
            json.dumps({"schemaVersion": 1, "osBuild": "25G76",
                        "byteIdenticalPairs": identical,
                        "pairCount": len(records),
                        "worstAbsoluteDelta": worst,
                        "verdict": verdict,
                        "records": records}, indent=2, sort_keys=True) + "\n",
            encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
