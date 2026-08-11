#!/usr/bin/env bash
set -euo pipefail

expected_fixture=0910b3b604fd3b4fbb66117e5b0c2ff2ba2eb98542fe2785880c05aac84ce693
expected_manifest=02d05b70b454f8841b7e0cedc3ad34600b3b2b57c4ff97775497f89e8775aa98
actual_fixture=$(sha256sum parity/small_clear_background_profile_v1_fixture.bin)
actual_fixture=${actual_fixture%% *}
actual_manifest=$(sha256sum parity/small_clear_background_profile_v1_fixture.json)
actual_manifest=${actual_manifest%% *}
if [[ $actual_fixture != "$expected_fixture" || $actual_manifest != "$expected_manifest" ]]; then
    echo "small-clear background profile fixture or manifest differs" >&2
    exit 1
fi

compiler=${CC:-gcc}
build_dir=$(mktemp -d)
trap 'rm -rf -- "$build_dir"' EXIT

common=(
    -std=c23
    -Wall
    -Wextra
    -Wpedantic
    -Wshadow
    -Wconversion
    -Werror
    -Iparity
    -fno-fast-math
    -ffp-contract=off
)
library_sources=(
    parity/liquid_glass_darwin_powf.c
    parity/liquid_glass_materialize.c
    parity/liquid_glass_resolved_color.c
    parity/liquid_glass_transition_profile.c
)

"$compiler" "${common[@]}" -O3 -flto \
    "${library_sources[@]}" \
    parity/test_liquid_glass_small_clear_background_profile.c \
    -lm -o "$build_dir/small-clear-release"
"$build_dir/small-clear-release"

"$compiler" "${common[@]}" -O1 -g -fsanitize=address,undefined \
    -fno-omit-frame-pointer \
    "${library_sources[@]}" \
    parity/test_liquid_glass_small_clear_background_profile.c \
    -lm -o "$build_dir/small-clear-sanitized"
ASAN_OPTIONS=detect_leaks=1 "$build_dir/small-clear-sanitized"

"$compiler" "${common[@]}" -O3 -flto \
    "${library_sources[@]}" \
    parity/test_liquid_glass_transition_profile.c \
    -lm -o "$build_dir/ordinary-release"
"$build_dir/ordinary-release"
