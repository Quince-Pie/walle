#!/usr/bin/env python3
"""Chain-with-pre-bias: C = RNE24(rna27(P + A/2 * 2^(bl-31))), bl >= 31.

Derivation: composing rna27 (half-away at 4v within 8v quanta) with
RNE24 (half-even at 32v) yields round-up thresholds 36v (M even) /
28v (M odd), v = 2^(cut-6).  Hardware thresholds are 27v/19v - both
exactly 9v lower -> the wide path is the narrow chain applied to
P + 9v.  Sweep A_half (units of v/2) and the bl gate.
"""
from __future__ import annotations

import sys

sys.path[:0] = ["/tmp/walle", "/tmp/walle/analysis"]

import wide_solver_data as W  # noqa: E402


def law(dm: int, d_o: int, a_half: int, gate: int):
    P = dm * d_o
    bl = P.bit_length()
    if bl >= gate and bl >= 31:
        P2 = (P << 1) + (a_half << (bl - 31 + 1 - 1))
        # P2 = 2P + a_half*2^(bl-31): chain on doubled value, exponent -1
        m2, k2 = W.narrow(P2)
        return m2, k2 - 1
    return W.narrow(P)


def main() -> None:
    names = sys.argv[1:] or ["tt4", "tt3", "tt1"]
    data = {n: W.load(n) for n in names}
    base = {n: W.score(data[n], lambda dm, d_o: W.narrow(dm * d_o))[0]
            for n in names}
    print(" ".join(f"baseline {n}={base[n]}/{len(data[n])}" for n in names))
    rows = []
    for gate in (31, 32):
        for a_half in range(0, 41):
            scores = {}
            for n in names:
                hits = 0
                for dm, e, d_o, sign, c_word in data[n]:
                    try:
                        mant, k = law(dm, d_o, a_half, gate)
                        pred = W.f32_from_int(sign, mant, e + k)
                    except (ValueError, ZeroDivisionError):
                        pred = None
                    hits += pred == c_word
                scores[n] = hits
            gain = sum(scores[n] - base[n] for n in names)
            rows.append((gain, gate, a_half, scores))
    rows.sort(reverse=True)
    for gain, gate, a_half, scores in rows[:15]:
        print(f"gain {gain:+6d}  gate>={gate} A={a_half/2:4.1f}v  "
              + " ".join(f"{n}={scores[n]}" for n in sorted(scores)))


if __name__ == "__main__":
    main()
