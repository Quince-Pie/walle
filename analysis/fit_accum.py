import sys, pickle
sys.path[:0] = ["/tmp/walle"]
from pathlib import Path
from fractions import Fraction
import numpy as np
import _sweep_fused_join_lattice as m

SC = ("/tmp/nix-shell.PFgUGF/claude-1000/-tmp-walle/"
      "4ccfbce8-33b2-4b5f-8e29-93486397c8a4/scratchpad")
def load_sdf(state):
    out = {}
    for line in Path(f"{SC}/childgeo_all_residual_states.txt").read_text().splitlines():
        if "CHILDSDF" not in line: continue
        t = line[line.index("CHILDSDF"):].split()
        if int(t[1]) != state: continue
        out[int(t[2])] = [[int(x,16) for x in t[3+4*v:7+4*v]] for v in range(3)]
    return out
def f32v(w): return m.bits_f32(w)
def fr_bits(word):
    if word == 0 or (word & 0x7fffffff) == 0: return Fraction(0)
    s, mant, e = m.f32_parts(word)
    return Fraction(s*mant) * Fraction(2)**e
def rne_f32(v):
    if v == 0: return 0
    neg = v < 0; av = abs(v); e = 0
    while av >= 2: av/=2; e+=1
    while av < 1: av*=2; e-=1
    scaled = av * 2**23
    mant = int(scaled); frac = scaled - mant
    if frac > Fraction(1,2) or (frac == Fraction(1,2) and mant & 1): mant += 1
    if mant >= 2**24: mant //= 2; e += 1
    return ((e+127)<<23) | (mant & 0x7fffff) | (0x80000000 if neg else 0)

basis = pickle.load(open("/tmp/walle/build/_rcb_obs.pkl","rb"))
dense = pickle.load(open("/tmp/walle/build/_rcd_obs.pkl","rb"))
def perturb(w, k):
    if w == 0: return w
    return m.key_to_bits(m.ordered_key(w) + k)

def acc_fma(vals, gs, order):
    idx = list(range(3))
    if order == "rev": idx = idx[::-1]
    acc = Fraction(0); started = False
    for i in idx:
        p = vals[i] * gs[i]
        if not started:
            acc = fr_bits(rne_f32(p)); started = True
        else:
            acc = fr_bits(rne_f32(p + acc))
    return rne_f32(acc) if isinstance(acc, Fraction) else acc

def acc_exact(vals, gs, order):
    return rne_f32(sum(v*g for v, g in zip(vals, gs)))

# --- Part 1: ctx3 (1,1,1) sum check on slopes ---
for name, fn in (("fma_fwd", lambda v,g: acc_fma(v,g,"fwd")),
                 ("fma_rev", lambda v,g: acc_fma(v,g,"rev")),
                 ("exact",   lambda v,g: acc_exact(v,g,"fwd"))):
    ok = tot = 0; ex = []
    for (st, od, ctx) in sorted(basis):
        if ctx != 0: continue
        g0 = basis[(st,od,0)]["AB"]; g1 = basis[(st,od,1)]["AB"]; g2 = basis[(st,od,2)]["AB"]
        hw3 = basis[(st,od,3)]["AB"]
        got = []
        for axis in range(2):
            gs = [fr_bits(g0[axis]), fr_bits(g1[axis]), fr_bits(g2[axis])]
            got.append(fn([Fraction(1)]*3, gs))
        tot += 1
        if tuple(got) == hw3: ok += 1
        elif len(ex) < 3:
            fmt=lambda w: format(w,'08x')
            ex.append(f"s{st}o{od}: hw=({fmt(hw3[0])},{fmt(hw3[1])}) got=({fmt(got[0])},{fmt(got[1])})")
    print(f"ctx3-sum {name}: {ok}/{tot}")
    for e in ex: print("   ", e)

# --- Part 2: production values from measured basis planes ---
for name, fn in (("fma_fwd", lambda v,g: acc_fma(v,g,"fwd")),
                 ("fma_rev", lambda v,g: acc_fma(v,g,"rev")),
                 ("exact",   lambda v,g: acc_exact(v,g,"fwd"))):
    ok = tot = 0; ex = []
    for (st, od, ctx), d in sorted(dense.items()):
        verts = load_sdf(st)[od]
        fixed = [(round(f32v(v[0])*256), round(f32v(v[1])*256)) for v in verts]
        an = min(range(3), key=lambda i:(fixed[i][1], fixed[i][0]))
        ch = ctx % 2
        vwords = []
        for vi in range(3):
            w = verts[vi][2+ch]
            if ctx >= 2 and vi == an: w = perturb(w, 1)
            vwords.append(w)
        vals = [fr_bits(w) for w in vwords]
        g0 = basis[(st,od,0)]["AB"]; g1 = basis[(st,od,1)]["AB"]; g2 = basis[(st,od,2)]["AB"]
        got = []
        for axis in range(2):
            gs = [fr_bits(g0[axis]), fr_bits(g1[axis]), fr_bits(g2[axis])]
            got.append(fn(vals, gs))
        tot += 1
        if tuple(got) == d["AB"]: ok += 1
        elif len(ex) < 3:
            fmt=lambda w: format(w,'08x')
            ex.append(f"s{st}o{od}c{ctx}: hw=({fmt(d['AB'][0])},{fmt(d['AB'][1])}) got=({fmt(got[0])},{fmt(got[1])})")
    print(f"production {name}: {ok}/{tot}")
    for e in ex: print("   ", e)
