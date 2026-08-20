#!/usr/bin/env python3
"""The R8-keyed boundary blend-curve instrument (session 195).

For every AA boundary pixel (0 < m < 255 on walle's corpus-exact mask), read
the pixel's EFFECTIVE blend factor - its luma normalized between its radial
inside (m=255) and outside (m=0) neighbours at +-3 px - for Apple's frame
and walle's render, binned by walle's coverage m.  This found that the
m~0.5 discontinuity in the boundary residual was WALLE's own: the lens
onset was step(0, insideRim) at the analytic circle, switching a ~70-100px
inward displacement across one pixel, where Apple's curve moves smoothly -
true coverage-weighted AA.  Fixed by coverage-centroid sampling in
liquid_glass.slang; this instrument referees that pixel class.

Usage: measure_boundary_blend_curve.py <scorer work dir with coded renders>
"""
import json, sys, numpy as np
from pathlib import Path
from PIL import Image

WORK = Path(sys.argv[1]); CAP = Path("/tmp/lgcap-2048")
EXTENT = 2048; CENTRE = (512.0, 614.4)
manifest = json.loads((CAP / "manifest.json").read_text())
LUMA = np.array([0.2126, 0.7152, 0.0722])

def load_bgra(path):
    raw = np.fromfile(path, dtype=np.uint8)
    return raw.reshape(EXTENT, EXTENT, 4)[..., [2, 1, 0]].astype(np.int16)

for seq_id in ("sweep__wallpaper-transition__regular__light",
               "sweep__wallpaper-transition__regular__dark"):
    sweep = next(s for s in manifest["sweepSequences"] if s["id"] == seq_id)
    frames = [f for f in sweep["frames"] if f.get("stable", True)]
    scored = np.ones((EXTENT, EXTENT), bool)
    for box in sweep.get("analysisExclusionPixels") or ():
        scored[box["y"]:box["y"]+box["height"], box["x"]:box["x"]+box["width"]] = False
    yy, xx = np.mgrid[0:EXTENT, 0:EXTENT]
    dy = yy - CENTRE[1]; dx = xx - CENTRE[0]
    rr = np.hypot(dx, dy); rr[rr == 0] = 1
    ux, uy = dx/rr, dy/rr
    NB = 16
    sums_a = np.zeros(NB); sums_w = np.zeros(NB); cnts = np.zeros(NB)
    m_res = np.zeros((8,3)); m_cnt = np.zeros(8); tot = np.zeros(3); tot_n = 0
    for idx in range(6, 17):
        apple = np.asarray(Image.open(CAP / frames[idx]["file"]).convert("RGB")).astype(np.float64)
        walle = load_bgra(WORK / seq_id / f"composition-state-{idx:04d}.bgra").astype(np.float64)
        mask = np.fromfile(WORK / seq_id / f"state-{idx:04d}.r8", dtype=np.uint8).reshape(EXTENT, EXTENT)
        B = (mask > 0) & (mask < 255) & scored
        by, bx = np.nonzero(B)
        r = (apple[by,bx] - walle[by,bx])
        m8 = np.minimum((mask[by,bx]/32).astype(int), 7)
        for c in range(3): np.add.at(m_res[:,c], m8, r[:,c])
        np.add.at(m_cnt, m8, 1)
        tot += r.mean(axis=0)*r.shape[0]; tot_n += r.shape[0]
        ins_x = np.clip((bx - 3*ux[by,bx]).round().astype(int), 0, EXTENT-1)
        ins_y = np.clip((by - 3*uy[by,bx]).round().astype(int), 0, EXTENT-1)
        out_x = np.clip((bx + 3*ux[by,bx]).round().astype(int), 0, EXTENT-1)
        out_y = np.clip((by + 3*uy[by,bx]).round().astype(int), 0, EXTENT-1)
        ok = (mask[ins_y,ins_x] == 255) & (mask[out_y,out_x] == 0)
        aY = apple @ LUMA; wY = walle @ LUMA
        inn = aY[ins_y,ins_x]; out = aY[out_y,out_x]
        me_a = (aY[by,bx]-out)/np.where(np.abs(inn-out)<1e-9,1,(inn-out))
        inn_w = wY[ins_y,ins_x]; out_w = wY[out_y,out_x]
        me_w = (wY[by,bx]-out_w)/np.where(np.abs(inn_w-out_w)<1e-9,1,(inn_w-out_w))
        g = ok & (np.abs(inn-out)>40) & (np.abs(inn_w-out_w)>40) & (me_a>-0.5) & (me_a<1.5)
        mb = np.minimum((mask[by,bx]/255.0*NB).astype(int), NB-1)
        np.add.at(sums_a, mb[g], me_a[g]); np.add.at(sums_w, mb[g], me_w[g]); np.add.at(cnts, mb[g], 1)
    mean = tot/tot_n
    print(f"== {seq_id.split('__')[-1]}: boundary mean RGB ({mean[0]:+.2f},{mean[1]:+.2f},{mean[2]:+.2f})")
    print("   resid vs m:", " ".join(f"{(m_res[i]/max(m_cnt[i],1)).mean():+6.2f}" for i in range(8)))
    print("   blend curve (m: apple vs walle):")
    for i in range(NB):
        if cnts[i] < 50: continue
        print(f"     {(i+0.5)/NB:.3f}  {sums_a[i]/cnts[i]:7.3f}  {sums_w[i]/cnts[i]:7.3f}")
