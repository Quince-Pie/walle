#!/usr/bin/env bash
# Capture the three remaining measurable parity gaps in ONE trip to the M1,
# because the harness's active/key-window preflight is flaky and every extra
# run is another chance to lose the window.
#
#   1. `.clear.tint()`.  walle's shader takes its tint branch BEFORE checking
#      the variant, so a clear+tint config silently renders the regular+tint
#      law.  The harness has a `clearTintedBlue` overlay but nothing in the
#      corpus ever captured it, so there is no measurement to branch on.
#      Swept here at the same eight mid-intensity colours that solved regular.
#
#   2. The light-appearance tint fit.  Dark fits `base = M @ linear(tint)`
#      to 5.63 codes, light only to 19.9, and excluding near-clipped samples
#      barely moves it - so the map itself is wrong, not the samples.  A
#      neutral LUMINANCE ladder and a fixed-hue SATURATION ladder say which:
#      if base is straight in linear(tint) along both, the model is right and
#      the error is elsewhere; if either curves, the curve is the law.
#
#   3. The blur kernel SHAPE.  walle ships a best-fit Gaussian (sigma 13.0
#      regular / 4.1 clear) but the per-period sigma climbs with period, so
#      the real kernel has heavier tails.  The one impulse measurement in the
#      corpus resolves it - clear is 0.54*bilinear2x + 0.45*gauss(4.15) - but
#      it was taken on 25E246 (macOS 26.4), the build whose material constants
#      this machine has already been proven not to match, and it never covered
#      `regular` at all.  A step edge through the element centre gives the
#      edge spread function directly, for both variants and both appearances,
#      at far better SNR than an impulse: differentiating it is the kernel.
#
# The capture gates on an active, key window and rightly so - macOS renders
# materials differently in an inactive one - so this REQUIRES the Mac unlocked
# and on the desktop, and retries, because focus-stealing prevention only
# grants key focus shortly after real user input.
set -euo pipefail

host=${WALLE_MAC_HOST:-quince@10.0.41.19}
remote=${WALLE_MAC_DIR:-/tmp/gapsweep}
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


def splice(old, new, why):
    """Replace `old` once, refusing to guess if the harness has moved on."""
    global s
    if s.count(old) != 1:
        raise SystemExit(f"anchor for {why} is not unique; harness changed")
    s = s.replace(old, new, 1)


# --- 1. A background filter -------------------------------------------------
# The full static suite is ~2200 captures, nearly all of them noise sweeps
# irrelevant here, and a long run is a long window in which to lose focus.
splice(
    "    var revealCoverageProbe = false\n",
    "    var revealCoverageProbe = false\n"
    "    var backgroundPrefixes: [String] = []\n",
    "config field")

splice(
    '            case "--reveal-coverage-probe":\n',
    '            case "--background-prefix":\n'
    '                guard let value = args.popFirst(), !value.isEmpty else {\n'
    '                    fatalError("--background-prefix requires a list")\n'
    '                }\n'
    '                c.backgroundPrefixes = value.split(separator: ",")\n'
    '                    .map(String.init)\n'
    '            case "--reveal-coverage-probe":\n',
    "config parsing")

# The base scene is hardcoded to the 500 pt circle, which bounds how wide a
# kernel the interior can resolve: `regular`'s bleed layer is wider than the
# element, so inside it the fit only sees a ramp and sigma stops being
# identifiable above about 600 px.  A full-frame element has no such limit.
splice(
    "    var backgroundPrefixes: [String] = []\n",
    "    var backgroundPrefixes: [String] = []\n"
    '    var baseSceneName = "circle-0500-center"\n',
    "base scene field")

splice(
    '            case "--background-prefix":\n',
    '            case "--base-scene":\n'
    '                guard let value = args.popFirst(), !value.isEmpty else {\n'
    '                    fatalError("--base-scene requires a scene name")\n'
    '                }\n'
    '                c.baseSceneName = value\n'
    '            case "--background-prefix":\n',
    "base scene parsing")

splice(
    '            let baseScene = scenes.first { $0.name == "circle-0500-center" }!\n',
    "            let baseScene = scenes.first {\n"
    "                $0.name == config.baseSceneName\n"
    "            }!\n",
    "base scene selection")

splice(
    "            let backgrounds = staticBackgrounds()\n",
    "            let allBackgrounds = staticBackgrounds()\n"
    "            let backgrounds = config.backgroundPrefixes.isEmpty\n"
    "                ? allBackgrounds\n"
    "                : allBackgrounds.filter { bg in\n"
    "                    config.backgroundPrefixes.contains {\n"
    "                        bg.name.hasPrefix($0)\n"
    "                    }\n"
    "                }\n",
    "background filter")

