"""Test the pure-plane C model on the dense capture:

  C_word(tx,ty) = RNE24( chain28(anchor_tile)
                         + (tx-atx) * A_word * 32
                         + (ty-aty) * B_word * 32 )

with all arithmetic exact (steps = slope words scaled by 2^5, exact in
fp; single rounding at export).  Fully input-only.
"""
import sys, json
sys.path[:0] = ["/tmp/walle", "/tmp/walle/analysis"]
from pathlib import Path
from fractions import Fraction
from collections import defaultdict, Counter
import _sweep_fused_join_lattice as m
from hunt_c_walk_seed import (build28, chain_wide, frac_of, rne24_word_frac,
                              load_sdf)

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

sdf_cache = {}
tot_ok = tot_bad = 0
worst = []
for (st, ordn, ctx, A, B), tiles in sorted(groups.items()):
    if len(tiles) < 4: continue
    if st not in sdf_cache: sdf_cache[st] = load_sdf(st)
    verts = sdf_cache[st].get(ordn)
    if verts is None: continue
    vwords = [verts[v][2 + ctx] for v in range(3)]
    n28, sel, se, ds, an, fx = build28(verts, vwords)
    atile = (fx[an][0] // (32 * 256), fx[an][1] // (32 * 256))
    seedv = chain_wide(n28, sel, se, ds, an, fx, vwords, [atile])[atile][0]
    stepx = frac_of(A) * 32
    stepy = frac_of(B) * 32
    ok = bad = 0
    deltas = Counter()
    for (tx, ty), w in sorted(tiles.items()):
        pred = seedv + (tx - atile[0]) * stepx + (ty - atile[1]) * stepy
        pw = rne24_word_frac(pred)
        if pw == w: ok += 1
        else:
            bad += 1
            d = (w & 0x7fffffff) - (pw & 0x7fffffff)
            deltas[d if abs(d) < 9 else 99] += 1
    tot_ok += ok; tot_bad += bad
    tag = "EXACT" if bad == 0 else f"{ok}/{ok+bad} deltas={dict(deltas.most_common(4))}"
    print(f"s{st} o{ordn} ctx{ctx} A={A:08x} B={B:08x} tiles={len(tiles)} {tag}")
print(f"TOTAL: {tot_ok} ok, {tot_bad} bad ({100.0*tot_ok/max(1,tot_ok+tot_bad):.2f}%)")
