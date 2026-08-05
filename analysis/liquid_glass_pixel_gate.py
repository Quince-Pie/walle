#!/usr/bin/env python3
"""Compare Walle's exact GPU output with photographed Apple Liquid Glass."""

import argparse
import hashlib
import io
import json
import math
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from zipfile import ZipFile

import numpy as np
from numpy.typing import NDArray
from PIL import Image
from scipy import __version__ as scipy_version
from scipy import ndimage
from skimage import color, metrics
from skimage import __version__ as skimage_version

from walle_shader_renderer import (
    WallpaperTextures,
    WalleShaderRenderer,
    file_sha256,
    rgba8,
)


type JsonObject = dict[str, Any]
type CodeImage = NDArray[np.uint8]
type BoolImage = NDArray[np.bool_]

PROTECTED_ERROR_METRICS = (
    "full.meanAbsoluteCodes",
    "full.rmseCodes",
    "full.p95AbsoluteCodes",
    "full.p99AbsoluteCodes",
    "full.maximumAbsoluteCodes",
    "active.meanAbsoluteCodes",
    "active.rmseCodes",
    "active.p95AbsoluteCodes",
    "active.maximumAbsoluteCodes",
    "edge.meanAbsoluteCodes",
    "edge.maximumAbsoluteCodes",
    "edgeWeightedMeanAbsoluteCodes",
    "perceptual.oneMinusSSIM",
    "perceptual.deltaE2000Mean",
    "perceptual.deltaE2000P95",
    "perceptual.deltaE2000Maximum",
)


def percentile(values: NDArray[np.floating[Any]], quantile: float) -> float:
    return float(np.percentile(values, quantile)) if values.size else 0.0


def numeric_summary(values: NDArray[np.floating[Any]]) -> JsonObject:
    if not values.size:
        return {
            "meanAbsoluteCodes": 0.0,
            "rmseCodes": 0.0,
            "p95AbsoluteCodes": 0.0,
            "p99AbsoluteCodes": 0.0,
            "maximumAbsoluteCodes": 0.0,
        }
    return {
        "meanAbsoluteCodes": float(values.mean()),
        "rmseCodes": float(np.sqrt(np.mean(np.square(values)))),
        "p95AbsoluteCodes": percentile(values, 95),
        "p99AbsoluteCodes": percentile(values, 99),
        "maximumAbsoluteCodes": float(values.max()),
    }


def exclusion_mask(
    *,
    width: int,
    height: int,
    exclusions: object,
) -> BoolImage:
    included = np.ones((height, width), dtype=np.bool_)
    if not isinstance(exclusions, list):
        return included
    for value in exclusions:
        if not isinstance(value, dict):
            continue
        x = int(value.get("x", 0))
        y = int(value.get("y", 0))
        rectangle_width = int(value.get("width", 0))
        rectangle_height = int(value.get("height", 0))
        if rectangle_width <= 0 or rectangle_height <= 0:
            continue
        left = max(0, x)
        top = max(0, y)
        right = min(width, x + rectangle_width)
        bottom = min(height, y + rectangle_height)
        included[top:bottom, left:right] = False
    return included


def activity_mask(
    apple: CodeImage,
    outgoing: CodeImage,
    included: BoolImage,
) -> BoolImage:
    active = np.any(apple[..., :3] != outgoing[..., :3], axis=2) & included
    if active.any():
        active = ndimage.binary_dilation(active, iterations=3) & included
    return active


def bounding_box(mask: BoolImage, padding: int = 16) -> tuple[slice, slice]:
    rows, columns = np.nonzero(mask)
    if not rows.size:
        return slice(0, mask.shape[0]), slice(0, mask.shape[1])
    top = max(0, int(rows.min()) - padding)
    bottom = min(mask.shape[0], int(rows.max()) + padding + 1)
    left = max(0, int(columns.min()) - padding)
    right = min(mask.shape[1], int(columns.max()) + padding + 1)
    return slice(top, bottom), slice(left, right)


