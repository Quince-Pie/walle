#!/usr/bin/env bash
set -euo pipefail
export LC_ALL=C

task_root=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
walle_binary=${1:-"$task_root/build/bin/release/walle"}
labwc_binary=${LABWC:-/run/current-system/sw/bin/labwc}
scorer="$task_root/analysis/score_reveal_vulkan_capture.py"
expected_candidate_inventory=9062b7bfde617f88638c9b48fdb8ace7b6f91b4518d54c5a6e54abcb51e93644
expected_count_hash=d6c006d789b551e875555f3e8ef32f0c46c3ec3911802fea405ef9d3458edb5d
capture_width=2048
capture_height=2048
capture_bytes=$((capture_width * capture_height))

fail()
{
    printf 'Walle Vulkan reveal process gate failed: %s\n' "$*" >&2
    exit 1
}

[[ -x "$walle_binary" ]] || fail "missing Walle binary: $walle_binary"
[[ -x "$labwc_binary" ]] || fail "missing Labwc binary: $labwc_binary"
[[ -f "$scorer" ]] || fail "missing Vulkan capture scorer: $scorer"
if ldd "$walle_binary" | grep -Eiq 'lib(EGL|GLES|GLX|OpenGL)'; then
    fail 'Walle still links an OpenGL/EGL runtime'
fi

umask 077
gate_root=$(mktemp -d "${TMPDIR:-/tmp}/walle-vulkan-process-gate.XXXXXX")
labwc_pid=
cleanup()
{
    if [[ -n "$labwc_pid" ]] && kill -0 "$labwc_pid" 2>/dev/null; then
        kill "$labwc_pid" 2>/dev/null || true
        wait "$labwc_pid" 2>/dev/null || true
    fi
    rm -rf -- "$gate_root"
}
trap cleanup EXIT INT TERM

runtime_directory="$gate_root/runtime"
config_home="$gate_root/config-home"
capture_directory="$gate_root/capture"
install -d -m 700 -- "$runtime_directory" "$config_home/labwc" "$capture_directory"

black_image="$gate_root/black.png"
white_image="$gate_root/white.png"
vips black "$black_image" 64 64 --bands 3
vips invert "$black_image" "$white_image"

walle_config="$gate_root/walle.ini"
printf '[default]\nfiles =\n\tstretch:%s\n\tstretch:%s\n%s\n' \
    "$black_image" \
    "$white_image" \
    $'timeout = 0\ntransition = true\ntransition_duration = 1\ntransition_variant = clear\nrandomize = false\ngamemode = false' \
    >"$walle_config"

env -u WAYLAND_DISPLAY \
    XDG_RUNTIME_DIR="$runtime_directory" \
    XDG_CONFIG_HOME="$config_home" \
    WLR_BACKENDS=headless \
    WLR_HEADLESS_OUTPUTS=1 \
    WLR_RENDERER=vulkan \
    WLR_LIBINPUT_NO_DEVICES=1 \
    "$labwc_binary" -d >"$gate_root/labwc.log" 2>&1 &
labwc_pid=$!

wayland_display=wayland-0
for _ in $(seq 1 200); do
    [[ -S "$runtime_directory/$wayland_display" ]] && break
    kill -0 "$labwc_pid" 2>/dev/null || {
        sed -n '1,240p' "$gate_root/labwc.log" >&2
        fail 'Labwc exited during Vulkan startup'
    }
    sleep 0.05
done
[[ -S "$runtime_directory/$wayland_display" ]] || fail "Labwc did not create $wayland_display"

if ! timeout 90s env \
    XDG_RUNTIME_DIR="$runtime_directory" \
    WAYLAND_DISPLAY="$wayland_display" \
    WALLE_VULKAN_VALIDATION=1 \
    "$walle_binary" \
    --config "$walle_config" \
    --reveal-mask-process-capture "$capture_directory" \
    >"$gate_root/walle.log" 2>&1; then
    sed -n '1,320p' "$gate_root/walle.log" >&2
    fail 'Walle Vulkan process capture did not complete successfully'
