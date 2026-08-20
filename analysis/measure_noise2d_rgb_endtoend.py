import numpy as np
from pathlib import Path
from PIL import Image

S = Path("/tmp/nix-shell.8sawvl/claude-1000/-tmp-walle/27a9e69b-bd18-4f7f-b663-08e463d933c8/scratchpad/noisergb")
SHOTS = Path("/tmp/lgcap-static-partial/shots")
yy, xx = np.mgrid[0:2048, 0:2048]
MASK = (np.hypot(xx-1024, yy-1024) < 440)

def load_bgra(p):
    raw = np.fromfile(p, dtype=np.uint8)
    return raw.reshape(2048, 2048, 4)[..., [2,1,0]].astype(np.float64)

for bg in ("noise-rgb-m064-a032-b0016-train", "noise-rgb-m128-a032-b0016-train",
           "noise-rgb-m192-a032-b0016-train", "noise-rgb-a064-train"):
    for ap in ("light", "dark"):
        apple_p = SHOTS / f"{bg}__circle-0500-center__regular__{ap}.png"
        if not apple_p.exists(): continue
        apple = np.asarray(Image.open(apple_p).convert("RGB")).astype(np.float64)
        row = []
        for mode in ("gauss", "warp", "flipcube", "gated"):
            wp = S / f"{bg}__{ap}__{mode}" / "composition-state-0000.bgra"
            if not wp.exists():
                row.append(f"{mode}     -")
                continue
            walle = load_bgra(wp)
            d = (apple - walle)[MASK]
            rms = np.sqrt((d*d).mean())
            per = np.sqrt((d*d).mean(axis=0))
            row.append(f"{mode} {rms:5.2f} ({per[0]:.1f}/{per[1]:.1f}/{per[2]:.1f})")
        print(f"{ap:5s} {bg:32s} " + "  ".join(row))
