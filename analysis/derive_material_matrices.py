#!/usr/bin/env python3
"""Fit the untinted Liquid Glass colour transfer, and choose its model honestly.

The transfer used to be fitted from sixteen backgrounds - five of them neutral,
six saturated enough to clip the material - and keeping the fit honest on the
gray axis needed a four-to-one weighting on the neutral samples.  That
weighting was a judgement, not a measurement, and it is gone: the corpus now
carries a lattice of flat colours spanning the cube, so the fit is determined
by where the data is rather than by how it is weighted.

Two facts the gray ladder establishes before any model is chosen.  `clear` is
affine in sRGB CODE space - its ladder is a straight line there to 0.27 code
values rms.  `regular` is NOT: its response bows ABOVE the chord by up to 4.4
code values at mid-scale, in both appearances, which no affine map can produce.

Four models are fitted for that bend and CROSS-VALIDATED, because the whole
risk with a richer model is that it fits the sampled colours rather than the
material:

  * affine, the baseline;
  * affine in x**g, one exponent per material - what the shader used to carry;
  * affine plus the squared inputs;
  * affine plus the squared inputs AND the three cross terms.

The last wins everywhere, on held-out backgrounds and by a wide margin:

                      affine        this
    regular light   3.65 / 15.8   1.58 / 6.77
    regular dark    2.08 /  8.89  0.90 / 5.43
    clear           2.88 / 16.0   2.03 / 13.0

and it also removes the reason the neutral weighting existed - the gray axis
falls from 5.66 to 0.87 code values for `regular` in light without any
weighting at all.

It also retires an unexplained constant.  The 0.795 exponent the shader used to
carry was fitted from sixteen backgrounds; refitted on the lattice the same
model returns 1.195, which is not a refinement but a sign the exponent was
never a property of the material - it was the sparse background set's shape.
A second-order polynomial in sRGB code space beats it on held-out error and
needs no pow() in the shader.

Over a FLAT background the blur is the identity, so these captures constrain
the transfer alone and none of the blur's error leaks in.  A background is
dropped WHOLE when any channel clips: the material mixes channels, so a pinned
channel is a wrong input to the other two rows, not merely a missing output for
its own.
"""

import argparse
import json
import re
from pathlib import Path

import numpy as np
from PIL import Image

SHAPE = "circle-0500-center"
INTERIOR_RADIUS = 400
CLIP_LOW, CLIP_HIGH = 0.5, 254.5
NAMED = {
    "red-128": (128.0, 0.0, 0.0), "green-128": (0.0, 128.0, 0.0),
    "blue-128": (0.0, 0.0, 128.0), "cyan-128": (0.0, 128.0, 128.0),
    "magenta-128": (128.0, 0.0, 128.0), "yellow-128": (128.0, 128.0, 0.0),
    "orange": (255.0, 128.0, 0.0), "violet": (128.0, 0.0, 255.0),
    "red-255": (255.0, 0.0, 0.0), "green-255": (0.0, 255.0, 0.0),
    "blue-255": (0.0, 0.0, 255.0), "cyan-255": (0.0, 255.0, 255.0),
    "magenta-255": (255.0, 0.0, 255.0), "yellow-255": (255.0, 255.0, 0.0),
}
GRAY = re.compile(r"^gray-(\d{3})$")
CUBE = re.compile(r"^cube-(\d{3})-(\d{3})-(\d{3})$")

type JsonObject = dict[str, object]


def background_code(name: str) -> tuple[float, float, float] | None:
    if (match := GRAY.match(name)) is not None:
        level = float(match.group(1))
        return (level, level, level)
    if (match := CUBE.match(name)) is not None:
        return (float(match.group(1)), float(match.group(2)),
                float(match.group(3)))
    return NAMED.get(name)


