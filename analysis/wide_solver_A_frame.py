#!/usr/bin/env python3
"""Track A: 48-bit frame harness for the segmented-multiplier track.

Everything here works in the NORMALISED FRAME rather than in P units,
because that is the frame in which tt3's exactness is structural instead
of a product-width gate (cross-track correction 1 from the lead):

    odd   = odd part of d_o          bl_odd = bit_length(odd)
    didx24 = odd << (24 - bl_odd)    (displacement normalised to 24 bits)
    P48    = dm * didx24             (the 48-bit product grid)

didx24 carries `TZ24 = 24 - bl_odd` trailing zeros, so every partial
product of P48 does too.  tt3 only ever probes bl_odd <= 6, hence
TZ24 >= 18 there: any truncation at a frame bit <= 17 drops no column of
tt3 at all, and tt3 stays exact with no bl(P) side condition.

A candidate returns V48; it reproduces a capture exactly iff V48 lands in
that capture's admissible frame interval, computed as the exact preimage
of the captured word under the proven narrow-law export.
"""

from __future__ import annotations

import pickle
import sys
from pathlib import Path

import numpy as np

sys.path[:0] = ["/tmp/walle", "/tmp/walle/analysis"]

import wide_solver_data as W  # noqa: E402
import wide_solver_xmap as XM  # noqa: E402

CACHE = Path("/tmp/walle/build/wide_solver_A_frame_v2.pkl")
NAMES = ("tt4", "tt3", "tt1")
TOTALS = {"tt4": 18001, "tt3": 18001, "tt1": 2610}

# subpixel trailing zeros contributed by each capture's own scaling
DATASET_TZ = {name: W.DATASETS[name][2] for name in NAMES}


def narrowval(v: int) -> int:
    mant, sh = W.narrow(v)
    return mant << sh


_PRE: dict[int, tuple[int, int]] = {}


def preimage(target: int) -> tuple[int, int]:
    """Inclusive [lo, hi] of integers V with narrowval(V) == target."""
    hit = _PRE.get(target)
    if hit is not None:
        return hit
    a, b = 1, 1 << 60
    while a < b:
        mid = (a + b) // 2
        if narrowval(mid) >= target:
            b = mid
        else:
            a = mid + 1
    lo = a
    a, b = 1, 1 << 60
    while a < b:
        mid = (a + b + 1) // 2
        if narrowval(mid) <= target:
            a = mid
        else:
            b = mid - 1
    hi = a
    res = (lo, hi) if narrowval(lo) == target == narrowval(hi) else (1, 0)
    _PRE[target] = res
    return res


def build() -> dict:
    out = {}
    for name in NAMES:
        shift = DATASET_TZ[name]
        rows = []
        for r in XM.observations(name):
            d_o = r["d_o"]
            tz = (d_o & -d_o).bit_length() - 1
            odd = d_o >> tz
            bl_odd = odd.bit_length()
            tz24 = 24 - bl_odd
            didx24 = odd << tz24
            fs = 24 - d_o.bit_length()          # P units -> frame units
            p48 = r["dm"] * didx24
            assert p48 == r["P"] << fs
            lo, hi = preimage(r["target"] << fs)
            # raw subpixel frame: R = dm * disp, disp = d_o << shift
            disp = d_o << shift
            rr = r["P"] << shift
            assert rr == r["dm"] * disp
            rlo, rhi = preimage(r["target"] << shift)
            rows.append((r["dm"], d_o, didx24, p48, lo, hi, tz24,
                         tz + shift, r["bl"], fs, disp, rr, rlo, rhi))
        out[name] = rows
    return out


def cells() -> dict:
    if CACHE.exists():
        return pickle.loads(CACHE.read_bytes())
    data = build()
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_bytes(pickle.dumps(data))
    return data


DATA = cells()
COLS = ("dm", "d_o", "didx24", "P48", "lo", "hi", "tz24", "subtz", "bl",
        "fs", "disp", "R", "rlo", "rhi")
ARR = {n: dict(zip(COLS, (np.array(c, dtype=np.int64)
                          for c in zip(*DATA[n])))) for n in NAMES}


def score(law, name: str, frame: str = "norm") -> int:
    """Count exact reproductions.  frame="norm" scores V in the 48-bit
    normalised grid, frame="raw" scores V in the raw subpixel grid."""
    a = ARR[name]
    v = law(a)
    lo, hi = (a["rlo"], a["rhi"]) if frame == "raw" else (a["lo"], a["hi"])
    return int(np.count_nonzero((v >= lo) & (v <= hi)))


def score_all(law, frame: str = "norm") -> tuple[int, int, int]:
    return tuple(score(law, n, frame) for n in NAMES)


def report(tag: str, law, frame: str = "norm") -> tuple[int, int, int]:
    s = score_all(law, frame)
    print(f"{tag:52s} tt4 {s[0]:5d}/18001  tt3 {s[1]:5d}/18001  "
          f"tt1 {s[2]:4d}/2610")
    return s


# ---- cheap early-rejection probes (lead's cross-track correction 3) ----

KILLER = (0x800C00, 1793)          # exact on grid, exported one granule LOW


def killer_index() -> int:
    a = ARR["tt4"]
    idx = np.nonzero((a["dm"] == KILLER[0]) & (a["d_o"] == KILLER[1]))[0]
    return int(idx[0])


def passes_killer(law, frame: str = "norm") -> bool:
    a = ARR["tt4"]
    i = killer_index()
    v = law(a)[i]
    lo, hi = (a["rlo"], a["rhi"]) if frame == "raw" else (a["lo"], a["hi"])
    return bool(lo[i] <= v <= hi[i])


if __name__ == "__main__":
    for n in NAMES:
        a = ARR[n]
        print(f"{n}: {len(a['dm'])} cells, tz24 range "
              f"{int(a['tz24'].min())}..{int(a['tz24'].max())}, "
              f"subpixel tz {sorted(set(a['subtz'].tolist()))[:6]}")
    report("narrow law (frame identity)", lambda a: a["P48"])
    i = killer_index()
    a = ARR["tt4"]
    print(f"\nkiller cell dm={KILLER[0]:06x} d_o={KILLER[1]}: "
          f"P48={int(a['P48'][i])}, admissible V48 offset "
          f"[{int(a['lo'][i] - a['P48'][i])}, "
          f"{int(a['hi'][i] - a['P48'][i])}], "
          f"tz24={int(a['tz24'][i])}, one granule = "
          f"{1 << (int(a['bl'][i]) - 24 + int(a['fs'][i]))}")
    print(f"narrow law passes killer cell: {passes_killer(lambda a: a['P48'])}")
