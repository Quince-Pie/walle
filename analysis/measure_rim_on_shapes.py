#!/usr/bin/env python3
"""Does the rim care what shape the element is?

Every rim measurement in this repo is on a circle, and walle's law is written
as a function of depth inside the boundary alone.  That is an assumption until
something without constant curvature is measured, and the corpus could not
supply one: its rectangles are 1600 by 900 POINTS in a 512 point window, so
they cover the frame completely and their boundary has never been in shot.

These three are small enough to fit: a sharp-cornered rectangle whose sides are
the zero-curvature limit, a rounded one whose corners curve at 120 capture
pixels, and a capsule whose ends curve harder than any circle in the corpus.
One signed distance function covers all three - a rounded box - and its sign
tells inside from out, so the same profile that was read from annuli can be
read from level sets here.

The straight sides and the corners are reported apart, because that is the
comparison: if the rim is a function of depth alone they agree with each other
and with the circle, and if it is not, the corner is where it shows.
"""

import argparse
import importlib.util
import json
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "rim", ROOT / "analysis/measure_rim_light.py")
RIM = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(RIM)

# scene -> (half width, half height, corner radius), capture pixels at 2x.
SHAPES = {
    "rect-0300x0200-r000": (300.0, 200.0, 0.0),
    "rect-0300x0200-r060": (300.0, 200.0, 120.0),
    "capsule-0300x0120": (300.0, 120.0, 120.0),
}
# A circle's level sets cross the pixel grid at every sub-pixel offset, so a
# narrow depth window always catches pixels.  An axis-aligned STRAIGHT edge
# does not: with the boundary on an integer coordinate every pixel centre sits
# at a half-integer depth exactly, and a window at 0.8 catches nothing at all.
# So the shapes are read on their own natural grid and the circle's curve is
# interpolated onto it for the comparison.
DEPTHS = np.arange(0.5, 4.01, 0.5)
REFERENCE = 0.5
TOLERANCE = 0.2
MINIMUM_BAND = 64
# How far a "straight" pixel must sit from where the corner arc begins.
MARGIN = 24.0

type JsonObject = dict[str, object]


def geometry(shape: tuple[float, float, float], width: int, height: int):
    """Signed distance to a rounded box, and which part of it is nearest.

    Positive outside.  A pixel belongs to a CORNER when the nearest boundary
    point is on one of the four arcs, which is exactly where both components of
    |p| exceed the box's straight extent.
    """
    half_width, half_height, radius = shape
    y, x = np.mgrid[0:height, 0:width]
    px = np.abs(x + 0.5 - width / 2.0) - (half_width - radius)
    py = np.abs(y + 0.5 - height / 2.0) - (half_height - radius)
    outside = np.hypot(np.maximum(px, 0.0), np.maximum(py, 0.0))
    inside = np.minimum(np.maximum(px, py), 0.0)
    distance = outside + inside - radius
    # The corner set gets the same margin as the straight one, and for the same
    # reason: right at the junction the arc and the flat run share pixels, and
    # a set that reaches the junction reads a mixture of both.
    corner = (px > MARGIN) & (py > MARGIN)
    # A margin either side of where the arc meets the flat run.  Without it the
    # "straight" set reaches right up to the transition and the arc's own
    # pixels leak into it, which is what made a capsule's flat side read 1.8
    # where its circle reads 0.6.
    clear_of_corner = (px < -MARGIN) | (py < -MARGIN)
    return distance, corner, clear_of_corner & ~corner


def profile(pixels: np.ndarray, distance: np.ndarray, picked: np.ndarray,
            interior: np.ndarray) -> dict[float, np.ndarray]:
    out = {}
    for depth in DEPTHS:
        band = picked & (np.abs(distance + depth) < TOLERANCE)
        if band.sum() >= MINIMUM_BAND:
            out[float(depth)] = pixels[band].mean(axis=0) - interior
    return out


