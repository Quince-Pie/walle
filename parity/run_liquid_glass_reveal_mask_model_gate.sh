#!/bin/sh
set -eu

root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
build_dir="$root/build/parity-reveal-mask-model"
table="$root/artifacts/apple-float-intrinsics-r8-30556057571.bin"
expected_table=fff71cc0d4428677ca5bc58b91212a7166b701e4efe504c3d71cab70846d0449

actual_table=$(sha256sum "$table" | awk '{print $1}')
test "$actual_table" = "$expected_table"

mkdir -p "$build_dir"
: "${CC:=gcc}"
warnings="-std=c23 -Wall -Wextra -Wpedantic -Wshadow -Wconversion -Wsign-conversion"
sources="$root/parity/liquid_glass_reveal_mask_model.c $root/parity/test_liquid_glass_reveal_mask_model.c"

# shellcheck disable=SC2086
"$CC" $warnings -Werror -O3 -flto=auto -fstack-protector-strong \
    -D_FORTIFY_SOURCE=3 -I"$root/parity" $sources -lm \
    -o "$build_dir/release"
"$build_dir/release" "$table"

if "$CC" -std=c23 -fanalyzer -x c -c /dev/null -o /dev/null >/dev/null 2>&1; then
    # shellcheck disable=SC2086
    "$CC" $warnings -Werror -O2 -fanalyzer -I"$root/parity" $sources -lm \
        -o "$build_dir/analyzed"
    "$build_dir/analyzed" "$table"
fi

# shellcheck disable=SC2086
"$CC" $warnings -Werror -O1 -g3 -fno-omit-frame-pointer \
    -fsanitize=address,undefined -fno-sanitize-recover=all \
    -I"$root/parity" $sources -lm -o "$build_dir/sanitized"
ASAN_OPTIONS=detect_leaks=1:halt_on_error=1 \
UBSAN_OPTIONS=halt_on_error=1 \
    "$build_dir/sanitized" "$table"

printf 'fastSqrtSha256=%s\n' "$actual_table"
