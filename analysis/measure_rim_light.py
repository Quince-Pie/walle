#!/usr/bin/env python3
"""Measure Liquid Glass's EDGE - the bright rim inside it and the shadow out.

walle's shader carries a ring: a directional specular lobe with an up gain, a
down gain, a base gain, an offset centre and a width that scales with the
radius, all fitted from Human Interface Guidelines photographs.  Rendering it
back against a capture is what exposed it.  Apple's element has a two-pixel
bright band at its boundary - up to 130 code values above the interior - and
`regular` darkens the backdrop OUTSIDE itself, and walle draws neither: the
ring's gains are small enough to vanish and the shadow's base is zero.

What the captures say, over the gray ladder, the saturated colours, and three
element sizes:

  * the rim is ISOTROPIC.  Sampled in twelve 30-degree sectors it varies by
    under one code value in twenty-two, so there is no lobe, no light
    direction, and nothing for the ring's angular model to describe;
  * its profile is ABSOLUTE, not a fraction of the radius: 2.2 px deep at
    every size, peaking 0.6 px inside the boundary;
  * its amplitude saturates by a 256 px radius and is dimmer below that;
  * the rim's colour is a function of the backdrop, like the material's own
    transfer, and is fitted here the same way;
  * `regular` casts a shadow that reaches about 80 px and is a MULTIPLY on the
    backdrop in sRGB code space - out = 0.954 * backdrop + 0.76 at its
    darkest, which is a 4.3% darkening in light and 7.6% in dark.  `clear`
    casts none, to under half a code value.

Over a FLAT background the blur is the identity, so this cannot say whether
the rim reads the blurred backdrop or the raw one.  The step-edge check
(analysis/verify_structured_background.py) is where that separates.
"""

import argparse
import importlib.util
import json
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "material", ROOT / "analysis/derive_material_matrices.py")
MATERIAL = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(MATERIAL)

# Element radius in capture pixels at 2x, for scenes whose element fits inside
# its frame with room to read the shadow outside it.
SCENES = {"circle-0128-center": 128.0, "circle-0256-center": 256.0,
          "circle-0500-center": 500.0}
# The rim's own depth axis: distance INSIDE the boundary, in capture pixels.
# 0.6 is the first sample whose pixel lies wholly inside; nearer the boundary a
# pixel straddles it and what it holds is coverage, which the rasterizer
# already models and which varies with whatever is outside.
RIM_DEPTHS = np.round(np.arange(0.6, 2.6001, 0.2), 4)
SHADOW_DEPTHS = np.array([1.0, 2.0, 3.0, 4.0, 6.0, 8.0, 12.0, 16.0, 24.0,
                          32.0, 40.0, 48.0, 64.0, 80.0, 96.0])
SECTORS = 12
# The depths the colour laws are fitted AT, and where each profile is therefore
# normalised to one, so that the shader's `lerp(plateau, law(backdrop), w(u))`
# reproduces the measurement exactly at that depth rather than near it.  0.8 is
# also where analysis/derive_tint_law.py --region rim samples.
RIM_REFERENCE_DEPTH = 0.8
SHADOW_REFERENCE_DEPTH = 1.0

type JsonObject = dict[str, object]


def annulus(distance: np.ndarray, radius: float, tolerance: float
            ) -> np.ndarray:
    return np.abs(distance - radius) < tolerance


def read(path: Path):
    pixels = np.asarray(Image.open(path).convert("RGB")).astype(float)
    height, width, _ = pixels.shape
    y, x = np.mgrid[0:height, 0:width]
    centre = (width / 2.0, height / 2.0)
    distance = np.hypot(x + 0.5 - centre[0], y + 0.5 - centre[1])
    angle = np.degrees(np.arctan2(-(y + 0.5 - centre[1]), x + 0.5 - centre[0]))
    return pixels, distance, angle


def sample(pixels: np.ndarray, mask: np.ndarray) -> np.ndarray | None:
    return pixels[mask].mean(axis=0) if mask.sum() >= 8 else None


