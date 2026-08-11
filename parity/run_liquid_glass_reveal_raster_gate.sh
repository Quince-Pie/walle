#!/bin/sh
set -eu

root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
build_dir="$root/build/parity-reveal-raster"
p25="$root/parity/raster_p25_selector_ceil_bits.bin"
apple_original="$root/artifacts/apple-float-intrinsics-r8-30556057571.bin"
apple_packed="$root/parity/apple_fast_sqrt_correction_nibbles.bin"
expected_p25=9fbc083dfd9c89fc0bcdc89308acfc4530d408e93789a7dab89ee59ff60a198f
expected_apple_original=fff71cc0d4428677ca5bc58b91212a7166b701e4efe504c3d71cab70846d0449
expected_apple_packed=dcd882a8af21ac9f2c0f82a3239d6d5f247e2eb5b3535348f6931b65c41f23b1

actual_p25=$(sha256sum "$p25" | awk '{print $1}')
actual_apple_original=$(sha256sum "$apple_original" | awk '{print $1}')
actual_apple_packed=$(sha256sum "$apple_packed" | awk '{print $1}')
test "$actual_p25" = "$expected_p25"
test "$actual_apple_original" = "$expected_apple_original"
test "$actual_apple_packed" = "$expected_apple_packed"

mkdir -p "$build_dir"
: "${CC:=gcc}"
warnings="-std=c23 -Wall -Wextra -Wpedantic -Wshadow -Wconversion -Wsign-conversion"
sources="$root/parity/liquid_glass_postguard.c $root/parity/liquid_glass_raster.c $root/parity/liquid_glass_reveal_mask_model.c $root/parity/test_liquid_glass_reveal_raster.c"

# shellcheck disable=SC2086
"$CC" $warnings -Werror -O3 -flto=auto -fstack-protector-strong \
    -D_FORTIFY_SOURCE=3 -I"$root/parity" $sources -lm \
    -o "$build_dir/release"
"$build_dir/release" "$p25" "$apple_original" "$apple_packed"

if "$CC" -std=c23 -fanalyzer -x c -c /dev/null -o /dev/null >/dev/null 2>&1; then
    # shellcheck disable=SC2086
    "$CC" $warnings -Werror -O2 -fanalyzer -I"$root/parity" $sources -lm \
        -o "$build_dir/analyzed"
    "$build_dir/analyzed" "$p25" "$apple_original" "$apple_packed"
fi

# shellcheck disable=SC2086
"$CC" $warnings -Werror -O1 -g3 -fno-omit-frame-pointer \
    -fno-wrapv -fsanitize=address,undefined -fno-sanitize-recover=all \
    -I"$root/parity" $sources -lm -o "$build_dir/sanitized"
ASAN_OPTIONS=detect_leaks=1:halt_on_error=1 \
UBSAN_OPTIONS=halt_on_error=1 \
    "$build_dir/sanitized" "$p25" "$apple_original" "$apple_packed"

printf 'revealRasterP25Sha256=%s\n' "$actual_p25"
printf 'appleArithmeticOriginalSha256=%s\n' "$actual_apple_original"
printf 'appleFastSqrtPackedSha256=%s\n' "$actual_apple_packed"
