# Walle Liquid Glass handoff

Updated: 2026-08-11 (US/Central)

## Current decision

The macOS/WindowServer causal experiment is paused. Do not build, run, repair,
or resume it unless the user explicitly asks. The active product work is the
Linux/Wayland Walle implementation.

In this handoff, “portable rule” means an algorithm reconstructed from public
inputs that does not depend on Apple private runtime calls or captured
state/pixel lookup. It does not mean making Walle cross-platform: Walle is
explicitly a Linux/Wayland program.

Walle now has one reveal path. It constructs the recovered reveal mesh from
public transition inputs, renders it into a per-output GLES `GL_R8` target,
and uses that target as the sole reveal mask in a separate composition shader.
The `transition_reveal` setting, implementation enum, legacy program, and all
runtime reveal branches were removed. First boot and transition-disabled
refreshes use the same composition path with the full-coverage endpoint mask.

Do not describe the Walle path as 99.9999967% parity. During this task we proved
that the often-quoted nine-pixel result is Apple-assisted rather than a
portable public-input algorithm:

- Old retained-root/offscreen-versus-physical localization result:
  `272,629,751 / 272,629,760` exact pixels, `99.999996698820%`, with nine
  one-code pixels in state 42. This used Apple-owned setup.
- A fresh app-owned SwiftUI/CARenderer tree renders the retained 65 masks with
  zero mismatches, but that is an Apple renderer/oracle, not a Linux rule.
- Current public-input, output-blind offline CPU reference:
  `272,629,669 / 272,629,760`, `99.999966621399%`, with 91 one-code channel-0
  mask-sample residuals and 52/65 exact channel-0 frames. This uses the
  start-preserving canonical Sutherland--Hodgman order and a vertex-zero fan;
  the older endpoint-rotating policy scored 116.
- Current production model plus mask shaders, exercised both by the
  surfaceless gate and by a diagnostic run of the Walle executable:
  `272,629,669 / 272,629,760`, `99.999966621399%`, with 91 one-code residual
  pixels and 52/65 exact frames. The signed delta is +51/-40. The integrated
  Radeon and discrete RX 9070 XT produce byte-identical candidate inventories
  under Mesa 26.1.5. The Walle process produces that same 65-mask inventory
  byte for byte. Candidate inventory SHA-256 is
  `9062b7bfde617f88638c9b48fdb8ace7b6f91b4518d54c5a6e54abcb51e93644`;
  the per-state mismatch-count SHA-256 is
  `d6c006d789b551e875555f3e8ef32f0c46c3ec3911802fea405ef9d3458edb5d`.
  This remains a mask score before composed-RGBA and physical-presentation
  transfer.

Two remaining boundaries must not be conflated. The public CPU reference still
encounters 95 non-axis-separable post-guard setup instances for which the
portable AGX coefficient law is unknown. The nine-pixel Apple-assisted result
delegated that setup to Apple and then localized a later presentation delta;
it is not an implementation of the missing setup law. A read-only analysis of
the historical 116-pixel policy attributed 105 pixels to retained Apple setup,
two to a helper-lane extrapolation, and nine to presentation. The canonical
91-pixel policy changes the clipped topology, so that historical per-pixel
decomposition must not be transplanted onto 91 without rerunning and sealing
the evidence. Its 64-report setup set is not provenance-closed; its audit-only
ordered-set digest is
`6442156630e7ea5af33ab375f4adf40c3be6a96de5ec019290feb0425a1e75b1`.

The production progression `8,268 -> 3,873 -> 141 -> 91` is recovered
arithmetic and raster behavior, not corpus correction. The first reduction came from a
generic integer binary32-to-binary16 round-to-nearest-even converter with an
exact saturated-alpha fast path. Across all 15,360 positive binary16 intervals
from zero through one and 33 samples around every midpoint (506,880 cases),
Mesa's native conversion was wrong 253,440 times and the recovered converter
zero times. The second reduction came from the input-derived P25/AGX axis law,
global public-mesh ownership for the center and XOR helper lanes, and the
admitted Apple fast-square-root correction. The last reduction came from the
exact public post-guard constructor: canonical clipping order, representable
generated children appended to the same owner table, child-scoped helper-lane
ownership, and last-active-owner ordering. Production and the canonical CPU
reference now have the same 91-pixel inventory. The remaining known boundary
is hardware-specific arbitrary generated-child setup, not the recovered
fragment arithmetic, binary16, or R8 transfer. Do not hide it with captured
per-state geometry, a state selector table, or a per-pixel correction map.

