#!/usr/bin/env bash
# Live-path standing gate: real timed transitions through the LIVE renderer
# (not the state-driven capture path), under a headless compositor with a
# real DRM render device.  Session 181 found the live path aborting on a
# geometry-dependent raster case that every capture-path gate was blind to;
# this gate exists so that class of failure can never hide again.
#
# It drives BOTH the known-bad geometry (1280x720, the resolution where the
# 181 abort reproduced at centre (320,240)) and a second output size, runs
# several full transition cycles per variant, screenshots mid-transition,
# and fails on any "Transition stopped", any walle death, or screenshots
# that never change (a frozen presentation).
set -euo pipefail
export LC_ALL=C

task_root=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
walle_binary=${1:-"$task_root/build/bin/release/walle"}
labwc_binary=${LABWC:-/run/current-system/sw/bin/labwc}
drm_device=${WALLE_LIVE_GATE_DRM:-/dev/dri/renderD128}
cycle_seconds=${WALLE_LIVE_GATE_CYCLES:-24}

fail()
{
    printf 'Walle live transition gate failed: %s\n' "$*" >&2
    exit 1
}

[[ -x "$walle_binary" ]] || fail "missing Walle binary: $walle_binary"
[[ -x "$labwc_binary" ]] || fail "missing Labwc binary: $labwc_binary"
[[ -e "$drm_device" ]] || fail "missing DRM render device: $drm_device"
command -v grim >/dev/null || fail 'grim is not available'

umask 077
gate_root=$(mktemp -d "${TMPDIR:-/tmp}/walle-live-gate.XXXXXX")
labwc_pid=
walle_pid=
cleanup()
{
    [[ -n "$walle_pid" ]] && kill "$walle_pid" 2>/dev/null || true
    [[ -n "$labwc_pid" ]] && kill "$labwc_pid" 2>/dev/null || true
    wait 2>/dev/null || true
    rm -rf -- "$gate_root"
}
trap cleanup EXIT INT TERM

vips xyz "$gate_root/a0.v" 512 512
vips linear "$gate_root/a0.v" "$gate_root/a1.v" "0.3,0.1" "20,60"
vips bandjoin_const "$gate_root/a1.v" "$gate_root/a.v" 128
vips cast "$gate_root/a.v" "$gate_root/wall_a.png" uchar
vips gaussnoise "$gate_root/b0.v" 512 512 --mean 128 --sigma 60
vips cast "$gate_root/b0.v" "$gate_root/b1.png" uchar
vips bandjoin "$gate_root/b1.png $gate_root/b1.png $gate_root/b1.png" \
    "$gate_root/wall_b.png"

run_case()
{
    local label=$1 outw=$2 outh=$3 variant=$4
    local rt="$gate_root/rt-$label"
    local ch="$gate_root/ch-$label"
    install -d -m 700 -- "$rt" "$ch/labwc"

    env -u WAYLAND_DISPLAY \
        XDG_RUNTIME_DIR="$rt" XDG_CONFIG_HOME="$ch" \
        WLR_BACKENDS=headless WLR_HEADLESS_OUTPUTS=1 \
        WLR_RENDERER=vulkan WLR_RENDER_DRM_DEVICE="$drm_device" \
        WLR_HEADLESS_OUTPUT_WIDTH="$outw" WLR_HEADLESS_OUTPUT_HEIGHT="$outh" \
        WLR_LIBINPUT_NO_DEVICES=1 \
        "$labwc_binary" -d >"$gate_root/labwc-$label.log" 2>&1 &
    labwc_pid=$!
    local _i
    for _i in $(seq 1 200); do
        [[ -S "$rt/wayland-0" ]] && break
        sleep 0.05
    done
    [[ -S "$rt/wayland-0" ]] || fail "$label: no wayland socket"

    printf '[walle]\nvulkan_device = auto\n\n[default]\nfiles =\n\tstretch:%s\n\tstretch:%s\ntimeout = 3\ntransition = true\ntransition_duration = 4\ntransition_variant = %s\nrandomize = false\ngamemode = false\n' \
        "$gate_root/wall_a.png" "$gate_root/wall_b.png" "$variant" \
        >"$gate_root/walle-$label.ini"

    env XDG_RUNTIME_DIR="$rt" WAYLAND_DISPLAY=wayland-0 \
        "$walle_binary" --config "$gate_root/walle-$label.ini" \
        >"$gate_root/walle-$label.log" 2>&1 &
    walle_pid=$!

    local shots=0 distinct=0 previous_hash="" shot_hash
    local deadline=$((SECONDS + cycle_seconds))
    while ((SECONDS < deadline)); do
        sleep 2
        kill -0 "$walle_pid" 2>/dev/null || fail "$label: walle died"
        if env XDG_RUNTIME_DIR="$rt" WAYLAND_DISPLAY=wayland-0 \
            grim "$gate_root/shot-$label-$shots.png" 2>>"$gate_root/grim-$label.log"
        then
            shot_hash=$(sha256sum "$gate_root/shot-$label-$shots.png")
            shot_hash=${shot_hash%% *}
            [[ -n "$previous_hash" && "$shot_hash" != "$previous_hash" ]] \
                && distinct=$((distinct + 1))
            previous_hash=$shot_hash
            shots=$((shots + 1))
        fi
    done

    if grep -Fq 'Transition stopped' "$gate_root/walle-$label.log"; then
        grep -F 'Transition stopped' "$gate_root/walle-$label.log" >&2
        fail "$label: a live transition aborted"
    fi
    kill -0 "$walle_pid" 2>/dev/null || fail "$label: walle exited during the soak"
    ((shots >= 5)) || fail "$label: only $shots screenshots captured"
    ((distinct >= 2)) || fail "$label: presentation appears frozen ($distinct changes over $shots shots)"

    kill "$walle_pid" 2>/dev/null || true
    wait "$walle_pid" 2>/dev/null || true
    walle_pid=
    kill "$labwc_pid" 2>/dev/null || true
    wait "$labwc_pid" 2>/dev/null || true
    labwc_pid=
    printf '%s: %s shots, %s distinct, no aborts\n' "$label" "$shots" "$distinct"
}

# The 181 abort geometry first, then a second size, across both variants.
run_case bad-720-clear 1280 720 clear
run_case bad-720-regular 1280 720 regular
run_case wide-1080-regular 1920 1080 regular

printf 'walleLiveTransitionGate=pass\n'