def isotropy(pixels: np.ndarray, distance: np.ndarray, angle: np.ndarray,
             radius: float, interior: np.ndarray) -> float | None:
    """How far the rim varies around the circle, as a fraction of its own
    excess over the interior.

    A directional lobe would put its peak on one side and its trough on the
    other, so the spread across twelve sectors is the whole angular signal in
    one number.  Reported as a fraction because a rim two code values from its
    interior cannot show a lobe either way, and skipped entirely below eight.
    """
    band = annulus(distance, radius - 0.8, 0.25)
    if band.sum() < SECTORS * 8:
        return None
    means = []
    for sector in range(SECTORS):
        low = -180.0 + sector * (360.0 / SECTORS)
        wedge = band & (angle >= low) & (angle < low + 360.0 / SECTORS)
        value = sample(pixels, wedge)
        if value is not None:
            means.append(value - interior)
    if len(means) < SECTORS - 1:
        return None
    stacked = np.array(means)
    excess = float(np.abs(stacked.mean(axis=0)).max())
    if excess < 8.0:
        return None
    return round(float(np.ptp(stacked, axis=0).max() / excess), 4)


def measure(shots: list[Path], scene: str, radius: float, background: str,
            variant: str, appearance: str) -> JsonObject | None:
    name = f"{background}__{scene}__{variant}__{appearance}.png"
    path = next((d / name for d in shots if (d / name).exists()), None)
    code = MATERIAL.background_code(background)
    if path is None or code is None:
        return None
    pixels, distance, angle = read(path)
    interior = sample(pixels, distance < radius * 0.6)
    if interior is None:
        return None

    rim = {}
    for depth in RIM_DEPTHS:
        value = sample(pixels, annulus(distance, radius - depth, 0.1))
        if value is not None:
            rim[float(depth)] = value
    shadow = {}
    for depth in SHADOW_DEPTHS:
        value = sample(pixels, annulus(distance, radius + depth, 0.5))
        if value is not None:
            shadow[float(depth)] = value
    if not rim:
        return None

    if RIM_REFERENCE_DEPTH not in rim:
        return None
    reference = rim[RIM_REFERENCE_DEPTH]
    peak_depth = max(rim, key=lambda d: float(np.abs(rim[d] - interior).max()))
    return {
        "scene": scene,
        "background": background,
        "backgroundCode": list(code),
        "variant": variant,
        "appearance": appearance,
        "elementRadiusPixels": radius,
        "interiorCodes": [round(float(v), 3) for v in interior],
        "peakDepthPixels": round(float(peak_depth), 2),
        "referenceCodes": [round(float(v), 3) for v in reference],
        "peakExcessCodes": round(
            float(np.abs(rim[peak_depth] - interior).max()), 3),
        "isotropySpreadCodes": isotropy(pixels, distance, angle, radius,
                                        interior),
        "shadowNearCodes": [round(float(v), 3)
                            for v in shadow[SHADOW_REFERENCE_DEPTH]]
                           if SHADOW_REFERENCE_DEPTH in shadow else None,
        "rimProfile": {f"{d:.1f}": [round(float(v), 3) for v in rim[d]]
                       for d in sorted(rim)},
        "shadowProfile": {f"{d:.0f}": [round(float(v), 3) for v in shadow[d]]
                          for d in sorted(shadow)},
    }


def amplitude_by_radius(records: list[JsonObject]) -> JsonObject:
    radii = sorted({r["elementRadiusPixels"] for r in records})
    shared = None
    for radius in radii:
        here = {r["background"] for r in records
                if r["elementRadiusPixels"] == radius}
        shared = here if shared is None else (shared & here)
    shared = shared or set()
    return {
        "backgroundCount": len(shared),
        "medianPeakExcessCodes": {
            f"{radius:.0f}": round(float(np.median([
                r["peakExcessCodes"] for r in records
                if r["elementRadiusPixels"] == radius
                and r["background"] in shared])), 3)
            for radius in radii
            if any(r["elementRadiusPixels"] == radius
                   and r["background"] in shared for r in records)
        },
    }


