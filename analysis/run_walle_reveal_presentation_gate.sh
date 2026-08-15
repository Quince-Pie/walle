#!/usr/bin/env bash
# Score walle's ANIMATING reveal against the hardware's live frames.
#
# The 65-state ladder gate scores the ROUNDED path - an explicitly set
# progress, which goes through Core Animation's model layer and lays out on
# whole pixels.  It is byte-exact, and it says nothing about the path users
# actually see: an animating layer's presentation values are interpolated
# without re-laying-out, so its circle is the interpolation between the two
# rounded endpoint rects rather than the rounded rect at that progress.
#
# This gate renders that second path at the progress values the hardware's own
# frames sit at and scores the mask against them.  It is a real gate: the
# expected mismatch per frame is recorded in the scorer and a regression fails.
#
# Corpus: analysis/capture_reveal_dynamic_frames.sh, 33 live frames of the
# two-wallpaper reveal oracle at 2048x2048.
set -euo pipefail
export LC_ALL=C

root=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
walle_binary=${1:-"$root/build/bin/release/walle"}
labwc_binary=${LABWC:-/run/current-system/sw/bin/labwc}
scorer="$root/analysis/score_reveal_presentation_frames.py"
frames=${WALLE_REVEAL_LIVE_FRAMES:-"$root/artifacts-revealdyn/dynamic/wallpaper-reveal__regular__dark"}
# Must match, in order, the frames the scorer expects.
progress=0.0136210684,0.0740005070,0.1748244920,0.2822963200,0.3764788370,0.4704757597,0.5645734865,0.6720809384

fail() { printf 'Walle reveal presentation gate failed: %s\n' "$*" >&2; exit 1; }

[[ -x "$walle_binary" ]] || fail "missing Walle binary: $walle_binary"
[[ -x "$labwc_binary" ]] || fail "missing Labwc binary: $labwc_binary"
[[ -f "$scorer" ]] || fail "missing scorer: $scorer"
[[ -d "$frames" ]] || fail "missing live frames: $frames (run analysis/capture_reveal_dynamic_frames.sh)"

umask 077
gate=$(mktemp -d "${TMPDIR:-/tmp}/walle-presentation-gate.XXXXXX")
labwc_pid=
cleanup() {
    if [[ -n "$labwc_pid" ]] && kill -0 "$labwc_pid" 2>/dev/null; then
        kill "$labwc_pid" 2>/dev/null || true
        wait "$labwc_pid" 2>/dev/null || true
    fi
    rm -rf -- "$gate"
}
trap cleanup EXIT INT TERM

install -d -m 700 -- "$gate/runtime" "$gate/config/labwc"
install -d -m 755 -- "$gate/capture"

# The same two-wallpaper oracle the corpus revealed: opaque white over opaque
# black, so the composed byte IS the mask.
vips black "$gate/black.png" 64 64 --bands 3
vips invert "$gate/black.png" "$gate/white.png"
printf '[walle]\nvulkan_device = auto\n\n[default]\nfiles =\n\tstretch:%s\n\tstretch:%s\n%s\n' \
    "$gate/black.png" "$gate/white.png" \
    $'timeout = 0\ntransition = true\ntransition_duration = 1\ntransition_variant = clear\nrandomize = false\ngamemode = false' \
    >"$gate/walle.ini"

env -u WAYLAND_DISPLAY XDG_RUNTIME_DIR="$gate/runtime" XDG_CONFIG_HOME="$gate/config" \
    WLR_BACKENDS=headless WLR_HEADLESS_OUTPUTS=1 WLR_RENDERER=vulkan \
    WLR_LIBINPUT_NO_DEVICES=1 "$labwc_binary" -d >"$gate/labwc.log" 2>&1 &
labwc_pid=$!
for _ in $(seq 1 200); do
    [[ -S "$gate/runtime/wayland-0" ]] && break
    kill -0 "$labwc_pid" 2>/dev/null || { sed -n '1,80p' "$gate/labwc.log" >&2; fail 'Labwc exited'; }
    sleep 0.05
done
[[ -S "$gate/runtime/wayland-0" ]] || fail 'Labwc did not create wayland-0'

if ! timeout 600s env XDG_RUNTIME_DIR="$gate/runtime" WAYLAND_DISPLAY=wayland-0 \
    "$walle_binary" --config "$gate/walle.ini" \
    --reveal-mask-process-capture "$gate/capture" \
    --reveal-mask-process-capture-presentation \
    --reveal-mask-process-capture-progress "$progress" \
    >"$gate/walle.log" 2>&1; then
    sed -n '1,200p' "$gate/walle.log" >&2
    fail 'Walle presentation capture did not complete'
fi

python3 "$scorer" --frames "$frames" --capture "$gate/capture" \
    --output "$root/analysis/results/reveal_presentation_gate.json" \
    || fail 'presentation frames regressed'
echo 'Walle reveal presentation gate passed'
