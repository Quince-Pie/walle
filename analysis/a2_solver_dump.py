#!/usr/bin/env python3
"""Dump the sensitive-pixel label sequence per state and transfer triangle."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "analysis"))

import a2_solver_constraints as constraints  # noqa: E402
import a2_solver_primary as primary  # noqa: E402


def main() -> int:
    base, bitmap = primary.load_tables()
    states = [int(value) for value in sys.argv[1:]] or [42]
    for state in states:
        built = constraints.build(state, base=base, bitmap=bitmap)
        mesh = constraints.load_mesh(state)
        for triangle in sorted(set(built.triangles[built.labels != 0].tolist())):
            selected = (built.labels != 0) & (built.triangles == triangle)
            ys, xs = np.nonzero(selected)
            order = np.argsort(ys * 4096 + xs)
            ys, xs = ys[order], xs[order]
            marks = "".join(
                "v" if built.labels[y, x] == constraints.LABEL_LOW else "."
                for y, x in zip(ys, xs)
            )
            corners = mesh.triangle(triangle)
            print(
                f"state {state} tri {triangle} verts {corners} "
                f"n={len(ys)} low={marks.count('v')}"
            )
            print(f"  {marks}")
            if marks.count("v"):
                for y, x in zip(ys, xs):
                    if built.labels[y, x] == constraints.LABEL_LOW:
                        print(f"    low ({x},{y})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
