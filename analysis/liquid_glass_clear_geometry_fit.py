#!/usr/bin/env python3
"""Identify clear Liquid Glass' geometry-selected reconstruction states."""

import argparse
import hashlib
import json
import platform
from dataclasses import dataclass
from importlib.metadata import version as package_version
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from liquid_glass_spatial_fit import CaptureSet


type FloatArray = NDArray[np.float64]
type BoolArray = NDArray[np.bool_]
type JsonObject = dict[str, Any]

RIG_VERSION = "2.13.0"
SCENES = (
    "circle-4000-center",
    "circle-6000-upper-left",
    "rect-6000x4000-r000-center",
)
MATERIAL = "clear"
APPEARANCE = "dark"
TRAINING_BACKGROUNDS = tuple(
    f"noise-rgb-a064-kernel-train-{index:02d}" for index in range(4)
)
DEVELOPMENT_EXPOSED_BACKGROUNDS = tuple(
    f"noise-rgb-a064-kernel-holdout-{index:02d}" for index in range(2)
)
PAIR_NAMES = tuple(
    (SCENES[left], SCENES[right])
    for left in range(len(SCENES))
    for right in range(left + 1, len(SCENES))
)
IDENTITY_MARGIN_PIXELS = 32
CONTRAST_MARGIN_PIXELS = 512
CONTRAST_BIN_WIDTH = 0.02
CONTRAST_MAXIMUM_COORDINATE = 0.26
THRESHOLD_GRID_STEP = 1e-6

# Broad, disjoint brackets around the positive response jumps measured from
# the four training fields. Exact thresholds are selected from bitwise
# cross-geometry equality constraints inside these brackets.
THRESHOLD_BRACKET_CENTERS = np.asarray(
    (
        0.0800,
        0.1576,
        0.2290,
        0.3037,
        0.3753,
        0.4435,
        0.5184,
        0.5866,
        0.6550,
        0.7234,
        0.7911,
        0.8596,
    ),
    dtype=np.float64,
)


@dataclass(frozen=True, slots=True)
class ShapeGeometry:
    kind: str
    center_x: float
    center_y: float
    half_width: float
    half_height: float
    corner_radius: float

    @classmethod
    def from_capture_set(
        cls,
        captures: CaptureSet,
        scene_name: str,
    ) -> "ShapeGeometry":
        scene = captures.scenes[scene_name]
        shapes = scene.get("shapes")
        if not isinstance(shapes, list) or len(shapes) != 1:
            raise ValueError(f"{scene_name} must contain exactly one shape")
        shape = shapes[0]
        scale = float(captures.manifest["backingScaleFactor"])
        return cls(
            kind=str(shape["kind"]),
            center_x=float(shape["centerX"]) * scale,
            center_y=float(shape["centerY"]) * scale,
            half_width=float(shape["width"]) * scale / 2.0,
            half_height=float(shape["height"]) * scale / 2.0,
            corner_radius=float(shape["cornerRadius"]) * scale,
        )

    @property
    def inradius(self) -> float:
        return min(self.half_width, self.half_height)

    def normalized_signed_distance(
        self,
        x: FloatArray,
        y: FloatArray,
    ) -> FloatArray:
        if self.inradius <= 0.0:
            raise ValueError("shape dimensions must be positive")
        dx = np.abs(x - self.center_x)
        dy = np.abs(y - self.center_y)
        if self.kind == "circle":
            if not np.isclose(self.half_width, self.half_height):
                raise ValueError("circle dimensions must be equal")
            signed_distance = np.hypot(dx, dy) - self.half_width
        elif self.kind in {"roundedRect", "capsule"}:
            radius = min(self.corner_radius, self.inradius)
            qx = dx - (self.half_width - radius)
            qy = dy - (self.half_height - radius)
            outside = np.hypot(np.maximum(qx, 0.0), np.maximum(qy, 0.0))
            inside = np.minimum(np.maximum(qx, qy), 0.0)
            signed_distance = outside + inside - radius
        else:
            raise ValueError(f"unsupported shape kind: {self.kind}")
        return 1.0 + signed_distance / self.inradius

    def coordinate_hypothesis(
        self,
        name: str,
        x: FloatArray,
        y: FloatArray,
    ) -> FloatArray:
        dx = x - self.center_x
        dy = y - self.center_y
        if name == "normalized-signed-distance":
            return self.normalized_signed_distance(x, y)
        if name == "normalized-bounding-ellipse":
            return np.hypot(dx / self.half_width, dy / self.half_height)
        if name == "width-normalized-radius":
            return np.hypot(dx, dy) / self.half_width
        if name == "height-normalized-radius":
            return np.hypot(dx, dy) / self.half_height
        if name == "normalized-box-maximum":
            return np.maximum(
                np.abs(dx) / self.half_width,
                np.abs(dy) / self.half_height,
            )
        raise ValueError(f"unknown coordinate hypothesis: {name}")


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def region_coordinates(
    shape: tuple[int, int],
    *,
    margin: int,
) -> tuple[tuple[slice, slice], FloatArray, FloatArray]:
    height, width = shape
    if margin < 0 or height <= 2 * margin or width <= 2 * margin:
        raise ValueError("invalid coordinate region")
    region = (
        slice(margin, height - margin),
        slice(margin, width - margin),
    )
    y = np.arange(margin, height - margin, dtype=np.float64)
    x = np.arange(margin, width - margin, dtype=np.float64)
    grid_y, grid_x = np.meshgrid(y, x, indexing="ij")
    return region, grid_x, grid_y


