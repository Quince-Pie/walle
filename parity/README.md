# Liquid Glass parity boundary

This directory is Walle's evidence-gated implementation boundary. It currently
implements the prospectively established common numeric transition law for
clear and regular material crossed with light and dark appearance in both
directions, plus the transferred regular/dark/materialize selected-region
origin and allocation path, the exact resolved-color mixer, and the
prospectively transferred static regular producer/copy/mip geometry.

`materialize_v2_fixture.bin` contains 6,016 expected IEEE-754 binary32 words:
47 numeric inputs for 32 dynamic states in each of four unopened Retina
holdouts. Its manifest binds those words to the frozen model and prospective
aggregate in `lg-test`. The C gate compares every word and rejects the first
different case, sample, and field.

`dematerialize_v1_fixture.bin` contains the independent reverse-direction
holdout: 5,828 expected binary32 words from 31 genuine dynamic states in each
of four new Retina geometries. All 5,828 words, 124 structured records, and the
preregistered 132-frame/129-distinct topology passed before this fixture was
generated. Both fixtures exercise `walle_lg_transition_numeric_inputs`; its
input is Apple's exact binary32 visible fraction, not an assumed animation
progress transform.

`transition_profile_v1_fixture.bin` retains the executing 258-byte QuartzCore
background profiles for those same eight direction/profile cases. The C23
packer matches all 252/252 profiles and 65,016/65,016 bytes under GCC 15,
Clang 21, Apple clang, and ASan/UBSan. The first 64 bytes carry measured SDF
and source-step geometry into the request; the independently generated region
is therefore reported separately and matches 48,888/48,888 bytes. Profile
offset 248 is not an opacity-affine shortcut. QuartzCore computes
`binary16(Darwin.powf(inputClamp, binary32(1.0f / 2.2f)))`, with gamma bits
`0x400ccccd` and exponent bits `0x3ee8ba2e`.

`small_clear_background_profile_v1_fixture.bin` closes the corresponding
210-byte profile for the small-clear `Tghn` branch. The production constructor
accepts only independent public state—appearance, diameter, exact binary32
visible fraction, binary64 element extent, and binary32 backdrop scale—and
matches 60/60 profiles and 12,600/12,600 retained Apple bytes. The fixture is
linked only into the test executable.

The small context exposes three operation-order distinctions hidden by the
ordinary corpus. The inner-height reciprocal uses the unrounded binary64
`min(20*k, 0.36*G)`, the `[0,8]` shadow offset is normalized by the invariant
128-pixel composition height to `0.0625`, and the shadow color matrix uses an
ascending binary32 FMA product order while the face matrix retains the
established descending order. The last distinction is observable at
clear/dark dematerialize sample 24: applying the face order to the shadow
matrix emits half word `0x3bf7`; Apple and the split-order constructor emit
`0x3bf6`. Across all states, the split-order face, bleed, and shadow matrices
match all 4,320 bytes.

GCC 15, Clang 21, and native Apple clang 21 reproduce all 12,600 bytes in
release and ASan/UBSan builds. The native M1 binaries contain no Nix-store
path. The same gate rechecks the ordinary constructor at 252/252 profiles and
65,016/65,016 bytes, so this scoped branch does not change its established
output. Run both Linux compiler gates with:

```sh
nix develop --command env CC=gcc \
  ./parity/run_small_clear_background_profile_gate.sh
nix develop .#llvm --command env CC=clang \
  ./parity/run_small_clear_background_profile_gate.sh
```

Regenerate the test-only fixture from the retained `lg-test` corpus with:

```sh
nix develop --command env PYTHONPATH=lg-test/Analysis \
  python parity/verify_small_clear_background_profile_corpus.py \
  --capture-root \
    lg-test/artifacts/combined-transition-geometry-holdout-7432ffa-run1 \
  --emitter build/emit-small-clear-profile \
  --fixture-output parity/small_clear_background_profile_v1_fixture.bin \
  --fixture-manifest-output \
    parity/small_clear_background_profile_v1_fixture.json
```

This admits the current-build `Tghn` profile constructor behind exact gates;
it does not close `Tmua/A2Xghfc`, physical compositor transfer, or production
Walle parity. The protected production shader remains unchanged.

`selected_region_v1_fixture.bin` composes that numeric model with Apple's
unseen circle-500 selected-region holdout. It requires 448/448 exact values for
radius staging, mip/alignment policy, desired integer bounds, storage extent,
and copy-base offset. This is a real boundary join: blur inputs come from the
C materialize implementation rather than from a duplicated fixture value.

