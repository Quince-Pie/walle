#!/usr/bin/env python3
"""Loader for the new tz-class captures (Track B), plus scale calibration.

`wide_solver_data.py` is shared and read-only, so the new datasets live here.
Each capture clones tt4's geometry and moves only the anchor's subpixel
position, so the value scale is predicted to be `tz - 20` (tt3: 13-20 = -7,
tt4: 6-20 = -14, both confirmed).  `calibrate()` verifies that prediction
against the capture instead of trusting it: the correct scale is the one
where the narrow law explains the narrow (bl(P) <= 27) cells, and a wrong
scale misses essentially all of them.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path[:0] = ["/tmp/walle", "/tmp/walle/analysis"]

import wide_solver_data as W  # noqa: E402
import _sweep_fused_join_lattice as m  # noqa: E402

ROOT = Path("/tmp/walle/build/analysis-agx-basis")

# tz -> (directory, anchor subpixels)
TZ_SETS = {
    3: ("c-tzclass3-plan-v1", 131064),
    4: ("c-tzclass4-plan-v1", 131056),
    5: ("c-tzclass5-plan-v1", 131040),
    8: ("c-tzclass8-plan-v1", 130816),
    9: ("c-tzclass9-plan-v1", 130560),
}


def load_tz(tz: int, scale: int | None = None):
    """Return [(dm, e, d_o, sign, C_word)] like wide_solver_data.load."""
    directory, ay = TZ_SETS[tz]
    if scale is None:
        scale = tz - 20
    d = ROOT / directory
    plan = json.load(open(d / "reveal-agx-setup-accumulator-plan.json"))
    triples = m.load_records(d / "capture.raw", len(plan["draws"]))
    out = []
    for exp, draw in zip(plan["experiments"], plan["draws"]):
        sign_w, dm, e_lsb = m.f32_parts(exp["word"])
        disp = draw["tileY"] * 8192 - ay
        assert disp % (1 << tz) == 0, (tz, disp)
        d_o = disp >> tz
        if d_o == 0:
            continue
        out.append((dm, e_lsb + scale, abs(d_o),
                    sign_w * (1 if d_o > 0 else -1),
                    int(triples[exp["recordIndex"]][0][2])))
    return out


def score(rows, law, z: int):
    hits = 0
    for dm, e, d_o, sign, c_word in rows:
        try:
            mant, k = law(dm, d_o, z)
            pred = W.f32_from_int(sign, mant, e + k)
        except (ValueError, ZeroDivisionError):
            pred = None
        hits += pred == c_word
    return hits, len(rows)


def calibrate(tz: int) -> int:
    """Find the value scale by testing which one explains the narrow cells."""
    best = None
    for scale in range(tz - 26, tz - 4):
        rows = load_tz(tz, scale)
        # prefer the narrowest products available in this capture; a wrong
        # scale misses essentially all of them, so the peak is unambiguous
        floor = min((r[0] * r[2]).bit_length() for r in rows)
        probe = [r for r in rows if (r[0] * r[2]).bit_length() <= floor + 2]
        hits, total = score(probe, lambda dm, d_o, z: W.narrow(dm * d_o), tz)
        if best is None or hits > best[1]:
            best = (scale, hits, total)
    return best


def main() -> None:
    for tz in sorted(TZ_SETS):
        if not (ROOT / TZ_SETS[tz][0] / "capture.raw").exists():
            continue
        scale, hits, total = calibrate(tz)
        rows = load_tz(tz, scale)
        allh, alln = score(rows, lambda dm, d_o, z: W.narrow(dm * d_o), tz)
        bls = [(r[0] * r[2]).bit_length() for r in rows]
        print(f"tz={tz}: calibrated scale {scale} "
              f"(predicted {tz - 20}); narrow-cell fit {hits}/{total}; "
              f"narrow law overall {allh}/{alln}; "
              f"bl(P) {min(bls)}..{max(bls)}")


if __name__ == "__main__":
    main()
