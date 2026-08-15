#!/usr/bin/env python3
"""Derive Apple's `.tint()` law for both Glass variants, on macOS 26.6.1.

WHAT THE CAPTURES SAY.  The tinted element is exactly affine in the UNTINTED
material's own output - not in the raw backdrop - and in that variable it
separates cleanly into luminance and chroma:

    substrate = the same material, same appearance, no tint
    subLuma   = kLuma . substrate
    out       = base(tint) + beta(tint) * subLuma
                           + gamma(tint) * (substrate - subLuma)

and gamma is the whole story.  Across every tint measured it is either one or
zero and nothing between: 0.96 to 1.16 for all seven NEUTRAL tints, and -0.009
to +0.015 for all eleven chromatic ones, including a tint only 27 code values
off the gray axis.  A neutral tint replaces the backdrop's lightness and lets
its colour through untouched; any chromatic tint replaces the colour outright.

Fitted per tint this is five free numbers where the general affine map needs
twelve, and it lands at 0.6 to 6.2 code values per tint against the general
map's 0.6 to 22 - so it is not merely a smaller model, it is a better one.

WHY THE EARLIER FITS FAILED.  Three separate errors, each of which alone was
enough to hide this:

  * the substrate was read against the raw BACKDROP.  The untinted material
    clamps on saturated backgrounds, so a neutral tint over red, green and
    blue does not sum to what it does over white - by 54 code values - and
    that reads as nonlinearity when it is the material's own clipping;
  * backgrounds whose SUBSTRATE clips were kept.  The substrate is the
    regressor here, so a pinned channel is a wrong input, not a missing
    output, and because the tint mixes channels one pinned channel corrupts
    all three rows.  The four backgrounds that pin `regular` in light are
    exactly the saturated ones the chroma response is read from;
  * stage two fitted the tint-to-coefficient map to the COEFFICIENTS.  The
    per-tint matrices are individually underdetermined - many fit the sampled
    backgrounds equally well - so their scatter is unidentifiable noise, and
    chasing it produced a law with a 17 code rms.

WHAT THIS FIXES.  `.clear.tint()` had never been captured at all - the harness
declares the overlay but no corpus run produced one - so walle shipped the
regular law for both variants, and its shader took the tint branch before
checking the variant.  The two differ by up to 50 code values over the same
backdrop, because clear passes roughly three and a half times as much of it.

End to end, predicting each captured interior from the tint colour and the
background alone:

    regular light   31.7 rms / 146 max  ->  5.05 / 23.7
    regular dark    18.0 / 114          ->  4.85 / 24.6
    clear   light   26.4 / 142          ->  4.80 / 26.7
    clear   dark    19.0 / 108          ->  5.17 / 14.6
"""

import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image

SHAPE = "circle-0500-center"
BACKGROUNDS = (
    "gray-000", "gray-064", "gray-128", "gray-192", "gray-255",
    "red-128", "green-128", "blue-128", "cyan-128", "magenta-128",
    "yellow-128", "orange", "violet", "red-255", "green-255", "blue-255",
)
INTERIOR_RADIUS = 400
CLIP_LOW, CLIP_HIGH = 0.5, 254.5
LUMA = np.array([0.2126, 0.7152, 0.0722])

# Exact sRGB components handed to Color(.sRGB:), not guesses about a system
# palette - which is why the saturated system tints were abandoned for these.
MID = {
    "M0": (0.70, 0.35, 0.35), "M1": (0.35, 0.70, 0.35),
    "M2": (0.35, 0.35, 0.70), "M3": (0.65, 0.65, 0.30),
    "M4": (0.30, 0.65, 0.65), "M5": (0.65, 0.30, 0.65),
    "M6": (0.50, 0.50, 0.50), "M7": (0.60, 0.45, 0.30),
}
LUMINANCE = {f"L{int(v * 100):02d}": (v, v, v)
             for v in (0.10, 0.25, 0.40, 0.55, 0.70, 0.85)}
SATURATION = {f"S{int(k * 100):02d}":
              (0.5 + 0.25 * k, 0.5 - 0.25 * k, 0.5 - 0.25 * k)
              for k in (0.25, 0.50, 0.75, 1.00)}
# The fine ladder that brackets where gamma falls from one to zero.  It found
# no transition to bracket: a tint 0.645 code values off the gray axis already
# has gamma = -0.0001, and only an EXACTLY gray one has gamma = 1.  The switch
# is exact equality, which is what a colour-SPACE distinction looks like - an
# exactly gray sRGB colour resolves to a grayscale space and takes the other
# path - rather than a numeric threshold.  beta switches with it: 0.24 for
# every one of these against 0.087 for exact gray, in regular/light.
FINE = {f"N{index:d}": (0.5 + d, 0.5 - d / 2, 0.5 - d / 2)
        for index, d in enumerate((0.002, 0.005, 0.010, 0.020, 0.040, 0.080))}