## What was recovered

The substantive Liquid Glass algorithms are not merely macOS-vs-Linux
compositor quirks or floating-point accidents. Recovered, evidence-backed
pieces include:

- materialize/dematerialize timelines and public transition-state
  construction;
- public rounded-circle bounds, scissor, and the border-grid versus compact
  visible-arc topology rule;
- selected-region/crop construction and reveal guard clipping;
- backdrop-pyramid construction and the pass ordering that feeds the glass;
- circle SDF, normal, fine-derivative antialiasing, binary16 coverage, and R8
  transfer;
- clear-material refraction/dispersion, blur/tone veil, regular platter
  behavior, contact shadow, edge relief, highlights, and nonlinear
  composition order;
- Apple/AGX/P25 numeric and raster behavior needed to reproduce retained
  pixels.

Platform-specific presentation, private CARenderer scheduling, and the missing
post-guard setup rule remain separate boundaries. The ordinary Walle product
translation uses the recovered public geometry and material law without
claiming Apple GPU execution equivalence.

## Walle implementation

Production integration:

- `walle.c`
  - has no reveal selector or alternate production reveal branch;
  - requires OpenGL ES 3.2, initializes the shared mask/composition programs
    and recovered arithmetic textures with EGL, and lazily creates per-output
    R8 texture/FBO/VAO/VBO/EBO resources;
  - renders first boot and transition-disabled refreshes through the same path
    at the full-coverage endpoint;
  - derives top-left public geometry from output extent, live center, radius,
    and linear transition progress, then converts it explicitly to GLES
    coordinates;
  - computes the public maximum radius in binary64;
  - batches up to four base owners and 90 canonical post-guard child owners in
    one 4,528-byte std140 block, generates one packed two-channel P25/AGX axis
    atlas, and renders exactly one mask draw plus one final composition pass;
  - checks draw and `eglSwapBuffers` failure, and swaps A/B textures only after
    a successful presentation;
  - deletes per-output mask objects with a current utility context.
- `parity/liquid_glass_reveal_mask_model.{h,c}`
  - bounded C23 public circle and table-free geometry constructor;
  - exact 16-vertex/54-index maximum, 48-byte vertex layout;
  - scalar Apple fast-sqrt/SDF/fine-derivative/F16/R8 reference API for
    caller-supplied retained-corpus raster coordinates.
- `parity/liquid_glass_postguard.{h,c}` and
  `parity/liquid_glass_raster.{h,c}`
  - use exact integer binary32 rational/RNE arithmetic and dynamic
    `[-extent/4, 5*extent/4]` guards with canonical L/R/T/B
    Sutherland--Hodgman clipping;
  - emit at most 90 ordered fan children, classify the retained corpus as 153
    supported, 88 hardware-specific unsupported, and 30 offscreen children,
    and pack the supported base+child owners into one RG32UI axis table;
  - keep child-center helper samples scoped to the selected child while base
    centers retain independent global-base helper ownership.
- `shaders/reveal_mask.vert.glsl` and `shaders/reveal_mask.frag.glsl`
  - OpenGL ES 3.2 R8 mask raster for the recovered two-family mesh;
  - uses `gl_PrimitiveID`, a reflected 94-owner std140 block, and the integer
    axis atlas to derive the public-mesh owner and XOR helper lanes rather than
    trusting native derivatives;
  - performs the recovered Apple fast-square-root correction and integer
    binary32-to-binary16 round-to-nearest-even transfer explicitly.
- `parity/apple_fast_sqrt_correction_nibbles.bin`
  - 4 MiB reveal-only low-nibble packing of the admitted 8 MiB Apple arithmetic
    artifact; an exhaustive provenance gate decodes all 8,388,608 source
    low-nibble entries exactly;
  - SHA-256
    `dcd882a8af21ac9f2c0f82a3239d6d5f247e2eb5b3535348f6931b65c41f23b1`.
- `shaders/frag_reveal_best_known.glsl`
  - separate composition shader; R8 is the sole `mask` value throughout the
    material, shadow, highlight, and final mix.
- `analysis/verify_reveal_best_known_gles.c`
  - surfaceless GLES gate for empty/border/compact/orientation mask behavior;
  - exercises R8 codes `0,1,127,128,254,255` and proves R8 replaces rather
    than multiplies or ignores an analytic mask.
