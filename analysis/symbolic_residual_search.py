#!/usr/bin/env python3
"""Search for the FORM of the residual, then snap its constants.

The campaign's blocker is not constant precision - most constants are known
to three or four figures - it is that the remaining mechanism's SHAPE is
unknown, and twenty-two hand-proposed shapes have been falsified.  So search
the shape instead of proposing it, and read the constants off the winner:
a search that lands on 1.99999 is telling you the answer is 2, and that snap
is testable, because forcing the constant to exactly 2 either costs nothing
or it does.

NEAT is the wrong engine for that.  It evolves network topology and weights,
and its output is a net of sigmoids with no 2 inside it to read.  This is
symbolic regression: a library of candidate basis functions, sparse subset
selection over it, and expressions that come out legible.  Being linear in
the coefficients it is also deterministic and fast, which matters because
the honest fitness here needs every capture evaluated on every candidate.

The failure mode this is built against is the one that killed most of the
twenty-two: a form that fits one wallpaper and does not transport.  So

  * fitness is the WORST context, never the mean - a law that works on three
    captures and fails on the fourth is not a law;
  * the saturated pair is held out entirely.  They are the same two
    wallpapers reversed, so a law that survives them survives a sign flip
    that content-specific fits cannot;
  * terms are added one at a time and each must earn its place on the
    HOLDOUT, not on the fit set;
  * constants are then snapped to Apple's own decoded values - 0.5, 0.35,
    0.125, 0.2, 0.3, 0.97, 1/21 - and the snap is kept only if it does not
    degrade the fit.  Snapping to a number Apple actually ships is evidence;
    snapping to a round number is numerology, and the two are reported
    separately.

Two self-tests run first, because a search that cannot recover a known
answer cannot be trusted on an unknown one.  The deep luma residual is known
to contain a term linear in (narrow - wide) with a coefficient near 0.0069
light and 0.0131 dark - the mixture weight error already shipped - and it is
known NOT to contain a pure level term, because the surface proved the
transfer exact along the diagonal.  The search must find the first and
reject the second.

Usage: symbolic_residual_search.py --data <npz> [--terms 4] [--out json]
"""
import argparse
import itertools
import json
from pathlib import Path

import numpy as np

# Apple's own decoded constants, the only defensible snap targets
APPLE = {
    "0.3 (refraction.outerOpacity)": 0.3,
    "0.35 (edgeBleed.height/size)": 0.35,
    "0.125 (refraction.outerHeight/size)": 0.125,
    "0.2 (refraction.outerAmount/size)": 0.2,
    "0.5 (blur.opacities / highlights.amount)": 0.5,
    "0.97 (whitePointShift)": 0.97,
    "1/21 (blur.radius slope)": 1.0 / 21.0,
    "0.7 (highlights.curvature)": 0.7,
    "0.06": 0.06,
    "0.9 (edgeBleed.ycc.black)": 0.9,
}
ROUND = [1 / 2, 1 / 3, 1 / 4, 1 / 8, 1 / 16, 1 / 32, 1 / 64, 1 / 128, 1 / 256,
         2 / 3, 3 / 4, 1.0, 2.0]


def library(row):
    """Candidate basis functions.  Variables are normalised so coefficients
    come out in output codes per unit, which is what makes them readable."""
    yn, yw, cn, cw, depth, radius = (row[:, 0] / 255.0, row[:, 1] / 255.0,
                                     row[:, 2] / 128.0, row[:, 3] / 128.0,
                                     row[:, 4] / 1000.0, row[:, 5] / 2000.0)
    d = yn - yw                      # the mixture's lever arm
    y = 0.5 * (yn + yw)              # level
    c = 0.5 * (cn + cw)
    dc = cn - cw
    terms = {
        "D": d,
        "D^2": d * d,
        "D^3": d * d * d,
        "D*Y": d * y,
        "D*Y^2": d * y * y,
        "D^2*Y": d * d * y,
        "D*C": d * c,
        "D*dC": d * dc,
        "dC": dc,
        "dC*Y": dc * y,
        "dC*C": dc * c,
        "Y": y,
        "Y^2": y * y,
        "C": c,
        "C^2": c * c,
        "D*depth": d * depth,
        "D/R": d / np.maximum(radius, 1e-3),
        "1": np.ones_like(d),
    }
    return terms


