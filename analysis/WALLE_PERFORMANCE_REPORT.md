# Walle GPU memory and latency report

Status: measured implementation candidate, quality-gated on AMD radeonsi/Mesa.
The independently reconstructed canonical static renderer is now byte-exact
through a Walle-owned C/OpenGL implementation, a real Wayland EGL window
surface, and the release Walle executable's layer-shell surface. This report
does **not** claim live-transition or physical-display Apple Liquid Glass
parity.

## Locked rendering invariant

The production fragment shader was not changed. Its SHA-256 before and after
the work is:

```text
6489828f12de599da9633d6183266a81b71ed846a1b03c03cb4eb9c23639352d  shaders/frag.glsl
```

The release path does not link Tracy. Profiling is isolated behind `TRACY=1`
in a separate object and binary profile.

## Measurement setup

- GPU/driver: AMD radeonsi, Mesa 26.1.5.
- Compositor protocol: Wayland layer shell through EGL/Wayland WSI.
- Outputs: HDMI-A-1 at 2560×2880 buffer pixels and DP-1 at 5120×2880.
- Allocation source: per-client Linux DRM fdinfo sampled at 50 or 100 ms.
- CPU latency source: Tracy 0.13.1 unwrapped CPU zones.
- Production schedule: HDMI changes every 30 seconds, DP every 60 seconds;
  both use ten-second transitions.
- Controlled schedule: HDMI every four seconds and DP every five seconds;
  both use one-second transitions for 35 seconds.

DRM fdinfo separates resident VRAM, shared/compositor VRAM, and resident GTT.
This is more useful than the rounded process row in `amdgpu_top` because it
also exposes the ownership class of each allocation.

## Allocation isolation

The no-output EGL/context floor is 12.527 MiB VRAM. Static-output probes then
isolate the cost of the duplicate first image:

| Configuration | Original VRAM | Aliased-first-image VRAM | Reduction |
|---|---:|---:|---:|
| no output | 12.527 MiB | 12.527 MiB | 0 MiB |
| HDMI only | 114.797 MiB | 86.039 MiB | 28.758 MiB |
| DP only | 199.047 MiB | 141.539 MiB | 57.508 MiB |
| both outputs | 289.316 MiB | 203.051 MiB | 86.266 MiB |

The raw standard-plus-glass payloads are 28.564 MiB for HDMI and 57.129 MiB
for DP. The measured allocation deltas are slightly larger because radeonsi
rounds the backing BOs. A Wayland presentation buffer is independently 28.75
MiB for HDMI and 57.5 MiB for DP.

This decomposition identified two dominant avoidable owners:

1. First boot uploaded the same standard and glass images into both A and B.
   The renderer now uploads only B and binds B as both shader inputs for that
   one completed frame. The normal A/B ownership is restored by the existing
   slot swap.
2. Mesa's idle EGL window surface retained spare Wayland swapchain buffers
   after the last committed frame. Walle now retires an idle Mesa surface and
   recreates it at the next upload. This behavior is vendor-gated to Mesa; an
   unknown EGL implementation keeps the conservative persistent surface.

The PBO store is also orphaned to zero after both queued texture copies, so a
full output-sized staging allocation does not remain live while idle.

## Controlled 35-second allocation result

The controlled comparison uses the same output files, transition schedule,
sampler, and machine.

| Resident VRAM statistic | Persistent surfaces | Retired surfaces | Change |
|---|---:|---:|---:|
| mean | 339.897 MiB | 242.280 MiB | −97.618 MiB (−28.7%) |
| median | 373.570 MiB | 258.566 MiB | −115.004 MiB (−30.8%) |
| p95 | 373.574 MiB | 287.316 MiB | −86.258 MiB (−23.1%) |
| peak | 373.574 MiB | 316.066 MiB | −57.508 MiB (−15.4%) |
| final sample | 373.574 MiB | 229.820 MiB | −143.754 MiB (−38.5%) |

Median shared/compositor VRAM fell from 172.5 MiB to 57.5 MiB. The final
shared allocation fell from 172.5 MiB to 28.75 MiB: one retained compositor
buffer instead of six output-sized WSI buffers.

## Real-config 75-second result

The production schedule includes intentionally overlapping HDMI and DP
transitions near the end, so the peak still reaches the irreducible overlap.
The time-weighted residency is materially lower:

| Statistic | Original | Candidate | Change |
|---|---:|---:|---:|
| mean VRAM | 330.233 MiB | 208.913 MiB | −121.320 MiB (−36.7%) |
| median VRAM | 318.070 MiB | 174.309 MiB | −143.762 MiB (−45.2%) |
| final VRAM | 375.648 MiB | 260.574 MiB | −115.074 MiB (−30.6%) |
| mean shared VRAM | 127.059 MiB | 64.314 MiB | −49.4% |
| mean GTT | 92.256 MiB | 53.555 MiB | −42.0% |
| final GTT | 94.391 MiB | 8.016 MiB | −91.5% |

