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

## 2026-08-13 (later 39): wide-path X-function partially mapped; session end

Wide-path (bl>=31) hw value = P + X(dm, d_o, ...) before rna27+RNE24.
X measurements (tt4, exact intervals):
- ty17/18/20 (d_o=129/257/513), odd P: X constant per row, = +26*(ty-16)
  within +-1, i.e. X ~ +13*(d_o-1)/64 = +P*13*2^-29 (fits rows 17-20).
- even-P: smaller, varies within row (not constant per (row,parity)).
- larger d_o rows: X turns NEGATIVE (ty63 etc.); per-(bl,parity)
  constants all infeasible; linear-in-dropped-bits infeasible
  (dropped=0 cells have X != 0).
- killer cell (dm=0x800C00, d_o=1793, dropped=0): X in [-1536,-512]
  (~ -1024 = signed-low-12 of dm; but +t-block cells contradict plain
  sdm12 forms: 'wide + sdm12', '-|sdm12|', widths 11-13 all score
  WORSE than X=0 globally).
- flip-based X extraction disagrees with interval-based (phase-drift
  artifact suspected in the flip method - see later-39 scripts); trust
  intervals; redo flip method with per-t phase correction.
NEXT SESSION: (1) build the exact X-table from tt4 intervals cell by
cell (only boundary-adjacent cells pin X; collect the ~2k tight cells
across rows into (dm, d_o, X) triples); (2) test 'rounded-operand'
family: dm' = round(dm, N bits) or d_o' = round(d_o, N), P' = dm'*d_o'
with correction; (3) once tt4+tt1 both 100%: tomography (sel stage),
dense 33.7k, corpus gate.  Narrow law (later-38) remains PROVEN.

## 2026-08-13 (later 40): iterated-walk revival tested and bounded

Tested: acc(k+1) = rna27(acc(k) + full-precision step), seeded at the
anchor tile, export RNE24 - scores tt4 12447/18001, tt3 15605/18001,
tt1 2008/2552.  tt3's perfection under the DIRECT law (later-38)
proves hw does NOT per-step-round at 27 bits; and the killer cell's
X ~ -1024 at bl=34 over <=14 tile steps excludes wide-accumulator
(W>=31) walks (error budget ±2/step).  Conclusion: no uniform
iterated-add law; the wide path is a direct computation whose X
function remains the one open item.  The three frozen datasets
(tt3 exact / tt4 dense-scan / tt1 mixed) + the X facts of later-39
fully specify the identification problem.

## 2026-08-14 (later 41): presentation class (task #4) characterized

The 18 presentation pixels verified against build/_residual_list.txt:
all are +-1 byte deltas, and the sign SPLITS: 14 have apple = walle-1
(s40, s41, s42 x10, s58, s60) and 4 have apple = walle+1 (s31:249:1628,
s33:70:1639, s42:1838:106, s42:259:2011).  Under the banked second-
stage model round255(h16(h16(primary) * secondary)), secondary in
{0x3C00, 0x3BFF} per (tile row x primitive), BOTH renderers apply a
tile-local secondary; the 18 pixels are tiles where apple's and
walle's selections DISAGREE in either direction.  walle's 0x3BFF
arises inside the coverage alpha (f16 alpha_half_bits, see
parity/test_liquid_glass_reveal_mask_model.c case 0x3bff); apple's
comes from the transfer draw's interpolated alpha-plane TILE CONSTANT
(f16 export of the AGX setup C for the alpha varying).  Closing #4
therefore needs the transfer draw's per-state alpha-plane geometry
(captured in the a2-geometry artifacts) plus the f16 C-tile export
law - the same setup laws now being closed for R8, at f16 precision.
NOTE: the old classifier _analyze_reveal_second_stage.py no longer
runs (SelectorTableOverride API drift in
analysis/liquid_glass_runtime_raster_coefficients).
Wide-path law search delegated to a solver subagent (brief:
analysis/WIDE_PATH_SOLVER_BRIEF.md; falsification log:
analysis/wide_solver_log.md).

## 2026-08-14 (later 42): task #4 inputs located - captured A2 transfer draws

/tmp/walle-analysis/A2-geometry-sweep-v74/state-N/reveal-mask-trace.json
holds, per state, the UNMODIFIED captured CoreAnimation transfer draw
(pipeline com.apple.coreanimation.PBGRAXm_A2Xghfc): 16 vertices x 48B
stride (vertexStreamHex + sha), 48 indices (4x4 grid mesh, 16 tris,
indexStreamHex), viewport/scissor.  Vertex layout begins
posX posY z w (f32) | f32 pair | TWO f16 varyings (0x3C00 1.0 seen) |
trailing f32s - the f16 varyings are the alpha/factor plane whose
AGX tile constants become the "secondary" (0x3C00 vs 0x3BFF).
PLAN for #4: decode the 48B layout across states; feed each state's
16-tri mesh through the PROVEN narrow setup law (f16 products are
narrow) to produce per-tile f16 alpha constants; select secondary =
that constant; verify the 18 residual pixels flip to apple's byte and
nothing else moves (corpus gate).  This makes #4 independent of the
wide-path law (task #3).

## 2026-08-14 (later 43): A2 transfer vertex layout decoded (state-42)

48B stride, 16 verts = 4x4 grid with DUPLICATED middle row/col (seams):
x in {-909, 512, 512, 1933}, y in {-806.5, 614.5, 614.5, 2035.5} - the
center cell is the reveal bbox and the seams sit exactly on the corpus
children's anchor (512, 614.5).  Layout per vertex (f32 LE):
[0] posX [1] posY [2] 0.0 [3] 1.0 [4] uvX=posX+1 [5] uvY=posY+1
[6] ndcX [7] ndcY (= +-1.00070 = 0xbf801713-class, or 0)
[8..9] varying pair (v0 bytes 003c003c... - alignment vs f16 pair
(0x3C00,0x3C00) still to be pinned against the shader's input layout)
[10..11] tail (uninitialized-looking, varies per vert).
NEXT for #4: pin slots [8..11] against the PBGRAXm_A2Xghfc vertex
descriptor (fresh-native-scale-a2-resource-trace-v103 may hold it),
then run the narrow setup law over the 16 triangles per state.

## 2026-08-14 (later 44): TASK #4 MECHANISM PROVEN ON HARDWARE

New capture a2-transfer-residue-plan-v1 (state-42 transfer triangles
2 and 6 redrawn via the setup probe with all-1.0 f32 varyings; 45
draws; capture.raw sha256
3850f4354d1b27d0fc8c5da56ed65ddd0a063da54eef7f9acdcfa40a388e70fa):
1. The constant-1.0 varying does NOT set up as zeros: tri 2 exports
   A=0, B=2e68b4e5/2e68b4e4 (residue ~ +2^-34, tile-dependent low
   bit); tris 6/7 export A=B=00000001 (DENORMAL residue slopes).
2. C tile words: 3f7fffff (1 - 2^-24) at exactly the tiles hosting
   the nine y<=31 state-42 residuals; 3f800000 elsewhere probed.
3. SELECTION LAW CONFIRMED: secondary factor = f16-RTZ of the
   interpolated residue plane per pixel (center-RTZ evaluation);
   signs reproduce 11/12 state-42 residuals from first principles:
   - y<=31 band: C=3f7fffff => value < 1 => 0x3BFF (apple=walle-1) OK
   - (1838,106): C=3f800000, B>0 => value >= 1 => 0x3C00 (+1) OK
   - (259,2011) tri 6: denormal positive slopes => value >= 1 (+1) OK
   - (1837,103) (-1) NOT yet: same tile as (1838,106) with C=1.0 and
     B>0 cannot split; the real draw feeds verts through the CA
     vertex shader NDC path (slots [6,7] = +-1.00070) and the
     rasterizer's NDC->screen conversion produces slightly different
     fixed-point verts than the direct pixel feed - next capture:
     feed NDC-transformed vertex positions and re-read the plane.
(1837,103) and (1838,106) share tile (57,3) with opposite corpus
directions - killing any per-tile-constant secondary model; the
per-pixel f16-RTZ law is the only survivor and is now hw-verified.

## 2026-08-14 (later 45): #4 narrowed to the vertex-shader varying ulps

Captures a2-transfer-values-plan-v1/v2/v3 (variant probe, per-pixel
iter center values; shas 44186754/82f2508a/b70093ce):
- v1 (buffer coords, varyings exactly 1.0): center = 3f7fffff exactly
  at the nine y<=31 residuals (f16-RTZ -> 0x3BFF ok) and 3f800000
  across all of tile (57,3) - (1837,103) unexplained.
- v2 (intendedPhysicalFrameEdges verts): everything 1.0 - the real
  rasterized verts are the BUFFER coords, not the physical mapping.
- v3 (8 half-pixel offset candidates x key pixels): x-shifts preserve
  the band (A=0), y-shifts kill it, nothing flips (1837,103).
CONCLUSION: the remaining knob is the VARYING VALUES: the CA vertex
shader (VfxXgh) emits alpha = 1.0 +- few f32 ulps per vertex (from
its uniform transform arithmetic), which shifts the plane at the
2^-32 scale needed to split (1837,103) [<1] from (1838,106) [>=1]
within one tile.  The uniform buffer is captured (v103 trace:
uniformBufferLength=48, recordOffset=96) - next: recover the real
uniform words, reproduce VfxXgh's arithmetic for the four tri-2/6
verts, feed the resulting varying words to the value probe, expect
12/12 state-42 signs; then sweep the remaining 5 states' pixels
(31/33/40/41/58/60) the same way.

## 2026-08-14 (later 46): varying-ulp scan; #4 constraint-solve plan

Capture a2-transfer-values-plan-v4 (sha 1d6d02c7...): tri 2 with all
125 combos of per-vertex varying words 1.0 +- {0..2} ulps, 6
discriminator pixels.  Response surface (banked in the plan dir):
baseline (0,0,0) reproduces band-v/controls-ok; NO combo splits
(1837,103) [v] from (1838,106) [.]: with per-vertex-constant words
the crossing line lands outside the pair every time.  Constraint
algebra: the split requires an OBLIQUE deficit plane (nonzero A tilt;
v3-deficit alone gives y* = 614.5 - 1421/k for integer k in 2^-25
units: k=3 -> 140.8, k=2 -> -96, neither in (103,106]), and the iter
output rounds at the 1 - 2^-25 boundary (f32-RNE of the wide plane).
NEXT (pure CPU, no M1 needed): dump walle's per-pixel primary f16 +
own secondary for state 42 from the parity CPU model; every
secondary-SENSITIVE pixel where apple==walle pins apple's secondary
= walle's; the 12 residuals pin the opposite; hundreds of pins =>
solve apple's deficit plane (A, B, C_int) exactly per triangle; then
validate across states 31/33/40/41/58/60 and derive the input-only
generation rule (CA vertex-shader arithmetic) from the solved planes.

## 2026-08-14 (later 47): integrator: 3-stage cascade lead (best rule to date)

Bias algebra unifies both threshold measurements: rna-stage biases add
as 2^(29-W) units of 2^(bl-30); measured totals (mine 13 absolute,
solvers' 9 above the narrow law's 4) = stages {29, 27, 26}.  Scored
wide-only cascade rna29->rna27->rna26->RNE24: tt4 14524/18001 (best
known; baseline 13876), tt3 intact 18001, tt1 2232 (from 2300).
Misses: 76% at phase-0 of the 29-cut; even-dm 3-4x worse than odd
(exact-on-grid mistreatment = the killer-cell family).  "-1 before
round" at the 26-stage flips the trade (tt1 2304-2310 > baseline,
tt4 drops) - every variant trades tt4 vs tt1, and the only systematic
input difference is didx trailing zeros (6/7/13).  Handed to the
solvers: sweep tz-parameterized final stage + explicit phase-0
handling under the bias-sum constraint.

## 2026-08-14 (later 48): CORRECTION - the bl(P)<=30 narrow gate is falsified

Track B (wide-law-solver2, commit 0890570) falsified the later-38
phrasing "narrow law PROVEN for products <= 30 bits"; I verified the
counterexamples directly against tt1's raw capture:
  dm=0x800004 d_o=51 (bl 29): hw = narrow-law + 1 lsb
  dm=0x800010 d_o=51 (bl 29): hw = narrow-law - 1 lsb
  dm=0x80000F d_o=115 (bl 30): hw = narrow-law - 1 lsb
tt3's 18001/18001 exactness is an ALIGNMENT artifact (its
displacements carry subpixel tz=13, so every frame column below the
injection point survives), not a product-width fact.  Structural
reframe (Track B): C = narrow(P + K*2^(T-sh_f)) with sh_f = 24 -
bl(odd(d_o)) - tz(d_o), T=17, K=9 scores tt4 14850 / tt3 18001
(DERIVED) / tt1 2226 - no bl side condition anywhere.  Further Track
B facts: tt4 and tt1 need OPPOSITE-SIGN compensation (deviation
censuses +1-dominated vs -1-dominated; no single K serves both; tz
parity is the only systematic difference); hw round-up thresholds are
PARITY-DEPENDENT (27v even-M / 19v odd-M, v = 2^(cut-6)); a ceiling
theorem bounds every dm-only bias family below tt4 17.3k (extra
dependence at bl=36, counterexample dm=0x800F00); the killer cell
remains outside all current laws.  Corpus-side note: the parity
narrow-branch port (rna27 half-up) remains gate-neutral at 91, but
its correctness domain must be restated in frame terms before task
#8 flips the general path.

## 2026-08-14 (later 50, task #4): presentation class is 11 px, solved

The dense-constraint solve of the A2 transfer plane is done (brief:
analysis/A2_PLANE_SOLVER_BRIEF.md; falsification log:
analysis/a2_solver_log.md; scripts analysis/a2_solver_*.py).

