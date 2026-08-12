#!/usr/bin/env python3
"""Measure the current Walle optical model against captured Apple evidence.

This is an analytical gate, not a substitute for rendering the GLSL and
comparing pixels. It catches model-level regressions before a GPU candidate is
allowed into the stricter image comparator.
"""

import argparse
import hashlib
import json
import math
import re
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from zipfile import ZipFile


type JsonObject = dict[str, Any]

GLSL_FLOAT = re.compile(
    r"^\s*const\s+float\s+([A-Z][A-Z0-9_]*)\s*=\s*"
    r"([-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)\s*;"
)
C_NUMBER = re.compile(
    r"^\s*constexpr\s+(?:double|float|int)\s+([A-Z][A-Z0-9_]*)\s*=\s*"
    r"([-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)\s*;"
)


@dataclass(frozen=True, slots=True, kw_only=True)
class Evidence:
    path: Path
    manifest: JsonObject
    measurements: JsonObject
    sha256: str | None


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def load_evidence(path: Path) -> Evidence:
    if path.is_file():
        with ZipFile(path) as archive:
            manifest = json.loads(archive.read("manifest.json"))
            measurements = json.loads(archive.read("measurements.json"))
        digest: str | None = file_sha256(path)
    else:
        manifest = json.loads(
            (path / "manifest.json").read_text(encoding="utf-8")
        )
        measurements = json.loads(
            (path / "measurements.json").read_text(encoding="utf-8")
        )
        digest = None
    if not isinstance(manifest, dict) or not isinstance(measurements, dict):
        raise ValueError("capture manifest and measurements must be JSON objects")
    return Evidence(
        path=path,
        manifest=manifest,
        measurements=measurements,
        sha256=digest,
    )


def parse_constants(path: Path, pattern: re.Pattern[str]) -> dict[str, float]:
    constants: dict[str, float] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if match := pattern.match(line):
            constants[match[1]] = float(match[2])
    return constants


def require(constants: dict[str, float], *names: str) -> None:
    missing = [name for name in names if name not in constants]
    if missing:
        raise ValueError(f"missing model constants: {missing}")


def smoothstep(edge0: float, edge1: float, value: float) -> float:
    unit = min(1.0, max(0.0, (value - edge0) / (edge1 - edge0)))
    return unit * unit * (3.0 - 2.0 * unit)


def srgb_to_linear(code: float) -> float:
    encoded = code / 255.0
    if encoded <= 0.04045:
        return encoded / 12.92
    return ((encoded + 0.055) / 1.055) ** 2.4


def linear_to_srgb_code(linear: float) -> float:
    value = min(1.0, max(0.0, linear))
    if value <= 0.0031308:
        encoded = 12.92 * value
    else:
        encoded = 1.055 * value ** (1.0 / 2.4) - 0.055
    return 255.0 * encoded


def current_body_code(
    input_code: float,
    *,
    regular: bool,
    constants: dict[str, float],
) -> float:
    value = srgb_to_linear(input_code)
    if regular:
        lightness = smoothstep(0.02, 0.30, value)
        adapt = (
            constants["ADAPT_DARK"]
            + (constants["ADAPT_LIGHT"] - constants["ADAPT_DARK"])
            * lightness
        )
        platter = constants["PLATTER_DARK_Y"] + (
            constants["PLATTER_LIGHT_Y"] - constants["PLATTER_DARK_Y"]
        ) * lightness
        output = value + (platter - value) * adapt
    else:
        output = (
            value * constants["CLEAR_GAIN"] + constants["CLEAR_LIFT"]
        )
        dim = constants["DIM_MAX"] * smoothstep(0.10, 0.42, value)
        output *= 1.0 - dim
    return linear_to_srgb_code(output)


def error_summary(predicted: list[float], observed: list[float]) -> JsonObject:
    errors = [prediction - target for prediction, target in zip(predicted, observed)]
    absolute = [abs(error) for error in errors]
    return {
        "meanAbsoluteCodes": statistics.fmean(absolute),
        "rmseCodes": math.sqrt(statistics.fmean(error * error for error in errors)),
        "maximumAbsoluteCodes": max(absolute, default=0.0),
        "signedErrorsCodes": errors,
    }


