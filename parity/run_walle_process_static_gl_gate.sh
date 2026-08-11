#!/usr/bin/env bash
set -euo pipefail

project_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$project_root"

wayland_display=${1:-${WAYLAND_DISPLAY:-wayland-1}}
shader_directory=build/generated/liquid-glass/desktop
fixture_directory=build/generated/liquid-glass/static-fixtures
walle=build/bin/release/walle

mkdir -p "$shader_directory" "$fixture_directory"

nix develop ./lg-test --command env PYTHONPATH=analysis:lg-test/Analysis \
    python analysis/generate_walle_exact_shaders.py \
    --api desktop --output "$shader_directory" >/dev/null
nix develop ./lg-test --command env PYTHONPATH=analysis:lg-test/Analysis \
    python analysis/generate_walle_exact_static_fixtures.py \
    --output "$fixture_directory" >/dev/null
nix develop --command make MODE=release

nix develop ./lg-test --command env \
    PYTHONPATH=analysis:lg-test/Analysis \
    python analysis/run_walle_process_static_gl_gate.py \
    --walle "$walle" \
    --shaders "$shader_directory" \
    --fixtures "$fixture_directory" \
    --wayland-display "$wayland_display" \
    --output analysis/walle_process_static_gl_gate_result.json
