#!/usr/bin/env python3
"""Two-stage decode of the eps-tomography v3 accumulator (t=0 rows).

At t=0 the wide part W = P = 2^23 * d_o is exact, so the 64-word scan
of each row is a pure function of the accumulator pipeline.  Model:
  s1 = part_a + part_b   -> intermediate quantize Q1
  s2 = s1 + part_c       -> final round F -> exported word
Parts (P-units): W = P; X = -(64+u)*4v; A = +(64+u)*8v, v = 2^(bl-30).
Sweep order x Q1 x F; a candidate must reproduce all 47x64 words.
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from fractions import Fraction
from pathlib import Path

sys.path[:0] = ["/tmp/walle", "/tmp/walle/analysis"]

import _sweep_fused_join_lattice as m  # noqa: E402

D = Path("/tmp/walle/build/analysis-agx-basis/c-epsilon-tomography-plan-v3")


def load():
    plan = json.load(open(D / "reveal-agx-setup-accumulator-plan.json"))
    T = m.load_records(D / "capture.raw", len(plan["draws"]))
    cells = defaultdict(dict)
    for exp, draw in zip(plan["experiments"], plan["draws"]):
        t = int(exp["family"][1:])
        cells[(t, draw["tileY"])][exp["offset"]] = int(
            T[exp["recordIndex"]][0][2])
    return cells


def wordval(w: int) -> Fraction:
    s, mant, e = m.f32_parts(w)
    return Fraction(s * mant) * Fraction(2) ** (e + 37)


def q_bits(x: Fraction, nbits: int, mode: str) -> Fraction:
    """Quantize x to nbits-significand at its own binade."""
    if x == 0:
        return x
    s = -1 if x < 0 else 1
    ax = abs(x)
    e = 0
    while ax >= 2:
        ax /= 2
        e += 1
    while ax < 1:
        ax *= 2
        e -= 1
    scaled = ax * (1 << (nbits - 1))
    fl = int(scaled)
    frac = scaled - fl
    if mode == "rtz":
        mant = fl
    elif mode == "rna":
        mant = fl + (1 if frac >= Fraction(1, 2) else 0)
    elif mode == "rne":
        if frac > Fraction(1, 2) or (frac == Fraction(1, 2) and fl & 1):
            mant = fl + 1
        else:
            mant = fl
    elif mode == "jam":
        mant = fl | 1
    else:
        raise ValueError(mode)
    return Fraction(s * mant, 1 << (nbits - 1)) * Fraction(2) ** e


def chain24(x: Fraction) -> Fraction:
    return q_bits(q_bits(x, 27, "rna"), 24, "rne")


FINALS = {
    "rne24": lambda x: q_bits(x, 24, "rne"),
    "rna24": lambda x: q_bits(x, 24, "rna"),
    "chain": chain24,
}
INTERS = {
    "none": lambda x: x,
    "rne27": lambda x: q_bits(x, 27, "rne"),
    "rna27": lambda x: q_bits(x, 27, "rna"),
    "rtz27": lambda x: q_bits(x, 27, "rtz"),
    "jam27": lambda x: q_bits(x, 27, "jam"),
    "rne28": lambda x: q_bits(x, 28, "rne"),
    "rna28": lambda x: q_bits(x, 28, "rna"),
    "rtz28": lambda x: q_bits(x, 28, "rtz"),
}
ORDERS = {
    "WX_A": (0, 1, 2),
    "WA_X": (0, 2, 1),
    "XA_W": (1, 2, 0),
}


def main() -> None:
    cells = load()
    rows = []
    for (t, ty), seq in sorted(cells.items()):
        if t != 0:
            continue
        d_o = 128 * ty - 2047
        P = (1 << 23) * d_o
        bl = P.bit_length()
        v = Fraction(1 << (bl - 24), 64)
        obs = [wordval(seq[u]) for u in range(64)]
        rows.append((P, v, obs))
    results = []
    for oname, order in ORDERS.items():
        for iname, inter in INTERS.items():
            for fname, fin in FINALS.items():
                ok = tot = 0
                for P, v, obs in rows:
                    for u in range(64):
                        parts = [Fraction(P), -(64 + u) * 4 * v,
                                 (64 + u) * 8 * v]
                        a, b, c = (parts[order[0]], parts[order[1]],
                                   parts[order[2]])
                        s2 = fin(inter(a + b) + c)
                        tot += 1
                        ok += s2 == obs[u]
                results.append((ok, tot, oname, iname, fname))
    results.sort(reverse=True)
    for ok, tot, oname, iname, fname in results[:14]:
        print(f"{ok:5d}/{tot} {oname} {iname:6s} {fname}")


if __name__ == "__main__":
    main()
