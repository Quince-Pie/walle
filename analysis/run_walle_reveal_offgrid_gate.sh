#!/usr/bin/env bash
# Continuous-progress MEASUREMENT (not a parity gate).  Renders the reveal at
# an explicit progress and scores it against the one hardware sample that
# falls between k/64 ladder states.  This does NOT reach zero: see TASK.md
# later-141.  The expectations below record the measured bound so a
# regression is still caught; they are not a parity claim.
set -euo pipefail
export LC_ALL=C

task_root=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
walle_binary=${1:-"$task_root/build/bin/release/walle"}
labwc_binary=${LABWC:-/run/current-system/sw/bin/labwc}
scorer="$task_root/analysis/score_reveal_offgrid_frame.py"
capture_root="$task_root/artifacts/liquid-glass-reveal-coverage-01421a3-v1/capture"
reference="$capture_root/dynamic/wallpaper-reveal__regular__dark/frame-0001.png"
# manifest.json: dynamicSequences[0].frames[1].presentationProgress
# The manifest's presentationProgress (0.4853515625) is the sequence clock,
# NOT the reveal's radius fraction: the frame's geometry lands between ladder
# states 43 and 44.  This is the best-matching walle geometry for it.
progress=0.67237975
expected_reference_sha=565e13cbe54c8ae04e9629c11c382ddf38d641b11cee513365b694dd55928167
capture_width=2048
capture_height=2048
capture_bytes=$((capture_width * capture_height))
composition_bytes=$((capture_bytes * 4))

fail()
{
    printf 'Walle off-ladder reveal gate failed: %s\n' "$*" >&2
    exit 1
}

[[ -x "$walle_binary" ]] || fail "missing Walle binary: $walle_binary"
[[ -x "$labwc_binary" ]] || fail "missing Labwc binary: $labwc_binary"
[[ -f "$scorer" ]] || fail "missing off-ladder scorer: $scorer"
[[ -f "$reference" ]] || fail "missing hardware reference: $reference"

umask 077
gate_root=$(mktemp -d "${TMPDIR:-/tmp}/walle-offgrid-gate.XXXXXX")
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

# The hardware sequence revealed the white wallpaper over the black one, the
# same two-wallpaper oracle the ladder corpus used.
black_image="$gate_root/black.png"
white_image="$gate_root/white.png"
vips black "$black_image" 64 64 --bands 3
vips invert "$black_image" "$white_image"

walle_config="$gate_root/walle.ini"
printf '[walle]\nvulkan_device = auto\n\n[default]\nfiles =\n\tstretch:%s\n\tstretch:%s\n%s\n' \
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
    --reveal-mask-process-capture-progress "$progress" \
    >"$gate_root/walle.log" 2>&1; then
    sed -n '1,320p' "$gate_root/walle.log" >&2
    fail 'Walle off-ladder capture did not complete successfully'
fi

for marker in \
    walleExecutableProcessRendered=true \
    walleLayerShellSurfaceRendered=true \
    revealMaskProcessCaptureStates=1 \
    "revealMaskProcessCaptureProgress=explicit=$progress" \
    compositionProcessCaptureStates=1 \
    revealMaskProcessCaptureComplete=true; do
    grep -Fxq "$marker" "$gate_root/walle.log" || fail "missing marker: $marker"
done
if grep -Eq '\[Vulkan (WARN|ERROR)\]|Validation Error|VUID-' "$gate_root/walle.log"; then
    sed -n '1,320p' "$gate_root/walle.log" >&2
    fail 'Vulkan validation reported an error'
fi

entry_count=$(find "$capture_directory" -mindepth 1 -maxdepth 1 -printf '.' | wc -c)
[[ "$entry_count" -eq 2 ]] || fail 'capture directory contains unexpected entries'
[[ $(stat -c '%s' "$capture_directory/state-0000.r8") -eq "$capture_bytes" ]] \
    || fail 'mask capture has the wrong byte count'
[[ $(stat -c '%s' "$capture_directory/composition-state-0000.bgra") \
    -eq "$composition_bytes" ]] \
    || fail 'composition capture has the wrong byte count'

reference_sha=$(sha256sum "$reference" | cut -d' ' -f1)
[[ "$reference_sha" == "$expected_reference_sha" ]] \
    || fail "hardware reference identity differs: $reference_sha"

python3 "$scorer" "$capture_directory" \
    --reference "$reference" \
    --progress "$progress" \
    --output "$gate_root/offgrid-score.json" \
    --expect-mismatches 3838 \
    >"$gate_root/offgrid-score.stdout"

printf '%s\n' \
    'Walle off-ladder reveal measurement completed' \
    "progress=$progress" \
    'mismatchedPixels=3838' \
    'mismatchClass=antialiased-boundary-ring-only' \
    'formalParityEstablished=false' \
    "referenceSha256=$reference_sha"