A final ordinary-release live sample after a production transition measured
231.824 MiB resident VRAM, 28.75 MiB shared VRAM, 8.016 MiB GTT, 0% sampled
single-core CPU, and 0% sampled GFX. The user's initial observation was about
373 MiB VRAM.

## Latency and work throughput

The old non-boot path uploaded the new textures and then waited for a frame
callback on a static background surface before drawing. The new path arms the
transition clock at the first submitted frame and draws immediately after the
upload. A fresh final-source trace was compared with the frozen baseline trace.

Simultaneous output preparations are treated as one batch and matched to
distinct following frame zones. The first boot batch is excluded, leaving 14
non-boot rotations per trace:

| Preparation complete → first frame starts | Original | Final | Change |
|---|---:|---:|---:|
| median | 18.511 ms | 12.646 ms | −5.865 ms (−31.7%) |
| mean | 18.806 ms | 12.581 ms | −6.225 ms (−33.1%) |
| p95 | 27.016 ms | 20.801 ms | −6.215 ms |
| maximum | 28.692 ms | 23.523 ms | −5.169 ms |

The first controlled HDMI rotation improved from 20.523 ms to 5.498 ms
(−73.2%). The aggregate is the honest headline because DP uploads are twice
as large and the desktop's full-screen application changes compositor pacing.

First boot now performs four fewer full texture uploads in the 35-second
dual-output trace: 32 rather than 36. Total measured upload-zone time fell
from 194.872 ms to 175.096 ms (−10.1%) while producing the same frames. This
is saved work, not a claim that individual recurring copies became faster.

Surface lifecycle overhead is small relative to the saved idle residency:

| Final trace zone | Count | Median | Mean | p99 |
|---|---:|---:|---:|---:|
| recreate idle EGL surface | 14 | 0.160 ms | 0.181 ms | 0.373 ms |
| retire idle EGL surface | 16 | 0.050 ms | 0.051 ms | 0.085 ms |

## Opt-in best-known reveal cost

The experimental `best-known` reveal keeps one per-output R8 mask bundle for
reuse across transitions. Its explicit persistent payload is `width * height`
bytes for the mask plus 768 bytes of vertex storage and 108 bytes of index
storage. That is 4.0008 MiB at 2048×2048, 7.032 MiB at 2560×2880, and
14.063 MiB at 5120×2880 (21.095 MiB for the latter two outputs together),
before driver bookkeeping. Retaining the bundle is intentional: prior
delete/recreate experiments grew Mesa's allocation cache, and redefining a
texture to 0×0 did not release its backing BO.

Invariant sampler bindings, FBO attachment/completeness checks, and one
duplicate viewport are now performed only at program creation or mask resize.
A source-level GL-entry audit therefore removes 11 calls from every warm
`best-known` frame without changing a shader, draw, upload, or swap: the
empty/fill-only/border/compact paths fall from 42/46/53/55 calls to
31/35/42/44. The 65-state ordinary-process gate remains byte-identical to the
standalone GLES inventory after this change. This is a measured work-count
reduction, not yet a claim of lower GPU frame time.

A single nine-second 1280×720 live sample recorded 30,072 KiB VRAM and
16,400 KiB GTT for `best-known`, versus 28,024 KiB and 13,856 KiB for legacy.
Its GPU-time samples (318.0 ms versus 366.5 ms over the interval) are too noisy
to attribute as a speedup. Use the persistent-memory delta as a sizing check,
not a general performance conclusion.

## Quality and correctness gates

- `make -B quality-gate` compiles the actual embedded production vertex and
  fragment shaders against surfaceless GLES3/radeonsi. It compares separately
  uploaded A/B inputs with the first-boot alias path over 48 time, center, and
  variant cases. Result: **0 unequal RGBA8 bytes**.
- A private 1280×720 headless labwc compositor captured Walle once with a
  persistent Mesa surface and once after idle-surface retirement. The complete
  PNG files are byte-identical and share SHA-256
  `bbe3de0df8631cb38c2064be692f68356ed740733042a22bc0e55b739a610720`.
- A 12-second dual-output runtime passed ASan and UBSan with no diagnostics.
- GCC 15 `-fanalyzer`, release, Tracy, and quality-gate builds are clean.
- Repeated 35- and 75-second rotations showed no EGL/Mesa errors or unbounded
  residency growth.

These gates establish no detected output change for the optimized ordinary
Walle path. The separate exact-static release-process gate below now closes the
canonical Apple-versus-Walle layer-shell boundary without using that
approximate shader.

## Rejected candidates

- Directly mapping the cache file saved only about 2 MiB GTT and slowed
  recurring uploads, so the proven `pread` into the mapped PBO remains.
- `mmap` plus `memcpy` into the PBO increased the controlled HDMI upload median
  from about 7.217 ms to 18.010 ms and was removed.
