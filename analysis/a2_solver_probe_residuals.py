#!/usr/bin/env python3
"""Print primary/secondary candidate bytes at every corpus residual pixel."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "analysis"))

import a2_solver_primary as primary  # noqa: E402


def main() -> int:
    rows: dict[int, list[tuple[int, int, int, int]]] = {}
    for line in (ROOT / "build/_residual_list.txt").read_text().splitlines():
        state, x, y, walle, apple = (int(value) for value in line.split())
        rows.setdefault(state, []).append((x, y, walle, apple))

    base, bitmap = primary.load_tables()
    states = [int(value) for value in sys.argv[1:]] or sorted(rows)
    for state in states:
        half, covered, _, _ = primary.render_state_half(state, base=base, bitmap=bitmap)
        byte_one = primary.packed_bytes(half, 0x3C00)
        byte_low = primary.packed_bytes(half, 0x3BFF)
        observed = primary.observed_frame(state)
        for x, y, walle, apple in rows[state]:
            print(
                f"state {state:2d} ({x:4d},{y:4d}) half=0x{int(half[y, x]):04x} "
                f"cov={int(covered[y, x])} b(1.0)={int(byte_one[y, x]):3d} "
                f"b(0x3bff)={int(byte_low[y, x]):3d} walle={walle:3d} "
                f"apple={apple:3d} observed={int(observed[y, x]):3d}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
