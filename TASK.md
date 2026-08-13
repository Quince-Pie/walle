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
- the unresolved law is the fused interaction of two simultaneously nonzero,
  opposite-sign products in the hidden p28 setup path;
- the apparent X-lane phase was disproved as a child-ownership confound.

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