def perceptual_metrics(
    apple: CodeImage,
    rendered: CodeImage,
    active: BoolImage,
    included: BoolImage,
) -> JsonObject:
    region = active if active.any() else included
    rows, columns = bounding_box(region)
    apple_crop = apple[rows, columns, :3]
    rendered_crop = rendered[rows, columns, :3]
    mask_crop = region[rows, columns]
    included_crop = included[rows, columns]
    step = 4
    apple_small = apple_crop[::step, ::step]
    rendered_small = rendered_crop[::step, ::step].copy()
    mask_small = mask_crop[::step, ::step]
    included_small = included_crop[::step, ::step]
    rendered_small[~included_small] = apple_small[~included_small]
    minimum_side = min(apple_small.shape[:2])
    if minimum_side < 7:
        ssim = 1.0 if np.array_equal(apple_small, rendered_small) else 0.0
    else:
        ssim = float(
            metrics.structural_similarity(
                apple_small,
                rendered_small,
                channel_axis=2,
                data_range=255,
            )
        )
    apple_lab = color.rgb2lab(apple_small.astype(np.float32) / 255.0)
    rendered_lab = color.rgb2lab(rendered_small.astype(np.float32) / 255.0)
    delta = color.deltaE_ciede2000(apple_lab, rendered_lab)
    selected = delta[mask_small]
    if not selected.size:
        selected = delta.reshape(-1)
    return {
        "sampleStride": step,
        "ssim": ssim,
        "oneMinusSSIM": 1.0 - ssim,
        "deltaE2000Mean": float(selected.mean()),
        "deltaE2000P95": percentile(selected, 95),
        "deltaE2000Maximum": float(selected.max(initial=0.0)),
    }


def frame_metrics(
    *,
    apple: CodeImage,
    rendered: CodeImage,
    outgoing: CodeImage,
    exclusions: object,
) -> JsonObject:
    if apple.shape != rendered.shape or apple.shape != outgoing.shape:
        raise ValueError("Apple, Walle, and source images must have equal dimensions")
    height, width = apple.shape[:2]
    included = exclusion_mask(
        width=width,
        height=height,
        exclusions=exclusions,
    )
    active = activity_mask(apple, outgoing, included)
    absolute = np.abs(
        rendered[..., :3].astype(np.int16) - apple[..., :3].astype(np.int16)
    ).astype(np.float32)
    full_values = absolute[included]
    active_values = absolute[active] if active.any() else full_values

    luminance = np.tensordot(
        apple[..., :3].astype(np.float32),
        np.array([0.2126, 0.7152, 0.0722], dtype=np.float32),
        axes=([2], [0]),
    )
    gradient = np.hypot(
        ndimage.sobel(luminance, axis=0, mode="nearest"),
        ndimage.sobel(luminance, axis=1, mode="nearest"),
    )
    included_gradient = gradient[included]
    scale = percentile(included_gradient, 95)
    normalized_gradient = (
        np.clip(gradient / scale, 0.0, 1.0) if scale > 0 else np.zeros_like(gradient)
    )
    weights = 1.0 + 4.0 * normalized_gradient
    per_pixel_absolute = absolute.mean(axis=2)
    weighted = float(
        np.sum(per_pixel_absolute[included] * weights[included])
        / np.sum(weights[included])
    )
    edge_threshold = percentile(included_gradient, 90)
    edge = (gradient >= edge_threshold) & included if scale > 0 else active
    edge_values = absolute[edge] if edge.any() else active_values

    full = numeric_summary(full_values)
    full["changedPixelFraction"] = float(
        np.mean(np.any(absolute > 0, axis=2)[included])
    )
    active_summary = numeric_summary(active_values)
    active_summary["pixels"] = int(active.sum())
    edge_summary = numeric_summary(edge_values)
    edge_summary["pixels"] = int(edge.sum())
    return {
        "includedPixels": int(included.sum()),
        "full": full,
        "active": active_summary,
        "edge": edge_summary,
        "edgeWeightedMeanAbsoluteCodes": weighted,
        "perceptual": perceptual_metrics(
            apple,
            rendered,
            active,
            included,
        ),
    }


