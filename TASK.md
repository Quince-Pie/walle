# Walle handoff

Updated: 2026-08-12 (US/Central)

## Current product state

Walle is a Linux/Wayland-only wallpaper engine. It now has one renderer:
Vulkan 1.4 with offline Slang-to-SPIR-V 1.6 shaders. There is no EGL, OpenGL,
OpenGL ES, legacy reveal shader, backend selector, runtime shader compiler, or
rendering fallback.

The production reveal result is:

```text
exact samples:       272,629,669 / 272,629,760
exact percentage:    99.99996662139893%
residuals:           91, each one code value
exact frames:        52 / 65
signed residuals:    +51 / -40
candidate inventory: 9062b7bfde617f88638c9b48fdb8ace7b6f91b4518d54c5a6e54abcb51e93644
count-list SHA-256:   d6c006d789b551e875555f3e8ef32f0c46c3ec3911802fea405ef9d3458edb5d
```

The actual Walle layer-shell executable, not a standalone surrogate, produces
that inventory through Vulkan on both the integrated Radeon and RX 9070 XT.
Vulkan validation is clean across all 65 frames.

Do not call the old nine-pixel Apple-assisted result a portable Walle rule.
That result was 272,629,751/272,629,760 and used Apple-owned setup. A fresh
SwiftUI/CARenderer tree can produce zero mismatches, but it is an Apple oracle,
not a public-input Linux algorithm.

## Vulkan architecture

The hard runtime contract is:

- `VK_API_VERSION_1_4` for the loader, instance, and physical device;
- direct Vulkan-exported `linux-dmabuf` presentation using compositor feedback;
- dynamic rendering and synchronization2;
- Vulkan 1.4 maintenance5 direct-SPIR-V pipeline creation and maintenance6,
  including `vkCmdBindDescriptorSets2` and `vkCmdPushConstants2`;
- `vkQueueSubmit2` and `vkCmdPipelineBarrier2` only;
- geometry shaders, shader int64, shader draw parameters, and Vulkan memory
  model/device-scope features required by emitted SPIR-V;
- SPIR-V 1.6, Vulkan memory model, precise floating point, and unknown sampled
  image formats emitted by Slang 2026;
- no render-pass/framebuffer objects and no runtime shader compilation.

`vulkan_renderer.c` owns the Vulkan instance/device, two graphics pipelines,
one transition-live shared 4 MiB nibble-packed Apple fast-sqrt buffer, adaptive
dma-buf presentation pools, and per-output lifecycle. `walle.c` owns wallpaper
policy, image preparation, timers, retained render backing, and layer-shell
surfaces.

Per-output resource policy:

- one direct dma-buf presentation image per output while idle, with a second
  created lazily while frames change and released again after promotion;
- `auto` prefers a qualifying discrete GPU and never selects a CPU Vulkan
  device; `[walle] vulkan_device`, `--vulkan-device`, or `WALLE_VK_DEVICE`
  can explicitly select a type, enumerated index, or device-name substring;
- one frame fence per output and no acquire/present semaphores;
- current wallpaper render backing retained as a file descriptor while idle;
- current and incoming standard+glass GPU texture pairs exist only during a
  transition and are both destroyed on successful promotion or abort;
- R8_UINT mask, descriptors, geometry/owner/axis data, and readback buffer are
  transition-lived;
- vertex, index, owner, primitive-map, and RG32 axis data share one allocation;
- host-visible device-local coherent memory is written directly when exposed;
  otherwise a single transition-lived staging allocation is used;
- the previous frame fence is waited before any mapped/staging overwrite;
- descriptor sets are updated only when their transition resources change;
- exactly one reveal draw and one composition draw per transition frame.
- composition push constants are two 16-byte lanes (`timeline`, `geometry`);
  C asserts offsets 0/16 and the build checks the same SPIR-V std430 offsets.

Presentation is deliberately per-output. Walle exports modifier-backed Vulkan
images as Wayland buffers and transfers queue ownership to/from
`VK_QUEUE_FAMILY_FOREIGN_EXT`. A busy two-image active pool schedules the next
frame callback without allocating a third image or poisoning the shared
renderer. Promotion requests compaction; an already released spare is
destroyed immediately and a compositor-owned spare is destroyed from its
eventual `wl_buffer.release` callback.

The wallpaper textures are `VK_FORMAT_R8G8B8A8_SRGB`; the compact glass image
keeps its native downsampled size. The mask is `VK_FORMAT_R8_UINT`. The
presentation image is `VK_FORMAT_B8G8R8A8_UNORM`, and the composition shader
performs the final linear-to-sRGB transfer once.

Measured idle VRAM on the 5120×2880 + 2560×2880 desktop is about 97.6 MiB:
86.25 MiB for one compositor-held modifier-backed image per output and roughly
11.4 MiB for shared Vulkan/driver state. The raw visible pixels alone are
84.375 MiB. The previous four-image-per-output WSI implementation used about
448 MiB.

## Shader/build contract

Sources:

- `shaders/reveal_mask.slang`: recovered public geometry/raster ownership,
  P25/AGX axes, Apple fast sqrt, exact binary32-to-binary16 RNE, R8 result;
- `shaders/liquid_glass.slang`: clear/regular material composition;
- `vulkan_renderer.[ch]`: Vulkan 1.4 backend and resource lifecycle;
- `parity/liquid_glass_reveal_mask_model.[ch]`: public circle/mesh state;
- `parity/liquid_glass_postguard.[ch]`: exact public post-guard children;
- `parity/liquid_glass_raster.[ch]`: packed owners/axes and exact arithmetic.

The Makefile compiles four Slang entry points offline with:

```text
-target spirv -profile spirv_1_6 -std 2026 -O2
-capability vk_mem_model
-emit-spirv-directly -matrix-layout-row-major
-restrictive-capability-check -fp-mode precise
-fvk-use-entrypoint-name -default-image-format-unknown
```

Every module runs through `spirv-val --target-env vulkan1.4` before C23
`#embed`. The SPIR-V outputs depend on both shader sources and the Makefile, so
flag changes cannot be hidden by stale binaries.

## Validation receipts

Green on the current implementation:

- isolated cold release build from absent objects/SPIR-V;
- ordinary release build;
- GCC 15 `-fanalyzer` build;
- ASan+UBSan build and full 65-frame actual-process run;
- reveal-mask model release/analyzer/sanitizer gates;
- reveal-raster release/analyzer/sanitizer and provenance gates;
- SPIR-V validation for all four modules against Vulkan 1.4;
- actual Wayland/Vulkan process capture: 65 states, 65 presents, 64 frame
  callbacks, no validation warning/error, canonical 91-residual inventory;
- actual presented BGRA readback at fixed state 32 for both clear and regular,
  requiring at least 1% distinct bytes; this catches material/push-ABI
  regressions but is not an Apple composed-frame parity claim;
- direct dma-buf presentation through the actual layer-shell process, with 65
  canonical presents and no swapchain/acquire compatibility path;
- same process inventory on integrated Radeon and RX 9070 XT;
- production binary has no EGL/OpenGL/OpenGL ES runtime dependency.

Commands:

```sh
nix develop --command make -j4 MODE=release
nix develop --command make reveal-mask-model-gate reveal-raster-gate
nix develop --command make MODE=release reveal-best-known-process-gate
nix develop --command make -j4 MODE=release ANALYZE=1
nix develop --command make -j4 MODE=release SANITIZER=1
WALLE_VK_DEVICE='RX 9070 XT' nix develop --command \
  bash analysis/run_walle_reveal_process_capture_gate.sh build/bin/release/walle
```

The Vulkan/Mesa/LLVM stack leaves four 256-byte process-global allocations
under LeakSanitizer. The ASan/UBSan process gate therefore disables only leak
detection; allocator/raster unit gates retain leak checks. No Walle-owned leak
was observed.

## What is still missing for literal parity

The 91 pixels are real and were reconfirmed after the Vulkan migration. The
migration neither improved nor regressed them.

Current decomposition:

- 82 residuals are explained by Apple's fixed-function setup coefficients for
  arbitrary non-axis-separable post-clip child triangles;
- nine state-42 residuals are in the later physical-presentation transfer;
  Apple's offscreen reveal mask is already exact at those positions.

The central reverse-engineering result must not be lost:

> Apple's fragment shader uses hardware `iter` for center samples, `ldcf` to
> fetch each rasterizer-generated coefficient triple, and evaluates explicit
> offsets as `value = ffma(x, A, ffma(y, B, C))`.

Therefore the missing 82 are not hidden in Metal source, derivative syntax,
half conversion, R8 transfer, or the fragment shader. They are in how AGX's
clip/setup unit materializes `(A,B,C)` for generated post-clip triangles.

Substantial M1 evidence already exists:

- setup is tile-local two-dimensional `(A,B,C)`;
- canonical clipping order and fan topology are fixed;
- public-f32 clipping, source-slope reuse, and simple fixed quantizers were
  falsified;
- isolated zero-anchor and single-axis/product sweeps close those simpler
  arithmetic cases exactly;
- the apparent X-lane phase was disproved as a child-ownership confound.

The 2026-08-13 continuation replaced that localization with a recovered rule.
Five new authenticated M1 captures isolate one setup stage at a time by making
every other stage exact by construction (power-of-two determinant, so the P25
selector takes its exact special case; a tile at zero displacement on one axis
and a power-of-two displacement on the other). The results are:

- the first-product join **rounds halfway cases away from zero, not to even**
  (28,400/28,400 unique targets across 256 shapes; round-half-to-even scores
  19,353/28,400);
- the middle product, the two-term middle join, and the reciprocal selector
  are all already exact as modelled;
- rescoring the retained captures with only that tie rule changed closes the
  real-child capture completely (1,224/1,456 to 1,456/1,456) and regresses
  nothing.

That rule does not change Walle's production result, because the shipped
packed path computes each channel's numerator as a single endpoint product and
so has no two-product join to correct. It applies only to per-triangle setup,
which the one-dimensional packed representation cannot express.

Two further results are recorded in the ledger and are not integrated:

- a CPU fragment model that reproduces the shipped Vulkan bytes exactly at all
  91 residuals shows 75 of them are center-interpolant errors of one or two
  ULP, not feather, binary16, or R8 errors;
- giving every drawn triangle its own AGX triangle setup and a two-dimensional
  tile constant reproduces the Apple byte at 17 residuals and never moves a
  residual the wrong way, but it needs a two-dimensional packed representation,
  owner block, and shader, gated on the whole corpus rather than on 91 pixels.

No output-dependent lookup, captured per-state geometry, or 91-pixel patch is
allowed. `case-study.md` is the intended method: recognize nearby constants
and structures as noisy observations of a known algorithm, then validate the
deduced input-only law on untouched evidence.

## Research resources

- `/tmp/asahi-docs`: local Asahi/AGX documentation and reverse-engineering
  tools; use the whole tree, not only the public “GPU part 6” article;
- `/tmp/HowToVulkan`: modern Vulkan implementation guidance;
- `/tmp/slang/docs`: the installed Slang 2026 language/toolchain guidance;
- `lg-test/README.md`: chronological Apple/AGX evidence ledger;
- `analysis/`: current setup probes, scorers, disassembly, and Vulkan process
  gate;
- `lg-test/Analysis/` and `lg-test/artifacts/`: frozen experiments and receipts.

The M1 host is `quince@10.0.41.19`. It has LLDB, Nix, Metal tools, and
passwordless sudo. Do not use an implicit SSH host alias. Apple runtime work
must still be preregistered and preserve evidence; root access is not a reason
to mutate or retry an experiment after observing output.

## Safe next research step

Resume only if the user asks for literal parity research:

1. Work from AGX setup/clip documentation and the existing `iter`/`ldcf`
   disassembly, not from PNG residual fitting.
2. Generate vertex-basis and two-product M1 probes that isolate the hidden p28
   alignment/join before opening output references.
3. Freeze the inferred integer law, then test it against retained coefficient
   pulls and untouched states.
4. Integrate the law into the public owner/axis constructor and Slang shader.
5. Require offscreen mask residuals to fall from 91 to nine without any
   per-state selector.
6. Treat the remaining nine as a separate physical-presentation problem.

## Worktree safety

The repository contains a large amount of untracked analysis evidence. Do not
run broad clean/reset commands and do not add the whole worktree. Commit only
the intended production, shader, build, test, and documentation paths.


## 2026-08-13 session addendum (production-children campaign)

- Laws 1-8 in the ledger's "production-children capture campaign" section
  supersede the earlier middle-join half-up note: middle join is RNE; the
  half-up finding survives only for the FIRST join.
- `parity/liquid_glass_raster.c`: narrow-product bypass in
  `product_stage`/`column_product_stage`, new `selector_product_stage`
  (slopes, dynamic T) and `constant_selector_product_stage` (constants,
  fixed T=20); `reciprocal_stage` now routes through the slope law.
- `parity/liquid_glass_postguard.c`: PROBE_CLIP_LERP / PROBE_CLIP_TRUNCATE
  probe-only env toggles added (production path remains exact-rational RNE,
  which the captures confirm as hardware-exact).
