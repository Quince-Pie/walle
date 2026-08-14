#!/usr/bin/env python3
"""Column-truncated multiplier array hypothesis.

The setup multiplier forms dm x didx24 (didx24 = the odd part of the
displacement normalised to 24 bits) but the array omits every partial-product
bit in column < T, replacing them by a constant K:

    value = sum_{i+j >= T} dm_i * didx24_j * 2^(i+j)  +  K      then narrow()

Why this is the right shape: tt3's displacements have at most 6 significant
bits, so didx24 carries >= 18 trailing zeros and NO partial product lands in
a column below 18 -- the truncation is invisible there for any T <= 18, which
is exactly why tt3 is narrow-law exact.  tt4 (up to 13 significant bits) and
tt1 (12) put bits in columns 11..17, so the same array is lossy for them.

Unlike a post-hoc `frame mod 2^T` truncation (already falsified), the dropped
column sum can exceed 2^T, so this family reaches the +-1 ulp deviations the
captures actually show.
"""

from __future__ import annotations

import sys

sys.path[:0] = ["/tmp/walle", "/tmp/walle/analysis"]

import wide_solver_data as W  # noqa: E402
import wide_solver_sweep as S  # noqa: E402
import _sweep_fused_join_lattice as m  # noqa: E402


def normalise(d_o: int) -> tuple[int, int]:
    """Return (didx24, shift) with didx24 = odd part of d_o << to 24 bits."""
    n = d_o
    tz = 0
    while not n & 1:
        n >>= 1
        tz += 1
    up = 24 - n.bit_length()
    return n << up, up + tz          # frame24 = P << shift


def dropped(dm: int, didx24: int, T: int) -> int:
    """Sum of partial-product bits in columns < T (the omitted array corner)."""
    total = 0
    j = 0
    d = didx24
    while d and j < T:
        if d & 1:
            total += (dm & ((1 << (T - j)) - 1)) << j
        d >>= 1
        j += 1
    return total


def make_law(T: int, K: int):
    def law(dm: int, d_o: int, z: int):
        didx24, shift = normalise(d_o)
        frame = dm * didx24
        mant, sh = W.narrow(frame - dropped(dm, didx24, T) + K)
        return mant, sh - shift
    return law


def k_interval(dm, e, d_o, sign, c_word, T, kmax):
    didx24, shift = normalise(d_o)
    frame = dm * didx24 - dropped(dm, didx24, T)
    sign_c, mant_c, e_c = m.f32_parts(c_word)
    if sign_c != sign:
        return (1, 0)
    target = mant_c << (e_c - e + shift)

    def val(K):
        mant, sh = W.narrow(frame + K)
        return mant << sh

    if val(0) > target or val(kmax) < target:
        return (1, 0)
    a, b = 0, kmax
    while a < b:
        mid = (a + b) // 2
        if val(mid) >= target:
            b = mid
        else:
            a = mid + 1
    lo = a
    a, b = 0, kmax
    while a < b:
        mid = (a + b + 1) // 2
        if val(mid) <= target:
            a = mid
        else:
            b = mid - 1
    return (lo, a) if val(lo) == target else (1, 0)


def main() -> None:
    data = [(n,) + S.obs(n) for n in S.NAMES]
    for T in range(12, 22):
        kmax = 1 << (T + 5)
        lo, hi = 0, kmax
        reach = 0
        total = 0
        clash = None
        for name, rows, z in data:
            for dm, e, d_o, sign, c_word in rows:
                total += 1
                a, b = k_interval(dm, e, d_o, sign, c_word, T, kmax)
                if a > b:
                    continue
                reach += 1
                nlo, nhi = max(lo, a), min(hi, b)
                if nlo > nhi and clash is None:
                    clash = (name, hex(dm), d_o, (lo, hi), (a, b))
                lo, hi = nlo, nhi
        state = (f"K in [{lo},{hi}]" if lo <= hi
                 else f"EMPTY first clash {clash}")
        print(f"T={T:2d} reachable {reach}/{total}  {state}")
        if lo <= hi:
            s = S.score_all(make_law(T, lo))
            print(f"      score with K={lo}: {s}")


if __name__ == "__main__":
    main()
