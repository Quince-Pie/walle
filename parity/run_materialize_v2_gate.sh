#!/usr/bin/env bash
set -euo pipefail

repo_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
cd -- "$repo_dir"

expected_fixture=f6a5427b3535c7a1f9df28ced757d7801d99c52e10217739a0218e39656999ff
expected_manifest=03d9caa3d6e2da6e848d78fc507af043662e32ffcaf1d4add83f0cdfa3dce715
expected_dematerialize_fixture=4f00b39ea6a965f53fb15405b7eb4777f50edefa1cdd1336de6e5a5c6a6fd09d
expected_dematerialize_manifest=381a672df7e40d32ab0a901ba9f0a8d3ed26a69ca73a668f62862ad192f9245f
expected_selected_fixture=5cf88e149c4f6d8733a1d7adda18d29b010e3fb81ec400cdcd58b256e9547c9d
expected_selected_manifest=3cdecb05f3ecc1ff761f57a1f8d2fcffdcba50892933e83eb2343d4baae63cdd
expected_powf_sweep=ade82dab80071f06aa9438043dd97d3ebd5baa56744a69ca20300aad26f46f2a
expected_profile_clamp_powf_sweep=adc847b647eb666e040c51493d3de90a5ec775d6670afd35f7b2f30195d0239e
expected_resolved_color_fixture=1cdb5f11fe7e2d04c02d91081a3bc73ade608fc048bc8c56304c515a8c28c061
expected_resolved_color_manifest=5d4aee7a19b4288e4cb4204d6aca320ceeaf5ac14ccc7048a42625e53f7bf06b
expected_static_pyramid_fixture=6b4e9920fe4cdb7fd18cf91d21a15c28ad67026a2dcbe8ffe8eb5afe10b66e79
expected_static_pyramid_manifest=daa722103be4bd6f3c6f958929baddbe60689ee7c14c8ada03b4f86a1eed043a
expected_transition_profile_fixture=f4db5fa0cf679c4cb98eb0561dfd655b243f5bb68332399fae01de290f317129
expected_transition_profile_manifest=396a009c756c96bd5fdae570da300f9261b1b4a68b819e0bd7df37182abb991f
actual_fixture=$(sha256sum parity/materialize_v2_fixture.bin)
actual_fixture=${actual_fixture%% *}
actual_manifest=$(sha256sum parity/materialize_v2_fixture.json)
actual_manifest=${actual_manifest%% *}
actual_dematerialize_fixture=$(sha256sum parity/dematerialize_v1_fixture.bin)
actual_dematerialize_fixture=${actual_dematerialize_fixture%% *}
actual_dematerialize_manifest=$(sha256sum parity/dematerialize_v1_fixture.json)
actual_dematerialize_manifest=${actual_dematerialize_manifest%% *}
actual_selected_fixture=$(sha256sum parity/selected_region_v1_fixture.bin)
actual_selected_fixture=${actual_selected_fixture%% *}
actual_selected_manifest=$(sha256sum parity/selected_region_v1_fixture.json)
actual_selected_manifest=${actual_selected_manifest%% *}
actual_resolved_color_fixture=$(sha256sum parity/resolved_color_v1_fixture.bin)
actual_resolved_color_fixture=${actual_resolved_color_fixture%% *}
actual_resolved_color_manifest=$(sha256sum parity/resolved_color_v1_fixture.json)
actual_resolved_color_manifest=${actual_resolved_color_manifest%% *}
actual_static_pyramid_fixture=$(sha256sum parity/static_regular_pyramid_v1_fixture.bin)
actual_static_pyramid_fixture=${actual_static_pyramid_fixture%% *}
actual_static_pyramid_manifest=$(sha256sum parity/static_regular_pyramid_v1_fixture.json)
actual_static_pyramid_manifest=${actual_static_pyramid_manifest%% *}
actual_transition_profile_fixture=$(sha256sum parity/transition_profile_v1_fixture.bin)
actual_transition_profile_fixture=${actual_transition_profile_fixture%% *}
actual_transition_profile_manifest=$(sha256sum parity/transition_profile_v1_fixture.json)
actual_transition_profile_manifest=${actual_transition_profile_manifest%% *}
if [[ $actual_fixture != "$expected_fixture"
      || $actual_manifest != "$expected_manifest"
      || $actual_dematerialize_fixture != "$expected_dematerialize_fixture"
      || $actual_dematerialize_manifest != "$expected_dematerialize_manifest"
      || $actual_selected_fixture != "$expected_selected_fixture"
      || $actual_selected_manifest != "$expected_selected_manifest"
      || $actual_resolved_color_fixture != "$expected_resolved_color_fixture"
      || $actual_resolved_color_manifest != "$expected_resolved_color_manifest"
      || $actual_static_pyramid_fixture != "$expected_static_pyramid_fixture"
      || $actual_static_pyramid_manifest != "$expected_static_pyramid_manifest"
      || $actual_transition_profile_fixture != "$expected_transition_profile_fixture"
      || $actual_transition_profile_manifest != "$expected_transition_profile_manifest" ]]; then
    echo "Liquid Glass parity fixture provenance differs" >&2
    exit 1