# --- 2. Step-edge backgrounds for the kernel -------------------------------
# A step through the element centre puts half the disc on each level, so one
# scanline is the edge spread function.  64/192 keeps both sides clear of the
# clamp in either appearance; 000/255 doubles the SNR and is kept as a
# cross-check for wherever the transfer does not clip.
splice(
    '    // Qualitative continuity with the HIG example.\n',
    '    // Step edges through the element centre: the interior scanline IS the\n'
    '    // edge spread function, so differentiating it gives the blur kernel\n'
    '    // for whichever material is composited over it.\n'
    '    for (name, lo, hi) in [\n'
    '        ("kstep-x-064-192", UInt8(64), UInt8(192)),\n'
    '        ("kstep-x-000-255", UInt8(0), UInt8(255)),\n'
    '    ] {\n'
    '        list.append(Background(name: name, family: .edge) { x, _, w, _ in\n'
    '            let v = x < w / 2 ? lo : hi\n'
    '            return (v, v, v)\n'
    '        })\n'
    '    }\n'
    '    list.append(Background(name: "kstep-y-064-192", family: .edge) {\n'
    '        _, y, _, h in\n'
    '        let v: UInt8 = y < h / 2 ? 64 : 192\n'
    '        return (v, v, v)\n'
    '    })\n'
    '\n'
    '    // Qualitative continuity with the HIG example.\n',
    "step backgrounds")

# --- 3. Tint overlays -------------------------------------------------------
# clearTint*: the same eight mid-intensity colours that solved regular, so the
# two laws are fitted from identical inputs and any difference is the variant.
# lum*: a neutral ladder - the sharpest test of whether base is linear in
# linear(tint), since hue cannot confound it.
# sat*: a fixed-hue ladder away from mid gray, testing the same linearity
# along a second, chromatic direction.
mid = {
    "M0": (0.70, 0.35, 0.35), "M1": (0.35, 0.70, 0.35),
    "M2": (0.35, 0.35, 0.70), "M3": (0.65, 0.65, 0.30),
    "M4": (0.30, 0.65, 0.65), "M5": (0.65, 0.30, 0.65),
    "M6": (0.50, 0.50, 0.50), "M7": (0.60, 0.45, 0.30),
}
lum = {f"L{int(v * 100):02d}": (v, v, v)
       for v in (0.10, 0.25, 0.40, 0.55, 0.70, 0.85)}
sat = {f"S{int(k * 100):02d}": (0.5 + 0.25 * k, 0.5 - 0.25 * k, 0.5 - 0.25 * k)
       for k in (0.25, 0.50, 0.75, 1.00)}
# The backdrop's CHROMA passes through a neutral tint essentially untouched
# (gamma 0.96 to 1.16 across seven of them) and is blocked outright by every
# chromatic one, including the mildest already measured - a tint only 27 code
# values off the gray axis blocks it as completely as a fully saturated one.
# Somewhere below that the transmission falls from one to zero, and nothing in
# the corpus says where.  These bracket it two decades either side, along the
# same red-ward direction the saturation ladder uses so the two compose.
fine = {f"N{index:d}": (0.5 + d, 0.5 - d / 2, 0.5 - d / 2)
        for index, d in enumerate((0.002, 0.005, 0.010, 0.020, 0.040, 0.080))}
sat |= fine

# Which tint families to add, so a follow-up run can capture only what is
# missing instead of re-shooting a corpus that already exists.
import os

# Twelve more colours spanning hue, saturation and level, all mid-intensity so
# nothing clips.  The chromatic branch of the tint law sits at 6 code values
# rms from twelve tints; the residual is not structural - the saturation form
# and the general affine map agree to 0.1 - so it is sample count, and these
# are the samples.
extra = {
    "X00": (0.60, 0.40, 0.50), "X01": (0.40, 0.60, 0.50),
    "X02": (0.50, 0.40, 0.60), "X03": (0.70, 0.55, 0.40),
    "X04": (0.40, 0.55, 0.70), "X05": (0.55, 0.70, 0.40),
    "X06": (0.30, 0.45, 0.60), "X07": (0.45, 0.30, 0.60),
    "X08": (0.75, 0.60, 0.60), "X09": (0.25, 0.40, 0.40),
    "X10": (0.55, 0.35, 0.45), "X11": (0.35, 0.55, 0.45),
}

