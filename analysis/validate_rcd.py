import json, sys, pickle
sys.path[:0] = ["/tmp/walle"]
from collections import defaultdict
from pathlib import Path
import numpy as np
import _sweep_fused_join_lattice as m
import _solve_clip_varyings as sv
import _fit_child_tiles as ft
import _joint_stage_sweep as js

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

def words28(n28, sel, se, ds, an, fx, vwords, tiles):
    out_slopes = []
    for axis in range(2):
        s, mag, g = n28[axis]
        if s == 0: out_slopes.append(0); continue
        idx, e = sel_oa(mag, g, sel, se)
        sh = idx.bit_length()-24
        if sh > 0:
            idx = m.rne_int(idx, sh); e += sh
            if idx.bit_length() > 24: idx >>= 1; e += 1
        try: out_slopes.append(m.dyadic_to_f32(s*ds, idx, e))
        except (ValueError, OverflowError): out_slopes.append(None)
    cs = {}
    asign, amant, aexp = m.f32_parts(vwords[an])
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
        s28x,m28x,e28x = ft.norm(value[0], value[1], 28, "rne")
        s24,m24,e24 = ft.norm(s28x*m28x, e28x, 24, "rne")
        if s24 == 0: cs[(tx,ty)] = 0
        else:
            try: cs[(tx,ty)] = m.dyadic_to_f32(s24, m24, e24)
            except ValueError: cs[(tx,ty)] = None
    return out_slopes, cs

def perturb(w, k):
    if w == 0: return w
    return m.key_to_bits(m.ordered_key(w) + k)

D = Path("/tmp/walle/build/analysis-agx-basis/residual-children-dense-plan-v1")
PLAN = json.loads((D / "reveal-agx-setup-accumulator-plan.json").read_text())
T = m.load_records(D / "capture.raw", len(PLAN["draws"]))
obs = defaultdict(lambda: defaultdict(dict))
AN = {}
for exp, draw in zip(PLAN["experiments"], PLAN["draws"]):
    r = exp["recordIndex"]
    for ctx in range(4):
        d = obs[(exp["state"], exp["drawOrdinal"], ctx)]
        d["AB"] = (int(T[r][ctx][0]), int(T[r][ctx][1]))
        d.setdefault("C", {})[(draw["tileX"], draw["tileY"])] = int(T[r][ctx][2])
    AN[(exp["state"], exp["drawOrdinal"])] = exp["anchor"]
ok = tot = 0; badc = 0
bad_rows = []
for (state, ordinal, ctx), d in sorted(obs.items()):
    verts = load_sdf(state)[ordinal]
    an0 = AN[(state, ordinal)]
    ch = ctx % 2
    vwords = []
    for vi in range(3):
        w = verts[vi][2+ch]
        if ctx >= 2 and vi == an0: w = perturb(w, 1)
        vwords.append(w)
    n28, sel, se, ds, an, fx = build28(verts, ch, vwords)
    slopes, cs = words28(n28, sel, se, ds, an, fx, vwords, d["C"].keys())
    bad_tiles = [(tt, w, cs.get(tt)) for tt, w in d["C"].items() if cs.get(tt) != w]
    slope_ok = (slopes[0] == d["AB"][0] and slopes[1] == d["AB"][1])
    good = slope_ok and not bad_tiles
    tot += 1; ok += good; badc += len(bad_tiles)
    if not good:
        bad_rows.append((state, ordinal, ctx, slope_ok,
                         d["AB"], (slopes[0], slopes[1]), len(bad_tiles),
                         bad_tiles[:3]))
print(f"residual-children dense: {ok}/{tot} contexts fully exact; bad C tiles {badc}")
for row in bad_rows[:25]:
    st, od, ctx, sok, hwab, lawab, nbad, ex = row
    print(f"  s{st} o{od} ctx{ctx} slopes_ok={sok} hwAB=({hwab[0]:08x},{hwab[1]:08x})"
          f" lawAB=({lawab[0] if lawab[0] is None else format(lawab[0],'08x')},"
          f"{lawab[1] if lawab[1] is None else format(lawab[1],'08x')}) badC={nbad}")
    for tt, hww, lw in ex:
        print(f"     tile{tt}: hw={hww:08x} law={lw if lw is None else format(lw,'08x')}")
pickle.dump(dict(obs), open("/tmp/walle/build/_rcd_obs.pkl","wb"))
