#!/usr/bin/env python3
"""Solve for a truncated-multiplier constant correction.

Hypothesis: the setup multiplier forms dm x didx where didx is the *integer
subpixel* displacement, and the array omits the lowest T columns, adding a
fixed constant K in their place:

    frame' = ((dm*didx + K) >> T) << T          then the proven narrow law.

tt3's displacements carry 13 trailing zeros, tt1's carry 7 and tt4's carry 6,
so such a rule is invisible on tt3 and active on the other two -- which is
exactly what the captures show (identical products P=0x800004*13 round
DOWN in tt3 and UP in tt1).

Because frame' and the narrow law are both monotone in K, the set of K
reproducing any one capture is a contiguous interval; the law exists for a
given T iff those intervals share a point.
"""

from __future__ import annotations

import sys

sys.path[:0] = ["/tmp/walle", "/tmp/walle/analysis"]

import wide_solver_data as W  # noqa: E402
import wide_solver_sweep as S  # noqa: E402
import _sweep_fused_join_lattice as m  # noqa: E402


def k_interval(dm: int, d_o: int, z: int, e: int, sign: int, c_word: int,
               T: int) -> tuple[int, int]:
    """Inclusive [lo, hi] of K in [0, 2^T) reproducing c_word, else (1, 0)."""
    frame = dm * (d_o << z)
    sign_c, mant_c, e_c = m.f32_parts(c_word)
    if sign_c != sign:
        return (1, 0)
    target = mant_c << (e_c - e + z)

    def val(K: int) -> int:
        mant, sh = W.narrow(((frame + K) >> T) << T)
        return mant << sh

    top = (1 << T) - 1
    if val(0) > target or val(top) < target:
        return (1, 0)
    a, b = 0, top
    while a < b:
        mid = (a + b) // 2
        if val(mid) >= target:
            b = mid
        else:
            a = mid + 1
    lo = a
    a, b = 0, top
    while a < b:
        mid = (a + b + 1) // 2
        if val(mid) <= target:
            a = mid
        else:
            b = mid - 1
    hi = a
    if val(lo) != target or val(hi) != target:
        return (1, 0)
    return (lo, hi)


def main() -> None:
    data = []
    for name in S.NAMES:
        rows, z = S.obs(name)
        data.append((name, rows, z))
    for T in range(int(sys.argv[1]) if len(sys.argv) > 1 else 7,
                   int(sys.argv[2]) if len(sys.argv) > 2 else 18):
        lo, hi = 0, (1 << T) - 1
        feasible = 0
        total = 0
        worst = None
        for name, rows, z in data:
            for dm, e, d_o, sign, c_word in rows:
                total += 1
                a, b = k_interval(dm, d_o, z, e, sign, c_word, T)
                if a > b:
                    continue
                feasible += 1
                nlo, nhi = max(lo, a), min(hi, b)
                if nlo > nhi and worst is None:
                    worst = (name, dm, d_o, (lo, hi), (a, b))
                lo, hi = nlo, nhi
        state = f"K in [{lo},{hi}]" if lo <= hi else f"EMPTY (first clash {worst})"
        print(f"T={T:2d}  reachable {feasible}/{total}  {state}")


if __name__ == "__main__":
    main()
