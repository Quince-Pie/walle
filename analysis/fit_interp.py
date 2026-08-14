import sys, json, struct
from pathlib import Path
from fractions import Fraction
sys.path[:0] = ["/tmp/walle"]
import numpy as np
import _sweep_fused_join_lattice as m
import _solve_clip_varyings as sv
import _fit_child_tiles as ft
import _joint_stage_sweep as js

S = Path("/tmp/nix-shell.PFgUGF/claude-1000/-tmp-walle/4ccfbce8-33b2-4b5f-8e29-93486397c8a4/scratchpad")
D = Path("build/analysis-agx-basis/residual-value-plan-v1")

def f32v(w): return m.bits_f32(w)

def fr_bits(word):
    if word & 0x7fffffff == 0: return Fraction(0)
    s, mant, e = m.f32_parts(word)
    return Fraction(s*mant) * Fraction(2)**e

def rne_f32(v):
    if v == 0: return 0
    neg = v < 0; av = abs(v); e = 0
    while av >= 2: av/=2; e+=1
    while av < 1: av*=2; e-=1
    scaled = av * 2**23
    mant = int(scaled); fracp = scaled - mant
    if fracp > Fraction(1,2) or (fracp == Fraction(1,2) and mant & 1): mant += 1
    if mant >= 2**24: mant //= 2; e += 1
    return ((e+127)<<23) | (mant & 0x7fffff) | (0x80000000 if neg else 0)

def rtz_f32(v):
    if v == 0: return 0
    neg = v < 0; av = abs(v); e = 0
    while av >= 2: av/=2; e+=1
    while av < 1: av*=2; e-=1
    mant = int(av * 2**23)
    return ((e+127)<<23) | (mant & 0x7fffff) | (0x80000000 if neg else 0)

def fma32(a, b, c, rnd):   # a,b,c Fractions; one rounding
    return rnd(a*b + c)

# ---- law internals (from the validated transcript implementation) ----
def sel_oa(mand, me, sel, se):
    prod = mand*sel; bits = prod.bit_length(); shift = bits-27
    if bits <= 32:
        if shift <= 0: return prod, me+se
        idx = (prod + (1 << (shift-1))) >> shift
        if idx.bit_length() > 27: idx >>= 1; shift += 1
        return idx, me+se+shift
    T = max(0, mand.bit_length() - 8)
    return (js.pps(mand, sel, T) + (20 << T)) >> shift, me+se+shift

def tap_rne(mag, gm, W):
    sh = mag.bit_length() - W
    if sh > 0:
        low = mag & ((1 << sh) - 1); half = 1 << (sh - 1)
        base = mag >> sh
        up = low > half or (low == half and base & 1)
        mag = base + (1 if up else 0); gm += sh
        if mag.bit_length() > W: mag >>= 1; gm += 1
    return mag, gm

def build28(verts, ch, vwords):
    fixed = [(round(f32v(v[0])*256), round(f32v(v[1])*256)) for v in verts]
    det = ((fixed[1][0]-fixed[0][0])*(fixed[2][1]-fixed[0][1])
           - (fixed[1][1]-fixed[0][1])*(fixed[2][0]-fixed[0][0]))
    anchor = min(range(3), key=lambda i:(fixed[i][1], fixed[i][0]))
    sel, se = sv.selector_for(abs(det))
    det_sign = -1 if det < 0 else 1
    n28 = []
    for axis in range(2):
        parts = []
        for v in range(3):
            if v == anchor: continue
            a2, b2 = (v+1)%3, (v+2)%3
            if axis == 0: e = fixed[a2][1]-fixed[b2][1]
            else: e = fixed[b2][0]-fixed[a2][0]
            delta = float(np.float32(np.float32(f32v(vwords[v]))
                                     - np.float32(f32v(vwords[anchor]))))
            edge = float(np.float32(e/256.0))
            if delta == 0.0 or edge == 0.0: continue
            _, md, ed = m.f32_parts(m.f32_bits(abs(delta)))
            _, mev, ee = m.f32_parts(m.f32_bits(abs(edge)))
            sign = -1 if (delta<0)!=(edge<0) else 1
            prod = md*mev; bits = prod.bit_length(); shift = bits-27
            if bits <= 32: t, g = prod, ed+ee
            else: t, g = (js.pps(md,mev,16)+(15<<16))>>shift, ed+ee+shift
            parts.append((sign*t, g))
        if not parts:
            n28.append((0,0,0)); continue
        gmin = min(g for _,g in parts)
        tot = sum(v << (g-gmin) for v,g in parts)
        s = 1 if tot>0 else (-1 if tot<0 else 0)
        m28, g28 = tap_rne(abs(tot), gmin, 28)
        n28.append((s, m28, g28))
    return n28, sel, se, det_sign, anchor, fixed