def shape(records: list[JsonObject], key: str, depths: np.ndarray,
          reference: str, at: float) -> JsonObject:
    """The profile's SHAPE, normalised out of its own amplitude.

    Each background gives the same curve scaled by however far its rim or its
    shadow sits from its own plateau, so dividing by that leaves the shape,
    and its spread across backgrounds says whether one shape is enough.
    """
    rows = []
    for record in records:
        plateau = (np.array(record["interiorCodes"]) if reference == "interior"
                   else np.array(record["backgroundCode"]))
        profile = record[key]
        values = []
        for depth in depths:
            entry = profile.get(f"{depth:.1f}" if reference == "interior"
                                else f"{depth:.0f}")
            values.append(np.nan if entry is None
                          else float(np.array(entry).mean() - plateau.mean()))
        values = np.array(values)
        finite = np.isfinite(values)
        if not finite.any():
            continue
        # Normalised at the depth the colour law was fitted at, NOT at the
        # profile's own maximum: the shader multiplies one by the other, so
        # they have to agree about which depth is one.
        index = int(np.argmin(np.abs(depths - at)))
        amplitude = values[index]
        # Only backgrounds whose own amplitude is worth normalising by: a rim
        # two code values from its interior is noise, and dividing by it turns
        # that noise into a shape.
        if abs(amplitude) < 8.0:
            continue
        rows.append(values / amplitude)
    if not rows:
        return {"sampleCount": 0}
    stacked = np.array(rows)
    return {
        "sampleCount": len(rows),
        "depthPixels": [round(float(d), 2) for d in depths],
        "weight": [round(float(v), 5) for v in np.nanmean(stacked, axis=0)],
        "spread": [round(float(v), 5) for v in np.nanstd(stacked, axis=0)],
    }


