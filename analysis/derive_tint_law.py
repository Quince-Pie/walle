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

WHAT IS LEFT is predicting a tint NOBODY CAPTURED, which is what the shader is
actually asked to do, and it was sample-limited rather than structural: from 29
chromatic tints a quadratic basis held one tint out at a time scored 4.4 to 5.2
code values rms.  Twenty-four more colours through the mid-intensity region
took that to 3.0 to 4.1, and with 53 tints in hand a CUBIC basis becomes
supportable and takes it to 2.2 to 3.7 - so the order is chosen here per
variant and appearance by that same held-out score, not fixed.  In-sample error
cannot make this choice: it falls with every term added whether the term is
real or not.

A PINNED OUTPUT IS A MEASUREMENT, NOT A HOLE.  A clipped SUBSTRATE has to go:
it is the regressor, its true value is above 255 and unknowable, and one wrong
input corrupts all three rows.  A clipped tinted OUTPUT is the opposite - the
target is at the rail, and that is a real one-sided reading: the true value is
at or past it.  Dropping those left four tints in the high-green corner
(Y11..Y14, all of them 0.30 red) contributing NO red rows at all, so the red
coefficient functions were unconstrained over exactly the region where M1 and
M4 sit, and they wandered: `regular` in dark predicted +19.5 codes of red over
green where the hardware reads 0.  Fitting them as inequalities instead - an
active set, so a pinned row only pulls while the fit is on the wrong side of
its rail - is what this does, and it takes `regular` dark from 24.9 to 11.3
code values on its worst tint and its neutral ladder from 20.9 to 5.2.