def child_words(verts, ch, tiles):
    vwords = [verts[v][2+ch] for v in range(3)]
    n28, sel, se, ds, an, fx = build28(verts, ch, vwords)
    slopes24 = []; slopes27 = []
    for axis in range(2):
        s, mag, g = n28[axis]
        if s == 0:
            slopes24.append(Fraction(0)); slopes27.append(Fraction(0)); continue
        idx, e = sel_oa(mag, g, sel, se)
        sgn = s*ds
        slopes27.append(Fraction(sgn*idx) * Fraction(2)**e)
        sh = idx.bit_length()-24
        i24, e24 = idx, e
        if sh > 0:
            i24 = m.rne_int(idx, sh); e24 = e + sh
            if i24.bit_length() > 24: i24 >>= 1; e24 += 1
        slopes24.append(Fraction(sgn*i24) * Fraction(2)**e24)
    asign, amant, aexp = m.f32_parts(vwords[an])
    cinfo = {}
    for (tx, ty) in tiles:
        parts = []
        for axis, tile in ((0, tx), (1, ty)):
            s, mag, g = n28[axis]
            disp = tile*32*256 - fx[an][axis]
            if s == 0 or disp == 0: continue
            didx = abs(disp); de = -8
            while didx.bit_length() < 24: didx <<= 1; de -= 1
            while didx.bit_length() > 24: didx >>= 1; de += 1
            prod = mag*didx; bits = prod.bit_length(); shift = bits-27
            if bits <= 32:
                if shift <= 0: t2, g2 = prod, g+de
                else:
                    t2 = (prod+(1<<(shift-1)))>>shift
                    if t2.bit_length() > 27: t2 >>= 1; shift += 1
                    g2 = g+de+shift
            else:
                Tc = max(0, mag.bit_length() - 8)
                c = js.carry_top(mag, didx, Tc)
                t2, g2 = (js.pps(mag,didx,Tc)+((c+10)<<Tc))>>shift, g+de+shift
            parts.append((s*(1 if disp>0 else -1)*t2, g2))
        if not parts:
            value = (asign*amant, aexp)
        else:
            gmin = min(g2 for _,g2 in parts)
            raw = sum(v<<(g2-gmin) for v,g2 in parts)
            jsign, jidx, je = ft.norm(raw, gmin, 28, "rne")
            if jsign == 0: value = (asign*amant, aexp)
            else:
                cidx, ce = sel_oa(jidx, je, sel, se)
                csign = jsign*ds
                mine = min(aexp, ce)
                value = ((asign*amant << (aexp-mine)) + (csign*cidx << (ce-mine)), mine)
        s28,m28x,e28 = ft.norm(value[0], value[1], 28, "rne")
        c28 = Fraction(s28*m28x) * Fraction(2)**e28
        s24,m24,e24 = ft.norm(s28*m28x, e28, 24, "rne")
        c24 = Fraction(s24*m24) * Fraction(2)**e24
        cexact = Fraction(value[0]) * Fraction(2)**value[1]
        cinfo[(tx,ty)] = (cexact, c28, c24)
    return slopes24, slopes27, cinfo

# ---- load geometry + hw ----
children = {}
for line in (S/"childgeo_all_residual_states.txt").read_text().splitlines():
    if "CHILDSDF" not in line: continue
    t = line[line.index("CHILDSDF"):].split()
    children.setdefault(int(t[1]), {})[int(t[2])] = [
        [int(x,16) for x in t[3+4*v:7+4*v]] for v in range(3)]

plan = json.loads((D/"reveal-agx-setup-accumulator-plan.json").read_text())
raw = (D/"capture2.raw").read_bytes()
RW = 36
samples = []   # (state, ordinal, x, y, hw_x_word, hw_y_word) for center+partners
for exp, draw in zip(plan["experiments"], plan["draws"]):
    r = exp["recordIndex"]
    words = struct.unpack_from(f"<{RW}I", raw, r*RW*4)
    x, y = exp["x"], exp["y"]
    st, od = exp["state"], exp["drawOrdinal"]
    xp = x+1 if x%2==0 else x-1
    yp = y+1 if y%2==0 else y-1
    center = words[16:20]
    xw = words[20:24] if x%2==0 else words[24:28]
    yw = words[28:32] if y%2==0 else words[32:36]
    samples.append((st, od, x, y, center[0], center[1]))
    samples.append((st, od, xp, y, xw[0], xw[1]))
    samples.append((st, od, x, yp, yw[0], yw[1]))