- State 31 fully solved in the general-children CPU model (5/5 residual
  bytes; ownership, plane words, and evaluation all bit-validated).
- Remaining: ±1-ulp jitter on nine clip-child channels (39..60), raw-base
  double-clip C deltas, byte score 16/91; then renderer integration and the
  state-42 presentation pixels.

## Clip-interpolator: open problem statement (2026-08-13, end of session)

The ONLY remaining unknown for the 82 AGX residuals is the guard-clip
varying interpolator's exact arithmetic.  Everything else is bit-validated
(see ledger "production-children capture campaign" + "clip-interpolator
rulers").

Facts pinned by the two ruler captures (pickles with solved hardware words:
`build/_clip_ruler_samples.pkl` = (which,P/Q, v_start, v_end, t_exact
Fraction, hw_word); `build/_single_clip_samples.pkl` = (geometryIndex,
s, e, t, frac, dev_vs_exact_rne)):

- state-31 operands: interpolation == exact-rational RNE (bit-exact, 408/408).
- other operands: hw deviates 0/+1 (sometimes ±2) from exact-RNE; the
  deviation flips quasi-periodically as the inside endpoint sweeps 1-ulp
  steps (period ≈ 1/(1-t)), so v = s + t_hw(e-s) with SOME finite t_hw and
  a finite multiply; but intersecting word-intervals proves NO constant
  t_hw with a fixed final rounding (rne/away/tz) fits a whole geometry.
  Hence the t x delta product itself is truncated operand-dependently.
- Families ruled out (best scores on 1,383-sample clean set): pure-f32
  lerp (any op order/fma), t at q0.16..q0.32 any rounding + exact interp
  (max 3,959/4,984 on the older set; 1,041/1,383 clean), P25-ceil
  reciprocal-t + exact interp (1,001/1,383), first-product pps T=16 on
  t x delta (worse), product T = bits-32 (worse).
- Suggested next probes: (a) sweep BOTH endpoints' varyings AND vary the
  edge denominator (more geometries) in a single-clip-ruler-v2 to separate
  t-quantization from product truncation; (b) model the interpolator as
  num x recip x delta fused (single truncated 3-operand chain
  (num*sel)*delta with T = bits-32 at each stage and the final add at the
  v-ulp grid with away/rne variants); (c) check whether the deviation
  correlates with the low bits of delta's mantissa (product truncation
  signature) using the existing pickles before any new capture.

