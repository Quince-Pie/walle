import numpy as np, json, sys, collections
from pathlib import Path
sys.path.insert(0, "/tmp/walle-parity/analysis")
src = open("/tmp/walle-parity/analysis/measure_chroma_warp_backdrop.py").read().replace('if __name__ == "__main__":\n    main()', '')
m = {}
exec(compile(src, "m", "exec"), m)
KLUMA=m["KLUMA"]; LINES=m["LINES"]; CONST=m["CONST"]; LADDER=m["LADDER"]; ANCHORS=m["ANCHORS"]
line_color=m["line_color"]; dominant=m["dominant"]; edge_profiles=m["edge_profiles"]
srgb_decode=m["srgb_decode"]; srgb_encode=m["srgb_encode"]
TO_PANEL=m["TO_PANEL"]; FROM_PANEL=m["FROM_PANEL"]; HALF=m["HALF"]; KN=m["KN"]; KW=m["KW"]

GATE_Y = np.array([0.0, 0.125, 0.25, 0.375, 0.5, 0.75, 1.0])
VGRID = np.linspace(0.0, 1.0, 201)
AP = "light"
c0 = CONST[AP]

def far_gated(N, p, gk):
    """far = U^-1(wide(U(N, Y_N)), Y_far); U(v,Y) = v + g(Y)(v^p - v)."""
    Yn = np.clip(N @ KLUMA, 0, 1)
    g = np.interp(Yn, GATE_Y, gk)
    Nv = np.clip(N, 0, 1)
    M = Nv + g[:, None] * (np.power(Nv, p) - Nv)
    Wm = np.stack([np.convolve(M[:, c], KW, mode="same") for c in range(3)], axis=1)
    Yf = np.clip(Wm @ KLUMA, 0, 1)
    gf = np.interp(Yf, GATE_Y, gk)
    # invert per pixel: U(v, Y)=v+gf(v^p-v) monotone in v for gf in [0, ~1.6], p<1
    Ug = VGRID[None, :] + gf[:, None] * (np.power(VGRID[None, :], p) - VGRID[None, :])
    far = np.zeros_like(Wm)
    for c in range(3):
        idx = np.clip(np.array([np.searchsorted(Ug[i], Wm[i, c]) for i in range(Wm.shape[0])]), 1, 200)
        x0 = VGRID[idx-1]; x1 = VGRID[idx]
        rows = np.arange(Wm.shape[0])
        y0 = Ug[rows, idx-1]; y1 = Ug[rows, idx]
        far[:, c] = x0 + (np.clip(Wm[:, c], None, None) - y0) / np.maximum(y1 - y0, 1e-9) * (x1 - x0)
    return np.clip(far, 0, 1)

# ---- colored instruments (forward-J)
COLOR = {}
SETS = {"rc": ("/tmp/lgcap-chroma-1024", "/tmp/lgcap-chroma-jac-1024"),
        "il": ("/tmp/lgcap-chroma-1024", "/tmp/lgcap-chroma-jac-1024"),
        "i5": ("/tmp/lgcap-chroma-iso-1024", "/tmp/lgcap-chroma-iso-1024")}
