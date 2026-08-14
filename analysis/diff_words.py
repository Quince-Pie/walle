import sys
from pathlib import Path
S = Path("/tmp/nix-shell.PFgUGF/claude-1000/-tmp-walle/4ccfbce8-33b2-4b5f-8e29-93486397c8a4/scratchpad")
hw = {}
for line in (S/"hw2_values.txt").read_text().splitlines():
    t = line.split(); p = t[0].split(":")
    hw.setdefault((p[0],p[1],p[2]), {})[p[3]] = [int(w,16) for w in t[1:7]]
exact = off = pixels = 0
from collections import Counter
hist = Counter()
for line in sys.stdin:
    t = line.split()
    st, x, y = t[1], t[2], t[3]
    words = [int(w,16) for w in t[4:10]]
    own = "o" + t[10].split("=")[1]
    rows = hw.get((st,x,y), {})
    ref = rows.get(own) or (next(iter(rows.values())) if rows else None)
    if ref is None: continue
    pixels += 1
    ds = tuple(h - l for h, l in zip(ref, words))
    hist[ds] += 1
    for d in ds:
        if d == 0: exact += 1
        else: off += 1
print(f"vr={sys.argv[1]} cm={sys.argv[2]}: exact {exact}, off {off} (pixels {pixels})")
for pat, c in hist.most_common(6): print("   ", pat, c)