FIRST: the Python public-raster model runs again.  The later-41
"SelectorTableOverride API drift" is a missing 20-line helper class, not a
changed algorithm - analysis/liquid_glass_runtime_raster_coefficients.py
reads a selector table through len() and one [index] only.  The shim in
analysis/a2_solver_primary.py restores render_primary_half /
_overlay_triangle / score_reveal_v74_public_raster, and the restored model
reproduces build/_residual_list.txt per state exactly (31:5 33:2 34:4 35:4
39:1 40:12 41:1 42:34 44:3 45:4 47:2 58:11 60:8, all |delta|=1).

CENSUS (a2_solver_census.py, all 51 border-grid states): 10,486 pixels are
"sensitive" (the two secondaries give different bytes); 45 of them carry
apple's 0x3BFF; ZERO match neither candidate.  That independently confirms
byte = round255(h16(h16(primary)*secondary)) and walle's primary.

The later-41 census of "18 presentation pixels" was wrong.  A secondary
<= 1.0 can only LOWER a byte, so every apple = walle+1 residual is a primary
residual by construction - including (1838,106) and (259,2011), which
later-44/46 used as plane constraints.  Removing (1838,106) removes the
"per-tile constants are dead" blocker AND the later-46 obliqueness argument.

THE SPLIT (a2_solver_plane.py, exact integer cones; a2_solver_slope_cone.py;
a2_solver_tile_offsets.py):
- 11 px = state 42 transfer tile (56,0), triangle 2: the ONLY tile-filling
  LOW cluster in the corpus (11 LOW / 0 HIGH).  Solved plane cone (g = D -
  2^-25 on doubled pixel centres) is simplicial with extreme rays
  (-1116,250,2008179), (-185,41,333158), (6,-4,-10713); it contains the
  pure-y family whose crossing row is pinned to Y* in (31.5, 34.5] - i.e.
  the 32-row tile boundary, matching later-44's measured C words
  (3f7fffff in tile row 0, 3f800000 in tile row 1) exactly.
- 34 px = isolated LOW pixels inside otherwise-HIGH tiles (states 33/35/40/
  41/42/44/45/58/60).  NOT the transfer plane: adding (1837,103) makes
  state 42's cone INFEASIBLE; states 40/58/60 are infeasible for any affine
  plane per triangle, for any per-tile constant at any tile size 8..128 and
  any phase, and for the AGX per-tile-constant + shared-slope shape down to
  4x4 tiles.  Two of them share one 2x2 quad with opposite labels
  ((1852,434)/(1852,435) in state 40).  These are one-binary16-ulp PRIMARY
  residuals - the same class as the 54 insensitive residuals - visible only
  because a byte boundary sits under them.  Also falsified: alternate
  binary16->unorm8 conversion laws, and "walle's binary32 alpha sits on its
  binary16 rounding boundary" (flip headroom is 0.3-2.5 binary32 distance
  ulps at LOW pixels while HIGH pixels needing 0.002 ulps did not flip).

VALIDATION (a2_solver_validate.py): applying the solved plane over all 51
states takes the corpus from 91 to 80 residuals - state 42 34 -> 23, every
other state unchanged, zero regressions.

REMAINING for #4: the input-only generation rule is now a single question -
which transfer tiles export C = 1 - 2^-24 for a constant-1.0 varying.  That
is the task #2/#3 two-product cancellation residue evaluated at binary32 on
the transfer triangle, not a new unknown; the hardware already produced the
right answer for state 42 (later-44).  The other 80 residuals belong to the
raster/interpolator programme (#3), which is now 80 pixels, not 73.

## 2026-08-14 (later 49): K(tz) tabulated empirically (Track B, 5 new captures)

Track B (commits d63e254, 0d3eb54; capture shas in
analysis/wide_solver_log.md B8) cloned tt4's geometry varying ONLY the
anchor subpixel position to set the displacement trailing-zero class:
tz in {3,4,5,8,9} (+ originals 6/7/13).  Scales calibrated from the
captures (all landed on predicted tz-20).  RESULT: best global
compensation K in narrow(P + K*2^(bl-30)) is a function of tz, NOT of
cut and NOT of bl(d_o) (confound broken: bl(d_o)=10 occurs at five
different tz):
    tz:  3    4    5    6    7    8    9    13
    K:  -4    0   -7   +9   -2   -4   -4    0
Thresholds sharp where separable (tz=6: +9.000 ulp30; tz=5: -7.06);
tz=8/9 admit no separable bracket.  CONSEQUENCES (propagated):
(a) "total bias 13 = {29,27,26}" is a tz=6-regime statement only;
(b) the bl=36 anomaly is a DISPLACEMENT-width effect: every class
changes behavior on the diagonal bl(disp)=19 (tz6 flips +9->-9,
tz5 -7->+1, tz8/9 ->-4).  FALSIFIED: fixed-frame column truncation +
normalizer-side compensation (family ceiling 84.8% joint; tt3 holdout
breaks exactly at T=14, confirming the tz=13 gate).
OPEN CONTROL (ordered): every tz class so far uses ONE anchor; K=f(tz)
vs K=f(anchor bit pattern) is not yet separated.  Next captures: same
tz with different odd parts and different subpixel fractions; tz
10/11/12 to bracket the decay to zero at 13; K vs bl(disp) along the
diagonal.

## 2026-08-14 (later 51): integrator verification of the later-50 solve

Re-ran analysis/a2_solver_validate.py independently: TOTAL residuals
91 -> 80, state 42 only (34 -> 23), zero regressions.  ACCEPTED.
Corrections to my earlier entries hereby adopted: later-41's "18
presentation pixels" census retracted (walle has NO secondary stage,
so apple = walle+1 residuals are primary-class by construction);
later-44's "per-tile-constants dead" blocker and later-46's oblique-
plane requirement rested on (1838,106) and are retracted with it; the
later-44 hardware C words (3f7fffff at tile row 0 / 3f800000 at
(57,3)) agree with the solved plane exactly - the hardware was right
and my constraint set was over-fit.  New campaign arithmetic:
80 raster residuals (task #3/#8 target) + 11 presentation (solved at
model level) = 91.  Remaining for #4: (a) input-only generation rule
for which transfer tiles export C = 1 - 2^-24 given constant-1.0
varyings = the banked two-product cancellation residue at binary32 on
the transfer triangles (state-42 hardware verification already in
later-44); validate against the full 45-pixel sensitive-3BFF census;
(b) implement apple's secondary stage in the parity model + renderer;
(c) corpus gate expecting 80.

## 2026-08-14 (later 52): adjudication of T=17 K=9; Track A complete

ADJUDICATION (integrator rerun, both reproduced exactly): the banked
"T=17 K=9 keeps tt3 at 18001 (DERIVED)" holds ONLY under an inject-
zero-when-fractional convention (sh_f > T => nothing added): tt3
18001/18001.  Under an honest fractional add tt3 = 16561/18001
(Track A's number).  The cutoff is itself a gate needing hardware
justification; later-48's "derived" is downgraded to "derived given
the cutoff convention".  Track A's structural point adopted: in the
normalized didx24 frame nothing is simultaneously tt3-exact-without-
a-gate and large enough to matter; the raw subpixel frame R = dm*disp
(trailing zeros tt3 13..18, tt4 6, tt1 7) is structurally tt3-safe
for cuts T <= 13 but bounded ~2^13, unreachable from the killer
cell's [-102400, -28673] granule excursion.

TRACK A COMPLETE (commit 1db4e24): segmented multiplier falsified -
36,000 configs ceiling tt4 14506/tt3 18001/tt1 2240; recursive
variant 2,160 configs ceiling 14504/18001/2240.  BORROW HYPOTHESIS
CONFIRMED MECHANICALLY: exactly 360/36000 configs reproduce the
killer cell and every one uses a SIGNED low segment (borrow) - but
those configs score below the narrow law on the bulk (12971/2164):
the bulk-fitting and killer-explaining configs are disjoint.
CARRY-FORWARD: the true law's low-order term is signed and carries a
borrow, but is not combined with the high partial as a plain
two-segment product - e.g. it may modulate the ROUNDING stage rather
than the product.

## 2026-08-14 (later 53): BASIS-PLANE CAPTURE - the campaign converges to one problem

Capture a2-basis-planes-plan-v1 (tri 2 and tri 6 of state-42's
transfer mesh, varyings one-hot (1,0,0)/(0,1,0)/(0,0,1) + all-1.0
control, 28 draws; capture.raw sha256
328bfa89827f9eae50fe938cf403c0014789087eb46b08fb6f237630bef42483):
1. hw computes PER-VERTEX BASIS PLANES and sums them internally:
   the all-1.0 exports equal the (internal) sum of the three one-hot
   exports - B_sum = 2e68b4e4/e5 is NOT the f32 sum of the exported
   basis words (that cancels exactly); the sum happens at internal
   precision.
2. *** THE BASIS SLOPE WORDS VARY PER TILE ***: basis1's B = 3a387a81
   at tiles (31,15)/(50,9) but 3a387a82 at (57,0)/(57,3) - a plane
   CONSTANT wobbling +-1 ulp with the tile.  This is the banked dense-
   capture "1-ulp main-slope miss" mystery (3a6b0059 vs 3a6b0058)
   observed in isolation on the simplest possible signal.
3. Residue tinies appear on exact-zero axes (basis2 B = 2d6693e4 at
   some tiles, 0 at others; tri6 basis0 A = 2e387a82 likewise).
4. tri6's all-1.0 sum exports A = B = 0x00000001 (DENORMAL floor):
   sub-denormal-scale nonzero internal sums clamp to the minimum
   denormal rather than 0.
CONSEQUENCE: task #4's generation rule and task #3's wide-path law are
THE SAME unknown - the per-tile derivation of the coefficient RAM
(slopes and C).  The per-tile SLOPE wobble is the cleanest instrument:
no didx product, numerator constant, output = the per-tile stage's
rounding alone.  Next: dense per-tile slope-word map over a whole
triangle.

## 2026-08-14 (later 54): the per-tile internal drift law measured exactly

Dense wobble map a2-slope-wobble-plan-v1 (729 tiles of state-42 tri 2,
basis1 varying; capture.raw sha256
21365468281ba5d583c8b55989efb9a7046b90c20786e240f06d165b108664da):
the exported B word takes exactly two values (3a387a81/82) split by a
boundary that satisfies *** tx - floor(ty/4) = 56 EXACTLY *** (18
rows, zero exceptions; least-squares slope 0.2477 ~ 1/4 with clean
4-row steps).  Therefore the internal per-tile slope value =
s0 + g*(tx - floor(ty/4)): the coefficient traversal advances with
+g bias per tile-x step and -g per FOUR-ROW BLOCK step (equal
magnitude, opposite sign) - i.e. the parameter-buffer walk processes
4-row tile bands, and both step kinds carry a single rounding bias g
with opposite signs.  Also measured: hardware self-consistency
C(t+1y) = RNE24(C(t) + 32*B(t)) holds 623/684 (deltas +1 x41, -3
x19) - the coefficient RAM is near-affine in the exports but the
internals drift per the block law.  Next discriminators: (a) taller/
wider triangle - does the 4-row blocking persist; (b) asymmetric
slopes (A != B) - how the two biases scale with step values; then
re-derive the C X-function as the SAME walk's accumulated bias, which
would unify tasks #3 and #4 into one implementable law.

## 2026-08-14 (later 55): wobble is join-path-only; the last unknown localized

Capture a2-slope-wobble-plan-v2 (basis0 and basis2 dense maps, 1458
draws; capture.raw sha256
50f3a361162b4b558311c182b890e253ab532ad9f113af1d2debb069ed446af6):
single-slope basis planes (numerator = ONE product; the passthrough
class walle already reproduces 18/18) export CONSTANT slope words at
all 729 tiles - NO wobble.  Only basis1 (numerator = a JOIN of two
products; the general class holding all 80 raster residuals) wobbles,
with the later-54 block law g*(tx - floor(ty/4)).  UNIFIED PICTURE:
the per-tile coefficient RAM for JOINED numerators is produced by a
4-row-block tile traversal whose accumulator carries a per-step bias
(+g per tile-x, -g per 4-row block); single-product paths are exact.
The campaign's one remaining unknown for all 91 bytes is this
traversal's arithmetic, constrained by: the K(tz) table (later-49),
the parity thresholds 27v/19v (Track A/B), the bl(disp)=19 diagonal,
the killer cell, the phase-0 census, the borrow carry-forward
(later-52), and now the exact block drift law (later-54/55).
Next: (a) map basis1's A word across the same tiles (is the drift on
one lane or both); (b) an asymmetric triangle (A != B) to scale g
against step values; (c) express the C X-function as the accumulated
block-walk bias and rescore tt1/tt3/tt4.

## 2026-08-14 (later 56): the join-path wobble is a DISCRETE fixed-position jump

Capture a2-wobble-gscan-plan-v1 (v1-value dm-scan k=-20..19 x tile
transects ty=0/8; 2480 draws; capture.raw sha256
6cf461122aa583c9b24a388bc3a90b52716a9fc69b9d173d438f9d82fef30c5f):
1. The wobble boundary does NOT move with the value word: whenever a
   within-row transition is visible it sits at tx*=56 (ty=0) and
   tx*=58 (ty=8) for every k - both = u* = 56 in the block metric
   u = tx - floor(ty/4).  A continuous internal drift would sweep the
   crossing with the value's sub-ulp phase; it does not.
