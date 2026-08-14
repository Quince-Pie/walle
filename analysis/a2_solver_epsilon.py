#!/usr/bin/env python3
"""Separate the corpus by the relative alpha deficit each pixel needs.

Model B: apple multiplies the binary32 coverage alpha by s = 1 - eps BEFORE
the binary16 conversion, so the corpus byte drops by one binary16 ulp exactly
when eps exceeds the pixel's own rounding headroom

    eps_needed = (a - (p - ulp/2)) / a

with a = walle's binary32 alpha, p = RNE16(a).  eps_needed is known exactly
per pixel, so the LOW/HIGH labels bracket apple's eps.
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


def epsilon_needed(alpha: float, bits: int) -> Fraction:
    value = Fraction(float(alpha))
    if value == 0:
        return Fraction(0)
    centre = Fraction(float(np.asarray([bits], dtype=np.uint16).view(np.float16)[0]))
    exponent = bits >> 10
    ulp = (
        Fraction(2 ** exponent, 2 ** 25) if exponent else Fraction(1, 2 ** 24)
    )
    return (value - (centre - ulp / 2)) / value


def collect(state: int, *, base: tuple[int, ...], bitmap: bytes):
    half, _, exact, _ = primary.render_state_half(state, base=base, bitmap=bitmap)
    built = constraints.build(state, base=base, bitmap=bitmap)
    records = []
    for label in (constraints.LABEL_LOW, constraints.LABEL_HIGH):
        ys, xs = np.nonzero(built.labels == label)
        for y, x in zip(ys, xs):
            records.append(
                (
                    int(x),
                    int(y),
                    int(built.triangles[y, x]),
                    label == constraints.LABEL_LOW,
                    epsilon_needed(exact[y, x], int(half[y, x])),
                )
            )
    return records


def main() -> int:
    base, bitmap = primary.load_tables()
    states = [int(value) for value in sys.argv[1:]] or [40, 41, 42, 58, 60]
    everything = []
    for state in states:
        records = collect(state, base=base, bitmap=bitmap)
        everything.extend((state, *record) for record in records)
        low = sorted(r[4] for r in records if r[3])
        high = sorted(r[4] for r in records if not r[3])
        print(
            f"state {state}: low n={len(low)} max={float(max(low)):.6e} "
            f"| high n={len(high)} min={float(min(high)):.6e} "
            f"separable={max(low) < min(high)}"
        )
        print(
            "   low  eps: " + " ".join(f"{float(value):.5e}" for value in low)
        )
        print(
            "   high eps (10 smallest): "
            + " ".join(f"{float(value):.5e}" for value in high[:10])
        )
    low_all = [record[5] for record in everything if record[4]]
    high_all = [record[5] for record in everything if not record[4]]
    print(
        f"ALL: max(low)={float(max(low_all)):.6e} min(high)={float(min(high_all)):.6e} "
        f"globally separable={max(low_all) < min(high_all)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
