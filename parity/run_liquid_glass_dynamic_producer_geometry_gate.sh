#!/bin/sh
set -eu

root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
build_dir="$root/build/parity-dynamic-producer"
mkdir -p "$build_dir"

: "${CC:=gcc}"
"$CC" -std=c23 -Wall -Wextra -Wpedantic -Wshadow -Wimplicit-fallthrough \
    -O3 -flto=auto -fno-plt -fstack-protector-strong -D_FORTIFY_SOURCE=3 \
    -I"$root" \
    "$root/parity/verify_liquid_glass_dynamic_producer_geometry.c" \
    "$root/parity/liquid_glass_transition_frame.c" \
    "$root/parity/liquid_glass_selected_region.c" \
    "$root/parity/liquid_glass_transition_profile.c" \
    "$root/parity/liquid_glass_materialize.c" \
    "$root/parity/liquid_glass_darwin_powf.c" \
    "$root/parity/liquid_glass_resolved_color.c" \
    -lm \
    -o "$build_dir/verify-dynamic-producer"

"$build_dir/verify-dynamic-producer"
