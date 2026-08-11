#!/usr/bin/env bash
set -euo pipefail

project_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$project_root"

wayland_display=${1:-${WAYLAND_DISPLAY:-wayland-1}}
shader_directory=build/generated/liquid-glass/desktop
fixture_directory=build/generated/liquid-glass/static-fixtures
renderer=build/bin/quality/render_walle_exact_static_gl

mkdir -p "$shader_directory" "$fixture_directory" "$(dirname -- "$renderer")"

nix develop ./lg-test --command env PYTHONPATH=analysis:lg-test/Analysis \
    python analysis/generate_walle_exact_shaders.py \
    --api desktop --output "$shader_directory" >/dev/null
nix develop ./lg-test --command env PYTHONPATH=analysis:lg-test/Analysis \
    python analysis/generate_walle_exact_static_fixtures.py \
    --output "$fixture_directory" >/dev/null

nix develop --command bash -lc \
    'gcc -std=c23 -Wall -Wextra -Wpedantic -Wshadow -Wconversion \
        -Wsign-conversion -Werror -O2 -I. \
        $(pkg-config --cflags egl opengl wayland-client wayland-egl) \
        parity/render_walle_exact_static_gl.c protocols/xdg-shell.c \
        $(pkg-config --libs egl opengl wayland-client wayland-egl) \
        -o build/bin/quality/render_walle_exact_static_gl'

nix develop ./lg-test --command env \
    PYTHONPATH=analysis:lg-test/Analysis \
    python analysis/run_walle_owned_wayland_static_gl_gate.py \
    --renderer "$renderer" \
    --shaders "$shader_directory" \
    --fixtures "$fixture_directory" \
    --wayland-display "$wayland_display" \
    --output analysis/walle_owned_wayland_static_gl_gate_result.json