- `analysis/score_reveal_best_known_gles.py` and
  `analysis/reveal_best_known_gles_corpus_gate_result.json`
  - run the production model and mask shaders over all 65 retained states;
  - invariant-check the opaque-grayscale references and hash every candidate
    frame;
  - record the current 91-pixel Mesa/GLES regression baseline without
    tolerances;
  - baseline SHA-256
    `107755cee108db83c489658df14b89da271503a9812612097b0804bc0360f76f`.
- `analysis/reveal_best_known_gles_cross_gpu_result.json`
  - SHA-256
    `d9bf40975cebec88d7ccaa038d4e53335f3764e2761b3b4012fad92574f33ad3`;
  - pins the byte-identical integrated-Radeon/RX-9070-XT candidate inventory.
- `analysis/run_walle_reveal_process_capture_gate.sh` and
  `analysis/reveal_best_known_process_capture_result.json`
  - result SHA-256
    `a00a0201acee1767ab8d22df2a96d5517a3d11b051b015e8dec3a3dea32a444a`;
  - drive the ordinary release Walle executable through first boot and 65
    canonical `best-known` states on a private headless Labwc compositor;
  - require 65 ordinary composition swaps and 64 frame callbacks, securely
    write the actual process-owned R8 masks, and compare each mask byte for
    byte with the standalone GLES inventory;
  - reject nonempty/symlink destinations and conflicting diagnostic authority.
- `parity/test_liquid_glass_reveal_mask_model.c` and
  `parity/run_liquid_glass_reveal_mask_model_gate.sh`
  - C23 release, GCC analyzer, ASan/UBSan/leak, fixed-state, all-64-family, and
    exact scalar arithmetic checks.
- `Makefile`
  - links the public model and adds `reveal-mask-model-gate`,
    `reveal-best-known-gate`, `reveal-best-known-corpus-gate`, and the explicit
    heavier `reveal-best-known-process-gate`.
- `README.md` and `config.ini`
  - document the sole reveal path and its evidence boundary.

Historical parity tooling still authenticates `shaders/frag.glsl` at SHA-256
`6489828f12de599da9633d6183266a81b71ed846a1b03c03cb4eb9c23639352d`.
Ordinary Walle no longer embeds or executes that historical reveal shader.
The reveal-stage and reveal-compositor research gates continue to authenticate
it, but it is not a production fallback or compatibility contract.

## Recorded public reference

The table-free constructor and honest retrospective scorer are recorded in:

- `lg-test/Analysis/score_reveal_v74_public_geometry.py`
  - SHA-256
    `cd66a07a52b58adb248b49c07a2cc6ededbc6a673df98ca3cce377c3989328c7`
- `lg-test/Analysis/test_score_reveal_v74_public_geometry.py`
  - SHA-256
    `3a6cde55ce1a68b51236d386f17ab805812c8367856631f3fc9d3108cde820a2`
- `lg-test/Analysis/score_reveal_v74_public_raster.py`
  - SHA-256
    `55189c5f59eff05f482093fee13afa83414b8580a85de4cfabde36821248506a`
- `lg-test/Analysis/test_score_reveal_v74_public_raster.py`
  - SHA-256
    `7277b200602a80c593567178cc767a1e288b2d3e34b10886d11244d15a506053`
- contract:
  `lg-test/Analysis/reveal_v74_public_geometry_contract.md`
  - SHA-256
    `fe217d5eb815541af272b22da0661de911e710f09dff6a115f68f09f14fa6942`
- result:
  `lg-test/Analysis/reveal_v74_public_geometry_score_result.json`
  - SHA-256
    `a7492a7984da685d2ca8cefc4109b3db376d1d3a63ed81ab6ec939508e3ea990`

The constructor reproduces every fragment-consumed position, SDF/helper
coordinate, constant, scissor, count, and index across all 64 nonempty retained
local streams: 52 border-grid and 12 compact-visible-arc states. It uses no
state or reference-pixel lookup. Sixty-eight fragment-unused border
source-coordinate scalars retain disclosed cancellation residue, and unused
vertex components 10-11 are intentionally zero rather than reconstructed.

This is a locally recorded retrospective result, not a provenance-complete
artifact closure: the external corpus, imported analyzer, and all calibration
assets are not bundled under one manifest. In particular, the result JSON's
`ordinaryWalleIntegrationComplete:false` and
`walleProductionFilesChangedAtAnalysisFreeze:false` describe its
analysis-only creation time, before the later product integration and process
capture; they are not current global status fields.

