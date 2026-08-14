"""Score C-chain candidate laws against ALL dense-capture C tiles.

Groups tiles by (state, ordinal, ctx, A, B); child geometry from
CHILDSDF; skips groups whose predicted slope word mismatches (those
are numerator-law failures, counted separately).

Knobs per candidate: numerator tap width NW, mid carry MIDC, mid width
MW, sel bias SELC, order ("mid" = didx first, "slope" = sel first).
"""
import sys, json
sys.path[:0] = ["/tmp/walle", "/tmp/walle/analysis"]
from pathlib import Path
from fractions import Fraction
from collections import defaultdict, Counter
import _sweep_fused_join_lattice as m
import _joint_stage_sweep as js
from hunt_c_walk_seed import build28, frac_of, rne24_word_frac, load_sdf

F2 = Fraction(2)

def sel_oa27(mand, me, sel, se, BIAS):
    prod = mand*sel; bits = prod.bit_length(); shift = bits-27
    if bits <= 32:
        if shift <= 0: return prod, me+se
        idx = (prod + (1 << (shift-1))) >> shift
        if idx.bit_length() > 27: idx >>= 1; shift += 1
        return idx, me+se+shift
    Tc = max(0, mand.bit_length() - 8)
    return (js.pps(mand, sel, Tc) + (BIAS << Tc)) >> shift, me+se+shift

def c_word(order, n28ax, sel, se, ds, ay_or_ax, tilepos, MIDC, MW, SELC):
    s_, mag, g = n28ax
    disp = tilepos - ay_or_ax
    if s_ == 0 or disp == 0:
        return None  # no contribution; caller handles
    didx = abs(disp); de = -8
    while didx.bit_length() < 24: didx <<= 1; de -= 1
    if order == "mid":
        prod = mag*didx; bits = prod.bit_length(); shift = bits-MW
        if bits <= 32:
            if shift <= 0: t2, g2 = prod, g+de
            else:
                t2 = (prod+(1<<(shift-1)))>>shift
                if t2.bit_length() > MW: t2 >>= 1; shift += 1
                g2 = g+de+shift
        else:
            Tc = max(0, mag.bit_length()-8)
            c = js.carry_top(mag, didx, Tc)
            t2 = (js.pps(mag, didx, Tc) + ((c+MIDC)<<Tc)) >> shift
            g2 = g+de+shift
        cx, ce = sel_oa27(t2, g2, sel, se, SELC)
    else:
        sl, sle = sel_oa27(mag, g, sel, se, SELC)
        prod = sl*didx; bits = prod.bit_length(); shift = bits-MW
        if bits <= 32:
            if shift <= 0: cx, ce = prod, sle+de
            else:
                cx = (prod+(1<<(shift-1)))>>shift
                if cx.bit_length() > MW: cx >>= 1; shift += 1
                ce = sle+de+shift
        else:
            Tc = max(0, sl.bit_length()-8)
            c = js.carry_top(sl, didx, Tc)
            cx = (js.pps(sl, didx, Tc) + ((c+MIDC)<<Tc)) >> shift
            ce = sle+de+shift
    return (1 if disp > 0 else -1)*s_*ds*cx, ce

def run(candidates):
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
    results = {name: Counter() for name in candidates}
    for (st, ordn, ctx, A, B), tiles in sorted(groups.items()):
        if st not in sdf_cache: sdf_cache[st] = load_sdf(st)
        verts = sdf_cache[st].get(ordn)
        if verts is None: continue
        vwords = [verts[v][2 + ctx] for v in range(3)]
        n28, sel, se, ds, an, fx = build28(verts, vwords)
        for name, (order, MIDC, MW, SELC) in candidates.items():
            R = results[name]
            for (tx, ty), hwC in tiles.items():
                parts = []
                for axis, tp in ((0, tx*8192), (1, ty*8192)):
                    r_ = c_word(order, n28[axis], sel, se, ds, fx[an][axis], tp,
                                MIDC, MW, SELC)
                    if r_ is not None: parts.append(r_)
                import _fit_child_tiles as ft
                asign, amant, aexp = m.f32_parts(vwords[an]) if vwords[an] else (0,0,0)
                if not parts:
                    val = Fraction(asign*amant)*F2**aexp if amant else Fraction(0)
                else:
                    gmin = min(e for _, e in parts)
                    tot_ = sum(v << (e-gmin) for v, e in parts)
                    if amant:
                        gm2 = min(gmin, aexp)
                        tot_ = (tot_ << (gmin-gm2)) + asign*amant*(1 << (aexp-gm2))
                        gmin = gm2
                    s28, m28, e28 = ft.norm(tot_, gmin, 28, "rne")
                    val = Fraction(s28*m28)*F2**e28
                if rne24_word_frac(val) == hwC: R["ok"] += 1
                else: R["bad"] += 1
    return results

if __name__ == "__main__":
    cands = {
        "banked (mid c+10 W27 sel20)": ("mid", 10, 27, 20),
        "mid c+14 W30 sel28":          ("mid", 14, 30, 28),
        "slope-first W32-ish sel20":   ("slope", 10, 32, 20),
    }
    for name, R in run(cands).items():
        t = R["ok"] + R["bad"]
        print(f"{name}: {R['ok']}/{t} ({100.0*R['ok']/max(1,t):.2f}%)")
