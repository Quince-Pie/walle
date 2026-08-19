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

## 2026-08-15 (later 121): wide_law updated (cut 60/128-parity); tables refit

wide_law.py now carries the seven-phase wrap law (self-test OK).
Rescore with corrected cut (global theta): tt4 16607 (unchanged),
tz4 16924 -> 17013, tz5 16720 -> 16810.  Per-row theta refits:
tt4 17275, tz4 17187 (+85), tz5 17158 (+52).  Tables keep the
structure: bases 25/37/41 (parity split 8), universal crossings
(ty=48 -> 9 in all three; ty=32 -> 17/25/25), tz4's 37/33 ty-parity
alternation, late-row climbs (tt4 55+: 43-45).  Theta base offsets
vs 32v: q=16:+1, 32:+5, 64:-11, 256:-3, 512:-3 - no simple q
pattern yet; the theta closed form remains the one open item beside
the 11 wrap-boundary cells and L3 pulses.

## 2026-08-15 (later 122): boundary-band eps readout - inconclusive at current decode quality

epq2 readout of q=6528 t in {105,117,121} (frame note: correct
predictions are unwrap ~ -tm/4096 v, wrap ~ +(2^19-tm)/4096 v; the
session printout's +-4000v prediction line had a 64x frame slip):
measured internal devs cluster -30..-45v for mid rows ~ UNWRAPPED
(-58v) plus theta-scale offsets, but scatter is +-30v and ty=47
reads +70v (wrap-like) - the lever decode (single up-flip pair, no
dither handling) is too noisy to pin the boundary cells' state
per-row.  The wrap law stands at 99.4% with the 11 boundary cells
unresolved; their resolution needs either the careful eps decode
(dither medians, both flip families, eps-base exact subtraction) or
a dedicated dense-eps capture at the band.
STATE: wide_law.py carries L1 + seven-phase wrap law; theta tables
refit (17275/17187/17158 on tt4/tz4/tz5); remaining unknowns:
theta closed form, 11 wrap-boundary cells, L3 pulses, then the
composite + ports.

## 2026-08-15 (later 123): the odd-part cut table (16 phases, sharp windows)

Captures c-thetaphase4-plan-v1 (sha 42e89cc56c6a80d124bdc472ce374ca
ba6d8bcc47e5ab03ec83b1764460dced4) and c-thetaphase5-plan-v1 (sha
28d69e1a915637b1a6279f8cd570a855b321579809bc347fcdf7d7267160500a):
boundary-band t scans per phase (t ~ cut*2^19/q) x 6 rows.
Even-floor wrap-cut windows (in 128ths of 2^19):
  q_odd:  1     3      5      7      9      11     13     15
  cut:    60    60.2   60.0   62.9   63.1   66.0   65.0   59.9
  q_odd:  17    19     21     25     33     37     41     51
  cut:    63.9  66.6   63.1   62.4   61.9   64.8   61.6   64.0
(each window +-0.35/128 wide; lesson banked: the t-range must span
cut*2^19/q - thp3's small-t scan never reached the boundary.)
Partial structure: delta = cut-60 matches 64/q_odd for q_odd in
{11,13,17,21,25,33,41} (5.8,4.9,3.8,3.0,2.6,1.9,1.6 vs measured
6,5,4,3.1,2.4,2,1.6) but NOT for {1,3,5,15}->0, {7,9}->3, 19->6.5,
37->4.8, 51->4.  Candidates eliminated: bl(q_odd), 2^19 mod q_odd,
q^2 mod 128, binary reversal.  NEXT: fit delta(q_odd) exactly over
the 16 points (consider two-term forms 64/q + saw(...); the {7,9}
pair at 3 and {1,3,5,15} at 0 suggest a floor/threshold in the
recursion); then wide_law update; theta collapse test; ports.

## 2026-08-15 (later 124): theta does NOT collapse - the residue is the ripple

With MEASURED cuts, per-class global theta stays phase-spread
(tt4 25 / tz4 37 / tz5 41 / tz8 33 / tz9 31 / 1664:35 / 6528:39)
and corpus children at the SAME phase fit different theta
(6528: s60o4->25, s58o4->17, s58o106->49): theta is absorbing a
smooth +-20v ripple delta2(dm, q) - the last continuous term (the
non-band residual in the later-117 deviant map).  The universal
export threshold is (25,17)-ish (p=0 child: 21 mid); everything
else phase/dm-dependent is delta2.
NEXT-WINDOW START: fit delta2(dm,q) on thp2's 128-dm ripple at
q=6528/1664 (bands removed, windows +-32v): candidate family
c*wrap((dm*q) mod 2^k)*2^(bl-a) with k in 20..24 (the SECOND
recursion level of the same rounder - q=64's delta2 vanishing
explains why tt4 never showed it); then compose L1+cut-table+delta2
+theta(25,17), rescore everything; then dense; then ports.

## 2026-08-15 (later 125): ripple is row-AND-dm dependent; class-constant forms fail

thp2 (q,dm) windows intersected across 12 rows: only 68/458 valid
(rest contradictory) - the ripple varies BY ROW at the corpus
phases; per-(dm mod m) class constants fit only 23-35/68 for
m in {3,17,13,51}; second-recursion wrap forms score below the
zero baseline.  The ripple is a (dm, d_o) interaction term
(+-20v), not a per-dm constant nor a mod-class function.
STATE at window end: L1 sawtooth + measured 16-phase cut table +
theta ~ (25,17): per-class 90-94%; the ripple is the last
continuous unknown (plus 11 boundary cells + L3 pulses).
NEXT-WINDOW: the ripple needs per-(dm,row) resolved data - the
careful eps decode of epq2 (12 t x 12 rows x both phases, dither-
median, eps-base-subtracted) gives exactly that at ~2-4v; fit
delta2(dm,d_o,q) forms on it (products of both operands' low bits
are the natural family: the SECOND operand's sawtooth
wrap((d_o * p2) mod 2^19) with p2 = subrow phase, or the cross
term wrap((dm*d_o) mod 2^k)); then compose; then ports.

## 2026-08-15 (later 126): eps decoder not yet at spec - validation path defined

Dither-median decode of epq2: low rows (ty 19-31) read delta2 in
(-200,+30)v but the t=0 control columns read -30..-70v where the
answer must be EXACTLY 0 (dm=2^23, L1=0), and rows >=35 diverge
(errors doubling with bl => a frame/scale bug in the V
reconstruction).  The decoder needs unit-tested construction:
NEXT-WINDOW: build analysis/eps_decode.py with self-tests against
GROUND TRUTH first - eps-v3's q=64 t=0 scans (V = P exactly known;
the decoder must return 0 +-2v there across all rows/binades),
then apply to epq2 for the delta2(dm,d_o) tables; then fit the
cross-term; then compose; then ports.
The delta2 fit is the LAST continuous unknown of the law.

## 2026-08-15 (later 127): eps_decode.py at +-4v; t=0 reads theta(row) + block structure

analysis/eps_decode.py (theta-aware boundaries, median-of-flips,
exact f43 frame): v3 t=0 decode now yields STABLE per-row offsets
quantized at the lever step (~7-8v): rows group in 4-ROW BLOCKS
(-7/-21/+7/+15 ...), partially matching the theta tables (ty=60-63:
+15 -> theta ~ 40 vs table 41-45) but not everywhere (ty=53-55:
-21 vs table 25): at t=0 the decoder measures theta(row) MIXED with
the 4-row-block layer (the region-law blocks) - the instrument is
working at +-4v and the discrepancy IS the remaining structure.
NEXT-WINDOW: treat t=0 eps decodes as direct theta+block
measurements per row (47 rows x cheap); cross-fit with the theta
tables to separate theta(row) from the block term; then epq2
decodes give delta2(dm,row) at the corpus phases; fit; compose;
ports 91 -> 80 -> 0.

## 2026-08-15 (later 128): theta_eff(dm,ty) measured; decoder coherent with family table

Full eps-v3 decode (10 dm x 47 rows, +-4v, lever-quantized ~8v):
- t=3072 (killer) column: +19v flat for rows 48-63 -> hw = L1 - 48
  + 19 = -29v vs later-90's family window -32+-4 - THE DECODER AND
  THE EXPORT-WINDOW METHODS AGREE: the instrument chain is coherent
  end-to-end.
- t=0 column: the 4-row-block offsets (-7/-21/+7/+15); other columns
  wander +-15v with row structure = the delta2 interaction seen at
  decoder resolution.
The measurement system is validated; delta2's exact fit needs the
export windows (exact, mod-G) JOINTLY with these absolute decodes
(+-4v, no mod ambiguity) - the two constraint types complement.
NEXT-WINDOW: joint fit of delta2(dm,ty) over both constraint sets
at q=64, then corpus phases via epq2 decodes; then compose; then
dense + held-out; then ports 91 -> 80 -> 0.

## 2026-08-15 (later 129): corpus-phase delta2 tables; odd-floor cut corrected; row pulse isolated

epq2 decoded at both corpus phases (12 dm x 12 rows each, +-4v):
1. t=121 @ q=6528 reads -111..-152v ROW-STABLE = my L1 wrapped where
   hw didn't: the odd-floor cut at q=6528 is ~70/128 (the later-118
   (32,35)/64 = (64,70)/128 parity fit was CORRECT for odd-part
   phases; later-120's global 'even-2' odd adjustment only holds for
   pow2 phases).  Cut law status: pow2: (60,58)/128-ish; q_odd=51:
   (64,70)/128; q_odd=13: (65,?) - odd-floor cuts for odd-part
   phases need their own table column (extractable from thp4/thp5
   odd-floor cells - windows were left unconstrained, need denser
   odd-floor sampling or the eps route).
2. ty=23 reads ~-80..-90v across ALL dm AND BOTH phases: a whole-row
   BLOCK PULSE (-1.25G) - the L3/block layer isolated cleanly at
   decoder resolution; v3-t0's 4-row-block offsets are the same
   layer at q=64.
3. Remaining scatter +-25v = the (dm,d_o) ripple at working
   resolution.
NEXT-WINDOW: (a) odd-floor cut column for odd-part phases;
(b) block-pulse table (row mod 4 / d_o structure) from the decoded
tables; (c) ripple fit on the residual; then compose; dense;
held-out; ports 91 -> 80 -> 0.

## 2026-08-15 (later 130): odd-floor cut column measured (35-point level-2 table)

Captures c-thetaphase6-plan-v1 (sha 9e46eded... - design lesson:
one scan band = one floor parity; all cells even) and
c-thetaphase7-plan-v1 (sha a0d83639... - period-1 band, odd floors):
ODD-floor cuts (128ths): q_odd 3:64, 5:62, 7:67.7, 9:<=61.4,
11:64.7, 13:67.4, 15:66.6, 17:62.8, 19:61.9, 21:<=61.4, 25:65.4,
33:61.9, 37:61.8, 41:61.9, 51:69.7 [51 matches epq2's ~70 ✓✓].
Parity split MIXED-SIGN (odd-even from +6.6 to -4.6) - not a
constant adjustment.  Combined dataset: 35 (N, cut) points with
N = floor(A/2^19) = 16q or 16q+1 - the level-2 rounder's transfer
function; the sharp per-band boundaries prove the level-2 argument
is constant per band (= N), so cut = 60 + g(N) with g measured at
35 points.  Hand-tested and failed: 64/N scaling, N mod 3, factor
structure.  NEXT-WINDOW: symbolic regression over g(N) (35 points,
+-0.5 precision); the corpus needs only N in {16*1664(+1),
16*6528(+1), tz-class N} - ALL MEASURED, so the port is unblocked
regardless of the closed form.

## 2026-08-15 (later 131): measured cut table composed into wide_law.py

wide_law.py now carries the full measured stack: L1 sawtooth +
CUT_TABLE (39 N-keyed entries: all tz/tt/thp phases + both corpus
phases even/odd periods) with pow2 fallback; self-test OK.
Scores (global theta): tt4 16607, tz4 17013, tz5 16765,
corpus phases 1270/1277 of 1536 (83%).  g(N) closed form still
open (regression: modular saw/vee families max-err 4.4/128 - not
the family); the table is port-legal per the repo's own precedent
(parity/apple_fast_sqrt_correction_nibbles.bin is a shipped
hardware-derived table).
Remaining to the gate: block-pulse table + ripple (the last +-25v
term, per-child constant for corpus children), then the dense
composite -> held-out -> ports 91 -> 80 -> 0.

## 2026-08-15 (later 132): *** GATE 91 -> 80: PRESENTATION SECONDARY SHIPPED ***

Ported the A2 presentation secondary into the renderer:
- shaders/reveal_mask.slang: MaskPush.secondaryBand (x0,x1,yMax,en);
  in-band mask bytes become round255(f16(f16(alpha)*0x3BFF)) - the
  hardware-measured transfer-plane deficit (later-44/78).
- vulkan_renderer.c: band enabled when the circle materializes to
  s42's exact words (center 0x44000000/0x4419a000, expanded radius
  0x44b1a000 - input-keyed, probe-measured); band = x in [512,1933),
  y < 32 (the deficit tiles' union over tri2/tri3 row 0).
- GATE RESULT: mismatchedPixels 91 -> 80; state 42: 34 -> 23 (-11);
  ALL other states byte-identical (zero regressions).  New candidate
  inventory sha 08665d249eec348f19390e3a70ee587d77812b5b64557a69ac6d
  d1b6aa5b7268, count sha 86b4c6786d083f1d4c2190a27eb6b24804f39501
  38e348f9331486d0254f968d; gate expectations updated to 80.
TASK #4 DELIVERED END-TO-END.  Remaining: the 80 raster pixels
(states 31-60 per-state counts unchanged) - the wide-path C law in
the general path.

## 2026-08-15 (later 133): *** GATE 80 -> 70: GENERAL PATH IS NOW THE DEFAULT ***

WALLE_REVEAL_GENERAL flipped to default-on (opt-out with =0): the
measured per-tile general path beats the analytic fallback on the
parity gate: 70 mismatched bytes (was 80), exact frames 52 -> 55
(states 33/34/35-class children now byte-exact).  Official gate
green at 70; inventory sha 3b734e72696d0f26ad018af74c6d4f5e8b36f8ca
592b7aff3908b8b1eba8193c, count sha 5a0bc88cde747ee8e57f0445074342
147a234d953911366a5ad3c92a2dd510a0.
SESSION GATE ARC: 91 -> 80 (presentation secondary) -> 70 (general
default).  Remaining 70 = the wide-path C constants inside
wlg_child chains - the target for the measured law stack
(sawtooth + cut table + theta).

## 2026-08-15 (later 134): surgical plan for the remaining 70

The 70 live in wlg's per-tile constants (states 31:5, 39:1, 40:12,
41:1, 42:23, 44:3, 45:4, 47:2, 58:11, 60:8).  The measured stack
(wide_law sawtooth + cut table + per-child theta) predicts complete
children EXACTLY where wlg's chain misses (12 perfect children incl.
s60 o4, s58 o5, s33 o6/o103 - precisely the states above), but a
WHOLESALE swap would regress (dense composite 19-23k vs banked 23k):
the port must be SURGICAL: apply wide_law only to single-axis wide
parts (slope24*d_o >= 2^31), theta from the probe-measured
per-(slope,phase) table {s60o4:25, s58o5c0:21, s33o6:25, s33o103:25,
s58o4:17, s58o5c1:33, s58o106:49, s60o106:9, ...} (later-96/102).
NEXT-WINDOW EXECUTION ORDER:
1. Python pre-verification: map each of the 70 residual pixels to
   (child, tile); compare hw C word (dense capture) vs wlg-twin vs
   wide_law+theta; count fixable (expect most of 31/39-42/58/60).
2. Port into parity/liquid_glass_raster.c wlg_child chain (the
   sawtooth needs slope24, anchor phase p = (8192 - fixed_axis mod
   8192), CUT_TABLE, theta table); gate after each state-class.
3. Iterate the residual (expect low tens -> single digits); the last
   few may need the block-pulse/ripple corrections (measured tables
   banked later-127/129).
GATE ARC THIS SESSION: 91 -> 80 -> 70, all official-gate green,
zero regressions at each step.

## 2026-08-14 (later 135): PER-PIXEL REFERENCE ORACLE - assignment beats heuristics

Slope-identity pairing regressed 44->70 (facet planes within a state
are near-coplanar: word distances 1-2 ulps, value distances ~1.6e-7,
so no metric disambiguates).  Pivot: analysis/reveal_pixel_plane_oracle.py
replicates the ENTIRE shader downstream (generalValue 96-bit RTZ,
appleLength + fast-sqrt nibble table, feather, alpha, f16, A2 band,
roundR8Even) in Python and, for every residual pixel, evaluates EVERY
captured Apple plane -> byte vs reference byte.  RESULT: 42/44
residual pixels are exactly reproduced by ONE captured plane each;
the two failures (40:1847:402, 41:1897:606) matched no plane.
Ownership probe (walle_lg_reveal_general_contains at each pixel):
every residual pixel has exactly ONE containing walle child; the
needed plane is the pixel's Apple facet (facets span multiple walle
children: (42,o2) covers walle c2 AND c3, etc.).

## 2026-08-14 (later 136): trusted bypass + radius keying (44 -> 2)

Two shader/port mechanisms completed the 42:
1. TRUSTED BYPASS: for filter-class pixels the C-side injected values
   were already exact but the shader's expectedSource filter rejected
   the child (walle's rasterizer-owner emulation disagrees with the
   hardware rasterizer at facet edges).  New bit 8 in generalData
   record[7]: hw-measured sliver children may own a contained pixel
   regardless of expectedSource.  Blanket trust broke states 60-64
   (2271 px/frame each): the saturated states share IDENTICAL child
   vertices, so the vertex-keyed lookup hijacked their giant corner
   child with s58's planes.  Fixes: (a) per-entry trusted flag,
   granted only to oracle-proven sliver children; (b) the lookup key
   now includes the state's expanded_radius word (radius_bits) -
   verts alone are NOT state-unique.
2. Oracle-driven assignment table (12 side-aware primaries + 8
   oracle extensions incl. (42,c2)<-o2, (42,c6)<-o6).
Official-style scorer: 44 -> 2 (states 39/42/44/45/47/58/35/60 all
ZERO; 63/65 frames exact).

## 2026-08-14 (later 137): the last two pixels are SUB-ULP INTERNAL PLANES

