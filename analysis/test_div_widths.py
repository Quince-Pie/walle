import sys, pickle, math
sys.path[:0] = ["/tmp/walle"]
from fractions import Fraction
d = pickle.load(open("/tmp/walle/build/_ruler_v6_thw.pkl","rb"))
rows = []
for g, ent in d.items():
    lo, hi = ent.get("lo"), ent.get("hi")
    if lo is None or hi is None or lo > hi: continue
    rows.append((ent["num"], abs(ent["den"]), lo, hi, ent.get("bad", "?")))
print(len(rows), "usable intervals")

def quant(t, W, mode):
    if t == 0: return t
    e = t.numerator.bit_length() - t.denominator.bit_length()
    if t < Fraction(2)**e: e -= 1
    step = Fraction(2)**(e - W + 1)
    q = t / step
    fl = q.numerator // q.denominator
    fr = q - fl
    if mode == "up": m_ = fl + (1 if fr > 0 else 0)
    elif mode == "flo": m_ = fl
    elif mode == "rne": m_ = fl + (1 if (fr > Fraction(1,2) or (fr == Fraction(1,2) and fl % 2)) else 0)
    elif mode == "hup": m_ = fl + (1 if fr >= Fraction(1,2) else 0)
    else: raise ValueError
    return m_ * step

for W in range(24, 34):
    for mode in ("up", "rne", "hup", "flo"):
        ok = 0
        for n, den, lo, hi, _ in rows:
            t = quant(Fraction(n) / den, W, mode)
            if lo <= t <= hi: ok += 1
        if ok > len(rows)*0.5:
            print(f"W={W} {mode}: {ok}/{len(rows)}")
