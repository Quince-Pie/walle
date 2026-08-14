import sys, pickle
sys.path[:0] = ["/tmp/walle"]
from fractions import Fraction
from pathlib import Path
import numpy as np
import _sweep_fused_join_lattice as m
f32 = np.float32
TABLE = Path("/tmp/walle/parity/raster_p25_selector_ceil_bits.bin").read_bytes()
P25 = 1 << 49

def sel_of_key(key):
    if key == (1 << 25) or key == (1 << 24): return 1 << 24
    bi = key - (1 << 24)
    ceil = (TABLE[bi >> 3] >> (bi & 7)) & 1
    q, r = divmod(P25, key)
    return q + (1 if (ceil and r) else 0)

def rcp_p25(den):
    """reciprocal via the p25 table on the 25-bit normalized denominator."""
    d = Fraction(den)
    # normalize denominator to [2^24, 2^25)
    e = 0
    num, dd = d.numerator, d.denominator
    key = Fraction(num, dd)
    while key >= (1 << 25): key /= 2; e += 1
    while key < (1 << 24): key *= 2; e -= 1
    # round key to integer (RNE)
    ki = int(key); fr = key - ki
    if fr > Fraction(1,2) or (fr == Fraction(1,2) and ki & 1): ki += 1
    sel = sel_of_key(ki)
    return Fraction(sel, 1 << 49) / Fraction(2)**e

d = pickle.load(open("/tmp/walle/build/_ruler_v6_thw.pkl","rb"))
cands = {
    "exact": lambda n, den: Fraction(n) / den,
    "f32div": lambda n, den: Fraction(float(f32(n / float(den)))),
    "rcp_p25": lambda n, den: Fraction(n) * rcp_p25(den),
    "f32(n*rcp_p25)": lambda n, den: Fraction(float(f32(float(Fraction(n) * rcp_p25(den))))),
}
counts = {k: 0 for k in cands}
total = 0
misses = {k: [] for k in cands}
for g, ent in sorted(d.items()):
    lo, hi = ent.get("lo"), ent.get("hi")
    if lo is None or hi is None: continue
    total += 1
    n, den = ent["num"], ent["den"]
    for k, fn in cands.items():
        t = fn(n, abs(den))
        if lo <= t <= hi: counts[k] += 1
        elif len(misses[k]) < 3:
            misses[k].append((g, float(lo), float(t), float(hi)))
print("total geometries with intervals:", total)
for k, c in counts.items():
    print(f"  {k}: {c}/{total}")
    for mi in misses[k][:2]: print("    miss:", mi)
