#!/usr/bin/env bash
# Render walle's material over an arbitrary background and dump the composed
# bytes, so its interior can be compared against Apple's capture of the same
# background.
#   $1 background.png   $2 out.bgra   $3 variant
# env: APPEARANCE=light|dark|auto   TINT=#RRGGBB|none   PROGRESS=<0..1>
set -euo pipefail
root=$(CDPATH="" cd -- "$(dirname -- "$0")/.." && pwd)
bg=$1; out=$2; variant=$3
g=$(mktemp -d); labwc=${LABWC:-/run/current-system/sw/bin/labwc}; pid=
cleanup(){ [[ -n "$pid" ]] && kill "$pid" 2>/dev/null; rm -rf -- "$g"; }
trap cleanup EXIT INT TERM
install -d -m 700 -- "$g/rt" "$g/ch/labwc" "$g/cap"

{
  echo "[walle]"
  echo "vulkan_device = auto"
  echo ""
  echo "[default]"
  echo "files ="
  printf '\tstretch:%s\n' "$bg"
  printf '\tstretch:%s\n' "$bg"
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

timeout 180s env XDG_RUNTIME_DIR="$g/rt" WAYLAND_DISPLAY=wayland-0 \
  WALLE_COMPOSE_MATERIAL=1 \
  "$root/build/bin/release/walle" --config "$g/w.ini" \
  --reveal-mask-process-capture "$g/cap" \
  --reveal-mask-process-capture-progress "${PROGRESS:-0.659}" \
  >"$g/walle.log" 2>&1 || { tail -8 "$g/walle.log" >&2; exit 1; }
cp "$g/cap/composition-state-0000.bgra" "$out"
