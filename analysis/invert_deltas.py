import sys, json
sys.path[:0] = ["/tmp/walle"]
from pathlib import Path
import numpy as np
import _sweep_fused_join_lattice as m
import _solve_clip_varyings as sv
import _joint_stage_sweep as js
f32 = np.float32
_lib = Path("/tmp/nix-shell.PFgUGF/claude-1000/-tmp-walle/4ccfbce8-33b2-4b5f-8e29-93486397c8a4/scratchpad/fit_clip_values.py").read_text()
exec(_lib.split("results = {}")[0])

# geometry: triple (cv0, v1, cv3) snapped
fx3 = [(0, 163840), (118784, 163840), (0, 235520)]
hws = [(0xba800000, 0xb2c00060), (0x3a800002, 0xbaffffc0), (0xaf4971bc, 0x3b000040)]

def ulp_shift(x, k):
    if x == 0.0:
        return float(m.bits_f32(k)) if k >= 0 else -float(m.bits_f32(-k))
    b = m.f32_bits(x)
    key = m.ordered_key(b) + k
    return m.bits_f32(m.key_to_bits(key))

# forward: given anchor value av and deltas d1 (v1-cv0), d3 (cv3-cv0) compute (A,B)
def forward(av, v1, v3):
    return slope_words(fx3, [av, v1, v3])

# per ctx: expected values
t0 = 0.546875          # cv0 on v0->v1
t3 = 0.4531187415122986  # cv3 on v2->v0
exp_vals = [
    (1-t0, 0.0, t3),      # ctx0: v0-basis: cv0 = 1-t0, v1 = 0, cv3 = lerp(v2=0 -> v0=1, t3) = t3
    (t0,   1.0, 0.0),     # ctx1
    (0.0,  0.0, 1-t3),    # ctx2
]
for ctx in range(3):
    av0, v10, v30 = [float(f32(v)) for v in exp_vals[ctx]]
    hw = hws[ctx]
    sols = []
    R = 300
    # sweep each value independently around expectation; sweep zero values over tiny range too
    cands_a = [ulp_shift(av0, k) for k in range(-R, R+1)] if av0 != 0 else [0.0] + [ulp_shift(0.0, k) for k in list(range(1, 40)) + [-k for k in range(1, 40)]]
    # to keep the sweep tractable, first find (v1, v3) with anchor fixed, then refine anchor
    best = None
    for da in (0,):
        av = av0
        for k1 in range(-R, R+1):
            v1 = ulp_shift(v10, k1) if v10 != 0 else (0.0 if k1 == 0 else ulp_shift(0.0, k1))
            if v10 == 0 and abs(k1) > 60: continue
            for k3 in range(-R, R+1):
                v3 = ulp_shift(v30, k3) if v30 != 0 else (0.0 if k3 == 0 else ulp_shift(0.0, k3))
                if v30 == 0 and abs(k3) > 60: continue
                got = forward(av, v1, v3)
                if got == hw:
                    sols.append((0, k1, k3, av, v1, v3))
                    if len(sols) > 4: break
            if len(sols) > 4: break
    print(f"ctx{ctx}: expected (av,v1,v3)=({av0!r},{v10!r},{v30!r})")
    for s in sols[:5]:
        print(f"   offsets (a,{s[1]},{s[2]}) values=({s[3]!r},{s[4]!r},{s[5]!r})")
    if not sols: print("   NO solution with anchor at expectation")
