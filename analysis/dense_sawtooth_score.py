#!/usr/bin/env python3
"""Dense-corpus scoring with the unified sawtooth + theta export law.

Uses the banked parts machinery (build28 + score_c_chain_dense.c_word)
for the exact per-axis products and anchor sum, then replaces the
final RNE24 with: value += sawtooth(axis products, anchor phases);
export = floor + [dropped >= theta(parity)].

sawtooth per axis = -wrap29/64((mant24(slope*sel) * p_axis) mod 2^19)
                    * 2^(bl(part)-42)
with p_axis = the anchor's fractional subpixel phase on that axis.
Theta swept globally (theta_even, theta_even-8).
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from fractions import Fraction
from pathlib import Path

sys.path[:0] = ["/tmp/walle", "/tmp/walle/analysis"]

import _sweep_fused_join_lattice as m  # noqa: E402
import _fit_child_tiles as ft  # noqa: E402
from hunt_c_walk_seed import build28, load_sdf  # noqa: E402
import score_c_chain_dense as sc  # noqa: E402

F2 = Fraction(2)
MOD = 1 << 19
CUT = (29 * MOD) // 64


def sawtooth(mant24: int, p: int) -> int:
    tm = (mant24 * p) % MOD
    return -tm + (MOD if tm >= CUT else 0)


def main() -> None:
    D = Path("/tmp/walle/build/analysis-agx-basis/"
             "residual-children-dense-plan-v1")
    PLAN = json.load(open(D / "reveal-agx-setup-accumulator-plan.json"))
    T = m.load_records(D / "capture.raw", len(PLAN["draws"]))
    groups = defaultdict(dict)
    for exp, draw in zip(PLAN["experiments"], PLAN["draws"]):
        key = (exp["state"], exp["drawOrdinal"])
        r = exp["recordIndex"]
        for ctx in range(2):
            A, B, C = (int(T[r][ctx][i]) for i in range(3))
            groups[key + (ctx, A, B)][(draw["tileX"], draw["tileY"])] = C

    sdf_cache = {}
    knobs = ("mid", 10, 27, 20)
    results = {}
    for theta in (17, 21, 25, 29, 33, 37, 41):
        ok = tot = 0
        for (st, ordn, ctx, A, B), tiles in sorted(groups.items()):
            if st not in sdf_cache:
                try:
                    sdf_cache[st] = load_sdf(st)
                except Exception:
                    sdf_cache[st] = None
            sdf = sdf_cache[st]
            if sdf is None or ordn not in sdf:
                continue
            verts = sdf[ordn]
            vwords = [verts[v][2 + ctx] for v in range(3)]
            try:
                n28, sel, se, ds, an, fx = build28(verts, vwords)
            except Exception:
                continue
            asign, amant, aexp = (m.f32_parts(vwords[an])
                                  if vwords[an] else (0, 0, 0))
            order, MIDC, MW, SELC = knobs
            for (tx, ty), hwC in tiles.items():
                parts = []
                saws = []
                for axis, tp in ((0, tx * 8192), (1, ty * 8192)):
                    r_ = sc.c_word(order, n28[axis], sel, se, ds,
                                   fx[an][axis], tp, MIDC, MW, SELC)
                    if r_ is not None:
                        parts.append(r_)
                        s_, mag, gg = n28[axis]
                        smant = mag * sel
                        if smant:
                            dm24 = smant >> max(0, smant.bit_length() - 24)
                            p_ax = (8192 - (fx[an][axis] % 8192)) % 8192
                            val, e_ = r_
                            blp = abs(val).bit_length()
                            sgn_p = 1 if val > 0 else -1
                            saws.append((sgn_p * sawtooth(dm24, p_ax),
                                         blp - 42, e_))
                if not parts:
                    val = (Fraction(asign * amant) * F2 ** aexp
                           if amant else Fraction(0))
                else:
                    gmin = min(e for _, e in parts)
                    tot_ = sum(v << (e - gmin) for v, e in parts)
                    if amant:
                        gm2 = min(gmin, aexp)
                        tot_ = ((tot_ << (gmin - gm2))
                                + asign * amant * (1 << (aexp - gm2)))
                        gmin = gm2
                    s28, m28, e28 = ft.norm(tot_, gmin, 28, "rne")
                    val = Fraction(s28 * m28) * F2 ** e28
                # add sawtooths (value-domain)
                for sw, blsh, e_ in saws:
                    val += Fraction(sw) * F2 ** (blsh + e_)
                if val == 0:
                    pred = 0
                else:
                    aval = abs(val)
                    num = aval.numerator
                    bl = num.bit_length()
                    G = 1 << max(0, bl - 24)
                    v_ = max(1, G >> 6)
                    M = num // G
                    r2 = (num % G) // v_
                    t2 = theta if (M & 1) == 0 else theta - 8
                    mant = M + (1 if r2 >= t2 else 0)
                    blm = mant.bit_length()
                    if blm > 24:
                        mant >>= blm - 24
                    elif blm < 24 and mant:
                        mant <<= 24 - blm
                    pred = mant
                hw_m = (hwC & 0x7FFFFF) | 0x800000 if hwC else 0
                tot += 1
                ok += pred == hw_m
        results[theta] = (ok, tot)
        print(f"theta={theta}: {ok}/{tot}")


if __name__ == "__main__":
    main()