def evaluate(terms, names, coef, target):
    pred = sum(coef[i] * terms[n] for i, n in enumerate(names))
    return float(np.sqrt(((target - pred) ** 2).mean()))


def normal_equations(contexts, names):
    ata = np.zeros((len(names), len(names)))
    atb = np.zeros(len(names))
    for terms, target in contexts:
        design = np.stack([terms[n] for n in names], axis=1)
        weight = 1.0 / len(target)
        ata += weight * (design.T @ design)
        atb += weight * (design.T @ target)
    return ata, atb


def fit(contexts, names):
    """Least squares pooled over the FIT contexts (each equally weighted, so a
    big capture cannot outvote a small one)."""
    ata, atb = normal_equations(contexts, names)
    return np.linalg.solve(ata + 1e-9 * np.eye(len(names)), atb)


def conditioning(contexts, names):
    """How collinear the chosen basis is.  This decides whether the fitted
    constants MEAN anything: with D, D^2, Y and Y^2 all strongly correlated on
    real wallpapers, a design can fit well while its individual coefficients
    are arbitrary, and an arbitrary coefficient cannot be snapped to 2.  A
    condition number in the thousands means read the fit, not the numbers."""
    ata, _ = normal_equations(contexts, names)
    scale = np.sqrt(np.diag(ata))
    scale[scale <= 0] = 1.0
    correlation = ata / np.outer(scale, scale)
    return float(np.linalg.cond(correlation))


def worst(contexts, names, coef):
    return max(evaluate(terms, names, coef, target) for terms, target in contexts)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, required=True)
    ap.add_argument("--terms", type=int, default=4)
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()

    data = np.load(args.data)
    results = {}
    for appearance in ("light", "dark"):
        fit_ctx, hold_ctx = [], []
        for key in data.files:
            capture, kind = key.rsplit("_", 1)
            if kind != appearance:
                continue
            row = data[key].astype(np.float64)
            entry = (library(row), row[:, 6])
            (hold_ctx if capture in ("satred", "satblue") else fit_ctx).append(entry)
        if not fit_ctx or not hold_ctx:
            continue
        pool = sorted(library(data[data.files[0]].astype(np.float64)))
        print(f"\n=== regular/{appearance} ===")
        print(f"   baseline  fit-worst {max(float(np.sqrt((t ** 2).mean())) for _, t in fit_ctx):.4f}"
              f"   holdout-worst {max(float(np.sqrt((t ** 2).mean())) for _, t in hold_ctx):.4f}")
        chosen, history = [], []
        for step in range(args.terms):
            best = None
            for candidate in pool:
                if candidate in chosen:
                    continue
                names = chosen + [candidate]
                try:
                    coef = fit(fit_ctx, names)
                except np.linalg.LinAlgError:
                    continue
                score = worst(fit_ctx, names, coef)
                if best is None or score < best[0]:
                    best = (score, candidate, coef)
            if best is None:
                break
            score, candidate, coef = best
            names = chosen + [candidate]
            held = worst(hold_ctx, names, coef)
            prior_held = (history[-1]["holdout"] if history else
                          max(float(np.sqrt((t ** 2).mean())) for _, t in hold_ctx))
            verdict = "keeps" if held < prior_held - 1e-4 else "REJECTED on holdout"
            print(f"   + {candidate:10s} coef {coef[-1]:+9.5f}   "
                  f"fit-worst {score:.4f}   holdout-worst {held:.4f}   {verdict}")
            history.append({"term": candidate, "coef": list(map(float, coef)),
                            "names": list(names), "fit": score, "holdout": held,
                            "kept": verdict == "keeps"})
            chosen = names
        results[appearance] = history

        if history:
            final = history[-1]
            print("   snapping the leading coefficient:")
            lead = final["coef"][0]
            for label, value in sorted(APPLE.items(), key=lambda kv: abs(kv[1] - abs(lead))):
                print(f"      |{lead:+.5f}| vs {label}: ratio {abs(lead) / value:.4f}")
                break
            for value in sorted(ROUND, key=lambda v: abs(v - abs(lead))):
                print(f"      |{lead:+.5f}| vs round {value:g}: ratio {abs(lead) / value:.4f}")
                break

    if args.out:
        args.out.write_text(json.dumps(results, indent=1))


if __name__ == "__main__":
    main()
