#!/usr/bin/env python3
"""Fast exact scoring harness.

Every capture is pre-reduced to (dm, d_o, P, xlo, xhi): a candidate that
feeds value V into the proven narrow-law export reproduces the captured
word exactly iff V - P lies in [xlo, xhi].  Scoring is then one integer
comparison per cell, so whole model families can be swept.
"""

from __future__ import annotations

import pickle
import sys
from pathlib import Path

sys.path[:0] = ["/tmp/walle/analysis"]

CACHE = Path("/tmp/walle/build/wide_solver_cells.pkl")
NAMES = ("tt4", "tt3", "tt1")
TOTALS = {"tt4": 18001, "tt3": 18001, "tt1": 2610}


def cells() -> dict:
    if CACHE.exists():
        return pickle.loads(CACHE.read_bytes())
    from wide_solver_xmap import observations
    out = {}
    for name in NAMES:
        out[name] = [(r["dm"], r["d_o"], r["P"], r["xlo"], r["xhi"])
                     for r in observations(name)]
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_bytes(pickle.dumps(out))
    return out


DATA = cells()


def score(law, name: str) -> int:
    """Count cells where law(dm, d_o) -> V reproduces the captured word."""
    hits = 0
    for dm, d_o, p, lo, hi in DATA[name]:
        d = law(dm, d_o) - p
        if lo <= d <= hi:
            hits += 1
    return hits


def score_all(law) -> tuple[int, int, int]:
    return tuple(score(law, n) for n in NAMES)


def report(tag: str, law) -> tuple[int, int, int]:
    s = score_all(law)
    print(f"{tag:56s} tt4 {s[0]:5d}/18001  tt3 {s[1]:5d}/18001  "
          f"tt1 {s[2]:4d}/2610")
    return s


if __name__ == "__main__":
    report("narrow law (baseline)", lambda dm, d_o: dm * d_o)