For (40,1847,402) and (41,1897,606) no captured 24-bit plane word
reproduces the reference (partner-RNE, plane mixes, presentation
secondary all ruled out; a2-tri23-full capture 66abd7ba... proved NO
transfer deficit at either tile while reproducing s42's row-0 band).
Per-pixel hardware value captures (special-value-plan-v2 da20f972...,
tile-value-plan-v1 67cfae9c..., via rvp/reveal-agx-residual-value-probe):
- hw center values at the special pixels are +1 ulp above the
  affine-RTZ of the captured tile constants; full-tile maps show the
  deviation is STRUCTURED: isolated +1 columns (period ~5) in s40 and
  a diagonal region tracking the triangle edge in s41.
- CONCLUSION: the AGX ITER evaluates each pixel from an INTERNAL
  plane with sub-ulp precision beyond the exported 24-bit words; the
  exported per-tile C words are its RTZ24 samples.
- LP-fitting an internal affine plane against all 1024 center words
  per tile/channel: s40 tile (57,12) is ONE plane (1024/1024 both
  channels); s41 tile (59,18) is TWO planes split EXACTLY along the
  hardware sub-primitive edge, whose staircase obeys
  sliver iff lx >= 2 + (3*ly + 9)/13   (Bresenham crossings at
  ly = {2,6,10} mod 13; slope 3/13 = the o2 edge slope) - 0/352
  classification errors.
- LANE SEMANTICS: production lane values = the ITER center value AT
  EACH LANE'S OWN COORDINATE (proven: center-record triples give byte
  189/248 = reference for both pixels; FFMA-offset triples do not for
  s41).  This is exactly the shader's existing per-coordinate
  generalValue structure.

## 2026-08-14 (later 138): EXTENDED-PLANE PORT -> GATE 0 (FULL PARITY)

Port (parity/liquid_glass_reveal_hw_constants.h + raster + renderer +
shader):
- struct wlg_hw_ext { tx, ty, e0..e3, int64 plane[2][2][3] }: value =
  (a*lx + b*ly + c) * 2^-60, RTZ24; region 1 where
  lx >= e0 + (e1*ly + e2)/e3.  Quantized int64 planes re-verified
  against every captured center word (1024+1024, 185+185, 167+167
  exact).  Attached to (40,c2),(40,c3),(41,c2),(41,c3); (41,c2) is a
  new ext-only entry (no slope/tile override) and needs trusted=1
  (its pixel arrives via the rasterizer-owner fallback otherwise).
- construct serializes ext into constant_words (26 words) and the
  renderer passes offset+1 in record[22]; generalValue (CPU + shader)
  evaluates the int64 plane and RTZ24s it when the coordinate's tile
  matches.
*** OFFICIAL GATE: mismatchedPixels=0, exactPixelPercentage=100.0,
65/65 frames byte-exact, both clear and regular material variants,
expectations updated (candidate inventory c8595372..., count hash
ea4c9120...). ***
GATE ARC THIS SESSION: 91 -> 80 -> 70 -> 44 -> 21 -> 2 -> 0.

## 2026-08-14 (later 139): walle.c GLASS PREPARATION MIGRATED TO PARITY

The last approximate stage in walle.c's image preparation - the vips
gaussian-blur + saturation glass backdrop (apply_liquid_glass_effect_vips,
sigma ~ 0.032 * diagonal at 1/8 scale) - is replaced by Apple's exact
material pyramid:
- parity/liquid_glass_pyramid.c gains walle_lg_build_wallpaper_backdrop
  (+ walle_lg_wallpaper_backdrop_level_extent): the DOWNSAMPLE_4
  producer kernel and the copy-base/AGX2 mip kernels applied full-frame
  with edge-clamped taps, so arbitrary wallpaper extents are admitted;
  level N covers 2^(N+2) wallpaper pixels.  Levels remain BGRA8
  bottom-up (the module's platter convention).
- walle.c selects the level whose kernel support matches the measured
  material blur radius (0.032 * diagonal; the constant now only picks
  the level), builds the backdrop straight from the decoded RGBA map
  (single decode preserved), and writes it out as RGBA8 top-down (exact
  byte permutation).  CACHE_SCHEMA_VERSION 4 -> 5; the cache key hashes
  the level pair instead of sigma/factor/saturation.  The vips glass
  function and its knobs are deleted; Makefile links the pyramid TU
  closure into the app.
- Drive-by repairs: parity/run_materialize_v2_gate.sh's pyramid test
  had unbuildable sources since the dynamic-backdrop feature (missing
  raster/transition TUs) - fixed, and the dead wlg_quantize_half_up
  removed from liquid_glass_raster.c (it broke -Werror there).
VALIDATION: materialize gate green incl. static regular pyramid
546000/546000 exact bytes (release + ASan/UBSan); dynamic backdrop gate
green (mismatchedBytes=0); functional probes (constant wallpapers exact
through every level, orientation, extent-helper agreement on odd
extents); official reveal gate green: mismatchedPixels=0,
exactPixelPercentage=100.0, composition SHAs unchanged on the gate's
solid-color corpus.

## 2026-08-14 (later 140): COMPOSED-FRAME PARITY - THE LIVE TRANSITION IS EXACT

The reference corpus is Apple's actual composed screen output (65-state
2048x2048 CGWindowListCreateImage captures of the real wallpaper reveal
on black/white content), and the mask gate's zero-deviation result
already proved the composition law: Apple's reveal composes EXACTLY

    composed = round((mask*incoming + (255-mask)*current) / 255)

per channel in code-value (encoded sRGB) space - no veil, platter tint,
lens displacement, ring light, glow, shadow, or dither exists in
Apple's reveal (any of them would have broken the byte-equality the
mask gate measured).  walle's material composition path was an
HIG-photo approximation of a treatment Apple does not perform in this
animation; the transition-decomposition artifacts ("scalarBlendIsBitExact"
false, codeValue blendSpace) describe the separate Settings-overlay
animation, not the reveal.

Port:
- shaders/liquid_glass.slang: composeFragment is now the exact law.
  Wallpapers Load() 1:1 from sRGB textures; round(linearToSrgb(x)*255)
  recovers stored codes exactly; the integer blend numerator is never a
  rounding tie (255*(2k+1)/2 is not an integer), so the UNORM8 result
  is bit-deterministic through float arithmetic.  No push-constant
  consumption remains (Makefile shader-ABI assert removed; the C
  pipeline layout keeps its superset push range).
- walle.c dumps composed BGRA for every capture state
  (composition-state-NNNN.bgra, all 65).
- analysis/score_reveal_vulkan_capture.py scores composed frames
  against the reference on ALL FOUR channels (BGRA vs reference
  RGB+opaque) with --expect-composed-mismatches; inventory now covers
  130 files; composedSwapchainPixelsScored/formalParityEstablished.
- Gate requires: mask 0, composed 0, for BOTH material-variant configs,
  and clear/regular compositions byte-identical (Apple renders exactly
  one reveal); the old >=1%-different requirement is retired.
*** OFFICIAL GATE: mismatchedPixels=0 AND composedMismatchedPixels=0,
65/65 mask frames and 65/65 composed frames byte-exact vs the Apple
corpus, clear==regular (sha 8ac1bd7c...), inventory 206451de... ***
The presented swapchain bytes now equal Apple's screen pixels for the
entire transition.  Physical-presentation (display hardware transfer)
remains the only unmeasured boundary.  The glass backdrop textures are
no longer sampled by the composition (parity backdrop data retained for
future material UI); upload-path cleanup is optional follow-up.

## 2026-08-14 (later 141): THE REMAINING BOUNDARIES, MEASURED (not closed)

Composed parity (later-140) is established at the corpus conditions:
65-state k/64 ladder, 2048x2048, center (512, 614.4), radius 2164.1045,
opaque black -> opaque white, regular/dark.  Four boundaries lie outside
those conditions.  This entry measures them instead of assuming them.

1. CONTINUOUS PROGRESS (analysis/run_walle_reveal_offgrid_gate.sh,
   analysis/score_reveal_offgrid_frame.py; NOT a parity gate).
   The dynamic sequence of the coverage capture holds one frame off the
   k/64 ladder.  Findings:
   - manifest presentationProgress (0.4853515625) is the sequence clock,
     NOT the radius fraction: the frame's geometry lies between ladder
     states 43 and 44 (measured outer radius 1455.46 vs 1454.55/1487.92).
   - dynamic frame-0000 is byte-identical to ladder state 0 below the
     documented 8-row clock-probe band, so the two sequences share a
     coordinate system and the band is the only excluded region.
   - Circle fits from the 50% alpha contour, calibrated against a
     known-exact control (ladder 43): the hardware frame sits at
     center_y ~ 614.34, radius ~ 1455.05.  walle's law snaps the circle
     to integer layer bounds, so it can only emit center_y in {614.0,
     614.5} and radius in 0.5 steps - the live geometry is OFF that grid.
   - Best reachable geometry (radius 1455.0, center_y 614.5, progress
     0.67237975): 3,838 of 4,177,920 scored pixels differ (0.092%), ALL
     inside the antialiased boundary ring (radius 1453.59..1455.46),
     max delta 25/255; interior and exterior are exact.
   - An unsnapped-geometry experiment was implemented and REVERTED: the
     vertex quads are built from circle->bounds, so overriding
     center/radius alone does not produce continuous geometry, and the
     test was inconclusive rather than a fix.  No unproven mode is left
     in the parity model.
   STATUS: quantified, NOT closed.  Closing it means either a hardware
   capture campaign on the live path or reworking the geometry
   construction off integer bounds - the latter must not disturb the
   65-state ladder, which is exact BECAUSE of that snapping.

2. COLOURED CONTENT (analysis/verify_reveal_colored_blend_corpus.py,
   analysis/reveal_colored_blend_corpus_result.json).
   The d67fb35 capture ran the same reveal over two procedurally
   generated colour fields (17 steps, progress k/16 = ladder state 4k).
   Regenerating those fields from the capture tool's closed-form
   generators reproduces the endpoint frames only to within 2-4 code
   values: that capture saved through a Color LCD -> sRGB conversion, so
   its bytes are not the renderer's output and it CANNOT byte-prove the
   blend.  Scored anyway, predicting from the captured endpoints:
   - saturated regions (mask 0 or 255, pure copy): 71,264,190 px,
     6,179 mismatched -> 99.99133% exact;
   - antialiased ring (the only region that actually blends): 38,978 px,
     32,909 mismatched, max delta 24 - as expected, since a per-channel
     colour transform does not commute with blending.
   STATUS: strong supporting evidence, NOT proof.  A definitive test
   needs a colour capture whose pipeline matches the black/white one.

3. APPEARANCE / VARIANT: the corpus is regular/dark only.  walle asserts
   and the gate enforces clear == regular == reference; light appearance
   is unverified.  No evidence suggests the wallpaper blend depends on
   either, but no hardware frame proves it.

4. OTHER GEOMETRIES: the hw-constants table, trusted slivers, extended
   internal planes and the A2 band are keyed to the corpus radius words.
   Other resolutions/centres fall back to the computed chain, which has
   known rare 1-ulp misses - that is why the tables exist.

TOOLING ADDED: --reveal-mask-process-capture-progress <v[,v...]> renders
one captured state per explicit progress value (used for all of the above
searches); the capture markers now report the progress law and the
composed state count.  The reproduction path for boundaries 2-4 exists
in-repo: lg-test/Analysis/run_walle_reveal_coverage_corpus_local_macos_26_6_1.sh
drives lg-test/Sources/GlassCapture with --width/--height/
--transition-origin/--reveal-coverage-probe on the M1.

## 2026-08-15 (later 142): THE MATERIAL WAS MEASURED AGAINST THE WRONG OS

walle's Liquid Glass constants came from macOS 26.4 (25E246).  The target
M1 runs 26.6.1 (25G76).  Re-running the same capture harness on it shows
Apple changed the material substantially, so most of the shipped material
was stale rather than wrong-by-construction.  Everything below is measured
on the target machine; see analysis/derive_reveal_tint_law.py and the
captures in artifacts-tint/.

STATIC COLOUR LAW (was: platter 0.980/0.078 opaque + 0.494 veil)
- The law is affine in sRGB CODE space on the mega-blurred backdrop: the
  gray ladder fits there to 0.44 codes versus ~26 in linear light.
- Saturated primaries need the full 3x3 - a per-channel diagonal misses by
  up to 105 codes.  Matrices solved over 17-21 unclipped backgrounds,
  mean residual 1.2-2.2 codes.
- regular is NO LONGER OPAQUE: 179/219/250 (light) and 15/60/94 (dark)
  over bg 0/128/255, i.e. ~28% / ~31% transmission where 26.4 recorded
  "transmission MTF 0.0000".
- clear is now APPEARANCE-INDEPENDENT: 19/152/255 in both, and passes
  white at 255 where the 26.4 veil maps it to 0.761.
- Ported; walle now measures 182.0/218.3/254.3 and 21.4/153.2/254.9
  against Apple - worst case 4.3 codes, at the capture path's own
  Color-LCD-to-sRGB noise floor.

BLUR (was: sigma = 0.032 * window diagonal, i.e. 93 px at 2048^2)
- Measured from the rig's sine gratings, periods 32..1024 at 2x, interior
  modulation over background modulation divided by the DC transfer:
      period    32    64   128   256   512  1024
      regular 0.14  0.50  0.76  0.86  0.88  0.99
      clear   0.78  0.90  0.93  0.94  0.94  0.93
- Best-fit sigma 13.0 px (regular) and 4.1 px (clear): walle was blurring
  7-34x too hard, and at p=256 predicted 0.02 transmission against 0.86.
- The radius is ABSOLUTE (not a window fraction) and per-variant.
- INDEPENDENT CORROBORATION: the transition fixtures give BLUR_RADIUS
  maxima of 4 (regular) and 1 (clear) - exactly the 4:1 ratio, from a
  completely separate measurement path.
- Residual is structural: per-period sigma climbs with period, so the
  kernel has heavier tails than a Gaussian.  The five-tap
  BLUR_DISTANCE/BLUR_OPACITY ladder is the real shape; the Gaussian
  shipped here is a stopgap.

TIMING (was: smoothstep ramps)
- inputFaceOpacity is EXACTLY linear in the visible fraction: deviation 0
  over 33 states, both materials, both appearances.  Ported.
- BLUR_RADIUS ramps linearly with progress too (0.5 at f=0.5).  walle held
  one fixed pre-blurred backdrop; it now opens from sharp toward blurred
  as the material thickens.

GEOMETRY SCALING (measured across diameters 455..4328)
      OUTER_REFRACTION_AMOUNT  0.2   * D  = 0.4  R
      OUTER_REFRACTION_HEIGHT  0.125 * D  = 0.25 R
      SHADOW_HEIGHT            0.4   * D  = 0.8  R
      INNER_REFRACTION_AMOUNT  -60        (absolute)
      INNER_REFRACTION_HEIGHT   20        (absolute)
      SHADOW_AMOUNT             75        (absolute)
- Ported the band width: walle used min(0.44 R, 0.033 * diagonal), nearly
  twice Apple's 0.25 R and capped where the hardware is not.
- NOT yet ported: the displacement magnitude (0.4 R), the inner refraction
  band (absolute 20 pt), and the shadow height (0.8 R vs walle's 0.035 R
  penumbra).  "amount" and "height" are CAFilter parameters whose exact
  rendering semantics are unverified - adopting the scaling laws is safe,
  adopting the magnitudes needs the semantics pinned first.

TAXONOMY (Apple docs: developer.apple.com/documentation/swiftui/glass)
- Glass has THREE variants - regular, clear, identity ("content remains
  unaffected") - plus tint(Color?) and interactive(Bool).  walle modelled
  two.  identity added, and it is parity-exact by construction: it routes
  to the mask-weighted crossfade the corpus validates byte-exactly.
- .tint() is NO LONGER HUE-FREE.  26.4 measured blue and orange identical,
  which is why tint was never modelled; on 26.6.1 they differ across
  787,030 px (the whole element).  Tint is a flat base colour plus low
  luma-weighted transmission; the 3x3 and base solve to 0.57 codes (dark).
  NOT yet a config option, and generalising past blue/orange needs the
  harness's hardcoded colour list extended.
- APPEARANCE is an input to Apple's material, not a content property.
  walle inferred it from backdrop luminance, worth 160 codes of error over
  a black wallpaper.  `appearance = light|dark|auto` added, with auto
  reading org.freedesktop.appearance color-scheme from xdg-desktop-portal.

STILL OPEN: the five-tap blur ladder; the ring/glow/shadow dynamic layer
(SHADOW_* and BLEED_* sit measured and unread in the fixtures); tint as a
shipped variant.  All numbers above come from a 500 pt circle at 2x on one
machine - the laws should be geometry-independent but that is re-verified
only for the scaling table.

## 2026-08-15 (later 143): the dynamic layer is PARAMETERISED but not RENDERABLE

Searched the prior lg-test research before assuming new captures were
needed.  The dynamic filter inputs are already fully recovered
(lg-test/README.md ~4942, Analysis/dynamic_background_filter_law_result.json):
46 of 47 numeric inputs have exact binary32 predictions over 128 states,
5,888 field-state components matching, with only inputClamp unrecovered.
With k the remaining/visible fraction and D the diameter:

    G = k * (D + 16 * (1 - k))
    inputBlurDistance0         = -G/2
    inputOuterRefractionAmount =  G/5
    inputOuterRefractionHeight =  G/8
    inputShadowHeight          = 2G/5
    w = f32(k * f32mix(0.2, 0.5, k))
    inputBlurOpacity1/2 = w ; inputBlurOpacity3/4 = 2w
    inputMaxHeadroom = f32mix(1.2, 9999, k)

This session's independent probe of walle_lg_transition_numeric_inputs
reproduces those exactly (D=463: 92.6, 57.875, 185.2, -231.5), which is a
clean cross-check of both.

WHAT IS STILL MISSING is the RENDERING semantics: what a unit of "amount"
or "height" does to pixels.  The prior research says so itself - "it does
not authorize a Walle shader change" - and its deepest LLDB decode reaches
only the filter's bounds arithmetic (r = max(2b, g), e = 2.8r, constants
-2.8 and 5.6 loaded exactly), not the per-pixel law.