fi

for marker in \
    walleExecutableProcessRendered=true \
    walleLayerShellSurfaceRendered=true \
    walleRenderer=Vulkan-1.4-Slang-SPIR-V-1.6 \
    revealMaskProcessCaptureStates=65 \
    revealMaskProcessCaptureSwaps=65 \
    revealMaskProcessCaptureCallbacks=64 \
    revealMaskProcessCaptureDimensions=2048x2048 \
    revealMaskProcessCaptureCenterTopLeft=512.0,614.4 \
    revealMaskProcessCaptureProgress=state/64 \
    revealMaskProcessCaptureFormat=R8-top-left-row-major \
    revealMaskProcessCaptureComplete=true; do
    grep -Fxq "$marker" "$gate_root/walle.log" || fail "missing marker: $marker"
done
if grep -Eq '\[Vulkan (WARN|ERROR)\]|Validation Error|VUID-' "$gate_root/walle.log"; then
    sed -n '1,320p' "$gate_root/walle.log" >&2
    fail 'Vulkan validation reported an error'
fi

file_count=$(find "$capture_directory" -mindepth 1 -maxdepth 1 -type f \
    -name 'state-????.r8' -printf '.' | wc -c)
[[ "$file_count" -eq 65 ]] || fail "expected 65 state files, found $file_count"
entry_count=$(find "$capture_directory" -mindepth 1 -maxdepth 1 -printf '.' | wc -c)
[[ "$entry_count" -eq 65 ]] || fail 'capture directory contains unexpected entries'
for state in $(seq 0 64); do
    state_path=$(printf '%s/state-%04u.r8' "$capture_directory" "$state")
    [[ -f "$state_path" ]] || fail "missing state $state"
    [[ $(stat -c '%s' "$state_path") -eq "$capture_bytes" ]] \
        || fail "state $state has the wrong byte count"
    [[ $(stat -c '%a' "$state_path") == 600 ]] || fail "state $state has unsafe permissions"
done

python3 "$scorer" "$capture_directory" \
    --output "$gate_root/vulkan-score.json" \
    --expect-mismatches 91 \
    --expect-candidate-inventory "$expected_candidate_inventory" \
    --expect-count-hash "$expected_count_hash" \
    >"$gate_root/vulkan-score.stdout"

if env XDG_RUNTIME_DIR="$runtime_directory" WAYLAND_DISPLAY="$wayland_display" \
    "$walle_binary" --config "$walle_config" \
    --reveal-mask-process-capture "$capture_directory" \
    >"$gate_root/nonempty.log" 2>&1; then
    fail 'a non-empty destination directory was accepted'
fi
grep -q 'Directory not empty' "$gate_root/nonempty.log" \
    || fail 'non-empty-directory rejection was not explicit'

symlink_target="$gate_root/symlink-target"
symlink_path="$gate_root/symlink-capture"
mkdir -m 700 -- "$symlink_target"
ln -s -- "$symlink_target" "$symlink_path"
if "$walle_binary" --reveal-mask-process-capture "$symlink_path" \
    >"$gate_root/symlink.log" 2>&1; then
    fail 'a symlink capture directory was accepted'
fi
grep -q 'Could not open empty reveal capture directory' "$gate_root/symlink.log" \
    || fail 'symlink-directory rejection was not explicit'

if "$walle_binary" --exact-static-fixture unused \
    >"$gate_root/removed-option.log" 2>&1; then
    fail 'removed exact-static compatibility option was accepted'
fi

printf '%s\n' \
    'Walle Vulkan reveal process gate passed' \
    'vulkanApi=1.4' \
    'shaderSource=Slang' \
    'spirvVersion=1.6' \
    'actualProcessStates=65' \
    'ordinaryCompositionPresents=65' \
    'frameCallbacks=64' \
    'mismatchedPixels=91' \
    'exactPixelPercentage=99.99996662139893' \
    "actualProcessCandidateInventorySha256=$expected_candidate_inventory"
