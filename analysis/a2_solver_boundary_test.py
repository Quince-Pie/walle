#!/usr/bin/env python3
"""Are the 0x3BFF pixels a PRIMARY rounding-boundary effect?

If apple's binary16 alpha is one ulp below walle's because apple's binary32
alpha is infinitesimally smaller, walle's binary32 alpha must sit just above
the binary16 rounding midpoint.  This prints, for every sensitive pixel, the
position r of walle's binary32 alpha inside its binary16 rounding interval:
r = (a - (p - ulp/2)) / ulp, so r near 0 means "one nudge down flips it".
"""

from __future__ import annotations

import sys
from fractions import Fraction
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "analysis"))

import a2_solver_constraints as constraints  # noqa: E402
import a2_solver_primary as primary  # noqa: E402


def ulp_of(bits: int) -> Fraction:
    exponent = bits >> 10
    if exponent == 0:
        return Fraction(1, 2 ** 24)
    return Fraction(2 ** exponent, 2 ** 25)


def position(alpha: float, bits: int) -> Fraction:
    value = Fraction(float(alpha))
    centre = Fraction(float(np.asarray([bits], dtype=np.uint16).view(np.float16)[0]))
    ulp = ulp_of(bits)
    return (value - (centre - ulp / 2)) / ulp


def main() -> int:
    base, bitmap = primary.load_tables()
    states = [int(value) for value in sys.argv[1:]] or [40, 41, 42, 58, 60]
    for state in states:
        half, _, exact, _, _ = primary.render_state_half(state, base=base, bitmap=bitmap)
        built = constraints.build(state, base=base, bitmap=bitmap)
        for label, name in ((constraints.LABEL_LOW, "LOW "),
                            (constraints.LABEL_HIGH, "high")):
            ys, xs = np.nonzero(built.labels == label)
            values = [
                float(position(exact[y, x], int(half[y, x])))
                for y, x in zip(ys, xs)
            ]
            if not values:
                continue
            array = np.asarray(values)
            print(
                f"state {state} {name} n={len(array)} "
                f"r: min={array.min():.6f} median={np.median(array):.6f} "
                f"max={array.max():.6f} frac(r<0.01)={np.mean(array < 0.01):.4f}"
            )
            if label == constraints.LABEL_LOW:
                print(
                    "     r values: "
                    + " ".join(f"{value:.6f}" for value in sorted(values))
                )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