for tag, (fdir, pdir) in SETS.items():
    fshots, pshots = Path(fdir)/"shots", Path(pdir)/"shots"
    a, b = LINES[tag]
    ts = np.array(LADDER, float)
    T = np.stack([dominant(fshots / f"chroma-{tag}-t{t:03d}__circle-0500-center__regular__{AP}.png") for t in LADDER])
    Js = {}
    for t in ANCHORS:
        J = np.zeros((3,3)); base = line_color(t, a, b)
        for ci, cname in enumerate("RGB"):
            hi = dominant(pshots / f"chroma-{tag}-j{t:03d}-{cname}p__circle-0500-center__regular__{AP}.png")
            lo = dominant(pshots / f"chroma-{tag}-j{t:03d}-{cname}m__circle-0500-center__regular__{AP}.png")
            dp = min(255, base[ci]+24) - max(0, base[ci]-24)
            J[:, ci] = (hi - lo)/dp
        Js[t] = J
    prof, start, width = edge_profiles(fshots / f"chroma-{tag}-edge-x__circle-0500-center__regular__{AP}.png")
    count = prof.shape[0]
    pad = HALF; n = width + 2*pad
    B0 = np.tile(line_color(0,a,b), (n,1)); B0[pad+width//2:] = line_color(255,a,b)
    P = srgb_encode(np.clip(srgb_decode(B0/255.0) @ TO_PANEL.T, 0, None))
    N = np.stack([np.convolve(P[:,c], KN, mode="same") for c in range(3)], axis=1)
    ld = line_color(255,a,b) - line_color(0,a,b)
    COLOR[tag] = dict(T=T, ts=ts, Js=Js, prof=prof, N=N, pad=pad, start=start,
                      count=count, a=a, b=b, ld=ld)

def score_color(tag, p, gk):
    d = COLOR[tag]
    F = far_gated(d["N"], p, gk)
    wC, wL = c0["wC"], c0["wL"]
    mixed = wC*d["N"] + (1-wC)*F + ((wL-wC)*((d["N"]-F) @ KLUMA))[:, None]
    out = srgb_encode(np.clip(srgb_decode(np.clip(mixed,0,1)) @ FROM_PANEL.T, 0, None))*255.0
    Bh = out[d["pad"]+d["start"]:d["pad"]+d["start"]+d["count"]]
    t_star = np.clip((Bh - line_color(0,d["a"],d["b"])) @ d["ld"]/(d["ld"]@d["ld"])*255.0, 0, 255)
    Js = d["Js"]
    def J_at(t):
        t = float(np.clip(t, 64, 192))
        if t <= 128:
            w = (t-64)/64.0; return (1-w)*Js[64] + w*Js[128]
        w = (t-128)/64.0; return (1-w)*Js[128] + w*Js[192]
    oh = np.zeros_like(Bh)
    for i in range(d["count"]):
        Tt = np.array([np.interp(t_star[i], d["ts"], d["T"][:,c]) for c in range(3)])
        oh[i] = Tt + J_at(t_star[i]) @ (Bh[i] - line_color(t_star[i], d["a"], d["b"]))
    r = d["prof"] - oh
    return float(np.sqrt((r*r).mean()))

# ---- gray instrument (exact flat tables)
rows = json.loads(open("/tmp/walle-parity/analysis/results/flat-field-rounding-26.6.1.json").read())
pts = {int(r["background"].split("-")[1]): r["dominantRGB"][0] for r in rows
       if r["overlay"]=="regular" and r["appearance"]==AP and r["dominantFraction"]>0.9999}
glv = np.array(sorted(pts), float); gout = np.array([pts[int(l)] for l in glv], float)
GRAY = []
from PIL import Image
for ax in ("x","y"):
    px = np.asarray(Image.open(f"/tmp/lgcap-static-partial/shots/edge-{ax}__circle-0500-center__regular__{AP}.png").convert("RGB")).astype(float)
    if ax == "y": px = px.transpose(1,0,2)
    h,w,_ = px.shape; cy,cx = h//2,w//2
    meas = px[cy-40:cy+40, cx-220:cx+220,:].mean(axis=(0,2))
    pad = HALF
    b = np.zeros(w+2*pad); b[pad+w//2:] = 1.0
    N1 = np.convolve(b, KN, mode="same")
    GRAY.append(dict(meas=meas, N=np.stack([N1]*3, axis=1), lo=pad+(cx-220)))

def score_gray(p, gk):
    sq = cnt = 0.0
    for g in GRAY:
        F = far_gated(g["N"], p, gk)[:,0]
        N1 = g["N"][:,0]
        u = np.clip(c0["wL"]*N1 + (1-c0["wL"])*F, 0, 1)  # gray: luma == channel
        pred = np.interp(255.0*u[g["lo"]:g["lo"]+g["meas"].size], glv, gout)
        r = g["meas"] - pred
        sq += float((r*r).sum()); cnt += r.size
    return float(np.sqrt(sq/cnt))

def objective(params):
    p = params[0]; gk = np.clip(params[1:], 0.0, 1.6)
    if not (0.1 <= p <= 1.5): return 1e9, None
    parts = {t: score_color(t, p, gk) for t in ("rc","il","i5")}
    parts["gray"] = score_gray(p, gk)
    return sum(parts.values()), parts

params = np.array([0.40, 1.0, 1.0, 0.8, 0.4, 0.0, 0.0, 0.0])
best, parts = objective(params)
print("init:", round(best,3), {k: round(v,2) for k,v in parts.items()})
step = 0.15
for sweep in range(40):
    improved = False
    for i in range(len(params)):
        for d in (+step, -step):
            trial = params.copy(); trial[i] += d
            v, pp = objective(trial)
            if v < best:
                best, params, parts, improved = v, trial, pp, True
    if not improved:
        step *= 0.5
        if step < 0.01: break
print("FIT:", round(best,3), {k: round(v,2) for k,v in parts.items()})
print("p =", round(params[0],3), " gate:", " ".join(f"{v:.2f}" for v in np.clip(params[1:],0,1.6)))
# references
for name, pr in (("no-warp", (1.0, np.zeros(7))), ("sandwich-p0.40-ungated", (0.40, np.ones(7)))):
    v, pp = objective(np.concatenate([[pr[0]], pr[1]]))
    print(f"{name}: {round(v,3)}", {k: round(x,2) for k,x in pp.items()})
