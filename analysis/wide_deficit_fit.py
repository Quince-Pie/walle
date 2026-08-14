#!/usr/bin/env python3
"""Fit deficit families D(dm, d_o) against exact preimage windows.

Cell passes iff lo <= D < hi (window = exact preimage of the captured
word under the narrow chain), so the membership count IS the gate score
for wide cells.  t = dm - 2^23 (dm's low part; t=0 cells are exact on
hw).  Families:
  trunc:  D = -((t*d_o) mod 2^L) + c*2^(L-1)          (c in 0,1,2)
  rnd:    D = rna(t*d_o at L) - t*d_o                  (round-half-away)
  dlow:   D = -t*(d_o mod 2^s) + c*2^(s-1)
  wtrunc: D = -(P mod 2^L) + c*2^(L-1)                 (whole product)
  reltr:  as trunc but L = bl(P) - W                   (msb-relative)
  ppsum:  D = -sum_i in bits(d_o) ((t<<i) mod 2^L) + c*2^(L-1)
"""
from __future__ import annotations

import sys
from collections import defaultdict

sys.path[:0] = ["/tmp/walle", "/tmp/walle/analysis"]


def load_windows(paths):
    cells = []
    for p in paths:
        for line in open(p):
            r = line.split()
            if r[4] == "?":
                continue
            cells.append((int(r[2], 16), int(r[1]), int(r[3]),
                          int(r[5]), int(r[6])))
    return cells


def rna_deficit(x: int, L: int) -> int:
    if L <= 0:
        return 0
    low = x & ((1 << L) - 1)
    return (1 << L) - low if low >= (1 << (L - 1)) else -low


def main() -> None:
    S = ("/tmp/nix-shell.PFgUGF/claude-1000/-tmp-walle/"
         "4ccfbce8-33b2-4b5f-8e29-93486397c8a4/scratchpad")
    cells = load_windows([S + "/tt4_exact.txt", S + "/tt1_exact.txt"])
    total = len(cells)
    print(f"{total} wide cells")
    results = []

    def score(name, fn):
        ok = 0
        for dm, d_o, bl, lo, hi in cells:
            d = fn(dm, d_o, bl)
            if lo <= d < hi:
                ok += 1
        results.append((ok, name))

    for L in range(6, 17):
        for c in (0, 1, 2):
            score(f"trunc L{L} c{c}",
                  lambda dm, d_o, bl, L=L, c=c:
                  -(((dm - (1 << 23)) * d_o) & ((1 << L) - 1))
                  + (c << (L - 1)))
        score(f"rnd L{L}",
              lambda dm, d_o, bl, L=L:
              rna_deficit((dm - (1 << 23)) * d_o, L))
        for c in (0, 1):
            score(f"wtrunc L{L} c{c}",
                  lambda dm, d_o, bl, L=L, c=c:
                  -((dm * d_o) & ((1 << L) - 1)) + (c << (L - 1)))
            score(f"ppsum L{L} c{c}",
                  lambda dm, d_o, bl, L=L, c=c:
                  -sum(((dm - (1 << 23)) << i) & ((1 << L) - 1)
                       for i in range(d_o.bit_length()) if (d_o >> i) & 1)
                  + (c << (L - 1)))
    for s in range(4, 13):
        for c in (0, 1):
            score(f"dlow s{s} c{c}",
                  lambda dm, d_o, bl, s=s, c=c:
                  -(dm - (1 << 23)) * (d_o & ((1 << s) - 1))
                  + (c << (s - 1)))
    for Wd in range(22, 31):
        for c in (0, 1, 2):
            score(f"reltr W{Wd} c{c}",
                  lambda dm, d_o, bl, Wd=Wd, c=c:
                  -(((dm - (1 << 23)) * d_o) & ((1 << max(0, bl - Wd)) - 1))
                  + ((c << (bl - Wd - 1)) if bl - Wd >= 1 else 0))
            score(f"relw W{Wd} c{c}",
                  lambda dm, d_o, bl, Wd=Wd, c=c:
                  -((dm * d_o) & ((1 << max(0, bl - Wd)) - 1))
                  + ((c << (bl - Wd - 1)) if bl - Wd >= 1 else 0))
        score(f"relrnd W{Wd}",
              lambda dm, d_o, bl, Wd=Wd:
              rna_deficit(dm * d_o, bl - Wd))
    score("zero", lambda dm, d_o, bl: 0)
    results.sort(reverse=True)
    for ok, name in results[:30]:
        print(f"{ok:6d}/{total} {100.0*ok/total:6.2f}%  {name}")


if __name__ == "__main__":
    main()