2. Therefore the internal carries a DISCRETE jump at a fixed
   traversal position (u >= 56): word transitions appear only for k
   whose phase straddles a rounding boundary across the jump;
   13/39 k values show it => jump magnitude ~ 0.33-0.36 ulp24
   ~ 3 lsb27.  The exported slope word is otherwise CONSTANT along
   the band (exact steps), reconfirming 4-row blocking with the
   boundary advancing +1 tile per block.
3. basis1's A lane shows NO wobble anywhere (drift on one lane only),
   and tt4's geometry (single-product numerator) has C deviations
   without slope wobble: the mid-product X law and the join-path jump
   are SEPARATE per-tile effects that compose for corpus children.
OPEN: what u* = 56 is geometrically (not parallel to any edge; the
anchor sits deep inside the jump region) - the asymmetric-triangle
and translated-geometry captures will pin whether u* tracks the
anchor, the edge, or a fixed superblock grid.

## 2026-08-14 (later 57): the jump is geometry-anchored; frame pinned next

Capture a2-wobble-translate-plan-v1 (x-translations 0/-1/-2/-8 tiles;
232 draws; capture.raw sha256
73f8fd01c77a3f8ef2d01f6c81e3d6ebadeae59c28c69a283dc7ba3fbfb05e7d):
- Shifts -1/-2 move the jump boundary EXACTLY with the geometry
  (tx* 56 -> 55 -> 54 at ty=0; 57 -> 56 -> 55 at ty=4): the jump
  position is ANCHOR-RELATIVE, not a screen-fixed superblock grid.
- Shift -8 (256px) REORGANIZES the pattern (tx* = 32 at ty=0, 27 at
  ty=4 - not 48/49, and the per-block advance flips sign): the
  position is not linear in the anchor either; candidate mechanism:
  the jump sits where a displacement quantity crosses a power of two
  (didx_x + didx_y/4 = 342.6px fits cases 0/-1/-2 at both ty but NOT
  case -8; case -8 may also expose a second transition my single-
  boundary finder conflated - needs a full map, not transects).
NEXT SESSION: full 2-D wobble maps for the -8 case and a +8 case;
didx-threshold hypothesis (power-of-2 crossings of the mid-product);
asymmetric triangle for g/u* scaling; then the composed model
(mid-product X law + join jump) rescored on tt1/tt3/tt4 and the
dense corpus captures.

## 2026-08-14 (later 58): jump position = displacement binade crossing (partial law)

Capture a2-wobble-map8-plan-v1 (full 2-D maps, shifts -8 and +4 tiles;
1349 draws; capture.raw sha256
7d6ff22087fea8ad59d33fc3278e2eeafa99113dc7f2843f9feac0b0c6ac2169):
- Shift +4: identical structure, u* = 60 = 56+4 (geometry-locked,
  confirming later-57).
- Shift -8: boundary ORIENTATION REVERSES: first-X tx per row falls
  ~-5/4 tiles/row (32 at ty0 -> 8 at ty18) vs +1/4 in the base case.
- POSITION LAW (small shifts): tx* = the tile where the anchor
  x-displacement didx_x = ax - 8192*tx crosses 2^15 subpx:
  base ax=494848: crossing 56.4 -> tx*=56; +4: 60.4 -> 60;
  -1: 55.4 -> 55 (all match); the -8 case (ax=429312, crossing 48.4)
  does NOT match its observed 32 - it sits in a different regime
  (bigger didx binades in-triangle), consistent with the orientation
  flip: the governing quantity involves BOTH didx components (the
  base case fits didx_x + didx_y/4 = 342.6px across ty; -8 fits
  ~tx + 5ty/4 = 31.5 iso-lines).  The jump is a MID-PRODUCT BINADE
  EVENT: when didx crosses a power of two, the product normalization
  shift changes by one, moving the join's rounding position -> the
  ~3-lsb27 step (later-56).  This finally connects the join jump to
  the SAME didx-binade arithmetic as the wide-path X law (bl(disp)=19
  diagonal, tz classes) - one mechanism family, two observables.
NEXT: derive tx*(ax, ay, ty) exactly from the banked mid-product
chain (the shift changes at bl(didx) transitions are computable
input-only); test on all five wobble captures; then compose with the
X-law constraints and rescore everything.

## 2026-08-14 (later 59): exact jump iso-invariants measured

Boundary invariants (didx_x = ax - 8192*(tx+1) right-edge, didx_y =
8192*ty - ay corner, subpixels):
- base geometry (ax=494848): 4*didx_x + didx_y = 318080 EXACT at
  ty = 0/4/8/12 boundary tiles (56/57/58/59).
- shift -8 (ax=429312): 4*didx_x - 5*didx_y = -396416 EXACT at
  ty = 0/4 (tiles 32/27) - the dy coefficient flips +1 -> -5 with
  the displacement regime.
- C-difference derivation of the slope FALSIFIED cleanly: exact-
  product C27 differences wobble far MORE than hw (182/729); hw's
  slope is computed once with ONE conditional adjustment, gated by
  these linear displacement invariants.
Small-shift position law: tx* = floor(ax/8192) - 4 (right-edge 2^15
crossing) confirmed for shifts 0/-1/-2/+4.
DERIVATION TARGET: coefficients (4, +1) vs (4, -5) and the constants
318080 / -396416 must fall out of the mid-product normalization
(didx24 shifts and the join alignment) - the 4:1 weight with EQUAL
slopes cannot come from value magnitudes; it is a shift-alignment
property.  Next session: derive them from the banked chain's
alignment arithmetic; then the composed rescore; then the ports
(91 -> 80 -> 0) per the standing goal.

## 2026-08-14 (later 60): asymmetric map - block-float storage identified

Capture a2-wobble-asym-plan-v1 (x-leg 710.5, y-leg 1421: A = 2B; 356
draws; capture.raw sha256
1db462d7e9388228ab608e2a9553f4f6b58db34663bb2a8965c5616cab058f73):
1. ONLY THE SMALLER slope's lane wobbles (B; the doubled A=3ab87a81
   is constant everywhere).  With equal slopes it was also B (the
   second lane).  => the per-tile triple is stored BLOCK-FLOATING:
   shared exponent from the larger slope; the smaller slope's
   mantissa is alignment-shifted, and the jump is its re-quantization.
2. Boundary fit: first-X = 58 (ty0-4), 59 (ty5-11), 60 (ty12-16):
   5*didx_x(right edge) + didx_y = 264064 fits ty0/ty5 exactly
   (ty12 sits at the anchor column, edge effects).  x-coefficient
   4 -> 5 as the slope-binade difference went 0 -> 1: c1 = 4 +
   (binade(A) - binade(B)) candidate; c2 = 1.  The -8 case's (4,-5)
   remains the outlier to derive (regime flip).
3. My banked chain's internal events (mid exponent steps at tiles
   55/58, product-bit changes) do NOT coincide with the measured
   boundary (56/57): the jump lives in a storage/alignment stage
   AFTER the modeled chain.
The mechanism picture: coefficient RAM = block-float (shared exp,
mantissas aligned); the smaller slope's stored mantissa changes by
one step where an alignment-relevant quantity (linear in didx with
slope-binade-dependent coefficients) crosses a threshold.  This is
the last arithmetic to pin for step 1 of the goal.

## 2026-08-14 (later 61): coefficient law confirmed; traversal-counter model

Capture a2-wobble-ratios-plan-v1 (4 geometries, 1795 draws;
capture.raw sha256
8ed26b539de228bbf344b1098991b0b7e627c6c2a1c5261f76af11505f159e0e):
1. c1 = 4 + (binade(A) - binade(B)) CONFIRMED at three points:
   ratio 1:1 -> 4, 2:1 -> 5 (later-60), 4:1 -> 6 (case 0: boundary
   advance +1 tile per ~6 rows, first-X 59 x ty0-5 then 60).
2. Case 3 (A slightly smaller than B, span 1833px): BOTH lanes jump
   at the same near-vertical boundary tx~30 (~ the didx_x 2^18
   crossing at 28.4), with LARGE steps: A +10 lsb, B +16 lsb -
   bigger alignment shifts produce bigger re-quantization steps,
   as block-float storage predicts.
3. Case 1 (B = 2A: A smaller): no wobble in the mapped window
   (boundary elsewhere - coverage miss, not falsification).
   Case 2 (ay + 100px): wobble vanished from the window although the
   naive K-invariant predicts ~u*=55: K depends on the anchor in a
   way the current form misses - a wrinkle for the derivation.
4. MODEL CANDIDATE unifying the 4:1 quantum: the coefficient
   traversal walks tile-x within 4-row blocks then steps block-y,
   decrementing ONE shared displacement counter by one tile-quantum
   per traversal step; the jump = the counter's binade crossings
   (2^15 base case, 2^18 case 3), which change the block-float
   alignment of the smaller slope's stored mantissa.  c1 = 4 + dbin
   would follow if the counter's per-x-step decrement scales with
   the slope-binade difference.  NEXT: formalize counter arithmetic;
   fit all six wobble datasets exactly; then compose with the
   mid-product X law and rescore (goal steps 1-2), then port
   (step 3).

## 2026-08-14 (later 62): jump existence is phase-dependent (wide-map nulls)

Capture a2-wobble-wide-plan-v1 (cases 1 and 2 full-screen maps, 1317
draws; capture.raw sha256
e0a71ace92af7b451b31326d4dc7dc881bfa7880c9decbec0156a7d79e23f37d):
both cases are COMPLETELY FLAT across all covered tiles (0..63 x
0..21) - no jump anywhere on screen:
- case 2 (identical slopes/ax, ay shifted +100px): the C=0.5 binade
  line DOES cross the mapped window, and the K-invariant predicts an
  in-window boundary; both fail.  ay%8192 = 640 vs base 1664: the
  jump's magnitude (hence visibility) depends on the anchor's
  sub-tile phase through the y-part product low bits - the ~3-lsb27
  step of later-56 is not a constant but a phase function.
- case 1 (B = 2A): no jump on screen either.
CONSOLIDATED LAW STATE (the one remaining unknown, all measured
constraints): block-float triple storage (later-60), smaller-slope
lane carries the jump; boundary position tx* = floor(ax/8192) - 4 + b
for the base family (b = 4-row block index), coefficient law
c1 = 4 + slope-binade-delta (three-point, later-61); jump size 10-16
lsb in large-alignment regimes (later-61 case 3), ~3 lsb27 base,
ZERO at other anchor phases (this entry); plus the entire mid-product
X-law constraint set (K(tz) table, parity thresholds, bl(disp)=19
diagonal, killer cell, phase-0 census, borrow requirement).
The identification is at the stage where the remaining freedom is
the exact alignment/rounding datapath generating these; every new
capture now adds an exact constraint in minutes.  Goal steps 2-3
(compose+rescore; port 91->80->0) remain queued on this law.

## 2026-08-14 (later 63): ay-phase scan closes the round; law state summary

Capture a2-wobble-ayphase-plan-v1 (32 sub-tile ay phases x transect;
capture.raw sha256
832d4a124e7477101bc3b5f25ad06a7a811a0fef961ed42d3a4b6f13b0b03a56):
- The boundary POSITION is invariant under ay phase: tx* = 56
  whenever visible (pure-ax position law confirmed:
  tx* = floor(ax/8192) - 4 + block).
- VISIBILITY toggles with ay's sub-tile phase: 11/32 phases show the
  jump (~1/3, matching the later-56 g-scan statistic); k=12/16 show
  the whole transect on the upper word (global phase crossing).
- Case 2's full-screen null (later-62) is therefore a phase-
  visibility effect, not a position change: at ay%8192=640 every
  in-window tile's phase sits clear of the word boundary.
THE JOIN-JUMP LAW, assembled: position = pure function of ax (+block
index, + slope-binade-delta coefficient for the boundary slope);
magnitude = sub-ulp internal step whose word-visibility follows the
value phase; storage = block-float triple, smaller-slope lane.
Remaining to write down: the closed-form internal step (the ~3-lsb27
base / 10-16-lsb large-alignment values as a function of the
alignment shift), then goal steps 2 (compose + rescore 38,612 pts +
dense corpus) and 3 (port 91 -> 80 -> 0).

## 2026-08-14 (later 64): REGION-STORAGE MODEL - the unifying structure

Exact idx27 for the base wobble slope = 96719882 (0x5c3d40a), e27=-37.
Its re-roundings: s<=1 -> word 3a387a81; s=2 rna/rup -> 3a387a82;
s=3 rna -> 81; s=4 rne/rna -> 82; s>=5 -> 80/other.  The observed map
(82 uniformly from the boundary to the anchor edge, across register
binades 15..1) is IMPOSSIBLE for any per-tile s(bl(R)) law, but exact
for REGION STORAGE: the coefficient RAM is written ONCE PER REGION,
where regions are delimited by binade crossings of the traversal
register R = ax - 8192*(tx+1+floor(ty/4)); the region's stored slope
= idx27 rounded at the REGION's alignment (base case: s=2, rna,
giving the +1-word jump); all tiles of the region export that word.
CONSEQUENCES (the unification):
- position law = region boundary = R's binade crossing (pure ax) OK
- c1 = 4+dbin boundary slope = how the region grid tilts with slope
  binades OK; visibility = whether the region-rounding moves the
  word (phase statistic ~1/3) OK
- THE C LANE: per-tile C = region base value + in-region increments;
  the wide-path X anomalies, K(tz) classes, bl(disp)=19 diagonal,
  killer cell, and phase-0 census are REGION-BOUNDARY artifacts -
  which is why every single-(dm,didx) pointwise model plateaued.
NEXT WINDOW (goal step 1 completion): formalize region decomposition
(R binade partitions x 4-row bands), storage rounding rna-at-s per
region for slopes AND the C base/increments; fit the six wobble maps
exactly, then tt1/tt3/tt4 (38,612 pts) and the dense corpus captures
(step 2); then port (step 3: 91 -> 80 -> 0).

