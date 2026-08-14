"""Compare probe clipped-children words vs the residual-children dense
capture (production values, ctx0/1)."""
import sys, json, pickle
sys.path[:0] = ["/tmp/walle"]
from pathlib import Path
from collections import defaultdict
import _sweep_fused_join_lattice as m

S = Path("/tmp/nix-shell.PFgUGF/claude-1000/-tmp-walle/4ccfbce8-33b2-4b5f-8e29-93486397c8a4/scratchpad")
children = defaultdict(list)   # (state, ordinal) -> [entries]
ctiles = defaultdict(dict)     # (state, index) -> {(tx,ty): (cx,cy)}
for line in (S/"childwords_clip.txt").read_text().splitlines():
    t = line.split()
    if t[0] == "CHILDW":
        st, ordn, idx = int(t[1]), int(t[2]), int(t[3])
        fx = [(int(t[4]), int(t[5])), (int(t[6]), int(t[7])), (int(t[8]), int(t[9]))]
        ds, pg = int(t[10]), int(t[11])
        slopes = [int(w, 16) for w in t[12:16]]
        children[(st, ordn)].append({"idx": idx, "fx": fx, "ds": ds,
                                     "slopes": slopes})
    else:
        st, idx, tx, ty = int(t[1]), int(t[2]), int(t[3]), int(t[4])
        ctiles[(st, idx)][(tx, ty)] = (int(t[5], 16), int(t[6], 16))

def contains(fx, x, y):
    det = ((fx[1][0]-fx[0][0])*(fx[2][1]-fx[0][1])
           - (fx[1][1]-fx[0][1])*(fx[2][0]-fx[0][0]))
    if det == 0: return False
    exp_ = -1 if det < 0 else 1
    cx, cy = 256*x+128, 256*y+128
    for e in range(3):
        nx = (e+1)%3
        ex = fx[nx][0]-fx[e][0]; ey = fx[nx][1]-fx[e][1]
        cr = ex*(cy-fx[e][1]) - ey*(cx-fx[e][0])
        if cr == 0: continue
        if (1 if cr > 0 else -1) != exp_: return False
    return True

D = Path("/tmp/walle/build/analysis-agx-basis/residual-children-dense-plan-v1")
PLAN = json.load(open(D / "reveal-agx-setup-accumulator-plan.json"))
T = m.load_records(D / "capture.raw", len(PLAN["draws"]))
ok_c = bad_c = no_child = 0
ok_ab = bad_ab = 0
from collections import Counter
cdelta = Counter()
for exp, draw in zip(PLAN["experiments"], PLAN["draws"]):
    st, ordn = exp["state"], exp["drawOrdinal"]
    px, py = draw["x"], draw["y"]
    tile = (draw["tileX"], draw["tileY"])
    cands = children.get((st, ordn), [])
    sub = next((c for c in cands if contains(c["fx"], px, py)), None)
    if sub is None:
        no_child += 1; continue
    r = exp["recordIndex"]
    for ctx in range(2):     # production channels only
        hwA, hwB, hwC = (int(T[r][ctx][i]) for i in range(3))
        myA, myB = sub["slopes"][2*ctx], sub["slopes"][2*ctx+1]
        if (myA, myB) == (hwA, hwB): ok_ab += 1
        else: bad_ab += 1
        myC = ctiles.get((st, sub["idx"]), {}).get(tile)
        if myC is None: continue
        mc = myC[ctx]
        if mc == hwC: ok_c += 1
        else:
            bad_c += 1
            d = (hwC & 0x7fffffff) - (mc & 0x7fffffff)
            cdelta[d if abs(d) < 6 else 99] += 1
print(f"AB pairs: {ok_ab} ok, {bad_ab} bad; C tiles: {ok_c} ok, {bad_c} bad; no-child {no_child}")
print("C deltas:", dict(cdelta.most_common(8)))
