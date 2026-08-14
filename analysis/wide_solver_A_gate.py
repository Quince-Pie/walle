#!/usr/bin/env python3
"""Which frame keeps tt3 exact under a fixed-position injection?

Track A needs to know where a segmented multiplier may truncate without
disturbing tt3.  Two candidate frames:

  normalised  N = dm * (odd(d_o) << (24 - bl(odd)))   -- tz >= 18 on tt3
  raw         R = dm * disp   (disp = subpixel displacement)

For each, inject a constant at a fixed absolute column and at a column
that floats with the product, and measure tt3 directly instead of
assuming it.
"""

from __future__ import annotations

import sys

import numpy as np

sys.path[:0] = ["/tmp/walle/analysis"]

from wide_solver_A_frame import ARR, NAMES, score_all  # noqa: E402


def raw(a):
    """Raw subpixel product R = dm * disp, and its trailing-zero count."""
    return a["P48"] >> (a["tz24"] - a["subtz"]) if False else None


def main() -> None:
    for n in NAMES:
        a = ARR[n]
        # R = P * 2^subtz ; P = P48 >> fs
        p = a["P48"] >> a["fs"]
        r = p << a["subtz"]
        print(f"{n}: bl(P) {int((np.log2(p.astype(float)) + 1).min())}.."
              f"{int((np.log2(p.astype(float)) + 1).max())}  "
              f"subtz {sorted(set(a['subtz'].tolist()))}  "
              f"min trailing zeros of R = {int(a['subtz'].min())}")
    print()

    def absolute(frame: str, t: int, k: int):
        def law(a):
            p = a["P48"] >> a["fs"]
            base = (p << a["subtz"]) if frame == "raw" else a["P48"]
            v = ((base + (k << t)) >> t) << t
            return v << a["fs"] >> a["subtz"] if frame == "raw" else v
        return law

    print("absolute-column injection into the RAW subpixel product R:")
    print("   T   K    tt4    tt3    tt1")
    for t in range(4, 16):
        for k in (1, 9):
            law = absolute("raw", t, k)
            s = score_all(law)
            flag = "  <== tt3 intact" if s[1] == 18001 else ""
            if s[1] >= 17800 or k == 9:
                print(f"  {t:2d} {k:3d} {s[0]:6d} {s[1]:6d} {s[2]:6d}{flag}")


if __name__ == "__main__":
    main()