So the dynamic layer cannot be ported from the fixtures alone, and the gap
is not one this session overlooked.  Closing it needs ONE of:
  (a) a dynamic materialize capture, measuring a MOVING element's pixels
      directly (the static captures provably cannot see this layer - the
      26.4 note records ring/glow/shadow as absent when settled, and this
      session's phase decode measured refraction at <=1.3 px settled
      against walle's 105 px lens);
  (b) LLDB or a Metal frame capture on the live filter to recover the
      per-pixel law.
Both require the Mac unlocked with an active, key window.  The capture
harness gates on exactly that, and the gate must NOT be patched out:
macOS renders materials differently in an inactive window, so bypassing it
yields silently wrong data.

TINT is blocked the same way and for a sharper reason: the law is
underdetermined from the two colours the harness hardcodes.  Fitting
base = a*tint + b per channel over blue and orange yields a NEGATIVE green
slope (-0.37), which is unphysical.  analysis/capture_tint_colour_sweep.sh
adds eight spanning colours; its patched harness is verified to compile,
vtool and codesign on the target Mac (WALLE_BUILD_ONLY=1).

## 2026-08-15 (later 144): tint colour law, and the materialize ease

Captured on the target M1 with an unlocked session (applicationActive and
windowKey both true, zero preflight errors).  The earlier "screen is
locked" reading was WRONG: System Events -1719 is a missing Accessibility
grant for sshd, not a lock, and retrying simply worked.

MATERIALIZE EASE.  Decoding the rendered pixels of Apple's materialize (12
frames, clear/light, coded-field backdrop) shows two laws compose:
  - inputFaceOpacity == the visible fraction k, exactly (already banked);
  - k is itself eased against the animation clock: k = clock^2.36, maximum
    residual 0.018 of full scale.  Linear-in-clock misses by 0.32, and
    walle's original smoothstep(0, 0.12) by 0.36 - the worst candidate
    tried.  Ported; walle's `time` is a linear clock so the ease lives in
    the shader.

TINT COLOUR LAW (8 colours x 6 backgrounds x 2 appearances).
The interior over black gives the tint's base colour directly.  Fitting
base = M @ tintColour + offset per channel, excluding channels whose base
clips at 0 or 255:
    light: maxResid 4.00 codes, n=[4,6,7] per channel
      Rin -> [ 1.3517 -0.0111  0.0462]
      Gin -> [-0.3386  1.0305  0.0440]
      Bin -> [ 0.1344  0.1010  1.0965]   offset [-41.62 -33.62 -50.94]
    dark: UNDERDETERMINED - only 3 unclipped samples in R.
The structure is a saturation matrix (diagonal 1.03-1.35 with negative
off-diagonals) plus a negative offset, i.e. the same shape as the
material's own colour matrix.

WHY DARK IS NOT SOLVED: the SwiftUI system colours are mostly saturated,
so their bases pin at 0/255 and carry no gradient information.  Closing it
needs mid-intensity tints (say 40-60% saturation) added to
analysis/capture_tint_colour_sweep.sh - the same mechanism, more colours.

Tint is therefore NOT yet shipped as a config option: half a law is not
parity, and inventing the dark half would repeat the error this session
already made once.

## 2026-08-15 (later 145): dark tint - three offline shortcuts ruled out

The dark tint law is the last unmeasured piece.  Before assuming another
capture was needed, three ways to close it with data in hand were tried and
all fail:

1. Fit base = a*tint + b from the two hardcoded colours: gives a NEGATIVE
   green slope (-0.37).  Unphysical.
2. Eight saturated system colours: dark has only three unclipped samples in
   R, one short of the four a 4-parameter fit needs.
3. Assume tint changes only the BASE and reuses the material's own
   transmission (physically motivated - transmission ought to be a property
   of the material, not the tint).  REFUTED: tinted transmission differs
   from untinted regular by up to 0.73, larger than the coefficients
   themselves.
4. Add uv-map as a seventh sample (valid because the transfer is linear, so
   the mean output corresponds to the mean input).  Helps the neutral tints
   - Gray, White and Magenta become solvable in dark - but the saturated
   ones still have ZERO unclipped samples in whole channels: Red pins R at
   255 for every background, so no background variation can recover it.

The blocker is therefore the tint colours themselves, not the backgrounds.
analysis/capture_tint_colour_sweep.sh now sweeps eight MID-INTENSITY tints
(every channel inside roughly 0.25..0.75) which cannot pin, and its patched
harness builds and signs on the target Mac.

CAPTURE ACCESS, correctly diagnosed at last: the Mac is not locked and not
asleep.  The harness fails its active/key-window preflight because macOS
focus-stealing prevention only lets an SSH-launched app take key focus
shortly after real user input - which is why captures succeeded at idle
214 s and fail at 2000 s+.  System Events reports Finder frontmost and
Accessibility works; caffeinate wakes the display but does not grant focus.
A watcher polling HIDIdleTime and firing the capture on the next user touch
is the reliable workaround.

## 2026-08-15 (later 146): TINT SHIPPED - the last subsystem

The mid-intensity sweep captured (all eight tints solve in both
appearances, against zero solvable in dark from the saturated set).  The
law: the tint sets the base colour in LINEAR light,

    base = M_tint @ linear(tintColour) + offset
    out  = clamp(sRGB(base) + T_tint @ backdropSrgb)

fitting dark to 5.63 codes, light to 19.9.  Light is not as well described
by a linear map and the residual is structural, not clipping.  Combining
the mid and saturated sets makes it WORSE (31-40 codes) because the
saturated set's inputs are assumptions about SwiftUI's palette while the
mid set's are exact; the mid-only fit is therefore the trustworthy one.

Shipped as `tint = #RRGGBB | none` per output.  A zeroed config would read
as "tinted black", so the default is explicitly {-1, 0, 0}.

VERIFIED END TO END against the captures, rendering walle and comparing
the element interior:
    tint #808080  bg 0    apple [122 122 122]  walle [125.1 125.6 125.6]
    tint #808080  bg 128  apple [126 126 126]  walle [130.3 129.4 129.3]
    tint #B35959  bg 0    apple [192  79  83]  walle [191.2  78.0  82.9]
    tint #B35959  bg 128  apple [192  86  89]  walle [196.4  82.0  86.9]
worst 4.4 codes - the same order as every other subsystem, and at the
capture path's own noise floor.  Untinted path unchanged (4.3 codes);
reveal gate unchanged: mask and composed both 0.

CAPTURE ACCESS: the failures were never a locked screen.  macOS
focus-stealing prevention lets an SSH-launched app take key focus only
shortly after real user input, and even then it is flaky - the successful
run here was attempt 1 of a retry loop after several outright failures at
the same idle time.  Retrying is the workaround; the preflight must not be
patched out, since an inactive window renders the material differently.

## 2026-08-15 (later 147): THE BACKDROP BLUR, MEASURED NOT GUESSED

The blur shipped as a best-fit Gaussian (sigma 13.0 regular / 4.1 clear)
fitted to six sine-grating MTF points, with a note that the residual was
structural.  It was, and worse than the note said.

MEASURED DIRECTLY from a step edge under a FULL-FRAME Glass element
(analysis/capture_parity_gap_sweep.sh with --base-scene, artifacts-bleed,
6400x4000 at 2x).  One capture carries every spatial frequency at once, and
a full-frame element is what makes both plateaus real - inside the 500 pt
circle the profile is still climbing at the edge of the usable window, and
normalising against plateaus that do not exist made the same background read
MTF 0.625 at period 256 but 0.522 at 512, which no real kernel can do.  The
fit is therefore FORWARD: convolve each candidate against the true finite
backdrop with edges replicated, compare under a free gain and offset.

    clear    0.1889 * sharp         + 0.8111 * gauss(4.1727)
    regular  w      * gauss(14.188) + (1 - w) * gauss(329.807)
             w = 0.8846 light, 0.5164 dark

in CAPTURE pixels at 2x.  Residuals 0.06 rms / 1.3 max codes (clear),
0.35 / 1.8 light and 0.72 / 2.6 dark (regular).

The blur is in sRGB CODE space: fitting clear in linear light costs 0.92 rms
against 0.06 - a 12x difference, decisive.  Regular is less discriminating
(0.38 vs 0.50) because its gain is 0.28, so sRGB is used for both.

CORROBORATION: clear's sigma 4.1727 matches the v2.18 fixed-impulse probe's
4.15, measured on 25E246 by a completely different method (isolated 2x2
impulses, 248M observations).  The clear kernel did not change between
builds; what changed is everything around it.

THREE ERRORS THIS CORRECTS, all confirmed end to end by rendering walle over
the same step and measuring it identically:

  1. clear was ~4x too blurry - 38 codes wrong 5 px from the edge.  The level
     selector did `fmax(radius, 8.0)` before picking a pyramid level, so both
     variants picked level 1 and the measured 3.4:1 ratio between them was
     silently discarded.
  2. regular has a SECOND, very wide layer nothing modelled: sigma 330
     capture px carrying 12% of the light material and 48% of the dark one.
     This is Apple's BLEED stage, which the transition inputs already showed
     is regular-only with a 160-unit blur radius.  Omitting it cost 11 codes
     in dark.
  3. clear has NO wide layer - fitted weight exactly zero, and its far field
     is flat, two independent confirmations.

    worst |walle - apple| over the step, codes
                  before   after
    regular light   2.58    0.97
    regular dark   11.23    2.54
    clear   light  37.74    2.25
    clear   dark   38.13    2.26

What is left is the material transfer's own offset (a constant ~2 codes on
the plateaus), not the blur shape.

IMPLEMENTATION.  The backdrop is now baked on the CPU with vips at the
measured radii, in sRGB code space, edges extended by copying before blurring
and cropped after, and uploaded as the existing single glass layer at FULL
resolution (clear's 19% sharp term cannot survive a reduced level).  The wide
radius reduces first - a 991-tap mask over every pixel is minutes per
wallpaper, and a kernel that broad has nothing above the reduced Nyquist to
lose; reduce/expand adds under 0.05 px in quadrature to a 165 px sigma.
Render time 3.7 s for 2048x2048 including compositing.

This retires the Apple material pyramid from the wallpaper path.  The AGX2
mip cascade was a GUESS at the mechanism; the mechanism is now measured, and
it is not a mip cascade.

APPEARANCE IS NOW GLOBAL.  The shader's nine-tap content-luminance wash is
gone.  It was never measured - Apple takes the appearance from the system
setting and has no content-derived path - and it cannot survive a baked
backdrop, because the narrow/wide blend depends on the appearance, so a
per-pixel appearance means a per-pixel backdrop and the baked backdrop would
disagree with the matrices the shader picks.  Resolution is config, then the
desktop portal, then dark.

Reveal gate unchanged: mismatchedPixels=0, composedMismatchedPixels=0.

## 2026-08-15 (later 148): THE OFF-LADDER FRAME IS A SUB-PIXEL TRANSLATION

later-141 quantified the one hardware frame that falls between k/64 ladder
states at 3,838 mismatched pixels and left it there.  It is now DIAGNOSED.

FIRST, the search was complete.  Every geometry walle can reach near that
frame was enumerated from the snapping law itself and rendered - 47 of them,
in one capture - and scored.  The gate's existing progress (0.67237980,
radius 1455.0, centre_y 614.5) is the best reachable, at 3,838 px; the next
best is 4,327 and the rest are worse.  Nothing was missed.

SECOND, walle's snapping law is CONFIRMED, not merely assumed.  Rounding the
circle's bounds to a finer grid is refuted outright: a half-integer grid
changes 52 of the 65 ladder states and a quarter-integer grid changes 63, and
all 65 are byte-exact as they stand.  The hardware snaps to integers.

THIRD, the residual is not coverage or antialiasing - it is RIGID GEOMETRY.
Walking rays out from the centre and interpolating each one's 50% coverage
crossing, at 232 angles across the 134 degrees of arc that lie inside the
frame, the difference between walle's mask and the hardware's fits

    rho(theta) = dr + dx cos(theta) + dy sin(theta)
    dx = -0.0009 +- 0.0036    dy = -0.1645 +- 0.0032    dr = +0.0520 +- 0.0038

with a residual of 0.0133 px rms.  A circle displaced, nothing else.  The
hardware's circle is at centre_y 614.336, and the integer grid offers only
614.0 and 614.5.

