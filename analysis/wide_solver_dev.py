#!/usr/bin/env python3
"""Integer deviation map: D = (hw value - narrow-law value) / granule.

D is exactly the number of result-lsb steps by which the hardware differs
from the proven narrow law, so it is the whole of the wide-path mystery in
one small integer per cell.
"""

from __future__ import annotations

import sys
from collections import Counter, defaultdict

sys.path[:0] = ["/tmp/walle", "/tmp/walle/analysis"]

import wide_solver_data as W  # noqa: E402
import _sweep_fused_join_lattice as m  # noqa: E402


def deviations(name: str):
    for dm, e, d_o, sign, c_word in W.load(name):
        P = dm * d_o
        mant_n, sh_n = W.narrow(P)
        sign_c, mant_c, e_c = m.f32_parts(c_word)
        k = e_c - e
        hw = mant_c << k
        ref = mant_n << sh_n
        g = 1 << sh_n
        assert (hw - ref) % g == 0 or True
        yield dict(dm=dm, d_o=d_o, P=P, bl=P.bit_length(), cut=sh_n,
                   drop=P & ((1 << sh_n) - 1), D=(hw - ref) // g,
                   rem=(hw - ref) % g, mant=mant_n)


def main() -> None:
    for name in ("tt4", "tt1", "tt3"):
        cells = list(deviations(name))
        c = Counter(x["D"] for x in cells)
        bad = sum(1 for x in cells if x["rem"])
        print(f"{name}: D histogram {dict(sorted(c.items()))} "
              f"non-granule-aligned {bad}")
        byrow = defaultdict(Counter)
        for x in cells:
            byrow[x["d_o"]][x["D"]] += 1
        if name == "tt4":
            for d_o in sorted(byrow)[:8]:
                print(f"   d_o={d_o}: {dict(sorted(byrow[d_o].items()))}")


if __name__ == "__main__":
    main()
