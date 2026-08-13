# WALLE

A Linux/Wayland wallpaper engine with a recovered Liquid Glass transition.

## Renderer

Walle has one rendering backend and no fallback:

- Vulkan 1.4 is a hard runtime requirement;
- shaders are authored in Slang 2026 and compiled offline to SPIR-V 1.6;
- the SPIR-V uses the Vulkan memory model and is validated for Vulkan 1.4 at
  build time;
- rendering uses dynamic rendering, synchronization2, `vkQueueSubmit2`, and
  the Vulkan 1.4 maintenance6 forms of descriptor-set binding and push
  constants;
- image transitions use the generic Vulkan 1.3+ `ATTACHMENT_OPTIMAL` and
  `READ_ONLY_OPTIMAL` layouts;
- maintenance5 feeds the embedded, build-validated SPIR-V directly into
  pipeline creation, without creating temporary shader-module objects;
- there are no render passes, framebuffers, runtime shader compilers, EGL,
  OpenGL, or OpenGL ES paths.

The recovered reveal-mask implementation currently agrees with the retained
65-frame, 2048×2048 corpus at 272,629,669 of 272,629,760 samples:
**99.99996662139893%**, with 91 one-code residuals and 52/65 exact frames.
The actual Walle layer-shell process reproduces the canonical candidate
inventory on both the integrated Radeon and RX 9070 XT:

```text
9062b7bfde617f88638c9b48fdb8ace7b6f91b4518d54c5a6e54abcb51e93644
```

This is the best public-input algorithm recovered so far, not a per-state or
per-pixel correction table. The remaining 91 samples are outside the Vulkan
migration: 82 are associated with Apple's unrecovered arbitrary post-clip
triangle setup coefficients and nine with the already isolated physical
presentation transfer.

## GPU and VRAM design

Walle is a long-running wallpaper process, so the renderer deliberately keeps
its persistent GPU footprint small:

- one FIFO swapchain at the compositor's minimum image count;
- only the current wallpaper pair is retained while idle;
- the incoming wallpaper pair, R8_UINT reveal mask, owner/axis data,
  descriptors, and optional readback are transition-lived and destroyed on
  promotion or abort;
- one shared 4 MiB nibble-packed Apple fast-sqrt table per Vulkan device;
- vertex, index, owner, mapping, and RG32 axis data share one allocation;
- host-visible device-local memory is used directly when available; otherwise
  one transition-lived staging allocation is used;
- one frame is in flight per output, one reveal draw and one composition draw
  are submitted per transition frame.

At 2048×2048 the reveal mask is exactly 4 MiB. No RGBA intermediate is used.
Wallpaper images use sRGB textures; the swapchain uses BGRA8 UNORM with the
composition shader performing the final sRGB transfer exactly once.

## Features

- multiple images and directories;
- per-output configuration;
- `clear` and `regular` Liquid Glass material variants;
- fill/stretch/fit and attention/entropy crop modes;
- native-pixel integer HiDPI rendering;
- hot configuration reload;
- GameMode integration;
- io_uring event core and coalesced timers;
- libvips image/PDF decoding and an atomically published, LRU-trimmed cache.

## Requirements

- Linux kernel 5.15 or newer;
- a Vulkan 1.4 driver with Wayland presentation, geometry shaders, shader
  int64, dynamic rendering, synchronization2, maintenance5, maintenance6,
  and the Vulkan memory-model features;
- a C23 compiler with `#embed` support (GCC 15 in the Nix build);
- Slang and SPIR-V Tools at build time;
- Wayland, wayland-protocols, wlr-protocols, libvips, inih, jemalloc,
  liburing, libsystemd, and xxHash.

The compositor must implement `zwlr_layer_shell_v1`.

## Build

```sh
nix develop
make -j
```

The release package is built with:

```sh
nix build
```

Useful verification targets:

```sh
make reveal-mask-model-gate reveal-raster-gate
make MODE=release reveal-best-known-process-gate
make MODE=release ANALYZE=1
make MODE=release SANITIZER=1
```

The process gate launches an isolated headless Wayland compositor, enables
Vulkan validation, renders/presents 65 normal Walle frames, reads back only the
R8 mask for scoring, and requires the 91-residual canonical inventory.

## Usage

```text
walle [-c /path/to/config.ini] [--help] [--version]
```

For the deterministic parity diagnostic only:

```text
walle -c CONFIG --reveal-mask-process-capture EMPTY_DIRECTORY
```

The capture option is not a second renderer. It state-steps the same Vulkan
path and writes its 65 top-left, row-major R8 masks after successful presents.

See `config.ini` for configuration syntax. Place it in
`$XDG_CONFIG_HOME/walle/config.ini` or pass it with `-c`. The inih parser's
line limit is 199 characters, so keep individual path lines below that limit.

## Research handoff

`TASK.md` records the exact implementation boundary, validation receipts,
remaining Apple setup problem, and the files future work should touch.
