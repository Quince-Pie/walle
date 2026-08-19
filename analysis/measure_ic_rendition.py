#!/usr/bin/env python3
"""Characterize Increase Contrast vs normal Liquid Glass (paired natural sets).
Correct geometry: origin (0.25,0.30) of 2048px; edges via sector-restricted st5-8."""
import numpy as np
from PIL import Image
from numpy.lib.stride_tricks import sliding_window_view

IC = "/tmp/lgcap-ic-1024"
NM = "/tmp/lgcap-natural-1024"
CASES = [("clear", "dark"), ("clear", "light"), ("regular", "dark"), ("regular", "light")]


def load(p):
    return np.asarray(Image.open(p).convert("RGB"), dtype=np.float64)


def luma(a):
    return 0.2126 * a[..., 0] + 0.7152 * a[..., 1] + 0.0722 * a[..., 2]


def eroded(mask, k=25):
    pad = np.pad(mask.astype(np.uint8), k // 2, mode="constant")
    win = sliding_window_view(pad, (k, k))
    return win.min(axis=(2, 3)).astype(bool)


ref = load(f"{NM}/reference/dynamic-natural-field.png")
ref_ic = load(f"{IC}/reference/dynamic-natural-field.png")
print(f"reference identity: mean|Δ| = {np.abs(ref_ic-ref).mean():.3f} codes")
L_ref = luma(ref)
H, W = L_ref.shape
cy, cx = 0.3 * H, 0.25 * W
yy, xx = np.mgrid[0:H, 0:W]
rad = np.sqrt((yy - cy) ** 2 + (xx - cx) ** 2)
ang_ok = ((xx - cx) / np.maximum(rad, 1) > 0.25) | ((yy - cy) / np.maximum(rad, 1) > 0.25)

print("\n== interior comparison (abs mean L / std L / mean|IC-NM|) st8 & st16 ==")
for st in (8, 16):
    for variant, app in CASES:
        seq = f"sweep__wallpaper-transition__{variant}__{app}"
        f_n = load(f"{NM}/sweeps/{seq}/frame-{st:04d}.png")
        f_i = load(f"{IC}/sweeps/{seq}/frame-{st:04d}.png")
        g = np.abs(f_n - ref).max(axis=2) > 6
        inter = eroded(g)
        Ln = luma(f_n)[inter]; Li = luma(f_i)[inter]
        pair = float(np.abs(f_i - f_n)[inter].mean())
        print(f"  st{st} {variant}/{app}: NM {Ln.mean():6.1f}/{Ln.std():5.1f} | IC {Li.mean():6.1f}/{Li.std():5.1f} | pair {pair:.2f}")

print("\n== sector-restricted edge profile at st8 (border hunt) ==")
for variant, app in CASES:
    seq = f"sweep__wallpaper-transition__{variant}__{app}"
    f_n = load(f"{NM}/sweeps/{seq}/frame-0008.png")
    f_i = load(f"{IC}/sweeps/{seq}/frame-0008.png")
    dL_ic = luma(f_i) - L_ref
    dL_nm = luma(f_n) - L_ref
    g = (np.abs(f_n - ref).max(axis=2) > 6) & ang_ok
    Rmax = rad[g].max()
    rr = np.arange(Rmax - 120, min(Rmax + 60, 1430), 2)
    prof_i, prof_n, rv = [], [], []
    for r0 in rr:
        m = (rad >= r0) & (rad < r0 + 2) & ang_ok
        if m.sum() < 60:
            continue
        rv.append(r0 + 1)
        prof_i.append(dL_ic[m].mean())
        prof_n.append(dL_nm[m].mean())
    prof_i = np.array(prof_i); prof_n = np.array(prof_n); rv = np.array(rv)
    d = prof_i - prof_n
    j = int(np.argmax(np.abs(d)))
    print(f"  {variant}/{app}: max|IC-NM| along edge = {d[j]:+.1f} @r={rv[j]:.0f} (edge~{Rmax:.0f})")
    print(f"     IC-NM prof: {' '.join(f'{v:+.0f}' for v in d[::4])}")
