## WALLE (Wallpaper Engine)

a wayland wallpaper engine

### FEATURES

- multi image support
- directory support
- Liquid Glass transition with a byte-locked production shader while the
  replacement optical model is fitted against validated macOS 26 captures
- both Liquid Glass variants, per output (`transition_variant`): `clear` and
  `regular`
- fill with cropping area (high, low, center, attention, entropy)
- hot config reload (including adding/removing output sections at runtime)
- per monitor config
- HiDPI aware (integer `wl_output.scale`; buffers are rendered at native pixels)
- gamemode compatibility (no transition while on)
- random/timeout/speed knobs
- raw Linux 6.18 io_uring reactor: one wait boundary, fixed files, direct
  signalfd/inotify reads, multishot Wayland input, cross-thread `MSG_RING`,
  and one shared absolute deadline
- supports for various image format and pdf if you want for some reason
  (relies on: [libvips](https://github.com/libvips/libvips))

### REQUIREMENT

- linux kernel >= 6.18 (the selected io_uring setup/features/opcodes are
  queried and probed at runtime)
- c23 compiler with embed support
- inih
- jemalloc
- libglvnd
- libvips
- systemd-dev (for dbus)
- wayland
- wayland-protocols
- wlr-protocols
- xxHash

The compositor must implement `zwlr_layer_shell_v1` (wlroots-based
compositors, KWin, Mir...; not GNOME).

**NixOS note:** binaries built from this flake load the *system's* GL driver
(`/run/opengl-driver`) at runtime, so the flake's nixpkgs must not be older
than the system's — an outdated `flake.lock` produces
`FATAL: no EGL display` because the pinned glibc cannot load the system's
mesa. If you see that error, run `nix flake update` and rebuild (and re-enter
`nix develop` so the shell picks up the matching toolchain and Linux UAPI).

### USAGE

```
walle [-c /path/to/config.ini] [--help] [--version]
```

### CONFIGURATION

Read config.ini for all options and config format. Place the config in your
$XDG_CONFIG_HOME (or ~/.config/walle), or pass one explicitly with `-c`.

Note: the system inih parser limits a config line to 199 characters; keep
per-line paths under that (use directories for long collections).

### PROFILING

Every flake development shell provides Tracy's installed headers, client
library, capture tool, and viewer through the compiler wrapper. No source
location or Nix store path is passed through the project build.
Instrumentation is opt-in and is not linked into ordinary release or package
builds.

```sh
nix develop
make MODE=release TRACY=1
./build/bin/release-tracy/walle -c /path/to/config.ini

# In another development shell:
tracy-capture -o build/walle.tracy -f -s 10
```

The trace isolates image realization, lossless texture uploads, main-thread
result activation, draw submission, buffer swaps, and complete transition
frames.

### LIQUID GLASS EVIDENCE GATE

The macOS capture audit and accepted measurements are documented in
[LIQUID_GLASS_EVIDENCE.md](LIQUID_GLASS_EVIDENCE.md). Evaluate the constants
in the current shader and renderer directly against an artifact with:

```sh
nix develop
python analysis/liquid_glass_compare.py \
  artifacts/liquid-glass-captures-<run-id>-all.zip \
  --report artifacts/walle-vs-apple-<run-id>.json
python analysis/liquid_glass_pixel_gate.py \
  artifacts/liquid-glass-captures-30326591212-all.zip \
  --baseline artifacts/walle-rendered-pixel-baseline-30326591212.json \
  --report artifacts/walle-rendered-pixel-candidate.json
python analysis/liquid_glass_transfer_fit.py \
  artifacts/liquid-glass-measurements-<run-id>.json \
  --report artifacts/liquid-glass-transfer-fit-<run-id>.json
python analysis/liquid_glass_spatial_fit.py \
  artifacts/liquid-glass-captures-<run-id>-static.zip \
  artifacts/liquid-glass-measurements-<run-id>.json \
  --report artifacts/liquid-glass-spatial-fit-<run-id>.json
python -m unittest discover -s analysis -v
```

For an analytical candidate, pass the preserved analytical report with
`--baseline`; any increase in one of the 56 protected analytical errors fails.
The rendered gate compiles the selected GLSL, renders all 156 real presentation
states, trains on the forward source traversal, and withholds the reverse
traversal. Any increase in any of 16 pixel, edge, or perceptual errors in any
frame fails that gate. The metric/renderer source, evidence, selection,
dependency versions, Mesa, and GPU must also match the baseline.

### NOTE ON CACHING

In order to make the transition as smooth as possible, walle places cached
bin files in XDG_CACHE_HOME (or ~/.cache/walle). Entries are published
atomically (a crash can never leave a torn cache file), evicted LRU, and the
cache is trimmed back below 512 MB in the background — at startup and
after each 128 MB of new cache growth. Cache hits are mmap-prefaulted by the
render worker and uploaded directly; these files consume disk/page-cache
space, not GPU VRAM.

See [EFFICIENCY_ANALYSIS.md](EFFICIENCY_ANALYSIS.md) for the measured VRAM
breakdown, cache decision, and raw io_uring design.
