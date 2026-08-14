#!/usr/bin/env python3
"""Validate basis-sum generation rules against the a2-allts capture.

(a) model per-basis 24-bit words vs hardware basis exports (s42 tris
    0/2/6), (b) candidate one-plane rules: exact sum of the model's
    28-bit-stage basis values, rounded RNE24 / RTZ24, vs the hardware
    all-1.0 C word - across every captured (state, triangle, tile).
"""
from __future__ import annotations

import json
import struct
import sys
from collections import defaultdict
from fractions import Fraction
from pathlib import Path

sys.path[:0] = ["/tmp/walle", "/tmp/walle/analysis"]

import _sweep_fused_join_lattice as m  # noqa: E402
from hunt_c_walk_seed import rne24_word_frac  # noqa: E402
import a2_rule_generate as rg  # noqa: E402

ONE = 0x3F800000
D = Path("/tmp/walle/build/analysis-agx-basis/a2-allts-plan-v1")


def rtz24_word(x: Fraction) -> int:
    if x == 0:
        return 0
    s = -1 if x < 0 else 1
    ax = abs(x)
    e = 0
    while ax >= 2:
        ax /= 2
        e += 1
    while ax < 1:
        ax *= 2
        e -= 1
    mant = int(ax * (1 << 23))
    E = e + 127
    return ((1 << 31) if s < 0 else 0) | (E << 23) | (mant & 0x7FFFFF)


def main() -> None:
    plan = json.load(open(D / "reveal-agx-setup-accumulator-plan.json"))
    T = m.load_records(D / "capture.raw", len(plan["draws"]))
    one = {}
    basis_hw = defaultdict(dict)
    for exp, draw in zip(plan["experiments"], plan["draws"]):
        r = exp["recordIndex"]
        C = int(T[r][0][2])
        key = (exp["state"], exp["offset"], (draw["tileX"], draw["tileY"]))
        if exp["family"] == "one":
            one[key] = C
        else:
            basis_hw[key][int(exp["family"][5:])] = C

    meshes = {}
    ok_b = bad_b = 0
    rule_score = defaultdict(lambda: [0, 0])
    mism = []
    for (state, tri, tile), hw_c in sorted(one.items()):
        if state not in meshes:
            meshes[state] = rg.load_mesh(state)
        verts, tris = meshes[state]
        pos = [verts[v] for v in tris[tri]]
        tx, ty = tile
        vals = [rg.chain_value(pos, [ONE if i == b else 0 for i in range(3)],
                               tx, ty) for b in range(3)]
        hwb = basis_hw.get((state, tri, tile))
        if hwb:
            for b in range(3):
                w_model = rne24_word_frac(vals[b])
                if w_model == hwb[b]:
                    ok_b += 1
                else:
                    bad_b += 1
                    if len(mism) < 12:
                        mism.append((state, tri, tile, b,
                                     f"{w_model:08x}", f"{hwb[b]:08x}"))
        total = sum(vals)
        for name, word in (("sum-rne24", rne24_word_frac(total)),
                           ("sum-rtz24", rtz24_word(total))):
            sc = rule_score[name]
            sc[1] += 1
            sc[0] += word == hw_c
    print(f"basis words: model=hw {ok_b}, mismatch {bad_b}")
    for r in mism:
        print("  basis mismatch:", r)
    for name, (ok, tot) in rule_score.items():
        print(f"one-plane rule {name}: {ok}/{tot}")


if __name__ == "__main__":
    main()