def pair_equalities(
    captures: CaptureSet,
    backgrounds: tuple[str, ...],
    *,
    region: tuple[slice, slice],
) -> dict[tuple[str, str], BoolArray]:
    result = {
        pair: np.ones(
            captures.image(
                backgrounds[0],
                pair[0],
                MATERIAL,
                APPEARANCE,
            )[region].shape[:2],
            dtype=np.bool_,
        )
        for pair in PAIR_NAMES
    }
    for background in backgrounds:
        images = {
            scene: captures.image(
                background,
                scene,
                MATERIAL,
                APPEARANCE,
            )[region].astype(np.uint8)
            for scene in SCENES
        }
        for pair in PAIR_NAMES:
            result[pair] &= np.all(images[pair[0]] == images[pair[1]], axis=2)
    return result


def threshold_brackets() -> tuple[tuple[float, float], ...]:
    centers = THRESHOLD_BRACKET_CENTERS
    boundaries = np.empty(centers.size + 1, dtype=np.float64)
    boundaries[0] = 0.0
    boundaries[-1] = 1.0
    boundaries[1:-1] = (centers[:-1] + centers[1:]) / 2.0
    return tuple(
        (float(boundaries[index]), float(boundaries[index + 1]))
        for index in range(centers.size)
    )


def refine_threshold(
    coordinates: dict[str, FloatArray],
    equalities: dict[tuple[str, str], BoolArray],
    *,
    lower: float,
    upper: float,
    step: float = THRESHOLD_GRID_STEP,
) -> JsonObject:
    if step <= 0.0 or not 0.0 <= lower < upper <= 1.0:
        raise ValueError("invalid threshold search")
    lows: list[FloatArray] = []
    highs: list[FloatArray] = []
    weights: list[NDArray[np.int8]] = []
    for pair, equality in equalities.items():
        first = coordinates[pair[0]].reshape(-1)
        second = coordinates[pair[1]].reshape(-1)
        low = np.minimum(first, second)
        high = np.maximum(first, second)
        selected = (low >= lower) & (high <= upper) & (high > low)
        lows.append(low[selected])
        highs.append(high[selected])
        weights.append(
            np.where(equality.reshape(-1)[selected], -1, 1).astype(np.int8)
        )
    low = np.concatenate(lows)
    high = np.concatenate(highs)
    weight = np.concatenate(weights)
    if low.size == 0:
        raise ValueError("threshold bracket has no pair constraints")

    grid = np.arange(lower, upper + step, step, dtype=np.float64)
    difference = np.zeros(grid.size + 1, dtype=np.int64)
    left = np.searchsorted(grid, low, side="right")
    right = np.searchsorted(grid, high, side="right")
    np.add.at(difference, left, weight)
    np.add.at(difference, right, -weight)
    score = np.cumsum(difference[:-1])
    best = int(score.max())
    best_indexes = np.flatnonzero(score == best)
    best_lower = float(grid[best_indexes[0]])
    best_upper = float(grid[best_indexes[-1]])
    return {
        "bracket": [lower, upper],
        "constraintPairs": int(low.size),
        "gridStep": step,
        "bestScore": best,
        "bestRange": [best_lower, best_upper],
        "selected": (best_lower + best_upper) / 2.0,
    }


def infer_thresholds(
    coordinates: dict[str, FloatArray],
    equalities: dict[tuple[str, str], BoolArray],
) -> tuple[FloatArray, list[JsonObject]]:
    records = [
        refine_threshold(
            coordinates,
            equalities,
            lower=lower,
            upper=upper,
        )
        for lower, upper in threshold_brackets()
    ]
    return (
        np.asarray([record["selected"] for record in records]),
        records,
    )


