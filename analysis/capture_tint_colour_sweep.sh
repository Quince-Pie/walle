#!/usr/bin/env bash
# Capture a tint COLOUR sweep on the target M1, so the tint law can be solved
# for an arbitrary colour rather than the two the harness hardcodes.
#
# Why this is needed: `.tint()` measured hue-free on macOS 26.4, so it was
# never modelled.  On 26.6.1 it is strongly hue-bearing (blue and orange
# differ across the whole element).  With only those two colours the law is
# underdetermined - fitting base = a*tint + b per channel gives a NEGATIVE
# green slope, which is unphysical - so walle cannot ship an arbitrary-colour
# tint without inventing one.  Eight spanning colours make the 3x3 plus
# offset solvable the same way the untinted matrices were.
#
# The capture harness gates on an active, key window, and rightly so: macOS
# renders materials differently in an inactive window.  This script therefore
# REQUIRES the Mac to be unlocked and sitting on the desktop.  It does not
# patch that gate out.
set -euo pipefail

host=${WALLE_MAC_HOST:-quince@10.0.41.19}
remote=${WALLE_MAC_DIR:-/tmp/tintsweep}
root=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
source_swift="$root/lg-test/Sources/GlassCapture/main.swift"
sdk=/Library/Developer/CommandLineTools/SDKs/MacOSX.sdk
tools=/Library/Developer/CommandLineTools/usr/bin

[[ -f "$source_swift" ]] || { echo "missing $source_swift" >&2; exit 1; }

work=$(mktemp -d)
trap 'rm -rf -- "$work"' EXIT
patched="$work/main.swift"

python3 - "$source_swift" "$patched" <<'PY'
import sys
src, dst = sys.argv[1], sys.argv[2]
s = open(src).read()

# Eight tints spanning the colour space, all at full opacity: the harness's
# own note records that a half-opacity tint "pre-multiplies to near-neutral
# and measured as a plain gray platter", so partial alpha would waste samples.
# Mid-intensity tints.  The SwiftUI system colours are too saturated: their
# rendered bases pin at 0 or 255, leaving too few unclipped samples to solve
# the dark law (only three in R).  These keep every channel inside roughly
# 0.25..0.75 so nothing clips, while still spanning hue.
cases = ["M0", "M1", "M2", "M3", "M4", "M5", "M6", "M7"]
colours = {
    "M0": "Color(.sRGB, red: 0.70, green: 0.35, blue: 0.35)",
    "M1": "Color(.sRGB, red: 0.35, green: 0.70, blue: 0.35)",
    "M2": "Color(.sRGB, red: 0.35, green: 0.35, blue: 0.70)",
    "M3": "Color(.sRGB, red: 0.65, green: 0.65, blue: 0.30)",
    "M4": "Color(.sRGB, red: 0.30, green: 0.65, blue: 0.65)",
    "M5": "Color(.sRGB, red: 0.65, green: 0.30, blue: 0.65)",
    "M6": "Color(.sRGB, red: 0.50, green: 0.50, blue: 0.50)",
    "M7": "Color(.sRGB, red: 0.60, green: 0.45, blue: 0.30)",
}

old_enum = ("    case none, regular, clear, tintedBlue, tintedOrange, "
            "clearTintedBlue\n")
if old_enum not in s:
    raise SystemExit("Overlay enum not found; harness changed")
new_enum = old_enum.rstrip("\n") + "\n    case " + ", ".join(
    "sweepTint" + c for c in cases) + "\n"
s = s.replace(old_enum, new_enum, 1)

anchor = "        case .clearTintedBlue: return .clear.tint(.blue)\n"
if anchor not in s:
    raise SystemExit("glass mapping not found; harness changed")
mapping = "".join(
    f"        case .sweepTint{c}: return .regular.tint({colours[c]})\n"
    for c in cases)
s = s.replace(anchor, anchor + mapping, 1)

# Sweep the tints on the same backgrounds the existing tints already use.
old_list = ("                        overlays += [.tintedBlue, .tintedOrange, "
            ".clearTintedBlue]\n")
if old_list not in s:
    raise SystemExit("tint overlay list not found; harness changed")
new_list = old_list.rstrip("\n") + "\n                        overlays += [" + ", ".join(
    ".sweepTint" + c for c in cases) + "]\n"
s = s.replace(old_list, new_list, 1)

open(dst, "w").write(s)
print(f"patched: +{len(cases)} tint colours")
PY

ssh "$host" "mkdir -p $remote"
scp -q "$patched" "$host:$remote/main.swift"

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

echo "capturing (requires the Mac unlocked and on the desktop)..."
ssh "$host" "cd $remote && rm -rf cap && mkdir -p cap && chmod 777 cap && \
  sudo -n launchctl asuser 501 sudo -n -u quince $remote/glasscap \
    --out $remote/cap --width 512 --height 512 --suite static \
    --skip-exact-sweeps >$remote/run.log 2>$remote/run.err; \
  tail -3 $remote/run.err; \
  echo \"shots: \$(ls $remote/cap/shots 2>/dev/null | wc -l)\""
