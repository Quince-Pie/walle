#!/usr/bin/env python3
"""Characterize the Reduce Transparency rendition vs the normal Liquid Glass material.

Paired comparison: lgcap-rt-1024 (reduceTransparency=1) vs lgcap-natural-1024
(reduceTransparency=0), same rig, same natural-statistics backgrounds, same states.
"""
import numpy as np
from PIL import Image
import json, sys

RT = "/tmp/lgcap-rt-1024"
NM = "/tmp/lgcap-natural-1024"
CASES = [("clear", "dark"), ("clear", "light"), ("regular", "dark"), ("regular", "light")]
STATES = [8, 16]  # mid and final sweep states


def load(p):
    return np.asarray(Image.open(p).convert("RGB"), dtype=np.float64)


def luma(a):
    return 0.2126 * a[..., 0] + 0.7152 * a[..., 1] + 0.0722 * a[..., 2]


ref_nm = load(f"{NM}/reference/dynamic-natural-field.png")
ref_rt = load(f"{RT}/reference/dynamic-natural-field.png")
print(f"reference identity check: mean|refRT-refNM| = {np.abs(ref_rt-ref_nm).mean():.3f} codes")

for variant, app in CASES:
    seq = f"sweep__wallpaper-transition__{variant}__{app}"
    for st in STATES:
        f_nm = load(f"{NM}/sweeps/{seq}/frame-{st:04d}.png")
        f_rt = load(f"{RT}/sweeps/{seq}/frame-{st:04d}.png")
        if f_nm.shape != f_rt.shape:
            print(f"{variant}/{app} st{st}: SHAPE MISMATCH {f_nm.shape} vs {f_rt.shape}")
            continue
        ref = ref_nm if ref_nm.shape == f_nm.shape else None
        if ref is None:
            # window crop differs from full reference; use RT-vs-NM footprint only
            d_pair = np.abs(f_rt - f_nm).max(axis=2)
            glass = d_pair > 6
            print(f"{variant}/{app} st{st}: no matching ref; pairdiff>6 px={glass.sum()}")
            continue
        d_nm = np.abs(f_nm - ref).max(axis=2)
        d_rt = np.abs(f_rt - ref).max(axis=2)
        glass = d_nm > 6  # glass + rim + shadow footprint from the normal material
        if glass.sum() < 500:
            print(f"{variant}/{app} st{st}: footprint too small ({glass.sum()} px)")
            continue
        # erode to interior: distance from footprint edge > 12 px via simple box trick
        from numpy.lib.stride_tricks import sliding_window_view
        g = glass.astype(np.uint8)
        k = 25
        pad = np.pad(g, k // 2, mode="constant")
        win = sliding_window_view(pad, (k, k))
        interior = win.min(axis=(2, 3)).astype(bool)
        if interior.sum() < 500:
            interior = glass
        yx = np.argwhere(interior)
        cy, cx = yx.mean(axis=0)

        def stats(fr, m):
            dv = fr - ref
            mean_ch = dv[m].mean(axis=0)
            det_f = luma(fr)[m] - luma(fr)[m].mean()
            det_r = luma(ref)[m] - luma(ref)[m].mean()
            denom = det_f.std() * det_r.std()
            corr = float((det_f * det_r).mean() / denom) if denom > 1e-9 else 0.0
            return mean_ch, float(luma(fr)[m].std()), corr

        nm_mean, nm_std, nm_corr = stats(f_nm, interior)
        rt_mean, rt_std, rt_corr = stats(f_rt, interior)
        pair = float(np.abs(f_rt - f_nm)[interior].mean())
        # footprint radius compare (does RT keep the same disc?)
        r_nm = np.sqrt(glass.sum() / np.pi)
        g_rt = d_rt > 6
        r_rt = np.sqrt(g_rt.sum() / np.pi)
        print(
            f"{variant}/{app} st{st}: interior_px={interior.sum()} "
            f"R(nm/rt)={r_nm:.0f}/{r_rt:.0f} | "
            f"NORMAL Δref=({nm_mean[0]:+.1f},{nm_mean[1]:+.1f},{nm_mean[2]:+.1f}) std={nm_std:.1f} bgcorr={nm_corr:+.2f} | "
            f"RT Δref=({rt_mean[0]:+.1f},{rt_mean[1]:+.1f},{rt_mean[2]:+.1f}) std={rt_std:.1f} bgcorr={rt_corr:+.2f} | "
            f"RTvsNM={pair:.1f}"
        )