def equality_metrics(same_state: BoolArray, equal_output: BoolArray) -> JsonObject:
    if same_state.shape != equal_output.shape or same_state.size == 0:
        raise ValueError("equality arrays must have the same nonempty shape")
    same_count = int(np.count_nonzero(same_state))
    different_count = int(same_state.size - same_count)
    equal_same = int(np.count_nonzero(same_state & equal_output))
    equal_different = int(np.count_nonzero(~same_state & equal_output))
    return {
        "pixels": int(same_state.size),
        "sameStatePixels": same_count,
        "differentStatePixels": different_count,
        "equalOutputAndSameStatePixels": equal_same,
        "equalOutputAndDifferentStatePixels": equal_different,
        "sameStateFraction": same_count / same_state.size,
        "equalOutputFraction": float(np.mean(equal_output)),
        "equalOutputGivenSameState": (
            equal_same / same_count if same_count else None
        ),
        "equalOutputGivenDifferentState": (
            equal_different / different_count if different_count else None
        ),
    }


def identity_report(
    captures: CaptureSet,
    backgrounds: tuple[str, ...],
    *,
    region: tuple[slice, slice],
    coordinates: dict[str, FloatArray],
    thresholds: FloatArray,
) -> JsonObject:
    joint_equalities = pair_equalities(
        captures,
        backgrounds,
        region=region,
    )
    states = {
        scene: np.digitize(coordinate, thresholds)
        for scene, coordinate in coordinates.items()
    }
    pairs: JsonObject = {}
    for pair in PAIR_NAMES:
        pair_key = f"{pair[0]}|{pair[1]}"
        same_state = states[pair[0]] == states[pair[1]]
        per_background: JsonObject = {}
        for background in backgrounds:
            first = captures.image(
                background,
                pair[0],
                MATERIAL,
                APPEARANCE,
            )[region]
            second = captures.image(
                background,
                pair[1],
                MATERIAL,
                APPEARANCE,
            )[region]
            per_background[background] = equality_metrics(
                same_state,
                np.all(first == second, axis=2),
            )
        pairs[pair_key] = {
            "jointSignature": equality_metrics(
                same_state,
                joint_equalities[pair],
            ),
            "perBackground": per_background,
        }
    return {
        "backgrounds": list(backgrounds),
        "jointSignatureChannelsPerPixel": len(backgrounds) * 3,
        "pairs": pairs,
    }


def contrast_collapse_report(
    captures: CaptureSet,
    geometries: dict[str, ShapeGeometry],
) -> JsonObject:
    sample = captures.image(
        TRAINING_BACKGROUNDS[0],
        SCENES[0],
        MATERIAL,
        APPEARANCE,
    )
    region, grid_x, grid_y = region_coordinates(
        sample.shape[:2],
        margin=CONTRAST_MARGIN_PIXELS,
    )
    energy: dict[str, FloatArray] = {}
    for scene in SCENES:
        accumulated = np.zeros(grid_x.shape, dtype=np.float64)
        for background in TRAINING_BACKGROUNDS:
            image = captures.image(
                background,
                scene,
                MATERIAL,
                APPEARANCE,
            )[region]
            centered = image - image.mean(axis=(0, 1), keepdims=True)
            accumulated += np.mean(np.square(centered), axis=2)
        energy[scene] = accumulated / len(TRAINING_BACKGROUNDS)

    bins = np.arange(
        0.0,
        CONTRAST_MAXIMUM_COORDINATE + CONTRAST_BIN_WIDTH,
        CONTRAST_BIN_WIDTH,
    )
    centers = (bins[:-1] + bins[1:]) / 2.0
    hypotheses = (
        "normalized-signed-distance",
        "normalized-bounding-ellipse",
        "width-normalized-radius",
        "height-normalized-radius",
        "normalized-box-maximum",
    )
    records: JsonObject = {}
    for hypothesis in hypotheses:
        scene_curves: dict[str, FloatArray] = {}
        scene_counts: dict[str, list[int]] = {}
        for scene in SCENES:
            coordinate = geometries[scene].coordinate_hypothesis(
                hypothesis,
                grid_x,
                grid_y,
            )
            indexes = np.digitize(coordinate.reshape(-1), bins) - 1
            values = energy[scene].reshape(-1)
            curve = np.full(centers.size, np.nan, dtype=np.float64)
            counts: list[int] = []
            for index in range(centers.size):
                selected = indexes == index
                count = int(np.count_nonzero(selected))
                counts.append(count)
                if count:
                    curve[index] = np.sqrt(values[selected].mean())
            scene_curves[scene] = curve
            scene_counts[scene] = counts
        matrix = np.stack([scene_curves[scene] for scene in SCENES])
        valid = np.all(np.isfinite(matrix), axis=0)
        records[hypothesis] = {
            "crossSceneCollapseRmsCodes": float(
                np.sqrt(np.mean(np.var(matrix[:, valid], axis=0)))
            ),
            "commonCoordinateBinCount": int(np.count_nonzero(valid)),
            "coordinateBinCenters": centers[valid].tolist(),
            "rmsContrastCodesByScene": {
                scene: scene_curves[scene][valid].tolist() for scene in SCENES
            },
            "pixelCountsByScene": {
                scene: np.asarray(scene_counts[scene])[valid].tolist()
                for scene in SCENES
            },
        }
    ranked = sorted(
        records,
        key=lambda name: float(records[name]["crossSceneCollapseRmsCodes"]),
    )
    return {
        "trainingBackgrounds": list(TRAINING_BACKGROUNDS),
        "boundaryExclusionPixels": CONTRAST_MARGIN_PIXELS,
        "coordinateBinWidth": CONTRAST_BIN_WIDTH,
        "maximumCommonCoordinate": CONTRAST_MAXIMUM_COORDINATE,
        "selectionRule": "minimum cross-scene RMS contrast-curve disagreement",
        "rankedHypotheses": ranked,
        "records": records,
    }


