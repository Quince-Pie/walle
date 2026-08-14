#!/usr/bin/env python3
"""Unit-tested eps-lever decoder (later-126).

Reads an eps scan (exports vs eps step u) and returns the internal
value deviation delta2 in v units.  All arithmetic in the dm*disp
integer frame scaled by 2^43 (value = dm*disp*2^-43 for the tt4-
family geometry; eps net lever = (64+u)*2^(k-1) value units).

KEY: an export flip at sample u means V + net crossed the biased
threshold boundary (M + theta(par)/64)*G - NOT the RNE midpoint.

self-test: eps-v3 (q=64) t=0 scans must decode to |delta2| <= 3v
across all rows (V = P exactly).
"""
from __future__ import annotations

import json
import sys
from fractions import Fraction
from pathlib import Path
from statistics import median

sys.path[:0] = ["/tmp/walle", "/tmp/walle/analysis"]

import _sweep_fused_join_lattice as m  # noqa: E402

THETA_E, THETA_O = 25, 17


def word_value_f43(word: int) -> Fraction:
    s, mant, e = m.f32_parts(word)
    return Fraction(s * mant) * Fraction(2) ** (e + 43)


def decode(seq: dict, dm: int, disp: int, k: int):
    """Return (delta2_in_v, n_flips) or None."""
    us = sorted(seq)
    vals = {u: word_value_f43(seq[u]) for u in us}
    dd = dm * disp
    bl = dd.bit_length()
    G43 = Fraction(1 << bl, 1 << 24)  # granule in the f43 frame... see below
    # value = dd * 2^-43; in f43 frame value*2^43 = dd exactly.
    # granule of the export = 2^(bl-24) (same frame); v = G/64.
    G = 1 << (bl - 24)
    v = Fraction(G, 64)
    net = lambda u: Fraction(64 + u) * Fraction(2) ** (k - 1 + 43)
    ests = []
    for u1, u2 in zip(us, us[1:]):
        if vals[u2] > vals[u1]:
            # boundary crossed between u1 and u2: word below = vals[u1]
            M = vals[u1] / G  # lower word in granule units (may be frac)
            Mi = int(M)
            par = Mi & 1
            th = THETA_E if par == 0 else THETA_O
            B = (Mi + 1) * G - G + Fraction(th, 64) * G + G  # (Mi + th/64 + 1)? no:
            # crossing from word Mi to Mi+1 happens when V+net >= (Mi+1-1+th/64+1)...
            # The up-threshold for exporting Mi+1 is at (Mi + th/64)*G + G? No:
            # export = Mi + [dropped >= th*v]: the flip to Mi+1 occurs when
            # (V+net) - Mi*G >= th*v i.e. V+net = Mi*G + th*v.
            B = Mi * G + th * v
            ustar = Fraction(u1 + u2, 2)
            ests.append(B - net(ustar) - dd)
    if len(ests) < 2:
        return None
    d2 = median(ests) / v
    return float(d2), len(ests)


def self_test() -> None:
    D = Path("/tmp/walle/build/analysis-agx-basis/"
             "c-epsilon-tomography-plan-v3")
    plan = json.load(open(D / "reveal-agx-setup-accumulator-plan.json"))
    T = m.load_records(D / "capture.raw", len(plan["draws"]))
    cells = {}
    meta = {}
    for exp, draw in zip(plan["experiments"], plan["draws"]):
        t = int(exp["family"][1:])
        if t != 0:
            continue
        key = draw["tileY"]
        cells.setdefault(key, {})[exp["offset"]] = int(
            T[exp["recordIndex"]][0][2])
        meta[key] = exp["epsK"]
    ay = 131008
    worst = 0.0
    n = 0
    for ty, seq in sorted(cells.items()):
        disp = ty * 8192 - ay
        if disp <= 0:
            continue
        # v3 lever k was bl(P)-64 with P = dm*d_o (d_o = disp>>6):
        # in the dm*disp frame that is k = bl(dm*disp) - 70; the plan
        # stored epsK directly - use it.
        res = decode(seq, 1 << 23, disp, meta[ty])
        if res is None:
            continue
        d2, nf = res
        worst = max(worst, abs(d2))
        n += 1
        if abs(d2) > 3.0:
            print(f"  ty={ty}: delta2={d2:+.1f}v ({nf} flips)  *** out of spec")
        else:
            print(f"  ty={ty}: delta2={d2:+.1f}v ({nf} flips)")
    print(f"self-test rows={n} worst |delta2| = {worst:.1f}v "
          f"({'PASS' if worst <= 3.0 else 'FAIL'})")


if __name__ == "__main__":
    self_test()