Byte status: 16/91 residuals reproduced by the general-children CPU model
(`analysis/probe_general_child_mask.c`, run:
`PROBE_ALL_TRIANGLES=1 PROBE_ALL_CHILDREN=1 $SCRATCH/maskprobe
parity/raster_p25_selector_ceil_bits.bin build/_residual_list.txt 1`).
State 31 is 5/5; the remaining 75 all sit on clipped children of states
33-60 whose planes depend on the interpolator law.  The 9 state-42
presentation residuals are untouched (separate subsystem, task #4).

Best interpolator candidate so far (NOT final): t = ceil(t_exact x 2^25)/2^25,
v = s + t(e-s) exact, truncate v toward zero at 26 bits, then RNE24:
1,115/1,383 on the clean single-clip set.  pps-truncating the t x delta
product (any T/bias) makes it worse, as does every single-stage variant.
Next session: single-clip-ruler-v2 with more geometries (vary denominator
bit patterns) + BOTH-endpoint sweeps to separate t-quantization from the
accumulator; consider that the interpolator may compute v as
(num_fix*(v_e - v_s) ... ) via the barycentric two-product path with the
26-bit toward-zero accumulator suggested above.

## Migration attempt log (2026-08-13, later)

1. Rebuilt walle with the session's raster.c law changes: gate regressed
   91 -> 129 because the shipped packed path was calibrated with the OLD
   stage behaviour.  Fixed by quarantining the measured laws in general-path
   functions (`general_product_stage`, `general_column_product_stage`,
   `selector_product_stage`, `constant_selector_product_stage`) and
   restoring `product_stage`/`column_product_stage`/`reciprocal_stage` to
   historical behaviour.  Gate re-verified: exactly 91 mismatches, correct
   candidate inventory sha (9062b7bf...).  Walle is byte-identical to
   baseline again.
2. Built a full-corpus CPU prediction sweep (PROBE_PREDICT=1 mode in
   analysis/probe_general_child_mask.c): for every pixel in postguard-child
   regions of all 65 states, compares packed-path byte vs general-path
   byte.  Result: state 31 predicts EXACTLY its 5 residuals (perfect
   migration there), states 33/34/36/44/59 predict 1-6, but ~20 states
   predict 100k-750k byte changes (7.5M total) - because a +-1-ulp error in
   an interpolated clip-vertex varying shifts the child SLOPE word, which
   drifts the whole region's values (not just knife pixels).  Integration
   is therefore HARD-GATED on the clip-interpolator law; do not wire the
   general path into the renderer before that law reproduces the ruler
   captures ~100%.
3. Additional interpolator families excluded on the clean ruler set:
   plane re-evaluation from source setup coefficients (941/1383), directed
   division roundings in f32 lerp (855/1383), ANY constant f32 t32 within
   +-8 ulp under muladd/fma in either direction (max 219/689 per geometry -
   pure f32 lerp is impossible).  The pipeline is wider than f32 but not
   exact-rational.  Next instrument: single-clip-ruler-v2 sweeping MANY
   denominators (dense t coverage) to chart the quotient/product error
   directly.

Ruler v2 (capture 60d492c0...) closes the constant-t hypothesis: the
interpolator rounds per-operand.  Next fit should model
v = s + round(t_q x (e - s)) with t_q at ~25-26 bits and a rounded product,
solved jointly against BOTH single-clip datasets (pickles listed above),
then validated against the residual-states capture before any integration.

KEY NEXT CAPTURE (single-clip ruler v3): choose varyings with e - s an
EXACT POWER OF TWO (e.g. s = -1.0, e = 1.0 or s = -0.5, e = 1.5): then
v = s + t_q x 2^k has no product rounding and each solved hardware word
reads t_q's quantization directly, per geometry/denominator.  Sweep s so
the value's fraction crosses many knife positions to pin t_q to full
precision; THEN the product-rounding law falls out of the existing v2
dataset (pickle build/_ruler_v2_samples.pkl: (t_exact, s, e, hw_word))
by dividing out the now-known t_q.  Product-rounded family
(t_q x delta -> round at 24..28 bits) already scored at most 317/619 with
t_q from simple quantization, so t_q itself must be non-trivially rounded
(divider artifact) - measure it, don't guess it.

Ruler v3 (capture 8db66082..., pickle build/_ruler_v3_samples.pkl, 3,377
solved words) exposed the interpolator's fingerprint: with delta = 2.0
exactly, per-geometry deviation from exact sits on a 1-word-ulp lattice
with (a) a constant per-geometry offset (t-quantization residual, e.g.
-1.514 / +1.751 / -1.030 x 2^-26) and (b) a PERIOD-4 pattern in s's low
mantissa bits (per-operand product truncation dropping ~2 low bits).
Simple families still failing (best 1,295/3,377): fixed absolute-grid
floors, mantissa-width quantizations of both products.  Next fit: truncate
each product at a column relative to ITS OWN leading bit (floating
mantissa truncation, pps-style on the product mantissas), sweeping kept
width 24..28 x rounding, jointly with t-hat width/rounding; the period-4
structure means the s-product keeps ~2 fewer low bits than exact.  All
seven interpolator datasets are captured and pickled; this is pure offline
fitting now.

## Interpolator fit closing state (goal-hook session)

Best per-geometry result on ruler v3: two-product pps model
(t-hat x e + u-hat x s, per-product pps T=19 fixed + bias + half-up shift
to 27 bits, exact join, RNE24 out) with FREE 27-bit coefficients solves
59/64 samples of the densest geometry at (dt=-7, du=-10, bias=20); the
5 misses cluster at high s-low-bits and are partially cured by the
carry-top term (2 geometries with exact u-products then solve fully at
bias=20+carry).  ITER-style exact MAC with f32 coefficients: excluded
(1,067/3,377).  Remaining unknowns: exact coefficient generation (the
divider's t-hat/u-hat outputs, offsets order ~10 x 2^-27 below RNE) and
one residual product detail affecting ~8% of samples per geometry.
Recommended: solve (t-hat, u-hat) pairs with the carry model over ALL 63
geometries allowing per-sample majority (not full-match), then regress
t-hat against the exact quotient's binary expansion to identify the
divider (likely Newton-Raphson with truncated iterations); datasets and
scripts are all in build/ and analysis/ per the ledger.

## Goal-session progress: interpolator product law recovered

v = toward_zero_24(coeff x operand) per product (ruler v4, unique fits).
Remaining: coefficient generation (divider) - solve t-hat from v4 channel 2
(both products active, u-hat known) then regress (t-hat,u-hat) vs
(num,den).  All candidate reciprocal families tested so far fail; the
on-device Metal divide words are in /tmp/rcp_results.txt (copy them into
build/ before they expire).  After the divider law: rebuild the general
child model with hardware-true clip varyings, rerun PROBE_PREDICT (expect
per-state diffs to collapse to the residual sets), then implement the
renderer general path (task 6) and run the gate.

## v5 findings (t-hat channel)

Captures: single-clip-ruler-v5-plan-v1/capture.raw sha
1cd72ba4ce29cecf988794431483c833b93ba6784b0cd24c75b6d37713b7b70d.
- 10 geometries pin t_q UNIQUELY on the 2^-30 lattice with value-tz24
  (g17:-28, g40:-13, g29:+40, g13:-13..-12, g1:+63, g32:+35, g62:-19,
  g25:+37, g5:+22..26, g10:+71).
- Most geometries admit NO constant t_q under value-based product rounding
  (any mode/width): the t-product truncates the E OPERAND's partial
  products (operand-role asymmetry: coefficient = pps multiplier for the
  u-product where the s-sweep fit value-tz24; varying = multiplicand).
  First pps(e_mant, t_mant, T) sweep (W 25..27, dt +-24, T 18..29) found
  no full fit for g0 - next: wider coefficient range, carry/bias terms,
  and validate the word-solver against synthetic planes first (unique-dq
  attribution may include wrong words; cross-check with channel pairs).
Solved-data pickles: build/_ruler_v4_uq.pkl (u-hat exact per geometry),
build/_ruler_v5_tq.pkl (t-hat windows).  MAC composition law
v = RNE24(P_t + P_u) verified on 12 geometries x ~55 samples via channel 2.

## Divider dataset extracted (end of goal-session window)

build/_ruler_v5_tc_pps.pkl: per-geometry 27-bit t_c under the pps(e, t_c,
T=22) law, 90-100% sample fits across all 64 geometries (offsets from the
exact quotient: -10..+8 x 2^-27).  NEXT: (1) regress t_c-t27 against
num/den mantissa structure to close the divider rule (likely a small
Newton-Raphson/table artifact); (2) chase the residual ~3% per-geometry
misses (T-column vs product bit-length, carry, or solver misattribution);
(3) unify with u_c (build/_ruler_v4_uq.pkl) - check u_c == pps-law
complement or its own division; (4) rebuild clip varyings with the full
law, PROBE_PREDICT, renderer integration, gate.


## Task 6 status: renderer general path DONE (behind WALLE_REVEAL_GENERAL=1)

Integrated + verified (see ledger).  Gate stays at 91 by default; flag-on
proves state 31 hardware-perfect through the real renderer (5 fixes, 0
breaks).  Remaining blockers unchanged: interpolator coefficient law
(divider) for the other 75 AGX residuals; then the 9 state-42 presentation
pixels; then flip the flag and gate at 0.

## Divider fit status (continuing)

t-product law confirmed at pps(e_mant, t_c, T=22 fixed, no bias/carry):
219/222 on g0 with t_c = t27+5; the 3 outliers are all hw = model+1 with
solver dq=+1 - suspected single-tile word-solver misattribution (ruler v5
samples ONE tile per record).  NEXT CAPTURE (v5b): identical e-sweep but
2-3 sampled tiles per experiment value to make the word solve unambiguous;
then per-geometry t_c become exact and the divider regression
(build/_ruler_v5_tc_pps.pkl currently 90-100% fits, simple reciprocal
models max 10/64) gets clean targets.  Also unify u_c (v4) under the same
pps law.  Renderer integration is DONE and gate-clean (see ledger); only
this law blocks flipping WALLE_REVEAL_GENERAL on by default.

## Interpolator law: near-closure state (v5b analysis)

Captures: single-clip-ruler-v5b (sha c45c8096..., 3-tile joint word solve,
3,589 unambiguous rows).  Established:
- t-product is plain fixed-column pps + bias (Booth excluded at equal fit,
  value-rounding families excluded).  g0 fits ALL 52 rows at
  pps(e_mant, t_c, T=21) + 10<<21 (equivalently (T=19,b=10)+(20,5) ids for
  other geometries).  36/64 geometries solve fully under (T=19, bias=10)
  with unique 27-bit t_c; 59/64 solve under SOME (T,bias); no single
  (T,bias) covers all (max 35/59) => remaining degeneracy is between t_c
  precision (>27 bits, cf. u_c on 2^-30 lattice), T, and bias, because the
  e-sweep only spans one mantissa family.
- Residual charts for failing geometries (e.g. g3) are within 0.03 x 2^-26
  of feasibility => suspect the solver's clip-POSITION assumption (f32(qx)
  then subpixel rounding) is a hair off hardware's snapping for some
  geometries, biasing the solved words.  NEXT: 3-way joint solve with the
  snapped position free (+-2 subpixels), and/or ruler v5c with e-sweeps
  across MULTIPLE mantissa families (0.5+, 0.75, 1.25, 1.75) to break the
  (t_c width, T, bias) degeneracy.  Law pickles:
  build/_ruler_v5b_{tc_final,tc_T19,lawscan}.pkl.

## v5c multi-family findings (this window's close)

Capture: single-clip-ruler-v5c-plan-v1/capture.raw sha
918577747fef30050f9ba37dc1699a7cf8393fc70232bc2a89805b7fdb4958bc
(64 geometries x 4 e-mantissa families x 16 values x 3 tiles).

- Single-family fits succeed broadly; joint multi-family fits fail for
  EVERY fixed/exponent-tracked/product-anchored column tried => the
  effective coefficient shifts BETWEEN FAMILIES: g0 families (e~0.75,
  1.25, 1.9) share dt=+4 while family (e~0.50, product 50 bits vs 51)
  needs dt=+6..7.  The shift correlates with product bit-length but with
  a magnitude no single-anchor pps reproduces (bits-32 anchor: 0/16
  joint).  NEXT: per-(geometry, family) win-set extraction over ALL 64
  geometries (script pattern in this session's history), then solve the
  cross-family loss law analytically from the dt-vs-bitlen table; also
  revisit whether the JOIN/normalize between P_t and the output injects a
  product-bitlen-dependent shift (the +1-normalize case) rather than the
  multiplier itself.
- All laws validated so far (u-product tz24, t-product per-family pps,
  MAC composition RNE24) remain solid.  Renderer integration unaffected
  and gate-clean at 91.

Also excluded this window: stage 27-bit output normalize (floor/half-up)
after the pps product (0/3 joint fits).  The cross-family effective-dt
table (per-family win-sets) is the instrument: extract it for all 64
geometries x 4 families and solve the family-shift law analytically -
candidates not yet tried: sticky/guard behavior in the RNE24 that consumes
the full pre-normalized product (bits 50/51 -> different guard population),
and the MAC alignment between P_t and P_u when P_u = 0 (s = 0 rows!) vs
the general case - compare against v4 channel-2 (s,e) rows where BOTH
products are active before concluding.

## Cross-family analysis (this window)

Slope-only (de-circularized) solving CONFIRMS the family shifts are
hardware-real, not C-model artifacts.  Per-geometry dt table under the
(T=19,b=10) reference (build/_ruler_v5c_famtable.pkl + this window's
slope-only rerun):
  f0 (e~0.50, product 50 bits): +2..+2.5 vs f1  [bitlen effect]
  f1 (0xC00000) == f3 (0xF33333) mostly        [dense==sparse-high]
  f2 (0xA00000): -1..-2 vs f1, GEOMETRY-DEPENDENT [reads coefficient low
    bits through truncation]
Excluded in joint fits: swapped-role pps at T 19-26 (27-bit tc), 30-bit tc
swapped-role at T 25-29, output p27 normalize, exponent-tracked and
product-anchored columns.  The f2 signature says partials are selected by
the E mantissa's set bits over a >=27-bit coefficient with truncation
around column ~24 (magnitude fits) - but no tested composition reproduces
all families at once.  NEXT IDEAS (untested): complement form
v = e - u_hat x (e - s) sharing the u-product datapath (with s=0 it reads
u_hat directly against the v4 u-law: tz24!  test v = e - tz24(u_c x e)
against these same rows - CHEAP AND CONSISTENT with both product laws);
per-family bias from the exponent alignment of the bias constant (bias
added at a VALUE-anchored column rather than product-anchored).

Additional exclusions (this window): complement form v = e - tz24(u_c x e)
(0 fits, u_c on 2^-30 +-100), complement with pps u-product
(T 17-23 x bias x both orders x 27-bit u_c +-40: 0 fits on g12).
The per-family dt table stands as the definitive fingerprint; the law
search should continue from it with fresh structural candidates (e.g. the
two-stage F32 pipeline: t32 = f32(divide), residual r = num - t32*den at
low precision, correction product r x recip - the classic divide-with-
remainder refinement whose error would be family-dependent through the
CORRECTION product's truncation).

## Session 2026-08-13: interpolator resolved conceptually; blocker recast

MAJOR REFRAME, hardware-validated: the production corpus does NOT depend on
an unknown clip-interpolator law.  The residual-states capture feeds
pre-clipped canonical children (our vertex words) to hardware, and 176/188
channels' A/B/C words match walle's model with the exact-rational-RNE
varyings at ZERO offset.  The 9 remaining "hard" channels are SETUP-LAW
gaps (slope/C words off by 1-2 low bits), NOT interpolation gaps:
  (39,101,ch1) (40,104,ch1) (41,104,ch1) (42,104,ch1) (58,101,ch1)
  (58,104,ch1) (58,109,ch1) (60,106,ch0) (60,109,ch1)
Common structure: every one has a degenerate partner axis (numerator
exactly 0 or tiny residual 0x1..0x45 from exact cancellation).

Divider/interpolator campaign (rulers v6/v7, new captures on M1):
- v6 (e swept 1.0+ulps, 224 geoms: 64 v5c + den sweeps + shared-den
  pairs): capture sha b432b77b38a11c4d27d9dbc4de0724eff528685eb667d590c
  30ec2d5d1d0191b.  v7 (1-ulp operand transfer curves + pow2 anchors):
  capture sha 733b007338557929625ebc232a9b11c73fb14b1c839f46f3b6152551ab
  e063e4.  Solvers: _solve_v6_thw.py/_solve_v7_thw.py ->
  build/_ruler_v6_thw.pkl/_ruler_v7_thw.pkl (t-hat intervals).
- Findings: hardware's own clipper (synthetic unclipped inputs) deviates
  from exact-RNE by at most ~1 output ulp; deviations ride a ~26-bit
  divider estimate + small em-dependent multiplier loss; final rounding
  half-up-ties-away in ruler contexts; g05 (t=15/16 exact) isolates the
  multiplier: loss = ~2 phase units (2^-26 rel) for 51-bit products, 0
  for 50-bit, junk-stable.  Per-geometry constant-t explains 91.2% of
  3680 v5c rows; per-row divide is excluded (62%); C-at-Q plane-eval
  excluded (0/58 off-drift); selector-pipeline-as-divider excluded
  (72/261); SRT-13/Goldschmidt idealized families excluded; exact-frac
  threshold theta=3/16 on the 2^-24 grid is the best single rule
  (220/261 confident quotients) - remainder is deterministic
  sub-0.35-ulp24 epsilon(num,den), unresolved, AND NOT NEEDED for the
  corpus (see reframe above).
- parity/liquid_glass_postguard.c gained PROBE_CLIP_QLAW (probe-only,
  default off) + g_clip_ties_away; a q24-quantized production clip law is
  EXCLUDED (breaks 87/188 channels that verify exact-RNE).

Nine-channel forensics so far (analysis vs residual-states capture):
- Bad words: B slope one key BELOW model at idx frac 4/8(tie) AND 5/8 for
  s39/s42/s58x2/s60-106; C words off +-1..2 low bits at some tiles for
  s40/41/42/58-104, 58/60-109.
- Mag-adjust solves: s42,s58-104: big-axis numerator -1; s60-109: -1..-2;
  s60-106: axis0 -1..-5; others unsolved by +-8 on both axes.
- Excluded as single global knobs: join tie mode (halfup/rne/halfdown at
  27-bit normalize), sel_product bias/carry (T 16..20, bias 9..20,
  carry 0/1), final 24-bit rounding mode, C-chain double-rounding
  variants (24-direct, 27/28 staged, halfup combos), alternate C anchor
  vertices (fixes s58-109 alone), packed-path-for-degenerate routing
  (fixes 3 but breaks 14+).
NEXT: the discriminator is in the join/first-product carry-save fine
structure for cancellation children: fit per-channel (product-stage
variant x join variant) jointly across ALL 188 channels requiring one
INPUT-ONLY selection rule; then rerun PROBE_PREDICT + flag-on capture,
flip WALLE_REVEAL_GENERAL, then task #4 (state-42 presentation pixels),
then the gate at --expect-mismatches 0.

## Session 2026-08-13 (late): word-sweep capture + corpus scoping

New captures (ledger-grade, M1):
- nine-word-sweep-plan-v1: 7 hard children x 64 anchor-word keys x 6
  tiles; capture sha 912ef3ebc899d983849e3a1ed07dd5061ec2e8437d92816d
  82c913a481c54859.  Solved (build/_nine_word_sweep_sols.pkl): per-k
  (d0,d1) numerator adjustments; canonical s41-o104 confirms join +1;
  scattered X entries (= C-chain flips beyond mag+-3) map the mid/sel
  boundary-riding across the sweep.
- Mid-stage finding: on sparse displacements (o104 family) the pps is
  exact and T16b15-vs-T19b10c1 differ only via bias; NO single fine bias
  B (0..2^24 swept at 2^15 grain, plus prod- and shift-proportional
  forms) explains all 80 constraints: best 69/80; the leftover 11 = one
  child-wide +1 (s41 join) + per-ty sel-stage wobble.  Composed
  (midB x selT/bias/carry/round) sweep also caps at 69/80.
- Discriminator search over single-part channels: labels T16-only
  {s40,s42}-o104, T19-only 19 channels incl. s34/s35/s60-o104 with
  nearly identical operands, neither {s41,s58}-o104: NO clean feature
  (sel/det/mag-low-bits non-monotone).  Conclusion: the +-1s are
  boundary-riding of ONE true law that both recipes approximate; the
  law lives in the join/first-product carry-save fine structure.

CORPUS SCOPING (PROBE_PREDICT, children-only): of the 91 residual bytes:
16 predicted-fixed (incl. all 5 of state 31), 5 predicted-wrong
(s35 x4, s41 x1), 70 NO-PRED = outside every postguard child = these
live in PACKED-path owners: they are 1-D packed-path boundary bytes,
NOT clip-child bytes!  (Dropped children are genuinely offscreen -
verified status=1 groups.)  PROBE_ALL_TRIANGLES run predicts ~10M byte
changes (base-owner general setup still diverges from validated packed
regions - do NOT flip base owners to general path).

=> Remaining work for 0-mismatch, in order:
1. Packed-path (1-D) +-1 boundary law refinement for the ~70 no-pred
   residuals (same carry/join phenomenon, in walle's shipped stages;
   dense captures reusable - probe per-state residual tiles directly).
2. The join/mid boundary law for the 9 hard children (5 predicted-wrong
   bytes) - evidence: _nine_mid_constraints.pkl, _nine_cidx_io.pkl,
   _nine_word_sweep_sols.pkl, dense capture 74610b36.
3. State-42 presentation pixels (9, subset of its 32 no-pred; task #4).
Artifacts all in build/analysis-agx-basis/* with SHAs in ledger; walle
ships gate-clean at exactly 91 (re-verified this session).

CORRECTION to the scoping note above: PROBE_PREDICT prints only pixels
whose byte CHANGES, so "no-pred" for a residual means the general path
(current law) predicts the SAME byte as packed there - not that the
pixel is outside children.  Verified: e.g. state-40 (1717,0) is >100px
inside child o104.  So the 70 unchanged residuals ARE clip-child bytes
whose fix requires the +-1 child-word corrections (the o104-ch1 mid/C
chain and join +-1s measured in this session's dense/word-sweep
captures).  The corpus therefore decomposes as: 16 fixed by current
general law, 5 flipped wrong (s35 x4, s41 x1 - same +-1 family), 70
needing the boundary law, all concentrated in the nine hard children's
regions plus s42's presentation set.  The ~7.5M PRED byte-change lines
include large general-vs-packed divergences (e.g. 255->0 rows inside
o104) that do NOT reproduce in the real shader path (probe quirk,
documented for state 37 previously) - trust the flag-on hardware
captures, not PRED, for regression truth.

## Session 2026-08-13 (cont.): native-28 numerator hypothesis

Word-sweep analysis (all against capture 912ef3eb..., analysis artifacts
build/_nws_*.pkl):
- First-product stage RE-CONFIRMED (T16 pps + 15<<16, floor): all
  alternatives (T-tracking, half-minus, half-up, carry variants) score
  far below base on the 448-point word sweep (348/448 base).
- The numerator delta d(mag) needed by the C-chain is CONSISTENT ACROSS
  didx within each k => the joined numerator itself carries the info.
- Sel stage verified clean 240/242 at pow2-didx (t2 = mag exactly).
- KEY HYPOTHESIS (partially validated): the joined numerator is
  NATIVELY 28-BIT (half-up at 28), not 27; my 27-bit model loses the
  28th bit. Explains the "impossible" rounding pattern exactly:
  frj=0.375@27 = 0.75@28 rounds up (+1/2 step), frj=0.5@27 is exact at
  28 (-1/2 step vs half-up@27), half-steps invisible except at boundary
  tys. Scores: native-28 + unchanged downstream: word-sweep 302/448,
  residual-states 180/188 (BEST regression yet), hard 3/9, broke only
  2 (35-104-0, 45-104-0). With MSB-anchored mid bias 10<<(shift-5):
  hard 5/9 but breaks 11. With fixed midB=20: sweep 348 but breaks 47.
- OPEN TENSION: 28-bit mags in different children want different mid
  bias anchoring (s33-104 wants 10<<19 fixed; hard o104s want
  10<<20-ish; 35/45-104 neither). Likely ONE law seen through the
  27/28-bit window ambiguity: B28 ~ 2*B27 -/+ didx couples the 28th bit
  to the apparent bias. NEXT: joint exact solve over (28th-bit per
  child, single mid-bias law) on the combined dense+word-sweep+resid
  datasets; then the 4 remaining tie rows (s58-109/60-109 corner
  children) and the {0,-1} tiny-axis rule under native-28.

## Session 2026-08-13 (cont. 2): TWO LAWS CLOSED - product proven, join corrected

NEW CAPTURES (M1, ledger-grade):
- product-isolation-c-plan-v3 (single-product children, C read at pow2
  displacement, anchor value 0): capture sha d6f97122d0f5e40be267d6b326
  0d9277e15ccf8ded3c5422c400eb5dff67e1e5.  RESULT: 2048/2048 EXACT under
  the first-product law pps(md,mev,16) + 15<<16, floor to 27 bits - the
  product stage is now PROVEN to sub-27-bit resolution across 16 edge
  mantissas x 128 value words.  (v1/v2 of this plan are superseded:
  design errors documented in generator history; captures 77f79e6a...,
  184d9781... retained.)
- join-isolation-c-plan-v1 (two-product children, both products exactly
  known, w1 x w2 sweeps, same-sign + opposite-sign): capture sha
  ce559c7fe282f57f6820925f0f06ee79742e10af4fa24f725e12441d2f4df744.
  RESULT (3,835 clean readouts, build/_jic_join_readout.pkl): the join
  normalize to 27 bits rounds UP iff the dropped fraction is STRICTLY
  GREATER THAN 1/4 (floor at exactly 1/4; both sign configurations).
  This replaces "first join half-up".  Global validation: word-sweep
  349/448 (half-up: 348), residual-states 179/188 with ZERO regressions;
  s41-o104's canonical numerator now correct (0x4675011, the join +1).
EXCLUDED this window: wide-join (products unshifted into the join) in
all rounding modes - breaks ~90 channels, per-product floor27 pre-shift
is triple-confirmed; sign-conditional wide-subtract (breaks 31);
native-28 numerator (inferior to 27+quarter once the join threshold is
right); T-tracking/half-based first-product variants.
REMAINING for the hard 9: with numerators now law-correct, the failures
are per-ty C/mid-stage +-1s and the tiny-axis subtractive -1s
(s39/58-101/60-106: exact sums, sh=0, hardware one BELOW the exact
difference - NOT a join-rounding issue; suspect the mid/C chain).  NEXT:
apply the quarter-threshold discovery to the MID stage (replace the
(c+10)<<19 bias hypothesis with quarter-rounding of pps19+c<<19) and the
C-final norms; re-derive the o104 per-ty table under the corrected
numerators; then flag-on capture + gate.

Status appendix (end of window): under corrected numerators the o104
per-ty mid/C constraint set (80 rows re-derived) still has no clean
single mid law (half-up 62/80 best; several rows need values below the
exact product => they carry SEL-stage wobble, which was measured at
+-1..2 for specific 28-bit jidx values earlier). The jic capture
validates the full sel chain on 3,835/4,096 clean rows, so remaining
deviance is sparse and concentrated in specific (jidx, sel) / (mag,
didx) pairs. NEXT WINDOW: isolate the SEL stage the same way the join
was isolated (fixed known numerator via anchor-0 single-product
children, sweep the DETERMINANT/selector across many keys at fixed
numerator - vary triangle positions, not varyings); apply the proven
methodology: prove stage exact-or-law at sub-bit resolution, then
recompose.  After sel closes: hard-9 -> flag-on capture -> gate.

## 2026-08-13: THE COMPLETE AGX GENERAL-SETUP LAW - CLOSED

Additional capture: sel-isolation-c-plan-v1 (det swept via apex height at
fixed proven numerator; 515 draws): sha 3e7a754957c7b06a8d122dfc382024a1
873861d3de9122805609923065bd0a80.

THE LAW (hardware-validated with ZERO exceptions on every dataset):
1. FIRST PRODUCTS: pps(md, mev, 16) + 15<<16, floor-shift to 27 bits
   (products <=32 bits: exact, half-up shift if needed).
2. NUMERATOR: aligned sum of the 27-bit first products, rounded RNE
   (ties-to-even) to 28 BITS.  This single 28-bit numerator feeds ALL
   consumers (slopes, mid, C).  [The historical 27-bit half-up numerator
   was the coarse shadow of this: even m28 is indistinguishable; every
   "hard channel" had odd m28.]
3. SELECTOR PRODUCTS (slope + C): pps(mand, sel, T) + 20<<T with
   T = mand_bit_length - 8 (operand-anchored column: 27-bit -> T19,
   28-bit -> T20; identity pps(2m,s,T) == 2*pps(m,s,T-1) unifies all
   earlier T19/T20/"sat20" observations), floor-shift to 27.
   Slopes then RNE to 24 -> f32 word.
4. MID (C-tile displacement) PRODUCTS: pps(m28, didx, T) +
   (carry_top(m28, didx, T) + 10)<<T, T = m28_bits - 8, floor-shift
   (narrow <=32-bit: exact/half-up as before).
5. C CHAIN: per-axis mid parts joined (aligned sum), norm-28 RNE,
   selector product per (3), det-sign applied, EXACT anchor add,
   norm-28 RNE, norm-24 RNE -> f32 word.

VALIDATION (all bit-exact, zero mismatches):
- residual-states capture:            188/188 channels (ALL 9 hard fixed)
- nine-word-sweep:                    448/448
- sel-isolation:                    2,060/2,060
- join-isolation:                   4,096/4,096
- product-isolation v3:             4,736/4,736
- nine-children dense (36 contexts):   36/36, 0 bad C tiles (18,788 pts)

NEXT (task #3 completion): implement the 28-bit-numerator law in
parity/liquid_glass_raster.c (wlg_child_prepare/constant_bits +
selector_product/general stages) and the reveal_mask shader general
path; PROBE_PREDICT; flag-on M1 capture; flip WALLE_REVEAL_GENERAL
default; then task #7 (packed-path residuals - likely the SAME
28-bit-numerator correction applies to the packed 1-D stages!), task #4
(state-42 presentation pixels), final gate at 0.

## 2026-08-13 (later): EVALUATION LAW MEASURED; SETUP LAW REOPENED ON ARCS

Root causes found and fixed this session:
1. UB BUG (one character): general_column_product_stage narrow branch used
   `if (shift < 0)` (vs `<= 0` elsewhere); shift==0 hit 1<<-1 = 2^63 UB
   garbage for near-cancellation numerators (4-bit num=11 etc.).  This -
   not owner hijacking - caused BOTH 7.5M flag-on regressions.  Fixed in
   parity/liquid_glass_raster.c (+ half-up on the narrow first-product
   path per the banked law text).
2. INTEGRATION SEMANTICS: correct rule is draw-order + exact triangle
   containment (last drawn triangle containing the pixel wins; base mesh
   triangles get general setups too, not just postguard children);
   partners evaluated with the CENTER's triple (LDCF).  Owner-source
   matching and raster owner-slot matching are both WRONG (hijack or
   miss).  Probe: general_owner_child(PROBE_MATCH_MODE=0), sample_triple.

NEW CAPTURES (M1, provenance):
- residual-value-plan-v1: every corpus residual pixel (+ collateral
  (35,1525,5)) x every containing triangle, variant probe records
  interpolate_at_center + 4 partner offsets.
  capture.raw  a7c7fcc5... (WRONG offsets - center-relative assumption)
  capture2.raw e49d6f77... (CORRECT: AGX interpolate_at_offset is
  CORNER-relative: (0.5,0.5)=center; proven by 1.5x/0.5x slope steps).
  RESULT: 74/92 pixels' apple bytes reproduced from hardware values by
  ANY covering triple (parent plane == child plane bit-exact on hw);
  18 pixels are the presentation-transform class (hw interpolation gives
  walle's byte): 31:249:1628 33:70:1639 40:1730:28 41:1897:606
  42:{1793:2,1794:5,1795:7,1799:16,1801:18,1801:20,1803:25,1805:29,
  1806:31,1837:103,1838:106,259:2011} 58:2033:1851 60:2042:1946.
  (Task #4 class is 18 pixels across states, NOT 9 in state 42.)
- PER-PIXEL EVALUATION LAW (measured): center value = RTZ of the plane
  (tile iterator); partner lane values = RNE (LDCF+FFMA offset path).
  Probe PROBE_VALUE_ROUND=9 implements it; with it all remaining word
  errors are UNIFORM whole-plane shifts => C-word/slope law errors only.
- residual-children-dense-plan-v1: 24 (state,ordinal) children covering
  every residual winner (base arcs o2/o4/o5/o6 + o103/o104/o106),
  16,868 draws, capture.raw a7576c5b...
  RESULT vs banked law: only 32/96 contexts exact; 21,478 bad C tiles;
  SLOPES wrong too: hw has tiny nonzero cross-slopes (2d79e622-class)
  where law says exact 0 (quantized first products do NOT cancel on hw),
  and 1-ulp main-slope misses (3a6b0059 vs 3a6b0058).  HYPOTHESIS: the
  narrow (<=32-bit) first-product exact bypass is wrong; hw truncates at
  the array column always.  The nine-children family never exercised
  narrow/cancelling products, which is why the law looked closed.

NEXT: fit first-product/numerator rules against the 96-context slope
words in build/_rcd_obs.pkl (obs[(state,ordinal,ctx)] = {"AB": (a,b),
"C": {tile: word}}), then the C chain; implement in parity C
(wlg_child_prepare/constant_bits); wire evaluation law (center RTZ,
partner RNE via center-triple, draw-order containment) into the shader
general path; flag-on gate expect 18; then the presentation transform.

## 2026-08-13 (later 2): BARYCENTRIC-BASIS DISCOVERY - THE SETUP IS PER-VERTEX PLANES

residual-children-basis-plan-v1 (16,868 draws; every vertex carries a
one-hot basis value so exported A/B/C ARE the per-vertex barycentric
plane words; ctx3 = (1,1,1) sum check):
  capture.raw 7c867151a99ac509d08b9295b03e64938482596c6b46aa91fc9d07c2edd89e72
Dense (production values) capture:
  residual-children-dense-plan-v1/capture.raw a7576c5b6dfadaa2fcf405c1f589efd8f0493afb7be5902c4051d08c6f6de78f
Value capture (per-pixel):
  residual-value-plan-v1/capture2.raw e49d6f77cf838eb7a600c5e365f0357ce0a6731605ec2b1018b671f59668b74a

FINDINGS (build/_rcb_anomaly_table.txt, build/_rcb_obs.pkl, _rcd_obs.pkl):
- 50/72 basis gradients match sel_oa(edge)+RNE24 exactly.
- FAMILY 1: main gradients off by +-1 ulp for some children (s40 o2: hw
  = model-1; s42 o6/s45/s47/s58/s60: hw = model+1) - the 27->24 or the
  27-bit sel product rounds differently per operand regime.
- FAMILY 2: ZERO edge coefficient -> tiny NONZERO hw gradient.
  e_x=0 rows (o6-family): tiny_A = +-other_mant * 2^(other_exp - k),
  SAME mantissa, k in {24,26}.  e_y=0 rows (o2/o4): tiny_B has
  DIFFERENT mantissa, exp delta -25..-31, ratio tiny/other varies
  continuously (-2^-25.3..-2^-31.2) => NOT a fixed shift; a function of
  finer inputs (sel low bits / det / coords).  These tiny gradients are
  REAL and produce the per-tile C drift seen in production (they explain
  the residual bytes' C words).
- The production slope residue (2d79e622 at s31 o6) EQUALS the basis
  cross gradient: the hardware plane = sum_i v_i * basis_plane_i with
  v=(0,1.00095,1.00095) reproducing the cross slope exactly via the
  basis planes; the accumulation is over per-vertex planes, NOT
  numerator/det (isolation captures had one-hot-like values, which is
  why the simpler law held there).
NEXT: regress FAMILY 2 tiny-gradient function (needs an edge/apex sweep
capture at fine granularity, or algebraic identification vs sel/det low
bits); pin FAMILY 1 rounding; then C basis planes from the same capture
(obs ctx0..2 "C" maps = per-vertex C planes per tile!); accumulation law
= sum v_i * plane_i (product+join arithmetic still to pin, constrained
by _rcd_obs production words + residual-value capture2 per-pixel words);
implement in parity C; expect flag-on gate = 18 (presentation class).

## 2026-08-13 (later 3): BIAS-SURVIVAL HYPOTHESIS + (1,1,1) SENTINEL

- basis ctx3 = (1,1,1) constant plane: hw slope words = (00000001,
  00000001) for ALL 24 children - denormal-1, i.e. a cancelled
  numerator leaves a POSITIVE epsilon that underflows to the smallest
  denormal.  Slopes are never exactly zero when computed.
- production s31 o6 ch1 slope A = 2d79e622 = EXACTLY the basis0 tiny
  gradient (same word): the numerator is computed in ONE FUSED ARRAY
  over all vertex terms; when the value terms cancel exactly, the
  array's additive BIAS (the +15<<16 / +20<<T class constant) survives
  at the operand alignment -> the tiny slope.  Same mechanism for
  e_x=0 basis rows (tiny_A = B*2^-26, same mantissa: partner-product
  leakage at column -26); e_y=0 rows have different-mantissa tinies
  (mixed bias+leak, unsolved).
- Accumulation candidates over MEASURED basis planes reproduce only
  ~37/96 production AB words -> production slopes are NOT a per-plane
  f32 accumulation of the exported basis words; consistent with the
  single fused array over v_i x e_i terms (the exported basis planes
  are just the array's outputs for one-hot inputs).
Scripts copied to analysis/: fit_accum.py fit_grad_chain.py
fit_gradient_form.py table_anomalies.py analyze_basis.py
validate_rcd.py fit_interp.py diff_words.py (paths reference the
session scratchpad; childgeo_all_residual_states.txt is copied to
build/ for permanence).
NEXT CONCRETE STEP: sweep-capture the epsilon: plans varying ONE vertex
value key at a time around cancellation (and varying det/apex) for a
fixed failing geometry; regress epsilon(alignment, sel) to pin the
fused-array bias/leak columns; then the C chain from basis C maps
(obs ctx0-2 C tile maps in _rcb_obs.pkl are the barycentric C planes -
compare against wlg constant chain per tile); finally implement.

## 2026-08-13 (later 4): VIEWPORT CLIPPING IS THE MISSING PIPELINE STAGE

dual-lane-sweep-plan-v1 (1140 on-screen geoms, capture 0ebfe896...):
per-edge law (sel_oa + RNE24) EXACT on 6762/6840 words - zero +-1
misses, zero tiny gradients.  The 78 misses are all the dense-mantissa
sliver family (deviations 30-200 ulps: the pps-T16+20 approximation
fails for dense mantissas; the real array law is still open).
dual-lane-sweep-plan-v2 (403 geoms: off-screen shifts + production
replicas + 360 dense slivers, capture 847b5483...):
- OFF-SCREEN IS THE ANOMALY TRIGGER: zero-edge tiny gradients appear
  ONLY when a vertex crosses the viewport (x<0/y<0 confirmed; tiny
  exps -19..-26, continuous in apex position); on-screen twins are
  clean.  Production replica (s31 o6) reproduces 2d79e622 exactly.
- INTERPRETATION: the hardware CLIPS the triangle at the viewport and
  re-sets-up the clipped polygon; new vertices carry f32-LERPED basis
  values whose rounding tilts the plane (tiny cross slopes, +-1 main
  shifts).  The 2^-26 same-mantissa production cases are special exact
  patterns of the lerp at .5-subpixel crossings.
- Clipped vertices generally have DENSE mantissa values => production
  correctness ALSO needs the dense-mantissa product law from the
  sliver data (510+78 measurements banked in _dls_rows.pkl /
  _dls2_rows.pkl).
REVISED PIPELINE MODEL: clip at viewport (Sutherland-Hodgman planes,
t + lerp arithmetic to recover) -> fan -> per-triangle setup with the
per-edge law (validated for sparse mantissas; dense law from slivers
pending) -> C chain -> evaluation (center RTZ / partner RNE).
NEXT: (1) dense-mantissa product law from sliver rows; (2) clip-lerp
law from off-screen rows (tiny gradients as probes); (3) implement
clip+fan+setup in walle general path.

## 2026-08-13 (later 5): SNAP LAW CLOSED - PER-EDGE SETUP EXACT ON-SCREEN

The entire "dense-mantissa array error" was the subpixel snap rounding:
hardware snaps vertex coords with floor(v*256 + 0.5) (HALF-UP), not
round-half-even.  With that single change the per-edge gradient law
(sel_oa product + RNE24, banked stages) is EXACT on ALL 7,386 on-screen
measurements of dual-lane-sweep v1+v2 (0 misses, every mantissa
density; harness MVP roundtrip variants are behaviorally identical on
this data).  fit_roundtrip.py.
=> ALL remaining slope anomalies are OFF-SCREEN CLIPPING artifacts.
REVISED PIPELINE: snap half-up -> viewport clip (planes/lerp law to
recover) -> fan -> BANKED per-triangle setup law -> C chain ->
evaluation (center RTZ / partner RNE).  The "fused dual-lane" and
"per-vertex superposition" framings are dead - artifacts of comparing
unclipped models against clipped hardware.

## 2026-08-13 (later 6): FUSED CLIP-SETUP + THE DIVIDER RETURNS

Inversion on sweep-v2 case ((-560,640),(464,640),(463.988,1151.996)):
- NO f32 value assignment at clip vertices can reproduce the hw slope
  words: hw B carries precision ~300x finer than 1 value-ulp steps
  allow.  CLIPPING AND SETUP ARE FUSED: the setup numerators consume
  WIDE (unrounded) lerped deltas, not f32 vertex values.
- Wide-delta fused chain (exact rational deltas -> banked product
  chain) reproduces the sub-f32-ulp word structure to within ~50 units
  at 2^-48 scales (fit_wide_delta.py); the remaining error is in
  hardware's t itself.
- v6 clip-ruler intervals (build/_ruler_v6_thw.pkl, 220 geometries,
  interval widths ~6e-10) show t_hw = exact + (0.5..0.9) t-ulp
  CONSISTENTLY POSITIVE: t = n x rcp_hw(den) with a ROUND-UP-family
  reciprocal (t_hw/n intervals directly bound rcp_hw(den) per den).
  None of {exact, f32div, p25-floor/ceil-table rcp} fit (best 52/220,
  test_divider.py).  The "divider hunt" (abandoned earlier as
  unnecessary) is REVIVED: it is the last unknown of the fused
  clip-setup law.  _ruler_v7_thw.pkl (1-ulp operand transfer curves)
  is the second dataset for this solve.
NEXT: solve rcp_hw from the 220 t-interval bounds (per-den rcp windows;
fit table/Newton generators); plug into the fused wide-delta chain;
validate on sweep-v2 off-screen (117 words) + basis anomalies + dense
production captures; then C chain (same fused model per tile), then
implement clip+fused-setup in parity C, flag-on gate -> expect 18.
Scripts: analysis/fit_wide_delta.py test_divider.py fit_clip*.py
invert_*.py fit_roundtrip.py (snap half-up!) fit_dense_law.py.

## 2026-08-13 (later 7): DIVIDER SOLVE BLOCKED ON INTERVAL RE-DERIVATION

- rcp-window extraction from _ruler_v6_thw.pkl: 185 dens, 22 with
  INCONSISTENT windows (=> t_hw not a pure function of den under the
  OLD downstream model), no 2^-W grid signature, and no direct-division
  quantization (W 24..33 x up/rne/hup/flo) exceeds 50% containment
  (solve_rcp.py, test_div_widths.py in scratchpad).
- KEY CAVEAT: the v6/v7 t_hw intervals were inverted assuming
  v = round_half_up_24(t_hw * e).  This session proved the downstream
  is the FUSED WIDE-DELTA chain (products from wide operands, RNE28
  join, sel product, RNE24; evaluation center-RTZ/partner-RNE).  The
  intervals MUST BE RE-DERIVED from the raw v6/v7 captures under the
  corrected transfer before any divider fitting is meaningful - the 22
  inconsistencies are likely artifacts of the old model.
NEXT (concrete): rework _solve_v6_thw.py/_solve_v7_thw.py with the
fused-chain transfer (t wide; v_word = chain(t_wide x e)); the 1-ulp
e-sweeps then read out t's sub-24 bits directly; solve the divider on
consistent windows; validate against sweep-v2 off-screen words; then C
chain; then implement clip+fused setup in parity C; flag-on -> 18.

## 2026-08-13 (later 8): RECIPROCAL-MULTIPLY CONFIRMED; GENERATOR OPEN

Re-derived v6 ruler windows under the fused chain
(analysis/solve_v6_wide.py -> build/_ruler_v6_thw_wide.pkl):
- 154/224 geometries consistent (vs 22 inconsistent dens before -> now
  only 3); t_exact inside 114/154.
- The 40 excluding windows have MIXED-sign offsets ~2^-25..2^-26.5 rel;
  even exact-binary t = 15/16 (g5, den 1200) misses => t is NEVER
  computed as n/den: t = n x rcp(den), 25-26-bit-precision reciprocal,
  nearest-family error.  RECIPROCAL-MULTIPLY CONFIRMED.
- Generator fits on the 126 usable rcp windows (solve_rcp2.py): best
  simple quantization 28-bit round-UP 72/126; Newton seed16+step28
  71/126; p25 selector table 44/126.  None close the generator yet.
NEXT: sharpen windows (use all quads per geometry in solve_v6_wide, and
the v7 transfer-curve capture) OR design a ruler v8 under the fused
chain with e-sweeps aligned to 27-bit product boundaries for direct
rcp bit readout; then implement clip+fused setup in parity C.

## 2026-08-13 (later 9): GUARD BAND ±512 CONFIRMED; t IS X-DEPENDENT

New captures (M1, all archived under build/analysis-agx-basis/):
- t-readback-plan-v1: 40 clip geometries x 40 px, value probe
  capture.raw 55b14674..., accumulator words capture-words.raw
  f013b427...  Calibration: dyadic t (45/64) reproduces EXACT (0.2 ulp).
- apex-x-sweep-plan-v1: 180 geometries sweeping V2.x at fixed crossing
  y-geometry; capture.raw a02d24d2...
FINDINGS:
- CLIP BOUNDARY: viewport +-512 px (NDC +-1.5) - guard-band clip
  (test_boundary.py: guard=512 -> 96/117 vs 59 at 0).  The v6 ruler's
  -512 convention was right; "clip at 0" (later 4/5 entries) is WRONG.
- Fan: matches are consecutive triples of the SH polygon; rotation
  varies; any-triple search used meanwhile (test_fan.py 66/117 for
  pixel-containing (0,i,i+1)).
- Per-channel word fits (fit_clip_words.py, exact-t lerp values):
  73/120; per-ctx c0=37/40 c1=27/40 c2=9/40 - failure scales with the
  number of lerped components; ctx2 (both crossings lerped) is a
  CANCELLATION AMPLIFIER of the t law.
- Joint (t, t') solves per geometry (solve_tt.py, _tt_solutions*.pkl):
  windows at 2^-27; g10/g11 controlled pair (SAME y-num/y-den,
  different V2.x) have DISJOINT t windows => t/values are
  X-GEOMETRY-DEPENDENT; no scalar t(y-num, y-den) model can fit.
  Distance-form (d0/(d0-d1)), NDC-endpoint, pixel-space f32div all tie
  at 5/8 solved windows; bary-eval-at-snapped-position REFUTED (9/120,
  fit_bary_eval.py).
OPEN: the x-coupling mechanism of clip-vertex values (apex-x sweep
data captured for exactly this; analyze transfer of ctx words vs x2
fractional phase); then the divider generator; then implementation.

## 2026-08-13 (later 10): CLIP-TRIGGER RECONCILIATION + ctx2 OPEN

RECONCILIATION (important for implementation): the nine-children dense
capture validated the UNCLIPPED banked setup law (36/36) because the
postguard children's vertices sit EXACTLY ON the +-512 guard boundary
(y=-512 etc.) - on-boundary vertices are kept, no clip.  Apple's
postguard children ARE pre-clipped geometry; only vertices strictly
BEYOND viewport+-512 (the base arcs: s31 o6 x=-537, s34 o2 y=-536...)
trigger the hardware guard-band clip.  Both capture families are
consistent under this rule.
- apex-x sweep analysis (analyze_apex.py): ctx0 mostly matches under
  exact-t clip model; ctx1 fails often; ctx2 (basis on the clipped-away
  vertex, both cut values lerped) fails ~everywhere (8/120), and value
  inversion within +-48*2^-27 of exact t finds NO solutions
  (invert_apex_vals.py, _apex_val_sols.pkl) => ctx2's value structure
  assumption (0 at on-screen verts, t/t' at cuts) is WRONG at a level
  beyond t-precision.  NEXT HYPOTHESES to test on the same data: values
  at cuts computed from a DIFFERENT parametrization (renormalized
  polygon barycentrics, or per-channel independent lerp with hw t per
  CHANNEL), or nonzero leakage values at on-screen vertices.
Sweep captures banked: apex-x-sweep-plan-v1 a02d24d2...

## 2026-08-13 (later 11): PER-CHANNEL INDEPENDENT VALUE ROUNDING PROVEN

- ctx3 = (1,1,1) sum plane on CLIPPED geometries produces STRUCTURED
  nonzero slope words (ad8b/2e5f-class, smooth per geometry), NOT the
  denormal-1 sentinels of unclipped geometries => clip-vertex channel
  values are rounded INDEPENDENTLY per channel (their sum != 1).
  The ctx3 words directly measure the summed per-channel value errors -
  use as fitting constraints.
- Per-channel value quantization round_W(exact-lerp): W=24 is best
  (79/120; ctx2 17/40 up from 9) - fit_clip_words5.py (scratchpad).
  The remainder needs the divider's t_hw INSIDE the rounded lerp
  (coupled unknowns): fit (t-law x W24-rounding) jointly on the
  apex-x sweep + t-readback words; ctx2 and ctx3 words carry the
  discriminating signal.
- NOTE: the earlier "no f32 value can reproduce B" argument excluded
  only W=24 SCALAR-t models; W=24 PER-CHANNEL rounding with hw-t is
  alive and now leading.

## 2026-08-13 (later 12): CLIP VALUE LAW = PER-CHANNEL RTZ24 OF WIDE-T LERP

Fit ladder on t-readback words (fit_clip_words*.py):
  exact-t shared lerp 73/120 -> per-channel RNE24 79 -> RTZ24 84/120
  (W=23:64, W=25:76 - sharp peak at 24; RTZ >> RNE/hup/up; f32-rounded
  t HURTS: 75 => t is wide/near-exact).  Position quantization RTZ24
  behaviorally identical on this set.
MODEL (current best, 84/120 with remaining misses almost all +-1 cell):
  guard-band clip at viewport+-512; SH polygon; cut values per channel
  = RTZ-24( v_u + t_wide * (v_w - v_u) ) rounded independently per
  channel; positions snapped half-up; then the fused wide setup chain.
  Remaining misses = t_hw vs t_exact in adjacent RTZ cells (divider
  refinement) + a few ctx2 outliers (dA +17/+47/-10 class).
The ruler geometries are adversarial (t near cell boundaries by
design); production t values are generic => NEXT: implement this
pipeline in the CPU probe general path (clip base arcs crossing the
guard band; postguard children are already pre-clipped/on-boundary),
run the 91-residual list + full PRED; if the 74 AGX bytes fit, port to
parity C + shader and run the flag-on gate (expect 18 presentation).

## 2026-08-13 (later 13): CLIP PIPELINE IMPLEMENTED IN THE CPU PROBE

analysis/probe_general_child_mask.c now implements the measured clip:
- clip_polygon_guard(): SH clip at viewport+-512 (planes x/y = -512 /
  2560); cut values = clip_rtz24(wide double lerp) PER CHANNEL; cut
  positions lerped in double; register_general_triangle() fans the
  polygon (0,i,i+1) and child_prepares each sub-triangle (only when a
  vertex lies strictly beyond the guard band - on-boundary geometry
  like the postguard children passes through unchanged).
  MAX_GENERAL_CHILDREN 64 -> 192.
Results on the 91-residual corpus list:
- bytes: vr=0/cm=1: 42 (was 40); vr=9/cm=1: 40.
- WORD-level vs residual-value capture2 (diff_words): vr=9/cm=0 now 37
  pixels all-6-words exact with ALL remaining misses UNIFORM +-1
  whole-plane shifts => evaluation law correct; per-tile C words for
  CLIPPED children off by +-1 (the C chain with lerped anchor values /
  big displacements needs the same measured refinement).
NEXT: validate my clipped-children C words per tile directly against
the residual-children dense capture (_rcd_obs.pkl has hw A/B/C per
(state, ordinal, ctx, tile) for production values); fit the C chain
for clipped children; then port clip+fused setup+evaluation to
parity/liquid_glass_raster.c + shader; flag-on gate (expect 18).

## 2026-08-13 (later 14): UNCLIPPED LAW CLOSED ON PRODUCTION; CLIP VALUES SOLE GAP

Word validation vs residual-children dense capture, split by child type
(probe clip pipeline, PROBE_C_MODE=0, RTZ24 clip values):
- PASSTHROUGH (unclipped, incl. all postguard children): AB 18/18
  EXACT.  The banked fused setup law is fully closed for unclipped
  production triangles.
- CLIPPED sub-children: AB 13/56; C tiles overall 24,561/33,736 with a
  +1-dominant residue invariant under output-rounding modes (cm sweep)
  => the remaining gap is ONLY the clip-vertex VALUE precision (t low
  bits / lerp rounding at production operand scales).
- RTZ24 clip values beat wide (PROBE_CLIP_VQ knob: 24561 vs 23549 C).
NEXT: invert t windows per failing clipped sub-child from its 4 AB
words (both channels; production endpoint values), regress t_hw on
production (num, den); close the t law; then C chain; then port to
parity C + shader.

## 2026-08-13 (later 15): PRODUCTION DIVIDER SCOPE = 3 CHILDREN (+4 COMPOUND)

Per-child production t-window inversion (invert_prod_t2.py ->
build/_prod_t_windows.pkl; windows in 2^-27 steps around exact):
- 8/11 bounded windows CONTAIN exact-t (and all divider candidates):
  s31 o6, s34 o2/o6, s39 o6, s42 o6, s47 o2, s58 o5 (both crossings).
  For these, exact rational t is CORRECT for the corpus.
- 3 children need non-exact t: s40 o2 [+10..+17], s44 o6 [-13..-6],
  s60 o4 [-15..-8] (x2^-27); ALL candidate dividers miss identically.
  These 3 windows + the v6/v7 ruler data are the divider law's
  remaining ground truth.
- 4 children with NO solutions (s35 o2, s42 o2, s45 o2, s58 o4):
  likely COMPOUND cuts (corner crossings re-cut by a second plane) that
  the inversion's t-offset keys did not sweep - extend clip3/keys to
  offset per plane-crossing including cut-of-cut vertices.
NEXT: (a) extend inversion to compound cuts; (b) fit the divider on the
3 windows + ruler datasets; (c) close the C chain for clipped children
(the +1-dominant residue: test C chain consuming WIDE lerp values with
RTZ24 only for slopes/numerators vs both); (d) port to parity C.

## 2026-08-13 (later 16): COMPOUND-CUT THEORY REFUTED; WIDE-ANCHOR NO-OP

- The 4 unsolved children (s35/s42/s45 o2, s58 o4) have plain 2-cut
  polygons; occurrence-indexed t sweeps +-60x2^-27 give ZERO solutions
  (invert_prod_t3.py) - their failure is structural, not t precision.
  Trying +-1-subpixel snap offsets on cut positions next
  (invert_prod_t4.py -> _prod_t_snapoff.pkl).
- Wide-anchor C chain (PROBE_C_WIDE_ANCHOR) is a NO-OP on the dense
  validation: these children's anchors are original on-screen vertices
  (exact values), so the +1-dominant C residue must come from the MID
  displacement products or the C-side numerator handling for clipped
  sub-triangles, not the anchor value.

## 2026-08-13 (later 17): *** CLIP FRAME RETRACTED - UNCLIPPED LAW STANDS ***

CRITICAL CORRECTION: the entire geometric-clip frame (entries later
4-16) was built on ONE hand-derivation error.  Recomputed: the
UNCLIPPED banked fused law reproduces the "provably clipped" sweep case
EXACTLY including the tiny B word (ctx0 (ba800000, b2c00060) - the
"clip lerp tiny" b2c00060 is the law's OWN near-cancellation residue).
Cross-capture rerun of the unclipped law:
- dual-lane-sweep-v2: ON-screen 1092/1092 EXACT; off-screen 63 exact +
  ~40 anomalies (Z/tiny/+-1 family).
- apex-x/t-readback (one-hot scalene, deep crossings): main A words
  +-1..4, B words 'far' - but those B words are TINY (near-cancelling
  cross numerators; one-hot values maximize cancellation).
- ALL production children fit the unclipped law on main words (the
  s40-o2 'window' fits were coincidental).
THERE IS NO GEOMETRIC CLIP.  The one remaining unknown is the
SUB-27-BIT BEHAVIOR OF NEAR-CANCELLING NUMERATOR SUMS (the tiny
outputs) and its knock-on +-1 drift in C tile words - the original
task-#2 cancellation theme at finer resolution.  All the tiny-word
observations across captures (basis, dense, sweeps, readback) are the
fitting corpus for it.
The probe's clip pipeline (register_general_triangle/clip_polygon_guard)
should be DISABLED for corpus work (it changed bytes 40->42 by luck);
keep for reference.  NEXT: fit the cancellation-residue law on the
tiny-word corpus; then the C-tile +-1 drift; then parity C port.

## 2026-08-13 (later 18): RESIDUE IS POSITION-DEPENDENT - POSITIONAL PLANE SOLVE

anchor-translate-sweep-plan-v1 (80 draws; s31-o6/o2-shaped triangles
translated in 8px steps; capture fae3f627...):
- The cancelled-axis tiny word CHANGES WITH PURE TRANSLATION (sign
  flips quasi-randomly, exponent -19..-27, mantissa varies smoothly-
  chaotically vs anchor position) while deltas/edges are translation-
  invariant => the hardware slope setup consumes ABSOLUTE VERTEX
  COORDINATES (a positional plane solve), and the near-cancellation
  residue inherits position low bits.  This unifies the slope-tiny
  family and the +-1 C-tile drift as one fused computation.
- Per-child residue-sign experiment (probe PROBE_TINY_RESIDUE knob):
  injecting other-axis*2^-26 with per-child best sign lifts C tiles
  24,561 -> 26,266 of 33,736; sign choice p26 for {35o2,40o2,58o4,
  58o5,60o4}, m26 for {42o2,44o6,47o2}, none for already-good children
  - DATA (not a law; the position dependence explains why no fixed
  sign works).
NEXT (the remaining unknown, now precisely shaped): recover the
positional plane-solve law - a 1-subpixel anchor-translate sweep to
read the residue's bit structure vs position (the 8px sweep is banked;
finer steps + both families + varying value scales pin the exact
column arithmetic).  Then rebuild slopes+C from the positional form,
validate across ALL banked captures, port to parity C, flag-on gate.

## 2026-08-13 (later 19): CANCELLATION RESIDUE IS A TRUNCATION-REMAINDER SAWTOOTH

anchor-translate-fine-plan-v1 (384 draws, 1-subpixel steps; capture
5c447347...):
- The cancelled-axis tiny is a LINEAR SAWTOOTH in the swept vertex's
  subpixel coordinate: increment ~ +3.067e9 x 2^-62 (~6.66e-10) per
  subpixel, wrapping at ~1.35e10 x 2^-62 (~4.4-subpixel period),
  sign symmetric around zero (two's-complement-style wrap).
  NOTE: in this fine sweep only v0 moves (shape changes - the o2
  family's axis-0 edge e_x = y0-614.5 sweeps), so the sawtooth tracks
  the OTHER AXIS's operand low bits: the residue = the truncated low
  part (discard) of a companion product pipeline, re-emerging scaled
  ~2^-26 in the cancelled axis.  Naive candidates (axis-0 first-product
  discard with delta=1.0 -> zero discard) do NOT explain it; increment
  magnitude is consistent with (main-slope x 2^-12-ish)/256 - the
  producing stage still to be identified (mid/C-pipeline discard is a
  candidate since displacement advances 1 subpixel per step).
- The 8px COARSE sweep (fae3f627) translated the WHOLE triangle
  (translation-invariant deltas/edges) and ALSO varied => position
  dependence via displacement/C-pipeline state confirmed there too.
NEXT: invert the sawtooth parameters (increment, wrap, phase) against
candidate producing stages using the 384-sample series (exact linear
algebra on the known operand advances); identify the stage; extend the
law; validate on basis+dense+sweeps; port to parity C; flag-on gate.

## 2026-08-13 (later 20): PURE TRANSLATION VARIES WORDS - SHOELACE FUSED ARRAY

pure-translate-plan-v1 (512 draws, whole-triangle 1-subpixel
translations; capture 81323c44...):
- DEFINITIVE: pure translation changes the words: xfam 197 / yfam 227
  DISTINCT (A,B) pairs over 256 steps: exact-zero-edge tinies come and
  go (00000000 <-> tiny), main words toggle +-1.  Absolute vertex
  position IS an input of the hardware setup ((later 18) stands; the
  fine sweep's v0-only motion was a confound but the conclusion holds).
UNIFYING HYPOTHESIS (fits every observation so far): the setup
computes det AND both slope numerators in ONE fused pass over
per-vertex SHOELACE products with ABSOLUTE coordinates
(n_x: +-v_i*y_j terms; n_y: +-v_i*x_j; det: +-x_i*y_j), each product
through the banked 27-bit stage; huge-product cancellation leaves the
position-dependent +-ulp/tiny residues, and lane truncation discards
leak across lanes at fixed column offsets (the 2^-26 same-mantissa
family = det-lane discard into numerator lanes).  Production's .0/.5
coords quantize exactly in most terms -> corpus main words exact.
FITTING CORPUS in hand: pure-translate (512), anchor-fine (384),
anchor-coarse (80), basis (96 ctx), dense (67k), sweeps (9k),
t-readback (120), apex-x (1080) - all with SHAs in this ledger.
NEXT: implement the shoelace-form numerator/det model with per-product
quantization + lane-leak offsets as fit parameters; solve on the
translate series first (linear structure), validate across all
captures, port to parity C, flag-on gate (74 AGX target; 18
presentation afterwards).

## 2026-08-13 (later 21): RESIDUE = SMALL-INT BANDS x DRIFTING QUANTUM

Pure-translate xfam A-tiny series (256 samples, _pure_translate_series
.pkl): tiny(k) = s(k) * m(k) * q(k) with
- m(k) in {0, 1, 2} (band structure; pattern like -2,0,-2,0,-2,0,+2,0,
  +2 then +-1 alternation runs, quasi-periodic ~4.4),
- s(k) alternating signs within the +-1 runs,
- q(k) a LINEARLY DRIFTING quantum: q ~ |B|*2^-26*(0.99324 +
  k*1.77e-6) (relative drift +1.77e-6 per subpixel of translation;
  0.9932 and the drift rate not yet identified with stage quantities -
  candidates around 27-bit numerator scales / coordinate magnitudes).
- yfam B-tiny has a DIFFERENT quantum scale (~1.7e-13 units, i.e. a
  different lane column) - per-axis lane offsets.
NEXT: exact-rational fit of (band sequence, sign rule, quantum) against
candidate fused-lane models using this series + anchor-fine series;
the model must also emit the +-1 main-word toggles (same mechanism at
the nonzero-numerator LSB).  Then C-chain integration, cross-capture
validation, parity C port, flag-on gate.

## 2026-08-13 (later 22): TOGGLE NOT DET-DISCARD; DRIFT CLUE OPEN

- yfam main-word LSB toggle sequence (256-bit, banked in
  _pure_translate_series.pkl) does NOT fit sums of shoelace det-product
  discards (best 143/256 over c=8..23, all 63 term subsets) - the
  carry driver is elsewhere (C-pipeline state, cross-channel, or a
  different operand form).
- Quantum drift clue (unidentified): q(k) proportional to
  (0.99324 + 1.77e-6*k); 1/(1.77e-6*256) ~ 2207 px - matches no obvious
  coordinate sum (|x2|+y1 = 2193.5 is 13.5 off); q(0)/(|B|*2^-26) =
  0.99324 ~ 1/1055.66 px vs base 1049 - also 0.6% off.  These two
  numbers are the sharpest unexplained constants; identifying them
  likely cracks the producing stage.
ALTERNATIVE PATH (bank for consideration): a corpus-tile isolation
capture - replay the ~15 corpus children with 1-ulp value sweeps and
dense tiles at the RESIDUAL pixels' tiles specifically, to over-
determine the residue at exactly the production operands; fit a law on
those captures (still input-only, but narrower generalization risk),
unblocking the 34 gated bytes while the general residue law matures.

## 2026-08-13 (later 23): QUANTUM STEP = EXACTLY 31/2^58 PER 2 SUBPIXELS

Exact-rational differencing of same-sign +-1-band xfam tinies
(156 step samples): the underlying value advances by EXACTLY 31/2^58
per 2 subpixels of translation (31/2^59 per subpixel; median exact,
odd-numerator/power-of-two).  This is the sharpest invariant of the
residue mechanism so far: the producing accumulator advances by an
odd count of 31 units per subpixel at column 2^-59 (raw-product LSB
scale; ~2^-49 relative to the slope's 2^-10 magnitude).  y-coordinate
advance rates (157312/425856 subpixel units; odd parts 1229/3327,
difference 2*1049) do not reduce to 31 by simple mods - the count is
likely a carry tally.  Data: _pure_translate_series.pkl.
STATUS at this point: corpus parity is gated on this residue law for
~34 of 74 AGX bytes; all other machinery (setup law for main words,
evaluation law, integration semantics, snap, C chain structure) is
validated and implemented in the CPU probe.  Presentation class (18)
is task #4 after the gate reaches ~18.

## 2026-08-13 (later 24): 31 = 15 + 16 - BIAS-CONSTANT FINGERPRINT

- 31 decomposes as 15 + 16: the banked first-product stage's bias(15)
  and truncation column(16) constants.  Under x-translation the n_y
  lane's shoelace products (v_i * x_j) advance by exactly -1 subpixel
  LSB per step - the natural unit driver.  Hypothesis for next
  stretch: a bit-accurate two-lane accumulator model with the banked
  bias/column constants (15/16, 20, carry+10) leaks (bias-sum = 31)
  units into the cancelled lane per unit driver advance at column
  2^-59; must reproduce: the 31/2^58 step, the 0/1/2 band structure,
  alternating signs, the 0.99324 quantum offset and its +1.77e-6/k
  drift, the yfam quantum at its own column, the main-word LSB toggle
  sequence, and the anchor-fine sawtooth (a ~ 6.72e-10, W ~ 2.94e-9).
  All series banked in _pure_translate_series.pkl and the fine/coarse
  captures.

## 2026-08-13 (later 25): RESIDUE LIVES ON THE RAW SELECTOR-PRODUCT GRID

Exact-arithmetic identification (xfam k=0 chain: first-product idx27 =
68747264 @2^-16; selector-product idx = 131019022 @2^-37 pre-RNE24;
B index24 = 16377378 @2^-34):
- The measured step 31/2^58 = EXACTLY 248 x 2^-61 where 2^-61 =
  2^(pe+se) is the RAW selector-product LSB (mand x sel before the
  27-bit shift): the residue accumulator advances EXACTLY 124 raw
  units per subpixel of translation.  The residue mechanism operates
  INSIDE the selector-product array (mand x sel partial products), not
  in the first-product stage.
- 124 = 4 x 31; interpretation open.  Approximate observation:
  124 x 2^-61 ~ quantum/(edge-subpixels) (1.7% off with float-precision
  measured quantum - re-derive the quantum exactly before trusting).
- Rejected exact fits: idx27 LSB (x0.0000148), idx24 LSB, first-product
  discard scales (all non-integer ratios).
NEXT: model the selector-product pps array with a position-coupled
input (the numerator operand carries position sub-LSB state? or the
array is shared across lanes with n_y x sel advancing -sel/2^26 raw
per subpixel) - test integer relations of 124 against sel(31974137),
det, e(268544), y-coords at EXACT precision; then the band/sign rules.

## 2026-08-13 (later 26): SECOND GRID POINT; RATE IS POSITION-DEPENDENT

- yfam (o2 shape, det 91872034816, sel 25098421): B-tiny same-sign
  steps cluster at 21/2^64 (and harmonics 43/2^66, 11/2^63) - the
  residue advances on a FINER grid (2^-64 = raw-sel-LSB * 2^-3) with a
  DIFFERENT count than xfam (which is 1984/2^64-per-2-subpixels =
  248 * 2^-61).  Steps drift in both families (xfam quantum drift
  +1.77e-6/k) => the advance RATE is itself position-dependent -
  linear product-advance models cannot produce this; the mechanism has
  a slowly-varying factor (drift scale ~1/2207px for xfam; det/2^25 =
  2149, |x2|+y1 = 2193.5 - near misses only, unidentified).
NEXT: build a parameterized fused-array simulator (shoelace operand
sets x lane offsets x banked truncation/bias constants) and fit
structure choices against the FULL exact xfam+yfam series (512
samples) rather than derived steps; the winning structure must also
reproduce the anchor-fine sawtooth and the basis-table tinies.

## 2026-08-13 (later 27): CARRIER IS A QUANTIZED STAIRCASE (EXACT-WINDOW PROOF)

- Exact RNE-window interval intersection proves the xfam +-1-band
  |tiny|(k) is NOT a pure line (infeasible in both sign classes at
  1e-7), while float LSQ residual was 2e-7: the underlying is a
  LINEAR CARRIER QUANTIZED TO THE RAW GRID (staircase; grid the
  31-count/2^-61-class quanta of (later 25)) - exactly an accumulator
  output.  Model to fit next: u(k) = quantize(a + b*k, g) with
  (a, b, g) dyadic; the split-sign windows + 0/+-2 bands + yfam series
  jointly overdetermine it.
- Monomial searches (2- and 3-factor over chain constants, n<=128,
  tol 2e-6) found NO product-form identification of (a, b) - additive/
  compound structure confirmed.
- Measured (float-level): a ~ 1.41087e-11, b ~ 5.3508e-17
  (b*2^61 ~ 123.38, b*2^64 ~ 987.04); a*2^65/sel = 16.2793.

## 2026-08-13 (later 28): STAIRCASE GRID 2^-61 LEADS; JOINT FIT NEXT

- Coarse staircase fit u(k) = floor((a0 + b0 k)/G)G: best G = 1x2^-61
  (63/126 windows; 21x2^-64 second at 61) - the raw-sel-grid staircase
  is the leading structure but floor/round mode, per-sign phase, and
  exact (a, b) need a JOINT exact-arithmetic fit (the b0/a0 seeds carry
  1e-7 error which staircase alignment amplifies).
NEXT STRETCH (concrete): joint fit over (a, b, G, round-mode,
per-sign/parity phases) with exact windows on xfam AND yfam
simultaneously; then generalize across the anchor-fine series; then
identify (a, b, G) against chain quantities; then the band/sign rules;
then C-chain integration and the parity C port.  Corpus: 34 AGX bytes
gated on this; everything else ready.

## 2026-08-13 (later 29): GENERATIVE MODEL = ONE ROTATING PHASE; ANCHOR-PRODUCT RATE

- xfam unfolded: tiny(k) = s(k)*m(k)*(q0+q1*k), m = 1 + c(k)*(-1)^k,
  with c a THREE-level (+1,0,-1) wave and s the sign wave - BOTH
  driven by ONE phase: exactly 25 quarter-cycle transitions in 256
  samples (alpha_x ~ 25/1024, period 40.96; three-distance gaps
  {9,10,11}).
- yfam: same construction, bands {0..3}, 137 transitions/256
  (alpha_y ~ 138/1024): RATIO alpha_y/alpha_x = 5.52 = 2 x
  (434176/157312) = 2 x (yfam ANCHOR X)/(xfam ANCHOR Y) - the phase
  advance rate tracks the ANCHOR POSITION PRODUCT (x_a*y_a advances at
  the cross-coordinate rate under translation).  Modulus not a clean
  power of 2 yet (157312/alpha_x = 6443467 not dyadic) - the exact
  modulus/scale still to pin (needs longer series or a third family
  for the CF).
NEXT: third translate family (different anchor coords) to triangulate
(alpha, modulus); then the amplitude/quantum laws per family; then the
full generative residue model into the C chain and the corpus test.

## 2026-08-13 (later 30): RATE FUNCTION HAS ZEROS - FAMILY TABLE

pure-translate-plan-v2 (capture 11feb047...):
- Family A (o6 shape, y anchor 400.5, same x-driver): residue
  IDENTICALLY ZERO across 256 subpixel translations.
- Family B (y anchor 900.25): residue present, q = 1.412e-11 (same
  quantum as xfam!), alpha = 0.0254 (~xfam's 0.0244).
- Family C (o2 shape, anchor x 1200, x2 = 16): IDENTICALLY ZERO
  (original o2 with anchor x 1696, x2 = 512 had residue).
- Family table so far (shape, anchor-cross, y/x odd-part structure ->
  rate): xfam(614.5/1663.5): 25/1024; B(900.25/1949.25): 26/1024;
  A(400.5/1449.5): 0; o2(1696, 512): 138/1024; C(1200, 16): 0.
  Quantum q identical for xfam/B (1.41e-11) despite different anchors.
- Zero families kill anchor-product-rate proportionality ((later 29)
  ratio hit was coincidental or conditional); the rate is a
  NUMBER-THEORETIC function of the coordinate sets (trailing-zero/
  odd-part structure; xfam/A both tz-7 in y yet differ -> finer bits
  matter).  With 5 families measured the rate function is becoming
  fittable; 2-3 more targeted families (vary ONE coordinate's low
  bits systematically) would tabulate it densely.
NEXT: coordinate-low-bit sweep families (y anchor 614.5 + n/256 for
n in 0..15, x-translate) to read the rate as a function of single-
coordinate bits directly; then close the rate/phase/quantum laws.

## 2026-08-13 (later 31): SLOPES AND C DECOUPLE - CORPUS GATES ON C ONLY

AB-oracle architecture test (inject MEASURED production A/B words as
numerators, build/_ab_oracle.txt):
- AB validation confirms injection (33,318/33,736).
- C tiles DROP to 19,618 (vs 24,561 clean); corpus bytes DROP to 30
  (vs 42): the C chain does NOT consume the slope-residue numerators -
  slopes and C are computed from SEPARATE numerator paths; C uses the
  CLEAN (law) numerators.
- Corpus implication: the residual pixels' uniform whole-plane +-1
  shifts are C-CHAIN errors under clean numerators.  The corpus is
  gated on the C-chain's +1-dominant error class (9,175 tiles), NOT on
  the slope residue.  The rotating-phase residue project only affects
  slope words (byte-relevant via feather only in edge cases).
NEXT: crack the C-chain +1 class with clean numerators - inspect
per-child C-delta maps vs tile coordinates (didx quantization stepping
patterns expected); the mid-product/didx handling is the suspect.

## 2026-08-13 (later 32): C IS AN INCREMENTAL TILE WALK - ERROR FIELDS MAPPED

Per-child C-delta tile maps (clean chain vs dense capture):
- s40 o2: smooth +1..+5 bands growing with tile distance from a
  zero-zone (~tx 26-38 at ty 0, boundary drifting ~1 tile/row,
  diagonal structure): the signature of INCREMENTAL tile-walk
  accumulation with per-step quantization vs my direct per-tile chain.
- s31 o6: delta identically 0 (direct chain == walk when steps exact).
- s58 o4: sparse vertical +1 stripes (tx 6,9,12,19,25,37...) + giant
  boundary-tile artifacts at the first column (exponent-far class).
HYPOTHESIS: hardware computes C at a seed tile via the direct chain,
then WALKS tiles adding quantized steps (step = slope*32 at C grid,
separate x/y walks, likely column-then-row); quantization per add
accumulates into the observed fields.  Fit (seed, step widths, walk
order, add rounding) per child against the full maps (33,736 tiles).
This is the CORPUS GATE (the residual pixels' +-1 C errors).

## 2026-08-13 (later 33): WALK MODEL 80-91% OUT OF THE GATE

fit_c_walk2.py: incremental-walk C model (seed tile value +
Q24-quantized steps sx = Q(A*32), sy = Q(B*32), per-step accumulation
at Wa) against the dense maps:
- s40 o2 ch0: 564/689 (Wq=24, Wa=28, order y-then-x, seed mid-left);
  ch1: 463/689; s58 o4 ch0: 1107/1214 (Wa exact).
- vs the direct per-tile chain's per-child numbers - the walk is the
  right FAMILY.  Remaining: true traversal structure (per-row reseeds,
  step recompute at row starts, boundary tiles, seed rule) - bounded
  discrete choices against 33,736-tile ground truth.
PLAN TO PARITY (updated, concrete):
1. Close the walk law (fit traversal variants; validate all children
   ~100%).
2. Implement walk-C in the probe; corpus bytes should jump toward 74
   (residual pixels' +-1 C errors are exactly the walk-vs-direct
   deltas).
3. Port general path (setup law + walk-C + evaluation law + draw-order
   containment) to parity/liquid_glass_raster.c + shader; flag-on gate
   -> expect 18.
4. Task #4 (18 presentation bytes) -> final gate 0 + ledger.
The slope-residue rotating-phase project (later 19-30) is now OFF the
corpus critical path (slopes' tinies affect bytes only via feather
edge cases; revisit only if the gate shows residue-class misses).

## 2026-08-13 (later 34): *** THE C WALK LAW (NEARLY COMPLETE) ***

MEASURED EXACTLY on s40 o2 ch0 (dense capture):
1. Hardware C tile words form a PERFECT 2D ARITHMETIC PROGRESSION:
   C(tx,ty) = seed + tx_rel*step_x + ty_rel*step_y on a fixed-point
   grid; export = RNE24 (words step by exactly 396947/2^24).
2. step = RTZ(slope_word * 32) at the 28-bit-significand grid of C's
   binade: step_x = floor(0.0236598886*2^28)/2^28 = 396947/2^24
   EXACT; step_y = RTZ(-0.537 grid units) = 0 EXACT (truncation
   TOWARD ZERO confirmed by 14 identical row seeds).  Grid re-derives
   at binade crossings (step denominators 2^24..2^31 across children's
   binades - all grid-true).
3. Wide-value comparison vs my direct chain: diff declines linearly to
   ZERO at tx=52 -> hw seeds the walk at a specific tile with the
   full (banked) C chain; seed rule = the remaining unknown (tx=52 for
   this child; anchor tile is 58; no obvious geometric marker yet -
   check other children: s58 o4 both axes, negative-slope cases).
This closes the corpus-gating C law modulo the seed rule.  The
implementation: compute C_chain at the seed; walk with RTZ28 steps of
the slope words; RNE24 for export/consumption.

## 2026-08-13 (later 35): C LAW REFRAME - DIRECT EVAL, NOT A WALK; tomography capture

RETRACTION of later-34's "walk" framing (the arithmetic-progression fit
was an artifact of coarse anchoring):
1. NEW CAPTURE: c-walk-tomography-plan-v1 (s58 o4 child redrawn with
   value-word key shifts; plan sha in manifest.json; capture.raw sha256
   f785aa14f280bb3181ee56f5f080fc45caf2f9268465c49c83523940fb1743dc).
   Family "seed" is a NO-OP (anchor ch1 value word = 00000000; key
   shift of 0 is identity) - only family "slope" (far-vertex ch1 word
   shifted k in [-20,20)) carries information.  Controls: base channels
   reproduce the dense capture 45/45 exactly (determinism confirmed).
2. s58 o4 ctx1 row 19 exposes the truth: C = plane evaluated at the
   TILE CORNER (y=608 above the top edge y=614.5 gives NEGATIVE
   bb593a5c) - C tiles are DIRECT per-tile evaluations
   C(tile) = S_int * (tile_corner - anchor), per-tile rounding; the
   "walk with hidden state" phenomenology = deterministic per-row
   quantization noise + a slope bias, NOT history.
3. MEASURED: v(ty)/exact_multiple ratio constant ~ (1 + 2^-24):
   hw internal slope is WIDER than the exported 24-bit word and sits
   ~ +4 lsb27 above my banked chain's idx27 (sel_oa pps+20 carry) for
   this child; the offset drifts slightly with k => hw numerator is
   kept wider than my 28-bit tap_rne as well (single late rounding).
4. Per-row structure: NO affine plane + RNE24 export fits (Fourier-
   Motzkin infeasible for most k) => intermediate rounding of the
   product before RNE24 export (double rounding), whose dropped-mass
   grows as didx gains low set bits (trailing-zero correlation) - a
   truncated-multiplier signature (pps/Booth-with-compensation).
   Model sweeps so far (widths 24..32, pps arg orders, carry 8..16,
   biases to 512, f32 chains, DDA/accumulator with free seed): best
   86% (bias 240-class), none exact => datapath form still open.
   Next: extract exact dropped-mass delta per (k, ty) from tight-ulp
   rows; test radix-4 Booth truncated multiply with sign-correction
   compensation.

## 2026-08-13 (later 36): C-PRODUCT TRUTH TABLE (major dataset + laws)

NEW CAPTURE: c-truthtable-plan-v1 (2610 draws; capture.raw sha256
20f331d807081c616f37738658e9b26218793e984023c62aa354b790e789d6ed).
Synthetic child: v0=(512,614.5,val 0)[anchor] v1=(2560,614.5,val 0)
v2=(2560,2662.5,val probe); edge=2048, det=2^38 subpx^2 (selector =
exact power of two => the capture isolates PRODUCT+EXPORT stages).
C(ty) must equal Q(delta * didx)/2048, didx = ty*8192-157312 (tz=7).
Probe words: 1.0, 1+2^-j (j=1..23), dense tails, 0.5x twins (0.5x
twins behave IDENTICALLY - binade-invariant).

HARD FACTS (bit-exact reads):
1. delta with <= 14 low-significant mantissa bits (product needs <=27
   result bits): hw C = RNE24(exact product), 45/45 rows each,
   including ties resolved to EVEN (j=13: +0.5 ties UP where up=even).
2. j=15 (28-bit products): hw = keep-top-24-of-M TRUNCATION toward
   zero (-0.5/-0.75/-0.375 signatures = RTZ of Mb=25/26/27), 25-bit
   ties broken TOWARD ZERO not to-even; EXCEPT Mb=24 rows split:
   ty24/25 export M exactly, ty26/27 export M-1 (same product frame,
   same binade, M odd in all four) - a Booth-digit/row-parity level
   discriminator that defeats every closed-form rounding tried.
3. j=18 (31-bit products, needs MORE precision): hw = RNE24(exact),
   45/45 incl. round-UP rows (NOT truncation) - so the rounding regime
   is not a simple function of product width either.
4. Model families swept to their ceilings (~88-89% of 2610): self-
   binade quantize W in 24..30 x rne/rtz/away/rna; fixed-48-frame
   truncation; pps partial truncation T in 12..24 with consts; radix-4
   Booth truncated (floor and faithful ~x+dropped-correction forms),
   both operand orientations; f32 fma chains; DDA accumulators with
   free sub-word seeds.  All plateau ~2300/2610; the same ~300
   observations (concentrated in j=15/19 and tail4/8 words) resist.
Scorer for candidates: analysis/score_c_chain_dense.py (full dense
capture 33,736 C-tiles; banked chain baseline 23,024-24,561 there).
NEXT (concrete): (a) explain the ty24/25-vs-ty26/27 Mb=24 split -
  candidates: sticky/guard from the 48-bit frame (bit 23 = M lsb) with
  round-to-odd-then-RNE24 double rounding, carry-save array parity;
  test round-to-odd at 25/26 bits then RNE24; (b) second truth-table
  capture with det NOT a power of two to expose the selector stage
  under the same probe series; (c) once the multiplier rule is exact
  on 2610/2610, re-run the s58-o4 tomography (1800 pts) and the dense
  33.7k scorer, then the corpus gate.

## 2026-08-13 (later 37): session close-out; best Booth variant so far

Per-sign partial rounding sweep: best = Booth(multiplier=didx24,
multiplicand=dm), B=20, positive partials FLOORED, negative partials
ROUND-HALF at B, K=0: 2340/2610 (89.7%) on the truth table - the best
closed form to date, still not exact.  The identification is now a
bounded search: a truncated radix-4 Booth array over dm x didx24 with
per-position correction/sign-extension handling, scored against three
frozen datasets in ascending strictness:
  1. c-truthtable-plan-v1: 2610 pts (sel transparent) - must be 100%
  2. c-walk-tomography-plan-v1: 1800 pts (sel=18280236 engaged)
  3. residual-children-dense-plan-v1: 33,736 C-tiles (all children)
then the corpus gate (expect the ~34 walk-gated bytes to flip green,
91 -> ~57 -> chase the rest).  All three datasets + loaders exist in
analysis/ (hunt_c_walk_seed.py loaders, score_c_chain_dense.py).
The Mb=24 ty24/25-vs-ty26/27 split (later-36 fact 2) remains the
sharpest single discriminator any candidate must pass.

## 2026-08-13 (later 38): NARROW C-PRODUCT LAW PROVEN; wide path cornered

NEW CAPTURES (all synthetic clean-selector children, column 60):
- c-truthtable2-plan-v1: anchor y=512.0, d = ty-16 in 1..47, height 2048
  fixed later to 4096 (v2 y=4608); capture.raw sha256
  dbd463c96bc92657a05f4866e59cdc0ff648eb41ee21ab4b82ae297367b50eaa
- c-truthtable3-plan-v1: same geometry, dm scans 0x3F800000+t (t=0..255)
  and +t<<8 (t=1..127); capture.raw sha256
  3fde603b0f56759f0c1525f3b122ff995d4cb4eea982df149e9a5f60871cc526
- c-truthtable4-plan-v1: anchor y=511.75 => d_o = 128*ty-2047 (8-13
  bits), height 4096; same word scans; capture.raw sha256
  9461fc6277836c48f5617ba57007cb8deb256987b550a49a231a247a062c2840

*** PROVEN LAW (narrow path): for products P = mant24 x didx_odd with
bit_length(P) <= 30: C = RNE24( rna27(P) ), where rna27 = round-half-
AWAY to 27 bits (the banked chain's narrow branch arithmetic).
EXACT on all 18001 tt3 points (products <= 30 bits). ***

WIDE PATH (bl >= 31) facts:
- tt4 (18001 pts): narrow law drops to 77-88% for bl 31..36.
- Deviations organized in contiguous dm-zones; at bl=31 hw rounds UP
  from dropped-fraction ~0.30 for ODD P, ~0.44 for even P (excess
  E ~ +25/+12 P-lsb at d_o=129, ~ +51 at d_o=257: E ~ 0.2*d_o-ish),
  yet ty63 (d_o=6017) shows NEGATIVE E, and rows are NOT constant-E.
- Killer datum: cell (dm=0x800C00, d_o=1793): P exactly representable
  after the cut (dropped=0), hw exports ONE LSB BELOW P (E in
  [-1536,-512] at bl=34; -P*2^-24 = -897 fits): consistent with a
  one-lsb-low selector/slope operand for THAT cell; but a global
  P*(1-2^-24) deficit contradicts tt1 wide rows (ty24/25 exact).
- Cascades (P->W1->27->24 all mode combos), Booth variants, preshift,
  postround, linear-in-dropped-bits (Kaczmarz feasibility): none fit.
Corpus relevance: real children numerators are ~28 bits x didx 9-12
bit odd parts => products 36-40 bits: corpus C tiles are ALL wide-path.
The wide law is the last gate for the ~34 AGX corpus bytes.
