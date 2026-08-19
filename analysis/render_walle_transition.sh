#!/usr/bin/env bash
# Render a real two-wallpaper transition headless and keep every captured
# state, so the reveal can be looked at the way the user sees it rather than
# the way the material grid reads it.
#   $1 outgoing.png  $2 incoming.png  $3 out-dir  $4 variant
# env: PROGRESS=v[,v...]  APPEARANCE  TINT  COMPOSE=1 (shipped material)
set -euo pipefail
root=$(CDPATH="" cd -- "$(dirname -- "$0")/.." && pwd)
out_png=$1; in_png=$2; dest=$3; variant=${4:-clear}
g=$(mktemp -d); labwc=${LABWC:-/run/current-system/sw/bin/labwc}; pid=
cleanup(){ [[ -n "$pid" ]] && kill "$pid" 2>/dev/null; rm -rf -- "$g"; }
trap cleanup EXIT INT TERM
install -d -m 700 -- "$g/rt" "$g/ch/labwc"
install -d -- "$dest"

{
  echo "[walle]"
  echo "vulkan_device = auto"
  echo ""
  echo "[default]"
  echo "files ="
  printf '\tstretch:%s\n' "$out_png"
  printf '\tstretch:%s\n' "$in_png"
  echo "timeout = 0"
  echo "transition = true"
  echo "transition_duration = 1"
  echo "transition_variant = $variant"
  echo "appearance = ${APPEARANCE:-auto}"
  echo "tint = ${TINT:-none}"
  echo "randomize = false"
  echo "gamemode = false"
} >"$g/w.ini"

env -u WAYLAND_DISPLAY XDG_RUNTIME_DIR="$g/rt" XDG_CONFIG_HOME="$g/ch" \
  WLR_BACKENDS=headless WLR_HEADLESS_OUTPUTS=1 WLR_RENDERER=vulkan \
  WLR_LIBINPUT_NO_DEVICES=1 "$labwc" -d >"$g/labwc.log" 2>&1 &
pid=$!
for _ in $(seq 1 200); do [[ -S "$g/rt/wayland-0" ]] && break; sleep 0.05; done

extra=()
[[ -n "${MATERIAL_PROGRESS:-}" ]] \
  && extra+=(--reveal-mask-process-capture-material-progress "$MATERIAL_PROGRESS")
[[ -n "${BACKING_SCALE:-}" ]] \
  && extra+=(--reveal-mask-process-capture-backing-scale "$BACKING_SCALE")

timeout 3000s env XDG_RUNTIME_DIR="$g/rt" WAYLAND_DISPLAY=wayland-0 \
  ${COMPOSE:+WALLE_COMPOSE_MATERIAL=1} \
  "$root/build/bin/release/walle" --config "$g/w.ini" \
  --reveal-mask-process-capture "$dest" \
  ${PROGRESS:+--reveal-mask-process-capture-progress "$PROGRESS"} \
  "${extra[@]}" \
  >"$g/walle.log" 2>&1 || { tail -20 "$g/walle.log" >&2; exit 1; }
cp "$g/walle.log" "$dest/walle.log"
echo "captured: $(ls "$dest" | wc -l) files in $dest"
