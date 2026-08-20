#!/usr/bin/env python3
"""Score walle's PRODUCTION Vulkan transition against real M1 Liquid Glass.

The repo's existing end-to-end gate (analysis/liquid_glass_pixel_gate.py) scores
`shaders/frag.glsl` - a GL mirror that has not moved since 2026-08-15 while the
shipped Slang did - against captures taken on a GitHub CI VirtualMac at backing
scale 1 on macOS 26.4.  Neither half of that is the thing we ship or the
hardware we are matching.

This scores the real binary against the real machine: walle's own process
capture (the production composeFragment path, WALLE_COMPOSE_MATERIAL=1) at the
rig's own exact-state progress ladder, over the rig's own two wallpapers, at the
rig's own geometry.  That alignment is exact rather than arranged: the rig's
--transition-origin 0.25,0.30 in a 1024x1024-point window at backing scale 2 is
pixel-for-pixel walle's canonical capture centre (512, 614.4) in 2048x2048.

Apple's element radius is measured from the frames rather than assumed, so a
disagreement in the radius law shows up as its own number instead of smearing
into the colour error.

PASS --material-progress 0.66.  The rig's sweeps are SETTLED - fully formed
glass at every state - while walle's material runs on the wall clock against a
one-second transition, and capturing seventeen states takes longer than that.
Omitting the flag therefore renders the late states AFTER the transition has
finished, where the glass is gone and the frame is the bare wallpaper: state 16
comes out byte-identical to the incoming reference and scores 94 codes against
Apple, dragging the headline from 1.27 to 24.08.  It fails quietly - the render
succeeds, the masks are correct to the pixel, and walle.log is byte-identical
to a good run - so nothing announces it but the score.  0.66 is full thickness;
1.0 is the far end of the materialize curve and is just as wrong (112 inside).
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image

LUMA = np.array([0.2126, 0.7152, 0.0722])


def load_png(path: Path) -> np.ndarray:
    return np.asarray(Image.open(path).convert("RGB")).astype(np.int16)


def load_bgra(path: Path, extent: int) -> np.ndarray:
    raw = np.fromfile(path, dtype=np.uint8)
    return raw.reshape(extent, extent, 4)[..., [2, 1, 0]].astype(np.int16)


def apple_radius(frame: np.ndarray, outgoing: np.ndarray, centre: tuple[float, float],
                 tolerance: float = 6.0) -> float:
    """Radius of the revealed element, read off the frame itself.

    Outside the element the frame is the outgoing wallpaper untouched, so the
    boundary is the largest distance at which the frame still departs from it.
    Read as a radial profile of the departure so a few stray pixels cannot set
    it.
    """
    height, width, _ = frame.shape
    y, x = np.mgrid[0:height, 0:width]
    distance = np.hypot(x - centre[0], y - centre[1])
    changed = np.abs(frame - outgoing).max(axis=2) > tolerance
    if not changed.any():
        return 0.0
    bins = np.arange(0, distance.max() + 2.0, 2.0)
    index = np.digitize(distance.ravel(), bins)
    total = np.bincount(index, minlength=bins.size + 1).astype(float)
    hit = np.bincount(index, weights=changed.ravel().astype(float),
                      minlength=bins.size + 1)
    fraction = np.divide(hit, total, out=np.zeros_like(hit), where=total > 0)
    # The outermost ring that is still majority-changed.
    occupied = np.nonzero(fraction > 0.5)[0]
    return float(bins[min(occupied.max(), bins.size - 1)]) if occupied.size else 0.0


def metrics(walle: np.ndarray, apple: np.ndarray, mask_inside: np.ndarray,
            edge_band: np.ndarray, scored: np.ndarray) -> dict[str, float]:
    delta = np.abs(walle.astype(np.int32) - apple.astype(np.int32)).max(axis=2)
    out: dict[str, float] = {}
    for name, selector in (("full", None), ("inside", mask_inside),
                           ("outside", ~mask_inside), ("edge", edge_band)):
        keep = scored if selector is None else (selector & scored)
        values = delta[keep]
        if values.size == 0:
            out[f"{name}.mean"] = out[f"{name}.p95"] = out[f"{name}.max"] = 0.0
            continue
        out[f"{name}.mean"] = float(values.mean())
        out[f"{name}.p95"] = float(np.percentile(values, 95))
        out[f"{name}.max"] = float(values.max())
    return out


def render_walle(script: Path, outgoing: Path, incoming: Path, destination: Path,
                 variant: str, appearance: str, progresses: list[float],
                 backing_scale: int, material_progress: float | None,
                 geometry_ease: tuple[float, float] | None = None) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    # Apple's ANIMATED reveal runs ahead of its own state parameter: the
    # exact-state sweeps put the radius at RMAX * state to under 4 px, but
    # against the presentation clock the same radius covers the frame at 0.64
    # rather than 1.  Feeding the eased state here tests that mapping without
    # touching the corpus-indexed capture path.
    geometry = progresses
    if geometry_ease is not None:
        scale, power, shift = geometry_ease
        geometry = [min(1.0, max(0.0, scale * max(p + shift, 0.0) ** power))
                    for p in progresses]
    environment = {
        "COMPOSE": "1",
        "APPEARANCE": appearance,
        "BACKING_SCALE": str(backing_scale),
        "PROGRESS": ",".join(f"{p:.6f}" for p in geometry),
    }
    # The rig's exact-state sweeps are SETTLED: its interior reads the same code
    # values at every radius (222.0 at progress 1/16 and 220.0 at 1, for
    # regular/light), so they measure the material and the geometry, not the
    # materialize curve.  Driving walle's clock from the same scalar would
    # instead compare Apple's finished glass against walle's easing thickness,
    # which is minimised only where that ease happens to reach 1.
    if material_progress is not None:
        environment["MATERIAL_PROGRESS"] = f"{material_progress:.6f}"
    elif geometry_ease is not None:
        # The geometry follows the measured clock-to-state mapping while the
        # material stays on the raw clock its own law was fitted against - which
        # is exactly what the live path now does, so this run is the combined
        # end-to-end number rather than either fix on its own.
        environment["MATERIAL_PROGRESS"] = ",".join(f"{p:.6f}" for p in progresses)

    subprocess.run([str(script), str(outgoing), str(incoming), str(destination), variant],
                   check=True, env={**dict(__import__("os").environ), **environment})


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--capture", type=Path, required=True,
                        help="glasscap --out directory from the M1")
    parser.add_argument("--render-script", type=Path, required=True)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--extent", type=int, default=2048)
    parser.add_argument("--backing-scale", type=int, default=2)
    parser.add_argument("--centre", type=float, nargs=2, default=(512.0, 614.4))
    parser.add_argument("--sequences", nargs="*", default=None)
    parser.add_argument("--mode", choices=("sweep", "dynamic"), default="sweep")
    parser.add_argument("--geometry-ease", type=float, nargs=3, default=None,
                        metavar=("SCALE", "POWER", "SHIFT"),
                        help="reveal state = min(1, SCALE * (clock + SHIFT)**POWER)")
    parser.add_argument("--material-progress", type=float, default=None,
                        help="pin the material clock (0.66 = full thickness); "
                             "omit to let the reveal progress drive it")
    parser.add_argument("--reuse", action="store_true",
                        help="score renders already on disk instead of re-rendering")
    parser.add_argument("--max-clock", type=float, default=None,
                        help="score only frames at or below this clock")
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()

    manifest = json.loads((arguments.capture / "manifest.json").read_text())
    # The exact-state sweeps are settled at every radius; the dynamic sequences
    # are the ones that actually animate, so they are what tests the arrival and
    # exit curve.  Their clock is the captured presentationProgress.
    if arguments.mode == "dynamic":
        sweeps = []
        for sequence in manifest["dynamicSequences"]:
            frames = [dict(f, progress=f["presentationProgress"])
                      for f in sequence["frames"]]
            sweeps.append(dict(sequence, frames=frames))
    else:
        sweeps = manifest["sweepSequences"]
    if arguments.sequences:
        sweeps = [s for s in sweeps if s["id"] in arguments.sequences]

    records: list[dict[str, object]] = []
    for sweep in sweeps:
        outgoing_name = sweep["outgoingBackground"]
        incoming_name = sweep["incomingBackground"]
        outgoing_path = arguments.capture / "reference" / f"{outgoing_name}.png"
        incoming_path = arguments.capture / "reference" / f"{incoming_name}.png"
        outgoing = load_png(outgoing_path)

        frames = [f for f in sweep["frames"] if f.get("stable", True)]
        if arguments.max_clock is not None:
            frames = [f for f in frames if float(f["progress"]) <= arguments.max_clock]
        progresses = [float(f["progress"]) for f in frames]
        destination = arguments.work / sweep["id"]
        if arguments.reuse and (destination / "composition-state-0000.bgra").exists():
            print(f"=== {sweep['id']}: scoring {len(progresses)} states already rendered",
                  flush=True)
        else:
            print(f"=== {sweep['id']}: rendering walle at {len(progresses)} states", flush=True)
            render_walle(arguments.render_script, outgoing_path, incoming_path,
                         destination,
                         sweep["overlay"], sweep["appearance"], progresses,
                         arguments.backing_scale, arguments.material_progress,
                         arguments.geometry_ease)

        height, width, _ = outgoing.shape
        y, x = np.mgrid[0:height, 0:width]
        distance = np.hypot(x - arguments.centre[0], y - arguments.centre[1])

        # The rig codes its presentation clock into a strip of the window's own
        # pixels and names it in the manifest.  Those pixels are a timestamp,
        # not the material, and walle does not render them - scoring them puts
        # a 255-code column into every frame's maximum.
        scored = np.ones((height, width), bool)
        for box in sweep.get("analysisExclusionPixels") or ():
            scored[box["y"]:box["y"] + box["height"],
                   box["x"]:box["x"] + box["width"]] = False

        for index, (frame, progress) in enumerate(zip(frames, progresses)):
            apple = load_png(arguments.capture / frame["file"])
            walle_path = destination / f"composition-state-{index:04d}.bgra"
            if not walle_path.exists():
                print(f"  MISSING {walle_path}", flush=True)
                continue
            walle = load_bgra(walle_path, arguments.extent)
            mask = np.fromfile(destination / f"state-{index:04d}.r8",
                               dtype=np.uint8).reshape(arguments.extent, arguments.extent)
            inside = mask > 0
            radius_walle = float(distance[inside].max()) if inside.any() else 0.0
            radius_apple = apple_radius(apple, outgoing, tuple(arguments.centre))
            band = np.abs(distance - radius_walle) < 8.0
            record = {
                "sequence": sweep["id"],
                "overlay": sweep["overlay"],
                "appearance": sweep["appearance"],
                "index": index,
                "progress": progress,
                "radiusApple": round(radius_apple, 1),
                "radiusWalle": round(radius_walle, 1),
                **{k: round(v, 3) for k, v in
                   metrics(walle, apple, inside, band, scored).items()},
            }
            records.append(record)
            print(f"  p={progress:6.4f}  R apple {radius_apple:7.1f} walle {radius_walle:7.1f}"
                  f"  | full {record['full.mean']:7.2f} p95 {record['full.p95']:6.1f}"
                  f" max {record['full.max']:6.1f}"
                  f"  | inside {record['inside.mean']:7.2f}", flush=True)

    if arguments.output:
        arguments.output.write_text(json.dumps(
            {"schemaVersion": 1,
             "classification": "walle production Vulkan vs M1 Liquid Glass, exact-state sweep",
             "osBuild": manifest.get("osBuild"),
             "hostModel": manifest.get("hostModel"),
             "backingScaleFactor": manifest.get("backingScaleFactor"),
             "records": records}, indent=2) + "\n")

    if records:
        full = np.array([r["full.mean"] for r in records])
        inside = np.array([r["inside.mean"] for r in records])
        print(f"\nALL {len(records)} states: full mean {full.mean():.2f} codes, "
              f"inside mean {inside.mean():.2f}, worst frame {full.max():.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