`resolved_color_v1_fixture.bin` contains every one of the 205 cases used to
prove Apple's private `Color.Resolved` helper against public SwiftUI getters
and the sRGB constructor. Each record preserves raw linear endpoint words,
public sRGB endpoint words, the binary64 fraction, mixed public words, and raw
reconstructed output. The C gate checks 3,280/3,280 staged words in release/LTO
and sanitizer builds. This includes signed zero, subnormals, transfer-threshold
neighbors, extended range, extrapolation, and the parity-critical endpoint
re-encoding ULPs. The constructor's nonlinear base is built as two rounded
binary32 divisions, a multiply, and an add; substituting `(x + .055) / 1.055`
is observably different.

`static_regular_pyramid_v1_fixture.bin` contains the complete six-level Apple
BGRA8 pyramid for the prospectively frozen 377-point fractional-center Retina
holdout. The C implementation starts from the diagnostic RGBA8 wallpaper and
independently performs producer crop/downsample, selected-region copy, and all
AGX mip reductions. GCC 15 and Clang 21, in optimized and ASan/UBSan builds,
match all 546,000/546,000 bytes. The fixture and manifest SHA-256 values are
`6b4e9920fe4cdb7fd18cf91d21a15c28ad67026a2dcbe8ffe8eb5afe10b66e79`
and
`daa722103be4bd6f3c6f958929baddbe60689ee7c14c8ada03b4f86a1eed043a`.
The exact implementation measures 13.4 ms mean wall time on this host versus
167 ms for the first direct software baseline; byte equality remained the
acceptance gate throughout.

Run the GCC release/LTO and sanitizer gate inside Walle's development shell:

```sh
nix develop --command ./parity/run_materialize_v2_gate.sh
```

Regeneration requires the retained v2 validation artifacts and deliberately
revalidates their hashes against the prospective aggregate:

```sh
nix develop --command python parity/generate_materialize_v2_fixture.py \
  --output parity/materialize_v2_fixture.bin \
  --manifest parity/materialize_v2_fixture.json

nix develop --command python parity/generate_dematerialize_v1_fixture.py \
  --output parity/dematerialize_v1_fixture.bin \
  --manifest parity/dematerialize_v1_fixture.json

nix develop --command python parity/generate_selected_region_v1_fixture.py \
  --output parity/selected_region_v1_fixture.bin \
  --manifest parity/selected_region_v1_fixture.json
```

`liquid_glass_darwin_powf.c` reproduces the measured macOS 26.6.1
positive-normal fast path instead of calling the host `powf`. Direct Apple
clang validation compares every one of 10,485,761 consecutive binary32 bases
from 0.5 through 1.25 against Darwin and finds zero unequal words. That range
strictly contains every base produced by the frozen Liquid Glass profiles.
The complete emitted-word stream has SHA-256
`ade82dab80071f06aa9438043dd97d3ebd5baa56744a69ca20300aad26f46f2a`
under Apple clang, GCC 15, and LLVM clang 21. The local gate pins that digest
and separately retains the two dematerialize sentinels where glibc `powf` is
one ULP lower than Apple.

The profile clamp uses the same portable path with exponent `0x3ee8ba2e`.
Apple clang compares every one of 8,388,609 consecutive positive binary32
bases from 1 through 2 against Darwin with zero unequal result words. This
strictly contains every supported profile's `inputClamp` range. GCC 15 and
Clang 21 reproduce the packed-half stream with SHA-256
`adc847b647eb666e040c51493d3de90a5ec775d6670afd35f7b2f30195d0239e`.
The corresponding glibc stream differs, so substituting host `powf` is an
explicit gate failure even though all 252 retained profiles happen to round to
the same half values.

This boundary establishes numeric materialize and dematerialize behavior for
the frozen four-profile families, exact transition-profile packing, and the
complete resolved-color blend. Static
regular crop, allocation, copy, and mip construction are also transfer
authorities after the unseen physical-Retina geometry holdout. The canonical
static circle now has independently generated main/shadow meshes, destination
prepass, private profile, and final-highlight inputs as well. Live transition
producer geometry, transition foreground/final-highlight production, and
physical compositor transfer remain open. The release
Walle executable now passes the canonical static matrix through its own
layer-shell/EGL surface in an explicit diagnostic mode. The ordinary live
wallpaper-transition mode is not yet authorized. The production shader is
unchanged.

## Captured-input reference oracle