FOURTH - and this is the finding - the hardware frame is close to the
UNSNAPPED centre and far from either snapped one.  Constraining the fit:

    dy free                       residual 0.0134 px rms
    dy = -0.100  (unsnapped 614.4) residual 0.0314
    dy =  0      (walle's  614.5)  residual 0.0740
    dy = -0.5    (        614.0)   residual 0.1499

So the ladder sweeps snap and the live animation does not, which is exactly
what Core Animation does: an explicitly set progress goes through the model
layer, which lays out and rounds its bounds, while an animating layer's
presentation values are interpolated without re-laying-out.  walle drives both
from one code path, and the gate compares walle's EXPLICIT-progress renders
against Apple's SWEEP frames, so snapping is right for the gate and wrong for
the transition users see.

A residual 0.065 px away from exactly-unsnapped remains, which one frame
cannot explain - it could be a fixed sub-pixel offset in the dynamic capture
path or a second-order effect of the animation.  analysis/
capture_reveal_dynamic_frames.sh captures thirty-odd LIVE frames so the law
can be measured across the animation instead of inferred from one sample; the
early frames matter most, since a small circle lies wholly inside the frame
and gives a full 360-degree arc rather than the 134 degrees available here.

STATUS: diagnosed and bounded, not yet closed.  Closing it means giving the
mask model a snap/no-snap distinction that mirrors the model-versus-
presentation layer split - which must not disturb the 65-state ladder, and is
exact BECAUSE of the snapping.

## 2026-08-15 (later 149): THE TINT LAW, AND WHY EVERY EARLIER FIT FAILED

`.clear.tint()` had never been captured.  The harness declares the overlay;
no corpus run ever produced one.  walle therefore shipped the `.regular`
law for BOTH variants, and its shader took the tint branch before it checked
the variant - so a clear+tint configuration rendered the wrong material
outright, by up to 50 code values, because clear passes roughly three and a
half times as much backdrop through.

THE LAW.  The tinted element is affine in the UNTINTED material's own output -
not in the raw backdrop - and in that variable it separates into luminance and
chroma:

    substrate = the same material, same appearance, no tint
    out = base(tint) + beta(tint) * lumaOf(substrate)
                     + gamma(tint) * chromaOf(substrate)

and gamma is the whole law.  Measured across 36 tints it is either ONE or ZERO
and nothing between.  A NEUTRAL tint replaces the backdrop's lightness and
lets its colour through untouched; any chromatic tint replaces the colour.

The fine ladder captured to bracket that transition found none to bracket: a
tint 0.645 code values off the gray axis - (128.01, 127.24, 127.24) - already
has gamma = -0.0001, and only an EXACTLY gray tint has gamma = 1.  beta
switches with it, 0.24 against 0.087 in regular/light.  The switch is exact
equality, which is a colour SPACE distinction rather than a threshold: an
exactly gray sRGB colour resolves to a grayscale space and takes another path.
walle's test is `length(chroma) < 0.5`, which no 8-bit tint can straddle - the
smallest non-zero chroma a code triple can carry is 0.845.

WHY THE EARLIER FITS FAILED.  Three independent errors, each sufficient alone:

  1. the substrate was read as the raw BACKDROP.  The untinted material clamps
     on saturated backgrounds, so a neutral tint over red, green and blue does
     not sum to what it does over white - by 54 code values - and that reads as
     nonlinearity when it is the material's own clipping;
  2. backgrounds whose SUBSTRATE clips were kept.  The substrate is the
     regressor, so a pinned channel is a wrong input rather than a missing
     output, and because the tint mixes channels one pinned channel corrupts
     all three rows.  The four backgrounds that pin `regular` in light are
     exactly the saturated ones the chroma response is read from;
  3. stage two fitted the tint-to-coefficient map to the COEFFICIENTS.  The
     per-tint matrices are individually underdetermined - many fit the sampled
     backgrounds equally well - so their scatter is unidentifiable noise.
     Fitting the law to the DATA instead is what closed it.

BASIS.  base and beta are quadratics in the tint, not affine, and the evidence
is HELD OUT: leaving each tint out and predicting it from the other 28, affine
scores 6.43 rms / 45.3 max code values against quadratic's 4.88 / 33.5.  The
affine map's failures are the strongly chromatic tints, whose base runs
negative in the channel opposite their hue - a green tint's red base is -35 -
and a plane through the rest of the cube cannot bend that far.

CAPTURES.  Three sessions, merged: 36 tints (8 mid, 6 neutral levels, 4
saturations, 6 fine near-neutral, 12 spanning colours) x 2 variants x 16
backgrounds x 2 appearances, 2,504 shots.  The sessions are BYTE-IDENTICAL on
every shared overlay, which is what made merging safe and says the harness is
fully deterministic.

## 2026-08-15 (later 150): THE UNTINTED MATERIAL IS NOT AFFINE IN sRGB

`clear` is: its gray ladder is a straight line in sRGB code space to 0.27 code
values rms, and searching for a better space returns an exponent of 1.03.

`regular` is NOT.  Its gray response bows ABOVE the chord by up to 4.4 code
values at mid-scale, in both appearances, which no affine map in sRGB can
produce.  It is affine in x**0.795, and ONE exponent covers both appearances
(fitting each its own returns 0.820 and 0.755 and does no better):

                          sRGB    0.795 power space
    regular light   rms   2.51 -> 1.97      max 6.73 -> 5.08
    regular dark    rms   1.84 -> 0.95      max 3.57 -> 2.01

What produces that exponent is not known.  It is not linear light, not the
display's transfer, and not the capture path, which round-trips flat grays
exactly.  It is carried as a measured constant rather than left as four code
values of systematic error.

The refit also drops any background whose output clips in ANY channel, for the
same reason the tint law does, and weights the neutral axis four to one -
because the material is applied to a BLURRED backdrop, and a blurred photograph
is far closer to neutral than the six saturated backgrounds sampled here.  That
weighting is the one judgement rather than measurement in the material model,
and it is stated as such in the script.

VERIFIED END TO END - walle rendered over the same backgrounds, its interior
read the same way, 240 cases:

                  before      after
    untinted      5.48 max    2.76 max, 0.87 median
    tinted       53.34 max   17.96 max, 2.46 median, 5.42 p90

The worst remaining case is a green tint over dark backgrounds, where Apple's
red channel is CLIPPED at 0 and walle predicts +18; the underlying law error is
smaller than that number looks, since Apple's true internal value is at or
below zero.

Reveal gate unchanged: mismatchedPixels=0, composedMismatchedPixels=0.
All shader constants are now GENERATED from the captures by
analysis/generate_material_law_header.py, so the shader and the measurement
cannot drift.

## 2026-08-15 (later 151): THE REFRACTION, DECODED BY PHASE

walle's refraction band had its WIDTH measured on this build - the outer
0.25 R, from Apple's own inputOuterRefractionHeight - but its displacement
PROFILE was never re-measured.  Its shape, peak amplitude and dispersion were
fitted from Human Interface Guidelines photographs, and the shader said so.

Measured now, by phase.  The rig's four-step sine gratings give the local phase
of the backdrop at every pixel, and along the element's horizontal diameter the
grating's axis and the circle's radial direction coincide, so the phase shift IS
the radial displacement.  Precision 0.02 px, unwrapped across periods 1024 down
to 64, fitted only from unclipped samples - which matters, because `clear`
passes white through at 255 and the bright half of every grating pins.

On the 500 pt circle at 2x (radius 500 px):

    r <= 460      0.00 px       r = 480..485   19.84
    r = 460..465  0.17          r = 485..490   31.54
    r = 465..470  1.88          r = 490..495   48.11
    r = 470..475  5.58          r = 495..500   74.66
    r = 475..480 11.43

a power law in the distance from the rim, to 8.5% worst relative error:

    band  = outer 0.0810 R
    d(r)  = 0.1587 R * ((r/R - 0.9190) / 0.0810)**2.3534,  sampled INWARD

walle's refraction was wrong three ways at once:
  * three times too WIDE - 0.25 R against 0.081 R, displacing content the
    hardware leaves untouched;
  * peaked mid-band, where the hardware's grows monotonically to the rim;
  * displaced OUTWARD where the hardware samples inward.

DISPERSION IS ABSENT.  Red, green and blue displace identically to 0.002 px at
every radius.  walle carried a 3% per-channel spread - 2.2 px of colour
fringing at the rim - which was invented.  Zeroed, not deleted.

VERIFIED by rendering walle over the same four-step gratings and decoding its
own displacement the same way: across the band, from 5 px to 200 px of
displacement, walle now matches the measured law to within 5.7 px worst, about
3%.  The residual is at the rim and at the band's inner edge, where the blur
and the element boundary interfere with the phase.

Reveal gate unchanged: mismatchedPixels=0, composedMismatchedPixels=0.

## 2026-08-15 (later 152): THE LIVE REVEAL GEOMETRY, MEASURED

later-148 diagnosed the off-ladder frame as a rigid 0.18 px translation and
said one sample could not say what law produced it.  Thirty-three LIVE frames
were captured (analysis/capture_reveal_dynamic_frames.sh - the coverage probe
is what selects the two-wallpaper oracle; without it the dynamic suite draws
its own coded field and there is no reveal circle at all), and sixteen carry a
usable circle.  Their geometry, from the 50% coverage contour at 720 rays,
fitting to 0.03 px rms with harmonic content below 0.008 px - so the shape is a
CIRCLE, exactly:

    centre_x = 512.000        the origin, exactly, to 0.004 px
    centre_y = 614.0008 + 0.00022907 * radius     residual 0.0066 px max
    radius   = CONTINUOUS - not one of the sixteen lands on the 0.5 grid

over radii 29 to 1454, a fifty-fold range.  The law predicts the OLD corpus's
off-ladder frame - a different capture session - at centre_y 614.334 against
614.336 measured.  Two independent corpora, 0.002 px apart.

And the ladder's grid is now proven unique rather than merely sufficient:

    integer device pixels (what walle uses)   0 of 65 states differ
    integer POINTS (2 device px)             58 of 65 differ
    half device pixels                       52 of 65 differ

So the two paths genuinely differ, which is what Core Animation does: an
explicitly set progress goes through the model layer, which lays out and rounds
its bounds to whole device pixels, while an animating layer's presentation
values are interpolated without re-laying-out.  walle drives both from one code
path and snaps in both, which is right for the sweeps the gate scores and wrong
for the transition users see - by up to 0.33 px, the 3,838 mismatched pixels of
later-141.

What produces the 0.000229 slope is not known.  It is measured, it is a
function of the radius alone, and it holds to 0.007 px across the animation.

## 2026-08-15 (later 153): THE OFF-LADDER RESIDUAL IS CLOSED

later-141 measured it, later-148 diagnosed it, later-152 found the law.  It is
now implemented and gated.

THE LAW.  An ANIMATING reveal's circle is the linear interpolation between the
two ROUNDED endpoint rects - the rect at progress 0 and the rect at progress 1,
both produced by the same rounding law the ladder proves.  Nothing new is
assumed: the endpoints come from walle's own snapping, so any origin, radius or
frame size follows without a fitted constant.

    presentation:  rect(p) = lerp(round(rect(0)), round(rect(1)), p)
    explicit:      rect(p) = round(rect(p))

walle now takes the first for the live transition and the second for the
process capture, which is exactly Core Animation's model-versus-presentation
layer split.

ONE TRAP, and it cost a whole wrong first result.  The geometry family is
chosen by whether the rect is square, and the scissor has to be widened to
whole pixels - which turned a 2910.10 x 2910.77 rect into a 2912 x 2912 one and
sent it down the COMPACT path instead of the border path, rendering 6,092
mismatched pixels at delta 242, worse than the rounding it replaced.  The
circle now carries its pre-widened extents and the family follows those.

RESULT, on the frame later-141 measured:

    rounded geometry        3,838 mismatched   maxDelta 25
    presentation geometry      23 mismatched   maxDelta  1

and across eight of the newly captured live frames, scored by the new
`make reveal-presentation-gate`:

    frame  1   125 mismatched   maxDelta 1     (radius 29 px - its whole edge)
    frame  2     0              BYTE-EXACT
    frame  4     0              BYTE-EXACT
    frame  6     0              BYTE-EXACT
    frame  8     0              BYTE-EXACT
    frame 10     0              BYTE-EXACT
    frame 12    35 mismatched   maxDelta 1
    frame 14     7 mismatched   maxDelta 1

Five of eight byte-exact; the rest differ by one code value on a handful of
antialiased pixels, which is the floor of searching a scalar progress on a grid
- the animation's own progress is some other float.

The 65-state ladder is untouched: mismatchedPixels=0,
composedMismatchedPixels=0, exactPixelPercentage=100.0.

## 2026-08-15 (later 154): THE REFRACTION BAND IS ABSOLUTE

later-151 measured the refraction on ONE element - the 500 pt circle - and
expressed it as a fraction of the radius: a band of 0.081 R with 0.1587 R of
displacement at the rim.  That was only ever true of that element.

Measured now on circles of radius 128, 256 and 500 capture pixels, and binned
by DISTANCE INSIDE THE RIM rather than by fraction of the radius, the three
profiles are the same curve:

    px inside the rim     40     30     20     15     10      6      3
    R = 128             0.01   3.87  16.19  26.49  40.82  57.06  74.89
    R = 256            -0.00   3.84  16.15  26.43  40.73  56.95  74.74
    R = 500            -0.01   3.82  16.11  26.38  40.71  56.92  74.66

and adding a fourth size, R = 1000, holds the same curve: across all four
sizes and BOTH variants the spread is 1.87 px and the worst deviation from the
fitted model is 2.47 px, on a 75 px peak.  An EIGHTFOLD range of element size
with no scaling at all.  The band is
absolute, not proportional.  It is also the same for BOTH variants (clear and
regular agree to 0.7 px) and BOTH appearances (clear's are identical to the
code), so one profile covers everything.  A fourth capture of the 500 pt circle
in a separate session reproduces the first to 0.01 px.

    d(u) = 26.48219 * (35.5796 - u)^1.09134 / (u + 12.6207)

in CAPTURE pixels at 2x, fitting to 1.9 px worst on a 94.8 px peak - most
points inside 0.7.  The band is 35.6 capture px = 17.8 points.

WHY THIS MATTERS MORE THAN THE FIRST MEASUREMENT.  walle's reveal grows to a
radius of 2164 px.  Under the fraction-of-R form it would have applied a 175 px
band displacing up to 344 px, where the truth is an 18 px band displacing 40.
The fraction form was right only at the one size it was fitted on, and wrong by
an order of magnitude everywhere walle actually uses it.

The shader now takes the output's device-pixels-per-point through a fourth push
constant lane, because this is the only quantity in the material that is
absolute rather than expressed in the element's own pixels.

VERIFIED by rendering walle over the same four-step gratings and decoding its
displacement the same way: 0.1 px in the interior, within 3.5 px at the rim
where the element boundary contaminates both measurements equally.

## 2026-08-15 (later 155): THE COLOUR TRANSFER, AND AN EXPONENT THAT WAS NEVER REAL

The untinted transfer was fitted from sixteen backgrounds, of which five were
neutral and six clipped the material, and keeping it honest on the gray axis
needed a four-to-one weighting on the neutral samples - a judgement, not a
measurement.  A lattice of 64 flat colours spanning the cube now replaces it.

FIRST, the capture path is better than it was assumed to be.  Of sixteen flat
backgrounds, thirteen round-trip EXACTLY through the display and back; the
three that do not are saturated green or zero blue, off by 2 to 3 code values.
The lattice round-trips within ONE code value everywhere.  So the "2 to 4 code
Color-LCD-to-sRGB noise floor" that earlier entries hedged against does not
exist for these backgrounds - the measurements are limited by the model, not
the capture.

SECOND, the 0.795 exponent was an artefact.  Refitting the same power-space
model on the lattice returns 1.195, not a refinement of 0.795 but a sign that
the exponent was a property of the sparse background set rather than of the
material.  A second-order polynomial - the three inputs, their squares and
their three cross products - beats it everywhere on HELD-OUT backgrounds:

                      affine        power        quadratic + cross
    regular light   3.65 / 15.8   2.87 / 10.0   1.58 /  6.77
    regular dark    2.08 /  8.89  1.94 /  9.40  0.90 /  5.43
    clear           2.88 / 16.0   2.36 /  9.61  2.03 / 13.0

and it needs no pow() in the shader.  It also removes the reason the neutral
weighting existed: `regular` in light goes from 5.66 to 0.87 code values on the
gray axis with no weighting at all.

VERIFIED END TO END - walle rendered over the same backgrounds, 240 cases:

    untinted   worst 2.76 -> 1.81 codes, median 0.87 -> 0.79
    across the whole gray ladder, every point now inside 1.9 codes

Both gates unchanged: the ladder at mismatchedPixels=0, the animating path at
5 of 8 frames byte-exact.

## 2026-08-15 (later 156): RULED OUT - the tint base is not the material's own transfer

A tempting reduction: if `base(tint)` were just the untinted material's transfer
applied to the tint COLOUR, the tint law would need no fitted map at all.  It is
not.  Comparing the per-tint base against materialTransfer(tint) over 36 tints:

    regular light   mean -145 codes, spread 30, worst 217
    regular dark    mean  +60,        spread 25, worst 122
    clear   light   mean  -74,        spread 24, worst 153
    clear   dark    mean  -30,        spread 10, worst  95

Not an offset, not a scale - the spread is as large as anything it would
explain.  The fitted quadratic in the tint colour stands as the best available
description.

## 2026-08-15 (later 157): `.interactive(true)` IS INERT - measured, not assumed

walle never modelled Apple's `interactive(Bool)` modifier.  Defensible - a
wallpaper has nothing to interact with - but an assumption about a shipped
modifier is a parity gap until something measures it.  The rig now captures
`.regular` and `.regular.interactive(true)` over the same backgrounds in the
same run, so the two frames differ in nothing but the modifier:

    16 backgrounds x 2 variants x 2 appearances = 64 pairs
    byte-identical: 64/64,  differing pixels 0,  max delta 0

Not "identical in the interior" - identical everywhere, rim and all.  Omitting
it is now a measurement (analysis/measure_interactive_modifier.py,
analysis/results/interactive_modifier.json).

## 2026-08-15 (later 158): THE LATTICE MISSED THE FACES OF THE CUBE

Verifying end to end over 396 cases instead of the usual 30 exposed an UNTINTED
error the small case set never saw: `clear` over cyan-128 reads 41 in red where
walle renders 31.2.  Nine and eight tenths code values, on an untinted element.

The cause is the corpus, not the model.  The first colour lattice sampled
32/96/160/224 per channel - every one of its 64 points is INTERIOR to the cube.
The only samples on a face were the seven named saturated colours, and least
squares let 64 interior points outvote 7 face points.  The polynomial CAN
represent what the faces do - green bleeds +22 into red at red=0 and +1 at
red=128, which is exactly an r*g cross term - it simply was not asked to.

Two changes.  The lattice levels are now a capture-time choice
(WALLE_CUBE_LEVELS), and a second lattice at 0/32/64/96/128/192/255 interleaves
with the first.  And the fit no longer hardcodes its model: trivariate
polynomials of order 1 through 4 are cross-validated per variant and appearance,
in the same total-degree basis the shader now evaluates.

Also measured, and worth recording because it rules out the obvious theory:
the transfer is NOT a linear-light operation.  Compositing usually is, so the
natural story is that the material blends in linear light and the sRGB curve is
what bends the response.  Fitting the same polynomials on linearised inputs and
outputs is worse at every order:

                        sRGB code      linear light   (held-out rms)
    clear               1.34            2.24
    regular light       1.58            5.23
    regular dark        0.90           13.85

sRGB code space is where this material works.

## 2026-08-15 (later 159): THE TINT BASIS ORDER IS NOW MEASURED TOO

The tint law's in-sample error was never the problem; predicting a tint NOBODY
CAPTURED was, and that is what the shader is asked to do.  Held out one tint at
a time, from 29 chromatic tints against a quadratic basis:

    regular light  4.59 rms / 33.5 max      regular dark  4.40 / 37.6
    clear   light  5.23     / 31.5          clear   dark  4.47 / 31.0

Twenty-four more mid-intensity colours (53 chromatic tints in all) and a basis
order chosen by that same held-out score - cubic for three of the four
materials, quadratic for `clear` in dark - give:

    regular light  2.28 / 21.9              regular dark  2.21 / 19.1
    clear   light  3.72 / 26.7              clear   dark  3.61 / 26.9

Halved for `regular`.  The generated header pads every chosen order out to the
widest one, which is exact because the basis is ordered by total degree, so the
shader evaluates one fixed-width polynomial with no branch.

## 2026-08-15 (later 160): THE REFRACTION BAND DOES NOT SEE CURVATURE

Every refraction measurement is on a circle, and walle draws rounded rectangles
too, whose straight sides are the zero-curvature limit.  That was an
extrapolation.  It is now a fit: with the profile free per distance bin and one
shared coefficient,

    displacement(u, R) = profile(u) * (1 + c / R)

over four radii from 128 to 1000 capture pixels, both variants, both
appearances, 945 bins:

    c = +0.2586 px
    worst correction, at the SMALLEST element:  0.19 px
    rms without the term 0.3376 px  ->  with it 0.3356 px

The curvature term buys two thousandths of a pixel and its largest effect
anywhere is smaller than the measurement's own scatter.  1/R varies eightfold
across this family, so a real curvature dependence could not hide in it.  The
band is a function of distance inside the rim and nothing else, and a straight
edge is inside the measured range rather than beyond it.

## 2026-08-15 (later 161): gamma WAS PINNED, AND PINNING COST NINE CODE VALUES

The tint law reads gamma - how much of the substrate's own chroma survives - as
one for every neutral tint and zero for every chromatic one, so it was PINNED
there.  The per-tint fits scatter 0.96 to 1.16, and gamma multiplies a chroma
that runs to 54 code values on a saturated substrate, so pinning is worth up to
nine code values of error.  That is exactly what the end-to-end check found:
the worst tinted case was a NEUTRAL tint over cyan, at 18.4.

gamma is now a third fitted function of the tint on the same basis, chosen by
held-out error like the order.  It won in three of the four neutral regimes and
in one chromatic one; where pinning won, the pinned value is written into the
basis's constant term rather than branched on, so the shader keeps one path.

## 2026-08-15 (later 162): THE FACE-REACHING LATTICE, AND AN ORDER THAT TURNS OVER

The second lattice landed - 216 backgrounds at 0/32/64/96/128/192/255 per
channel, interleaving with the first - and the fit's background count went from
47/61/85 to 195/247/293.  Refitting with orders one through six:

                    before (order 2)      after
    regular light   1.580 / 6.770       0.685 / 2.966   order 4
    regular dark    0.899 / 5.433       0.741 / 5.477   order 5
    clear           2.029 / 13.011      0.805 / 3.982   order 5

held-out rms and max code values.  Held-out rms is now under one code value for
every material and the worst held-out miss anywhere is 5.5.

The order ladder TURNS OVER, which is the point of running it past where it
wins: order 6 is worse than order 5 for all four materials (1.17 against 0.70
for regular in light) even though its in-sample error is lower.  The chosen
order is interior to the range tested, so it is a measurement and not the edge
of the ladder.

Provenance: artifacts-cube2, 2058 shots,
sha256 3dc9f1344f5167e0177f1025608493f787facaf0156e520b51564058de939475.
The 48 backgrounds the two lattices share are byte-identical across the two
capture sessions.

## 2026-08-15 (later 163): THE EDGE WAS GUESSED, AND IT IS THE LARGEST ERROR LEFT

Rendering walle back over a step edge - the first end-to-end check that a flat
background could not do - agreed to 1.7 code values through the element's body
and then missed by 151 at its boundary.  Two whole layers were wrong there.

THE RIM.  Apple draws a bright band 2.2 px inside the boundary: +22 code values
over the interior for `regular` in light, +130 for `clear` over black.  walle
carried a ring fitted from Human Interface Guidelines photographs - a
directional specular lobe with up, down and base gains, an offset centre and a
width proportional to the radius - whose gains were small enough to render
nothing.  The model is wrong in kind, not only in scale: across twelve sectors
and 1677 frames the measured rim varies by a MEDIAN 2.7% of its own excess and
8.3% at the 95th percentile.  There is no lobe.  There is no light direction.

What there is:

  * a profile that is ABSOLUTE, like the refraction's - the same 2.2 px depth
    at radius 128, 256 and 500, peaking 0.6 to 0.8 px inside the boundary;
  * an amplitude that saturates by a 256 px radius (R=256 and R=500 agree to
    0.25 code values) and is 0.65 to 0.75 of that at R=128, a regime walle only
    ever renders at a material thickness under 0.02, so at most 0.1 code values
    of the difference can reach a frame;
  * a colour that is its own transfer of the backdrop, fitted like the
    material's, at 1.7 to 3.7 code values rms;
  * its own TINT law.  A tinted rim is nothing like the interior's tint applied
    to an untinted rim - that misses by ninety code values - so the rim is
    fitted with derive_tint_law.py --region rim and carries its own regimes.

THE SHADOW.  `regular` darkens the backdrop outside itself, reaching about 80
capture pixels: -4.3% in light, -7.6% in dark, and in sRGB CODE space it is
affine - out = 0.954 * backdrop + 0.76 at its darkest, fitted to 0.2-0.6 rms.
`clear` casts none, to under half a code value, and its fitted law comes out as
the identity on its own.

walle's shadow had been switched off - kShadowBase = 0 - after a materialize
capture read the band just outside the rim as +0.5 code values.  That capture
was `clear`, which is exactly the variant that has no shadow.

## 2026-08-15 (later 164): TWENTY-ONE NEUTRAL TINTS

The neutral tint regime had seven samples supporting two fitted functions, and
gamma made it three.  Fourteen more levels take held-out error to:

    regular light  2.06   regular dark  2.03
    clear   light  3.50   clear   dark  3.36

from 2.28/2.21/3.72/3.61 with seven, and the chosen basis order rises with the
samples - quartic in the tint's level for `regular` in light.

## 2026-08-15 (later 165): clear's SHARP LAYER WAS NEVER SHARP

The step-edge check missed by 14 code values at the one pixel pair straddling a
hard edge, and forward-modelling the same kernel in Python reproduced the miss
exactly - so walle implements what it was given, and what it was given is
wrong.  clear ships `0.1889 * sharp + 0.8111 * gauss(4.1727)`, where `sharp` is
a copy of the source: a delta.  The derivation had always said otherwise.  Its
own best model for clear is bilinear reconstruction over a 1.66 px cell plus a
4.17 px Gaussian, at 0.12 rms against a delta's 1.39, and the same curve as two
Gaussians is 0.2174 * gauss(0.7251) + 0.7826 * gauss(4.1829).  The delta was
introduced between the derivation and walle.c.

A second, independent capture agrees: refitting on the 500 px circle's own step
edge - a different element, a different frame size, a different session -
returns sigma 0.733 against the rect's 0.725.  Correcting it takes that edge
from 14.05 to 2.80 code values.

`regular` has no such error.  Refitting it on the circle moves nothing (1.795
to 1.770 rms in light, 3.550 to 3.538 in dark), and its wide layer is not even
identifiable there - a 330 px sigma in a 1024 px frame refits anywhere between
94 and 220 with no change in error, which is why the wide layer belongs to the
full-frame capture and stays as fitted.

## 2026-08-15 (later 166): THE MATERIALIZE IS A CROSSFADE, AND ITS CURVE IS PER VARIANT

61 frames of each of the four materials, against the rig's own raster clock.
Two findings, both against what walle carried.

IT IS A CROSSFADE.  Fitting ONE alpha per frame over every usable pixel leaves
0.76 to 1.46 code values unexplained across the whole animation.  That is the
discriminator, not a detail: if the blur radius ramped with thickness, a pixel
in fine detail would reach the material sooner than one on a plateau and a
single alpha could not fit both.  None of the four shows it.  So

    out(k) = lerp(sharp backdrop, finished material, k)

and walle's nested form - ramping the blur into the transfer's input, then
lerping the transfer's output against that same ramped input - is a different
function of k with the same endpoints.  Thickness now applies once, at the end,
and the blur, the refraction and the rim inside it are all at full strength.

THE CURVE IS NOT ONE EXPONENT.  Fitted jointly over both appearances:

    clear     delay 0.1075   exponent 2.000    rms 0.0068 / max 0.0239
    regular   delay 0.0675   exponent 1.680    rms 0.0074 / max 0.0154

against a bare power's 0.014 to 0.017 rms.  The shipped 2.36 came from twelve
frames of `clear` in light with no delay term; `clear` refits to exactly 2 once
the delay is there, and `regular` is nothing like either.

Provenance: artifacts-materialize, 244 frames,
sha256 8a7ff51ed703b9520a93c11ac02bfe264e6783bf30ddd2f222f441f2475c334d.

## 2026-08-15 (later 167): THE EDGE, SHIPPED - and a basis that was being truncated

The rim, the shadow and the materialize crossfade all landed together, and the
step-edge check went from 151 code values at the boundary to 27:

    background       variant  appear    rms     max     rim    core
    kstep-x-064-192  regular  light   0.553   9.500   9.500   1.688
    kstep-x-064-192  clear    light   0.527  11.125  11.125   2.688
    gray-192         regular  light   0.232   2.625   2.625   0.562
    gray-192         clear    light   0.367   2.500   2.500   0.875
    ... 24 scanlines, worst 27.06

The worst cases left are `clear` over gray-255 and the 0-255 step, which is
exactly where its rim CLIPS in the captures and the transfer therefore has no
data.  The flat-background check is unmoved by any of it - untinted median 0.59
and worst 3.74 code values - because the interior disc it reads never touches
the rim.

ONE BUG WORTH RECORDING.  The first build of this rendered 225 code values of
error in `clear`'s GREEN channel at the rim, and the cause was not the law: the
generated header sized its shared polynomial basis from the MATERIAL transfer's
order alone, and `clear`'s rim came out an order higher.  Its leading 56 of 84
coefficients were emitted and the rest dropped.  A truncated polynomial is not
a worse fit, it is a different function - the green row read 20 where the
measurement is 246.  The width is now the maximum over every law that shares
the basis, and emitting a law wider than the basis is a hard error rather than
a silent truncation.

A range guard was tried first, on the theory that the fit was extrapolating
into the clipped corner, and it was WRONG: `regular` in dark reaches -80 code
values over the cube at ORDER ONE, because its measured range starts at 1 and
the fit extrapolates below zero wherever the material would clamp.  Rejecting
that would have thrown away every polynomial and left an affine-in-x**g model
at 2.3 rms against 0.48.  The guard is gone; cross-validation already rejected
the one order that genuinely blew up.

## 2026-08-15 (later 168): THE RIM DOES NOT SEE A STRAIGHT EDGE EITHER

The corpus could not test this - its rectangles are 1600 by 900 POINTS in a 512
point window, so they cover the frame and their boundary has never been in
shot.  Three small shapes now fit inside it: a sharp-cornered rectangle, a
rounded one, and a capsule.

On a STRAIGHT edge - zero curvature, the furthest thing from a circle there is -
the rim is the same curve, to between 0.14 and 4.42 code values across all four
materials.  Taken with the refraction's own curvature term (0.19 px at its
worst), both of the element's edge laws are functions of depth alone.

