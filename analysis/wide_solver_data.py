#!/usr/bin/env python3
"""Shared loader + exact scorer for the wide-path C-product law hunt.

Every observation reduces to: integer product P = dm * d_o (dm = 24-bit
mantissa of the probe f32 word, d_o = odd displacement), plus a pure
power-of-two exponent.  The hardware exports a 24-bit f32 mantissa word.
A candidate law is a function law(dm, d_o) -> (M, k) meaning the value
M * 2^k, with M a nonnegative integer (normalised to 24 bits by the
encoder).  Scoring compares the encoded word against the captured word.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path[:0] = ["/tmp/walle", "/tmp/walle/analysis"]

import _sweep_fused_join_lattice as m  # noqa: E402

ROOT = Path("/tmp/walle/build/analysis-agx-basis")

# name -> (directory, anchor-y subpixels, shift to odd d_o, value scale exp)
DATASETS = {
    "tt3": ("c-truthtable3-plan-v1", 131072, 13, -7),
    "tt4": ("c-truthtable4-plan-v1", 131008, 6, -14),
    "tt1": ("c-truthtable-plan-v1", 157312, 7, -12),
}


def f32_from_int(sign: int, mant: int, exp: int) -> int:
    """Encode sign * mant * 2^exp as an f32 word (mant <= 24 bits)."""
    if mant == 0:
        return 0
    bl = mant.bit_length()
    if bl < 24:
        mant <<= 24 - bl
        exp -= 24 - bl
    elif bl > 24:
        raise ValueError(f"mantissa too wide: {mant:x}")
    e = exp + 23 + 127
    if not 1 <= e <= 254:
        raise ValueError(f"exponent out of range: {e}")
    return ((1 << 31) if sign < 0 else 0) | (e << 23) | (mant & 0x7FFFFF)


def load(name: str):
    """Return list of (dm, e_lsb, d_o, sign, C_word) observations."""
    directory, ay, shift, scale = DATASETS[name]
    d = ROOT / directory
    plan = json.load(open(d / "reveal-agx-setup-accumulator-plan.json"))
    triples = m.load_records(d / "capture.raw", len(plan["draws"]))
    out = []
    for exp, draw in zip(plan["experiments"], plan["draws"]):
        word = exp["word"]
        sign_w, dm, e_lsb = m.f32_parts(word)
        disp = draw["tileY"] * 8192 - ay
        assert disp % (1 << shift) == 0, (name, disp)
        d_o = disp >> shift
        if d_o == 0:
            continue
        sign = sign_w * (1 if d_o > 0 else -1)
        c_word = int(triples[exp["recordIndex"]][0][2])
        out.append((dm, e_lsb + scale, abs(d_o), sign, c_word))
    return out


def score(obs, law) -> tuple[int, int, list]:
    """Score a law over observations; returns (hits, total, misses)."""
    hits = 0
    misses = []
    for dm, e, d_o, sign, c_word in obs:
        try:
            mant, k = law(dm, d_o)
            pred = f32_from_int(sign, mant, e + k)
        except (ValueError, ZeroDivisionError):
            pred = None
        if pred == c_word:
            hits += 1
        else:
            misses.append((dm, d_o, e, sign, c_word, pred))
    return hits, len(obs), misses


# ---- rounding primitives (exact integer arithmetic; no float, no round())


def rna(value: int, width: int) -> tuple[int, int]:
    """Round to `width` significant bits, ties away from zero."""
    sh = value.bit_length() - width
    if sh <= 0:
        return value, 0
    base = value >> sh
    if value & (1 << (sh - 1)):
        base += 1
        if base.bit_length() > width:
            base >>= 1
            sh += 1
    return base, sh


def rne(value: int, width: int) -> tuple[int, int]:
    """Round to `width` significant bits, ties to even."""
    sh = value.bit_length() - width
    if sh <= 0:
        return value, 0
    base = value >> sh
    rem = value & ((1 << sh) - 1)
    half = 1 << (sh - 1)
    if rem > half or (rem == half and (base & 1)):
        base += 1
        if base.bit_length() > width:
            base >>= 1
            sh += 1
    return base, sh


def rtz(value: int, width: int) -> tuple[int, int]:
    sh = value.bit_length() - width
    if sh <= 0:
        return value, 0
    return value >> sh, sh


def rup(value: int, width: int) -> tuple[int, int]:
    """Round away from zero (ceiling on magnitude)."""
    sh = value.bit_length() - width
    if sh <= 0:
        return value, 0
    base = value >> sh
    if value & ((1 << sh) - 1):
        base += 1
        if base.bit_length() > width:
            base >>= 1
            sh += 1
    return base, sh


def rodd(value: int, width: int) -> tuple[int, int]:
    """Round to odd (sticky) at `width` bits."""
    sh = value.bit_length() - width
    if sh <= 0:
        return value, 0
    base = value >> sh
    if value & ((1 << sh) - 1):
        base |= 1
    return base, sh


MODES = {"rna": rna, "rne": rne, "rtz": rtz, "rup": rup, "rodd": rodd}


def narrow(P: int) -> tuple[int, int]:
    """The PROVEN narrow law: RNE24(rna27(P))."""
    m1, s1 = rna(P, 27)
    m2, s2 = rne(m1, 24)
    return m2, s1 + s2


if __name__ == "__main__":
    for name in ("tt3", "tt4", "tt1"):
        obs = load(name)
        hits, total, _ = score(obs, lambda dm, d_o: narrow(dm * d_o))
        widths = {}
        for dm, e, d_o, sign, c in obs:
            widths[(dm * d_o).bit_length()] = \
                widths.get((dm * d_o).bit_length(), 0) + 1
        print(f"{name}: narrow law {hits}/{total}  "
              f"bl range {min(widths)}..{max(widths)}")