## 2026-08-14 (later 65): REGION LAW FIRST EXACT FITS

Law: R(tile) = ax - 8192*(tx + 1 - floor(ty/4)); tiles with R < 2^15
belong to the anchor region and export RNE24(rna_s(idx27)) with s=2
(s=4 equivalent on this data); all others export RNE24(idx27).
Scores: shift+4 map EXACT 665/665; base map 727/729 with both misses
at corner-sliver tiles (60,18)/(60,19) (partially covered right-
bottom corner; rows 16/17 of the same band fit).  asym2x 331/356
(needs the c1=4+dbin shear generalization); shift-8 119/684 (the
reversed regime needs its own threshold/shear - candidate 2^17).
The slope-lane storage law is now essentially identified for the
base family; remaining: dbin shear form, the reversed regime, the
C-lane region law, then compose and rescore (goal step 2).

## 2026-08-14 (later 66): region law does not transfer naively to children

Applying the later-65 law (smaller lane, R < 2^15, rna_2) to the
dense capture's slope words: baseline 29548/38793; with the law
28335/38793 (fixed 0, broke 1213).  The corpus children's regions
use different thresholds/alignments than the transfer-mesh family -
consistent with the asym (c1 = 4+dbin) and shift-8 (reversed regime)
maps: the law's parameters (threshold, shear, s) are functions of
the slope binades and anchor regime still to be generalized.
STATE FOR CONTINUATION (goal unchanged, three steps):
1. Region-storage structure PROVEN (shift+4 map exact; base 727/729
   corner slivers).  Generalize: fit (threshold, shear, s, lane
   rule) per wobble dataset as functions of (slope binades, anchor
   bits); the asym and shift-8 maps are the discriminators in hand;
   new maps cost ~1 min each on the M1 harness.
2. Then re-apply to the dense capture (slope lanes AND C tiles with
   region-base + in-region behavior), rescore tt1/tt3/tt4.
3. Then the ports with gate contracts 91 -> 80 -> 0.
All capture inventory: 15+ hardware captures today, shas in entries
later-53..66; harness /tmp/walle-agx-single-axis-multi-anchor.GRzoaQ.

## 2026-08-14 (later 67): GENERAL REGION LAW (odd-parity regime) EXACT

Coupled-quantum invariant fits (shear quantum = c1 rows):
  base:    (c1=4, c2=1, K=318080) 18/18 boundary rows EXACT
  asym2B:  (c1=6, c2=1, K=275584) 16/17
  ratio4B: (c1=7, c2=1, K=229760) 13/15
DECOMPOSITION (exact on all three): K = c1*dx0 - ay with
  dx0 = (ax mod 8192) + (4*B/A - 1)*8192   [ty0 boundary distance]
  c1  = 8 - 4*B/A
i.e. REGION(tile) <=> didx_x(right edge) < dx0 - (32768/c1)*floor(ty/4):
the region = tiles whose x-distance to the anchor is less than the
value-equivalent of one 4-row block (A*dx0 ~ B*128px), sheared by
32768/c1 per block.  Verified advance rates match asym (~0.67
tiles/block) and base (1 tile/block).
SHIFT-8 REVERSAL EXPLAINED (hypothesis): base ax=494848 (0x78D00),
shift-8 ax=429312 (0x68D00) - EXACTLY 2^16 apart; bit 16 of ax
(= 8-tile superblock parity) flips: the traversal is BOUSTROPHEDON
over 8-tile superblocks; even-parity anchors reverse the boundary
orientation (observed -5/4 slope).  Next: fit shift-8 with the
mirrored law; extend c1/dx0 to non-integer 4B/A (span1833); then the
C-lane, compose, rescore, port (goal steps 2-3).

## 2026-08-14 (later 68): real-child wobble boundaries (the law's true testset)

Dense-capture 1-ulp wobble lanes (the only clean ones among 96):
- (33,6,ctx1,B): 3a6b0058/59, 21 wobble tiles in rows 53-54
  (near-horizontal boundary).
- (41,2,ctx0,A): 3a3d2316/17, boundary EXACTLY tx* = 34 - ty
  (slope -1: didx_x + didx_y = const) across 8+ rows.  This child has
  B = 0 EXACTLY (single-slope plane) yet its A lane wobbles -
  contradicting later-55's "join-only" reading, and its (c1,c2) =
  (1,1) breaks the 8-4B/A interpolation (which gave (4,1)/(6,1)/(7,1)
  for ratios 1/2/4).  The region-geometry law is therefore richer
  than the 3-point toy fit; the s41 and s33 boundaries plus the six
  toy maps are the constraint set for the next fitting round.
- The parity/boustrophedon hypothesis remains open (shift-8's
  even-parity regime fits (3,4)-family at best 11/19 with phase).
Structure retained: region storage + block-float + anchor-relative
traversal is the right frame; the exact region geometry (c1, c2,
quanta, K as functions of A, B binades, anchor bits incl. bit 16)
is the one remaining derivation.

## 2026-08-14 (later 69): s41 is base-family; cross-channel block-float

CORRECTION to later-68: my boundary extractor caught the triangle's
LEFT EDGE (coverage boundary), not the wobble boundary.  The full s41
o2 A-lane map shows the true structure: the minority word (3a3d2316,
48 tiles) forms an ANCHOR-SIDE region at the right (first-dot tx per
ty: 55@0, ~57@5, ~58@10, 59@14, gone@18: +1 per ~4-5 rows), i.e. the
BASE-FAMILY law (anchor region, ~4-row shear, storage rounded DOWN
this time).  s41's ax = 486144 (bit16 odd - consistent with the
odd-parity family), ax mod 8192 = 2816, and the ty0 boundary
dx_r = 2816 + 3*8192 -> n = 3, same as the A=B toy case, despite
B = 0.  RESOLUTION HYPOTHESIS: the block-float record is shared
ACROSS ALL FOUR CHANNELS of the tile RAM entry (LDCF fetches
channelwise but storage shares one exponent): s41-ctx0's pairing
ratio is set by the other channels' slopes (ctx1 B ~ A0 -> ratio ~ 1
-> n = 3).  The toy captures used identical varyings on all four
channels, hiding this.  Test: toy capture with DIFFERENT per-channel
varyings to move the shared exponent and watch the region shift.
The 8-4B/A interpolation must be recast with the RECORD-max slope.

## 2026-08-14 (later 70): THE REGION CONDITION IN VALUE UNITS (scale-invariant)

Capture a2-crosschan-plan-v1 (channels scaled 1/4/0.25/1 vs all-1
control; capture.raw sha256
4fed781690987ad36c770e0e402ec762689243d94200a44714ed5c48277718ef):
ALL FOUR channels show the IDENTICAL boundary (56/57/58 at ty 0/4/8)
in both cases - cross-channel exponent coupling FALSIFIED, and the
boundary is invariant under slope SCALE.  This pins the condition:
*** REGION <=> A * didx_x < B * 128px ***
(x-value-distance to the anchor less than one 4-row block of
y-value; scale-invariant in the channel, consistent with all three
span maps: boundary tile-distance = 4*B/A exactly -> n+1 = 4, 2, 1
for spans 1421/710.5/355.25) plus the +1-per-c1-rows shear with
c1 = 8 - 4B/A as fitted.  OPEN WRINKLES: s41 (B = 0) still shows
n = 3 - its y-quantum source unresolved (record-max-B falsified by
this same capture); shift-8 even-parity regime; the stored-rounding
value per region (s, direction: base +1 word, s41 -1 word); the
C-lane analogue.  These four wrinkles are the whole remaining
unknown; every one is measurable with existing instruments.

## 2026-08-14 (later 71): storage-rounding wrinkle; window close-out

s41 ctx0 A-lane: chain idx27 = 99162298 (mod 4 = 2); hw region word
3a3d2316 (one BELOW base 3a3d2317) is NOT produced by rna at any s
(rna_2 gives ...18, one above); rtz at s=4 reproduces it.  Either the
region storage's rounding mode differs per case (base rna_2 up, s41
rtz_4 down) or my idx27 is a few lsb off (chain approximation) - the
partner-lane FFMA values (residual-value probe) can measure the true
internal slope directly per child and settle it.
CONTINUATION ORDER (all instruments ready, goal unchanged):
1. Measure true internal slopes for s41/s33 via the value probe;
   settle the storage rounding (mode, s) per case.
2. Resolve s41's B=0 y-quantum and the shift-8 even-parity regime
   (one wobble map each with targeted anchors).
3. C-lane analogue of the region law; then compose with the narrow
   law and rescore tt1/tt3/tt4 + dense (goal step 2).
4. Ports with gate contracts 91 -> 80 -> 0 (goal step 3).
Since later-53: 20 hardware captures, every one sha-banked; the
region-storage law's value-unit condition (later-70) is the
campaign's deepest structural result to date.

## 2026-08-14 (later 72): *** THE STORAGE RULE: 2-BIT VON NEUMANN JAM ***

Capture s41-storage-kscan-plan-v2 (anchor-value +-16 ulp scan x
in/out-region tiles; capture.raw sha256
eb61f273411a956dc19f999d5e24ae499354e98346d44326664ea2547487ebdb):
- The base (out-region) word is k-invariant (the two numerator parts
  nearly cancel the perturbation - an accidental sub-ulp phase scan).
- The REGION word alternates 3a3d2316 <-> 3a3d231A with period ~5.3k:
  two adjacent points of a 4-lsb grid whose members end in binary
  "10" =>
  *** region storage = truncate the slope mantissa at 2 bits and JAM
  the half bit: m_stored = (m_internal >> 2 << 2) | 2 ***
  (the classic hardware jam/von-Neumann rounding).
- ONE RULE explains every observed region word across ALL captures:
  base 3a387a81 -> jam -> 3a387a82 (+1 seen); s41 3a3d2317 -> jam ->
  3a3d2316 (-1 seen); span1833 landings 3a0f037e / 3a387a72 - every
  region word ever captured ends in bits "10" (82, 16, 1A, 7e, 72).
- The k-alternation maps the internal sub-ulp phase directly
  (~0.75 lsb/k), giving a per-child instrument for the exact internal
  slope low bits.
REMAINING: region membership wrinkles (later-70/71), C-lane analogue
(likely the same jam at the C storage - testable on the region C
words in existing captures), compose, rescore, port.

## 2026-08-14 (later 73): C-lane jam confirmed; membership = anchor proximity

1. C-LANE JAM: in-region C words of the base wobble capture are
   52/60 = 87% congruent 2 mod 4 (vs uniform 25% outside): the
   region storage jams C exactly like the slopes.  (Exceptions ~8
   tiles - likely corner-sliver/edge membership, to refine.)
2. MEMBERSHIP is anchor PROXIMITY: |R| < quantum (~32768 for A=B),
   not one-sided R < quantum: base map's beyond-anchor tiles
   (R = -4864, -13056) are in-region; tt3's probes (R = -368640,
   44 tiles right of the anchor) are correctly OUT - resolving the
   would-be contradiction with tt3's exact narrow-law words.
3. THE tt1 ANOMALY IDENTIFICATION: tt1's bl<=30 deviating cells
   (d_o = 51, 115 <-> rows ty = 20, 21) are exactly the rows adjacent
   to the anchor row (19.2) - the Y-DIRECTION region band.  The
   wide-path X phenomenology = the region system seen from the C
   lane (with a storage variant to pin: those cells' words end 0b11,
   not 0b10 - the y-region C storage granularity differs, or the jam
   sits at 26-bit pre-export for C in that regime).
The unified picture: anchor-proximity regions (x- and y-bands, one
block quantum wide, value-ratio-scaled, 4-row sheared) whose stored
triples are 2-bit-jammed; everything else is the exact chain.
NEXT: pin the y-band's storage granularity from tt1's deviation set;
formalize both bands; rescore tt1/tt3/tt4 (38,612) + dense; port.

## 2026-08-14 (later 74): layer separation; window carry-forward

Scoring tt1 with anchor-proximity y-band + jam variants: best
band<16384/jam@i27-2bit = 2302/2610 (+2 over baseline) - the
large-didx C deviations (the K(tz) system, bl(disp)=19 diagonal,
killer cell) are a SEPARATE LAYER from the anchor-proximity region
jam; the proximity band explains only the bl<=30 anomalies' location.
COMPOSED MODEL STATE:
  layer 1 (PROVEN): narrow chain RNE24(rna27(P)) away from regions;
  layer 2 (PROVEN): anchor-proximity regions (x-band dx < 128*B/A px
    scale-invariant, 4-row shear; y-band existence at anchor rows)
    whose stored slope AND C mantissas are 2-BIT JAMMED
    ((m>>2<<2)|2) - one rule, both signs, all observed region words;
  layer 3 (OPEN): the large-didx C compensation (K(tz) table, parity
    thresholds, diagonal, killer cell) - the solvers' constraint set
    stands; likely the same jam at a didx-binade-dependent granule.
NEXT: test layer-3 as jam-at-granule (didx-binade-scaled positions)
against tt4's 18001; then compose all three layers, rescore, port.

## 2026-08-14 (later 75): layer-3 plain jam insufficient; window state

Global wide-only jam on tt4: const-1/2-bit 14210/18001 (+334 over
baseline, below the cascade 14524 and T17K9 14850) - layer 3 is not
a uniform jam; its didx-binade/tz structure (solvers' constraint set,
later-48..52) stands as the remaining identification.  Layers 1-2
(narrow chain; anchor-proximity regions with 2-bit-jam storage) are
PROVEN and cover the slope lanes completely.  Continue: layer-3
against tt4/tt1 with jam positions tied to the didx-normalization
shift per Track B's fixed-frame facts, then compose and port.

