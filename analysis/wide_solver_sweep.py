#!/usr/bin/env python3
"""Sweep harness for wide-path candidate laws.

Key structural fact this harness exposes that the P-only view hides: the
three datasets differ in how many trailing zeros the *subpixel* displacement
carries (tt3: 13, tt1: 7, tt4: 6).  A law that quantises at a fixed absolute
bit of the integer product dm * disp is therefore invisible on tt3 and
active on tt4/tt1 -- exactly the observed narrow/wide split.

A candidate law is law(dm, d_o, z) -> (mant, k) where disp = d_o << z.
"""

from __future__ import annotations

import sys
from functools import lru_cache

sys.path[:0] = ["/tmp/walle", "/tmp/walle/analysis"]

import wide_solver_data as W  # noqa: E402

Z = {"tt3": 13, "tt4": 6, "tt1": 7}
NAMES = ("tt4", "tt3", "tt1")


@lru_cache(maxsize=None)
def obs(name: str):
    return tuple(W.load(name)), Z[name]


def score(name: str, law, want_misses: int = 0):
    rows, z = obs(name)
    hits = 0
    misses = []
    for dm, e, d_o, sign, c_word in rows:
        try:
            mant, k = law(dm, d_o, z)
            pred = W.f32_from_int(sign, mant, e + k)
        except (ValueError, ZeroDivisionError):
            pred = None
        if pred == c_word:
            hits += 1
        elif len(misses) < want_misses:
            misses.append((dm, d_o, c_word, pred))
    return hits, len(rows), misses


def score_all(law):
    return tuple(score(n, law)[0] for n in NAMES)


def quant(V: int, u: int, mode: str) -> int:
    """Quantise V to a multiple of 2^u at a FIXED absolute position."""
    if u <= 0:
        return V
    step = 1 << u
    q, r = divmod(V, step)
    if mode == "rtz":
        pass
    elif mode == "rup":
        q += 1 if r else 0
    elif mode == "rna":
        q += 1 if r * 2 >= step else 0
    elif mode == "rne":
        if r * 2 > step or (r * 2 == step and q & 1):
            q += 1
    elif mode == "rodd":
        if r:
            q |= 1
    else:
        raise ValueError(mode)
    return q << u


def main() -> None:
    base = W.narrow
    print("reference (narrow law):",
          score_all(lambda dm, d_o, z: base(dm * d_o)))
    print()
    print("family: narrow( quant_T( dm*disp ) ), T absolute in dm*disp")
    best = []
    for T in range(0, 20):
        line = []
        for mode in ("rtz", "rne", "rna", "rup", "rodd"):
            def law(dm, d_o, z, T=T, mode=mode):
                mant, k = base(quant(dm * (d_o << z), T, mode))
                return mant, k - z   # frame is dm*disp = P << z
            s = score_all(law)
            line.append(f"{mode}:{s[0]:5d}/{s[1]:5d}/{s[2]:4d}")
            best.append((sum(s), T, mode, s))
        print(f" T={T:2d}  " + "  ".join(line))
    best.sort(reverse=True)
    print("\ntop:")
    for tot, T, mode, s in best[:8]:
        print(f"  T={T} {mode}: {s}  total {tot}")


if __name__ == "__main__":
    main()
