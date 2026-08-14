#!/usr/bin/env python3
"""How far is each sensitive pixel from flipping, measured in DISTANCE ulps?

alpha = clip((1 - d)/feather + 1/2), so a change of one binary32 ulp in the
signed distance d moves alpha by ulp32(d)/feather - the feather is O(1e-3) at
the reveal boundary, so the divide amplifies a last-bit difference in d into
~1e-4 of relative alpha, which is the same size as a binary16 alpha ulp.

If the "secondary" residual class is really a PRIMARY story (apple's d differs
from walle's in the last binary32 bits) then the pixels that flipped are the
ones needing the fewest distance ulps.  If it is a genuine transfer-plane
story, the flipped set will be indifferent to this quantity.
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


def ulp32(value: float) -> Fraction:
    bits = int(np.asarray([value], dtype=np.float32).view(np.uint32)[0])
    exponent = (bits >> 23) & 0xFF
    if exponent == 0:
        return Fraction(1, 2 ** 149)
    return Fraction(2 ** exponent, 2 ** 173)


def flip_distance_ulps(alpha: float, bits: int, feather: float, distance: float) -> Fraction:
    """Distance increase (in binary32 ulps of d) needed to drop alpha one ulp."""
    value = Fraction(float(alpha))
    centre = Fraction(float(np.asarray([bits], dtype=np.uint16).view(np.float16)[0]))
    exponent = bits >> 10
    ulp16 = (
        Fraction(2 ** exponent, 2 ** 25) if exponent else Fraction(1, 2 ** 24)
    )
    headroom = value - (centre - ulp16 / 2)
    return headroom * Fraction(float(feather)) / ulp32(distance)


def main() -> int:
    base, bitmap = primary.load_tables()
    states = [int(value) for value in sys.argv[1:]] or [40, 41, 42, 58, 60]
    for state in states:
        half, _, alpha, extras, _ = primary.render_state_half(
            state, base=base, bitmap=bitmap
        )
        built = constraints.build(state, base=base, bitmap=bitmap)
        groups: dict[str, list[float]] = {}
        for label, name in ((constraints.LABEL_LOW, "LOW"),
                            (constraints.LABEL_HIGH, "high")):
            ys, xs = np.nonzero(built.labels == label)
            groups[name] = sorted(
                float(
                    flip_distance_ulps(
                        alpha[y, x],
                        int(half[y, x]),
                        extras["feather"][y, x],
                        extras["distance"][y, x],
                    )
                )
                for y, x in zip(ys, xs)
            )
        low = groups["LOW"]
        high = groups["high"]
        print(
            f"state {state}: LOW n={len(low)} range=[{low[0]:.3f}, {low[-1]:.3f}] "
            f"| high n={len(high)} range=[{high[0]:.3f}, {high[-1]:.3f}]"
        )
        print("   LOW  ulps: " + " ".join(f"{value:.3f}" for value in low))
        print(
            "   high ulps (12 smallest): "
            + " ".join(f"{value:.3f}" for value in high[:12])
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
