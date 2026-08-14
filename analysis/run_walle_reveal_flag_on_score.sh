#!/usr/bin/env bash
# Flag-on reveal capture + lenient scoring: like the process gate but with
# WALLE_REVEAL_GENERAL=1 and no mismatch expectation, reporting the actual
# mismatch count under the general path.
set -euo pipefail
export LC_ALL=C

task_root=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
walle_binary=${1:-"$task_root/build/bin/release/walle"}
labwc_binary=${LABWC:-/run/current-system/sw/bin/labwc}
scorer="$task_root/analysis/score_reveal_vulkan_capture.py"

umask 077
gate_root=$(mktemp -d "${TMPDIR:-/tmp}/walle-flag-on-score.XXXXXX")
labwc_pid=
cleanup()
{
    if [[ -n "$labwc_pid" ]] && kill -0 "$labwc_pid" 2>/dev/null; then
        kill "$labwc_pid" 2>/dev/null || true
        wait "$labwc_pid" 2>/dev/null || true
    fi
    rm -rf -- "$gate_root"
}
trap cleanup EXIT

runtime_directory="$gate_root/runtime"
config_home="$gate_root/config"
capture_directory="$gate_root/capture"
install -d -m 700 -- "$runtime_directory" "$config_home" "$capture_directory"
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
    sleep 0.05
done
[[ -S "$runtime_directory/$wayland_display" ]] || {
    echo "labwc failed" >&2; exit 1; }

timeout 90s env \
    XDG_RUNTIME_DIR="$runtime_directory" \
    WAYLAND_DISPLAY="$wayland_display" \
    WALLE_REVEAL_GENERAL=1 \
    "$walle_binary" \
    --config "$walle_config" \
    --reveal-mask-process-capture "$capture_directory" \
    >"$gate_root/walle.log" 2>&1 || {
    sed -n '1,80p' "$gate_root/walle.log" >&2; exit 1; }

python3 "$scorer" "$capture_directory" --output "$gate_root/score.json" \
    >"$gate_root/score.stdout" || true
cat "$gate_root/score.stdout"
python3 - "$gate_root/score.json" <<'EOF'
import json, sys
s = json.load(open(sys.argv[1]))
print(json.dumps({k: v for k, v in s.items()
                  if "ismatch" in k or "state" in k.lower() or "byte" in k.lower()},
                 indent=1)[:2000])
EOF