def interior(path: Path) -> np.ndarray:
    pixels = np.asarray(Image.open(path).convert("RGB")).astype(float)
    height, width, _ = pixels.shape
    y, x = np.mgrid[0:height, 0:width]
    inside = ((x - width // 2) ** 2 + (y - height // 2) ** 2) < INTERIOR_RADIUS**2
    return pixels[inside].mean(axis=0)


def gather(shots: Path, variant: str, appearance: str
           ) -> tuple[np.ndarray, np.ndarray]:
    codes, values = [], []
    for path in sorted(shots.glob(f"*__{SHAPE}__{variant}__{appearance}.png")):
        code = background_code(path.name.split("__")[0])
        if code is None:
            continue
        value = interior(path)
        if value.min() <= CLIP_LOW or value.max() >= CLIP_HIGH:
            continue
        codes.append(code)
        values.append(value)
    return np.array(codes), np.array(values)


def design_for(codes: np.ndarray, model: str, exponent: float) -> np.ndarray:
    unit = codes / 255.0
    red, green, blue = unit[:, 0], unit[:, 1], unit[:, 2]
    one = np.ones(len(unit))
    if model == "affine":
        return np.column_stack([unit, one])
    if model == "power":
        return np.column_stack([unit**exponent, one])
    if model == "quadratic":
        return np.column_stack([unit, unit**2, one])
    return np.column_stack([unit, unit**2, red * green, red * blue,
                            green * blue, one])


def solve(codes: np.ndarray, values: np.ndarray, model: str, exponent: float):
    design = design_for(codes, model, exponent)
    target = (values / 255.0) ** exponent if model == "power" else values / 255.0
    solution, *_ = np.linalg.lstsq(design, target, rcond=None)
    return solution


def predict(codes: np.ndarray, solution, model: str, exponent: float
            ) -> np.ndarray:
    raised = design_for(codes, model, exponent) @ solution
    if model == "power":
        raised = np.clip(raised, 0.0, None) ** (1.0 / exponent)
    return np.clip(raised * 255.0, 0.0, 255.0)


def cross_validate(codes: np.ndarray, values: np.ndarray, model: str,
                   exponent: float, folds: int = 8) -> tuple[float, float]:
    """Held-out error, so a richer model cannot win by memorising."""
    order = np.arange(len(codes))
    errors = []
    for fold in range(folds):
        test = order % folds == fold
        if test.sum() == 0 or (~test).sum() < 12:
            continue
        solution = solve(codes[~test], values[~test], model, exponent)
        errors.extend((predict(codes[test], solution, model, exponent)
                       - values[test]).ravel())
    error = np.array(errors)
    return float(np.sqrt((error**2).mean())), float(np.abs(error).max())


def fit(shots: Path, variant: str, appearance: str) -> JsonObject | None:
    codes, values = gather(shots, variant, appearance)
    if len(codes) < 20:
        return None

    best_exponent, best_score = 1.0, None
    for exponent in np.arange(0.55, 1.45, 0.005):
        score = cross_validate(codes, values, "power", float(exponent))[0]
        if best_score is None or score < best_score:
            best_exponent, best_score = float(exponent), score

    scored = []
    for model, exponent in (("affine", 1.0), ("power", best_exponent),
                            ("quadratic", 1.0), ("quadraticCross", 1.0)):
        held = cross_validate(codes, values, model, exponent)
        solution = solve(codes, values, model, exponent)
        residual = predict(codes, solution, model, exponent) - values
        scored.append({
            "model": model,
            "exponent": round(exponent, 4),
            "heldOutRootMeanSquareCodes": round(held[0], 3),
            "heldOutMaximumCodes": round(held[1], 3),
            "inSampleRootMeanSquareCodes": round(
                float(np.sqrt((residual**2).mean())), 3),
            "solution": solution,
        })
    chosen = min(scored, key=lambda entry: entry["heldOutRootMeanSquareCodes"])
    solution = chosen["solution"]
    for entry in scored:
        entry.pop("solution", None)

    quadratic = chosen["model"] in ("quadratic", "quadraticCross")
    cross = chosen["model"] == "quadraticCross"
    gray = np.array([abs(c[0] - c[1]) < 1e-9 and abs(c[1] - c[2]) < 1e-9
                     for c in codes])
    residual = predict(codes, solution, chosen["model"], chosen["exponent"]) - values
    return {
        "variant": variant,
        "appearance": appearance,
        "backgroundCount": int(len(codes)),
        "grayBackgroundCount": int(gray.sum()),
        "model": chosen["model"],
        "exponent": chosen["exponent"],
        # Rows are the contribution of one INPUT channel to (R, G, B) out, which
        # is the layout the shader's kFromR / kFromG / kFromB carry.
        "fromR": [round(float(solution[0][c]), 6) for c in range(3)],
        "fromG": [round(float(solution[1][c]), 6) for c in range(3)],
        "fromB": [round(float(solution[2][c]), 6) for c in range(3)],
        "squaredFromR": [round(float(solution[3][c]), 6) for c in range(3)]
                        if quadratic else [0.0, 0.0, 0.0],
        "squaredFromG": [round(float(solution[4][c]), 6) for c in range(3)]
                        if quadratic else [0.0, 0.0, 0.0],
        "squaredFromB": [round(float(solution[5][c]), 6) for c in range(3)]
                        if quadratic else [0.0, 0.0, 0.0],
        "crossRedGreen": [round(float(solution[6][c]), 6) for c in range(3)]
                         if cross else [0.0, 0.0, 0.0],
        "crossRedBlue": [round(float(solution[7][c]), 6) for c in range(3)]
                        if cross else [0.0, 0.0, 0.0],
        "crossGreenBlue": [round(float(solution[8][c]), 6) for c in range(3)]
                          if cross else [0.0, 0.0, 0.0],
        "offsetCodes": [round(float(solution[-1][c]), 6) for c in range(3)],
        "maximumResidualCodes": round(float(np.abs(residual).max()), 3),
        "rootMeanSquareResidualCodes": round(
            float(np.sqrt((residual**2).mean())), 3),
        "grayAxisMaximumResidualCodes": round(
            float(np.abs(residual[gray]).max()) if gray.any() else 0.0, 3),
        "heldOutRootMeanSquareCodes": chosen["heldOutRootMeanSquareCodes"],
        "heldOutMaximumCodes": chosen["heldOutMaximumCodes"],
        "candidates": scored,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--shots", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()

    records = [
        record
        for variant in ("regular", "clear")
        for appearance in ("light", "dark")
        if (record := fit(arguments.shots, variant, appearance)) is not None
    ]
    for record in records:
        print(f"  {record['variant']:8s} {record['appearance']:5s} "
              f"{record['backgroundCount']:3d} backgrounds "
              f"({record['grayBackgroundCount']} neutral)  chose "
              f"{record['model']}"
              + (f" g={record['exponent']}" if record["model"] == "power" else "")
              + f"   rms={record['rootMeanSquareResidualCodes']:6.3f} "
              f"max={record['maximumResidualCodes']:6.3f} "
              f"grayMax={record['grayAxisMaximumResidualCodes']:6.3f}")
        for entry in record["candidates"]:
            print(f"        {entry['model']:9s} g={entry['exponent']:.3f}  "
                  f"held-out rms={entry['heldOutRootMeanSquareCodes']:6.3f} "
                  f"max={entry['heldOutMaximumCodes']:7.3f}   in-sample rms="
                  f"{entry['inSampleRootMeanSquareCodes']:6.3f}")
    if arguments.output is not None:
        arguments.output.write_text(
            json.dumps({"schemaVersion": 2, "osBuild": "25G76",
                        "records": records}, indent=2, sort_keys=True) + "\n",
            encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