The scorer invariant-checks every retained RGBA PNG as opaque grayscale, then
compares its channel-0 mask samples. A historical prototype's 115-residual
result was reproduced and explained: it selected only one source triangle per
group and ignored clipped polygons unless they had exactly three vertices.
The complete endpoint-rotating policy evaluates both source triangles and
fan-splits four-vertex clips, producing 116 residuals. The canonical policy
now preserves the polygon start through no-op Sutherland--Hodgman planes and
uses the vertex-zero fan `(0,1,2), (0,2,3)`, reducing the output-blind score to
91. Apple setup traces independently support this topology for every generated
child representable by the admitted axis-separable setup model. The public and
captured pre-clip geometry candidates are byte-identical across all 64
nonempty states; 115 was an incomplete overlay, not a superior model.

The canonical Python CPU prototype took about 630 ms per 2048-by-2048 frame on the
measured host and allocates a 4 MiB candidate surface, excluding NumPy
temporaries and calibration tables. It is a calibrated retrospective reference
for the admitted CPU model, not a viable production full-frame generator.
Walle therefore executes the bounded public mesh with Mesa/GLES invocation,
an exact public post-guard constructor, a combined base/child AGX axis atlas,
explicit current/XOR-helper ownership, Apple fast-square-root correction,
exact binary16 materialization, and R8 transfer. Supported generated children
now use the same canonical policy as the CPU reference; the 88 visible
arbitrary children whose Apple setup law is unknown remain explicitly
unsupported rather than approximated.

A rejected output-blind shortcut evaluated the normalized circle directly at
top-left pixel centers, using the admitted Apple square root, XOR helper lanes,
binary16, and R8 transfer but bypassing mesh interpolation. It scored 1,767
residuals over the same 65 states: much worse than both the current production
score of 91 and the canonical mesh model's 91. Preserve the two-family mesh;
a fullscreen analytic circle is not an equivalent replacement.

A historical endpoint-rotating-policy ablation changed only the admitted Apple
fast square root to IEEE binary32 `sqrt`; its residuals rose from 116 to 979.
That experiment proves the intrinsic matters, but its exact delta is not a
score for the newer canonical clipping policy. A later production ablation
showed that exact axes and helper ownership without Apple sqrt still scored
1,148; the admitted Apple correction reduced that to 141, and canonical
post-guard child setup reduced it to 91. The correction is
now stored as a lossless 4 MiB nibble table rather than the original 8 MiB
multi-intrinsic artifact. The production and canonical CPU mask inventories
now agree; the stronger Apple/physical oracle boundary remains described
above.

## Existing parity building blocks

The `parity/` directory also contains the evidence-derived C23/OpenGL modules
for transition frames, raster tables, producer raster, backdrop pyramid, the
modular desktop-core renderer, reveal routing, and fixed-state composition.

Important boundaries:

- `parity/liquid_glass_gl_renderer.c` is the admitted desktop-core renderer,
  not an object-sharing extension of Walle's GLES context.
- `parity/render_walle_exact_static_gl.c` is a fixture-driven diagnostic path,
  not the ordinary transition.
- `parity/liquid_glass_reveal_stage.c` remains deliberately fail-closed; its
  exact production authority is not being weakened for `best-known`.
- `parity/liquid_glass_reveal_compositor.c` freezes a compositor shell but
  does not provide the missing public post-guard setup rule.

Useful research routing documents:

- `lg-test/Analysis/walle_ordinary_reveal_integration_v107_result.json`
- `lg-test/Analysis/liquid_glass_parity_deep_model_handoff.md`

## Full analysis-tree audit

On 2026-08-11 the root `analysis/`, `lg-test/Analysis/`, artifact-contained
analysis trees, and every root `_analyze*`, `_probe*`, `_search*`, fit, and
sweep script were inventoried and the semantically unique reports were read.
Root `Analysis/` is empty and `lg-test/analysis/` does not exist; most artifact
copies are frozen duplicates or provenance inputs. No retained output-blind
scorer beats the canonical 91 and no report closes arbitrary
non-axis-separable post-clip AGX setup.

Reusable findings that are already implemented include:

- 1/256-pixel fractional-coordinate transfer with nearest rounding and ties
  toward positive infinity;
- exhaustive normalized-P25 selection over all 16,777,216 keys;
- the 27-bit setup, truncated-partial-product carry, 28-bit composite, and
  36-bit center iterator/axis generator for admitted raster domains;
- native-owner parity-zero 2x2 helper-lane semantics, generalized to the
  public reveal mesh through a global owner lookup;
