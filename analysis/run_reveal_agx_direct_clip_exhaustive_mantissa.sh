#!/bin/sh
set -eu

if [ "$#" -ne 1 ]; then
    echo "usage: $0 REMOTE_SCRATCH_ROOT" >&2
    exit 2
fi

scratch_root=$1
for low7 in $(/usr/bin/jot 128 0 127); do
    suffix=$(/usr/bin/printf '%03u' "$low7")
    capture_root="$scratch_root/capture-exhaustive-$suffix"
    trace_root="$scratch_root/trace-exhaustive-$suffix"
    stdout_path="$scratch_root/exhaustive-$suffix.stdout"
    stderr_path="$scratch_root/exhaustive-$suffix.stderr"
    if [ -e "$capture_root" ] || [ -e "$trace_root" ]; then
        echo "refusing to overwrite exhaustive chunk $suffix" >&2
        exit 1
    fi
    /usr/bin/env \
        WALLE_AGX_MANTISSA_LOW7="$low7" \
        WALLE_AGX_EXPORT_LDCF=1 \
        WALLE_AGX_TRACE_DIR="$trace_root" \
        DYLD_INSERT_LIBRARIES="$scratch_root/libwalle-agx-ldcf-export.dylib" \
        "$scratch_root/direct-user-clip-exhaustive-mantissa-probe" \
        "$scratch_root/reveal-agx-clip-weight-plan.bin" \
        "$scratch_root/reveal_agx_clip_weight_tomography_preregistration.json" \
        "$capture_root" >"$stdout_path" 2>"$stderr_path"
    /usr/bin/printf 'completed %s\n' "$suffix"
done