def fit_report(captures: CaptureSet) -> JsonObject:
    if captures.manifest.get("rigVersion") != RIG_VERSION:
        raise ValueError(f"clear geometry fit requires rig {RIG_VERSION}")
    sample = captures.image(
        TRAINING_BACKGROUNDS[0],
        SCENES[0],
        MATERIAL,
        APPEARANCE,
    )
    region, grid_x, grid_y = region_coordinates(
        sample.shape[:2],
        margin=IDENTITY_MARGIN_PIXELS,
    )
    geometries = {
        scene: ShapeGeometry.from_capture_set(captures, scene)
        for scene in SCENES
    }
    coordinates = {
        scene: geometry.normalized_signed_distance(grid_x, grid_y)
        for scene, geometry in geometries.items()
    }
    training_equalities = pair_equalities(
        captures,
        TRAINING_BACKGROUNDS,
        region=region,
    )
    thresholds, threshold_records = infer_thresholds(
        coordinates,
        training_equalities,
    )
    return {
        "clearGeometryFitSchemaVersion": 1,
        "analysisImplementation": {
            "file": "analysis/liquid_glass_clear_geometry_fit.py",
            "sha256": file_sha256(Path(__file__).resolve()),
            "python": platform.python_version(),
            "numpy": package_version("numpy"),
        },
        "source": {
            "rigVersion": captures.manifest.get("rigVersion"),
            "ciCommit": captures.manifest.get("ciCommit"),
            "scenes": list(SCENES),
            "material": MATERIAL,
            "appearance": APPEARANCE,
        },
        "policy": {
            "productionShaderModified": False,
            "qualityGate": (
                "zero unequal decoded channels on fresh protected Apple captures"
            ),
            "developmentDisclosure": (
                "The two v2.13 kernel holdouts were opened during geometry "
                "identification. They are reported as development-exposed "
                "confirmation and are not represented as a fresh final gate."
            ),
        },
        "coordinateDefinition": {
            "name": "normalized signed-distance depth",
            "formula": (
                "1 + signedDistance(point, shapeBoundary) / "
                "signedDistanceMagnitude(shapeCenter, shapeBoundary)"
            ),
            "pixelCoordinateConvention": (
                "integer top-left capture coordinates at the manifest backing scale"
            ),
            "geometries": {
                scene: {
                    "kind": geometry.kind,
                    "centerPixels": [geometry.center_x, geometry.center_y],
                    "halfExtentsPixels": [
                        geometry.half_width,
                        geometry.half_height,
                    ],
                    "cornerRadiusPixels": geometry.corner_radius,
                    "inradiusPixels": geometry.inradius,
                }
                for scene, geometry in geometries.items()
            },
        },
        "contrastCoordinateSelection": contrast_collapse_report(
            captures,
            geometries,
        ),
        "stateThresholdInference": {
            "trainingBackgrounds": list(TRAINING_BACKGROUNDS),
            "boundaryExclusionPixels": IDENTITY_MARGIN_PIXELS,
            "selection": (
                "Within each training-response bracket, maximize differing-"
                "signature intervals crossed minus equal-signature intervals crossed."
            ),
            "thresholds": thresholds.tolist(),
            "records": threshold_records,
        },
        "trainingStateIdentity": identity_report(
            captures,
            TRAINING_BACKGROUNDS,
            region=region,
            coordinates=coordinates,
            thresholds=thresholds,
        ),
        "developmentExposedStateIdentity": identity_report(
            captures,
            DEVELOPMENT_EXPOSED_BACKGROUNDS,
            region=region,
            coordinates=coordinates,
            thresholds=thresholds,
        ),
        "interpretation": (
            "Geometry selects a finite clear-material reconstruction state. "
            "This report identifies the selector; it does not yet identify "
            "the exact source-to-state filter or authorize a renderer change."
        ),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Identify geometry-selected clear Liquid Glass states.",
    )
    parser.add_argument("captures", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    captures = CaptureSet.open(args.captures)
    try:
        report = fit_report(captures)
    finally:
        captures.close()
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
