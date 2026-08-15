#!/usr/bin/env python3
"""Measure a reveal frame's circle geometry from its coverage contour.

The 65-state ladder proves walle's geometry model: the circle's bounds are
snapped to integers, and a finer rounding grid is not just unnecessary but
refuted - a half-integer grid changes 52 of those 65 states, a quarter-integer
grid 63, and all 65 are byte-exact as they stand.

The corpus also holds frames from the LIVE animation, and one of them lands
where that grid cannot reach.  This measures where each frame's circle actually
is, to about a hundredth of a pixel, so the live path's law can be read off many
frames instead of guessed from one.

Method: walk rays out from an assumed centre, find where coverage crosses the
half value by linear interpolation between bilinear samples, and fit

    rho(theta) = radius + dx * cos(theta) + dy * sin(theta)

which is the first-order response of a circle's contour to being translated.
Re-centre and repeat until dx and dy vanish.  The fit's own residual is
reported: on a real circle it lands near 0.01 px, and anything larger means the
contour is not a translated circle and the numbers should not be trusted.
"""

import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image

type JsonObject = dict[str, object]


def coverage(path: Path) -> np.ndarray:
    """The reveal's coverage plane, 0..255, whatever the file's layout."""
    if path.suffix == ".r8":
        data = np.frombuffer(path.read_bytes(), dtype=np.uint8)
        extent = int(round(len(data) ** 0.5))
        return data.reshape(extent, extent).astype(float)
    pixels = np.asarray(Image.open(path).convert("RGBA")).astype(float)
    return pixels[..., 0]


def contour_radius(plane: np.ndarray, center: tuple[float, float],
                   theta: float, low: float, high: float) -> float | None:
    height, width = plane.shape
    samples = np.linspace(low, high, int((high - low) * 200))
    x = center[0] + samples * np.cos(theta)
    y = center[1] + samples * np.sin(theta)
    inside = (x >= 0) & (x < width - 1) & (y >= 0) & (y < height - 1)
    if inside.sum() < 8:
        return None
    samples, x, y = samples[inside], x[inside], y[inside]
    x0, y0 = x.astype(int), y.astype(int)
    fx, fy = x - x0, y - y0
    value = (plane[y0, x0] * (1 - fx) * (1 - fy)
             + plane[y0, x0 + 1] * fx * (1 - fy)
             + plane[y0 + 1, x0] * (1 - fx) * fy
             + plane[y0 + 1, x0 + 1] * fx * fy)
    crossings = np.nonzero(np.diff(np.sign(value - 127.5)))[0]
    if len(crossings) == 0:
        return None
    index = crossings[0]
    span = value[index + 1] - value[index]
    fraction = 0.0 if span == 0 else (127.5 - value[index]) / span
    return float(samples[index] + fraction * (samples[index + 1] - samples[index]))


def measure(plane: np.ndarray, center: tuple[float, float], radius: float,
            *, window: float = 12.0, iterations: int = 6) -> JsonObject | None:
    """Circle centre and radius from the contour, refined until it stops moving."""
    cx, cy = center
    angles = np.radians(np.arange(-180.0, 180.0, 1.0))
    for _ in range(iterations):
        rows = []
        for theta in angles:
            found = contour_radius(plane, (cx, cy), theta,
                                   radius - window, radius + window)
            if found is not None:
                rows.append((theta, found))
        if len(rows) < 24:
            return None
        theta = np.array([row[0] for row in rows])
        rho = np.array([row[1] for row in rows])
        design = np.column_stack([np.cos(theta), np.sin(theta), np.ones(len(rho))])
        solution, *_ = np.linalg.lstsq(design, rho, rcond=None)
        residual = design @ solution - rho
        cx += float(solution[0])
        cy += float(solution[1])
        radius = float(solution[2])
        if abs(solution[0]) < 1e-4 and abs(solution[1]) < 1e-4:
            break
    return {
        "centerX": round(cx, 4),
        "centerY": round(cy, 4),
        "radius": round(radius, 4),
        "rayCount": len(rho),
        "residualRmsPixels": round(float(np.sqrt((residual**2).mean())), 5),
        "residualMaxPixels": round(float(np.abs(residual).max()), 5),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("frames", type=Path, nargs="+")
    parser.add_argument("--center", type=float, nargs=2, default=(512.0, 614.4),
                        help="starting centre guess, in capture pixels")
    parser.add_argument("--maximum-radius", type=float, default=2164.1045)
    parser.add_argument("--crop-top", type=int, default=0,
                        help="rows the manifest excludes from the top")
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()

    records: list[JsonObject] = []
    for path in arguments.frames:
        plane = coverage(path)[arguments.crop_top:]
        center = (arguments.center[0], arguments.center[1] - arguments.crop_top)
        # Seed by finding the contour once along whichever axis stays inside
        # the frame, so no progress is assumed and a circle clipped by the
        # frame - which every late reveal state is - still seeds correctly.
        seed = None
        for degrees in (0.0, 90.0, 180.0, 270.0, 45.0, 135.0):
            seed = contour_radius(plane, center, np.radians(degrees),
                                  1.0, arguments.maximum_radius * 1.2)
            if seed is not None:
                break
        if seed is None:
            continue
        fit = measure(plane, center, seed)
        if fit is None:
            continue
        fit["frame"] = path.name
        fit["centerY"] = round(fit["centerY"] + arguments.crop_top, 4)
        fit["radiusFraction"] = round(fit["radius"] / arguments.maximum_radius, 8)
        records.append(fit)
        print(f"  {path.name:20s} centre=({fit['centerX']:9.4f},"
              f" {fit['centerY']:9.4f}) radius={fit['radius']:10.4f}"
              f"  fraction={fit['radiusFraction']:.6f}"
              f"  residual {fit['residualRmsPixels']:.5f} rms /"
              f" {fit['residualMaxPixels']:.5f} max px")

    if arguments.output is not None:
        arguments.output.write_text(
            json.dumps({"schemaVersion": 1, "records": records},
                       indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