- Deleting/recreating old textures, `glFinish`, and delayed radeonsi cache trims
  caused VRAM cache growth on every rotation and were removed.
- Redefining a texture to 0×0 did not release its backing BO.
- `glTexSubImage2D` added state complexity without a measured recurring-copy
  improvement and was removed.
- A copy-plus-scissor/two-pass shader idea was not implemented. Mean GFX usage
  is already roughly 0.27%, and changing the render topology is unjustified
  without a bitwise gate.
- A wholesale migration of the existing shader from GLES3 to core OpenGL was
  rejected. Across repeated identical 48-case matrices, 24/100 core runs
  changed one regular-material channel by one code value; 30/30 GLES matrices
  were stable. The exact Apple renderer uses core only behind its own byte
  gate, while the admitted VRAM-optimized production path remains GLES.

## Reproducibility anchors

- Baseline real-config fdinfo SHA-256:
  `92e07e71156870babfc0b42d9fc0b5c4928b142a5c4d5954f151be838f6508e3`
- Candidate real-config fdinfo SHA-256:
  `7f2d07cdefc5bf4338af12f9547e18789d6369bc1f4885c556c566289e687251`
- Baseline Tracy trace SHA-256:
  `6717d680a03548a34fa414362846ff71e19de3f9f6b4848ac22dd43257bd9724`
- Final Tracy trace SHA-256:
  `4511f7d8c323b9800f85dd6b65cdb6407afce6da0acad3ecd08f8a0c0d0a34ff`
- Latency comparison JSON SHA-256:
  `acfefc1460444790b3ff9f47b37689451d0d8bd768655b101f97dc85ced1ebe9`

The latency comparison is reproducible with:

```sh
nix develop --command python analysis/compare_tracy_latency.py \
  artifacts/tracy-baseline-both-transition.csv \
  artifacts/tracy-final-mesa-gated-both-transition.csv \
  --output artifacts/tracy-final-mesa-gated-latency-comparison.json
```

## Apple parity boundary

Formal Liquid Glass parity is still binary-failing because ordinary live
transition frames and the physical display transfer have not passed their
final gates. The direct Retina Mac is currently active at 3456×2234 pixels,
1728×1117 points, and 2× backing scale; GitHub Actions is no longer used for
native capture.

The static regular producer, copy, and six-level mip path now matches an unseen
Retina holdout byte for byte. Walle's corrected AMD circle specialization also
matches four complete 1024×1024 Apple frames with an independently generated
backdrop pyramid: 16,777,216/16,777,216 bytes on the Ryzen integrated GPU and
the same count on the RX 9070 XT. The rejected shortcut differed in 90 bytes
because it reassociated `(point - halfSize) + circleScale`; the admitted shader
preserves that exact order.

Those are shader/input-boundary admissions, not a production claim. The static
private profile is independently reconstructed: all four 258-byte payloads
match Apple exactly under GCC 15 and Clang 21. Main/shadow geometry is exact
for 896/896 components and indices; the generated wallpaper and destination
prepass are exact for 50,331,648/50,331,648 compared bytes; and the four
final-highlight meshes plus 248-byte constructor prefixes are exact for all
1,552 compared bytes. With all of those substitutions active, both AMD gates
still report zero unequal bytes across 33,554,432 combined output bytes.

The independent gate supplies an empty capture-runtime object and omits the
per-capture half lookup. Captured pixels are read only as the final Apple
comparison oracle. A second gate now executes the same complete static path in
a Walle-owned C23 EGL/OpenGL renderer. Its main, shadow, and final-highlight
draws are exact for all four fixtures on both AMD devices: another
33,554,432/33,554,432 checked bytes with zero unequal pixels. The initial GLES
retarget was rejected after its derivative substitution changed 278 bytes in
clear/light; the admitted core OpenGL path preserves the already exact desktop
semantics.

The same C renderer also created an actual EGL window surface on the live Niri
Wayland session, blitted each exact result into the default back buffer, and
read that buffer before presentation. Clear/regular crossed with light/dark is
exact for 16,777,216/16,777,216 Wayland-buffer bytes, with zero unequal pixels
and maximum channel delta zero on the RX 9070 XT. This closes the standalone
C, driver, EGL, and Wayland-window pixel boundary.

The admitted renderer is now linked into the release Walle executable behind
an exact-static diagnostic mode. Walle creates its own
`zwlr_layer_shell_v1`/EGL surface, renders all four profiles, and reads both the
offscreen and layer-shell back buffers. Each matrix is
16,777,216/16,777,216 bytes exact with zero unequal pixels and maximum channel
delta zero. Release/LTO, ASan/UBSan, GCC 15, and Clang 21 all pass. The scoped
result is `analysis/walle_process_static_gl_gate_result.json`.

This closes canonical static production-process integration, not ordinary
live-transition parity. Remaining work is live transition-state production
and Retina color/pixel/compositor transfer. The existing VRAM and latency wins
remain accepted because the protected production shader stayed unchanged.
