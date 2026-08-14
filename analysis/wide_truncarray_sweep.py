#!/usr/bin/env python3
"""Truncated partial-product array sweep (fixed 48-grid frame).

Hypothesis (from the killer cell dm=0x800C00, d_o=1793): the C-product
multiplier operates on dm x didx24 (didx24 = d_o << (24 - bl(d_o))),
discards all partial-product bits below a fixed column Tc of the 48-bit
grid, adds a compensation constant at/near column Tc, then feeds the
narrow chain rna27 -> RNE24.  Killer arithmetic: deficit 3072<<13,
comp 2048<<13 -> net -1024<<13 = one 24-bit granule low, as captured.

Sweep: orientation (PPs indexed by didx24 bits / dm bits / no array =
whole-product truncation), Tc, compensation comp = k * 2^(Tc-6)
(k = 0..40; k=32 is the von-Neumann jam at Tc), plus sticky per-PP
compensation (n_nonzero_lost * 2^(Tc-1)) and per-PP RNE at Tc.
"""
from __future__ import annotations

import sys

sys.path[:0] = ["/tmp/walle", "/tmp/walle/analysis"]

import wide_solver_data as W  # noqa: E402


def bases(obs, Tc: int):
    """Per-cell truncated sums for each orientation, plus sticky counts."""
    mask = (1 << Tc) - 1
    out = []
    for dm, e, d_o, sign, c_word in obs:
        nd = 24 - d_o.bit_length()
        didx = d_o << nd
        full = dm * didx
        rows = {}
        for name, mult, mcand in (("didx", didx, dm), ("dm", dm, didx)):
            total = 0
            n_sticky = 0
            lost_sum = 0
            i = 0
            mm = mult
            while mm:
                if mm & 1:
                    pp = mcand << i
                    lost = pp & mask
                    total += pp - lost
                    lost_sum += lost
                    if lost:
                        n_sticky += 1
                i += 1
                mm >>= 1
            rows[name] = (total, n_sticky, lost_sum)
        rows["full"] = (full & ~mask, 1 if full & mask else 0, full & mask)
        out.append((nd, rows, sign, e, c_word))
    return out


def score_variant(rows, orient: str, comp_of) -> int:
    hits = 0
    for nd, r, sign, e, c_word in rows:
        total, n_sticky, lost = r[orient]
        t = total + comp_of(n_sticky, lost)
        try:
            mant, k = W.narrow(t)
            pred = W.f32_from_int(sign, mant, e + k - nd)
        except (ValueError, ZeroDivisionError):
            pred = None
        hits += pred == c_word
    return hits


def main() -> None:
    names = sys.argv[1:] or ["tt4"]
    data = {n: W.load(n) for n in names}
    base = {n: W.score(data[n], lambda dm, d_o: W.narrow(dm * d_o))[0]
            for n in names}
    for n in names:
        print(f"baseline {n}: {base[n]}/{len(data[n])}")
    best = []
    for Tc in range(14, 29):
        cell = {n: bases(data[n], Tc) for n in names}
        for orient in ("didx", "dm", "full"):
            variants = {}
            for k in range(0, 41):
                variants[f"k{k}"] = (
                    lambda ns, lost, kk=k, T=Tc: kk << (T - 6))
            variants["sticky-half"] = (
                lambda ns, lost, T=Tc: ns << (T - 1))
            variants["pp-rne"] = (
                lambda ns, lost, T=Tc:
                    ((lost + (ns << (T - 1))) >> T) << T)
            for vname, comp in variants.items():
                scores = {n: score_variant(cell[n], orient, comp)
                          for n in names}
                gain = sum(scores[n] - base[n] for n in names)
                if gain > 0:
                    best.append((gain, Tc, orient, vname, dict(scores)))
        sys.stderr.write(f"Tc={Tc} done\n")
    best.sort(reverse=True)
    for gain, Tc, orient, vname, scores in best[:25]:
        print(f"gain {gain:+6d}  Tc={Tc:2d} {orient:4s} {vname:11s} "
              + " ".join(f"{n}={scores[n]}" for n in sorted(scores)))
    if not best:
        print("no variant beat baseline")


if __name__ == "__main__":
    main()