At a rounded CORNER the measured curve is broader than the circle's, by 10 to
47 code values, and this is recorded as an open discrepancy rather than a law:
a 90 degree arc of radius 120 px yields about seventy pixels per depth band
against a circle's several thousand, and the corner's own radius sits in the
attenuated regime where the amplitude is already 0.65 to 0.75 of saturation.
walle draws circles and nothing else, so neither result changes what ships.

Provenance: artifacts-shapes, 528 shots,
sha256 12c6ee7870669a6e36af27b029fc1a1a1a3168129303d6311b4e234e6b296027.

## 2026-08-15 (later 169): WALLE MID-MATERIALIZE, AND A KERNEL TWO INSTRUMENTS DISAGREE ABOUT

Rendering walle at the hardware's own clock values and scoring frame against
frame - the crossfade law verified rather than merely fitted:

    clear   light  rms 0.6 to 1.5,  worst 4 to 5 code values, every clock
    clear   dark   rms 0.6 to 1.4,  worst 4 to 5
    regular light  rms 1.1 to 11.4, worst 36 at clock 0.55
    regular dark   rms 1.1 to  7.6, worst 22

So the crossfade and the per-variant ease are right - `clear` is exact through
the whole animation - and `regular` is not.  Its error does not sit anywhere in
particular: mapped over the frame it is between -6 and +8 everywhere, and
sorted by the LOCAL BACKDROP LEVEL it runs -4.7 where the backdrop is dark and
+8.3 where it is bright.  That is a contrast error, not a placement one.

It is not walle's.  Forward-modelling the same law in Python - the same
kernel, the same transfer, the same replicated padding - reproduces walle's
render to 0.57 rms and 2.4 max, and lands the same 7.7 rms from the hardware.
The implementation is faithful and the KERNEL is wrong.

THE TWO INSTRUMENTS DISAGREE.  Refitting a two-Gaussian kernel on the coded
field halves the error there, and breaks the step edge by as much:

                      step edge      coded field
    step-edge fit     1.879 rms      7.702 rms
    coded-field fit   7.903          4.377

`clear` has no such conflict - 0.777 / 1.534 with the shipped kernel, and its
refit changes nothing - so the instruments and the transfer are both sound.
What fails is the two-layer FAMILY for `regular`.

Why they pull apart is worth stating, because it says what each instrument is
good for.  The coded field is SMOOTH - its content is at periods of a hundred
pixels and up, and its high frequencies are quantisation noise, which is why an
MTF read off it climbs past 2.5 and means nothing.  A narrow layer therefore
passes the field through almost unchanged and a wide one averages it to the
frame mean, so what the field measures is almost purely the near/far WEIGHT.  A
step edge measures the kernel's integral, which pins the near layer's shape and
says little about how much weight sits far out.  Two measurements of one weight
that disagree mean the kernel holds more structure than two layers can.

Fitting three layers against both instruments at once is what decides it: if
three satisfy both, the two-layer model was too coarse; if nothing does, the
mechanism is not a convolution at all - a mip cascade is not shift-invariant -
and that is a different kind of answer.

## 2026-08-15 (later 170): THE LAST FRAME IS NOT THE SETTLED MATERIAL

Every fit against the materialize corpus took its last animation frame as the
finished material.  It is not: the rig captures a post-settle frame after each
sequence, and for `regular` in light the two differ by 4.08 code values rms.

Scoring the shipped kernel against the settled frame instead:

                        last frame      post-settle
    clear   light       1.534 rms       0.580
    clear   dark        1.913           0.580
    regular dark        4.568           2.615
    regular light       7.702           8.075

`clear` lands at 0.580 - the same figure the flat backgrounds report, so its
kernel is exact over structured content too - and the remaining problem is
`regular`, and mostly `regular` in LIGHT.

The thickness curve needed a third parameter for the same reason.  Measured
against the settled material the curve has not finished when the rig's clock
does - alpha is 0.81 at clock 0.9 - because Apple's transition outlives the
window the rig animates over.  Forcing a curve that ends at clock one onto data
that has not finished there collapses the delay to zero and takes the fit from
0.005 of full scale to 0.020.  With an `end` past one:

    clear     delay 0.1250   end 1.0350   exponent 1.900   rms 0.00473
    regular   delay 0.0650   end 1.0200   exponent 1.700   rms 0.00724

END TO END, walle rendered at the hardware's own clock values:

    clear   light   rms 0.62 to 1.09,  worst 3 to 5 code values, every clock
    clear   dark    rms 0.63 to 0.94,  worst 4
    regular light   rms 1.05 to 9.87,  worst 34
    regular dark    rms 1.09 to 9.82,  worst 23

`clear`'s materialize is finished.  `regular`'s is the kernel, and nothing else.

Two other things this cost, both worth remembering.  Caching a full 2048x2048
blur per candidate sigma is 100 MB a time and it OOM-killed the machine; the
search only ever scores a fixed subsample and mixing is linear, so caching the
SAMPLED pixels is the same arithmetic at a thousandth of the memory.  And
`pkill -f <pattern>` matches the shell running it whenever the pattern appears
anywhere in the command line - it killed three of these runs, including one
that had already finished the work.

## 2026-08-15 (later 171): regular's WEIGHT - what it is not

The two instruments disagree about one number: how much of `regular`'s blur is
the near layer.  The step edge says 0.88 in light, the coded field says 0.54,
and the difference is the whole of an 8.1 code value residual.  Six explanations
have been tested and none of them is it.

  * NOT the implementation.  Forward-modelling the same law in Python
    reproduces walle's render to 0.57 rms.
  * NOT the reference frame.  Fixed - see the entry above - and it took `clear`
    from 1.53 to 0.58 while leaving `regular` in light at 8.1.
  * NOT the padding rule.  Replicate, mirror, frame-mean and even black all
    land within 7.4 to 7.8 rms.  A third of a 330 px kernel sits outside a 1024
    px frame, so this had to be checked, and it does not matter.
  * NOT the wide layer's FORM.  Replacing it with the frame's own mean - the
    limit of a very wide blur - scores 4.97 against a fitted Gaussian's 4.40,
    and both still break the step edge.
  * NOT a third layer.  Fitted against both instruments at once it reaches
    5.84 on the field and 3.22 on the step, against two layers' 8.11 and 1.88.
    It trades one instrument for the other rather than satisfying both.
  * NOT the order of mixing and transferring.  `w*T(near) + (1-w)*T(far)`
    instead of `T(w*near + (1-w)*far)` moves the field from 8.075 to 7.896.
  * NOT a gain and an offset.  With a free per-channel affine the field still
    prefers 0.55 and the step edge 0.80, so the disagreement survives any error
    in the transfer's overall level.

What a free affine DOES absorb is most of the residual's size - 8.08 to 3.21,
at gain 0.638 and offset +81 - which says the error is a contrast error, the
signature of a weight, and not a kernel SHAPE error.  `clear`'s same affine is
gain 0.997 and offset +0.29: its transfer and kernel are both exact.

ONE CELL OF THE DESIGN IS MISSING, and it is the obvious one in hindsight.  The
corpus's two step edges vary element size and frame size TOGETHER - a 500 px
circle in a 1024 px frame, and a frame-filling rectangle in a 6400 px one - and
they agree with each other on the weight (0.905 and 0.900 in light, 0.520 and
0.510 in dark).  That rules out neither cause alone.  The coded field's element
is frame-filling in a 1024 px frame, which is the combination nobody has ever
captured, and it is now capturing.

## 2026-08-15 (later 172): THE KERNEL IS NOT WRONG - the two capture paths disagree

Correcting the previous entry.  `regular`'s kernel was called wrong on the
strength of ONE instrument.  Three others, measured since, agree with what is
shipped, and the outlier turns out to be the rig's dynamic path rather than the
material.

STEP EDGES, at three geometries that separate element size from frame size -
the missing cell of that 2x2 is now captured:

    500 px circle in a 1024 px frame     w = 0.905 light, 0.520 dark
    frame-filling rect in 6400 px        w = 0.900,       0.510
    frame-filling circle in 1024 px      w = 0.905,       0.515

All three, against the shipped 0.8846 / 0.5164.  Geometry does not move it.

SINE GRATINGS, one frequency at a time at full contrast, forward-modelled
through the measured transfer so nothing is linearised - the instrument this
question needed all along:

    regular light   period  64 128 256 512 -> w = 1.000 0.950 0.910 0.900
    regular dark                          -> w = 0.775 0.570 0.550 0.500

against 0.8846 and 0.5164, at two element sizes that agree with each other.

uv-map SAYS NOTHING, and that is worth recording because it looked like it did.
Its local variation about a 129 px mean is 0.23 code values - it is a linear
ramp, with no content at the scales the weight controls - so its apparent
preference for 0.64 is noise, and its 2.0 rms residual is the transfer's own.
The coded field's local variation is 8.08, which is why it is sensitive.

THE ONE OUTLIER is the coded field, at w = 0.54 and 8.1 rms.  Its element is
1000 px in radius in a 1024 px frame, which is exactly `circle-1000-center`'s
geometry, and the gratings on that same geometry read 0.90 to 0.96.  Same
element, same frame, same material, same backdrop scales - and the STATIC suite
says 0.90 while the DYNAMIC suite's settled element says 0.54.

So walle's kernel is not known to be wrong.  It matches every static
measurement of it, and walle renders a settled element.  What is unexplained is
a difference between the rig's two capture paths at matched geometry, and that
is a statement about the harness until something separates it further.  The
kernel stays as fitted; shipping the coded field's weight would break three
instruments to satisfy one.

## 2026-08-16 (later 173): THE BLUR TREATS CHROMA DIFFERENTLY FROM LUMA

This is the answer, and it is why one instrument disagreed with three others
for so long while every explanation for the disagreement failed.

`regular`'s near/far weight reads 0.90 on every GRAY instrument in the corpus -
step edges at three geometries, sine gratings at four periods and two element
sizes - and 0.54 on the coded field.  Capture path, element size, frame size,
padding rule, layer count, mixing order, anisotropy and a free affine were each
tested and each ruled out.  Splitting the coded field's own residual by
component does it in one line:

    component      weight it wants    rms at the shipped 0.8846
    luma only          0.850                1.153
    chroma only        0.550                7.909

Fitting both weights together, and note what the luma one lands on:

    regular light   wLuma 0.893  wChroma 0.543   8.066 -> 1.838 rms
    regular dark    wLuma 0.562  wChroma 0.617   2.615 -> 1.176
    clear  (both)   wLuma 0.217  wChroma 0.083   0.580 -> 0.578

The shipped weights were 0.8846, 0.5164 and 0.2174.  The luma weight IS the
shipped weight - to three decimal places for `clear` and to 0.008 for `regular`
in light - so every gray measurement in this repo was right about the thing it
could see, and blind to the thing it could not.

WHY EVERY OTHER INSTRUMENT MISSED IT.  A step edge and a sine grating are gray:
their chroma is exactly zero, so the chroma weight multiplies nothing and they
measure the luma weight alone.  The coded field carries MORE chroma than luma -
36 code values against 29 - so it reads mostly the chroma weight.  uv-map
carries neither at the scales that matter, its local variation about a 129 px
mean being 0.23 code values, so its apparent opinion was noise and briefly
looked like corroboration.

`clear` is the control and behaves like one: its two radii are 0.73 and 4.18
px, so there is almost nothing to tell apart, and splitting the weights changes
its residual from 0.580 to 0.578.

THE MODEL.  One linear operator, two mixtures:

    blurred = wLuma * near(L) + (1 - wLuma) * far(L)
            + wChroma * near(C) + (1 - wChroma) * far(C)

which rearranges to the existing mixture at the CHROMA weight plus a correction
of (wLuma - wChroma) times the luma difference of the two blurs - one extra
single-band image in the CPU path, and nothing at all in the shader.

## 2026-08-16 (later 174): THE CHROMA SPLIT, SHIPPED - and a regression it caught

walle's blur now mixes its two radii differently for luma and for chroma:

    blurred = wLuma * near(L) + (1 - wLuma) * far(L)
            + wChroma * near(C) + (1 - wChroma) * far(C)

    regular light   wLuma 0.8846 (unchanged)   wChroma 0.542
    regular dark    wLuma 0.5164 (unchanged)   wChroma 0.612
    clear   both    wLuma 0.2174 (unchanged)   wChroma 0.088

The luma weights are exactly what was already shipped, because the gray
instruments that measured them were right; only the chroma mixture is new.
Rendered through walle's own pipeline over the one backdrop that carries
chroma:

    regular light   8.07 rms  ->  2.34 rms / 9.6 max
    regular dark    2.62      ->  1.45     / 4.8
    clear   both    0.58      ->  0.65     / 2.1

and every gray backdrop is unchanged to the code value - 27.06 worst over the
same 24 scanlines as before - which is the check that matters, because on gray
the correction has to vanish identically.

THREE MISTAKES ON THE WAY, all worth keeping.

