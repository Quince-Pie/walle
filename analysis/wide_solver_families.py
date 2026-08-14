#!/usr/bin/env python3
"""Broad family sweep for the wide-path law.

Scoring is exact integer arithmetic throughout.  Every family is scored on
all three frozen datasets; a candidate is only interesting if it holds
tt3 at 18001 (the narrow-law control) while beating tt4/tt1.
"""

from __future__ import annotations

import sys
from itertools import product

sys.path[:0] = ["/tmp/walle", "/tmp/walle/analysis"]

import wide_solver_data as W  # noqa: E402
import wide_solver_sweep as S  # noqa: E402

MODES = W.MODES


def run(tag: str, law, results: list) -> tuple:
    s = S.score_all(law)
    results.append((s, tag))
    return s


def report(results: list, top: int = 12) -> None:
    results.sort(key=lambda r: (-sum(r[0]), r[1]))
    print("  best:")
    for s, tag in results[:top]:
        flag = " <== BEATS BAR" if (s[0] > 14141 and s[1] >= 18001
                                    and s[2] > 2124) else ""
        print(f"    {s}  total {sum(s):6d}  {tag}{flag}")


# ---------------------------------------------------------------- families

def fam_granule_bias(results):
    """narrow(P + c * 2^(bl(P)-r)) -- bias proportional to the granule."""
    for r, c in product((28, 29, 30, 31, 32), range(0, 40)):
        def law(dm, d_o, z, r=r, c=c):
            P = dm * d_o
            sh = P.bit_length() - r
            return W.narrow(P + (c << sh if sh > 0 else 0))
        run(f"granule-bias r={r} c={c}", law, results)


def fam_split_dm(results):
    """dm = H*2^s + L; low partial L*d_o rounded to Wl significant bits."""
    for s, Wl, mode in product(range(9, 17), range(14, 28),
                               ("rtz", "rne", "rna", "rup")):
        def law(dm, d_o, z, s=s, Wl=Wl, mode=mode):
            H, L = dm >> s, dm & ((1 << s) - 1)
            didx = d_o << z
            lo = L * didx
            mant, sh = MODES[mode](lo, Wl)
            return _fin((H * didx << s) + (mant << sh), z)
        run(f"split-dm s={s} W={Wl} {mode}", law, results)


def fam_split_didx(results):
    """didx = DH*2^s + DL; low partial dm*DL rounded to Wl significant bits."""
    for s, Wl, mode in product(range(8, 17), range(14, 28),
                               ("rtz", "rne", "rna", "rup")):
        def law(dm, d_o, z, s=s, Wl=Wl, mode=mode):
            didx = d_o << z
            DH, DL = didx >> s, didx & ((1 << s) - 1)
            lo = dm * DL
            mant, sh = MODES[mode](lo, Wl)
            return _fin((dm * DH << s) + (mant << sh), z)
        run(f"split-didx s={s} W={Wl} {mode}", law, results)


def fam_cascade(results):
    """P -> W1 bits (mode1) -> 27 bits (rna) -> RNE24."""
    for W1, m1 in product(range(25, 40), ("rtz", "rne", "rna", "rup", "rodd")):
        def law(dm, d_o, z, W1=W1, m1=m1):
            P = dm * d_o
            a, sa = MODES[m1](P, W1)
            b, sb = W.narrow(a)
            return b, sa + sb
        run(f"cascade W1={W1} {m1}", law, results)


def _fin(frame: int, z: int):
    mant, sh = W.narrow(frame)
    return mant, sh - z


FAMILIES = {
    "granule-bias": fam_granule_bias,
    "split-dm": fam_split_dm,
    "split-didx": fam_split_didx,
    "cascade": fam_cascade,
}


def main() -> None:
    which = sys.argv[1:] or list(FAMILIES)
    for name in which:
        print(f"### family {name}")
        results = []
        FAMILIES[name](results)
        report(results)
        print()


if __name__ == "__main__":
    main()
