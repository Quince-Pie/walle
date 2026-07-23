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
