#!/usr/bin/env bash
# Capture the wallpaper reveal's LIVE frames on the target M1, many of them.
#
# Why.  The 65-state ladder is byte-exact, and it proves walle's geometry model
# outright: the circle's bounds are snapped to integers, and a finer rounding
# grid is not merely unnecessary but refuted - a half-integer grid changes 52 of
# those 65 states and a quarter-integer grid changes 63.
#
# But the corpus also holds ONE frame from the live animation that lands between
# ladder states, and walle cannot reach it.  Measured by the 50% coverage
# contour at 47 angles, the difference is a rigid translation of dy = -0.177 px
# with dx = -0.009 and dr = +0.067, and the fit's own residual is 0.014 px rms -
# so it is geometry, not antialiasing, and the hardware circle sits at
# centre_y 614.32 where the integer grid only offers 614.0 and 614.5.  It is not
# on the unsnapped centre either, which would be 614.4.
#
# One sample cannot say what law that is.  This captures thirty-odd, so the live
# path's geometry can be measured the same way the ladder's was rather than
# guessed from a single frame.
set -euo pipefail

host=${WALLE_MAC_HOST:-quince@10.0.41.19}
remote=${WALLE_MAC_DIR:-/tmp/revealdyn}
frames=${WALLE_DYNAMIC_FRAMES:-33}
duration=${WALLE_DYNAMIC_DURATION:-1.0}
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

# 2048x2048 at 1x is the corpus geometry: the reveal origin is 0.25, 0.30 of
# the frame, so the circle is centred at (512, 614.4) exactly as the ladder's.
echo "capturing (requires the Mac unlocked and on the desktop)..."
for attempt in 1 2 3 4 5 6 7 8; do
    echo "attempt $attempt"
    if ssh "$host" "cd $remote && rm -rf cap && mkdir -p cap && chmod 777 cap && \
      sudo -n launchctl asuser 501 sudo -n -u quince $remote/glasscap \
        --out $remote/cap --width 2048 --height 2048 --suite dynamic \
        --dynamic-modes wallpaper-reveal --dynamic-frames $frames \
        --dynamic-duration $duration --transition-origin 0.25,0.30 \
        >$remote/run.log 2>$remote/run.err; \
      test -d $remote/cap/dynamic && \
      test \$(find $remote/cap/dynamic -name 'frame-*.png' | wc -l) -ge $frames"
    then
        echo "capture ok"
        break
    fi
    ssh "$host" "tail -3 $remote/run.err" || true
    [[ $attempt -lt 8 ]] || { echo "capture failed after 8 attempts" >&2; exit 1; }
done

ssh "$host" "echo frames: \$(find $remote/cap/dynamic -name 'frame-*.png' | wc -l)"
