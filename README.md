## WALLE (Wallpaper Engine)

a wayland wallpaper engine

### FEATURES

- multi image support
- directory support
- Liquid Glass transition fitted to structured-light measurements of REAL
  macOS 26 `glassEffect` renders (tone transfer, blur MTF, refraction
  displacement field), plus the HIG-derived dynamic highlight layer, so you
  don't get flash banged
- both Liquid Glass variants, per output (`transition_variant`): `clear`
  (the measured ~50% sRGB veil over a mega-blur, with the real outward
  edge-lensing profile) and `regular` (the opaque platter: white over light
  content, dark over dark, hue-washed by the wallpaper)
- recovered reveal topology: a table-free public-input constructor selects the
  observed border-grid or compact-visible-arc mesh, renders an R8 mask, then
  uses that mask as the sole transition coverage input. This is Walle's only
  reveal implementation. The current recorded offline CPU reference agrees
  at 272,629,669 of 272,629,760 channel-0 mask samples (91 one-code residuals);
  this is intentionally named `best-known`, not `exact`. The production model
  now reproduces that same score through Mesa/GLES on both the integrated
  Radeon and RX 9070 XT. Its one-draw OpenGL ES 3.2 path combines recovered
  canonical post-guard children with P25/AGX axes and exact current/XOR-helper
  ownership, then applies the admitted Apple fast-square-root correction and
  binary16 round-to-nearest-even transfer. The remaining mask boundary is
  Apple's hardware-specific setup law for arbitrary non-axis-separable clipped
  children. A deterministic 65-state diagnostic
  run of the Walle executable produces the same R8 inventory byte for byte (65
  normal composition swaps and 64 frame callbacks). Composed-RGBA and
  physical-presentation scoring remain separate.
- fill with cropping area (high, low, center, attention, entropy)
- hot config reload (including adding/removing output sections at runtime)
- per monitor config
- HiDPI aware (integer `wl_output.scale`; buffers are rendered at native pixels)
- gamemode compatibility (no transition while on)
- random/timeout/speed knobs
- io_uring event core: single wait syscall per wakeup, zero idle overhead,
  timers coalesced onto a whole-second grid so multi-monitor setups wake once
- supports for various image format and pdf if you want for some reason
  (relies on: [libvips](https://github.com/libvips/libvips))

### REQUIREMENT

- linux kernel >= 5.15 (io_uring with NODROP + EXT_ARG; 6.0+ adds
  single-issuer/sync-cancel fast paths, probed at runtime)
- c23 compiler with embed support
- inih
- jemalloc
- libglvnd
- OpenGL ES 3.2
- liburing (>= 2.4)
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
`nix develop` so the shell picks up liburing and the matching toolchain).

### USAGE

```
walle [-c /path/to/config.ini] [--help] [--version]
```

### CONFIGURATION

Read config.ini for all options and config format. Place the config in your
$XDG_CONFIG_HOME (or ~/.config/walle), or pass one explicitly with `-c`.

Note: the system inih parser limits a config line to 199 characters; keep
per-line paths under that (use directories for long collections).

### NOTE ON CACHING

In order to make the transition as smooth as possible, walle places cached
bin files in XDG_CACHE_HOME (or ~/.cache/walle). Entries are published
atomically (a crash can never leave a torn cache file), evicted LRU, and the
cache is trimmed back below 512 MB in the background — at startup and
periodically while the daemon runs.