# precompute words per (state,ordinal,tile)
need = {}
for st, od, x, y, *_ in samples:
    need.setdefault((st,od), set()).add((x//32, y//32))
W = {}
for (st,od), tiles in need.items():
    verts = children[st][od]
    W[(st,od)] = [child_words(verts, ch, tiles) for ch in range(2)]

def offsets(x, y):
    tx, ty = x//32, y//32
    return tx, ty, Fraction(2*(x-tx*32)+1,2), Fraction(2*(y-ty*32)+1,2)

MODELS = {}
def model_eval(name, st, od, x, y, ch):
    slopes24, slopes27, cinfo = W[(st,od)][ch]
    tx, ty, ox, oy = offsets(x, y)
    cex, c28, c24 = cinfo[(tx,ty)]
    sx24, sy24 = slopes24; sx27, sy27 = slopes27
    if name == "exact_rtz":   return rtz_f32(cex + sx24*ox + sy24*oy)
    if name == "exact_rne":   return rne_f32(cex + sx24*ox + sy24*oy)
    if name == "c24_fma_yx_rne": return fma32(sx24, ox, fma32(sy24, oy, c24, rne_f32) and fr_bits(fma32(sy24, oy, c24, rne_f32)), rne_f32)
    return None

# simpler: explicit chains
def chain(cword, s1, o1, s2, o2, rnd):
    t = rnd(s1*o1 + cword)      # fma1
    return rnd(s2*o2 + fr_bits(t))  # fma2

results = {}
def test(name, fn):
    bad = 0; total = 0
    for st, od, x, y, hwx, hwy in samples:
        for ch, hw in ((0,hwx),(1,hwy)):
            slopes24, slopes27, cinfo = W[(st,od)][ch]
            tx, ty, ox, oy = offsets(x,y)
            cex, c28, c24 = cinfo[(tx,ty)]
            w = fn(cex, c28, c24, slopes24, slopes27, ox, oy)
            total += 1
            if w != hw: bad += 1
    results[name] = (bad, total)
    print(f"{name}: {bad}/{total} words wrong")

test("exact_c24_s24_rtz", lambda cex,c28,c24,s24,s27,ox,oy: rtz_f32(c24 + s24[0]*ox + s24[1]*oy))
test("exact_c24_s24_rne", lambda cex,c28,c24,s24,s27,ox,oy: rne_f32(c24 + s24[0]*ox + s24[1]*oy))
test("exact_c28_s24_rtz", lambda cex,c28,c24,s24,s27,ox,oy: rtz_f32(c28 + s24[0]*ox + s24[1]*oy))
test("exact_c28_s24_rne", lambda cex,c28,c24,s24,s27,ox,oy: rne_f32(c28 + s24[0]*ox + s24[1]*oy))
test("exact_c28_s27_rne", lambda cex,c28,c24,s24,s27,ox,oy: rne_f32(c28 + s27[0]*ox + s27[1]*oy))
test("exact_cex_s27_rne", lambda cex,c28,c24,s24,s27,ox,oy: rne_f32(cex + s27[0]*ox + s27[1]*oy))
test("exact_cex_s24_rne", lambda cex,c28,c24,s24,s27,ox,oy: rne_f32(cex + s24[0]*ox + s24[1]*oy))
test("fma_y_then_x_c24_rne", lambda cex,c28,c24,s24,s27,ox,oy: chain(c24, s24[1], oy, s24[0], ox, rne_f32))
test("fma_x_then_y_c24_rne", lambda cex,c28,c24,s24,s27,ox,oy: chain(c24, s24[0], ox, s24[1], oy, rne_f32))
test("fma_y_then_x_c28_rne", lambda cex,c28,c24,s24,s27,ox,oy: chain(c28, s24[1], oy, s24[0], ox, rne_f32))
test("fma_x_then_y_c28_rne", lambda cex,c28,c24,s24,s27,ox,oy: chain(c28, s24[0], ox, s24[1], oy, rne_f32))
