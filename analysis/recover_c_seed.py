"""Recover the exact C walk seed per child group under EXACT steps
(step = slope_word * 32, exact), then score seed candidates:

  cand1 = my banked chain evaluated at the anchor tile corner
  cand2 = exact plane through the anchor VERTEX value word:
          v_an + (t*8192 - fx_an)/8192 * word*32 per axis

Reports the feasible acc interval at the anchor tile (grid units of
the A-slope ulp*32) and where each candidate falls.
"""
import sys, json
sys.path[:0] = ["/tmp/walle", "/tmp/walle/analysis"]
from pathlib import Path
from fractions import Fraction
from collections import defaultdict
import _sweep_fused_join_lattice as m
from hunt_c_walk_seed import (build28, chain_wide, frac_of, rne24_word_frac,
                              load_sdf)

F2 = Fraction(2)
D = Path("/tmp/walle/build/analysis-agx-basis/residual-children-dense-plan-v1")
PLAN = json.load(open(D / "reveal-agx-setup-accumulator-plan.json"))
T = m.load_records(D / "capture.raw", len(PLAN["draws"]))
groups = defaultdict(dict)
for exp, draw in zip(PLAN["experiments"], PLAN["draws"]):
    key = (exp["state"], exp["drawOrdinal"])
    r = exp["recordIndex"]
    for ctx in range(2):
        A, B, C = (int(T[r][ctx][i]) for i in range(3))
        groups[key + (ctx, A, B)][(draw["tileX"], draw["tileY"])] = C

def word_ulp(w):
    if not (w & 0x7fffffff): return None
    return F2**(m.f32_parts(w)[2])

sdf_cache = {}
n_consistent = n_inconsistent = 0
c1_in = c2_in = n_scored = 0
for (st, ordn, ctx, A, B), tiles in sorted(groups.items()):
    if len(tiles) < 8: continue
    if st not in sdf_cache: sdf_cache[st] = load_sdf(st)
    verts = sdf_cache[st].get(ordn)
    if verts is None: continue
    vwords = [verts[v][2 + ctx] for v in range(3)]
    n28, sel, se, ds, an, fx = build28(verts, vwords)
    atile = (fx[an][0] // (32 * 256), fx[an][1] // (32 * 256))
    stepx = frac_of(A) * 32
    stepy = frac_of(B) * 32
    lo = hi = None
    ok_int = True
    for (tx, ty), w in tiles.items():
        if not (w & 0x7fffffff): continue
        v = frac_of(w)
        ulp = word_ulp(w)
        walk = (tx - atile[0]) * stepx + (ty - atile[1]) * stepy
        a, b = v - ulp / 2 - walk, v + ulp / 2 - walk
        lo = a if lo is None else max(lo, a)
        hi = b if hi is None else min(hi, b)
    if lo is None: continue
    if lo > hi:
        n_inconsistent += 1
        print(f"s{st} o{ordn} ctx{ctx} A={A:08x} B={B:08x} tiles={len(tiles)} "
              f"INCONSISTENT width={float(hi-lo):.3e}")
        continue
    n_consistent += 1
    seed1 = chain_wide(n28, sel, se, ds, an, fx, vwords, [atile])[atile][0]
    van = frac_of(vwords[an])
    seed2 = (van + Fraction(atile[0] * 8192 - fx[an][0], 8192) * stepx / 32 * 32
                 + Fraction(aty := 0, 1))
    seed2 = (van
             + Fraction(atile[0] * 8192 - fx[an][0], 8192) * frac_of(A) * 32
             + Fraction(atile[1] * 8192 - fx[an][1], 8192) * frac_of(B) * 32)
    # express positions in units of the interval width scale: use word ulp
    # of a representative in-range word
    ref = next(w for w in tiles.values() if w & 0x7fffffff)
    u = word_ulp(ref)
    mid = (lo + hi) / 2
    inside1 = lo <= seed1 <= hi
    inside2 = lo <= seed2 <= hi
    n_scored += 1
    c1_in += inside1; c2_in += inside2
    print(f"s{st} o{ordn} ctx{ctx} A={A:08x} B={B:08x} tiles={len(tiles):4d} "
          f"iw={float((hi-lo)/u):.4f}ulp "
          f"c1={'IN ' if inside1 else 'out'}({float((seed1-mid)/u):+.4f}) "
          f"c2={'IN ' if inside2 else 'out'}({float((seed2-mid)/u):+.4f})")
print(f"\nconsistent groups: {n_consistent}, inconsistent: {n_inconsistent}")
print(f"cand1 (chain@atile) inside: {c1_in}/{n_scored}; "
      f"cand2 (anchor-vertex plane): {c2_in}/{n_scored}")