def flatten_metrics(record: JsonObject, prefix: str = "") -> dict[str, float]:
    result: dict[str, float] = {}
    for key, value in record.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            result.update(flatten_metrics(value, path))
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            result[path] = float(value)
    return result


@dataclass(slots=True, kw_only=True)
class CaptureArtifact:
    path: Path
    archive: ZipFile | None
    manifest: JsonObject
    references: dict[str, JsonObject]
    sha256: str | None
    manifest_sha256: str

    @classmethod
    def open(cls, path: Path) -> "CaptureArtifact":
        if path.is_dir():
            archive = None
            manifest_bytes = (path / "manifest.json").read_bytes()
            artifact_sha256 = None
        else:
            archive = ZipFile(path)
            manifest_bytes = archive.read("manifest.json")
            artifact_sha256 = file_sha256(path)
        manifest = json.loads(manifest_bytes)
        if not isinstance(manifest, dict):
            if archive is not None:
                archive.close()
            raise ValueError("manifest must be a JSON object")
        references = {
            str(record["background"]): record
            for record in manifest.get("references", [])
        }
        return cls(
            path=path,
            archive=archive,
            manifest=manifest,
            references=references,
            sha256=artifact_sha256,
            manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
        )

    def close(self) -> None:
        if self.archive is not None:
            self.archive.close()

    def image(self, relative: str) -> CodeImage:
        source: io.BytesIO | Path
        if self.archive is None:
            source = self.path / relative
        else:
            source = io.BytesIO(self.archive.read(relative))
        with Image.open(source) as image:
            return rgba8(image)

    def reference(self, background: str) -> CodeImage:
        record = self.references.get(background)
        if record is None:
            raise ValueError(f"missing reference background {background!r}")
        return self.image(str(record["file"]))