- exact public geometry and compositor transfer;
- Apple fast-square-root correction and the binary16 RNE law now used by the
  production reveal mask shader.

The exact-axis experiment is now integrated. Its one-draw OpenGL ES 3.2 path
uses `gl_PrimitiveID` only to identify the input triangle; global ownership for
the center and XOR partners is recomputed from the public quad metadata. The
packed axis texture is `RG32UI`, at most 283,776 bytes on the 65-state corpus.
Together with the 4 MiB sqrt table, persistent arithmetic storage is 4,478,080
bytes, down from 8,956,160 bytes in the first exact-axis implementation.

OpenGL 4.6 and SPIR-V were evaluated rather than assumed. The RX 9070 XT host
supports GL 4.6, `ARB_gl_spirv`, compute, images, SSBOs, int64, and subgroups,
but reports no SPIR-V float-control extensions. Base SPIR-V therefore cannot
freeze Apple rounding, square root, or derivative/helper behavior; it does not
solve the remaining setup law. GLES 3.2 already supports the required
one-draw `gl_PrimitiveID` path. A GL 4.6 compute backend remains a valid future
performance experiment, not an accuracy prerequisite or current migration
target.

The archive's exact highlight arithmetic, dynamic full-frame fixtures, and
desktop-core compositor results are valuable for the wider Liquid Glass model
but do not solve reveal-mask clipping. Conversely, the v114 texture-backing
analysis proves only that the sampled slot-3 region was opaque black at bind
sequence 8 and opaque white at bind sequence 19; it does not prove draw-time
immutability, shader consumption, the whole texture, or a portable producer.

## Validation completed

Passing commands on 2026-08-11:

```text
nix develop --command make -B reveal-raster-gate
nix develop --command make -B reveal-mask-model-gate
nix develop --command make -B reveal-best-known-gate
nix develop --command make -B reveal-best-known-corpus-gate
nix develop --command make MODE=release reveal-best-known-process-gate
nix develop --command make ANALYZE=1 reveal-best-known-process-gate
nix develop --command env ASAN_OPTIONS=detect_leaks=1 \
  UBSAN_OPTIONS=halt_on_error=1 make SANITIZER=1 reveal-best-known-process-gate
bash parity/run_liquid_glass_reveal_stage_gate.sh
bash parity/run_liquid_glass_reveal_compositor_gate.sh
nix develop --command make -B -j2
nix develop --command make -B MODE=release -j2
nix develop .#llvm --command make -B MODE=release -j2
```

Results:

- model release/analyzer/ASan+UBSan+leak checks pass;
- surfaceless GLES geometry/R8-composition gate passes;
- the production GLES mask corpus gate reproduces 272,629,669/272,629,760
  exact pixels (91 one-code residuals) on both available AMD GPUs with identical
  candidate hashes;
- an actual release Walle process completed first boot and the exact 65-state
  diagnostic trajectory on a private 2048-by-2048 headless Labwc/Wayland
  compositor; its 65 R8 masks are byte-identical to the scored standalone
  inventory, with 65 swaps and 64 frame callbacks;
- the same actual-process gate passes under the combined AddressSanitizer,
  UndefinedBehaviorSanitizer, and leak check;
- historical reveal-stage and reveal-compositor research gates pass with their
  frozen shader hash;
- GCC 15 debug and release Walle builds pass without diagnostics;
- Clang 21 release Walle build passes; its warnings are pre-existing C23
  portability warnings (`[[unsequenced]]` and `%w64x`), not reveal changes;
- new standalone C files pass `clang-format --dry-run --Werror`;
- public Python reference suite: 13/13 tests, Ruff, formatting, and Python 3.14
  compilation pass.
- the raster gate passes release, GCC analyzer, and ASan/UBSan/leak with
  explicit `-fno-wrapv`, including `INT32_MIN`-adjacent public coordinates;
- the packed sqrt provenance test exhaustively decodes all 8,388,608 source
  entries, and the 64-state axis census is 2,181,364 two-channel words;
- the final 576-frame 2048-by-2048 mask benchmark measured 17.647 ms
  synchronized / 13.467 ms pipelined on the integrated Radeon and 3.305 ms /
  2.730 ms on the RX 9070 XT; CPU batch construction measured about 2.7 ms on
  both. These are focused mask benchmarks, not end-to-end compositor latency
  guarantees.

Not completed in this environment:

- deterministic scoring of the composed RGBA output and physical presentation
  transfer for all 65 ordinary-Walle states; the completed process gate scores
  the process-owned R8 mask before those downstream stages;
- full resize/multi-output/hotplug/failure-injection lifecycle closure;
- end-to-end composed-frame GPU timing and residency across the supported
  output-size matrix.

Treat those as the next product validation step. Do not infer them from the
surfaceless shader gate.

## Worktree safety

The worktree was heavily dirty before this task. Tracked prior/user changes
already existed in `Makefile`, `flake.nix`, and `walle.c`, and the workspace
contains many untracked research files. Preserve them. Never reset, replace,
or clean the tree. Use bounded `apply_patch` edits and inspect overlap first.

The generated `build/` and protocol outputs have been refreshed by validation.
Do not treat unrelated dirty/untracked files as part of this implementation.

At this handoff, the new parity/model, GLES-gate, and reveal-shader files are
still untracked. A normal build in the current checkout sees them, but a Git
flake source such as `nix build .` omits untracked files. Stage or commit the
intended implementation files before using Git-flake packaging; do not solve
that by adding the unrelated research tree wholesale.

## Paused macOS evidence

The latest landed probe source remains:

- `lg-test/Sources/GlassIntrospect/main.swift`
- SHA-256
  `e5917d861a7e9385e16e56a31a5f0fcc011bb19b9ec2c1a2b7574c01013714ca`

The last signed probe binary is evidence only:

- SHA-256
  `59141f8f52131316b50de72da42441404d198c7b721e32f8bc9f9dfa01eaf587`
- closure:
  `lg-test/artifacts/reveal-gen4-live-final-swap-cross-encoder-build-state42-20260811T113801Z`

The last sealed negative control is:

`lg-test/artifacts/reveal-gen4-live-final-swap-cross-encoder-control-state42-20260811T125715Z`

It ran one C child, returned status 1 with empty streams, and made no retry.
The source draw returned at installed-hook sequence 24; the source encoder
ended at sequence 52, with sequences 25-51 intervening and no hooks after end.
This falsified immediate-end/later-distinct-encoder topology but did not
falsify the visual pipeline/texture hypothesis. Active/analyzer/retry remained
denied.

One later analysis-only artifact is accepted but strictly diagnostic: the
[v114 texture-backing successor](lg-test/artifacts/early-fresh-a2-texture-backing-samples-v114-analysis-only-state42-20260810T235000Z).

It records the sampled slot-3 region as uniformly opaque black at bind sequence
8 and uniformly opaque white at bind sequence 19, the last recorded binding
through selected draw 30. Its receipt explicitly does not prove draw-time
immutability, selected-shader consumption, the whole texture, its producer, or
a portable coverage rule.

The unfinished observation successor must remain unbuilt and unrun:

- `lg-test/Analysis/reveal_gen4_live_final_swap_post_source_observation_prospective.swift`
  - SHA-256
    `895850e873c4e569add318bbb1aaf9823e4291b519e457bb3732600ad20fd571`
- `lg-test/Analysis/reveal_gen4_live_final_swap_post_source_observation_preregistration.json`
  - SHA-256
    `479ae53602351a38ca58ad467e053afb01a52e3062925b0f53959cf546d8fb6e`
- no source test exists.

Known blocker: a missing source/end fail-closed path constructs an invalid
Swift range and can trap before publishing a negative report. The fixture is
mid-integration, unfrozen, and unaudited.

## Path to stronger parity

For the reveal raster:

1. recover the public coefficient setup law for arbitrary non-axis-separable
   post-guard triangles; canonical clipping order/diagonal is already fixed;
2. reduce the public scorer from 91 to the old retained-root nine-pixel
   localization result without captured-state or output-pixel lookup;
3. explain and eliminate the remaining state-42 presentation split;
4. freeze the rule before a genuinely unseen fractional-center holdout;
5. pass that holdout at zero mismatch;
6. score composed Walle RGBA frames and physical presentation (the
   process-owned 65-state R8 mask inventory is already closed);
7. evaluate direct integer compute only if it improves measured cost while
   preserving the exact 91-mask inventory and public-domain gates;
8. optimize only while keeping those gates green.

For the current product branch, the diagnostic-only 65-state process capture
and standalone-inventory comparison are complete, including release and
ASan/UBSan Wayland runs. The implementation is finalized at the public
91-pixel model. Further work is research/validation: recover the missing
hardware setup law and score composed output and physical presentation. Keep
the name `best-known` until those stronger gates justify a different claim.
