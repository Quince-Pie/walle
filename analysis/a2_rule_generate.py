#!/usr/bin/env python3
"""Input-only generation of A2 deficit tiles (task #4 rule).

Rule (validated against a2-transfer-residue-plan-v1, 22/22 tiles):
for each transfer triangle, compute the three per-vertex BASIS planes
(value 1 at one vertex, 0 at the others) through the banked setup
chain per 32x32 tile; join the two non-anchor basis constants with
f32 round-toward-zero, add the anchor basis with f32 round-to-nearest;
a tile whose joined constant is below 1.0 (word != 0x3F800000) is a
deficit tile: the secondary factor there is f16-RTZ(constant) = 0x3BFF.

Meshes come from the A2 geometry traces (Apple's captured transfer
draw inputs) - positions only; varyings are constant 1.0.
"""
from __future__ import annotations

import json
import struct
import sys
from fractions import Fraction
from pathlib import Path

sys.path[:0] = ["/tmp/walle", "/tmp/walle/analysis"]

import _sweep_fused_join_lattice as m  # noqa: E402
import _fit_child_tiles as ft  # noqa: E402
from hunt_c_walk_seed import build28, rne24_word_frac  # noqa: E402
import score_c_chain_dense as sc  # noqa: E402

F2 = Fraction(2)
ONE = 0x3F800000
TRACE = Path("/tmp/walle-analysis/A2-geometry-sweep-v74")


def load_mesh(state: int):
    tr = json.load(open(TRACE / f"state-{state}" /
                        "reveal-mask-trace.json"))["nativeScale"]["A2Geometry"]
    vs = bytes.fromhex(tr["vertexStreamHex"])
    verts = []
    for i in range(tr["vertexCount"]):
        x, y = struct.unpack_from("<II", vs, i * 48)
        verts.append((x, y))
    idx = tr["indices"]
    tris = [tuple(idx[i:i + 3]) for i in range(0, len(idx), 3)]
    return verts, tris


def rtz_frac(x: Fraction) -> Fraction:
    if x == 0:
        return Fraction(0)
    s = -1 if x < 0 else 1
    ax = abs(x)
    e = 0
    while ax >= 2:
        ax /= 2
        e += 1
    while ax < 1:
        ax *= 2
        e -= 1
    return Fraction(s * int(ax * (1 << 23)), 1 << 23) * F2 ** e


def add_rne(a: Fraction, b: Fraction) -> Fraction:
    return Fraction(struct.unpack("<f", struct.pack("<f",
                                                    float(a) + float(b)))[0])


def chain_value(tri_pos, vwords, tx, ty, knobs=("mid", 10, 27, 20)):
    order, MIDC, MW, SELC = knobs
    verts = [[px, py, w, w] for (px, py), w in zip(tri_pos, vwords)]
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
        return (Fraction(asign * amant) * F2 ** aexp) if amant else Fraction(0)
    gmin = min(e for _, e in parts)
    tot = sum(val << (e - gmin) for val, e in parts)
    if amant:
        gm2 = min(gmin, aexp)
        tot = (tot << (gmin - gm2)) + asign * amant * (1 << (aexp - gm2))
        gmin = gm2
    s28, m28, e28 = ft.norm(tot, gmin, 28, "rne")
    return Fraction(s28 * m28) * F2 ** e28


def anchor_of(tri_pos):
    verts = [[px, py, ONE, ONE] for (px, py) in tri_pos]
    return build28(verts, [ONE, ONE, ONE])[4]


def tile_word(tri_pos, tx, ty) -> int:
    an = anchor_of(tri_pos)
    others = [i for i in range(3) if i != an]
    basis = [chain_value(tri_pos, [ONE if i == v else 0 for i in range(3)],
                         tx, ty) for v in range(3)]
    s1 = rtz_frac(basis[others[0]] + basis[others[1]])
    s2 = add_rne(s1, basis[an])
    return struct.unpack("<I", struct.pack("<f", float(s2)))[0]


def fixed_px(word: int) -> float:
    return m.bits_f32(word)


def deficit_tiles(state: int, width_tiles=64, height_tiles=64):
    verts, tris = load_mesh(state)
    out = []
    for tindex, tri in enumerate(tris):
        pos = [verts[v] for v in tri]
        xs = [fixed_px(p[0]) for p in pos]
        ys = [fixed_px(p[1]) for p in pos]
        if len({(round(x * 4), round(y * 4)) for x, y in zip(xs, ys)}) < 3:
            continue
        tx0 = max(0, int(min(xs)) // 32)
        tx1 = min(width_tiles - 1, int(max(xs)) // 32)
        ty0 = max(0, int(min(ys)) // 32)
        ty1 = min(height_tiles - 1, int(max(ys)) // 32)
        if tx0 > tx1 or ty0 > ty1:
            continue
        for ty in range(ty0, ty1 + 1):
            for tx in range(tx0, tx1 + 1):
                w = tile_word(pos, tx, ty)
                if w != ONE:
                    out.append((tindex, tx, ty, w))
    return out


def main() -> None:
    states = [int(a) for a in sys.argv[1:]] or [42]
    for state in states:
        tiles = deficit_tiles(state)
        print(f"state {state}: "
              + " ".join(f"tri{t}({x},{y})={w:08x}" for t, x, y, w in tiles))


if __name__ == "__main__":
    main()