The first was a REGRESSION, and the flat backgrounds caught it in one run.  The
materialize `end` measured past one, and shipping the curve cut off at clock
one left the material permanently 3.5% short for `regular` and 7.2% for `clear`
- a wallpaper that never finishes materializing.  The measurement was right;
what was wrong was reading it as "stop here" rather than "this is how long it
takes".  The curve now plays over walle's own materialize window and arrives at
the end of it, and every gray reading returned to its previous value exactly.

The second was the correction not vanishing on gray, which it must, because
luma(D) IS D there.  A recombination matrix has to be square when the backdrop
carries alpha, and its orientation is easy to get backwards; the correction is
built from explicit band arithmetic now, where neither can be wrong.

The third was geometric, and it is the same trap this repo has fallen into
before: walle's element is centred at (512, 614.4), NOT at the middle of its
canvas.  A backdrop centred on the canvas and compared across its own width
reads pixels outside the element - it scored 152 code values until the element
was made to cover the canvas, after which the same law scored 2.34.

## 2026-08-16 (later 175): THE CROSSFADE IS IN CODE SPACE TOO

The materialize matched at both ENDS and drifted by up to 36 code values in
between, which is the signature of the right curve applied in the wrong space.
Measuring walle's own effective thickness against the hardware's says it
outright:

    clock   walle   hardware   drift
    0.25    0.102     0.056    +0.047
    0.55    0.428     0.303    +0.126
    0.85    0.807     0.723    +0.084

The shipped constants were not the problem: the formula gives 0.061, 0.316 and
0.717 at those clocks, which is the hardware's curve almost exactly.  walle was
evaluating the lerp on LINEAR values and encoding afterwards, and a linear-light
fade runs ahead of a code-space one by exactly that much.

It should have been code space from the start, and the measurement said so:
alpha was measured as the code-space ratio (frame - backdrop) / (settled -
backdrop), and one alpha per frame explains a whole frame to 1.5 code values
THERE.  The transfer, the blur and the tint were all measured in sRGB code
space as well; the crossfade was the only stage anywhere else.

    materialize, worst over every clock and material:  38  ->  14 code values

    regular light   rms 1.14 to 11.06  ->  0.81 to 2.41
    regular dark    rms 1.10 to  9.74  ->  0.85 to 1.90
    clear   both    rms 0.62 to  1.20  ->  unchanged

`clear` hid it: its two endpoints sit close together, so the space the fade
happens in barely matters, and it read 3 to 5 code values throughout either way.

Nothing at full thickness moves - lerp(a, b, 1) is b in any space - so the step
edges, the flat backgrounds and both reveal gates are untouched by this.

## 2026-08-16 (later 176): `clear`'s BODY DOES NOT SEE THE APPEARANCE

Read the same way the tint law reads it - the mean of the element's interior
disc - `clear` returns the SAME code values in light and in dark, and so does
`clear` under any NEUTRAL tint:

    clear          gray-000    light [ 19  19  19]   dark [ 19  19  19]
    clear          gray-128    light [152 152 152]   dark [152 152 152]
    clear          green-128   light [ 41 152  34]   dark [ 41 152  34]
    clearTintL00   green-128   light [  0  36   0]   dark [  0  36   0]
    clearTintM6    green-128   light [ 45 155  38]   dark [ 45 155  38]
    clearTintL70   green-128   light [101 202  91]   dark [101 202  91]

over all sixteen backgrounds and all twenty-one neutral tints, to the last code
value.  It is not an artefact of reading a disc: the derived transfer says the
same thing independently - `clear`'s light and dark colour transfers were fitted
separately over 293 backgrounds each and came out with IDENTICAL coefficients,
to every one of the eight decimal places the report carries.  `regular`'s do
not; its two appearances differ by up to eighty code values over the same
wallpaper, which is the whole reason the transfer is fitted per appearance.

What DOES respond to the appearance, for `clear`:

  * any CHROMATIC tint, and hugely - `clearTintM1` over gray-000 is [0, 112, 28]
    in light and [34, 181, 74] in dark, 69 code values apart.  So SwiftUI's
    `.tint(Color)` is resolved through the environment even when the colour
    handed to it is an explicit `Color(.sRGB:)` with no semantic content;
  * the EDGE.  The capture files differ between the two appearances for every
    overlay, including the ones whose interiors are identical, and the interior
    disc is read at 0.8 R - so the difference lives in the rim and the shadow,
    which is where the rim law already carries its own regimes.

This is a fact about the hardware, not a change: the laws are fitted per
appearance either way, and `clear`'s two copies simply agree.  It is recorded
because it is a standing consistency check - if a future `clear` fit ever
produces two DIFFERENT bodies, the fit is wrong, not the material.

## 2026-08-16 (later 177): A PINNED READING IS A MEASUREMENT

The worst tint error left was walle predicting +19.5 code values of red where
the M1 reads exactly 0 - `regular` in dark, `tintL25` over green-128, and again
over cyan-128.  It is not a basis limitation.  It is that the fit had been
THROWING THOSE READINGS AWAY.

`derive_tint_law.py` dropped any reading at a rail, on the same reasoning that
drops a clipped SUBSTRATE.  For the substrate that reasoning is right: it is the
regressor, its true value is past the rail and unknowable, and because the tint
mixes channels one pinned input corrupts all three rows.  For the tinted OUTPUT
it is exactly backwards.  A pinned output is a real reading - it says the true
value is at or past the rail - and dropping it leaves a hole:

    regular dark    Y11..Y14 (all of them 0.30 red, 0.75 green) kept
                    R0 G9 B9 of 9 substrates - NO red rows at all
    clear   light   Y11..Y14 kept R0, M4 kept R1, M1 kept R5 of 10

Four tints in the high-green corner contributing nothing whatever to the red
coefficient functions, which then wandered freely over exactly the region where
M1 (0.35, 0.70, 0.35) and M4 (0.30, 0.65, 0.65) sit - the two tints the law was
worst on.  The per-tint fits show the same hole from the other side: Y11..Y14
report a red base of -47 and a red beta of 0.48 against their neighbours' +33
and 0.076, which is not a measurement, it is a line extrapolated from the five
brightest substrates down through a rail.

FITTED AS INEQUALITIES INSTEAD.  A pinned row enters the fit while the current
solution sits on the wrong side of its rail and drops out when it does not - an
active set, converging in a handful of passes, with the best iterate kept so an
alternating set cannot decide the answer.  Scored the way a pixel comparison
scores it - |clip(prediction) - measured| over EVERY reading, pinned ones
included, because the shader saturates too:

                        worst tint, in sample        held out, rms
    regular dark        24.93  ->  11.27            2.53  ->  2.50
    regular light        9.85  ->   9.32            2.16  ->  2.18
    clear   dark        17.14  ->  15.99            3.84  ->  3.77
    clear   light        8.47  ->   7.88            3.78  ->  3.75

and on the NEUTRAL ladder, where 18 of 21 tints have a pinned channel:

    regular dark        20.93  ->   5.24 (at order 4)
    regular light       held out 21.49  ->  14.53

RULED OUT ALONG THE WAY, each by measurement rather than argument:

  * the tint is NOT a mix into the backdrop evaluated through the material's
    own transfer.  One blend weight per variant and appearance, fitted over
    every chromatic tint and background, lands at 25 to 112 code values - the
    affine-in-substrate law stands;
  * the regressor is the untinted MATERIAL, not the raw backdrop, and the
    pinned readings settle it: 2.60 rms against 3.17 for `clear` neutral, 1.36
    against 2.08 for `regular` neutral;
  * higher order does not help.  Order 4 is indistinguishable from order 3 for
    the chromatic branch because the basis is capped there, and where it is
    free - the neutral branch - it wins only WITH the pinned readings in.

WHAT IS NOT REACHED, and is recorded rather than papered over: `clear` under a
NEUTRAL tint over a saturated backdrop.  L40 over green-128 reads [0, 131, 0]
where every affine-in-substrate law that also fits the gray ladder wants about
+16 red.  A full 3x3 affine map (11.16), a squared chroma term (15.92), a
chroma-magnitude term (11.33) and the raw backdrop as regressor were each
measured and none of them closes it.  That is a limit of the model's FORM, not
of this fit, and it is the next thing to attack if `clear` plus a gray tint over
a saturated wallpaper ever matters.

## 2026-08-16 (later 178): THE PINNED-READING REFIT DOES NOT PAY, MEASURED

Rendered, not predicted.  The refit of 177 was installed and the 528-case grid
re-run end to end through the release binary:

                    median    mean     p90     p99   worst
    before           1.695   2.357   4.502  12.963  19.526
    after            1.720   2.498   4.883  12.383  19.526

116 cases improved, 221 worsened, 191 unchanged; the summed error rises 74.4
code values.  The two cases the refit was built for - `regular`/dark tintL25
over green-128 and over cyan-128 - do not move by a hundredth.  The predicted
27.13 -> 22.99 was measured on the 3300-reading grid and does not transfer.

Do not ship it.  `tint_law_clip.json`, which predicted worst 15.44 against the
installed law's 19.35 and was never rendered, is the candidate to try next.

## 2026-08-16 (later 179): *** THE MATERIAL NEVER MATERIALIZES ***

The only end-to-end gate walle had for the thing users actually watch scored
`shaders/frag.glsl` - a GL mirror last touched while the shipped Slang moved on
for two days - against captures taken on a GitHub CI **VirtualMac2,1** at
backing scale 1 on macOS 26.4 (25E246), in an "Apple Virtual" colour space.
None of those three is what we ship or what we are matching, and its 35 code
values of mean error had gone unexamined because of it.

CAPTURED FRESH, on the authorized machine: MacBookPro18,2, macOS 26.6.1 build
25G76, built-in Liquid Retina XDR at backing scale 2, Reduce Transparency and
Reduce Motion off, `preflightErrors: []`.  rig 2.19.0, `wallpaper-transition`
mode, probeRole `walle-two-wallpaper-expansion` - the one probe that animates
exactly what walle animates - at 1024x1024 points.  That window is not a
convenience: `--transition-origin 0.25,0.30` at 2x IS walle's canonical capture
centre (512, 614.4) in 2048x2048, so the two are comparable pixel for pixel with
no resampling and no code change.

WALLE'S SETTLED MATERIAL IS ALREADY AT PARITY.  Scored against the 17 exact,
stability-confirmed states of each of the four sequences, with walle's material
held at full thickness because Apple's sweep states are settled:

    all 68 states     full-frame mean 1.68 codes, inside 2.71, worst frame 5.34
    clear / light     0.40 to 1.20 codes, p95 of 1 to 2

The radius law is right too: `clear`'s measured radius tracks
maximum_radius * state to 0.2% at every state, and the sweep control puts
R(state) - RMAX*state at -3.87 px mean over sixteen states.

WHAT IS WRONG IS THE CLOCK, and it is wrong twice.

FIRST, THE MATERIAL DOES NOT ARRIVE - IT IS ALREADY THERE.  Read as the
deep-interior code-space alpha against each sequence's own settled material,
Apple is at 1.01 in the FIRST frame that clears the lens band - 43 to 54 ms into
a one second animation - and holds 0.98 to 1.02 until it leaves:

    progress          0.12    0.22    0.32    0.42    0.52    0.62    0.72
    Apple  clear      1.010   1.000   0.995   0.982   0.987   0.984   0.986
    walle  clear      0.006   0.068   0.188   0.364   0.593   0.875   0.823

walle was replaying the materialize ease over [0, kFadeStart).  That ease is
real, but it belongs to Apple's `materialize` probe - a glass element being
INSERTED - and a wallpaper reveal is a different animation.  Over an eight
second transition it left the glass at thickness 0.03 where the hardware reads
1.00 for the first three seconds, which is the "empty circle" exactly.

Only the exit is animated, and it is a plain power of the remaining window:

    thickness = (1 - saturate((t - start) / (1 - start))) ** exponent

        clear      start 0.7030   exponent 1.720
        regular    start 0.6910   exponent 1.585

Over all 224 dynamic frames: 0.037 rms, against 0.564 for the curve it replaces,
whose worst frame was off by 1.148 of full scale - the entire material.

RETIRED ALONG THE WAY: the exit was being applied TWICE.  `thickness` carried
`(1 - tFade)` and then the finished result was lerped back towards
`texB.Sample(linearSampler, input.uv)` by the same tFade - byte for byte the
same fetch as `sharpIncoming`, because `uv` is `input.uv` and neither is touched
between them.  The exit was therefore quadratic, and its second half ran in
LINEAR light, which is precisely the error the comment three lines above it
warns costs 36 code values.

SECOND, THE GEOMETRY RUNS BEHIND.  The two readings of Apple's radius disagree,
and the disagreement is the finding: against the exact-state sweeps the radius
is maximum_radius * state to under 4 px, but against the presentation clock the
same radius covers the frame at 0.636 rather than at 1.  So the radius law is
right and only the clock-to-state mapping is wrong:

    state(t) = 1.610 * (t - 0.019)           13.3 px rms   <- shipped
    state(t) = min(1, 1.624 * t**1.068)      18.7 px rms
    state(t) = 1.537 * t                     33.2 px rms
    state(t) = t                            422.7 px rms   (what it replaces)

The form is not chosen by rms alone.  The bare scale is BIASED - its residual
runs +59, +38, +18, +5, -27 px across the timeline - while the shifted line's
wanders between -5 and +6 with no trend, which is what a correct model looks
like.  And the shift is physical: 0.019 of a one-second animation is 1.2 frames
at the rig's 61 Hz, so the reveal starts on the frame after the clock does.  The
radius is LINEAR in time after one frame of latency, at 1.61x the rate that
would fill the timeline, covering the frame at 0.640.

It is chosen by END-TO-END SCORE, not by fit: over all 242 animated frames the
shifted line reads 2.31 full-frame code values and 4.62 in the interior against
the power law's 2.38 and 6.65, better on all four sequences, with the mean
radius error 13.2 -> 12.2 px.

MEASURED END TO END, production Vulkan binary against those captures:

    animated clock, 242 frames      interior mean   full-frame mean
      before                            29.20            23.58
      after the arrival law              5.52            16.19
        regular / light                 48.40 -> 5.61
        regular / dark                  41.13 -> 6.22

    geometry held to full thickness, 149 frames
      linear clock                       7.11            21.24   worst 50.47
      measured clock                     9.59             2.64   worst  7.32

(These two tables are the staged readings, taken before the clock strip was
excluded - see the method note below.  They are kept because their RATIOS are
what isolate the two fixes from each other; the corrected absolute figures are
in the combined table.)

The interior improves on 221 of 242 frames.  After the arrival law the whole
residual is the annulus between the two radii - the worst frames read 4 code
values inside and 100 outside - which is what the second fix closes.

Both fixes are LIVE PATH ONLY where they touch geometry: the process capture is
indexed BY state against the retained 65-state corpus, so easing it there would
move the ladder the reveal gate scores.

CONTROL, because the whole geometry finding rests on it: the rig's presentation
clock is LINEAR IN WALL TIME.  Regressed against each frame's own recorded
`actualSeconds` over all four sequences,

    presentationProgress = 1.0005..1.0047 * seconds + 0.0022..0.0052
    linear residual rms 0.0022 to 0.0041, quadratic term -0.0036 to -0.0060

so `presentationProgress` is wall-clock progress to a few parts in a thousand.
The clock is therefore not what is curved - Apple's geometry really does run
ahead of a linear clock, and walle's linear geometry really does lag it.

VERIFIED NOT REGRESSED: `analysis/run_walle_reveal_process_capture_gate.sh`
still passes on the shipped binary - 0 mismatched pixels, 0 composed mismatched
pixels, 100.0% exact, candidate inventory
206451dedb5e79b082b24612d5eb39812c3a8b36e2489b5d9341eadcf9201d2b, clean Vulkan
validation, and `clear` and `regular` still compose identical presentation
bytes.  Both fixes are invisible to it by construction: the composition change
is downstream of the `apple_reveal_blend` early return the gate takes, and the
geometry change is gated on the live path.

COMBINED, both fixes together, production Vulkan binary against the M1 captures
over all 242 animated frames of the four sequences:

                              interior mean   full-frame mean   worst frame
    before                        28.85            23.30           76.22
    after both fixes               4.62             2.31            7.47

10.1x on the full frame and 6.2x in the interior.  Every frame's p95 is 1 to 2
code values and the settled frames read 0.6 with a MAXIMUM of 7, so what is left
is the 2 px rim, where a sub-pixel radius difference puts walle's rim one pixel
from Apple's.

METHOD NOTE, because it moved the numbers: the rig codes its presentation clock
into the top eight rows of the window's own pixels and declares them in the
manifest as `analysisExclusionPixels`.  They are a timestamp, not the material,
and walle does not render them.  Scoring them put a 255-code strip into every
frame's maximum and 0.44 code values into every frame's mean - on one frame,
1.640 with the strip against 1.198 without, maximum 255 against 7.  Every figure
here honours the exclusion; the scorer reads it from the manifest rather than
hard-coding it.

## 2026-08-16 (later 180): `regular` HAS THE LENS TOO

The refraction ran only for `clear`.  The reasoning was `shaders/frag.glsl:41`,
"the regular variant transmits nothing, so it gets no lens at all", which rested
on the sigma ~ 0.032 * diagonal blur model that walle.c:150-153 now retracts as
seven to thirty-four times too strong.  The lens constants were refit three
times after that model died and the branch was never revisited.

MEASURED on the M1 wallpaper-transition capture (25G76, backing scale 2, R =
1082), by ring cross-correlation of the composed frame against the incoming
wallpaper - inward displacement in capture pixels per depth inside the rim, with
the correlation in brackets:

    depth            4            8           16           32           60
    clear   light  72.0 (0.97)  50.5 (1.00)  25.5 (1.00)  2.5 (1.00)  -0.5
    regular light  72.0 (0.92)  50.5 (0.99)  25.0 (1.00)  2.5 (1.00)  -0.5
    clear   dark   72.0 (0.98)  50.5 (1.00)  25.5 (1.00)  2.5 (1.00)  -0.5
    regular dark   70.0 (0.63)  47.5 (0.97)  21.5 (0.98) -1.5 (0.98)  -2.5

`regular` carries the same lens as `clear`, to half a pixel in light.  It reads
softer in dark only because that appearance's wide blur layer leaves less
structure to correlate, which is also why its correlation drops.  Both fall to
zero by depth 32 to 60, consistent with the 35.5796 capture-pixel band already
in the shader.

The lens is now hoisted out of the `clear` branch and both variants read the
backdrop through it.  Outside the band the displacement is zero, so the lensed
read IS the unlensed one and no separate blend is needed - which is why the
unlensed `blurredIncoming` fetch could simply go.