class PixelGate:
    def __init__(
        self,
        *,
        artifact: CaptureArtifact,
        shader: Path,
        vertex_shader: Path,
        frame_step: int,
    ) -> None:
        self.artifact = artifact
        self.shader = shader
        self.vertex_shader = vertex_shader
        self.frame_step = frame_step
        width, height = (int(value) for value in artifact.manifest["windowPoints"])
        scale = float(artifact.manifest.get("backingScaleFactor", 1))
        self.width = round(width * scale)
        self.height = round(height * scale)
        self.renderer = WalleShaderRenderer(
            width=self.width,
            height=self.height,
            vertex_shader=vertex_shader,
            fragment_shader=shader,
        )
        self.reference_pixels: dict[str, CodeImage] = {}
        self.texture_cache: dict[tuple[str, bool], WallpaperTextures] = {}

    def close(self) -> None:
        for textures in self.texture_cache.values():
            textures.release()
        self.renderer.close()

    def reference(self, background: str) -> CodeImage:
        if background not in self.reference_pixels:
            self.reference_pixels[background] = self.artifact.reference(background)
        return self.reference_pixels[background]

    def textures(self, background: str, *, regular: bool) -> WallpaperTextures:
        key = (background, regular)
        if key not in self.texture_cache:
            self.texture_cache[key] = self.renderer.upload_wallpaper(
                self.reference(background),
                regular=regular,
            )
        return self.texture_cache[key]

    def selected_frames(self, frames: list[JsonObject]) -> list[JsonObject]:
        if self.frame_step == 1:
            return frames
        selected = frames[:: self.frame_step]
        if frames and frames[-1] not in selected:
            selected.append(frames[-1])
        return selected

    def run(self) -> JsonObject:
        manifest = self.artifact.manifest
        origin = manifest.get("transitionOriginNormalized", [0.25, 0.30])
        center = (self.width * float(origin[0]), self.height * float(origin[1]))
        farthest = max(
            math.hypot(x - center[0], y - center[1])
            for x in (0.0, float(self.width))
            for y in (0.0, float(self.height))
        )
        cases: JsonObject = {}
        partitions: dict[str, list[dict[str, float]]] = {
            "training": [],
            "holdout": [],
        }

        for sequence in manifest.get("dynamicSequences", []):
            mode = str(sequence.get("mode"))
            if mode not in {
                "wallpaper-transition",
                "wallpaper-transition-reverse",
            }:
                continue
            partition = "training" if mode == "wallpaper-transition" else "holdout"
            regular = sequence.get("overlay") == "regular"
            outgoing_name = str(sequence["outgoingBackground"])
            incoming_name = str(sequence["incomingBackground"])
            outgoing_pixels = self.reference(outgoing_name)
            outgoing_textures = self.textures(
                outgoing_name,
                regular=regular,
            )
            incoming_textures = self.textures(
                incoming_name,
                regular=regular,
            )
            sequence_id = str(sequence["id"])
            for frame in self.selected_frames(sequence["frames"]):
                progress = float(frame["presentationProgress"])
                rendered = self.renderer.render(
                    outgoing=outgoing_textures,
                    incoming=incoming_textures,
                    time=progress,
                    center_top_left=center,
                    maximum_radius=farthest * 1.03,
                    regular=regular,
                )
                apple = self.artifact.image(str(frame["file"]))
                measured = frame_metrics(
                    apple=apple,
                    rendered=rendered,
                    outgoing=outgoing_pixels,
                    exclusions=sequence.get("analysisExclusionPixels"),
                )
                key = f"{sequence_id}|frame-{int(frame['index']):04d}"
                flattened = {
                    name: value
                    for name, value in flatten_metrics(measured).items()
                    if name in PROTECTED_ERROR_METRICS
                }
                partitions[partition].append(flattened)
                cases[key] = {
                    "partition": partition,
                    "sequence": sequence_id,
                    "mode": mode,
                    "overlay": sequence.get("overlay"),
                    "appearance": sequence.get("appearance"),
                    "frame": frame["file"],
                    "index": frame["index"],
                    "targetSeconds": frame["targetSeconds"],
                    "actualSeconds": frame["actualSeconds"],
                    "presentationProgress": progress,
                    "metrics": measured,
                    "protectedMetrics": flattened,
                }

        aggregates: JsonObject = {}
        for partition, records in partitions.items():
            names = sorted({name for record in records for name in record})
            aggregates[partition] = {
                "cases": len(records),
                "metrics": {
                    name: {
                        "mean": statistics.fmean(
                            record[name] for record in records if name in record
                        ),
                        "maximum": max(
                            record[name] for record in records if name in record
                        ),
                    }
                    for name in names
                },
            }
        return {
            "cases": cases,
            "partitions": aggregates,
        }