# Twelve more colours spanning hue, saturation and level, all mid-intensity so
# nothing clips - the chromatic branch is sample-limited, not structural.
EXTRA = {
    "X00": (0.60, 0.40, 0.50), "X01": (0.40, 0.60, 0.50),
    "X02": (0.50, 0.40, 0.60), "X03": (0.70, 0.55, 0.40),
    "X04": (0.40, 0.55, 0.70), "X05": (0.55, 0.70, 0.40),
    "X06": (0.30, 0.45, 0.60), "X07": (0.45, 0.30, 0.60),
    "X08": (0.75, 0.60, 0.60), "X09": (0.25, 0.40, 0.40),
    "X10": (0.55, 0.35, 0.45), "X11": (0.35, 0.55, 0.45),
}

type JsonObject = dict[str, object]


def interior(path: Path) -> np.ndarray | None:
    if not path.exists():
        return None
    pixels = np.asarray(Image.open(path).convert("RGB")).astype(float)
    height, width, _ = pixels.shape
    y, x = np.mgrid[0:height, 0:width]
    inside = ((x - width // 2) ** 2 + (y - height // 2) ** 2) < INTERIOR_RADIUS**2
    return pixels[inside].mean(axis=0)


def substrate_table(shots: Path, variant: str, appearance: str
                    ) -> dict[str, np.ndarray]:
    """The same material with no tint - the tint's substrate.

    Backgrounds whose substrate has ANY clipped channel are dropped: the
    substrate is the regressor, so a pinned channel is a wrong input whose
    true internal value is above 255 and unknowable, and the tint mixes
    channels, so one pinned channel corrupts all three rows.
    """
    table = {}
    for name in BACKGROUNDS:
        value = interior(shots / f"{name}__{SHAPE}__{variant}__{appearance}.png")
        if value is None or value.min() <= CLIP_LOW or value.max() >= CLIP_HIGH:
            continue
        table[name] = value
    return table


def chroma_magnitude(tint: np.ndarray) -> float:
    return float(np.linalg.norm(tint - float(LUMA @ tint)))


def solve_per_tint(samples, gamma: float | None = None) -> JsonObject | None:
    """base (3), beta (3) and gamma (1) for one tint, clipping excluded."""
    rows, targets = [], []
    for substrate, measured in samples:
        luminance = float(LUMA @ substrate)
        chroma = substrate - luminance
        for channel in range(3):
            if not (CLIP_LOW < measured[channel] < CLIP_HIGH):
                continue
            row = np.zeros(7)
            row[channel] = 1.0
            row[3 + channel] = luminance
            row[6] = chroma[channel]
            rows.append(row)
            targets.append(measured[channel])
    if len(rows) < 10:
        return None
    design, target = np.array(rows), np.array(targets)
    if gamma is not None:
        target = target - gamma * design[:, 6]
        design = design[:, :6]
    solution, *_ = np.linalg.lstsq(design, target, rcond=None)
    residual = np.abs(design @ solution - target)
    return {
        "base": [round(float(v), 4) for v in solution[0:3]],
        "beta": [round(float(v), 6) for v in solution[3:6]],
        "gamma": round(float(solution[6]) if gamma is None else gamma, 5),
        "maximumResidualCodes": round(float(residual.max()), 3),
    }


def tint_terms(tint: np.ndarray, neutral: bool) -> list[float]:
    """The basis base and beta are expanded in, for one tint.

    A neutral tint is one-dimensional, so its functions take the tint's single
    level; a quadratic in that level costs three numbers and is free.

    A chromatic tint gets a full quadratic in the three components.  An affine
    basis is NOT enough, and the evidence is held-out rather than in-sample:
    leaving each tint out in turn and predicting it from the other 28, affine
    scores 6.43 rms / 45.3 max code values against quadratic's 4.88 / 33.5 for
    regular in light.  The affine map's failures are the strongly chromatic
    tints, whose base runs negative in the channel opposite their hue - a green
    tint's red base is -35 - and a plane through the rest of the colour cube
    cannot bend far enough to reach them.
    """
    if neutral:
        level = float(LUMA @ tint) / 255.0
        return [level, level * level, 1.0]
    red, green, blue = tint / 255.0
    return [red, green, blue, 1.0,
            red * red, green * green, blue * blue,
            red * green, red * blue, green * blue]


def fit_regime(items, neutral: bool) -> tuple[np.ndarray, np.ndarray] | None:
    """base and beta as functions of the tint, fitted to the DATA.

    Fitting to the per-tint coefficients instead is what produced a 17 code
    law: those coefficients are individually underdetermined, so their scatter
    is noise rather than signal.  Here every captured pixel is a row, and the
    unknowns enter linearly, so this is still ordinary least squares.
    """
    solutions = []
    residuals = []
    for channel in range(3):
        rows, targets = [], []
        for tint, samples in items:
            terms = tint_terms(tint, neutral)
            for substrate, measured in samples:
                if not (CLIP_LOW < measured[channel] < CLIP_HIGH):
                    continue
                luminance = float(LUMA @ substrate)
                chroma = substrate[channel] - luminance
                rows.append([*terms, *(np.array(terms) * luminance)])
                targets.append(measured[channel] - (chroma if neutral else 0.0))
        if not rows or len(rows) < 2 * len(rows[0]):
            return None
        design, target = np.array(rows), np.array(targets)
        solution, *_ = np.linalg.lstsq(design, target, rcond=None)
        solutions.append(solution)
        residuals.append(design @ solution - target)
    return np.array(solutions), np.concatenate(residuals)


def evaluate(solutions: np.ndarray, tint: np.ndarray, substrate: np.ndarray,
             neutral: bool) -> np.ndarray:
    terms = np.array(tint_terms(tint, neutral))
    luminance = float(LUMA @ substrate)
    chroma = substrate - luminance
    width = len(terms)
    base = solutions[:, :width] @ terms
    beta = solutions[:, width:] @ terms
    return np.clip(base + beta * luminance + (chroma if neutral else 0.0),
                   0.0, 255.0)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--shots", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    # MEASURED as exact equality, so any value below the smallest chroma an
    # 8-bit tint can carry (0.845, for a one-code difference) is equivalent.
    parser.add_argument("--neutral-chroma", type=float, default=0.5,
                        help="chroma magnitude below which a tint is neutral")
    arguments = parser.parse_args()

    colours = {**MID, **LUMINANCE, **SATURATION, **FINE, **EXTRA}
    ladders = {**LUMINANCE, **SATURATION, **FINE, **EXTRA}
    overlays = {
        "regular": {**{k: f"sweepTint{k}" for k in MID},
                    **{k: f"tint{k}" for k in ladders}},
        "clear": {**{k: f"clearTint{k}" for k in MID},
                  **{k: f"clearTint{k}" for k in ladders}},
    }

    report: JsonObject = {
        "schemaVersion": 2,
        "classification": "Apple Liquid Glass tint law, both variants",
        "osBuild": "25G76",
        "model": ("out = base(tint) + beta(tint) * lumaOf(untinted)"
                  " + gamma(tint) * chromaOf(untinted)"),
        "variants": {},
    }
    for variant, names in overlays.items():
        entry: JsonObject = {}
        for appearance in ("light", "dark"):
            substrate = substrate_table(arguments.shots, variant, appearance)
            if len(substrate) < 6:
                continue
            measured = {}
            for short, overlay in names.items():
                samples = [
                    (value, reading)
                    for name, value in substrate.items()
                    if (reading := interior(
                        arguments.shots
                        / f"{name}__{SHAPE}__{overlay}__{appearance}.png")) is not None
                ]
                if samples:
                    measured[short] = samples
            if not measured:
                continue

            free = {
                short: fit
                for short, samples in measured.items()
                if (fit := solve_per_tint(samples)) is not None
            }
            regimes = {"neutral": [], "chromatic": []}
            for short, samples in measured.items():
                tint = np.array(colours[short]) * 255.0
                key = ("neutral" if chroma_magnitude(tint) < arguments.neutral_chroma
                       else "chromatic")
                regimes[key].append((tint, samples))

            fitted: JsonObject = {}
            errors = []
            for key, items in regimes.items():
                if len(items) < 3:
                    continue
                result = fit_regime(items, key == "neutral")
                if result is None:
                    continue
                solutions, residual = result
                fitted[key] = {
                    "tintCount": len(items),
                    "coefficients": [[round(float(v), 8) for v in row]
                                     for row in solutions],
                    "maximumResidualCodes": round(float(np.abs(residual).max()), 3),
                    "rootMeanSquareResidualCodes": round(
                        float(np.sqrt((residual**2).mean())), 3),
                }
                errors.append(residual)
            if not errors:
                continue
            total = np.concatenate(errors)
            entry[appearance] = {
                "perTint": {
                    short: {
                        **fit,
                        "chromaMagnitude": round(
                            chroma_magnitude(np.array(colours[short]) * 255.0), 2),
                    }
                    for short, fit in sorted(free.items())
                },
                "regimes": fitted,
                "maximumResidualCodes": round(float(np.abs(total).max()), 3),
                "rootMeanSquareResidualCodes": round(
                    float(np.sqrt((total**2).mean())), 3),
            }
            print(f"{variant:8s} {appearance:5s} "
                  f"rms={entry[appearance]['rootMeanSquareResidualCodes']:6.3f} "
                  f"max={entry[appearance]['maximumResidualCodes']:7.3f} codes"
                  + "".join(f"   {k}:{v['tintCount']}" for k, v in fitted.items()))
        report["variants"][variant] = entry

    if arguments.output is not None:
        arguments.output.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
