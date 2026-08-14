# A2 presentation plane solver: brief (task #4)

## Goal

Close the 18 presentation-transform corpus residuals by solving, per
state and per transfer triangle, Apple's f16 "secondary" selection
plane, then validating all 18 residual directions and deriving the
input-only generation rule.

## Established (TASK.md later-41..46 — read them first)

- Corpus byte model: byte = round255(h16(h16(primary) * secondary)),
  secondary in {0x3C00, 0x3BFF} per pixel.
- Apple's secondary = f16-RTZ of a per-pixel interpolated "deficit
  plane" value: secondary = 0x3BFF iff plane(x, y) < 1.0. The plane is
  the AGX-interpolated alpha varying of the CA transfer draw
  (pipeline PBGRAXm_A2Xghfc), whose iter output rounds to f32 at the
  1 - 2^-25 boundary (hw-verified: captures a2-transfer-values-* and
  a2-transfer-residue-plan-v1).
- The transfer mesh per state: /tmp/walle-analysis/A2-geometry-sweep-
  v74/state-N/reveal-mask-trace.json (vertexStreamHex: 48B stride,
  slots [0,1] = posX,posY f32; 16 verts; "indices" = 48 idx, 16 tris).
  All f16 varying inputs are exactly 1.0; the deficit comes from the
  CA vertex shader's arithmetic (unknown, to be recovered as a rule).
- State-42 hard constraints (from build/_residual_list.txt):
  plane < 1 at (1793,2),(1794,5),(1795,7),(1799,16),(1801,18),
  (1801,20),(1803,25),(1805,29),(1806,31),(1837,103) [all apple=walle-1,
  triangle 2 = verts (1933,-806.5),(1933,614.5),(512,614.5)];
  plane >= 1 at (1838,106) [apple=walle+1, also tri 2] and at
  (259,2011) [tri 6 = (512,614.5),(512,2035.5),(-909,2035.5)].
  NOTE (1837,103) and (1838,106) share tile (57,3): the plane's
  crossing line passes between them (oblique: a pure-y deficit cannot
  do it — see later-46 algebra).
- Other states' single residuals: 31:(249,1628)+1, 33:(70,1639)+1,
  40:(1730,28)-1, 41:(1897,606)-1, 58:(2033,1851)-1, 60:(2042,1946)-1.
  (+1 means apple = walle+1: walle picked the 0x3BFF-equivalent and
  apple's plane >= 1; -1 means apple's plane < 1 while walle used 1.0.)

## The dense-constraint method (the actual task)

The 12+6 residuals under-constrain the planes. Densify:
1. Reproduce per-pixel PRIMARY f16 and WALLE's own secondary for each
   residual state. Walle's CPU reference is
   parity/liquid_glass_reveal_mask_model.c (walle_lg_reveal_mask_sample_r8
   gives alpha_half_bits + coverage; parity tests show usage). The
   reveal geometry inputs per state come from the corpus sweep dir
   artifacts/liquid-glass-reveal-coverage-01421a3-v1/capture/sweeps/
   sweep__wallpaper-reveal__regular__dark (state json + observed png)
   and the state parameters in the A2-geometry traces (roundedCircle,
   progress, etc.). An older (API-drifted, unrunnable) reference for
   the model equation is _analyze_reveal_second_stage.py.
2. For every pixel where round255(h(h(p)*0x3C00)) != round255(h(h(p)*0x3BFF))
   ("sensitive"), the corpus byte identifies apple's secondary exactly.
   That yields hundreds of plane-sign constraints per triangle.
3. Solve each triangle's plane (deficit d(x,y) = a*x + b*y + c,
   secondary = 0x3BFF iff d(x,y) > 2^-25-ish threshold — formulate the
   exact inequality from the later-46 model) by exact LP/Fourier-
   Motzkin over rationals. Feasibility MUST hold; report the polytope.
4. Validate: solved planes must classify every sensitive pixel AND all
   18 residuals correctly across all 6 states.
5. Then the generation rule: express the solved per-vertex deficits
   (plane at the three vertices) and hunt the pattern across states/
   triangles (they should be small integer multiples of 2^-25/2^-24
   ulps determined by the CA vertex shader's fma chain on the
   transform uniforms — state-dependent but input-only).

## Rules

- Exact arithmetic (Fraction / integers) for all constraint algebra;
  Python round() is banker's — never use it for half-up.
- Write scripts as analysis/a2_solver_*.py; keep a running log in
  analysis/a2_solver_log.md (append-only).
- No output-dependent lookup tables in any proposed parity fix: the
  deliverable is a rule computing the planes from state inputs, plus
  validation evidence.
- M1 hardware access exists (ssh quince@10.0.41.19, probe harness in
  /tmp/walle-agx-single-axis-multi-anchor.GRzoaQ, value probe
  ./rvp/reveal-agx-residual-value-probe, plan format = the
  a2-transfer-values-plan-v* dirs under build/analysis-agx-basis/) —
  use it to verify solved planes if helpful.
- SUCCESS: solved planes + validation 18/18 + the generation rule
  candidate. Append a dated TASK.md entry, commit (do NOT push).
- On budget exhaustion: commit the log + scripts, report the tightest
  polytopes found and remaining ambiguity.
