#!/usr/bin/env bash
set -euo pipefail
sources=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
project=$(cd -- "$sources/../.." && pwd)
out="$project/build/tests/lifecycle"
mkdir -p "$out"
read -r -a flags <<< "$(pkg-config --cflags --libs vips wayland-client libsystemd liburing inih libxxhash)"
${CC:-cc} -std=c23 -O2 -fPIC -shared -fvisibility=hidden -ffunction-sections -fdata-sections -Wl,--gc-sections \
    -I"$project" -I"$project/material" "$sources/trace.c" "${flags[@]}" -ldl -pthread -o "$out/trace.so"
protocol=$(pkg-config --variable=pkgdatadir wlr-protocols)/unstable/wlr-output-management-unstable-v1.xml
wayland-scanner client-header "$protocol" "$out/output-management.h"
wayland-scanner private-code "$protocol" "$out/output-management.c"
read -r -a wayland <<< "$(pkg-config --cflags --libs wayland-client)"
${CC:-cc} -std=c23 -O2 -Wall -Wextra -Wpedantic -I"$out" \
    "$sources/output_control.c" "$out/output-management.c" "${wayland[@]}" -o "$out/output-control"
sha256sum "$project/walle.c" > "$out/app-source.sha256"
