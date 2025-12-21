## WALLE (Wallpaper Engine)

a wayland wallpaper engine

### FEATURES

- multi image support
- smooth transition (frost glass) so you don't get flash banged
- fill with cropping area (high, low, center, attention, entropy)
- hot config reload
- per monitor config
- gamemode compatibility (no transition while on)
- random/timeout/speed knobs
- supports for various image format and pdf if you want for some reason (relies on: [libvips](https://github.com/libvips/libvips))


### REQUIREMENT

- c23 compiler with embed support
- inih
- jemalloc
- libglvnd
- systemd-dev (for dbus)
- wayland
- wayland-protocols
- wlr-protocols
- xxHash

### CONFIGURATION

Read config.ini for all options and config format. Place the config in your $XDG_CONFIG_HOME (or ~/.config/walle).


### NOTE ON CACHING

In order to make the transition as smooth as possible, walle places cached bin files in XDG_CACHE_HOME (or ~/.cache/walle). The cache gets cleaned automatically and should not exceed 512mb.