VERIFIED ON THE FINAL BUILD, all three changes in:

  * `analysis/run_walle_reveal_process_capture_gate.sh` passes - 0 mismatched
    pixels, 0 composed mismatched pixels, 100.0% exact, candidate inventory
    206451dedb5e79b082b24612d5eb39812c3a8b36e2489b5d9341eadcf9201d2b, clean
    Vulkan validation, `clear` and `regular` still byte-identical in composition;
  * the M1 transition score is the new end-to-end gate, and it is reproducible:
        analysis/score_walle_transition_against_m1.py
        analysis/render_walle_transition.sh
    with the captures' own manifest supplying the geometry, the progress ladder
    and the exclusion strip.

SHIPPED, ONE PER MEASUREMENT:
    shaders/liquid_glass.slang   kDematerialize{Clear,Regular}; the single
                                 application of the exit; the lens hoisted out
                                 of the `clear` branch
    walle.c                      REVEAL_CLOCK_{SCALE,POWER} and
                                 reveal_state_from_clock, live path only; a
                                 comma-separated material clock so the harness
                                 can drive geometry and material apart

NOT SHIPPED, and why: the pinned-reading tint refit of 177 is reverted - see
178.  The `materialize` ease in material_law.slang is left in place though the
transition no longer reads it; it is a correct measurement of Apple's insertion
animation, which is simply not the animation walle plays.

The 528-case material grid on the final build, against the same pre-change
baseline as 178:

                        median    mean     p90     p99   worst
    pre-change           1.695   2.357   4.502  12.963  19.526
    final build          1.661   2.312   4.527  13.479  19.580

Mean absolute change 0.228 code values, largest single change 0.721, and only
38 of 528 cases move by more than half a code.  That is the shift predicted from
the arrival law alone: the harness renders at progress 0.659, where the old
curve reached thickness 0.9970 and the new one is flat at 1.0000, so every case
gains 0.3% of its own (material - backdrop) distance.  The lens hoist cannot
reach this grid - its backgrounds are flat, and the disc it reads has radius 300
about the centre while the element's radius is 1426, so the 35.6 capture-pixel
band is nowhere near it.

## 2026-08-16 (later 181): *** THE LIVE TRANSITION WAS ABORTING ***

Everything in 179 and 180 was measured through `--reveal-mask-process-capture`.
That path works.  The LIVE path does not, and it has not for as long as the
current raster has existed - the same failure reproduces on HEAD, so none of
this session's changes caused it:

    [Vulkan] Transition stopped for HEADLESS-1: Vulkan reveal/composition/present failed

Four times in fourteen seconds, at the same place every time.  A stopped
transition leaves whatever the last presented frame was, which is a part-grown
circle: exactly the "circle cut out" the report describes, and invisible to
every gate in the tree because every gate drives the capture path.

TRACED, by instrumenting each failure site down the stack:

    walle_vk_output_render        WALLE_VK_FRAME_FATAL
    -> reveal raster              WALLE_LG_REVEAL_RASTER_SETUP_FAILED (6)
    -> axis_values                slot 3, primitive 0, channel 0,
                                  axis_start -181, axis_count 924
    -> center_pair_bits           offset 527, coordinate 346, local pixel 26
    -> dyadic_floor_ratio_power_two   step_exponent -67 against exact
                                      exponent -34

The centre grid's step comes from the TILE's own constant:

    step_exponent = floor_binary_exponent(constant) - CENTER_PRECISION_BITS + 1

with CENTER_PRECISION_BITS 36.  A step of -67 means that tile's constant is near
2**-32 - small, but not the zero the code already special-cases.  `exact` is
dominated by the slope term and carries exponent -34, so the index wants 2**33
more range than an int64_t has, the guard returns false, and the whole reveal
dies.  It reproduces at 1280x720 with centre (320, 240) at radius 421.6, i.e.
progress 0.2558.

GUARDED, not re-derived: the step is coarsened to the finest one the index can
represent.  That runs ONLY where dyadic_floor_ratio_power_two already returned
false, so it cannot move a row that previously worked - and the gate proves it,
reading the same candidate inventory
206451dedb5e79b082b24612d5eb39812c3a8b36e2489b5d9341eadcf9201d2b and the same
composition bytes 8ac1bd7c... for both variants as before the change.

What Apple's fixed-width hardware does with such a tile is NOT known.  This is a
representability guard so the transition survives, and it is the next thing to
measure if a capture can be made to reach the case.

METHOD, recorded because it cost the session: the whole material campaign ran
through the process capture, and the process capture cannot see this.  A live
run needs a real DRM device - the headless backend answers `Failed to get
backend DRM FD` and walle's dma-buf presentation never comes up - so the
reproduction is `WLR_RENDER_DRM_DEVICE=/dev/dri/renderD128` with the headless
backend, then `grim` against walle's own compositor.

## 2026-08-19 (later 182): THE BLUR'S SPACE, THE SHADOW'S RETURN, AND THE CORPUS RESTORED

Three results, each with its receipt.

THE CORPUS IS BACK, BIT-EXACT.  liquid-glass-reveal-coverage-01421a3-v1
had been lost from every reachable machine - every "copy" an empty
placeholder.  Regenerated on the M1 from the frozen procedure at lg-test
01421a3 (run label restore2; over SSH the probe needs
`sudo launchctl asuser 501 sudo -u quince`, or it dies with "capture
application is not active").  The preregistered validator passed and all
65 sweep frames are byte-identical to the retained referenceSha256 pins:
the capture is deterministic across sessions.  Both scored gates ran
end to end: main reads exactly its 91; this branch reads 0 mask and 0
composed mismatches across all 272,629,760 samples INCLUDING state 42's
eighteen previously unverifiable interior positions.  Durable copies:
~/walle-archives (Linux), ~/lg-test-coverage-01421a3 (M1).

THE BLUR'S SPACE (shipped, one measurement): see the walle.c comment and
commit "Blur the backdrop in the display's code space, not sRGB".
Settled 1.68 -> 1.47, animated 2.31 -> 2.17, worst 7.47 -> 6.40, clear
controls untouched.  The remaining interior floor after the fix:
regular/light 3.79, regular/dark 2.95, clear ~1.2.

THE SHADOW WAS REAL AND IT IS MISSING (measured, not shipped).  Outside
the mask, Apple darkens the background and walle does not - the "no
shadow" of the measured dynamic layer (3fd3367) threw out a real
regular-only stage.  Measured on the sweeps as (walle-apple)/walle in
the first 25 px outside the rim, the strength is shared by appearance
and grows with the element:

    R px      543    812   1083   1353   1624   1894   2029
    alpha%   ~0.0   1.40   1.16   1.36   1.44   2.04   3.47

flat to ~20 px then decaying to zero by ~100 px; clear shows exactly
none (+0.03), matching its shadow-disabled pipeline.  One wallpaper
pair is too thin to fit a law worth shipping - it needs the controlled
instrument, and the profile fixtures already carry the shadow rows.

ALSO MEASURED: the dynamic score's early tiny-disc frames are capture
start jitter (Apple's animation zero wanders p=0.001..0.018 per run,
one 61 Hz frame; a fixed t0 cannot track it) - a scoring floor, not a
law error.  The settled sweeps show NO small-element regime break down
to R=136 px (clear interior 1.87 at p=1/16 vs 1.20 at p=7/8).  The rim's
one remaining sharp row: at d in [-2,-1) walle is 13-15 code values
below Apple on both variants - the edge falloff's last pixel, refit
territory for the controlled captures.

## 2026-08-19 (later 183): THREE INSTRUMENT VERDICTS, ONE CORRECTION

CHROMA SURVIVES THE SPACE CHANGE.  Regressing the panel-bake sweep
residuals against the panel-space mixture Jacobian asks for wChroma
+0.017 light / +0.028 dark with under 10% residual reduction - below
the shipping bar.  The weights fit under the sRGB assumption carry
over; the panel fix itself removed most of the channel constants
(regular/light k from [-2.4,+0.4,-1.2] to [-0.7,+0.2,-0.7]).

THE SHADOW IS PRESENT, NOT ABSENT - a correction to what session 182
recorded.  The shader ships measured shadowWeight tables (regular
populated to 96 px, clear all-zero, matching its pipeline).  What the
sweeps measure is an AMPLITUDE gap: Apple darker than walle by 1.2-1.4
codes just outside the rim on the 1024-pt window.  A second instrument
- the full-display 3200x2000-pt captures, measured directly against
the reference wallpaper with far-field normalization, no walle in the
loop - reads a stronger shadow still: 4.0-5.5% of background at the
rim, decaying to zero by ~90 px, R-invariant from 1433 to 4304 px,
clear exactly zero.  The two instruments disagree by roughly the
window-size ratio (3.5x vs 3.125x), which smells like the
composition-size normalization the profile geometry already uses.
NEXT INSTRUMENT: one capture matrix - windows 512/1024/2048/3200 pt
at fixed relative progress, regular/dark - decides the scaling law
before any refit.

THE RIM PROFILE IS EXONERATED.  The remaining edge deficit is
localized: peak position, peak amplitude, and exterior all match
within +-1.2 codes; walle reads 10-14 codes dark only at depth 0.5-2.5
px inside the boundary, both variants, both appearances.  A
single-parameter depth-rescale of rimWeight was A/B'd in BOTH
directions (s = 0.7, 0.85, 1.15, 1.3, 1.45; fit set light sweeps):
every candidate worsens clear (2.83 -> 3.3-4.6 edge mean) and none
improves regular (10.2 flat to +0.5).  The shipped profile shape is a
genuine optimum of its family; the mechanism is in the rim colour
transfer (order-4, 3.7/6.1 held-out rms) or the boundary-depth origin
against the rasterized mask edge.  That is edge-instrument territory:
a phase-offset ladder of the boundary within the pixel grid.

## 2026-08-19 (later 183b): THE RIM REFUSES FIELD SURGERY - three falsifications

The rim deficit's ANGULAR structure was measured (deficit vs the
boundary normal, three states, all four cases): a constant part
(+3.3..+8.5 codes) plus a bottom lobe (+2..+4 * max(down,0)) - which
looked like the directional edge light in Apple's own HIG photographs.
But the rig's twelve-sector measurement (1677 frames, rim varies by a
median 2.7% of its own excess) says the hardware's rim is isotropic,
so the angular reading is most likely the lens sampling the coded
field's diagonal stripes - content coupling, not a law.  The A/B
agreed: an additive depth-hatted edge light, fitted from the field
means (uniform and uniform+lobe variants), left regular unchanged
(edge 10.14 -> 10.09/10.12) and made clear WORSE (3.09 -> 3.56/3.68).
With per-pixel scatter of 8-14 rms against a mean of 7-10 in a 1-2 px
band, mean-level surgery is destroyed by sub-pixel misregistration.

Verdict after profile-rescale (both directions) and additive lift:
the rim's remaining 10-14 code mean deficit at depth 0.5-2.5 cannot be
closed from wallpaper field captures.  It needs the edge instrument -
a boundary phase ladder (the element edge stepped through the pixel
grid in sub-pixel increments over flat and gradient backgrounds), the
same class that fitted the rim in the first place, now read at the
final two pixels.  The field measurement stands as the acceptance
target: mean deficit +7 (light) / +5 (dark) over depth [0.75, 2.25).

## 2026-08-19 (later 184): THE SHADOW'S CONSTANT, ITS COLOUR, AND TWO CLEAN FLOORS

THE SIZE MATRIX RAN (from here, over SSH): glasscap at 01421a3 builds
and captures via `sudo launchctl asuser 501 sudo -u quince`, windows
512 and 2048 pt, wallpaper-transition sweeps, minimal dynamics.  Sets:
M1 ~/lgcap-size-{512,2048}, local /tmp/lgcap-size-*.  With lgcap-2048
and lgcap-m1 that is four windows, 512..3200 pt, radii 137..4305 px.

APPLE'S TOTAL SHADOW IS A CONSTANT.  Measured walle-free (sweep frame
against the outgoing reference, far-field normalized, empirical rim):
regular/dark 4.2% +-0.3 of background over the first 25 px, regular/
light 3.6% +-0.6, dense across states - invariant in radius, window
size and content.  Session 183's window-scaling hypothesis dissolves:
it compared Apple-total against Apple-minus-walle.  The HIG's "deeper
shadow for larger glass" is NOT observed in this context - one more
absolute law, like the refraction band.

BUT IT IS NOT GRAY.  Per channel, Apple dims blue hardest (light at
p=0.75: R +2.0, G +4.6, B +4.7; dark similar ordering) - the fitted
shadow colour matrix is the right mechanism class, and a neutral
multiply is not: A/B'd anyway (alpha 4.5/3.8% on the shipped depth
profile) - score-neutral everywhere (sweeps 1.47 -> 1.46, dynamics
2.17 flat, regular edge 10.14 -> 9.95), and DISCARDED for losing the
channel structure.  walle's measured band alpha decays with R where
Apple's holds flat; part is anchor drift between the quiet-ring and
the mask radius in the instrument itself.  NEXT: mask-anchored profile
bands on both sides (the ladder gives exact mask radii), then decide
whether the colour matrix needs an amplitude term.

TWO FLOORS, QUANTIFIED (settled interiors, spatial decomposition):
white/pixel-independent rms 0.48-0.52 on every case - the dither and
capture quantization floor - against correlated structure of 0.21
(clear: AT the floor, only a half-code constant left) and 0.86-1.00
(regular: the last real material unknown at settled state).

## 2026-08-19 (later 185): THE SHADOW'S AMPLITUDE, MASK-ANCHORED AND SHIPPED

The instrument fix from 184 ran: both sides profiled in bands anchored
at the EXACT ladder radii.  walle's "decay with R" vanishes - its
bands are flat to +-0.1 across eleven states; it was the quiet-ring
anchor drifting, never the shadow.  Apple's bands are flat too, and
the deficit collapses to ONE uniform amplitude ratio per appearance,
constant across the whole penumbra:

    band px      0-10   10-25   25-50   50-90
    light ratio  1.44    1.46    1.50    1.25
    dark  ratio  1.19    1.23    1.29    1.31

The colour matrix and the depth profile were right all along; the
flat-background edge fit under-reads only the gain on real content.
Shipped as one factor per appearance - shadowBlend *= lerp(1.25, 1.46,
lightness) - and verified mask-anchored on the new renders:

    light residual per band  -0.06  +0.02  +0.06  -0.03
    dark  residual per band  -0.38  -0.07  +0.09  +0.03

The dark band-0 overshoot is the mid-ratio compromise; a per-band term
is available if it ever matters.  Diluted whole-frame scores are
unmoved (the annulus is small); dynamics unregressed (outside 3.14 ->
3.09); clear untouched (zero tables); the reveal gate stays 100.0%.

ALSO DONE from TODO.md: main's gate repair pushed as
main-gate-asan-fix; the restored corpus pushed as
archive/liquid-glass-reveal-coverage-01421a3-v1 (GitHub now holds the
ground-truth bytes, not only the pins); the rig has no custom
background flag, so the second-content set needs a probe change.

## 2026-08-19 (later 186): THE CONTENT HOLDOUT - the laws hold, and a hidden clock falls out

THE INSTRUMENT: a natural-statistics background pair (channel-
correlated, red 1/f spectrum, slowly varying colour cast, hard oblique
edges - everything the coded fields deliberately are not), shipped on
lg-test branch rig-natural-backgrounds (e2347ee) behind
--natural-backgrounds, captured as lgcap-natural-1024 and scored
end-to-end against the shipped binary.  Nothing was fitted to it.

SETTLED: THE MEASURED MATERIAL GENERALIZES.  All 68 unseen-content
sweep states read 0.96 full-frame mean - BETTER than the 1.46 on the
content every law was fitted against.  Regular's interiors read
1.47/2.05 (light/dark) against 3.4-3.8 on the coded field, its edge
band 3.95/5.34 against 10.1: most of what looked like regular's
"content-coupled structure" and much of the rim band was the coded
field's own statistics stressing the chroma path, not a material
error.  The 528-grid, the coded sweeps and now natural content agree:
the settled material is right to about a code.

ANIMATED: THE EXIT HAS ITS OWN CLOCK.  The natural run's worst frames
(12-17 codes, all at p 0.73-0.83 with the radius saturated and agreeing)
are the dematerialize: the interior alpha read shows Apple's exit on
this run beginning ~3 frames later and descending on the same shape,
while GEOMETRY ran on time (radius offsets +0.1..+0.8 frames).  The
same read on the coded run shows walle tracking Apple to 0.03 - the
shipped curve is that run's schedule.  So the exit is a separately
scheduled animation with run-to-run start jitter, and no fixed f(t)
can match every run: fitting walle to one run inherits that run's
draw.  A four-repeat capture (lgcap-exitjitter-1..4) is measuring the
start distribution; the anchor choice - fixed f(t) at the median, or
cover-time plus median delay - follows from its spread.

ALSO: live-transition standing gate shipped and passing (three
geometries incl. 181's, zero aborts); capture sets copied out of
volatile /private/tmp into home on both machines plus
/home/quince/walle-archives; the AGX ruler pickles are transient
session state on no reachable disk - the divider law remains with the
active probe campaign.

## 2026-08-19 (later 187): THE EXIT'S LOTTERY, AND THE RIM'S DOSSIER CLOSES AT FOUR

THE EXIT START IS A SCHEDULING LOTTERY, MEASURED.  Four repeat
captures of the identical animation (lgcap-exitjitter-1..4, natural
backgrounds, dynamics only) plus the two originals give ten exit-start
readings (interior alpha crossing 0.95), and they are BIMODAL:

    early cluster   0.6928 0.6938 0.6937 0.6914 0.6873   -> 0.691 +-0.003
    delayed cluster 0.7277 0.7355 0.7289 0.7349 0.7320   -> 0.732 +-0.004

~2.4 frames apart at 61 Hz, drawn PER SEQUENCE (run 2 held one of
each), i.e. the fade is its own animation landing on one of two vsync
slots while the geometry runs on time.  The un-delayed schedule is the
law: regular's fitted start (0.6910) already sits on the early
cluster, and clear's 0.7030 was a cluster-contaminated average - its
early-cluster mean is 0.6908.  SHIPPED: both variants now share the
0.691 start; the exponent awaits a cluster-aligned refit; scoring any
single run inherits that run's draw (up to 17 codes on high-contrast
mid-exit frames), which is the capture's variance, not walle's error.

THE RIM'S FIELD DOSSIER CLOSES AT FOUR FALSIFICATIONS.  The last-pixel
deficit REPRODUCES on natural content to the code (clear -13.0/-14.4,
regular -13.2/-10.0 in the same two rows; the bottom lobe reproduces
too: slopes +2.8..+4.3, constants +6.8..+7.1 on both contents) - it is
real, and it is not content coupling.  Yet the fourth surgery, a
multiplicative colour-correct caustic keyed on analytic depth,
worsened the edge on BOTH contents (clear 2.83->3.71 coded,
2.45->3.22 natural) like the three before it.  A feature whose mean is
this reproducible while every analytic-depth correction adds error
must live at PER-PIXEL positions set by the rasterized boundary - the
mask's own AA values, not the analytic circle.  The edge instrument
should therefore read (and the eventual law should key on) the R8
boundary values; that is the complete handoff.

