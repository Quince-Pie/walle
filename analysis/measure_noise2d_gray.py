import numpy as np, json, sys
from math import erf, sqrt
from pathlib import Path
from PIL import Image

SHOTS = Path("/tmp/lgcap-static-partial/shots")
HALF = 1600
PAD = HALF
SN, SW = 14.188, 329.807
WL = {"light": 0.8846, "dark": 0.5164}
KNOTS_U = np.array([0.0, 0.05, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 0.95, 1.0])
LUT = {ap: np.load(f"/tmp/warp-knots-{ap}.npy") for ap in ("light", "dark")}

def gk(sigma):
    e = np.arange(-HALF, HALF + 2) - 0.5
    cdf = np.array([0.5*(1+erf(x/(sigma*sqrt(2)))) for x in e])
    k = np.diff(cdf)
    return k / k.sum()

def blur2d(img, kern):
    """Separable FFT convolution with edge padding; center crop back."""
    p = np.pad(img, PAD, mode="edge")
    n = p.shape[0]
    kfull = np.zeros(n)
    kfull[:HALF+1] = kern[HALF:]
    kfull[-HALF:] = kern[:HALF]
    K = np.fft.rfft(kfull)
    p = np.fft.irfft(np.fft.rfft(p, axis=0) * K[:, None], n=n, axis=0)
    p = np.fft.irfft(np.fft.rfft(p, axis=1) * K[None, :], n=n, axis=1)
    return p[PAD:-PAD, PAD:-PAD]

KN, KW = gk(SN), gk(SW)

rows = json.loads(open("/tmp/walle-parity/analysis/results/flat-field-rounding-26.6.1.json").read())
def gray_T(ap):
    pts = {int(r["background"].split("-")[1]): r["dominantRGB"][0] for r in rows
           if r["overlay"]=="regular" and r["appearance"]==ap and r["dominantFraction"]>0.9999}
    lv = np.array(sorted(pts), float)
    return lv, np.array([pts[int(l)] for l in lv], float)

def warp(v, hyp, ap):
    v = np.clip(v, 0, 1)
    if hyp == "none": return v
    if hyp == "power": return np.power(v, 0.40 if ap=="light" else 1.34)
    if hyp == "flip3": return 1-np.power(1-v, 3.0)
    if hyp == "lut": return np.interp(v, KNOTS_U, LUT[ap])
    if hyp == "gated":
        p, A = (0.285, 1.045) if ap=="light" else (1.313, 1.983)
        g = np.clip(A*v, 0, 0.98)
        return v*(1-g) + g*np.power(v, p)
def unwarp(v, hyp, ap):
    v = np.clip(v, 0, 1)
    if hyp == "none": return v
    if hyp == "power": return np.power(v, 1/(0.40 if ap=="light" else 1.34))
    if hyp == "flip3": return 1-np.power(1-v, 1/3.0)
    if hyp == "lut": return np.interp(v, LUT[ap], KNOTS_U)
    if hyp == "gated":
        p, A = (0.285, 1.045) if ap=="light" else (1.313, 1.983)
        vg = np.linspace(0, 1, 501)
        out = np.zeros_like(v)
        # per-pixel gate from the warped field's own luma == value on gray
        g = np.clip(A*v, 0, 0.98)
        # solve (1-g)f + g f^p = v: iterate bisection vectorized
        lo = np.zeros_like(v); hi = np.ones_like(v)
        for _ in range(24):
            mid = 0.5*(lo+hi)
            val = (1-g)*mid + g*np.power(mid, p)
            below = val < v
            lo = np.where(below, mid, lo)
            hi = np.where(below, hi, mid)
        return 0.5*(lo+hi)

BGS = ["noise-gray-m064-a032-b0016-train", "noise-gray-m128-a032-b0016-train",
       "noise-gray-m192-a032-b0016-train", "noise-gray-a064-train", "noise-gray"]
HYPS = ("none", "power", "flip3", "lut", "gated")
R = 420   # central analysis half-size (well inside the 1000px circle)

for ap in ("light", "dark"):
    lv, outT = gray_T(ap)
    for bg in BGS:
        pn = SHOTS / f"{bg}__circle-0500-center__none__{ap}.png"
        pr = SHOTS / f"{bg}__circle-0500-center__regular__{ap}.png"
        if not (pn.exists() and pr.exists()):
            continue
        backdrop = np.asarray(Image.open(pn).convert("L")).astype(float) / 255.0
        apple = np.asarray(Image.open(pr).convert("RGB")).astype(float).mean(axis=2)
        h, w = backdrop.shape
        cy, cx = h//2, w//2
        N = blur2d(backdrop, KN)
        res = []
        for hyp in HYPS:
            F = unwarp(np.clip(blur2d(warp(N, hyp, ap), KW), 0, 1), hyp, ap)
            mix = np.clip(WL[ap]*N + (1-WL[ap])*F, 0, 1)
            pred = np.interp(255.0*mix, lv, outT)
            d = (apple - pred)[cy-R:cy+R, cx-R:cx+R]
            res.append(f"{hyp} {np.sqrt((d*d).mean()):5.2f}")
        print(f"{ap:5s} {bg:34s} " + "  ".join(res))