The exact reference renderer recovered from commit `3d11a54` is preserved in
`analysis/apple_glass_reference*.glsl` and
`analysis/apple_glass_reference_renderer.py`. The old commit's headline audit
was invalid as a Walle claim: it constructed a path to `shaders/frag.glsl` but
rendered `analysis/apple_glass_reference.frag.glsl` instead.

`analysis/run_captured_input_reference_oracle.py` replaces that audit with a
fail-closed scope. It hashes the protected production shader as metadata but
marks it as unrendered, pins all oracle sources and 1,794 fixture files, and
reports every still-open product gate explicitly. Run it with the `lg-test`
analysis shell:

```sh
nix develop ./lg-test --command env \
  PYTHONPATH=analysis:lg-test/Analysis \
  python analysis/run_captured_input_reference_oracle.py
```

The recorded result checks four complete 1024-by-1024 frames—16,777,216 output
bytes—with zero unequal bytes. Its authority is limited to the recovered
shader supplied with captured Apple uniforms and backdrop mips. It is an exact
oracle for subsequent independent-input and Walle integration work, not proof
that the current production shader is exact.

## AMD circle-specialization admission

`analysis/run_amd_exact_circle_reference_gate.py` applies only byte-gated
specializations needed by Walle's circular geometry and substitutes every
static render input with an independent construction. An
earlier fast-circle
candidate evaluated `point + (circleScale - halfSize)` instead of Apple's
`(point - halfSize) + circleScale`. That reassociation changed 90 output bytes
across 79 pixels and was removed. The admitted generator preserves Apple's
supercircle operation order; material constants, half conversions, and the
circle-only dispatch were then isolated and gated independently.

The corrected clear and regular shaders each reproduce all four complete
Apple frames exactly on both local radeonsi devices:

- Ryzen 9 9950X3D integrated GPU: 16,777,216/16,777,216 bytes.
- Radeon RX 9070 XT: 16,777,216/16,777,216 bytes.

Run the two device gates with:

```sh
nix develop ./lg-test --command env \
  GLCONTEXT_DEVICE_INDEX=0 PYTHONPATH=analysis:lg-test/Analysis \
  python analysis/run_amd_exact_circle_reference_gate.py

nix develop ./lg-test --command env \
  GLCONTEXT_DEVICE_INDEX=1 PYTHONPATH=analysis:lg-test/Analysis \
  python analysis/run_amd_exact_circle_reference_gate.py
```

This admits the generated shader arithmetic on those two devices, not the
production process. The profile path is no longer captured input:
`liquid_glass_static_profile.c` reconstructs all 258 bytes from geometry and
the recovered endpoint attributes. It matches 1,032/1,032 bytes under GCC 15
and Clang 21. The constructor uses Apple's captured BT.709 bases and exact
binary32 FMA order; in particular, shadow face alpha is the rounded sum of
fill alpha and SDR shadow alpha.

The main/shadow geometry matches 896/896 captured components and indices. The
coordinate-hash wallpaper plus its BGRA/flipped destination prepass match
50,331,648/50,331,648 compared bytes. The four final-highlight meshes and
248-byte constructor prefixes match all 1,552 compared bytes. The render gate
then runs with an empty capture-runtime object and no per-capture half lookup;
the only captured pixels it reads are the final Apple frames used as the
comparison oracle. Both AMD devices remain exact across 33,554,432 combined
output bytes.

The actual Walle process, its layer-shell integration, live transition
producer, and physical Retina output are outside this gate. A standalone
Walle-owned Wayland EGL window surface is covered separately below. The
protected production shader remains unchanged until a real production Walle
frame has zero unequal bytes.

## Walle-owned C/OpenGL static gate

`parity/render_walle_exact_static_gl.c` moves the admitted static path out of
Python/ModernGL and into a C23 renderer using EGL and a core OpenGL context.
The fixture packer generates every render input independently: wallpaper,
destination prepass, source mip chain, profile, three meshes, index streams,
highlight payload, and raster coefficients. Captured bytes appear only in the
final comparison images. The renderer compiles the exact specialized shaders,
uploads those inputs itself, executes main, shadow, and final-highlight draws,
and reads back its own RGBA8 framebuffer.

All four clear/regular × light/dark frames are byte-exact on both local AMD
devices: 33,554,432/33,554,432 combined bytes, zero unequal pixels, and maximum
channel delta zero. GCC 15 and Clang 21 both compile the renderer cleanly. The
repeatable gate is:

```sh
./parity/run_walle_owned_static_gl_gate.sh
```