def tone_metrics(
    measurements: JsonObject,
    constants: dict[str, float],
) -> tuple[JsonObject, dict[str, float]]:
    result: JsonObject = {}
    flattened: dict[str, float] = {}
    tone = measurements["toneTransfer"]
    for key in ("light/clear", "dark/clear", "light/regular", "dark/regular"):
        record = tone[key]
        inputs = [float(value) for value in record["inputCodes"]]
        observed = [float(value) for value in record["outputCodes"]]
        regular = key.endswith("/regular")
        predicted = [
            current_body_code(value, regular=regular, constants=constants)
            for value in inputs
        ]
        errors = error_summary(predicted, observed)
        result[key] = {
            "inputCodes": inputs,
            "appleOutputCodes": observed,
            "walleOutputCodes": predicted,
            "error": errors,
            "scope": "deep body, excluding transient origin glow and dither",
        }
        for metric in ("meanAbsoluteCodes", "rmseCodes", "maximumAbsoluteCodes"):
            flattened[f"tone.{key}.{metric}"] = float(errors[metric])
    return result, flattened


def blur_metrics(
    evidence: Evidence,
    constants: dict[str, float],
) -> tuple[JsonObject, dict[str, float]]:
    width, height = (float(value) for value in evidence.manifest["windowPoints"])
    diagonal = math.hypot(width, height)
    predicted_by_variant = {
        "clear": diagonal * constants["GLASS_SIGMA_FRAC_CLEAR"],
        "regular": diagonal * constants["GLASS_SIGMA_FRAC_REGULAR"],
    }
    records = evidence.measurements["checkerEdgeSpread"]
    result: JsonObject = {
        "currentPreprocessSigmaPixels": predicted_by_variant,
        "downsampleFactor": int(constants["GLASS_DOWN_FACTOR"]),
        "cases": {},
    }
    flattened: dict[str, float] = {}
    for scene, cases in records.items():
        result["cases"][scene] = {}
        for key, record in cases.items():
            variant = key.split("/", 1)[1]
            observed = float(record["sigmaPixels"])
            predicted = predicted_by_variant[variant]
            absolute = abs(predicted - observed)
            result["cases"][scene][key] = {
                "appleSigmaPixels": observed,
                "wallePreprocessSigmaPixels": predicted,
                "absoluteErrorPixels": absolute,
                "ratio": predicted / observed,
            }
            flattened[f"blur.{scene}.{key}.absoluteErrorPixels"] = absolute
    return result, flattened


def current_refraction(
    depth: float,
    *,
    regular: bool,
    radius: float,
    diagonal: float,
    constants: dict[str, float],
) -> float:
    width = min(
        constants["LENS_WIDTH_MAXDIAG"] * diagonal,
        max(constants["LENS_WIDTH_MIN"], radius * constants["LENS_WIDTH_FRAC"]),
    )
    if depth >= width:
        return 0.0
    edge_weight = 1.0 - depth / width
    strength = (
        constants["LENS_BEND_REGULAR"]
        if regular
        else constants["LENS_BEND_CLEAR"]
    )
    return edge_weight * edge_weight * width * strength


def refraction_metrics(
    evidence: Evidence,
    constants: dict[str, float],
) -> tuple[JsonObject, dict[str, float]]:
    width, height = (float(value) for value in evidence.manifest["windowPoints"])
    diagonal = math.hypot(width, height)
    radius = 250.0
    result: JsonObject = {}
    flattened: dict[str, float] = {}
    for key, records in evidence.measurements["phaseRefraction"].items():
        regular = key.endswith("/regular")
        samples = []
        absolute_errors = []
        for depth_text, record in sorted(
            records.items(), key=lambda item: int(item[0])
        ):
            depth = float(depth_text)
            observed = float(record["apparentOutwardDisplacementPixels"])
            predicted = current_refraction(
                depth,
                regular=regular,
                radius=radius,
                diagonal=diagonal,
                constants=constants,
            )
            absolute = abs(predicted - observed)
            absolute_errors.append(absolute)
            samples.append(
                {
                    "depthInsidePixels": depth,
                    "appleDisplacementPixels": observed,
                    "walleDisplacementPixels": predicted,
                    "absoluteErrorPixels": absolute,
                }
            )
        summary = {
            "samples": samples,
            "meanAbsolutePixels": statistics.fmean(absolute_errors),
            "maximumAbsolutePixels": max(absolute_errors),
        }
        result[key] = summary
        flattened[f"refraction.{key}.meanAbsolutePixels"] = float(
            summary["meanAbsolutePixels"]
        )
        flattened[f"refraction.{key}.maximumAbsolutePixels"] = float(
            summary["maximumAbsolutePixels"]
        )
    return result, flattened


def shadow_scale_metrics(constants: dict[str, float]) -> JsonObject:
    result: JsonObject = {}
    for diameter in (128, 256, 500, 1000, 1600):
        radius = diameter / 2
        offset = constants["SHADOW_OFF_FRAC"] * radius
        penumbra = max(
            constants["SHADOW_PEN_FRAC"] * radius,
            constants["SHADOW_PEN_MIN"],
        )
        result[str(diameter)] = {
            "offsetPixels": offset,
            "penumbraPixels": penumbra,
            "nominalOuterReachPixels": offset + penumbra,
        }
    return result