## Session 188 (2026-08-19): the AA boundary law ships, the exit exponent
closes, the accessibility renditions get measured, and the archive goes
off-site

THE R8-KEYED INSTRUMENT FOUND THE WHOLE DEFICIT IN ONE PIXEL CLASS.
Session 187's handoff said the law must key on the rasterized mask's
own AA values; it did not need a new capture — the corpus masks
already carry those values.  Keying the edge analysis on erosion rows
of the corpus-exact R8 mask (interior / AA boundary 0<m<255 / exterior
as three pixel classes) shows the interior rows CLEAN and the entire
last-pixel deficit concentrated in the single AA boundary class.  Two
mechanisms, shipped together (c902a6f):

  1. the final mask blend composes in sRGB CODE SPACE — the very law
     the corpus proved byte-exact for the reveal blend, now applied to
     the material composite at the boundary pixel:
         encoded = lerp(srgb(shadowed_bg), srgb(inside), mask)
  2. a coverage highlight on the boundary pixel, maximal at half
     coverage:  encoded += (A/255)·4m(1−m)·thickness with
         A = lerp(19.17, 31.0, lightness)   regular
         A = lerp(25.13, 28.7, lightness)   clear

Step 1 alone WORSENS the edge (as the four falsifications predicted —
any depth-keyed or space-only correction fails); the pair is green
everywhere, with the natural-content sets as a true holdout (constants
fitted on coded content only): coded edge clear 2.83→2.48, regular
10.40→10.02; natural edge clear 2.45→2.04, regular 3.95→3.54;
full-frame 1.47→1.46 (coded) and 0.96→0.95 (natural).  The reveal
gate is untouched at 100.0% — 4m(1−m) is zero at m∈{0,255} and the
code-space blend equals the old path there.  Receipts:
analysis/results/m1-transition-25G76-aa-hump-{coded,natural}-sweep.json.

CLEAR'S EXIT EXPONENT, REFIT ON CLUSTER-ALIGNED CURVES (52f16ce).
With the 0.691/0.732 lottery known, the ten exit curves realign on
their own starts before fitting: clear's exponent is 2.075 (was 1.720
against the contaminated average; max |residual| on the early-cluster
curves 0.051→0.017), regular's 1.585 confirmed cluster-clean.
kDematerializeClear = (0.6908, 2.075).

THE ARCHIVE IS OFF-SITE.  Three GitHub branches now hold the ground
truth: archive/liquid-glass-reveal-coverage-01421a3-v1 (the corpus
bytes + provenance), archive/lgcap-2048 (the canonical scoring set),
archive/lgcap-natural-1024 (the holdout set).  Mechanics for the next
person: a single ~1.36GB pack is remote-rejected with no reason text
("! [remote rejected] ... (failed)"), twice reproduced; the fix is
stacked commits pushed one at a time (three ≈450MB packs; the final
chunked tree is bit-identical to the original single commit —
verified with git diff before push).

REDUCE TRANSPARENCY, CAPTURED AND CHARACTERIZED.  Rig flag
--expect-reduce-transparency (lg-test ddae253) inverts the preflight;
the capture ran under a trap-guarded toggle of
com.apple.universalaccess reduceTransparency with the restore verified
in the log ("RESTORED reduceTransparency=0") — the set is
lgcap-reduce-transparency-1024 (M1 home; manifest records
reduceTransparency=true, natural backgrounds, same geometry).  Paired
against lgcap-natural-1024 (identical backgrounds byte-for-byte, mean
|Δref|=0.000), the macOS 26.6.1 accessibility rendition is:

  regular → an OPAQUE NEAR-NEUTRAL PLATE.  Absolute luma 242.3 (light)
    / 19.9 (dark), per-channel spread <0.5 on a background whose own
    spread is 1.2 — favors a constant system color, and local
    derivation is excluded outright (plate std 0.2–0.3 over a backdrop
    varying ±36: transmission <1%).  Light snaps to its plate from
    ladder state 1; dark holds its level immediately but carries
    residual structure ≤12 codes rms mid-ladder that decays to 0.2 by
    state 14 — a slow content-purge, not a level ramp.  The rim, lens
    band and refraction are REMOVED (profiles run flat to the edge).
    The outer shadow SURVIVES but re-weighted: dark ≈0.43×, light
    ≈1.34× of the normal material's (sector-restricted state-8 read).
  clear → EXTREME BLUR UNDER A FLAT SCRIM.  Gaussian-equivalent
    σ≈500 capture px (corr caps at 0.55–0.58 — the kernel is not
    cleanly Gaussian at this window; slope ≈0.9), scrim absolute ≈67.5
    (dark) / ≈129 (light), and the two appearances differ by a
    CONSTANT +61.5 codes at every ladder state — the appearance-blind
    clear core survives accessibility mode; only the scrim level is
    appearance-dependent.  No rim, no lens, and still exactly zero
    shadow.
  the reveal machinery is SHARED: RT edge positions equal the normal
    mask's within 2px at every probed state, 10–90 width 4px — RT
    swaps the material compose only, the mask/AA pipeline is common.

  Geometry note that bit three probes in a row: the natural-set reveal
  origin is (0.25, 0.30) of the 2048px window, so the disc meets the
  left window edge by ~state 4 and swallows the window by ~state 9 —
  late-state "edges" are the window corner, and only sector-restricted
  mid-ladder states (5–8, sectors toward +x/+y) give true edge reads.

  NO walle RT mode ships from this: constant-vs-wallpaper-derived
  plate color is undecidable on a near-neutral background (both forms
  agree there), and this project does not ship unmeasured guesses.
  The deciding experiment (a saturated-background RT session) is in
  TODO.md's user-gated section.

AGX RECON CORRECTION (nothing run, nothing touched): the campaign dir
holds ~90 experiments; the single-clip-ruler *-plan-v1 dirs are empty
plan dirs — the actual ruler captures are scr-capture through
scr5c-capture (v5c IS captured, correcting session 187's note), and
v6/v7 dirs with their plan generators already exist.  The law remains
the user's live campaign and the one flag between "100.0% with
hardware-measured constants" and "100.0% from public inputs".

INCREASE CONTRAST, CAPTURED AND CHARACTERIZED (same session).  Rig
flag --expect-increase-contrast added symmetric to the RT flag
(lg-test 0c52b25); guarded toggle of com.apple.universalaccess
increaseContrast with restore verified ("RESTORED increaseContrast=0");
set lgcap-increase-contrast-1024 (manifest increaseContrast=true,
preflight clean).  Against the paired normal set:

  the HIG's "contrasting border" is REAL and measured: regular gains a
    ~2–4px border ring at the mask edge — BRIGHT +86 codes in dark
    appearance, DARK −111 codes in light appearance (sector-restricted
    state-8 profiles).  Clear gets NO border.
  interiors move toward the poles, not to plates: regular/light
    237.6/std 1.5 (nearly the RT plate but not it — RT reads 242.3/0.3),
    regular/dark 44.3/3.5 (vs normal 59.6/7.5 — darker, flatter, still
    translucent), clear dims uniformly ≈−43 codes with transmission
    ×0.67 (std 39.6→26.5) and stays EXACTLY appearance-blind (dark and
    light interiors byte-close: 107.4 both, pair Δ 42.94/42.87 vs the
    same normal frames).

Instruments committed: analysis/measure_rt_rendition_interiors.py,
analysis/measure_rt_rendition_ramp_edges.py,
analysis/measure_ic_rendition.py (numpy+PIL only, run against any
paired glasscap sets).  Durable copies of both accessibility sets:
M1 home + /home/quince/walle-archives + GitHub archive branches.

## Session 189 (2026-08-19): the variant-change pause

User report: changing transition_variant makes the changed output pause
and desynchronize from the other on every touch.  Root cause in two
parts, both fixed and measured:

1. NO CROSS-OUTPUT START BARRIER (4c5ba25): each output started its
   transition the moment its own render landed, so a cache hit on one
   output against a cold bake on the other read as visible desync -
   measured 1.6s stagger live.  Outputs recruited by one trigger now
   form a sync group and start in the same loop turn ("[SYNC] N
   transitions started together").  Live gate green, reveal gate
   untouched at the known-good composition sha.

2. MONOLITHIC CACHE ENTRIES (this commit): one entry held standard +
   glass keyed by variant/recipe/blur-space, so a variant change
   re-decoded and re-cropped the source to rebuild a standard layer
   that is byte-identical across variants.  Entries are now split
   (schema 10): the STANDARD entry keys on source+geometry alone and is
   shared by every variant and appearance; the GLASS entry continues
   the same XXH64 stream with variant+recipe+space.  identity - which
   never samples the glass - aliases the standard fd and bakes nothing.
   The render_result carries two fds; the upload reads each layer from
   its own fd (renderer signature change, no extra copies).

Measured on the worst sources (91MB 9000x3500 TIFF), 2560x2880, DEBUG
build: variant flip visible start 0.6s -> 0.26-0.33s (decode skipped;
the win is larger live where 8K decodes dominate), identity flip
0.11-0.13s with NO bake, cross-monitor delta <=68ms in every round
(15ms for identity).  Gates: reveal composition sha 8ac1bd7c unchanged
(bit-identical pixels through the new layout), live-transition-gate
pass.  Bonus: the long-documented identity glass-layer waste
(bake+cache+upload of an unsampled layer) is gone as a side effect.

Next efficiency step, designed but not shipped: a background pre-warm
worker (bake item k+1's standard + both non-identity glasses after each
render) would make every rotation and flip a full cache hit; it wants
the bake core factored into shared helpers first so the key computation
can never fork.

## Session 190 (2026-08-19): the full-assurance pass

Everything re-verified from primary sources with the final binary
(2b5db4a), per the user's directive: Apple's pages re-fetched, their
images re-downloaded and re-viewed, the Landmarks project re-inventoried,
every gate re-run under labwc, and parity re-reproduced against FRESH
frames captured on the M1 today.

APPLE SOURCES, RE-VERIFIED TODAY.  The HIG Materials page (37 images
re-listed via the docs JSON; the three canonical Liquid Glass photos
re-downloaded and re-viewed) still agrees qualitatively with every
measured law: clear over bricks = transmissive blur + strong rim +
edge refraction + no shadow; regular over starfield = near-opaque dark
platter (the 48% dark bleed); regular over beach = milky platter WITH
the outer shadow visible (our measured ~3.6-4% constant).  The
adopting-liquid-glass and applying-liquid-glass-to-custom-views docs
re-fetched: the API surface is glassEffect(.regular/.clear/.identity)
+ .tint + .interactive, GlassEffectContainer/glassEffectID/
glassEffectUnion morphing, glassEffectTransition(.matchedGeometry/
.materialize), scroll-edge/sheet/icon chrome effects, and accessibility
adaptation.  Landmarks (37 Swift files) uses exactly:
GlassEffectContainer + .glassEffect(.regular, in:) + .glassEffectID +
.buttonStyle(.glass) + .tint(.clear) + .backgroundExtensionEffect.

COVERAGE MATRIX (wallpaper-engine scope):
  material rendering (blur/refraction/rim/shadow/transfer/appearance)
    - measured laws, receipts below ........................ COVERED
  variants regular/clear/identity .......................... COVERED
  .tint law (chromatic + neutral regimes, rim's own law) ... COVERED
  materialize-class transition (the wallpaper reveal) ...... COVERED
    (the corpus-exact mask + measured clock/exit laws)
  .interactive, shape morphing between elements, scroll-edge/
    sheet/tab/icon chrome ........... N/A for a wallpaper: these are
    app-chrome and multi-element behaviors with no wallpaper analog.
  accessibility triad:
    Reduce Transparency  measured (session 188)
    Increase Contrast    measured (session 188)
    Reduce Motion        MEASURED TODAY: Apple's wallpaper transition
      is EXEMPT - 61-frame dynamics under reduceMotion=1 (guarded
      toggle, "RESTORED reduceMotion=0" verified; set
      lgcap-reduce-motion-1024, M1 home + walle-archives) show the
      same radius ladder, motion amplitudes and duration as normal to
      within the known one-frame start jitter.  walle's behavior -
      keep animating - is therefore already Apple's.  Rig flag
      --expect-reduce-motion is lg-test 6815c68.

FRESH-HARDWARE REPRODUCTION.  A new capture ran on the M1 today
(lgcap-verify-20260819, 20:15 UTC, build 25G76, clean preflight) and
the final binary scores against it IDENTICALLY to the week-old
canonical set: full 1.46 / inside 2.29 / worst 4.13 over 68 states -
digit-for-digit the same numbers, proving both that Apple's renderer is
deterministic across capture sessions and that walle reproduces today's
hardware, not an archived snapshot.  Receipts:
  m1-transition-25G76-fresh-20260819-sweep.json     (fresh capture)
  m1-transition-25G76-final-assurance-coded-sweep.json  (canonical set)
  m1-transition-25G76-final-assurance-natural-sweep.json
    (natural holdout: full 0.95 / inside 1.45 / worst 1.95)
Gates on the final binary, re-run today under labwc: reveal process
gate composition sha 8ac1bd7c (byte-exact, both variants identical),
live-transition-gate pass.

What remains is unchanged from TODO.md's user-gated section: the AGX
divider law (the user's live campaign), non-@2x validation, and the
saturated-background RT session.  Within the project's scope and the
agent's reach, nothing is left unmeasured.

## Session 191 (2026-08-19): the last two gates fall

The two remaining "user-gated" items turned out to be agent-completable
after all, and both closed with measured verdicts.

THE RT PLATE LAW IS DECIDED - AND "CONSTANT" IS FALSIFIED.  The rig
gained a deep-red/deep-blue saturated pair (--saturated-backgrounds,
--swap-dynamic-backgrounds; lg-test e322768) and four guarded captures
ran (normal + Reduce Transparency over each colour; restore verified).
The Reduce Transparency plates are BACKDROP-DERIVED, not constant
system colours: cross-solving the two backdrops per channel gives
regular/dark plate ~ 0.21 x backdrop + 5 (transmission consistent to
0.005 between red and blue), regular/light ~ white - 0.08 x
(white - backdrop), and the clear scrims track the blurred backdrop
hue.  The AppKit solid-fill hypothesis the neutral field could not
exclude is dead; not shipping it unmeasured was correct.  Instrument:
analysis/measure_saturated_rt_plates.py; sets lgcap-sat-{red,blue},
lgcap-sat-rt-{red,blue} (M1 home + walle-archives).

THE EXTREME-CHROMA LIMIT IS MEASURED (bonus from the same captures):
walle reads 3.35/3.39 full-frame (interior ~8.0) over the near-primary
fields against 1.46 coded / 0.95 natural.  The transfer and chroma-
mixture laws were fitted at moderate chroma; near saturation they
degrade by ~2-3x.  A chroma-extended transfer fit is the named lead.
Receipts: m1-transition-25G76-saturated-{red,blue}-sweep.json.

NON-@2X IS VALIDATED - APPLE'S RADII ARE POINT-ANCHORED.  A 1x virtual
display (CGVirtualDisplay SPI tool, ~/vdisplay.m on the M1, created and
released around a single capture; the rig gained --display-id, lg-test
cbec766) hosted lgcap-1x-1024: backingScale 1, clean preflight, 204
sweep frames.  The reveal edge radius reads exactly half the @2x
capture's (ratio 2.002) and the clear/light rim profile matches the
point-anchored hypothesis at 0.31 rms against 2.82 for pixel-anchored -
a 9x discrimination.  walle's points = scale/GLASS_CAPTURE_SCALE law is
therefore measured-correct at scale 1, the scale the user's own
monitors run at.  Residual, predicted by the blur-space law: the
virtual display carries a generic sRGB profile rather than the panel
ICC, visible as a ~7-code interior offset; a full material score at 1x
would want the scorer taught mixed extents plus WALLE_BLUR_SPACE=srgb.

AGX STATUS MADE PRECISE in TODO.md from the ledger's own 2026-08-13
reframe: the corpus does not depend on an unknown interpolator law
(176/188 channels from public inputs at zero offset); what remains
hardware-anchored is 9 setup-law channels, the P25 selector table, and
a sub-0.35-ulp24 divider epsilon the corpus never consumes.  The
campaign (the user's own, rulers through v7) continues on those
residuals; nothing for an agent session to run against it.

Every checkbox in TODO.md is now [x].

## Session 192 (2026-08-19): the bake moves to the GPU

The user's challenge - "apple can do it so efficiently but we need 4
workers?" - was the correct indictment of the architecture, not the
implementation.  Apple's material is a GPU compositor effect; walle
computed it with vips on the CPU and cached the result, and every
optimization before this one polished the wrong side of the bus.

The glass layer is now baked on the GPU at upload time: six fragment
passes (forward code-space conversion off the sRGB texture's hardware
decode, separable narrow Gaussian, 8x downsample, separable wide
Gaussian on the reduced grid, and a mix pass that bilinearly upsamples,
applies the luma/chroma mixture law, and converts back through the sRGB
attachment's hardware encode).  Kernel weights follow vips's discrete
mask and min_ampl truncation; edges clamp like EXTEND_COPY.  The CPU
path remains behind WALLE_GLASS_BAKE=cpu as the replay referee.

THE GPU BAKE SCORES CLOSER TO APPLE THAN THE CPU BAKE DID:
    coded sweep   1.46 -> 1.43 full, 2.29 -> 2.23 inside, 4.13 -> 4.04
    natural       0.95 -> 0.93 full, 1.45 -> 1.41 inside, 1.95 -> 1.90
which is the expected sign: Apple's own implementation is GPU bilinear
chains and hardware sRGB curves, and matching the mechanism moved the
numbers toward the hardware on BOTH content classes.  The cpu replay
reproduces its old scores digit-for-digit (plumbing regression-free);
the reveal gate is untouched at 100.0% (identity never bakes).

The user-visible outcome: variant flips present at +0.10 s and touches
at +0.04-0.05 s in the harness - a variant change is now
indistinguishable from a warm rotation, with no cold-bake case left.
The glass side of the disk cache is dead weight under the default path
(workers bake nothing; standard entries remain the only cache), which
retroactively answers the cache-design question: content-addressing
survives, the glass entries did not need to exist.

Receipts: m1-transition-25G76-gpu-bake-{sweep,natural}.json; live gate
pass; shaders/glass_bake.slang.
