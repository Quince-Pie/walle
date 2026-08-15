#!/usr/bin/env python3
"""Per-residual-pixel plane oracle: which captured Apple plane reproduces
the reference byte through the exact shader downstream?"""
import sys, json, struct
sys.path[:0]=["/tmp/walle","/tmp/walle/analysis"]
from pathlib import Path
from fractions import Fraction
import numpy as np
from PIL import Image
import _sweep_fused_join_lattice as m
from collections import defaultdict

F32 = np.float32
CORPUS = Path("/tmp/walle/artifacts/liquid-glass-reveal-coverage-01421a3-v1/"
              "capture/sweeps/sweep__wallpaper-reveal__regular__dark")
CAP = Path(sys.argv[1]) if len(sys.argv) > 1 else None
TABLE = open("/tmp/walle/parity/apple_fast_sqrt_correction_nibbles.bin","rb").read()

def frac_to_f32(r: Fraction) -> np.float32:
    if r == 0: return F32(0.0)
    s = -1 if r < 0 else 1
    a = abs(r)
    num, den = a.numerator, a.denominator
    e = num.bit_length() - den.bit_length()
    # scale so that 2^23 <= a*2^-e' < 2^24 with sticky
    e -= 24
    if e >= 0: den <<= e
    else: num <<= -e
    q, rem = divmod(num, den)
    while q.bit_length() > 24:
        rem = rem + (q & 1) * den  # sticky via rem!=0 tracking
        sticky = rem != 0
        q >>= 1; e += 1
        rem = 1 if sticky else 0; den = 2
    while q.bit_length() < 24:
        q <<= 1; e -= 1
        q |= 1 if rem else 0 and 0  # cannot borrow bits; instead recompute
    # simpler exact path: recompute with target exponent
    # (fallback: use precise loop below)
    return None

def to_f32(r: Fraction) -> np.float32:
    """Exact RNE round of a Fraction to float32."""
    if r == 0: return F32(0.0)
    sgn = -1.0 if r < 0 else 1.0
    a = abs(r)
    num, den = a.numerator, a.denominator
    e = num.bit_length() - den.bit_length()
    # ensure 2^24 <= num/den * 2^(24-e) < 2^25 range; get 25 bits + sticky
    sh = 25 - e
    if sh >= 0: num <<= sh
    else: den <<= -sh
    q, rem = divmod(num, den)
    if q.bit_length() > 25: q >>= 1; e += 1; rem |= q & 1
    # q has 25 bits: round to 24 with RNE using guard=bit0, sticky=rem
    guard = q & 1; q >>= 1
    if guard and (rem or (q & 1)): q += 1
    if q.bit_length() > 24: q >>= 1; e += 1
    # value = sgn * q * 2^(e-24)
    v = np.ldexp(F32(1.0), 0)
    res = F32(sgn) * F32(np.ldexp(np.float64(q), e - 24))
    return F32(res)

def fbits(f) -> int: return struct.unpack('<I', struct.pack('<f', float(f)))[0]
def bitsf(w) -> np.float32: return F32(struct.unpack('<f', struct.pack('<I', w & 0xFFFFFFFF))[0])

def fma32(a, b, c):
    return to_f32(Fraction(float(a)) * Fraction(float(b)) + Fraction(float(c)))

def apple_length(x: np.float32, y: np.float32) -> np.float32:
    xx = F32(x) * F32(x)
    square = fma32(y, y, xx)
    root = F32(np.sqrt(F32(square)))
    sb = fbits(square)
    mant = sb & 0x7fffff
    pm = mant >> 1
    ba = pm & ~3
    pw = struct.unpack('<I', TABLE[ba:ba+4])[0]
    pc = (pw >> ((pm & 3) * 8)) & 0xff
    code = (pc >> ((mant & 1) * 4)) & 15
    corr = code & 3 if ((sb >> 23) & 1) == 0 else (code >> 2) & 3
    return bitsf((fbits(root) + corr - 1) & 0xFFFFFFFF)

def decompose(bits):
    sign = -1 if (bits >> 31) else 1
    enc = (bits >> 23) & 0xff
    if enc == 0: return (bits & 0x7fffff), -149, sign
    return (bits & 0x7fffff) | 0x800000, enc - 150, sign

def toward_zero_word(total: int, grid: int) -> int:
    if total == 0: return 0
    sbit = 1 if total < 0 else 0
    mag = abs(total)
    high = mag.bit_length() - 1
    low = high - 23
    mantissa = (mag >> low) if low > 0 else (mag << -low)
    ec = grid + low + 150
    if ec <= 0: return sbit << 31
    if ec >= 255: return (sbit << 31) | 0x7f000000
    return (sbit << 31) | (ec << 23) | (mantissa & 0x7fffff)

