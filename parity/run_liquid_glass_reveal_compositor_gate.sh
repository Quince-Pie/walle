#!/bin/sh
set -eu

root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
build_dir="$root/build/parity-reveal-compositor"
object="$build_dir/liquid_glass_reveal_compositor.o"
binary="$build_dir/test-liquid-glass-reveal-compositor"
protected_shader="$root/shaders/frag.glsl"
protected_sha256=6489828f12de599da9633d6183266a81b71ed846a1b03c03cb4eb9c23639352d

test "$(sha256sum "$protected_shader" | awk '{print $1}')" = "$protected_sha256"
mkdir -p "$build_dir"
: "${CC:=gcc}"
warnings="-std=c23 -Wall -Wextra -Wpedantic -Wshadow -Wconversion -Wimplicit-fallthrough"
flags="$warnings -O2 -fstack-protector-strong -D_FORTIFY_SOURCE=3"

# shellcheck disable=SC2086
"$CC" $flags -I"$root/parity" -c \
    "$root/parity/liquid_glass_reveal_compositor.c" -o "$object"
symbols=$(nm -g --defined-only "$object" | awk 'NF >= 3 { print $3 }' | LC_ALL=C sort)
expected=$(printf '%s\n' \
    walle_lg_reveal_compositor_create \
    walle_lg_reveal_compositor_destroy \
    walle_lg_reveal_compositor_draw)
test "$symbols" = "$expected"

# shellcheck disable=SC2086
"$CC" $flags -I"$root/parity" \
    "$root/parity/liquid_glass_reveal_compositor.c" \
    "$root/parity/test_liquid_glass_reveal_compositor.c" \
    $(pkg-config --cflags --libs opengl egl) \
    -o "$binary"
LIBGL_ALWAYS_SOFTWARE=1 "$binary"

test "$(sha256sum "$protected_shader" | awk '{print $1}')" = "$protected_sha256"