def compare_baseline(
    metrics: dict[str, float],
    baseline_path: Path | None,
) -> JsonObject:
    if baseline_path is None:
        return {
            "evaluated": False,
            "passed": False,
            "reason": "no analytical baseline supplied",
        }
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    previous = baseline.get("protectedAnalyticalMetrics")
    if not isinstance(previous, dict):
        raise ValueError("baseline lacks protectedAnalyticalMetrics")
    missing = sorted(set(previous) ^ set(metrics))
    regressions = {
        key: {
            "baseline": float(previous[key]),
            "candidate": value,
            "increase": value - float(previous[key]),
        }
        for key, value in metrics.items()
        if key in previous
        and not math.isclose(value, float(previous[key]), rel_tol=0, abs_tol=1e-12)
        and value > float(previous[key])
    }
    return {
        "evaluated": True,
        "passed": not missing and not regressions,
        "missingMetrics": missing,
        "regressions": regressions,
        "rule": "no protected analytical error may increase",
    }


def build_report(
    evidence: Evidence,
    shader_path: Path,
    renderer_path: Path,
    baseline_path: Path | None,
) -> JsonObject:
    shader_constants = parse_constants(shader_path, GLSL_FLOAT)
    renderer_constants = parse_constants(renderer_path, C_NUMBER)
    require(
        shader_constants,
        "ADAPT_DARK",
        "ADAPT_LIGHT",
        "PLATTER_DARK_Y",
        "PLATTER_LIGHT_Y",
        "CLEAR_GAIN",
        "CLEAR_LIFT",
        "DIM_MAX",
        "LENS_WIDTH_FRAC",
        "LENS_WIDTH_MIN",
        "LENS_WIDTH_MAXDIAG",
        "LENS_BEND_CLEAR",
        "LENS_BEND_REGULAR",
        "SHADOW_OFF_FRAC",
        "SHADOW_PEN_FRAC",
        "SHADOW_PEN_MIN",
    )
    require(
        renderer_constants,
        "GLASS_SIGMA_FRAC_CLEAR",
        "GLASS_SIGMA_FRAC_REGULAR",
        "GLASS_DOWN_FACTOR",
    )
    constants = shader_constants | renderer_constants
    tone, tone_flat = tone_metrics(evidence.measurements, constants)
    blur, blur_flat = blur_metrics(evidence, constants)
    refraction, refraction_flat = refraction_metrics(evidence, constants)
    protected = tone_flat | blur_flat | refraction_flat
    return {
        "analysisSchemaVersion": 1,
        "evidence": {
            "file": evidence.path.name,
            "sha256": evidence.sha256,
            "rigVersion": evidence.manifest.get("rigVersion"),
            "ciCommit": evidence.manifest.get("ciCommit"),
            "osBuild": evidence.manifest.get("osBuild"),
        },
        "implementation": {
            "file": str(Path(__file__).relative_to(Path.cwd())),
            "sha256": file_sha256(Path(__file__)),
            "shader": str(shader_path),
            "shaderSha256": file_sha256(shader_path),
            "renderer": str(renderer_path),
            "rendererSha256": file_sha256(renderer_path),
        },
        "currentModel": {
            "toneTransfer": tone,
            "blur": blur,
            "refraction": refraction,
            "shadowScale": shadow_scale_metrics(constants),
        },
        "protectedAnalyticalMetrics": protected,
        "analyticalNonRegression": compare_baseline(protected, baseline_path),
        "pixelGate": {
            "passed": False,
            "reason": (
                "analytical probes do not replace a rendered per-pixel, "
                "edge-weighted, and perceptual Apple-reference comparison"
            ),
        },
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Compare Walle's current optical constants with measured Apple "
            "Liquid Glass evidence."
        )
    )
    parser.add_argument("evidence", type=Path, help="capture directory or ZIP")
    parser.add_argument(
        "--shader", type=Path, default=Path("shaders/frag.glsl")
    )
    parser.add_argument("--renderer", type=Path, default=Path("walle.c"))
    parser.add_argument(
        "--baseline",
        type=Path,
        help="earlier comparator JSON; no analytical error may increase",
    )
    parser.add_argument("--report", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    evidence = load_evidence(args.evidence)
    report = build_report(
        evidence,
        args.shader,
        args.renderer,
        args.baseline,
    )
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(args.report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
