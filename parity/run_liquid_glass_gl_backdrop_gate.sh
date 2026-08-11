#!/bin/sh
set -eu

root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
artifact=${1:-"$root/artifacts/local-walle-regular-controlled-backdrop-1cd9af4-run1-v1"}
calibration=${2:-"$root/lg-test/Analysis"}
shader=${3:-"$root/parity/liquid_glass_backdrop.comp.glsl"}
build_dir="$root/build/parity-gl-backdrop"
mkdir -p "$build_dir"

: "${CC:=gcc}"
"$CC" -std=c23 -Wall -Wextra -Wpedantic -Wshadow -Wimplicit-fallthrough \
    -O3 -flto=auto -fno-plt -fstack-protector-strong -D_FORTIFY_SOURCE=3 \
    -I"$root" \
    "$root/parity/verify_liquid_glass_gl_backdrop.c" \
    "$root/parity/liquid_glass_gl_pyramid.c" \
    "$root/parity/liquid_glass_pyramid.c" \
    "$root/parity/liquid_glass_raster.c" \
    "$root/parity/liquid_glass_postguard.c" \
    "$root/parity/liquid_glass_transition_frame.c" \
    "$root/parity/liquid_glass_selected_region.c" \
    "$root/parity/liquid_glass_transition_profile.c" \
    "$root/parity/liquid_glass_materialize.c" \
    "$root/parity/liquid_glass_darwin_powf.c" \
    "$root/parity/liquid_glass_resolved_color.c" \
    $(pkg-config --cflags --libs opengl egl) -lm -lpthread -lz \
    -o "$build_dir/verify-gl-backdrop"

"$build_dir/verify-gl-backdrop" "$artifact" "$calibration" "$shader"
