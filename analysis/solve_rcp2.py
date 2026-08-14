import pickle, sys, math
sys.path[:0] = ["/tmp/walle"]
from fractions import Fraction
from collections import defaultdict
d = pickle.load(open("/tmp/walle/build/_ruler_v6_thw_wide.pkl","rb"))
win = {}
for g, e in sorted(d.items()):
    if not (e["consistent"] and e["rows"] > 0): continue
    t, lo, hi = e["t_exact"], e["lo"], e["hi"]
    den = abs(e["den"])
    num = t * den
    assert num.denominator == 1 or True
    lo_r, hi_r = lo / num, hi / num
    if den in win:
        plo, phi = win[den]
        win[den] = (max(plo, lo_r), min(phi, hi_r))
    else:
        win[den] = (lo_r, hi_r)
bad = [den for den, (lo, hi) in win.items() if lo > hi]
print(f"{len(win)} dens, {len(bad)} inconsistent")
usable = {den: w for den, w in win.items() if w[0] <= w[1]}

def quant(v, W, mode):
    e = v.numerator.bit_length() - v.denominator.bit_length()
    if v < Fraction(2)**e: e -= 1
    step = Fraction(2)**(e - W + 1)
    q = v / step
    fl = q.numerator // q.denominator
    fr = q - fl
    if mode == "flo": mant = fl
    elif mode == "up": mant = fl + (1 if fr > 0 else 0)
    elif mode == "rne": mant = fl + (1 if (fr > Fraction(1,2) or (fr == Fraction(1,2) and fl % 2)) else 0)
    elif mode == "hup": mant = fl + (1 if fr >= Fraction(1,2) else 0)
    else: raise ValueError
    return mant * step

best = []
for W in range(22, 32):
    for mode in ("flo", "up", "rne", "hup"):
        ok = 0
        for den, (lo, hi) in usable.items():
            r = quant(Fraction(1, int(den)), W, mode)
            if lo <= r <= hi: ok += 1
        best.append((ok, W, mode))
best.sort(reverse=True)
for b in best[:6]: print("rcp quant:", b, "of", len(usable))
# Newton: seed W0-bit table (round-to-nearest), one NR step in W1-bit arithmetic
def newton(den, W0, W1):
    x = quant(Fraction(1, int(den)), W0, "rne")
    # NR: x2 = x*(2 - den*x), products quantized to W1
    p = quant(Fraction(int(den)) * x, W1, "rne")
    x2 = quant(x * (2 - p), W1, "rne")
    return x2
res = []
for W0 in (8, 10, 12, 14, 16):
    for W1 in (24, 25, 26, 27, 28):
        ok = 0
        for den, (lo, hi) in usable.items():
            r = newton(den, W0, W1)
            if lo <= r <= hi: ok += 1
        res.append((ok, W0, W1))
res.sort(reverse=True)
for r in res[:6]: print("newton:", r, "of", len(usable))
