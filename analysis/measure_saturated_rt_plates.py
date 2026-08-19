#!/usr/bin/env python3
"""The RT plate law decider: absolute plate/scrim colors over a deep-red vs a
deep-blue background. Constant plate => identical absolutes over both.
Background-derived => it moves. Also reports the normal (non-RT) saturated
interiors as the extreme-chroma stress reference."""
import numpy as np
from PIL import Image
from numpy.lib.stride_tricks import sliding_window_view

CASES = [("clear", "dark"), ("clear", "light"), ("regular", "dark"), ("regular", "light")]


def load(p):
    return np.asarray(Image.open(p).convert("RGB"), dtype=np.float64)


def eroded(mask, k=25):
    pad = np.pad(mask.astype(np.uint8), k // 2, mode="constant")
    win = sliding_window_view(pad, (k, k))
    return win.min(axis=(2, 3)).astype(bool)


def interiors(base_norm, base_rt, refname):
    ref = load(f"{base_norm}/reference/{refname}.png")
    print(f"  background mean RGB = {ref.reshape(-1,3).mean(axis=0).round(1)}")
    for variant, app in CASES:
        seq = f"sweep__wallpaper-transition__{variant}__{app}"
        f_n = load(f"{base_norm}/sweeps/{seq}/frame-0008.png")
        f_r = load(f"{base_rt}/sweeps/{seq}/frame-0008.png")
        g = np.abs(f_n - ref).max(axis=2) > 6
        if g.sum() < 5000:
            print(f"  {variant}/{app}: footprint missing"); continue
        inter = eroded(g)
        mn = f_n[inter].mean(axis=0)
        mr = f_r[inter].mean(axis=0)
        sr = f_r[inter].std(axis=0).mean()
        print(f"  {variant}/{app}: NORMAL abs=({mn[0]:6.1f},{mn[1]:6.1f},{mn[2]:6.1f})"
              f"  RT abs=({mr[0]:6.1f},{mr[1]:6.1f},{mr[2]:6.1f}) rt-std={sr:5.1f}")


print("== over DEEP RED (dynamic-saturated-red) ==")
interiors("/tmp/lgcap-sat-red", "/tmp/lgcap-sat-rt-red", "dynamic-saturated-red")
print("\n== over DEEP BLUE (dynamic-saturated-blue) ==")
interiors("/tmp/lgcap-sat-blue", "/tmp/lgcap-sat-rt-blue", "dynamic-saturated-blue")
