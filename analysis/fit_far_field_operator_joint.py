import numpy as np, itertools, json, sys
from math import erf, sqrt
from pathlib import Path
src = open("/tmp/walle-parity/analysis/measure_chroma_warp_backdrop.py").read().replace('if __name__ == "__main__":\n    main()', '')
M = {}
exec(compile(src, "m", "exec"), M)
KLUMA=M["KLUMA"]; LINES=M["LINES"]; CONST=M["CONST"]; LADDER=M["LADDER"]; ANCHORS=M["ANCHORS"]
line_color=M["line_color"]; dominant=M["dominant"]; edge_profiles=M["edge_profiles"]
srgb_decode=M["srgb_decode"]; srgb_encode=M["srgb_encode"]
TO_PANEL=M["TO_PANEL"]; FROM_PANEL=M["FROM_PANEL"]; HALF=M["HALF"]; KN=M["KN"]; KW=M["KW"]
AP = sys.argv[1]
c0 = CONST[AP]; wL, wC = c0["wL"], c0["wC"]
EXPS = [t for t in sorted(itertools.product(range(4), repeat=3), key=lambda t:(sum(t),t)) if 2 <= sum(t) <= 3]
K = len(EXPS)
EXP_ARR = np.array(EXPS)
def basis(x):
    u = np.clip(x, 0, 1.3)
    return np.stack([np.prod(u**e, axis=-1) for e in EXP_ARR], axis=-1)

def V(x, C):
    return x + (basis(x) @ C)/255.0
def Vinv(y, C):
    x = np.clip(y, 0, 1.3).copy()
    n = x.reshape(-1,3).shape[0]
    for _ in range(7):
        f = (V(x, C) - y).reshape(-1,3)
        J = np.zeros((n,3,3))
        base_v = V(x, C).reshape(-1,3)
        xf = x.reshape(-1,3)
        for c in range(3):
            d = np.zeros(3); d[c] = 1e-3
            J[:,:,c] = (V(xf+d, C) - base_v)/1e-3
        try:
            step = np.linalg.solve(J, f[..., None])[..., 0]
        except np.linalg.LinAlgError:
            step = np.einsum("nij,nj->ni", np.linalg.pinv(J), f)
        x = np.clip((xf - step).reshape(x.shape), -0.3, 1.5)
    return x
def to_panel(c):
    return srgb_encode(np.clip(srgb_decode(np.clip(c,0,255)/255.0) @ TO_PANEL.T, 0, None))
def from_panel_codes(u):
    return srgb_encode(np.clip(srgb_decode(np.clip(u,0,1)) @ np.linalg.inv(TO_PANEL).T, 0, None))*255.0

