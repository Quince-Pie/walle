#!/usr/bin/env python3
"""Measure the hardware's effective round-up threshold per row.

For each cell write P = M*g + drop (g = 2^cut).  The capture either keeps M
or bumps it.  If the wide path were "narrow law on a biased product" then
`hw_up` would be a clean threshold function of (drop, M parity); this script
reports the threshold and every cell that violates it.
"""

from __future__ import annotations

import sys
from collections import defaultdict

sys.path[:0] = ["/tmp/walle", "/tmp/walle/analysis"]

import wide_solver_dev as V  # noqa: E402


def narrow_threshold(cut: int, parity: int) -> int:
    """drop at which RNE24(rna27(.)) starts rounding up, given M parity."""
    if cut <= 3:
        half = 1 << (cut - 1) if cut else 0
        return half + (1 if parity == 0 else 0) if cut else 0
    r27 = cut - 3                      # bits dropped by rna27
    # q = floor((drop + 2^(r27-1)) / 2^r27); up iff q > 4 or (q == 4 and odd)
    need = 4 if parity else 5
    return (need << r27) - (1 << (r27 - 1))


def main() -> None:
    name = sys.argv[1] if len(sys.argv) > 1 else "tt4"
    rows = defaultdict(list)
    for x in V.deviations(name):
        M = x["P"] >> x["cut"]
        up = x["D"] + (1 if x["drop"] >= narrow_threshold(x["cut"], M & 1)
                       else 0)
        rows[x["d_o"]].append((x["drop"], M & 1, up, x))
    for d_o in sorted(rows):
        out = []
        for parity in (1, 0):
            pts = [(d, u, x) for d, p, u, x in rows[d_o] if p == parity]
            ups = [d for d, u, x in pts if u]
            downs = [d for d, u, x in pts if not u]
            if not ups:
                out.append(f"p{parity}: never up")
                continue
            thr = min(ups)
            viol = sum(1 for d in downs if d >= thr)
            cut = pts[0][2]["cut"]
            out.append(f"p{parity}: thr={thr:5d}/{1<<cut} "
                       f"(narrow {narrow_threshold(cut, parity)}) viol={viol}")
        print(f" d_o={d_o:6d}  " + " | ".join(out))


if __name__ == "__main__":
    main()
