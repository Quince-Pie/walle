#!/usr/bin/env python3
"""Basis-form generation rule test for the A2 transfer plane (task #4).

Hardware (later-44) exports nonzero residue planes for constant-1.0
varyings, so setup is basis-form: each vertex's basis plane (value 1
at that vertex, 0 at the others) runs the banked product/join chain
individually and the results sum.  This script computes, per tile,
the three basis constants via the banked chain and their sum, and
compares against the measured words: tri 2 of state 42 must give
C = 3f7fffff on tile row 0 band tiles and 3f800000 above, with
B residue ~ 2e68b4e5.
"""
from __future__ import annotations

import struct
import sys
from fractions import Fraction

sys.path[:0] = ["/tmp/walle", "/tmp/walle/analysis"]

import _sweep_fused_join_lattice as m  # noqa: E402
import _fit_child_tiles as ft  # noqa: E402
from hunt_c_walk_seed import build28, rne24_word_frac  # noqa: E402
import score_c_chain_dense as sc  # noqa: E402

F2 = Fraction(2)
ONE = 0x3F800000

TRI2 = ((0x44F1A000, 0xC449A000),   # (1933, -806.5)
        (0x44F1A000, 0x4419A000),   # (1933, 614.5)
        (0x44000000, 0x4419A000))   # (512, 614.5)


def chain_value(vwords, tx, ty, knobs=("mid", 10, 27, 20)):
    order, MIDC, MW, SELC = knobs
    verts = [[px, py, w, w] for (px, py), w in zip(TRI2, vwords)]
    n28, sel, se, ds, an, fx = build28(verts, [v[2] for v in verts])
    aw = verts[an][2]
    asign, amant, aexp = m.f32_parts(aw) if aw else (0, 0, 0)
    parts = []
    for axis, tp in ((0, tx * 8192), (1, ty * 8192)):
        r_ = sc.c_word(order, n28[axis], sel, se, ds, fx[an][axis], tp,
                       MIDC, MW, SELC)
        if r_ is not None:
            parts.append(r_)
    if not parts:
        return Fraction(asign * amant) * F2 ** aexp if amant else Fraction(0)
    gmin = min(e for _, e in parts)
    tot = sum(val << (e - gmin) for val, e in parts)
    if amant:
        gm2 = min(gmin, aexp)
        tot = (tot << (gmin - gm2)) + asign * amant * (1 << (aexp - gm2))
        gmin = gm2
    s28, m28, e28 = ft.norm(tot, gmin, 28, "rne")
    return Fraction(s28 * m28) * F2 ** e28


def f32_add(a: float, b: float) -> float:
    return struct.unpack("<f", struct.pack("<f", a + b))[0]


def word_of(x: float) -> int:
    return struct.unpack("<I", struct.pack("<f", x))[0]


def main() -> None:
    for (tx, ty) in ((56, 0), (56, 1), (56, 2), (57, 0), (57, 3),
                     (58, 0), (48, 0), (56, 10), (40, 0)):
        basis = [chain_value([ONE if i == v else 0 for i in range(3)],
                             tx, ty) for v in range(3)]
        exact = sum(basis)
        w_exact = rne24_word_frac(exact)
        f = [float(b) for b in basis]
        w_seq01 = word_of(f32_add(f32_add(f[0], f[1]), f[2]))
        w_seq21 = word_of(f32_add(f32_add(f[2], f[1]), f[0]))
        print(f"tile({tx:2d},{ty:2d}): basis "
              f"{[hex(rne24_word_frac(b)) for b in basis]} "
              f"exact {w_exact:#010x} seq01 {w_seq01:#010x} "
              f"seq21 {w_seq21:#010x}")


if __name__ == "__main__":
    main()