## 2026-08-14 (later 76): layer-3 localized to at/above the 27-bit stage

Frame-positioned jams at T=15..18 (below the rna27 cut for wide
products) leave all three datasets EXACTLY at baseline - the layer-3
compensation cannot live below the 27-bit stage; it acts at or above
it (consistent with Track B's value-injection findings: K*2^(bl-30)
units, i.e. 27-bit-stage lsb multiples).  Combined layer-3 spec for
continuation: a 27-bit-stage modification, mean +9..13 lsb30-units in
the tz=6 regime with the K(tz) table across regimes, parity-split
thresholds (27v/19v), the bl(disp)=19 diagonal flip, the killer
cell's -1-granule at drop=0, and the phase-0 census.  Candidate
family for next window: the 27-bit stage's OWN storage jam at a
tz-dependent position (2-bit jam proven at region storage; the wide
path may jam at bl(didx)-shifted positions in the same RAM format).

## 2026-08-14 (later 77): epsilon tomography - the sum pipeline has its own wide bias

New instrument: tt4 geometry + exact narrow anchor value eps on V0
(anchor term eps, x-part -eps*dx/2048; probe scans eps finely and the
exported C word's flip positions read the internal sum).  Three
captures (plans committed; capture.raw sha256 first-20 banked below):
- c-epsilon-tomography-plan-v1 (0x3F800000+j signed scan): flips move
  ~G/29..G/35 per step; artifacts at |j|=16 binade crossing.
  sha 4b4f326a5f9aa6e22c...
- v2 (single binade (64+j)*2^k, column 60): monotone with narrow +-1
  word dither zones.  sha e83a9f8547d90f21d1...
- v3 (column 48, lever exactly eps/2, all eps terms 4v-multiples):
  STILL non-monotone locally =>
  *** the C sum is NOT a single exact accumulation: parts are
  quantized on a 27-bit grid (8v quanta, v = 2^(bl-30)) before the
  final 24-bit round; consecutive crossing spacings alternate
  (period-2 stutter = the eps x-part (odd multiple of 4v) rounding
  alternately on the 8v grid) ***  sha 6ba5fc8344c369fa46...
- flat-sum + RNE24 reproduces t=0 scans EXCEPT zones where hw rounds
  UP EARLY (e.g. bl31: up at dropped >= ~24v instead of 32v): the
  wide-path biased threshold applies to the SUM's export round, not
  only to products - layer 3 is (at least partly) IN THE QUANTIZER.
- Single-threshold-per-row fits fail hard for bl>=33 (up to 39/64
  misses): the export threshold is not constant per row; combined
  with the 27-bit part grid this needs a two-stage model
  (part-quantize at 27 bits, then biased final round).
- v4 (column 64) invalid: tile 64 is outside the 2048px target -
  probe rejects (draw 0); plan kept for the record, no capture.
Next: two-stage decode of the v3 scans (27-bit part rounding rule x
final threshold rule) - or pivot: the corpus gate first (91->80 port).

## 2026-08-14 (later 78): PRESENTATION CLASS COMPLETE AT MODEL LEVEL (91->80, all-triangle hw truth)

Capture a2-allts-plan-v1 (780 draws; every non-degenerate transfer
triangle of states 42/31/33/40/41/58/60 with all-1.0 varyings, ~6-12
interior tiles each; PLUS the three per-vertex BASIS configs for s42
tris 0/2/6; capture.raw sha256
ce0ca2c02b3acb7ab81c9d5905bf3848ec16d285301e04960db8d13998f0e9b8):
1. Hardware one-plane truth per (state, tri): s42 tri0 A=2e8ba58e
   B=2e68b4e4 with C=3f7fffff through rows 0..10 (high at (15,18));
   tri1 A=0 B=2f000000 low rows <=10, high row 18; tri2/tri3 A=0
   B=2e68b4e4 low EXACTLY at tile row 0; tris 4-7 denormal slopes
   A=B=1, C=1.0.  s41 tri0/tri1 have low bands too.  s58 tri2/tri3
   row 0 exports C=3f800001 (ABOVE 1.0 -> f16 secondary still 1.0,
   raster-class residual confirmed).  s31/33/40/60: no deficit tiles.
2. BASIS DECOMPOSITION (drawn value 1 at one vertex, 0 others): hw
   exports per-basis C words; the all-1.0 plane is NOT the f32 sum of
   the rounded basis words (tiles (35,0) vs (34,1) sum equal, differ
   in one-C) - the hw sums at internal >24-bit precision.  Model
   chain vs hw basis words: 58/108 exact; misses are 1-2 ulp and
   concentrate where the reciprocal path engages (transfer triangles
   have non-power-of-2 det 1421^2) - the remaining chain gap.
   Exact-sum-of-model-28bit-values -> RNE24 scores 623/672 one-C.
3. CORPUS RESCORE with the COMPLETE hw-measured deficit sets (111
   s42-tiles + 20 s41-tiles + full row-0 spans for s42 tri2/tri3):
   *** TOTAL residuals 91 -> 80, ZERO regressions *** - s41's and
   s42-tri0/tri1's deficit tiles contain no sensitive pixels, the
   9+2 presentation pixels are exactly the sensitive pixels of the
   s42 deficit region.  Task #4's mechanism + extent are both now
   hardware-complete; only the input-only chain reproduction (basis
   words at reciprocal-path precision) and the renderer port remain.
BUG FIXED in analysis/a2_solver_validate.py: apply_plane assigned
select[window] per tile (later tiles of another triangle in the SAME
window erased earlier selections - (3,56,0) wiped (2,56,0)'s fix);
now ORs.  Rule sketch in analysis/a2_rule_generate.py (RTZ-join of
non-anchor basis constants + RNE anchor add) reproduces tri 2
22/22 hw tiles but over-extends tri 0; superseded by the basis-sum
internal-precision model of (2).

## 2026-08-14 (later 79): the two remaining classes converge on ONE law

Knob sweep on the a2-allts basis words: ('slope',10,32,20) fits
72/108 (best; banked 'mid' knobs 58/108) but the one-plane score is
KNOB-INVARIANT at 623/672, and the 49 misses are EXACTLY the deficit
tiles (+ s58's above-1 pair): the model basis sum lands at 1.0 where
hw is one ulp low.  The true barycentric sum is identically 1.0, so
the deficit is a SYSTEMATIC LOW BIAS in the hw's internal per-basis
values - the reciprocal-path chain (transfer dets are 1421^2, not
2^k) truncating below the model's precision.  CONCLUSION: the 80
raster residuals (wide-path C law, task #3) and the presentation
generation rule (task #4 port) are the same missing object: the
exact product/reciprocal chain at internal precision.  Closing the
chain closes BOTH: 91 -> 80 -> 0.
NEXT-WINDOW PLAN (in order):
1. Wide-path law: two-stage decode of eps-tomography v3 (27-bit part
   grid + biased final round; the t=0 rows constrain the quantizer,
   the t-scans constrain the multiplier deviation D) - or the
   equivalent read from the a2 basis words (each hw basis word =
   chain(numerator, reciprocal, didx) with KNOWN inputs: 108 words
   of ground truth on the reciprocal path, 1-2 ulp resolution).
   The basis dataset is the cleanest reciprocal-path probe yet.
2. Then rescore tt1/tt3/tt4 + dense + 38612 held-out; then the two
   gated ports (presentation secondary stage in the renderer via the
   closed basis chain; general path flip) with zero-regression
   contracts 91 -> 80 -> 0.

## 2026-08-14 (later 80): basis-word micro-fits (nulls banked)

On the 108 hw basis words: knobs ('slope',10,32,20) = 72/108 (best);
final-RTZ24 68/108; global multiplicative biases (1-2^-25/-26 etc)
all <= 72.  Mismatch mantissa deltas (hw-model): -1 x18 (dominant
low-bias), +2/+4/+23 at the tiny-value walk cells (absolute-scale
walk deviations, same word 3b95e3a0 repeating across tiles),
-2/-4/-6 tails.  hw-vs-exact-barycentric ulp histogram centers at
-0.25 ulp (systematic low).  Not a global bias: per-cell walk/
reciprocal structure - proceed with the later-79 next-window plan
(two-stage eps-v3 decode; 108-word reciprocal-path fit).

## 2026-08-14 (later 81): eps-v3 t=0 decode progress + basis-chain eliminations

Instrument decode (t=0 rows, all-exact wide part):
- The X-part (negative eps lever) enters GRANULE-QUANTIZED with RNE:
  down-jumps at u == 8 (mod 16) exactly (ties wobble to even).  The
  A-part staircase is NOT plain RNE/RNA/ceil at granule scale; the
  net sequences have period 32 (bl31; exact repeat) / period 64 with
  tie alternation (bl36).  Exhaustive 2-stage pipelines (3 orders x
  {rne,rna,rtz,floor,jam} x {24,27,28} inner x {rne24,rna24,chain}
  final) cap at 46%; sequential-f32 caps at 36/64 on ty63: the sum
  has at least three quantization events with at least one biased.
- u=0 column (eps exact powers: A=8G, X=-4G): rows 34..63 export
  P+4G EXACTLY at t=0; ty=30 (d_o=1793 - the killer d_o) exports
  ONE GRANULE LOW at the same all-exact inputs (earlier dump):
  the killer mechanism is d_o-selective, input-exactness-independent.
- t=3072 (killer dm) u=0: all bl35 rows sit at exact half-granule
  (P mod G = G/2) and ALL floor (no round-up at the tie) - with the
  27v/19v thresholds a 32v tie should round up; so either D < 0
  shifts the position sub-half, or the tie rule differs at exact
  half.  Constant per-bl-class columns = P mod G phase, not D.
Basis-chain eliminations (108 hw words):
- Reciprocal word confirmed exact (sel+-1/2 score 46/24 vs 72 at 0).
- Parts sum at >=28-bit internal precision (sequential-f32 orders
  score 52..56/108 vs 72 for the 28-bit-norm model).
- Remaining misses are INSIDE the per-part product chain (task #3).

## 2026-08-14 (later 82): THE WALK HYPOTHESIS (reframe of layer 3)

Smoking gun from eps-v3: with ALL-EXACT inputs (u in {0,16,32,48}:
A, X exact granule multiples; t=0 wide part exact), exports deviate
by WHOLE GRANULES (+1/-1/+2), row- and u-dependent (rows 19..33
table banked in analysis; rows 34..63 exact at u=0).  Exact-input
whole-granule errors cannot come from any per-cell rounding rule -
they are accumulated carries: *** the per-tile C constants are
produced by a SEQUENTIAL TILE WALK from the anchor row with per-step
quantization, not by direct evaluation ***.  This unifies: tt3's
exactness (narrow partial sums never round -> walk == direct = the
proven narrow law), the wide-path "compensation" (accumulated step
roundings), K(tz) means, the killer d_o=1793 (walk carry boundary;
one granule low at exact inputs, ty=30 u=0), the phase-0 census
(step-phase statistic), and s58's banked "drift -2u/row with resync
jumps" (the walk observed directly).
First walk fits (analysis/wide_walk_law.py; seed = first probed row,
step = dm*pitch, quantize each step):
  rne28-walk: tt4 13637/18001, tt3 17457/18001, tt1 1125/1305
  rna27-walk: tt4 12447, rne24-walk: tt4 6464
- rne28 nearly matches the direct-law baseline (13876) with NO
  special cases, but tt3 must be exact (17457 != 18001): the
  accumulator must hold >=30 bits OR quantize on an absolute grid
  anchored at the walk seed's binade, not renormalized per step.
NEXT: sweep walk variants: seed row (geometric anchor ty=16, d_o=1),
accumulator width 28..32, absolute-grid vs renormalized quantization,
per-step vs per-export rounding split (eps decode says the export
rounds at 24 with the X-part granule-RNE; the accumulator itself is
finer), walk direction, and the 2-D version (x-walk then y-walk -
the a2 basis planes need the x-axis walk too).  Rescore tt1/tt3/tt4
+ dense + 38612 held-out; then ports 91 -> 80 -> 0.

## 2026-08-14 (later 83): walk model refinement (residual-register family)

Per-binade per-step quantization is self-contradictory (tt4 bl31 rows
deviate while tt3 bl30 cells are exact -> no fixed significand width
works).  The consistent family: the walk carries the EXPORTED 24-bit
word plus a FINITE-PRECISION RESIDUAL REGISTER:
    t      = c(row) + step_exact + res(row)
    c(row+1)   = round24(t)
    res(row+1) = Qres(t - c(row+1))       (few bits, lossy)
- s58's banked walk phenomenology is this model observed: drift
  -2u/row = per-step lossy Qres bias; +(ulp-2) resync jumps = res
  saturation/wrap; narrow tt3 exact because res stays 0 exactly.
- Fit knobs: res grid (ulp/2^r, r=2..6), Qres mode (floor/rtz ->
  negative drift), res width/saturation, seed row (geometric anchor
  ty=16 / first covered row), step = dm*pitch exact vs pre-rounded.
- Fit targets, in order: (a) s58 o4 walk drift/resync trace (c-walk
  tomography capture f785aa14 + dense capture rows), (b) tt4/tt3/tt1
  full tables, (c) eps-v3 exact-input granule table (rows 19..33),
  (d) the 108 a2 basis words (2-D: x-walk then y-walk).

## 2026-08-14 (later 84): residual-register walk BEATS the direct law

analysis/wide_walk_resreg.py sweep (t = c + dm*pitch + res;
c' = R(t); res' = Qres(t - c', grid = ulp24(c')/2^r)):
  *** r=5, Qres=RNE, R=chain(rna27->rne24):
      tt4 = 14011/18001  (direct-law baseline 13876)
      tt3 = 18001/18001  EXACT ***
First candidate ever to beat the wide baseline while keeping the
narrow table perfect - and it does so with the PROVEN narrow chain
as its per-step rounder (the narrow law is the walk's fixed point;
zero extra machinery).  r=6/rtz/floor variants cluster just below.
tt1 = 28/1305 is a FRAME BUG, not model failure: tt1's d_o crosses
zero (d_o = 64*ty - 1229 goes negative below the anchor) and the
loader's abs() breaks the walk order/seed; needs a signed walk from
the anchor outward (re-derive disp from tileY in a custom loader).
NEXT: fix tt1 signed walk; then sweep the residual family finer
(r, per-direction modes, seed at geometric anchor d_o=1, res clamp
width, step pre-round) toward 18001/18001/2610; then eps-v3
granule table and the 108 basis words (2-D walk); then dense +
held-out; then ports 91 -> 80 -> 0.

## 2026-08-14 (later 85): tt1 signed walk (frame fixed, mode differs)

Signed walk over tt1's zero-crossing d_o (disp>>7 signed, walk
ascending, symmetric rounding): best 1158/1305 at r=4 Qres=floor
(direct-law equivalent baseline ~1150).  The winning Qres mode
differs from tt4's (floor vs rne) and the gain is small - the tt1
walk frame is still not right (candidates: walk starts at the
geometric anchor and goes outward in both directions rather than
ascending through zero; the Mb=24 split rows suggest per-tile-column
walks; mixed families per d_o may collapse in the dm-keyed dict).
State of the walk law: tt3 18001/18001 exact, tt4 14011/18001
(beats direct 13876), tt1 1158/1305 (~baseline).  The family is
right (exact-input granule deviations + s58 drift are walk-only
phenomena); the per-step residual law needs the finer sweep of
later-84's NEXT list.

## 2026-08-15 (later 86): *** PATH DEPENDENCE PROVEN - f(dm,d_o) CANNOT EXIST ***

Cross-referencing tt4 against the tz-class captures (same dm, same
odd d_o, different anchor subpixel ay): the SAME exact product
exports DIFFERENT mantissa words:
  tz4 vs tt4: 925/4213 differ; tz5: 2670/8809; tz8: 1180/4213;
  tz9: 593/1915 - deltas (tt4 - tz) in {-2,-1,+1,+2}, +1 dominant
  (~2-3:1 over -1), growing with |tz-6| and with bl; full per-bl
  tables banked above in the analysis output.
CONSEQUENCE: the "wide-path C-product law" hunted as a pure function
of (dm, d_o) DOES NOT EXIST - the exported C word depends on the
anchor context (walk length / subpixel phase).  This is why every
input-only family plateaued at 78-90% (they were fitting the tz6
path mixture).  The law is f(dm, d_o, anchor-context), which is
still input-only (the anchor is a geometry input) and so still
house-legal.  The residual-register walk (later-84: tt4 14011/tt3
exact) is the right FRAME; its per-step law must now be fit against
the cross-anchor delta tables - a direct measurement of the path
term with every input known.
Also: eps-v3 granule deviations at t=0 occur even where the wide
part's walk would be exact (dm = 2^23 power-of-two) - those
artifacts live in the multi-part SUM pipe, so the eps instrument
measures walk + sum jointly; the cross-anchor tables are the cleaner
walk probe (single-part cells, no eps).

## 2026-08-15 (later 87): the step-size ladders - crisp walk-law constraints

Cross-anchor ladders (same (dm,d_o) at S = 16..1024, L = (d_o-1)/S
rounding events): deviations vs exact rne24(P) are DETERMINISTIC in
(dm mod 4, S, d_o) - e.g. d_o=3073: S128/L24 exact for ALL t;
S256/L12 = -1 for ALL t; S512/L6 and S1024/L3 = 0 for t=1 mod 4,
-1 for t=3 mod 4.  d_o=5121: -1 nearly everywhere, S512 exact only
at t=6 mod 8.  NON-MONOTONE in L (L=24 exact while L=12 low) =>
plain per-step rounding modes cannot fit (checked by hand: rna27/
rne27/rtz27 all predict wrong signs); the finite residual register
holding half-grid residues until overflow is the only family that
produces such resonances.  NEXT: build the full deviation tables
D(dm mod 8, S, d_o) for every ladder cell and enumerate the
residual-register variants (R in {rna27,rne27}, res in ulp27 units,
width 2-3 bits, saturation/wrap) against the tables - hundreds of
crisp constraints; the right member should snap to 100%.

## 2026-08-15 (later 88): crossing-aware walk best; two-product subtraction inferior

- Binade-crossing variants on the residual walk: forcing the residual
  negative at each crossing ("minus") is the new best family member:
  total 91540/108006 across the six anchor classes with tt3 EXACT
  (tz-classes up to 15155; tt4 trades down to 13541).  Crossing
  events are load-bearing (predicted by the ladder algebra: S=256
  steps are always grid-aligned, so only crossings can lose the
  observed ulp).
- Tested the no-walk alternative for the anchor dependence: C =
  rW(dm*tile*8192) - rW(dm*ay) (two wide products, task-#2 shape),
  W=28..33 x rna/rne/rtz: best 89742, breaks tt3 - INFERIOR to the
  walk family; the anchor dependence is not a simple pre-subtraction
  rounding.
Running totals (wide classes, tt3-exact members): plain res-walk
90955 -> crossing-minus 91540 (+585).  The remaining ~15% misses
concentrate in tt4 lo-block and the crossing neighborhoods; next:
per-ladder-cell autopsy of crossing steps (state trace vs the 5-S
ladder for single cells), then eps-v3 rows 19..33 as crossing
tests (their 4-row-block granule table maps crossing rows), then
joint refit; then dense + held-out; then ports.

## 2026-08-15 (later 89): dC autopsy - crossings spike, exports add noise

Consecutive-row exported-word differences dC(ty) = C(ty+1)-C(ty):
- For clean dm (0x800001): dC = exact step everywhere EXCEPT
  excursions at specific rows; killer dm 0x800C00 shows NEGATIVE
  spikes exactly at its crossing rows (ty=30 = d_o 1793, ty=48).
- In general dC mixes the two adjacent export roundings (+-ulp24
  noise, odd multiples of ulp27), so the step law cannot be read
  from single differences; but crossing rows spike at 2^34-scale
  (e.g. ty=48: -1x2^34 x34 dm's, +1x2^34 x25) - crossing events
  remain the dominant special structure.
Session state: path-dependence proven (later-86), walk frame with
residual register best at 91540/108006 (later-88), crossing rows
localized as the law's remaining unknown.  Next window: model the
crossing-row transition exactly (state trace vs the excursion table
per dm class at ty=48/34-38), then joint refit -> dense -> ports.

## 2026-08-15 (later 90): THE DM-FAMILY SAWTOOTH (atomic excursion law)

Value-space rescan of tt4 (exact binade-aligned comparison):
1. Atomic threshold at the first wide row (ty=17, bl31): hw rounds
   up at dropped >= 19v = 32v - 13v for odd parity - the 13/64
   constant IS the atomic threshold bias, visible in a single
   rounding event with no history.
2. Granule-excursion families: dm's with low-8 bits zero and low-13
   part L = k*256: excursion windows (intersected across rows, in v):
   k=6,7: [-16,0]; k=8..11 descending toward -32; k=12 (0xC00,
   killer): PINNED -32v = -G/2; k=13..15: row-varying; k=16
   (0x1000): [+32,+96]; k=17..23 positive row-varying; k=24
   (0x1800): PINNED +32v = +G/2; k=26: +32; k=29: +16; k=28,30,31:
   [0,+32].  A SAWTOOTH around L = 4096 (dm's 2^13 half-point):
   negative below, positive at/above, decaying by k=31 - the
   signature of dm being segmented/rounded at bit 13 in one of the
   multiplier's paths, with the error scaled to ~2^-25 relative
   (32v = G/2).  Row-varying windows for k=13..23 mean delta also
   depends on ty/r - next: per-row delta(L, ty) tables to close the
   sawtooth's exact formula, then compose: value' = P + delta(L)
   with threshold theta = 32v - 13v and the crossing/walk terms;
   rescore all classes.

## 2026-08-15 (later 91): family excursions are row-dependent (windows banked)

Per-row windows for family k=20 (L=0x1400): [0,+64] for bl35 rows
with +64..+128 spikes at ty=45/47; [+16,+80] uniformly for bl36
rows.  The excursion delta(L, ty) shifts by +16v at the bl35->36
crossing and spikes a full granule at isolated rows - consistent
with the sawtooth being applied at a fixed absolute bit position
(so its v-scaled window shifts with bl) plus crossing events.
NEXT-WINDOW (start here): build the complete delta(L, ty) table for
all 32 families x 47 rows (window intersection per (L, ty) is
mostly a single 64v-window: the value is pinned mod G; stitch
adjacent-row continuity to pin absolutes), fit the closed sawtooth
formula, compose with theta = 32v-13v threshold + walk residue,
rescore all six classes -> dense -> held-out -> ports 91 -> 80 -> 0.

## 2026-08-15 (later 92): SAWTOOTH + THETA COMPOSITION AT 96.1% (tt4)

The closed sawtooth: delta = -(t mod 8192)*2^(bl-36), WRAPPED: rep
chosen with cut at tm >= 3712 (+8192) - fits the entire family table
(bl36 row: delta = -4k exactly, k=0..14, wrap +68 at k=15).
Composed with parity thresholds (theta_even, theta_odd = theta-8):
  global theta=(25,17):        16607/18001
  per-row theta (split locked): *** 17291/18001 (96.1%) ***
Theta(ty) profile (the walk's 1-D shadow): base (25,17) almost
everywhere; crossing rows DROP (ty=32: (17,9); ty=48: (9,1));
late rows CLIMB in steps (ty=55+: (43,35) -> (45,37), ~+2v/2rows);
occasional (21,13)/(23,15) dips.  Per-row fit quality 92-100%
(ty=60: 383/383 PERFECT).
Model status: value' = P - wrapped_sawtooth(t)*2^(bl-36);
export = floor(value'/G) + [dropped >= theta(path)].  Remaining 710
misses = sub-row theta structure.  NEXT: theta(ty) closed form
(crossing resets + steps), cross-class validation (tz sets, tt3
exactness, tt1), then dense + held-out, then ports.

## 2026-08-15 (later 93): *** UNIFIED SAWTOOTH LAW - INPUT-ONLY, CROSS-CLASS ***

delta = -wrap((dm * p) mod 2^19) * 2^(bl-42),  wrap cut at 29/64
where p = the anchor's fractional subpixel phase (8192 - ay mod 8192:
tt4 64, tz4 16, tz5 32, tz8 256, tz9 512).  Equivalently: the
hardware computes the anchor-offset product dm*p only to 2^19
granularity (biased wrap at 29/64 - the same 32-13 asymmetry as the
export threshold), and the dropped low bits are the sawtooth.
Global-theta scores (single (theta, theta-8) parity pair per class):
  tt4 (p=64):  16607/18001  theta 25   [identical to the t-mod-8192
               form - confirms unification: 64*(t mod 8192)]
  tz4 (p=16):  16924/18001  theta 37   [was 14074 before unification]
  tz5 (p=32):  16720/18001  theta 41
(tz8/tz9 sweep pending - timed out; phases 256/512.)
ALL INPUTS: dm (probe mantissa), ay (anchor geometry), bl (product
width) - fully house-legal.  With per-row theta (the walk shadow,
+2v/row drift, crossing resets) tt4 reached 17291 (96.1%); the same
composition should lift every class similarly.
NEXT: tz8/tz9 + tt3 (exactness check: p=0 - sawtooth VANISHES for
phase-0 anchors, explaining tt3's perfection for free!!) + tt1
(p=1664?); close theta(path); rescore dense + 38612 held-out; ports.

## 2026-08-15 (later 94): unified law validated on all five wide classes

tz8 (p=256): 15729/16852 wide cells (93.3%), theta 33.
tz9 (p=512): 13752/15320 (89.8%), theta 33.
All five anchor classes now fit one input-only formula with a single
global theta per class (89-94%); per-row theta adds ~+4 points
(tt4: 96.1%).  tt3 (p=0) is exact BY THE LAW (sawtooth vanishes).
Remaining: theta(path) closed form + the last few percent sub-row
structure; then tt1 (p=1664, non-power-of-2 phase - key test),
dense, held-out, ports.

## 2026-08-15 (later 95): theta(ty) tables for all five classes (banked)

Per-row theta with the unified sawtooth (excursion-outliers separate):
tt4 17291/17987, tz4 17102/17891, tz5 17106/17867, tz8 15956/16755,
tz9 14267/15195 - ~95.5% average.  Structure:
- UNIVERSAL crossing values: theta(ty=48) = 9 in ALL five classes;
  theta(ty=32) = 25 (17 in tt4); crossings drop theta by 8/16/17.
- Class bases: tt4 25, tz4 36 (alternating 37/33 by ty parity),
  tz5 42 (dropping to 33 late), tz8/tz9 ~33 -> 29 with parity
  alternation.  tt4 late rows (55+) climb to 41-45.
- A per-row theta shift is indistinguishable from a per-row value
  shift delta_row = (base - theta_row)*v: the tables ARE the
  d_o-side second-order structure (crossing pulses +8v/+16v,
  late-row drifts -18v, parity alternation +-2v).
Full theta tables banked in this entry's analysis output (session
transcript); regenerate with the later-95 script pattern.

## 2026-08-15 (later 96): *** LAW VALIDATED ON REAL CORPUS CHILDREN - FOUR PERFECT ***

Unified sawtooth + single theta per child, applied to the dense
corpus single-axis children (C = slope_mant * disp, phase from the
child's own anchor subpixels):
  s60 o4 ctx1: 1166/1166 PERFECT (theta 25, p=6528 non-pow2)
  s58 o5 ctx0:  990/990  PERFECT (theta 21, p=0)
  s33 o6 ctx1:  434/434  PERFECT (theta 25)
  s33 o103 ctx1: 436/436 PERFECT (theta 25)
  s58 o5 ctx1: 990/1034 (96%), s58 o4: 1039/1166, s58 o106: 981/1166
The 0/N children (s31/34/35/39/40/41/42/44/45/47...) all miss the
ANCHOR-VALUE term (their anchor vertex carries a nonzero value word;
my quick test omitted it) and/or negative-side disp - integration
details already solved in score_c_chain_dense's parts model, NOT law
failures.  This is the first model ever to predict complete real-
geometry children exactly.
NEXT (mechanical): full composition = anchor value + per-axis parts
via the banked chains + sawtooth per axis + theta; rescore all 58
dense groups; theta closed form from the growing theta table
(25/21/17/33/49 observed); then the 38612 held-out corpus points;
then the ports 91 -> 80 -> 0.

## 2026-08-15 (later 97): anchor term integrated; o2-family convention remains

With the anchor-value term + signed frames: TOTAL 11378/18862 on
single-axis dense children.  NEWLY PERFECT: s31 o6 ctx0, s31 o103
ctx0, s33 o6 ctx0, plus near-perfect s34 o6 (460/472), s39 o6
(550/552), s42/s44 o6 (592/600 each).  The remaining 0/N cluster is
EXACTLY the ordinal-2/104 family (s34/35/40/41/42/45/47 o2, o104s)
- one shared convention issue (anchor vertex selection / slope
direction for that child family), not a law failure.  Theta values
observed so far: {9,17,21,25,33,37,49} - quantized on the 4v grid,
parity split 8.
NEXT: fix the o2-family convention (check winding/anchor of ordinal-2
children in _childgeo/CHILDSDF), rescore; two-axis children via the
full parts model; theta closed form; held-out corpus; ports.

## 2026-08-15 (later 98): frame fix (slope per 256 subpx); cancellation children need staged rounding

Slope words are per-256-subpixel units; with nonzero anchor values
the av/product frames were misaligned by 2^8 (pure-product children
were scale-invariant and unaffected - why they were perfect).  Fixed:
TOTAL 13125/18862; s34 o2: 0 -> 392/549, s42 o2: 0 -> 328/729,
o104s partial.  Still-zero children (s35/40/41/45/47 o2, s39 o6
ctx1) are the NEAR-CANCELLATION cases (av ~ -product): the hw rounds
the product term to its own export precision BEFORE the subtraction
(task #2's two-product cancellation law); the model must apply
sawtooth+theta at the PRODUCT stage inside the banked parts chain
(score_c_chain_dense c_word with the product stage swapped), not on
the final difference.  That is the remaining integration.

## 2026-08-15 (later 99): cancellation children diagnosed - frame right, staging wrong

s41 o2 ctx1 model-vs-hw VALUES match (ratio 1.0000 across tiles):
geometry/frames confirmed.  The hw C words carry difference bits
FINER than the product's 24-bit granule (0.00469 at 24-bit mantissa
= 2^-31.7 absolute vs product granule 2^-23) => the av+product
subtraction happens at >=28-bit precision BEFORE export (the banked
28-bit numerator stage); stage-1 product rounding at 24 bits is
wrong for cancellation children (staged total 12845 < flat 13125).
The flat model with sawtooth got s42 o2 to 328/729 with one global
theta; cancellation children have long walks (anchors at negative
tiles, 40+ rows) -> they need the per-row theta layer / the theta
closed form - the single remaining law piece.
NEXT: per-row theta for cancellation children (expect +15-30pts),
then theta(path) closed form from all tables, then the two-axis
children, held-out corpus, ports.

## 2026-08-15 (later 100): per-row theta on corpus children; G-pulse layer isolated

Per-row theta over all single-axis dense children: 13814/18862 (73%).
Since A=0 children have ONE C word per row, a row that no theta fits
means V itself is off by >= a granule: the remaining ~27% of rows
carry the +-G crossing/carry pulses (the eps-v3 exact-input granule
artifacts) - child-specific crossing rows where the accumulated walk
injects whole-granule offsets.  LAYER MAP now complete:
  L1 closed: sawtooth = -wrap29/64((slope*p) mod 2^19)*2^(bl-42)
  L2 open-but-tabled: theta(path) (per-row tables, universal crossing
     values, parity split 8)
  L3 open: +-G carry pulses at child-specific crossing rows
NEXT WINDOW: extract the G-pulse rows per child (rows where no theta
fits), correlate with the child's binade-crossing rows (bl(P) steps),
fit the pulse rule from the eps granule tables (rows 19..33 banked),
then compose L1+L2+L3 -> rescore -> theta closed form -> held-out ->
ports 91 -> 80 -> 0.

## 2026-08-15 (later 101): cancellation deficit = sub-word anchor precision

s41 o2's constant absolute deficit is ~+0.49 av-lsb (between the
word value and +2 lsb; the 2-bit jam overshoots to +386G, none
-125G at the last row).  The anchor VALUE the hw interpolates has
sub-f32-word precision: the SDF vertex words in _childgeo/CHILDSDF
are rounded exports of walle's internal SDF values - the hw (and
the parity CPU model) carry the unrounded value.  Fix: source the
internal vertex values from the parity reveal-mask model (they are
walle's own SDF math), not the captured word dump.  This also
matches the eps-instrument's A-part "+delta bias" observation.
NEXT-WINDOW START: wire internal vertex values into the child
scorer; rescore cancellation children (expect them to join the
perfect list); then L3 G-pulses for the remaining rows; theta
closed form; held-out; ports 91 -> 80 -> 0.

## 2026-08-15 (later 102): internal slope integrated - 12 perfect children

Using the banked build28 internal 28-bit slope (n28 x sel, frame
2^(g+se-8) per subpixel) instead of the exported 24-bit B word:
TOTAL 13906/18862, *** 12 perfect children *** (was 8).  Newly
(near-)fixed: s35 o104 0->532/569, s47 o2 0->483/789, s40 o104
526/651, s41 o2 0->101, s42 o2 0->148.  The measured internal-slope
deficit (-1.08 ulp24-relative vs the exported word on s41 o2) is
exactly what build28 produces - CONFIRMING the internal-slope
hypothesis and the banked chain for most children.
Remaining failures (s35/40/45 o2 at 0; s41/s42 o2 partial; s39/s44
o6 partial) trace to the slope chain's low bits being +-1-2 ulp28
off for a SUBSET of children - the same reciprocal-path gap as the
108-basis-words (72/108).  ONE remaining imperfect law (the
selector/reciprocal chain low bits) now gates both fronts.
NEXT: nail the reciprocal chain's last bits (probe: children where
cancellation rows pin the internal slope to sub-ulp28 - s41 o2's
rows ARE such an instrument: solve slope_int from the hw C words
exactly, compare against build28 variants); then rescore; theta
closed form; held-out; ports.

## 2026-08-15 (later 103): dense-corpus integration - wide/narrow gating needed

Full 33,736-tile dense scoring:
  banked parts chain + plain RNE24:            23028 (68.3%) baseline
  parts chain + per-axis sawtooth added:       18550 (worse: parts
    already quantized - double-counting)
  EXACT parts + sawtooth + theta final:        19400 at theta 33
    (worse than baseline: narrow tiles NEED the banked narrow chain,
    not a flat theta export)
CONCLUSION: the composition must GATE per part: bl(product) <= 30 ->
banked narrow chain stage; bl >= 31 -> exact part + sawtooth; final
export theta-threshold only when a wide part dominates, else the
narrow RNE24.  (This mirrors how the tt classes behaved: all-wide
rows fit theta; tt3's all-narrow rows fit the chain.)
NEXT-WINDOW START: implement the gated composite in the dense scorer;
expect > 23028 immediately and > 30k with per-child theta; then the
L3 pulses; then held-out; then ports 91 -> 80 -> 0.

## 2026-08-15 (later 104): slope24 gating; theta(path) confirmed as the last blocker

Slope24-frame gate (wide iff bl(slope24*odd(disp)) >= 31): 19668/33736
global-theta - still under the banked baseline 23028 because 22269
exports go wide and a GLOBAL theta throws away what the banked
chain's intermediate quantizations partially emulate.  The single-
axis evidence (12 perfect children, 73.7% with per-ROW theta) says
the wide law + per-path theta wins where theta is allowed to follow
the path.  CONCLUSION: every integration route now funnels into the
single remaining unknown - the theta(path) closed form (the L2
tables: base per class, universal crossing drops 9/17/25, parity
split 8, +-2v alternations, late-row climbs).
NEXT-WINDOW START (concrete): fit theta(path) as
  theta = base + f(rows-since-crossing, d_o mod 2^k, parity(ty))
against the five banked class tables + the per-child tables from the
12 perfect children (their fitted thetas are known-good anchors);
then the gated dense composite with theta(path); then held-out;
then ports 91 -> 80 -> 0.

## 2026-08-15 (later 105): theta-exact windows mostly EMPTY - cell-level residual remains

Exact per-row theta windows (even-parity-folded, all cells must fit):
most rows are CONTRADICTORY by 5-10v (10-20 cells per ~380-cell row
deviate from any single threshold); only the crossing rows (ty=48)
give clean windows [9,64].  The earlier per-row scores were majority
fits.  CONCLUSION: after the closed sawtooth, the residual ~4-8% is
CELL-structured (second-order dm term), not a pure per-row theta:
candidates - the wrap cut is not a constant 29/64 (per-bl boundaries
showed [-7/16,+9/16] with fine structure), the parity fold is not
exactly 8 everywhere, and +-G pulses pollute specific dm families.
The law stack is: L1 sawtooth (closed) + L2' second-order cell term
(open: ~4-8% of cells) + crossing pulses.  NEXT: characterize the
residual cells directly (which dm's deviate from the row threshold,
vs dm bits/r-position) on tt4 rows with the cleanest windows - the
same family-census method that cracked L1 (later-90).

## 2026-08-15 (later 106): residual census in clean rows - second harmonic hypothesis

Rows 49-54 of tt4 under L1+theta(25,17): 2266/2298 (98.6%); the 32
violations are structured: tiny-t cells (t=3..45, r=18/26) round
DOWN 1v above threshold; t in [196..251] (r=16/24) round UP 1v
below; t = 0x0E00-family at r=0 (+1).  Cut variants (27/64, 28/64)
score WORSE (2246) - not a cut shift; integer-vs-exact threshold
comparison is identical (algebra).  The +-1v tilt within each 256-t
block suggests a SECOND, weaker sawtooth one scale down (the same
biased rounder at the next pipeline stage - self-similar cascade):
delta2 ~ -wrap((dm*p) mod 2^13)*2^(bl-48)-scale.  98.6% on clean
rows means the L1 law + simple theta is already at the corpus-
useful level for most rows; the harmonic closes the last 1.4%.
NEXT: fit the second harmonic's (modulus, cut, amplitude) against
the 32-violation census + all-class residuals; then the dense gated
composite + per-path theta; held-out; ports.

## 2026-08-15 (later 107): eliminations on the 32-cell residual

Tried on rows 49-54 (2266/2298 baseline with L1+theta(25,17)):
- wrap cut 27/64 or 28/64: WORSE (2246)
- second harmonic -wrap((dm*p) mod 2^M2)*2^(bl-amp), M2 in {12,13,14},
  cuts {16..40}/64, amp {44..52}: no gain (2266 unchanged at best)
- rna27/rne27 stage between sawtooth and threshold: WORSE (2254/2226)
The 32 residual cells (t=3..45 down-1v-late, t=196..251 up-1v-early,
0x0E00-family at r=0) need exact per-cell inversion next: compute
each cell's required (value, threshold) preimage and difference
against the L1 model - the preimage method at cell scale (as used
for the wide windows in later-85/87).
STATE SUMMARY at window end: L1 unified sawtooth closed and corpus-
validated (12 perfect children); clean-row exactness 98.6%; class
tables ~96%; the residual is a single small structured term plus
theta(path).  Gate remains 91; the port milestones (91->80->0) queue
behind the exact closure.

## 2026-08-15 (later 108): tie-rule elimination

The 32 clean-row violators are all exact-v-grid ties within +-1v of
theta, split by dm parity (even up / odd down) - but the UNCONDITIONAL
dm-parity tie shift scores WORSE (2230/2298 vs 2266; all-rows 16491
vs 16463 baseline is +28 net but clean rows regress): the parity
pattern does not generalize; the discriminator among near-theta ties
is something else (candidates: parity of a deeper pipeline word, the
27-stage lattice position, or the t mod 4 storage-jam bits).  Next
window: build the tie-cell census across ALL rows/classes (every
exact-grid cell within +-1v of theta, with full dm/d_o/r context)
and solve the discriminator exactly; it is the last cell-level term.

## 2026-08-15 (later 109): single-row boundaries are SHARP; wide_law.py module

- analysis/wide_law.py: the law consolidated into one self-tested
  module (frame 2^48/P-unit); reproduces tt4 16607/18001 exactly.
- Correctly-scaled dm-side second harmonic (max 1v): only +31
  (16638) - not the fine term.
- SINGLE-ROW boundary profiles are SHARP to ~+-0.5v (ty=20: clean
  transition at theta ~ 24, one mixed sliver bin): the pooled +-0.8v
  smear was PER-ROW THETA VARIATION, not a cell-level term.  Theta
  needs fractional-v resolution per row; the cell-level residual is
  only the tie slivers.
- Theta(path) hypothesis to test next: theta drift = carried export
  residual (theta(ty+1) - base ~ -res(ty)/v with res = V - W*G of
  the previous row's export) - i.e. the walk's residual register
  RE-ENTERS as the next row's threshold shift.  Test against the
  banked theta tables; if it holds, theta(path) is CLOSED with zero
  new constants and the composite becomes fully input-only.

## 2026-08-15 (later 110): residual-feedback theta eliminated; instrument design banked

Carried-residual threshold feedback (theta_eff = base*v - lambda*res
with res = previous row's signed export residual): all lambda > 0
score WORSE (16060 at 1/4 vs 16607 at 0) - theta drift is not raw
carried residual.  Row-constant second-sawtooth candidates in d_o
(q in {p,64}, K in {13,19}) do not reproduce the theta-table pattern
(the ty=55+ +18v jump in particular).
INSTRUMENT DESIGN for the next capture (closes theta empirically):
tt4 geometry with the anchor's ay scanned over ~64 sub-tile phases
(ay = 131072 - q for q in {8,16,24,...,512}) x rows 17..63 x ~16 dm
values: measures theta(p, d_o) on a dense grid directly; factoring
that surface gives the closed form (or a small exact table keyed by
p - still input-only since p is a geometry input with 8192 possible
values, of which the corpus uses a handful).
NOTE the pragmatic port option: theta(p, d_o) as a computed table
from the INSTRUMENT captures (hardware-derived, input-keyed) is
house-legal if banked as hardware-validated law data - the corpus
children's p values are all in {0, 768, 1152, 1664, 1920, 3072,
3328, 3968, 4224, 6144, 6400, 6528, 7296, 7808} (from later-96/97
runs) - a 14-phase table.

## 2026-08-15 (later 111): theta-phase instrument captured (14 phases x 24 rows x 24 dm)

Capture c-thetaphase-plan-v1 (8064 draws; tt4 geometry with ay =
131072 - q for q in {8..512}; capture.raw sha256
c09ad6eae6ea3a8bdcece1a6d73607bbad9b5b534c6235555c975173f1f961c6).
First read: per-(q,row) theta windows under the p=64-calibrated
sawtooth are mostly contradictory - at new phases the L1 sawtooth
itself must be re-verified per phase BEFORE theta can be read (the
wrap/cut may be phase-dependent, or the 24-dm sample mixes sawtooth
errors into the windows).  NEXT-WINDOW START: per-phase L1 check
(the 24 dm ladder per (q,row) is enough to fit wrap/cut per q),
then the theta(p,d_o) surface, then the closed form or the
14-phase port table; then gated composite -> held-out -> ports.

## 2026-08-15 (later 112): theta surface first read - q mod 128 structure

Theta(q, ty) surface from the phase instrument (L1-clean cells,
24 dm/cell - noisy, many contradictory windows):
- LOW-theta phases are exactly q = 64 mod 128 (q=64/192/320/448:
  theta ~ 18-28) vs ~33-45 elsewhere: the anchor phase's BIT 6
  (quarter-pixel bit) controls the theta base.
- tt4's low base 25 (q=64) fits the pattern; corpus children mostly
  have q = 0 mod 128 (quarter/half-pixel anchors) -> the high-theta
  regime.
- Surface too noisy at 24 dm/cell for exact windows; next capture:
  128+ dm per (q,row) on 6 phases x 12 rows for clean windows.
NEXT: denser theta capture; then closed form/table; gated composite;
held-out; ports 91 -> 80 -> 0.

## 2026-08-15 (later 113): dense theta capture - large-q L1 gap exposed; port math clarified

Capture c-thetaphase2-plan-v1 (9216 draws; 6 phases incl corpus
phases 1664/6528; 128 odd dm x 12 rows; capture.raw sha256
ed9c156e85ee07eae93506e63650588d06eff60a3e4c8aceb4ce6d7d23c6e21e):
- q=64 column: theta windows CONSISTENT and pinned (23.3-23.6 at
  rows 33-45; 26 at 21-29/49-53; 42-46 at 57-63) - L1+theta exact
  at the calibration phase with 128-dm ladders.
- q=1664/6528 (corpus phases): 30-40v window contradictions - L1's
  dm-structure at LARGE q is wrong (the small-q instrument never
  tested it).  KEY INSIGHT: corpus children carry ONE slope mantissa
  each, so any dm-dependent L1 error is a per-child constant folded
  into theta - which is exactly why 12 children scored perfect with
  fitted theta while the multi-dm truth tables expose the gap.
- Port math: the gate needs correctness only at the corpus's actual
  (slope, phase) pairs; the general input-only law needs the large-q
  L1 correction, for which thp2's 128-dm ladders at q=1664/6528 are
  the direct census data.
NEXT-WINDOW START: family-census the q=6528 ladders (the later-90
method) -> large-q L1 correction -> re-derive theta(q,ty) surface ->
closed form -> gated composite -> held-out -> ports 91 -> 80 -> 0.

## 2026-08-15 (later 114): odd-form sawtooth eliminated; large-q gap quantified

Original folding (dm*q mod 2^19) vs odd-part form (dm*q_odd mod 2^19,
scale shifted): odd-form is MUCH worse at large q (q=6528: 13.2% vs
78.1% in-window; q=1664: 51.4% vs 75.9%) - decisively eliminated;
the original folding stands.  In-window rates (excursion-tolerant,
+-32v): small q 82-93%, corpus phases 76-78% - the large-q gap is
~22% of cells falling outside half-a-granule (L3 pulses + a real
large-q L1 second-order term).  CAVEAT for the census method: the
per-cell export mid is only +-32v resolution - the apparent t-mod-3
"residual cycle" at q=6528 may be aliased export noise; only
window-membership statistics are trustworthy at single-export
resolution.  Sub-v resolution needs the eps-lever instrument
combined with the phase scan (eps tomography AT q=6528).
NEXT-WINDOW: eps-lever + phase instrument for corpus phases (pin the
internal value to ~2v at q=1664/6528 as eps-v3 did at q=64), then
the large-q L1 term, then theta closed form, then ports.

## 2026-08-15 (later 115): eps-at-phase instrument captured at corpus phases

Captures (both banked, plans committed):
- c-epsphase-plan-v1 (k=bl-64 lever: too coarse, every step flips;
  calibration lesson: k = bl(dm*disp) - 70 reproduces eps-v3's
  frame) sha f70bda817309a6c13a9166917bf3473f28c8d9a00a51273bf3a59a
- c-epsphase-plan-v2 (k=bl-70): flips/scan 4-13 - usable resolution
  (lever step ~G/3..G/8).  sha
  882b149ba1455cabd9284a609cc4881e5daac3e60af0215db8e0171a9ad34691
  Scans: q in {1664, 6528} x 12 t values (incl. killer 3072, 0xF00
  3840, census t=65..199 band) x 12 rows x 32 eps steps.
NEXT-WINDOW START: decode epq2 with SELF-CALIBRATED levers (lever
slope from consecutive up-flip spacing within each scan; boundary
values from the exported words) -> V_internal(q,t,ty) at ~2-4v ->
subtract P + L1 -> the large-q correction map -> close L1 general
form -> theta closed form / 14-phase table -> gated composite ->
held-out -> ports 91 -> 80 -> 0.

## 2026-08-15 (later 116): eps-phase decode confirms the large-q term (t-selective, ~1.7G)

First-pass self-calibrated decode of epq2 (lever slope from flip
spacing; NOTE: the printed corrections carry a systematic +4G-ish
offset = the eps base at u=0, not yet subtracted; deviations FROM
the +261v baseline are the signal, scatter +-20v decode noise):
- Most (q,t,ty): baseline (L1 correct within noise).
- q=6528, t in {117,121,199}: corrections ~ -110v vs baseline
  across ALL rows (ty23 ~ -150) - REAL internal deviations ~1.7G,
  dm-selective, row-stable: the large-q L1 second-order term is a
  PER-dm CONSTANT at fixed phase (matching the per-child constant-
  fold insight of later-113; these t are the census band).
- t=3072/3840 (killer/0xF00 families): baseline at q=6528 - the
  small-q families are NOT the large-q deviants; the large-q term
  has its own dm-selection rule (t=117=0x75, 121=0x79, 199=0xC7...).
NEXT-WINDOW: clean the decode (subtract eps base, median-filter
lever), map the large-q deviant set exactly over the 128-dm ladders
of thp2 (which t deviate at q=1664/6528 and by how much), fit the
rule; then compose; then ports.

## 2026-08-15 (later 117): large-q deviants = wrap-boundary cells; cut law refinement

thp2 128-dm deviant map at corpus phases: ~100/128 t deviate with a
smooth +-10..30v ripple PLUS ~-110v (~1.7G) BANDS at t={37-39,
117-123, 199} (q=6528) and t={143-159} (q=1664).  Band spacing =
2^19/q in t EXACTLY (80.3 / 315) and the band positions align with
arg = (dm*q mod 2^19)/2^19 ~ 0.45-0.53 = MY WRAP CUT: the bands are
cells where the model's wrap decision differs from hw's.  The wrap
boundary is NOT the constant 29/64 measured at q=64: at large q the
hw un-wrapped cells extend to arg ~0.53.  Candidate laws tested by
hand (arg >= 1/2; RNA at 2^16) each fit one phase and break the
other - the wrap decision needs the per-(q,t) empirical extraction:
classify every band cell as hw-wrapped/unwrapped from the export,
map the boundary in arg space per q, THEN fit.  The smooth +-20v
ripple outside bands is separate (second-order, possibly decode
noise - needs the cleaned eps decode).
NEXT-WINDOW START: wrap-decision extraction per (q,t) from thp2 ->
cut law; cleaned eps decode for the ripple; then compose; then
theta; then ports 91 -> 80 -> 0.

## 2026-08-15 (later 118): wrap decision is a parity-split rounder at 2^19 - phase conflict open

Empirical wrap classification at q=6528 (unwrap/wrap per t near the
boundary): the decision follows cut = 32/64 for floor(dm*q/2^19)
EVEN, 35/64 for ODD - every boundary cell consistent (t=199 unwrap
at 0.478/even, 201 wrap 0.503/even, 121/123 unwrap 0.507/0.532/odd,
125 wrap 0.556/odd, all higher args wrap).  The wrap is ANOTHER
biased parity rounder (same motif as the export's 25/17).
CONFLICT: tt4 (q=64) global scoring prefers flat 29/64 (16607) over
(32,35)-parity (16281) - the cut law is phase-dependent OR the
parity bit is not floor(dm*q/2^19)&1 (at q=64 that bit is dm bit 13
= 0 for the lo-block; conflict is 29-vs-32 for the SAME even class).
ARBITRATION: extract q=1664's empirical wrap boundary (band
t=143..159) the same way; three phases will pin the true cut law.
NEXT-WINDOW START: q=1664 wrap extraction -> unified cut law ->
rescore all phases -> theta -> composite -> held-out -> ports.

## 2026-08-15 (later 119): wrap cut = 30/64 universal for power-of-2 phases

Empirical cut windows per phase (unwrap-max, wrap-min in arg):
  tz4 (q=16):  (0.4609, 0.4688]   <- tightest; EXCLUDES 29/64
  tz5 (q=32):  (0.4531, 0.4688]
  tt4 (q=64):  (0.4375, 0.4688]
  tz8 (q=256): (0.3750, 0.5000]
  tz9 (q=512): (0.2500, 0.5000]
ALL consistent with cut = 30/64 = 0.46875 - ONE constant, NO tz
dependence (kills the tz-cut theory; simplifies L1's final form).
Non-power-of-2 phases (odd part != 1): cut shifts UP:
  q=1664 (13*2^7): class-0 cut in (0.5046, 0.5110] ~ 65/128
  q=6528 (51*2^7): even-floor ~ (0.478, 0.503], odd-floor
    (0.5315, 0.5564] ~ 35/64 - parity-split.
The odd part of the phase drives a +5..10/128 cut shift with a
floor-parity split - the remaining wrap unknown, constrained by
three phases.  q=1664/6528 boundary tables banked (later-117/118).
NEXT: fit the odd-part cut shift (candidates: second-level rounder
consuming bits 19..21 of dm*q; q_odd-scaled bias), update
wide_law.py to cut=30/64 + odd-part term, rescore everything;
then theta; then ports.

## 2026-08-15 (later 120): wrap law at 99.4% across seven phases

Joint fit over 1788 empirically-classified wrap decisions (7 phases:
tz4/5/tt4/tz8/tz9 + corpus 1664/6528):
  *** wrap iff (dm*q mod 2^19) >= 60/128*2^19 - 1/128*2^19*((dm*q>>19)&1)
      -> 1777/1788 (99.4%) ***
The 11 misfits are contiguous t-bands at the corpus phases (1664:
t=151..159 cl0; 6528: t=39,119-123,199) - hw UNWRAPPED at args
0.478..0.53 where the fit wraps.  Same-class pow2 cells (tz4 cl0)
wrap from 0.4688, so (A>>19)&7 is NOT the full discriminator; the
remaining bit distinguishes odd-part phases in the boundary zone
only (~0.6% of wrap decisions ~ 0.02% of all cells).  Candidates
eliminated: floor mod 4 alone, tz(A) monotone cut.  Best structural
candidate: the boundary zone is the SECOND-level rounder's own
transition (recursion depth 2), needing the eps-instrument at a
boundary-band (t=119..123, q=6528) to read the internal value
directly - scans exist in epq2 (t=117/121 are in its TVALS!).
NEXT-WINDOW START: read epq2's t=117/121 q=6528 scans (internal V
at the boundary band) -> the last wrap bit; update wide_law.py
(cut 60/128 + parity term); rescore all; theta; ports.