families = {
    "clearMid": [(f"clearTint{k}", v, "clear") for k, v in mid.items()],
    "regularMid": [(f"sweepTint{k}", v, "regular") for k, v in mid.items()],
    "regularLadder": [(f"tint{k}", v, "regular")
                      for k, v in {**lum, **sat}.items()],
    "clearLadder": [(f"clearTint{k}", v, "clear")
                    for k, v in {**lum, **sat}.items()],
    "regularExtra": [(f"tint{k}", v, "regular") for k, v in extra.items()],
    "clearExtra": [(f"clearTint{k}", v, "clear") for k, v in extra.items()],
}
selected = os.environ.get(
    "WALLE_TINT_CASES", "clearMid,regularMid,regularLadder").split(",")
unknown = [name for name in selected if name not in families]
if unknown:
    raise SystemExit(f"unknown tint families: {unknown}")
cases = [case for name in selected for case in families[name]]


def colour(rgb):
    r, g, b = rgb
    return f"Color(.sRGB, red: {r:.4f}, green: {g:.4f}, blue: {b:.4f})"


splice(
    "    case none, regular, clear, tintedBlue, tintedOrange, clearTintedBlue\n",
    "    case none, regular, clear, tintedBlue, tintedOrange, clearTintedBlue\n"
    + "".join(f"    case {n}\n" for n, _, _ in cases),
    "overlay enum")

splice(
    "        case .clearTintedBlue: return .clear.tint(.blue)\n",
    "        case .clearTintedBlue: return .clear.tint(.blue)\n"
    + "".join(f"        case .{n}: return .{base}.tint({colour(c)})\n"
              for n, c, base in cases),
    "glass mapping")

# Every captured background gets every tint, rather than the harness's own
# seven.  The tint law needs a full 3x3 against the material's substrate, and
# from three collinear grays plus three primaries that clip the material, that
# matrix is conditioned at 3080 and its coefficients are noise.  The background
# filter already decides which backgrounds run, so gating them twice would only
# be a second list to keep in step.
splice(
    "                    if tintBackgrounds.contains(bg.name) {\n"
    "                        overlays += [.tintedBlue, .tintedOrange, "
    ".clearTintedBlue]\n"
    "                    }\n",
    "                    if tintBackgrounds.contains(bg.name) {\n"
    "                        overlays += [.tintedBlue, .tintedOrange, "
    ".clearTintedBlue]\n"
    "                    }\n"
    "                    if config.tintEveryBackground {\n"
    + "".join(f"                        overlays.append(.{n})\n"
              for n, _, _ in cases)
    + "                    }\n",
    "overlay list")

splice(
    '    var baseSceneName = "circle-0500-center"\n',
    '    var baseSceneName = "circle-0500-center"\n'
    "    var tintEveryBackground = false\n",
    "tint-every-background field")

splice(
    '            case "--base-scene":\n',
    '            case "--tint-every-background":\n'
    '                c.tintEveryBackground = true\n'
    '            case "--base-scene":\n',
    "tint-every-background parsing")

open(dst, "w").write(s)
print(f"patched: +{len(cases)} overlays, +3 step backgrounds, +filter")
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

# gray-000/128/255 and the three primaries span the transfer's input space the
# same way they did for regular; uv-map is the harness's own tint background;
# kstep- carries the kernel.
prefixes=${WALLE_PREFIXES:-gray-000,gray-128,gray-255,red-255,green-255,blue-255,uv-map,kstep-}
scene=${WALLE_BASE_SCENE:-circle-0500-center}
width=${WALLE_WIDTH:-512}
height=${WALLE_HEIGHT:-512}
minimum=${WALLE_MIN_SHOTS:-300}

echo "capturing (requires the Mac unlocked and on the desktop)..."
for attempt in 1 2 3 4 5 6 7 8; do
    echo "attempt $attempt"
    if ssh "$host" "cd $remote && rm -rf cap && mkdir -p cap && chmod 777 cap && \
      sudo -n launchctl asuser 501 sudo -n -u quince $remote/glasscap \
        --out $remote/cap --width $width --height $height --suite static \
        --skip-exact-sweeps --background-prefix $prefixes \
        --base-scene $scene ${WALLE_TINT_ALL:+--tint-every-background} \
        >$remote/run.log 2>$remote/run.err; \
      test -d $remote/cap/shots && \
      test \$(ls $remote/cap/shots | wc -l) -ge $minimum"
    then
        echo "capture ok"
        break
    fi
    ssh "$host" "tail -3 $remote/run.err" || true
    [[ $attempt -lt 8 ]] || { echo "capture failed after 8 attempts" >&2; exit 1; }
done

ssh "$host" "echo shots: \$(ls $remote/cap/shots | wc -l)"