# ------- context setups (precomputed) -------
CTX = []
# cube
def axis_weights(sigma):
    edges = [(-(-k*2048//27), -(-(k+1)*2048//27)-1) for k in range(27)]
    centers = [(a+b)/2.0 for a,b in edges]
    W = np.zeros((27,27)); s2 = sigma*sqrt(2)
    Phi = lambda x: 0.5*(1+erf(x/s2))
    for i, cx in enumerate(centers):
        for j,(a,b) in enumerate(edges):
            lo = -1e9 if j==0 else a-0.5
            hi = 1e9 if j==26 else b+0.5
            W[i,j] = Phi(hi-cx)-Phi(lo-cx)
    return W/W.sum(axis=1, keepdims=True)
Wx27 = axis_weights(sqrt(329.807**2+14.188**2)); Nx27 = axis_weights(14.188)
levels = np.array([0,32,64,96,128,160,192,224,255], float)
tiles = np.zeros((27,27,3))
for row in range(27):
    for col in range(27):
        idx = row*27+col
        tiles[row,col] = (levels[idx%9], levels[(idx//9)%9], levels[(idx//81)%9])
P27 = to_panel(tiles)
N27 = np.einsum("ab,bck,dc->adk", Nx27, P27, Nx27)
mm = json.load(open("/tmp/walle-parity/analysis/results/material_matrices.json"))
rec = next(r for r in mm["records"] if r["variant"]=="regular" and r["appearance"]==AP)
TEXP = np.array(rec["termExponents"]); TCOF = np.array(rec["coefficients"])
def Tmat(x):   # sRGB codes -> output codes
    u = np.clip(x,0,300)/255.0
    return (np.prod(u[..., None, :] ** TEXP[None,None,:,:], axis=-1) @ TCOF)*255.0 if u.ndim==3 else (np.prod(u[..., None, :] ** TEXP[None,:,:], axis=-1) @ TCOF)*255.0
SHOTS = Path("/tmp/lgcap-static-partial/shots")
from PIL import Image
apple_cube = np.asarray(Image.open(SHOTS/f"color-cube-9__circle-4000-center__regular__{AP}.png").convert("RGB")).astype(float)
tilepx = 2048/27.0
acube = np.zeros((27,27,3))
for ty in range(27):
    for tx in range(27):
        cy = int((ty+0.5)*tilepx); cx = int((tx+0.5)*tilepx)
        acube[ty,tx] = apple_cube[cy-8:cy+8, cx-8:cx+8].mean(axis=(0,1))
def cube_resid(C):
    Wn = V(np.clip(N27,0,1), C)
    G = np.einsum("ab,bck,dc->adk", Wx27, Wn, Wx27)
    F = Vinv(np.clip(G,0,1.3), C)
    mix = wC*N27 + (1-wC)*F + ((wL-wC)*((N27-F)@KLUMA))[...,None]
    pred = Tmat(from_panel_codes(mix))
    return (acube - pred).reshape(-1)
CTX.append(("cube", cube_resid))
# lines
SETS = {"rc": ("/tmp/lgcap-chroma-1024", "/tmp/lgcap-chroma-jac-1024"),
        "il": ("/tmp/lgcap-chroma-1024", "/tmp/lgcap-chroma-jac-1024"),
        "i5": ("/tmp/lgcap-chroma-iso-1024", "/tmp/lgcap-chroma-iso-1024")}
ANCH = np.array(ANCHORS, float)
for tag,(fdir,pdir) in SETS.items():
    fshots, pshots = Path(fdir)/"shots", Path(pdir)/"shots"
    a, b = LINES[tag]
    T = np.stack([dominant(fshots / f"chroma-{tag}-t{t:03d}__circle-0500-center__regular__{AP}.png") for t in LADDER])
    Jarr = []
    for t in ANCHORS:
        J = np.zeros((3,3)); base = line_color(t, a, b)
        for ci, cname in enumerate("RGB"):
            hi = dominant(pshots / f"chroma-{tag}-j{t:03d}-{cname}p__circle-0500-center__regular__{AP}.png")
            lo = dominant(pshots / f"chroma-{tag}-j{t:03d}-{cname}m__circle-0500-center__regular__{AP}.png")
            dp = min(255, base[ci]+24) - max(0, base[ci]-24)
            J[:, ci] = (hi - lo)/dp
        Jarr.append(J)
    Jarr = np.array(Jarr)
    prof, start, width = edge_profiles(fshots / f"chroma-{tag}-edge-x__circle-0500-center__regular__{AP}.png")
    count = prof.shape[0]
    pad = HALF; n = width + 2*pad
    B0 = np.tile(line_color(0,a,b), (n,1)); B0[pad+width//2:] = line_color(255,a,b)
    Pl = to_panel(B0)
    Nl = np.stack([np.convolve(Pl[:,c], KN, mode="same") for c in range(3)], axis=1)
    l0 = line_color(0,a,b); ld = line_color(255,a,b) - l0
    lines_t = np.stack([line_color(t, a, b) for t in range(256)])
    def line_resid(C, Nl=Nl, prof=prof, T=T, Jarr=Jarr, l0=l0, ld=ld, lines_t=lines_t,
                   pad=pad, start=start, count=count):
        Wn = V(np.clip(Nl,0,1), C)
        G = np.stack([np.convolve(Wn[:,c], KW, mode="same") for c in range(3)], axis=1)
        F = Vinv(np.clip(G,0,1.3), C)
        mixed = wC*Nl + (1-wC)*F + ((wL-wC)*((Nl-F)@KLUMA))[:,None]
        Bh = from_panel_codes(np.clip(mixed,0,1))[pad+start:pad+start+count]
        t_star = np.clip((Bh - l0) @ ld/(ld@ld)*255.0, 0, 255)
        tcl = np.clip(t_star, ANCH[0], ANCH[-1])
        Jpix = np.empty((count,3,3))
        for r_ in range(3):
            for cc in range(3):
                Jpix[:, r_, cc] = np.interp(tcl, ANCH, Jarr[:, r_, cc])
        Tt = np.stack([np.interp(t_star, np.array(LADDER,float), T[:, c]) for c in range(3)], axis=1)
        base_ = lines_t[np.clip(t_star.astype(int), 0, 255)]
        oh = Tt + np.einsum("nij,nj->ni", Jpix, Bh - base_)
        return (prof - oh).reshape(-1)
    CTX.append((tag, line_resid))
# gray edges
rows = json.loads(open("/tmp/walle-parity/analysis/results/flat-field-rounding-26.6.1.json").read())
pts = {int(r["background"].split("-")[1]): r["dominantRGB"][0] for r in rows
       if r["overlay"]=="regular" and r["appearance"]==AP and r["dominantFraction"]>0.9999}
glv = np.array(sorted(pts), float); gout = np.array([pts[int(x)] for x in glv], float)
for axn in ("x","y"):
    px = np.asarray(Image.open(f"/tmp/lgcap-static-partial/shots/edge-{axn}__circle-0500-center__regular__{AP}.png").convert("RGB")).astype(float)
    if axn == "y": px = px.transpose(1,0,2)
    h,w,_ = px.shape; cy,cx = h//2,w//2
    meas = px[cy-40:cy+40, cx-220:cx+220,:].mean(axis=(0,2))
    pad = HALF
    b1 = np.zeros(w+2*pad); b1[pad+w//2:] = 1.0
    N1 = np.stack([np.convolve(b1, KN, mode="same")]*3, axis=1)
    def gray_resid(C, N1=N1, meas=meas, lo_i=pad+(cx-220)):
        Wn = V(np.clip(N1,0,1), C)
        G = np.stack([np.convolve(Wn[:,c], KW, mode="same") for c in range(3)], axis=1)
        F = Vinv(np.clip(G,0,1.3), C)
        mixed = wC*N1 + (1-wC)*F + ((wL-wC)*((N1-F)@KLUMA))[:,None]
        # gray referee via exact flat tables on the luma of the mixed field
        u = np.clip(mixed @ KLUMA, 0, 1)
        pred = np.interp(255.0*u[lo_i:lo_i+meas.size], glv, gout)
        return (meas - pred)
    CTX.append((f"gray{axn}", gray_resid))

def residual(C):
    return np.concatenate([f(C) for _, f in CTX])
def report(C, tagline):
    print(tagline)
    for name, f in CTX:
        r = f(C)
        print(f"   {name:5s}: rms {np.sqrt((r*r).mean()):6.2f}")
    sys.stdout.flush()

C = np.zeros((K,3))
r0 = residual(C)
report(C, f"{AP} V=id baseline:")
lam = 1.0
cost = (r0*r0).sum()
for it in range(12):
    # numeric Jacobian
    Jm = np.zeros((r0.size, K*3))
    eps = 2.0
    for i in range(K):
        for c in range(3):
            Cp = C.copy(); Cp[i,c] += eps
            Jm[:, i*3+c] = (residual(Cp) - r0)/eps
    # Levenberg-Marquardt step
    A = Jm.T @ Jm
    g = Jm.T @ r0
    for _ in range(8):
        try:
            step = np.linalg.solve(A + lam*np.diag(np.diag(A) + 1e-6), -g)
        except np.linalg.LinAlgError:
            lam *= 10; continue
        Cn = C + step.reshape(K,3)
        rn = residual(Cn)
        cn = (rn*rn).sum()
        if cn < cost:
            C, r0, cost = Cn, rn, cn
            lam = max(lam*0.5, 1e-3)
            break
        lam *= 4
    print(f"iter {it}: cost rms {np.sqrt(cost/r0.size):.3f} lam {lam:.3g}")
    sys.stdout.flush()
report(C, f"{AP} honest joint V:")
np.save(f"/tmp/honest-V-{AP}.npy", C)
