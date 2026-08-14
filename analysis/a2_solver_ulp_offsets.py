#!/usr/bin/env python3
"""What binary16 alpha would apple's byte need, relative to walle's?

Prints, per residual, the signed binary16 ulp offsets d for which
round255(p + d*ulp) equals apple's corpus byte.  A secondary multiply by
(1 - 2^-11) is exactly d = -1 for p in (0.5, 1]; the point of this dump is to
see whether the whole 91-pixel residual set is a one-ulp alpha story.
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "analysis"))

import a2_solver_primary as primary  # noqa: E402


def byte_of(bits: int) -> int:
    value = np.asarray([bits], dtype=np.uint16).view(np.float16)[0]
    return int(np.rint(np.float32(value) * np.float32(255)))


def offsets_for(bits: int, target: int) -> list[int]:
    return [
        delta
        for delta in range(-4, 5)
        if 0 <= bits + delta < 0x7C00 and byte_of(bits + delta) == target
    ]


def main() -> int:
    rows: dict[int, list[tuple[int, int, int, int]]] = {}
    for line in (ROOT / "build/_residual_list.txt").read_text().splitlines():
        state, x, y, walle, apple = (int(value) for value in line.split())
        rows.setdefault(state, []).append((x, y, walle, apple))

    base, bitmap = primary.load_tables()
    census: Counter[str] = Counter()
    for state in sorted(rows):
        half, _, _, _, _ = primary.render_state_half(state, base=base, bitmap=bitmap)
        for x, y, walle, apple in rows[state]:
            bits = int(half[y, x])
            deltas = offsets_for(bits, apple)
            census[",".join(str(value) for value in deltas)] += 1
            print(
                f"state {state:2d} ({x:4d},{y:4d}) p=0x{bits:04x} walle={walle:3d} "
                f"apple={apple:3d} needs ulp offsets {deltas}"
            )
    print("census:", dict(census))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