def compare_baseline(report: JsonObject, baseline_path: Path | None) -> JsonObject:
    if baseline_path is None:
        return {
            "evaluated": False,
            "passed": False,
            "reason": "no rendered baseline supplied",
        }
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    prior_cases = baseline.get("cases")
    current_cases = report.get("cases")
    if not isinstance(prior_cases, dict) or not isinstance(current_cases, dict):
        raise ValueError("baseline and candidate must contain case records")
    compatibility_paths = (
        "pixelGateSchemaVersion",
        "implementation.sha256",
        "implementation.rendererSha256",
        "implementation.vertexShaderSha256",
        "implementation.glVersion",
        "implementation.glVendor",
        "implementation.glRenderer",
        "implementation.moderngl",
        "implementation.numpy",
        "implementation.Pillow",
        "implementation.pyvips",
        "implementation.libvips",
        "implementation.python",
        "implementation.scipy",
        "implementation.scikitImage",
        "evidence.sha256",
        "selection",
    )

    def value_at(record: JsonObject, path: str) -> object:
        value: object = record
        for component in path.split("."):
            if not isinstance(value, dict) or component not in value:
                return None
            value = value[component]
        return value

    incompatibilities: JsonObject = {}
    for path in compatibility_paths:
        prior_value = value_at(baseline, path)
        current_value = value_at(report, path)
        if prior_value is None and current_value is None:
            continue
        if prior_value != current_value:
            incompatibilities[path] = {
                "baseline": prior_value,
                "candidate": current_value,
            }
    missing = sorted(set(prior_cases) ^ set(current_cases))
    regressions: JsonObject = {}
    for case in sorted(set(prior_cases) & set(current_cases)):
        prior = prior_cases[case].get("protectedMetrics")
        current = current_cases[case].get("protectedMetrics")
        if not isinstance(prior, dict) or not isinstance(current, dict):
            missing.append(f"{case}:protectedMetrics")
            continue
        expected = set(PROTECTED_ERROR_METRICS)
        if set(prior) != set(current) or set(current) != expected:
            missing.append(f"{case}:metric-set")
        for metric in sorted(set(prior) & set(current) & expected):
            candidate = float(current[metric])
            baseline_value = float(prior[metric])
            if candidate > baseline_value and not math.isclose(
                candidate,
                baseline_value,
                rel_tol=0,
                abs_tol=1e-12,
            ):
                regressions[f"{case}|{metric}"] = {
                    "baseline": baseline_value,
                    "candidate": candidate,
                    "increase": candidate - baseline_value,
                }
    return {
        "evaluated": True,
        "passed": not incompatibilities and not missing and not regressions,
        "incompatibilities": incompatibilities,
        "missing": missing,
        "regressions": regressions,
        "rule": (
            "the gate implementation, evidence, selection, and GPU stack must "
            "match; no protected rendered error metric may increase in any frame"
        ),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Render Walle's exact production shader and compare it with "
            "Apple Liquid Glass frames."
        )
    )
    parser.add_argument("evidence", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument(
        "--shader",
        type=Path,
        default=Path("shaders/frag.glsl"),
    )
    parser.add_argument(
        "--vertex-shader",
        type=Path,
        default=Path("shaders/vert.glsl"),
    )
    parser.add_argument(
        "--frame-step",
        type=int,
        default=1,
        help="diagnostic subsampling only; 1 protects every captured frame",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.frame_step < 1:
        raise ValueError("--frame-step must be positive")
    artifact = CaptureArtifact.open(args.evidence)
    gate = PixelGate(
        artifact=artifact,
        shader=args.shader,
        vertex_shader=args.vertex_shader,
        frame_step=args.frame_step,
    )
    try:
        rendered = gate.run()
        report = {
            "pixelGateSchemaVersion": 1,
            "implementation": {
                "file": str(Path(__file__).relative_to(Path.cwd())),
                "sha256": file_sha256(Path(__file__)),
                "rendererFile": "analysis/walle_shader_renderer.py",
                "rendererSha256": file_sha256(
                    Path("analysis/walle_shader_renderer.py")
                ),
                "scipy": scipy_version,
                "scikitImage": skimage_version,
                **gate.renderer.implementation,
            },
            "evidence": {
                "file": artifact.path.name,
                "sha256": artifact.sha256,
                "manifestSha256": artifact.manifest_sha256,
                "rigVersion": artifact.manifest.get("rigVersion"),
                "ciCommit": artifact.manifest.get("ciCommit"),
                "osBuild": artifact.manifest.get("osBuild"),
            },
            "selection": {
                "modes": [
                    "wallpaper-transition",
                    "wallpaper-transition-reverse",
                ],
                "frameStep": args.frame_step,
                "training": "forward source traversal",
                "holdout": "reverse source traversal",
                "clock": "captured presentationProgress",
            },
            **rendered,
        }
        report["nonRegression"] = compare_baseline(report, args.baseline)
    finally:
        gate.close()
        artifact.close()
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(args.report)
    non_regression = report["nonRegression"]
    return (
        1 if non_regression.get("evaluated") and not non_regression.get("passed") else 0
    )


if __name__ == "__main__":
    raise SystemExit(main())
