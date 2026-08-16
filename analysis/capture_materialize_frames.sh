#!/usr/bin/env bash
# Capture the MATERIALIZE animation on the target M1, for every material.
#
# Why.  Everything else in this repo is measured at full thickness, and the
# thickness curve itself - the shader's pow(clock, 2.36) - was fitted from
# twelve frames of ONE material, `clear` in light.  Three of the four materials
# have never been seen mid-materialize at all, and the claim that the blur
# radius ramps linearly with thickness comes from reading Apple's transition
# INPUTS rather than from a rendered frame.
#
# The animation cannot be stepped.  `glassEffectTransition(.materialize)` is a
# SwiftUI transition driven by the system's own clock, so unlike the reveal's
# geometry there is no explicit progress to set - which is exactly why the rig
# renders a raster CLOCK into the frame.  Every captured frame carries its own
# timestamp, so a loaded host that misses a requested instant still produces a
# usable sample: the frame says when it is.
#
# The backdrop is the dynamic coded field, which is structured, so these frames
# constrain the blur's ramp and not only the transfer's.
set -euo pipefail

host=${WALLE_MAC_HOST:-quince@10.0.41.19}
remote=${WALLE_MAC_DIR:-/tmp/capmaterialize}
frames=${WALLE_DYNAMIC_FRAMES:-61}
duration=${WALLE_DYNAMIC_DURATION:-1.0}
width=${WALLE_WIDTH:-512}
height=${WALLE_HEIGHT:-512}
root=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
source_swift="$root/lg-test/Sources/GlassCapture/main.swift"
sdk=/Library/Developer/CommandLineTools/SDKs/MacOSX.sdk
tools=/Library/Developer/CommandLineTools/usr/bin

[[ -f "$source_swift" ]] || { echo "missing $source_swift" >&2; exit 1; }

ssh "$host" "mkdir -p $remote"
scp -q "$source_swift" "$host:$remote/main.swift"

echo "building on the Mac..."
ssh "$host" "cd $remote && \
  $tools/swiftc -O -parse-as-library -sdk $sdk -target arm64-apple-macosx26.0 \
    main.swift -o glasscap-unlinked 2>&1 | tail -20 && \
  $tools/vtool -set-build-version macos 26.0 26.5 -replace \
    -output glasscap glasscap-unlinked >/dev/null && \
  /usr/bin/codesign --force --sign - glasscap >/dev/null && echo 'build ok'"

if [[ "${WALLE_BUILD_ONLY:-0}" == "1" ]]; then
    echo "build-only requested; not capturing"
    exit 0
fi

# The dynamic driver runs every appearance and both overlays whenever the mode
# list is anything but wallpaper-reveal, so this one invocation covers all four
# materials.  --skip-exact-sweeps costs nothing here: materialize has no exact
# geometry sweep, its progress being a transition rather than a layout.
echo "capturing (requires the Mac unlocked and on the desktop)..."
expected=$((frames * 4))
for attempt in 1 2 3 4 5 6 7 8; do
    echo "attempt $attempt"
    if ssh "$host" "cd $remote && rm -rf cap && mkdir -p cap && chmod 777 cap && \
      sudo -n launchctl asuser 501 sudo -n -u quince $remote/glasscap \
        --out $remote/cap --width $width --height $height --suite dynamic \
        --dynamic-modes materialize --skip-exact-sweeps \
        --dynamic-frames $frames --dynamic-duration $duration \
        >$remote/run.log 2>$remote/run.err; \
      test -d $remote/cap/dynamic && \
      test \$(find $remote/cap/dynamic -name 'frame-*.png' | wc -l) -ge $expected"
    then
        echo "capture ok"
        break
    fi
    ssh "$host" "tail -3 $remote/run.err" || true
    [[ $attempt -lt 8 ]] || { echo "capture failed after 8 attempts" >&2; exit 1; }
done

ssh "$host" "echo frames: \$(find $remote/cap/dynamic -name 'frame-*.png' | wc -l); \
  ls $remote/cap/dynamic"
echo "materialize done"
