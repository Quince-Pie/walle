import sys, pickle
sys.path[:0] = ["/tmp/walle"]
from fractions import Fraction
from collections import defaultdict
d = pickle.load(open("/tmp/walle/build/_ruler_v6_thw.pkl","rb"))
win = defaultdict(lambda: [Fraction(0), Fraction(10)])
for g, ent in d.items():
    lo, hi = ent.get("lo"), ent.get("hi")
    if lo is None or hi is None: continue
    n, den = ent["num"], abs(ent["den"])
    w = win[den]
    w[0] = max(w[0], lo / n)
    w[1] = min(w[1], hi / n)
print(f"{len(win)} distinct denominators")
bad = [den for den, (lo, hi) in win.items() if lo > hi]
print("inconsistent windows:", len(bad))
# grid detection: find smallest W such that some multiple of 2^-W lies in every window
import math
for W in range(24, 42):
    ok = 0
    for den, (lo, hi) in win.items():
        if lo > hi: continue
        # rcp ~ 1/den: exponent scale e = floor(log2(1/den)); grid = 2^(e-W+1)
        e = -(den.numerator.bit_length() - den.denominator.bit_length()) if isinstance(den, Fraction) else -(int(den).bit_length())
        step = Fraction(2)**(e - W + 1)
        k_lo = math.ceil(lo / step)
        k_hi = math.floor(hi / step)
        if k_lo <= k_hi: ok += 1
    print(f"W={W}: windows admitting a 2^-grid value: {ok}/{len(win)-len(bad)}")
