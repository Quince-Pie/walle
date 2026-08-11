#!/bin/sh
set -eu

root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
build_dir="$root/build/parity-reveal-stage"
production_object="$build_dir/liquid_glass_reveal_stage.o"
interposition_object="$build_dir/liquid_glass_reveal_stage-interposition-audit.o"
test_binary="$build_dir/test-liquid-glass-reveal-stage"
analyzed_binary="$build_dir/test-liquid-glass-reveal-stage-analyzed"
sanitized_binary="$build_dir/test-liquid-glass-reveal-stage-sanitized"
protected_shader="$root/shaders/frag.glsl"
protected_sha256=6489828f12de599da9633d6183266a81b71ed846a1b03c03cb4eb9c23639352d

actual_sha256=$(sha256sum "$protected_shader" | awk '{print $1}')
test "$actual_sha256" = "$protected_sha256"

mkdir -p "$build_dir"
: "${CC:=gcc}"
warnings="-std=c23 -Wall -Wextra -Wpedantic -Wshadow -Wconversion -Wimplicit-fallthrough"
flags="$warnings -O2 -fstack-protector-strong -D_FORTIFY_SOURCE=3"

# Compile the actual production translation unit: no test authority symbol is
# present and authority acquisition remains fail-closed.
# shellcheck disable=SC2086
"$CC" $flags -I"$root/parity" -c \
    "$root/parity/liquid_glass_reveal_stage.c" -o "$production_object"
production_symbols=$(nm -g --defined-only "$production_object" | awk 'NF >= 3 { print $3 }' | LC_ALL=C sort)
expected_symbols=$(printf '%s\n' \
    walle_lg_reveal_stage_authority_acquire \
    walle_lg_reveal_stage_route)
if test "$production_symbols" != "$expected_symbols"; then
    echo "unexpected production reveal-stage symbol surface:" >&2
    printf '%s\n' "$production_symbols" >&2
    exit 1
fi
# Compile without inlining so a future internal call through the exported
# acquire symbol remains visible as a relocation instead of disappearing by
# optimization.
# shellcheck disable=SC2086
"$CC" $warnings -O1 -fno-inline -fsemantic-interposition -fPIC -I"$root/parity" -c \
    "$root/parity/liquid_glass_reveal_stage.c" -o "$interposition_object"
if objdump -r "$interposition_object" \
    | grep -Fq walle_lg_reveal_stage_authority_acquire; then
    echo "production authority guard calls an interposable acquire symbol" >&2
    exit 1
fi

# The separately compiled unit-test build gets an internal capability solely
# to prove the post-approval single-texture fan-out contract.
# shellcheck disable=SC2086
"$CC" $flags -DWALLE_LG_REVEAL_STAGE_TESTING=1 -I"$root/parity" \
    "$root/parity/liquid_glass_reveal_stage.c" \
    "$root/parity/test_liquid_glass_reveal_stage.c" \
    -o "$test_binary"
"$test_binary"

# Analyze both the guarded implementation and the authority-enabled test path
# when the selected C23 compiler provides GCC's static analyzer.
if "$CC" -std=c23 -fanalyzer -x c -c /dev/null -o /dev/null >/dev/null 2>&1; then
    # shellcheck disable=SC2086
    "$CC" $flags -fanalyzer -DWALLE_LG_REVEAL_STAGE_TESTING=1 -I"$root/parity" \
        "$root/parity/liquid_glass_reveal_stage.c" \
        "$root/parity/test_liquid_glass_reveal_stage.c" \
        -o "$analyzed_binary"
    "$analyzed_binary"
fi

# Exercise pointer identity, all rejection routes, and exact descriptor fan-out
# under both AddressSanitizer and UndefinedBehaviorSanitizer.
# shellcheck disable=SC2086
"$CC" $warnings -O1 -g3 -fno-omit-frame-pointer \
    -fsanitize=address,undefined -fno-sanitize-recover=all \
    -DWALLE_LG_REVEAL_STAGE_TESTING=1 -I"$root/parity" \
    "$root/parity/liquid_glass_reveal_stage.c" \
    "$root/parity/test_liquid_glass_reveal_stage.c" \
    -o "$sanitized_binary"
ASAN_OPTIONS=detect_leaks=1:halt_on_error=1 \
UBSAN_OPTIONS=halt_on_error=1 \
    "$sanitized_binary"

actual_sha256=$(sha256sum "$protected_shader" | awk '{print $1}')
test "$actual_sha256" = "$protected_sha256"
printf 'protectedShaderSha256=%s\n' "$actual_sha256"