fi

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
)
sources=(
    parity/liquid_glass_darwin_powf.c
    parity/liquid_glass_materialize.c
    parity/test_liquid_glass_materialize.c
)

dematerialize_sources=(
    parity/liquid_glass_darwin_powf.c
    parity/liquid_glass_materialize.c
    parity/test_liquid_glass_dematerialize.c
)

powf_sources=(
    parity/liquid_glass_darwin_powf.c
    parity/test_liquid_glass_darwin_powf.c
)

gcc "${common[@]}" -O3 -flto=auto "${powf_sources[@]}" -lm \
    -o "$build_dir/powf-release"
"$build_dir/powf-release"

gcc "${common[@]}" -O1 -g -fsanitize=address,undefined \
    -fno-omit-frame-pointer "${powf_sources[@]}" -lm \
    -o "$build_dir/powf-sanitized"
ASAN_OPTIONS=detect_leaks=1 "$build_dir/powf-sanitized"

gcc "${common[@]}" -O3 -flto=auto \
    parity/liquid_glass_darwin_powf.c \
    parity/test_liquid_glass_darwin_powf_sweep.c \
    -lm -o "$build_dir/powf-sweep"
actual_powf_sweep=$("$build_dir/powf-sweep" --emit | sha256sum)
actual_powf_sweep=${actual_powf_sweep%% *}
if [[ $actual_powf_sweep != "$expected_powf_sweep" ]]; then
    echo "Darwin powf exhaustive interval differs" >&2
    exit 1
fi

gcc "${common[@]}" -O3 -fno-fast-math -ffp-contract=off \
    parity/liquid_glass_darwin_powf.c \
    parity/test_liquid_glass_profile_clamp_powf_sweep.c \
    -lm -o "$build_dir/profile-clamp-powf-sweep"
actual_profile_clamp_powf_sweep=$(
    "$build_dir/profile-clamp-powf-sweep" --emit | sha256sum
)
actual_profile_clamp_powf_sweep=${actual_profile_clamp_powf_sweep%% *}
if [[ $actual_profile_clamp_powf_sweep != "$expected_profile_clamp_powf_sweep" ]]; then
    echo "profile clamp Darwin powf exhaustive interval differs" >&2
    exit 1
fi

gcc "${common[@]}" -O3 -flto=auto "${sources[@]}" -lm -o "$build_dir/release"
"$build_dir/release"

gcc "${common[@]}" -O1 -g -fsanitize=address,undefined -fno-omit-frame-pointer \
    "${sources[@]}" -lm -o "$build_dir/sanitized"
ASAN_OPTIONS=detect_leaks=1 "$build_dir/sanitized"

gcc "${common[@]}" -O3 -flto=auto "${dematerialize_sources[@]}" -lm \
    -o "$build_dir/dematerialize-release"
"$build_dir/dematerialize-release"

gcc "${common[@]}" -O1 -g -fsanitize=address,undefined \
    -fno-omit-frame-pointer "${dematerialize_sources[@]}" -lm \
    -o "$build_dir/dematerialize-sanitized"
ASAN_OPTIONS=detect_leaks=1 "$build_dir/dematerialize-sanitized"

selected_sources=(
    parity/liquid_glass_darwin_powf.c
    parity/liquid_glass_materialize.c
    parity/liquid_glass_selected_region.c
    parity/test_liquid_glass_selected_region.c
)

