#!/usr/bin/env python3
"""RT final probe: absolute plate levels per state (flatness ramp) and
sector-restricted true edge/shadow at a mid-ladder state with a real in-window
disc edge (st8, R~1121; sectors toward +x and +y stay in-window)."""
import numpy as np
from PIL import Image
from numpy.lib.stride_tricks import sliding_window_view

RT = "/tmp/lgcap-rt-1024"
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
L_ref = luma(ref)
H, W = L_ref.shape
cy, cx = 0.3 * H, 0.25 * W
yy, xx = np.mgrid[0:H, 0:W]
rad = np.sqrt((yy - cy) ** 2 + (xx - cx) ** 2)
ang_ok = ((xx - cx) / np.maximum(rad, 1) > 0.25) | ((yy - cy) / np.maximum(rad, 1) > 0.25)

print("== absolute RT interior level per state (mean_L / std_L, eroded NM footprint) ==")
print("st |  clear/dark |  clear/light | regular/dark | regular/light")
for st in (1, 2, 3, 4, 5, 6, 8, 10, 12, 14, 16):
    line = [f"{st:2d} |"]
    for variant, app in CASES:
        seq = f"sweep__wallpaper-transition__{variant}__{app}"
        f_n = load(f"{NM}/sweeps/{seq}/frame-{st:04d}.png")
        g = np.abs(f_n - ref).max(axis=2) > 6
        if g.sum() < 5000:
            line.append("      -      |")
            continue
        inter = eroded(g)
        if inter.sum() < 2000:
            line.append("    tiny     |")
            continue
        f_r = load(f"{RT}/sweeps/{seq}/frame-{st:04d}.png")
        L = luma(f_r)[inter]
        line.append(f" {L.mean():6.1f}/{L.std():5.1f} |")
    print("".join(line))

print("\n== sector-restricted edge & shadow at st8 (real disc edge, R~1121) ==")
for variant, app in CASES:
    seq = f"sweep__wallpaper-transition__{variant}__{app}"
    f_n = load(f"{NM}/sweeps/{seq}/frame-0008.png")
    f_r = load(f"{RT}/sweeps/{seq}/frame-0008.png")
    dL_rt = luma(f_r) - L_ref
    dL_nm = luma(f_n) - L_ref
    g = np.abs(f_n - ref).max(axis=2) > 6
    gs = g & ang_ok
    Rmax = rad[gs].max()
    rr = np.arange(Rmax - 200, min(Rmax + 80, 1430), 2)
    prof_rt, prof_nm, rv, cnt = [], [], [], []
    for r0 in rr:
        m = (rad >= r0) & (rad < r0 + 2) & ang_ok
        if m.sum() < 60:
            continue
        rv.append(r0 + 1)
        prof_rt.append(dL_rt[m].mean())
        prof_nm.append(dL_nm[m].mean())
    prof_rt = np.array(prof_rt); prof_nm = np.array(prof_nm); rv = np.array(rv)
    g_rt = np.abs(np.gradient(prof_rt)); g_nm = np.abs(np.gradient(prof_nm))
    i_rt = int(np.argmax(g_rt)); i_nm = int(np.argmax(g_nm))
    e_rt, e_nm = rv[i_rt], rv[i_nm]
    lin = prof_rt[max(0, i_rt - 25):max(1, i_rt - 6)].mean()
    lout = prof_rt[i_rt + 6:i_rt + 25].mean() if i_rt + 25 <= len(prof_rt) else prof_rt[-1]
    span = lin - lout
    w = float("nan")
    if abs(span) > 4:
        t = (prof_rt - lout) / span
        left = np.where(t[: i_rt + 1] > 0.9)[0]
        right = np.where(t[i_rt:] < 0.1)[0]
        if len(left) and len(right):
            w = rv[i_rt + right[0]] - rv[left[-1]]
    E = max(e_rt, e_nm)
    msha = (rad > E + 6) & (rad < E + 46) & ang_ok
    sha_rt = dL_rt[msha].mean(); sha_nm = dL_nm[msha].mean()
    m2 = (rad > E + 50) & (rad < E + 120) & ang_ok
    far_rt = dL_rt[m2].mean(); far_nm = dL_nm[m2].mean()
    print(f"  {variant}/{app}: edge NM@{e_nm:.0f} RT@{e_rt:.0f} (Δ{e_rt - e_nm:+.0f}px) width10-90={w:.0f}px")
    print(f"      annulus +6..+46: RT={sha_rt:+.2f} NM={sha_nm:+.2f} | +50..+120: RT={far_rt:+.2f} NM={far_nm:+.2f}")
    print(f"      prof RT[{rv[0]:.0f}..{rv[-1]:.0f}]: {' '.join(f'{v:+.0f}' for v in prof_rt[::8])}")
    print(f"      prof NM: {' '.join(f'{v:+.0f}' for v in prof_nm[::8])}")