def general_value(cw, ax, bx, x, y):
    """Evaluate one channel plane (C word cw, slopeX ax, slopeY bx) at pixel."""
    lx, ly = x & 31, y & 31
    mc, ec, sc = decompose(cw) if cw else (0, 0, 1)
    mx, ex, sx = decompose(ax) if ax else (0, 0, 1)
    my, ey, sy = decompose(bx) if bx else (0, 0, 1)
    px = mx * (2 * lx + 1); ex -= 1
    py = my * (2 * ly + 1); ey -= 1
    maxe = -1000
    if cw and mc: maxe = max(maxe, ec)
    if px: maxe = max(maxe, ex)
    if py: maxe = max(maxe, ey)
    if maxe == -1000: return bitsf((cw >> 31) << 31)
    grid = maxe - 64
    tot = 0
    if cw and mc: tot += sc * (mc << (ec - grid))
    if px: tot += sx * (px << (ex - grid))
    if py: tot += sy * (py << (ey - grid))
    return bitsf(toward_zero_word(tot, grid))

def downstream(coords_c, coords_x, coords_y, state, x, y):
    dv = apple_length(*coords_c)
    dx = apple_length(*coords_x)
    dy = apple_length(*coords_y)
    feather = max(F32(abs(F32(dx) - F32(dv))) + F32(abs(F32(dy) - F32(dv))), F32(1e-4))
    t = F32(F32(F32(F32(1.0) - dv) / feather) + F32(0.5))
    alpha = min(max(t, F32(0.0)), F32(1.0))
    if alpha != 0.0 and alpha != 1.0:
        alpha = F32(np.float16(alpha))
    if state == 42 and 512 <= x < 1933 and y < 32:
        alpha = F32(np.float16(F32(alpha) * F32(np.float16(bitsf(0x3BFF7 >> 4)))))
    scaled = F32(alpha * F32(255.0))
    tr = int(scaled)
    rem = F32(scaled - F32(tr))
    if rem > 0.5 or (rem == 0.5 and (tr & 1)): tr += 1
    return min(tr, 255)

def eval_plane(group_tiles, gsl, x, y, state):
    tile = (x >> 5, y >> 5)
    if tile not in group_tiles: return None
    c0, c1 = group_tiles[tile]
    def coords(px, pyy):
        return (general_value(c0, gsl[0][0], gsl[0][1], px, pyy),
                general_value(c1, gsl[1][0], gsl[1][1], px, pyy))
    return downstream(coords(x, y), coords(x ^ 1, y), coords(x, y ^ 1), state, x, y)

def main():
    D = Path("/tmp/walle/build/analysis-agx-basis/residual-children-dense-plan-v1")
    PLAN = json.load(open(D / "reveal-agx-setup-accumulator-plan.json"))
    T = m.load_records(D / "capture.raw", len(PLAN["draws"]))
    groups = defaultdict(dict); gslopes = {}
    for exp, draw in zip(PLAN["experiments"], PLAN["draws"]):
        r = exp["recordIndex"]; key = (exp["state"], exp["drawOrdinal"])
        tile = (draw["tileX"], draw["tileY"])
        cs = groups[key].setdefault(tile, [0, 0])
        for ctx in range(2):
            A, B, C = (int(T[r][ctx][i]) for i in range(3))
            cs[ctx] = C
            gslopes.setdefault(key, [[0, 0], [0, 0]])[ctx] = [A, B]
    # residual pixels
    for st in range(64):
        r8 = CAP / f"state-{st:04}.r8"
        if not r8.exists(): continue
        cand = np.frombuffer(r8.read_bytes(), dtype=np.uint8).reshape(2048, 2048)
        ref = np.asarray(Image.open(CORPUS / f"frame-{st:04}.png").convert("RGBA"))[..., 0]
        ys, xs = np.nonzero(cand != ref)
        if not len(ys): continue
        print(f"state {st}: {len(ys)} residuals")
        for y, x in zip(ys.tolist(), xs.tolist()):
            row = [f"  ({x:4d},{y:4d}) walle={cand[y,x]:3d} ref={ref[y,x]:3d} |"]
            for (s2, o), tiles in sorted(groups.items()):
                if s2 != st: continue
                b = eval_plane(tiles, gslopes[(s2, o)], x, y, st)
                if b is None: continue
                mark = "*" if b == ref[y, x] else (" " if b != cand[y, x] else "w")
                row.append(f" o{o}={b:3d}{mark}")
            print("".join(row))

main()