The first attempted GLES 3.2 retarget was correctly rejected. GLES lacks the
desktop fine/coarse derivative entry points used by the recovered shader; a
naive mapping to the base derivative functions changed 278 bytes in the first
clear/light frame. Walle's AMD stack exposes OpenGL 4.6 through the same EGL
implementation, so the accepted boundary retains the already byte-gated
desktop semantics rather than tolerating that difference.

This closes the C renderer and local AMD execution unknowns, but it is not yet
the production `walle` process. The renderer additionally creates a real xdg
toplevel and EGL window surface on the live Wayland session, copies each exact
frame to the default back buffer, and compares that buffer before presentation.
All four cases pass: 16,777,216/16,777,216 bytes, zero unequal pixels, and
maximum channel delta zero on the RX 9070 XT. Reproduce that matrix with:

```sh
./parity/run_walle_owned_wayland_static_gl_gate.sh
```

These results close the standalone C, driver, EGL, and Wayland-window pixel
boundaries. The exact renderer is also linked into the release Walle
executable behind a fail-closed diagnostic mode. Walle creates its normal
`zwlr_layer_shell_v1` surface, selects the admitted core context for that mode,
renders the four independent fixtures, and compares both its offscreen result
and layer-shell back buffer. All 16,777,216/16,777,216 bytes are exact, with
zero unequal pixels, under release/LTO and ASan/UBSan; GCC 15 and Clang 21
produce the same exact result. Reproduce this boundary with:

```sh
./parity/run_walle_process_static_gl_gate.sh
```

The diagnostic mode deliberately does not claim ordinary live-transition
parity. A proposed wholesale migration of Walle's existing approximate shader
from GLES to core OpenGL was rejected: 24 of 100 repeated core runs changed
one regular-material channel by one code value, while 30 of 30 GLES runs were
stable. The exact Apple renderer therefore selects core only inside its gated
path; the existing protected GLES shader and VRAM optimization remain
unchanged.

The scoped results are `analysis/walle_owned_static_gl_gate_result.json`,
`analysis/walle_owned_wayland_static_gl_gate_result.json`, and
`analysis/walle_process_static_gl_gate_result.json`.

## Wallpaper reveal: what is proven, and what is not

The paragraphs above predate the reveal work and describe the OpenGL
diagnostic path. The Vulkan wallpaper reveal is now established separately,
and this section is the current statement of record.

**Established.** `analysis/run_walle_reveal_process_capture_gate.sh` runs the
actual Walle executable against a real `zwlr_layer_shell_v1` surface and
scores its readback against Apple's 65-frame hardware corpus:

- reveal mask: `mismatchedPixels=0`, 65/65 frames byte-exact;
- composed presentation: `composedMismatchedPixels=0`, 65/65 frames
  byte-exact on all four channels;
- both material-variant configurations compose identical bytes, because
  Apple renders exactly one wallpaper reveal.

The composition law is a mask-weighted code-value blend,
`round((mask * incoming + (255 - mask) * current) / 255)` per channel, with
no veil, platter, lens, ring, glow, shadow, or dither. That is not a fitted
approximation: any such term would have broken the byte equality measured
across 273 million reference pixels. The blend numerator is never a rounding
tie, so the result is bit-deterministic through float arithmetic.

**Conditions.** Those results hold at the corpus conditions: 2048x2048,
centre (512, 614.4), maximum radius 2164.104505809273, the k/64 progress
ladder, opaque black revealing opaque white, regular material, dark
appearance.

**Not proven.** Four boundaries lie outside those conditions and are measured
rather than assumed (TASK.md later-141):

- *continuous progress* - the single off-ladder hardware frame is matched to
  0.092% of pixels (3,838 of 4,177,920), all inside the antialiased boundary
  ring, maximum delta 25/255. Its geometry sits off the integer-bounds grid
  that walle's circle law snaps to, and the 65-state ladder is exact
  *because* of that snapping. `analysis/run_walle_reveal_offgrid_gate.sh`
  records the bound; it is a measurement, not a parity gate;
- *colour content* - the in-repo colour-field corpus saved through a lossy
  Color LCD to sRGB conversion (its endpoints differ from the regenerated
  sources by 2 to 4 code values), so it cannot byte-prove the blend at
  interior codes. Saturated regions verify 99.99133% exact;
  `analysis/verify_reveal_colored_blend_corpus.py` reports the split;
- *appearance and material variant* - the corpus is regular/dark only;
- *other geometries* - the hardware-measured plane tables are keyed to the
  corpus radius words, so other resolutions and centres fall back to the
  computed setup chain, which has known rare one-ulp misses.

Physical presentation (display hardware transfer) remains outside any
software gate.