def transfer(records: list[JsonObject], key: str) -> JsonObject | None:
    """The rim's or the shadow's own colour law, fitted like the material's."""
    codes, values = [], []
    seen = set()
    for record in records:
        if record["background"] in seen:
            continue
        seen.add(record["background"])
        codes.append(record["backgroundCode"])
        values.append(record[key])
    codes, values = np.array(codes, float), np.array(values, float)
    if len(codes) < 24:
        return None
    keep = (values.min(axis=1) > MATERIAL.CLIP_LOW) & (
        values.max(axis=1) < MATERIAL.CLIP_HIGH)
    codes, values = codes[keep], values[keep]
    if len(codes) < 24:
        return None

    scored = []
    for order in MATERIAL.ORDERS:
        model = f"order{order}"
        held = MATERIAL.cross_validate(codes, values, model, 1.0)
        if held is None:
            continue
        solution = MATERIAL.solve(codes, values, model, 1.0)
        residual = MATERIAL.predict(codes, solution, model, 1.0) - values
        scored.append({
            "order": order,
            "termCount": int(solution.shape[0]),
            "heldOutRootMeanSquareCodes": round(held[0], 3),
            "heldOutMaximumCodes": round(held[1], 3),
            "inSampleRootMeanSquareCodes": round(
                float(np.sqrt((residual**2).mean())), 3),
            "solution": solution,
        })
    if not scored:
        return None
    chosen = min(scored, key=lambda e: e["heldOutRootMeanSquareCodes"])
    solution = chosen["solution"]
    for entry in scored:
        entry.pop("solution", None)
    residual = MATERIAL.predict(codes, solution, f"order{chosen['order']}",
                                1.0) - values
    return {
        "backgroundCount": int(len(codes)),
        "order": chosen["order"],
        "termExponents": [list(t) for t in MATERIAL.EXPONENTS[:len(solution)]],
        "coefficients": [[round(float(v), 8) for v in row] for row in solution],
        "rootMeanSquareResidualCodes": round(
            float(np.sqrt((residual**2).mean())), 3),
        "maximumResidualCodes": round(float(np.abs(residual).max()), 3),
        "heldOutRootMeanSquareCodes": chosen["heldOutRootMeanSquareCodes"],
        "heldOutMaximumCodes": chosen["heldOutMaximumCodes"],
        "candidates": scored,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--shots", type=Path, nargs="+", required=True)
    parser.add_argument("--scene", default="circle-0500-center",
                        help="the scene the colour laws are fitted from")
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()

    backgrounds = sorted({
        path.name.split("__")[0]
        for directory in arguments.shots
        for path in directory.glob(f"*__{arguments.scene}__regular__light.png")
        if MATERIAL.background_code(path.name.split("__")[0]) is not None
    })
    records = [
        record
        for scene, radius in SCENES.items()
        for background in backgrounds
        for variant in ("regular", "clear")
        for appearance in ("light", "dark")
        if (record := measure(arguments.shots, scene, radius, background,
                              variant, appearance)) is not None
    ]
    if not records:
        print("  no rim measurable in this corpus")
        return 1

    report: JsonObject = {"schemaVersion": 1, "osBuild": "25G76",
                          "classification": "Liquid Glass edge: rim and shadow",
                          "materials": {}}
    spreads = [r["isotropySpreadCodes"] for r in records
               if r["isotropySpreadCodes"] is not None]
    report["isotropyMedianSpreadFraction"] = round(
        float(np.median(spreads)), 4) if spreads else None
    report["isotropyHighSpreadFraction"] = round(
        float(np.percentile(spreads, 95)), 4) if spreads else None
    report["isotropySampleCount"] = len(spreads)
    print(f"  rim isotropy over {SECTORS} sectors and {len(spreads)} frames: "
          f"median spread {report['isotropyMedianSpreadFraction']} of the "
          f"rim's own excess, 95th percentile "
          f"{report['isotropyHighSpreadFraction']}\n")

    for variant in ("regular", "clear"):
        for appearance in ("light", "dark"):
            picked = [r for r in records if r["variant"] == variant
                      and r["appearance"] == appearance]
            fitted = [r for r in picked if r["scene"] == arguments.scene]
            entry: JsonObject = {
                "rimShape": shape(fitted, "rimProfile", RIM_DEPTHS,
                                  "interior", RIM_REFERENCE_DEPTH),
                "shadowShape": shape(fitted, "shadowProfile", SHADOW_DEPTHS,
                                     "background", SHADOW_REFERENCE_DEPTH),
                "rimTransfer": transfer(fitted, "referenceCodes"),
                "shadowTransfer": transfer(
                    [r for r in fitted if r["shadowNearCodes"] is not None],
                    "shadowNearCodes"),
                # Only backgrounds captured at EVERY radius, so the radii are
                # compared on the same colours rather than on whichever set
                # each scene happens to carry.
                "amplitudeByRadius": amplitude_by_radius(picked),
            }
            report["materials"][f"{variant}/{appearance}"] = entry
            rim = entry["rimTransfer"]
            print(f"  {variant:8s} {appearance:5s} "
                  f"rim shape n={entry['rimShape'].get('sampleCount', 0):3d} "
                  f"shadow n={entry['shadowShape'].get('sampleCount', 0):3d}"
                  + (f"   rim transfer order {rim['order']} from "
                     f"{rim['backgroundCount']} backgrounds, "
                     f"{rim['rootMeanSquareResidualCodes']} rms / "
                     f"{rim['heldOutRootMeanSquareCodes']} held out"
                     if rim else "   rim transfer: too few backgrounds"))
            shadow = entry["shadowTransfer"]
            if shadow:
                print(f"        shadow transfer order {shadow['order']} from "
                      f"{shadow['backgroundCount']} backgrounds, "
                      f"{shadow['rootMeanSquareResidualCodes']} rms / "
                      f"{shadow['heldOutRootMeanSquareCodes']} held out")
            print("        peak excess by radius, over the "
                  f"{entry['amplitudeByRadius']['backgroundCount']} "
                  "backgrounds every radius shares: "
                  + "  ".join(
                      f"R={k}:{v:+.2f}" for k, v in
                      entry["amplitudeByRadius"]["medianPeakExcessCodes"].items()))
            if entry["rimShape"].get("sampleCount"):
                print("        rim w(u):    " + " ".join(
                    f"{v:5.3f}" for v in entry["rimShape"]["weight"]))
            if entry["shadowShape"].get("sampleCount"):
                print("        shadow s(u): " + " ".join(
                    f"{v:5.3f}" for v in entry["shadowShape"]["weight"]))

    if arguments.output is not None:
        arguments.output.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n",
            encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