gcc "${common[@]}" -O3 -flto=auto "${selected_sources[@]}" -lm \
    -o "$build_dir/selected-release"
"$build_dir/selected-release"

gcc "${common[@]}" -O1 -g -fsanitize=address,undefined -fno-omit-frame-pointer \
    "${selected_sources[@]}" -lm -o "$build_dir/selected-sanitized"
ASAN_OPTIONS=detect_leaks=1 "$build_dir/selected-sanitized"

resolved_color_sources=(
    parity/liquid_glass_darwin_powf.c
    parity/liquid_glass_resolved_color.c
    parity/test_liquid_glass_resolved_color.c
)

gcc "${common[@]}" -O3 -flto=auto "${resolved_color_sources[@]}" -lm \
    -o "$build_dir/resolved-color-release"
"$build_dir/resolved-color-release"

gcc "${common[@]}" -O1 -g -fsanitize=address,undefined \
    -fno-omit-frame-pointer "${resolved_color_sources[@]}" -lm \
    -o "$build_dir/resolved-color-sanitized"
ASAN_OPTIONS=detect_leaks=1 "$build_dir/resolved-color-sanitized"

static_regular_sources=(
    parity/liquid_glass_darwin_powf.c
    parity/liquid_glass_materialize.c
    parity/liquid_glass_selected_region.c
    parity/liquid_glass_static_regular.c
    parity/test_liquid_glass_static_regular.c
)

gcc "${common[@]}" -O3 -flto=auto "${static_regular_sources[@]}" -lm \
    -o "$build_dir/static-regular-release"
"$build_dir/static-regular-release"

gcc "${common[@]}" -O1 -g -fsanitize=address,undefined \
    -fno-omit-frame-pointer "${static_regular_sources[@]}" -lm \
    -o "$build_dir/static-regular-sanitized"
ASAN_OPTIONS=detect_leaks=1 "$build_dir/static-regular-sanitized"

pyramid_sources=(
    parity/liquid_glass_darwin_powf.c
    parity/liquid_glass_materialize.c
    parity/liquid_glass_selected_region.c
    parity/liquid_glass_static_regular.c
    parity/liquid_glass_pyramid.c
    parity/liquid_glass_raster.c
    parity/liquid_glass_postguard.c
    parity/test_liquid_glass_pyramid.c
)

gcc "${common[@]}" -O3 -flto=auto "${pyramid_sources[@]}" -pthread -lm \
    -o "$build_dir/pyramid-release"
"$build_dir/pyramid-release"

gcc "${common[@]}" -O1 -g -fsanitize=address,undefined \
    -fno-omit-frame-pointer "${pyramid_sources[@]}" -lm \
    -pthread \
    -o "$build_dir/pyramid-sanitized"
ASAN_OPTIONS=detect_leaks=1 "$build_dir/pyramid-sanitized"

static_profile_sources=(
    parity/liquid_glass_static_profile.c
    parity/test_liquid_glass_static_profile.c
)

gcc "${common[@]}" -O3 -flto=auto "${static_profile_sources[@]}" -lm \
    -o "$build_dir/static-profile-release"
"$build_dir/static-profile-release"

gcc "${common[@]}" -O1 -g -fsanitize=address,undefined \
    -fno-omit-frame-pointer "${static_profile_sources[@]}" -lm \
    -o "$build_dir/static-profile-sanitized"
ASAN_OPTIONS=detect_leaks=1 "$build_dir/static-profile-sanitized"

transition_profile_sources=(
    parity/liquid_glass_darwin_powf.c
    parity/liquid_glass_materialize.c
    parity/liquid_glass_resolved_color.c
    parity/liquid_glass_transition_profile.c
    parity/test_liquid_glass_transition_profile.c
)

gcc "${common[@]}" -O3 -flto=auto -fno-fast-math -ffp-contract=off \
    "${transition_profile_sources[@]}" -lm \
    -o "$build_dir/transition-profile-release"
"$build_dir/transition-profile-release"

gcc "${common[@]}" -O1 -g -fno-fast-math -ffp-contract=off \
    -fsanitize=address,undefined -fno-omit-frame-pointer \
    "${transition_profile_sources[@]}" -lm \
    -o "$build_dir/transition-profile-sanitized"
ASAN_OPTIONS=detect_leaks=1 "$build_dir/transition-profile-sanitized"