def measure(shots: list[Path], scene: str, background: str, variant: str,
            appearance: str) -> JsonObject | None:
    name = f"{background}__{scene}__{variant}__{appearance}.png"
    path = next((d / name for d in shots if (d / name).exists()), None)
    if path is None:
        return None
    pixels = np.asarray(Image.open(path).convert("RGB")).astype(float)
    height, width, _ = pixels.shape
    distance, corner, straight = geometry(SHAPES[scene], width, height)
    body = pixels[distance < -60.0]
    if len(body) < 500:
        return None
    interior = body.mean(axis=0)

    entry: JsonObject = {
        "scene": scene, "background": background,
        "variant": variant, "appearance": appearance,
        "interiorCodes": [round(float(v), 3) for v in interior],
    }
    for label, picked in (("straight", straight), ("corner", corner)):
        table = profile(pixels, distance, picked, interior)
        if REFERENCE not in table:
            continue
        reference = table[REFERENCE]
        amplitude = float(np.abs(reference).max())
        entry[label] = {
            "referenceExcessCodes": round(amplitude, 3),
            "referenceCodes": [round(float(v + i), 3)
                               for v, i in zip(reference, interior)],
            # In CODE VALUES, not normalised.  A ratio against a curve that
            # reaches zero explodes where the rim has already ended, which said
            # 3.78 about a corner that is three tenths of a code value from the
            # circle; code values are also what parity is measured in.
            "excessCodes": [round(float(np.abs(table[d]).max()), 4)
                            if d in table else None for d in DEPTHS],
            "weight": [round(float(np.abs(table[d]).max() / amplitude), 4)
                       if d in table and amplitude > 1e-6 else None
                       for d in DEPTHS],
        }
    return entry


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--shots", type=Path, nargs="+", required=True)
    parser.add_argument("--circle", type=Path,
                        default=ROOT / "analysis/results/rim_light.json",
                        help="the circle's own measurement, to compare against")
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()

    records = [
        record
        for scene in SHAPES
        for background in ("gray-000", "gray-064", "gray-128", "gray-192",
                           "red-128", "green-128", "blue-128", "cyan-128",
                           "magenta-128", "yellow-128", "orange", "violet")
        for variant in ("regular", "clear")
        for appearance in ("light", "dark")
        if (record := measure(arguments.shots, scene, background, variant,
                              appearance)) is not None
    ]
    if not records:
        print("  no shaped elements found in this corpus")
        return 1

    circle = json.loads(arguments.circle.read_text(encoding="utf-8"))
    print("  The rim's shape on a straight edge and at a corner, against the\n"
          "  circle's own curve interpolated onto the same depths.\n")
    print("  depths: " + " ".join(f"{d:5.1f}" for d in DEPTHS))
    summary: JsonObject = {}
    for variant in ("regular", "clear"):
        for appearance in ("light", "dark"):
            reference = circle["materials"][f"{variant}/{appearance}"]
            # Interpolated onto this grid, and renormalised at the same depth
            # the shapes are normalised at, so the two curves mean the same
            # thing before they are subtracted.
            curve = np.interp(DEPTHS, reference["rimShape"]["depthPixels"],
                              reference["rimShape"]["weight"],
                              left=reference["rimShape"]["weight"][0], right=0.0)
            anchor = curve[int(np.argmin(np.abs(DEPTHS - REFERENCE)))]
            shape = list(curve / anchor) if abs(anchor) > 1e-6 else list(curve)
            print(f"\n  {variant} / {appearance}")
            print("    circle, normalised   " + " ".join(f"{v:5.3f}"
                                                         for v in shape))
            for scene in SHAPES:
                for label in ("straight", "corner"):
                    rows = [r[label]["weight"] for r in records
                            if r["scene"] == scene and r["variant"] == variant
                            and r["appearance"] == appearance and label in r]
                    if not rows:
                        continue
                    # A depth is compared only where the shape has samples at
                    # it.  A straight edge cannot report every depth on this
                    # grid - its level sets are lines on the pixel lattice, so
                    # half of them fall between rows of pixels - and dropping
                    # the whole shape for that would leave nothing to compare.
                    stacked = np.array(
                        [[np.nan if w is None else w for w in row]
                         for row in rows], dtype=float)
                    # The same comparison in code values: each shape's own
                    # curve against the circle's, both scaled to the shape's
                    # measured amplitude at the reference depth.
                    amplitudes = np.array(
                        [r[label]["referenceExcessCodes"] for r in records
                         if r["scene"] == scene and r["variant"] == variant
                         and r["appearance"] == appearance and label in r])
                    with np.errstate(invalid="ignore"):
                        mean = np.nanmean(stacked, axis=0)
                    seen = ~np.isnan(mean)
                    difference = (mean[seen] - np.array(shape)[seen])
                    worst = float(np.abs(difference).max())
                    codes = float(np.abs(difference).max() * amplitudes.mean())
                    summary[f"{variant}/{appearance}/{scene}/{label}"] = {
                        "sampleCount": len(rows),
                        "comparedDepths": [round(float(d), 2)
                                           for d in DEPTHS[seen]],
                        "meanReferenceExcessCodes": round(
                            float(amplitudes.mean()), 3),
                        "worstWeightDifferenceFromCircle": round(worst, 4),
                        "worstDifferenceCodes": round(codes, 3),
                    }
                    print(f"    {scene:20s} {label:8s} "
                          + " ".join("    -" if np.isnan(v) else f"{v:5.3f}"
                                     for v in mean)
                          + f"   worst {worst:6.4f} of a "
                          f"{amplitudes.mean():6.2f} code rim = "
                          f"{codes:6.2f} codes")

    if arguments.output is not None:
        arguments.output.write_text(
            json.dumps({"schemaVersion": 1, "osBuild": "25G76",
                        "classification": "Liquid Glass rim on non-circles",
                        "comparison": summary,
                        "records": records}, indent=2, sort_keys=True) + "\n",
            encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
