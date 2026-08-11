#!/bin/sh
set -eu

root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
artifact=${1:-"$root/artifacts/local-walle-regular-controlled-backdrop-1cd9af4-run1-v1"}
calibration=${2:-"$root/lg-test/Analysis"}
build_dir="$root/build/parity-dynamic-backdrop"
mkdir -p "$build_dir"

: "${CC:=gcc}"
"$CC" -std=c23 -Wall -Wextra -Wpedantic -Wshadow -Wimplicit-fallthrough \
    -O3 -flto=auto -fno-plt -fstack-protector-strong -D_FORTIFY_SOURCE=3 \
    -I"$root" \
    "$root/parity/verify_liquid_glass_dynamic_backdrop.c" \
    "$root/parity/liquid_glass_pyramid.c" \
    "$root/parity/liquid_glass_raster.c" \
    "$root/parity/liquid_glass_postguard.c" \
    "$root/parity/liquid_glass_transition_frame.c" \
    "$root/parity/liquid_glass_selected_region.c" \
    "$root/parity/liquid_glass_transition_profile.c" \
    "$root/parity/liquid_glass_materialize.c" \
    "$root/parity/liquid_glass_darwin_powf.c" \
    "$root/parity/liquid_glass_resolved_color.c" \
    -lm -lpthread -lz \
    -o "$build_dir/verify-dynamic-backdrop"

"$build_dir/verify-dynamic-backdrop" "$artifact" "$calibration"
