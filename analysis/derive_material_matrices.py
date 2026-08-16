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

Trivariate POLYNOMIALS in sRGB code space are fitted at orders one through
four and CROSS-VALIDATED, alongside the affine-in-x**g model the shader used to
carry, because the whole risk with a richer model is that it fits the sampled
colours rather than the material.  Order two wins over affine everywhere:

                      affine        order 2
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
A polynomial in sRGB code space beats it on held-out error and needs no pow()
in the shader.

sRGB CODE space, not linear light, and that is measured too.  Compositing is
usually a linear-light operation, so the obvious theory is that the material
blends there and the sRGB curve is what bends the response.  It does not: the
same polynomial fitted on linearised inputs and outputs scores 6.18 rms code
values held out against 2.03 for `clear`, and 13.85 against 0.90 for `regular`
in dark.  Every order tested is worse in linear light than in code space.

Over a FLAT background the blur is the identity, so these captures constrain
the transfer alone and none of the blur's error leaks in.  A background is
dropped WHOLE when any channel clips: the material mixes channels, so a pinned
channel is a wrong input to the other two rows, not merely a missing output for
its own.

The lattice has to reach the FACES of the cube.  A first lattice sampled
32/96/160/224 per channel - all interior - and a fit dominated by its 64
interior points missed the faces by up to 9.7 code values, reading 31 in red
where `clear` over cyan measures 41, because the only samples out there were
seven named colours.  The levels are a capture-time choice (WALLE_CUBE_LEVELS)
and the corpus now carries a second, interleaved lattice through 0 and 255.
"""

import argparse
import itertools
import json
import re
from pathlib import Path

import numpy as np
from PIL import Image

SHAPE = "circle-0500-center"
INTERIOR_RADIUS = 400
CLIP_LOW, CLIP_HIGH = 0.5, 254.5
ORDERS = (1, 2, 3, 4, 5, 6)
# The exponent triples of a trivariate polynomial, ordered by TOTAL DEGREE, so
# a lower order's terms are exactly the first terms of a higher one.  That is
# what lets the generated header pad whichever order was chosen out to one
# fixed width with zeros and evaluate identically.
EXPONENTS = tuple(
    triple
    for triple in sorted(itertools.product(range(max(ORDERS) + 1), repeat=3),
                         key=lambda t: (sum(t), t))
    if sum(triple) <= max(ORDERS)
)
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


def gather(shots: list[Path], variant: str, appearance: str
           ) -> tuple[np.ndarray, np.ndarray]:
    """Every flat background across the given corpora, deduplicated by name.

    The two lattices live in separate directories because they were captured
    months apart; where they overlap the frames are byte-identical, so the
    first one that carries a name wins and the union is what gets fitted.
    """
    seen: dict[str, np.ndarray] = {}
    for directory in shots:
        for path in sorted(directory.glob(
                f"*__{SHAPE}__{variant}__{appearance}.png")):
            name = path.name.split("__")[0]
            if name in seen or background_code(name) is None:
                continue
            seen[name] = interior(path)
    codes, values = [], []
    for name, value in sorted(seen.items()):
        if value.min() <= CLIP_LOW or value.max() >= CLIP_HIGH:
            continue
        codes.append(background_code(name))
        values.append(value)
    return np.array(codes), np.array(values)


def term_count(order: int) -> int:
    return sum(1 for triple in EXPONENTS if sum(triple) <= order)


def design_for(codes: np.ndarray, model: str, exponent: float) -> np.ndarray:
    unit = codes / 255.0
    if model == "power":
        return np.column_stack([unit**exponent, np.ones(len(unit))])
    order = int(model.removeprefix("order"))
    red, green, blue = unit[:, 0], unit[:, 1], unit[:, 2]
    return np.column_stack([
        red**i * green**j * blue**k
        for i, j, k in EXPONENTS if i + j + k <= order
    ])


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
                   exponent: float, folds: int = 8
                   ) -> tuple[float, float] | None:
    """Held-out error, so a richer model cannot win by memorising."""
    width = design_for(codes[:1], model, exponent).shape[1]
    index = np.arange(len(codes))
    errors = []
    for fold in range(folds):
        test = index % folds == fold
        if test.sum() == 0 or (~test).sum() < 2 * width:
            return None
        solution = solve(codes[~test], values[~test], model, exponent)
        errors.extend((predict(codes[test], solution, model, exponent)
                       - values[test]).ravel())
    error = np.array(errors)
    return float(np.sqrt((error**2).mean())), float(np.abs(error).max())


def fit(shots: list[Path], variant: str, appearance: str) -> JsonObject | None:
    codes, values = gather(shots, variant, appearance)
    if len(codes) < 20:
        return None

    best_exponent, best_score = 1.0, None
    for exponent in np.arange(0.55, 1.45, 0.005):
        score = cross_validate(codes, values, "power", float(exponent))
        if score is not None and (best_score is None or score[0] < best_score):
            best_exponent, best_score = float(exponent), score[0]

    scored = []
    for model, exponent in ([("power", best_exponent)]
                            + [(f"order{order}", 1.0) for order in ORDERS]):
        held = cross_validate(codes, values, model, exponent)
        if held is None:
            continue
        solution = solve(codes, values, model, exponent)
        residual = predict(codes, solution, model, exponent) - values
        scored.append({
            "model": model,
            "exponent": round(exponent, 4),
            "termCount": int(solution.shape[0]),
            "heldOutRootMeanSquareCodes": round(held[0], 3),
            "heldOutMaximumCodes": round(held[1], 3),
            "inSampleRootMeanSquareCodes": round(
                float(np.sqrt((residual**2).mean())), 3),
            "solution": solution,
        })
    if not scored:
        return None
    chosen = min(scored, key=lambda entry: entry["heldOutRootMeanSquareCodes"])
    solution = chosen["solution"]
    for entry in scored:
        entry.pop("solution", None)

    gray = np.array([abs(c[0] - c[1]) < 1e-9 and abs(c[1] - c[2]) < 1e-9
                     for c in codes])
    residual = predict(codes, solution, chosen["model"], chosen["exponent"]) - values
    order = 0 if chosen["model"] == "power" else int(
        chosen["model"].removeprefix("order"))
    return {
        "variant": variant,
        "appearance": appearance,
        "backgroundCount": int(len(codes)),
        "grayBackgroundCount": int(gray.sum()),
        "model": chosen["model"],
        "order": order,
        "exponent": chosen["exponent"],
        # One entry per basis term, in EXPONENTS order, each the term's
        # contribution to (R, G, B) out - the layout the generated header's
        # WalleMaterialTransfer carries straight through.
        "termExponents": [list(triple) for triple in EXPONENTS[:len(solution)]],
        "coefficients": [[round(float(v), 8) for v in row] for row in solution],
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
    parser.add_argument("--shots", type=Path, nargs="+", required=True,
                        help="one or more capture directories, merged by name")
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
            print(f"        {entry['model']:9s} g={entry['exponent']:.3f} "
                  f"{entry['termCount']:2d} terms  "
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
