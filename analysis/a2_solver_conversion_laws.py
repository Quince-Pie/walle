#!/usr/bin/env python3
"""Score candidate binary16 -> unorm8 conversion laws against the corpus.

walle uses byte = rint(255 * p) with numpy's ties-to-even.  If apple's
conversion differs (a narrower product, a different tie rule, a truncation),
the disagreement would look exactly like a scattered one-ulp "secondary".
Each law is scored over the whole frame, so a law that explains the 37
secondary-class pixels must leave the other 54 residuals and nothing else.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "analysis"))

import a2_solver_primary as primary  # noqa: E402


def laws(half: np.ndarray) -> dict[str, np.ndarray]:
    value16 = half.view(np.float16)
    value32 = value16.astype(np.float32)
    scaled = value32 * np.float32(255)
    scaled16 = (value16 * np.float16(255)).astype(np.float32)
    return {
        "rint(255p) [walle]": np.rint(scaled),
        "floor(255p+0.5)": np.floor(scaled + np.float32(0.5)),
        "floor(255p)": np.floor(scaled),
        "rint(f16(255p))": np.rint(scaled16),
        "floor(f16(255p)+0.5)": np.floor(scaled16 + np.float32(0.5)),
        "floor(f16(255p))": np.floor(scaled16),
        "rint(p*256-p)": np.rint(value32 * np.float32(256) - value32),
        "rint(255*f16(p*0x3bff))": np.rint(
            (value16 * np.asarray([0x3BFF], dtype=np.uint16).view(np.float16)[0])
            .astype(np.float16)
            .astype(np.float32)
            * np.float32(255)
        ),
    }


def main() -> int:
    base, bitmap = primary.load_tables()
    states = [int(value) for value in sys.argv[1:]] or [40, 41, 42, 58, 60]
    totals: dict[str, int] = {}
    for state in states:
        half, covered, _, _, _ = primary.render_state_half(
            state, base=base, bitmap=bitmap
        )
        observed = primary.observed_frame(state)
        report = []
        for name, candidate in laws(half).items():
            bytes_ = np.where(covered, candidate.astype(np.uint8), np.uint8(0))
            count = int(np.count_nonzero(bytes_ != observed))
            totals[name] = totals.get(name, 0) + count
            report.append(f"{name}={count}")
        print(f"state {state}: " + "  ".join(report))
    print("totals:", totals)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