WHAT IT DOES NOT REACH is `clear` under a NEUTRAL tint over a saturated
backdrop: L40 over green-128 reads [0, 131, 0] where every affine-in-substrate
law that also fits the gray ladder wants about +16 red.  That is a limit of the
model's FORM, not of this fit - a full 3x3 affine map, a squared chroma term, a
chroma-magnitude term and the raw backdrop as regressor were each measured and
none closes it (11.2 codes at best, and worse everywhere else).  It is recorded
rather than papered over.
"""

import argparse
import functools
import itertools
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
# The element's own radius in SHAPE, and where inside it the rim peaks - see
# analysis/measure_rim_light.py.  Sampling there instead of the interior fits
# the same law for the RIM, which needs its own: a tinted rim is nothing like
# the tint law applied to an untinted one, missing it by ninety code values,
# because the rim is a separate layer that the tint filters separately.
ELEMENT_RADIUS = 500.0
RIM_DEPTH = 0.8
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
             for v in (0.00, 0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40,
                       0.45, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90,
                       0.95, 1.00)}
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

# Twenty-four more colours, a lattice through the mid-intensity region.  The
# quadratic basis has ten terms, and held out one tint at a time it predicted an
# unseen tint to about 5 code values rms from 29 chromatic samples - so the fit
# was sample-limited, and these are the samples.
WIDE = {
    "Y00": (0.30, 0.30, 0.45),
    "Y01": (0.30, 0.30, 0.60),
    "Y02": (0.30, 0.30, 0.75),
    "Y03": (0.30, 0.45, 0.30),
    "Y04": (0.30, 0.45, 0.45),
    "Y05": (0.30, 0.45, 0.60),
    "Y06": (0.30, 0.45, 0.75),
    "Y07": (0.30, 0.60, 0.30),
    "Y08": (0.30, 0.60, 0.45),
    "Y09": (0.30, 0.60, 0.60),
    "Y10": (0.30, 0.60, 0.75),
    "Y11": (0.30, 0.75, 0.30),
    "Y12": (0.30, 0.75, 0.45),
    "Y13": (0.30, 0.75, 0.60),
    "Y14": (0.30, 0.75, 0.75),
    "Y15": (0.45, 0.30, 0.30),
    "Y16": (0.45, 0.30, 0.45),
    "Y17": (0.45, 0.30, 0.60),
    "Y18": (0.45, 0.30, 0.75),
    "Y19": (0.45, 0.45, 0.30),
    "Y20": (0.45, 0.45, 0.60),
    "Y21": (0.45, 0.45, 0.75),
    "Y22": (0.45, 0.60, 0.30),
    "Y23": (0.45, 0.60, 0.45),
}

type JsonObject = dict[str, object]


# Set once from --region: "interior" reads the disc, "rim" the bright band.
REGION = "interior"


def locate(shots: list[Path], name: str) -> Path | None:
    """The tint corpus was captured over several sessions and lives in several
    directories; they are searched in order and the first hit wins."""
    return next((d / name for d in shots if (d / name).exists()), None)


@functools.cache
def interior(path: Path | None) -> np.ndarray | None:
    """Cached: the hold-out loop refits the same corpus dozens of times, and
    decoding a 1024 square PNG per call dominated the run."""
    if path is None or not path.exists():
        return None
    pixels = np.asarray(Image.open(path).convert("RGB")).astype(float)
    height, width, _ = pixels.shape
    y, x = np.mgrid[0:height, 0:width]
    distance = np.hypot(x + 0.5 - width / 2.0, y + 0.5 - height / 2.0)
    picked = (np.abs(distance - (ELEMENT_RADIUS - RIM_DEPTH)) < 0.1
              if REGION == "rim" else distance < INTERIOR_RADIUS)
    return pixels[picked].mean(axis=0) if picked.sum() >= 8 else None


def substrate_table(shots: list[Path], variant: str, appearance: str,
                    keep_clipping: bool = False) -> dict[str, np.ndarray]:
    """The same material with no tint - the tint's substrate.

    Backgrounds whose substrate has a clipped channel used to be dropped, on
    the argument that the substrate is the REGRESSOR, so a pinned channel is a
    wrong input whose true internal value is unknowable, and the tint mixes
    channels, so one pinned channel corrupts all three rows.

    That argument is about the wrong variable, and it was worth checking: the
    regressor the SHADER has is not Apple's internal substrate but
    `saturate(transfer)`, because walle's materialTransfer saturates before the
    tint runs - and over exactly these backgrounds the clamped substrate
    predicts Apple's tinted output to a median 1.45 code values where the
    unclamped transfer manages 2.27.  So they are usable, and keeping them does
    pull the worst case in.

    KEPT OUT ANYWAY, because it is a bad trade.  Scored over every captured
    tint and eleven backgrounds - 3300 readings - against the M1:

        clean substrates, pinned readings fitted    1.51 median  4.57 p90  22.99
        + clipping substrates in the neutral fit    1.71          5.44      20.93
        + clipping substrates everywhere            1.77          5.45      20.93

    Two code values off the tail for nearly a full code value of p90 across the
    whole grid.  The composite `clamped substrate -> output` is not affine, so
    fitting those backgrounds bends the law away from the ones that are.
    """
    table = {}
    for name in BACKGROUNDS:
        value = interior(locate(
            shots, f"{name}__{SHAPE}__{variant}__{appearance}.png"))
        if value is None:
            continue
        if not keep_clipping and (value.min() <= CLIP_LOW
                                  or value.max() >= CLIP_HIGH):
            continue
        table[name] = value
    return table


def chroma_magnitude(tint: np.ndarray) -> float:
    return float(np.linalg.norm(tint - float(LUMA @ tint)))


def censored_solve(design: np.ndarray, target: np.ndarray, side: np.ndarray
                   ) -> np.ndarray | None:
    """Least squares where `side` marks rows that are one-sided.

    side is 0 for an ordinary reading, -1 where the target is pinned at the
    low rail and +1 at the high one.  A pinned row says only that the true
    value is at or past its rail, so it belongs in the fit while the current
    solution sits on the wrong side of it and nowhere else - an active set,
    which settles in a handful of passes because each pass can only add or drop
    rows the previous one disagreed with.

    Recomputing the set from scratch each pass can ALTERNATE between two of
    them rather than settling, so the best iterate is kept and returned instead
    of the last one; that makes the result independent of where the cap falls.
    """
    active = side == 0
    best, best_cost = None, np.inf
    for _ in range(32):
        if active.sum() < design.shape[1]:
            break
        solution, *_ = np.linalg.lstsq(design[active], target[active],
                                       rcond=None)
        predicted = design @ solution
        cost = float((censored_residual(design, target, side,
                                        solution) ** 2).sum())
        if cost < best_cost:
            best, best_cost = solution, cost
        wanted = ((side == 0)
                  | ((side < 0) & (predicted > target))
                  | ((side > 0) & (predicted < target)))
        if np.array_equal(wanted, active):
            break
        active = wanted
    return best


def censored_residual(design: np.ndarray, target: np.ndarray, side: np.ndarray,
                      solution: np.ndarray) -> np.ndarray:
    """The error a pixel comparison would see: a pinned row costs nothing while
    the prediction is past its rail, because the display clamps it too."""
    predicted = design @ solution
    residual = predicted - target
    residual[side < 0] = np.maximum(predicted[side < 0] - target[side < 0], 0.0)
    residual[side > 0] = np.minimum(predicted[side > 0] - target[side > 0], 0.0)
    return residual


def solve_per_tint(samples, gamma: float | None = None) -> JsonObject | None:
    """base (3), beta (3) and gamma (1) for one tint.

    A clipped SUBSTRATE is already gone - substrate_table drops it.  A clipped
    reading stays, as an inequality: see censored_solve.
    """
    rows, targets, sides = [], [], []
    for substrate, measured in samples:
        luminance = float(LUMA @ substrate)
        chroma = substrate - luminance
        for channel in range(3):
            row = np.zeros(7)
            row[channel] = 1.0
            row[3 + channel] = luminance
            row[6] = chroma[channel]
            rows.append(row)
            if CLIP_LOW < measured[channel] < CLIP_HIGH:
                targets.append(measured[channel])
                sides.append(0)
            else:
                low = measured[channel] <= CLIP_LOW
                targets.append(0.0 if low else 255.0)
                sides.append(-1 if low else 1)
    if len(rows) < 10:
        return None
    design, target = np.array(rows), np.array(targets)
    side = np.array(sides)
    if gamma is not None:
        target = target - gamma * design[:, 6]
        design = design[:, :6]
    solution = censored_solve(design, target, side)
    if solution is None:
        return None
    residual = np.abs(censored_residual(design, target, side, solution))
    return {
        "base": [round(float(v), 4) for v in solution[0:3]],
        "beta": [round(float(v), 6) for v in solution[3:6]],
        "gamma": round(float(solution[6]) if gamma is None else gamma, 5),
        "maximumResidualCodes": round(float(residual.max()), 3),
    }


CHROMATIC_ORDERS = (1, 2, 3)
NEUTRAL_ORDERS = (1, 2, 3, 4)
# The exponent triples of a trivariate polynomial, ordered by total degree, so
# the first terms of a higher order are exactly a lower order's - which is what
# lets the generated header pad a chosen order out to the shader's fixed width
# with zeros and evaluate identically.
EXPONENTS = tuple(
    (i, j, k)
    for i, j, k in sorted(itertools.product(range(max(CHROMATIC_ORDERS) + 1),
                                            repeat=3),
                          key=lambda t: (sum(t), t))
    if sum((i, j, k)) <= max(CHROMATIC_ORDERS)
)
CHROMATIC_WIDTH = {order: sum(1 for e in EXPONENTS if sum(e) <= order)
                   for order in CHROMATIC_ORDERS}


def tint_terms(tint: np.ndarray, neutral: bool, order: int) -> list[float]:
    """The basis base and beta are expanded in, for one tint.

    A neutral tint is one-dimensional, so its functions take the tint's single
    level, and its order is cross-validated on the same ladder.  Both bases run
    from the CONSTANT term upwards, so a lower order's coefficients are the
    leading coefficients of a higher one and the generated header can pad.

    A chromatic tint gets a full polynomial in the three components, whose
    order is CROSS-VALIDATED per variant and appearance rather than fixed.  An
    affine basis is not enough anywhere - held out one tint at a time it scores
    5.27 rms / 38.2 max code values against quadratic's 3.07 / 23.7 for regular
    in light - because a strongly chromatic tint drives the base NEGATIVE in the
    channel opposite its hue, and a plane through the rest of the colour cube
    cannot reach that.  Whether CUBIC is warranted depends on the material:
    from 53 chromatic tints `regular` improves again, to 2.16 / 16.7, while
    `clear` does not, so the choice is measured, not assumed.
    """
    if neutral:
        level = float(LUMA @ tint) / 255.0
        return [level**power for power in range(order + 1)]
    red, green, blue = tint / 255.0
    return [red**i * green**j * blue**k
            for i, j, k in EXPONENTS if i + j + k <= order]


def fit_regime(items, neutral: bool, order: int, gamma_free: bool
               ) -> tuple[np.ndarray, np.ndarray] | None:
    """base, beta and optionally gamma as functions of the tint, fitted to the
    DATA.

    Fitting to the per-tint coefficients instead is what produced a 17 code
    law: those coefficients are individually underdetermined, so their scatter
    is noise rather than signal.  Here every captured pixel is a row, and the
    unknowns enter linearly, so this is still ordinary least squares.

    gamma - how much of the substrate's own chroma survives - reads as one for
    every neutral tint and zero for every chromatic one, so it used to be
    PINNED there.  Pinning it costs real accuracy on a saturated backdrop: the
    per-tint fits scatter between 0.96 and 1.16, and against a substrate whose
    chroma runs to 54 code values that spread is nine code values of error.
    With `gamma_free` it becomes a third fitted function of the tint on the
    same basis, and which way to go is decided by held-out error like
    everything else here.
    """
    built = [regime_design(items, channel, neutral, order, gamma_free)
             for channel in range(3)]
    return solve_regime(built, keep=None)


def regime_design(items, channel: int, neutral: bool, order: int,
                  gamma_free: bool):
    """One channel's rows, plus which tint each row came from.

    Held out one tint at a time this is rebuilt fifty-three times over from
    Python loops, which dominated the run; returning the owner index lets the
    caller drop one tint's rows from a design it already has.
    """
    rows, targets, sides, owner = [], [], [], []
    for index, (tint, samples) in enumerate(items):
        terms = np.array(tint_terms(tint, neutral, order))
        for substrate, measured in samples:
            luminance = float(LUMA @ substrate)
            chroma = substrate[channel] - luminance
            row = [*terms, *(terms * luminance)]
            if gamma_free:
                row += list(terms * chroma)
            rows.append(row)
            pinned = not (CLIP_LOW < measured[channel] < CLIP_HIGH)
            low = pinned and measured[channel] <= CLIP_LOW
            value = (0.0 if low else 255.0) if pinned else measured[channel]
            targets.append(value
                           - (0.0 if gamma_free or not neutral else chroma))
            sides.append(0 if not pinned else (-1 if low else 1))
            owner.append(index)
    if not rows:
        return None
    return (np.array(rows), np.array(targets), np.array(sides),
            np.array(owner))


def solve_regime(built, keep) -> tuple[np.ndarray, np.ndarray] | None:
    """Fit the three channels of one regime, optionally over a subset of tints.

    `keep` is None for every tint, or the index of the one to LEAVE OUT.
    """
    solutions, residuals = [], []
    for channel in range(3):
        if built[channel] is None:
            return None
        design, target, side, owner = built[channel]
        if keep is not None:
            mask = owner != keep
            design, target, side = design[mask], target[mask], side[mask]
        if len(design) < 2 * design.shape[1]:
            return None
        if (side == 0).sum() < 2 * design.shape[1]:
            return None
        solution = censored_solve(design, target, side)
        if solution is None:
            return None
        solutions.append(solution)
        residuals.append(censored_residual(design, target, side, solution))
    return np.array(solutions), np.concatenate(residuals)


def evaluate(solutions: np.ndarray, tint: np.ndarray, substrate: np.ndarray,
             neutral: bool, order: int, gamma_free: bool) -> np.ndarray:
    terms = np.array(tint_terms(tint, neutral, order))
    luminance = float(LUMA @ substrate)
    chroma = substrate - luminance
    width = len(terms)
    base = solutions[:, :width] @ terms
    beta = solutions[:, width:2 * width] @ terms
    gamma = (solutions[:, 2 * width:] @ terms if gamma_free
             else (1.0 if neutral else 0.0))
    return np.clip(base + beta * luminance + gamma * chroma, 0.0, 255.0)


def hold_out_one_tint(items, neutral: bool, order: int, gamma_free: bool
                      ) -> np.ndarray:
    """Predict each tint from all the others - the only honest score here.

    In-sample error falls with every term added, so it cannot choose the order.
    What the shader is asked to do is predict a tint nobody captured, and this
    measures exactly that.

    Scored against every reading, pinned ones included, after the same clamp a
    display applies: predicting -20 where the hardware reads 0 is free, and
    predicting +20 there is a twenty code value error.  Skipping the pinned
    readings instead scored a law blind to the one thing it got worst.
    """
    built = [regime_design(items, channel, neutral, order, gamma_free)
             for channel in range(3)]
    errors: list[float] = []
    for index in range(len(items)):
        trained = solve_regime(built, keep=index)
        if trained is None:
            continue
        tint, samples = items[index]
        for substrate, measured in samples:
            predicted = evaluate(trained[0], tint, substrate, neutral, order,
                                 gamma_free)
            errors.extend(predicted[channel] - measured[channel]
                          for channel in range(3))
    return np.array(errors) if errors else np.zeros(1)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--shots", type=Path, nargs="+", required=True,
                        help="capture directories, searched in order")
    parser.add_argument("--output", type=Path)
    # MEASURED as exact equality, so any value below the smallest chroma an
    # 8-bit tint can carry (0.845, for a one-code difference) is equivalent.
    parser.add_argument("--neutral-chroma", type=float, default=0.5,
                        help="chroma magnitude below which a tint is neutral")
    parser.add_argument("--region", choices=("interior", "rim"),
                        default="interior",
                        help="fit the tint over the element's body or its rim")
    arguments = parser.parse_args()
    global REGION
    REGION = arguments.region

    colours = {**MID, **LUMINANCE, **SATURATION, **FINE, **EXTRA, **WIDE}
    ladders = {**LUMINANCE, **SATURATION, **FINE, **EXTRA, **WIDE}
    overlays = {
        "regular": {**{k: f"sweepTint{k}" for k in MID},
                    **{k: f"tint{k}" for k in ladders}},
        "clear": {**{k: f"clearTint{k}" for k in MID},
                  **{k: f"clearTint{k}" for k in ladders}},
    }

    report: JsonObject = {
        "schemaVersion": 2,
        "classification": "Apple Liquid Glass tint law, both variants",
        "region": arguments.region,
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
                    if (reading := interior(locate(
                        arguments.shots,
                        f"{name}__{SHAPE}__{overlay}__{appearance}.png"))) is not None
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
            held_out = []
            for key, items in regimes.items():
                if len(items) < 3:
                    continue
                neutral = key == "neutral"
                # A neutral tint is one-dimensional and has seven samples, so
                # there is no order to choose; a chromatic one is chosen by
                # held-out error over the whole ladder.
                candidates = []
                for order in (NEUTRAL_ORDERS if neutral else CHROMATIC_ORDERS):
                    for gamma_free in (False, True):
                        if fit_regime(items, neutral, order, gamma_free) is None:
                            continue
                        held = (hold_out_one_tint(items, neutral, order,
                                                  gamma_free)
                                if len(items) > 6 else np.zeros(1))
                        candidates.append({
                            "order": order,
                            "gammaFitted": gamma_free,
                            "termCount": len(tint_terms(items[0][0], neutral,
                                                        order)),
                            "heldOutRootMeanSquareCodes": round(
                                float(np.sqrt((held**2).mean())), 3),
                            "heldOutMaximumCodes": round(
                                float(np.abs(held).max()), 3),
                            "errors": held,
                        })
                if not candidates:
                    continue
                chosen = min(candidates,
                             key=lambda c: c["heldOutRootMeanSquareCodes"])
                order, gamma_free = chosen["order"], chosen["gammaFitted"]
                held_out.extend(chosen["errors"])
                for candidate in candidates:
                    candidate.pop("errors", None)
                solutions, residual = fit_regime(items, neutral, order,
                                                 gamma_free)
                fitted[key] = {
                    "tintCount": len(items),
                    "order": order,
                    "gammaFitted": gamma_free,
                    "termCount": chosen["termCount"],
                    "coefficients": [[round(float(v), 8) for v in row]
                                     for row in solutions],
                    "maximumResidualCodes": round(float(np.abs(residual).max()), 3),
                    "rootMeanSquareResidualCodes": round(
                        float(np.sqrt((residual**2).mean())), 3),
                    "candidates": candidates,
                }
                errors.append(residual)
            if not errors:
                continue
            total = np.concatenate(errors)
            held = np.array(held_out) if held_out else np.zeros(1)
            entry[appearance] = {
                "heldOutRootMeanSquareCodes": round(
                    float(np.sqrt((held**2).mean())), 3),
                "heldOutMaximumCodes": round(float(np.abs(held).max()), 3),
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
                  f"max={entry[appearance]['maximumResidualCodes']:7.3f}"
                  f"   held-out rms="
                  f"{entry[appearance]['heldOutRootMeanSquareCodes']:6.3f} "
                  f"max={entry[appearance]['heldOutMaximumCodes']:7.3f}"
                  + "".join(f"   {k}:{v['tintCount']} order {v['order']}"
                            + (" gamma" if v["gammaFitted"] else "")
                            for k, v in fitted.items()))
        report["variants"][variant] = entry

    if arguments.output is not None:
        arguments.output.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
