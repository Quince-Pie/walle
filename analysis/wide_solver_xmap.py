#!/usr/bin/env python3
"""Extract, per observation, the exact interval of pre-rounding values that
would reproduce the captured word under the proven narrow law.

Model lens: hw_word = encode( narrow(V) ) for some internal V.  Because
`narrow` is monotone in V, the set of V reproducing the captured word is a
contiguous integer interval [Vlo, Vhi].  Reporting X = V - P (P = dm*d_o)
turns every capture into a hard two-sided constraint on the deviation.
"""

from __future__ import annotations

import sys
from collections import defaultdict

sys.path[:0] = ["/tmp/walle", "/tmp/walle/analysis"]

import wide_solver_data as W  # noqa: E402
import _sweep_fused_join_lattice as m  # noqa: E402


def narrowval(V: int) -> int:
    """Value (in P's lsb units) produced by the narrow law on input V."""
    mant, sh = W.narrow(V)
    return mant << sh


_PRE_CACHE: dict[int, tuple[int, int]] = {}


def preimage(target: int) -> tuple[int, int]:
    """Inclusive [lo, hi] of integers V with narrowval(V) == target."""
    hit = _PRE_CACHE.get(target)
    if hit is not None:
        return hit
    # lo: smallest V with narrowval(V) >= target
    a, b = 1, 1 << 52
    while a < b:
        mid = (a + b) // 2
        if narrowval(mid) >= target:
            b = mid
        else:
            a = mid + 1
    lo = a
    # hi: largest V with narrowval(V) <= target
    a, b = 1, 1 << 52
    while a < b:
        mid = (a + b + 1) // 2
        if narrowval(mid) <= target:
            a = mid
        else:
            b = mid - 1
    hi = a
    if narrowval(lo) != target or narrowval(hi) != target:
        res = (1, 0)  # empty: captured word is not in the range of narrow()
    else:
        res = (lo, hi)
    _PRE_CACHE[target] = res
    return res


def observations(name: str):
    """Yield dicts describing each cell with its exact X interval."""
    for dm, e, d_o, sign, c_word in W.load(name):
        P = dm * d_o
        sign_c, mant_c, e_c = m.f32_parts(c_word)
        k = e_c - e
        target = mant_c << k if k >= 0 else None
        if target is None:
            yield dict(dm=dm, d_o=d_o, P=P, bad="negative k", xlo=1, xhi=0)
            continue
        lo, hi = preimage(target)
        yield dict(dm=dm, d_o=d_o, P=P, sign=sign, sign_c=sign_c,
                   mant=mant_c, k=k, target=target,
                   xlo=lo - P, xhi=hi - P,
                   bl=P.bit_length(), cut=k,
                   dropped=P & ((1 << k) - 1) if k > 0 else 0)


def main() -> None:
    for name in ("tt3", "tt4", "tt1"):
        rows = list(observations(name))
        empty = [r for r in rows if r["xlo"] > r["xhi"]]
        zero_ok = sum(1 for r in rows if r["xlo"] <= 0 <= r["xhi"])
        widths = defaultdict(int)
        for r in rows:
            if r["xlo"] <= r["xhi"]:
                widths[r["xhi"] - r["xlo"] + 1] += 1
        print(f"{name}: {len(rows)} cells, X=0 admissible in {zero_ok}, "
              f"unreachable words {len(empty)}")
        tight = sorted(widths)[:4]
        print(f"   interval widths (smallest): "
              f"{[(w, widths[w]) for w in tight]}")


if __name__ == "__main__":
    main()
