#!/usr/bin/env python3
"""Fit and independently validate Liquid Glass' interior spatial response."""

import argparse
import hashlib
import json
import math
import platform
from dataclasses import dataclass
from importlib.metadata import version as package_version
from io import BytesIO
from pathlib import Path
from typing import Any
from zipfile import ZipFile

import numpy as np
from numpy.typing import NDArray
from PIL import Image
from scipy.optimize import least_squares
from scipy.special import ndtr


type JsonObject = dict[str, Any]
type FloatArray = NDArray[np.float64]

APPEARANCES = ("dark", "light")
PERIODS = (32, 64, 128, 256, 512, 1024)
SOURCE_SPACES = ("srgb-code", "linear-srgb")
PIPELINES = ("tone-after-spatial-filter", "spatial-filter-after-tone")
SIGMA_BOUNDS = {
    1: ((0.2,), (1000.0,)),
    2: ((0.2, 8.0), (80.0, 1000.0)),
    3: ((0.2, 4.0, 40.0), (30.0, 160.0, 1200.0)),
    4: ((0.2, 2.0, 16.0, 100.0), (20.0, 80.0, 400.0, 1600.0)),
}


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def error_summary(actual: FloatArray, predicted: FloatArray) -> JsonObject:
    absolute = np.abs(actual - predicted).reshape(-1)
    return {
        "meanAbsoluteCodes": float(absolute.mean()),
        "p95AbsoluteCodes": float(np.percentile(absolute, 95)),
        "maximumAbsoluteCodes": float(absolute.max(initial=0.0)),
        "rootMeanSquareCodes": float(np.sqrt(np.mean(np.square(absolute)))),
    }


def srgb_to_linear(values: FloatArray) -> FloatArray:
    normalized = np.clip(values, 0.0, 1.0)
    return np.where(
        normalized <= 0.04045,
        normalized / 12.92,
        np.power((normalized + 0.055) / 1.055, 2.4),
    )


def linear_to_srgb(values: FloatArray) -> FloatArray:
    normalized = np.clip(values, 0.0, 1.0)
    return np.where(
        normalized <= 0.0031308,
        normalized * 12.92,
        1.055 * np.power(normalized, 1.0 / 2.4) - 0.055,
    )


def to_working_space(values: FloatArray, source_space: str) -> FloatArray:
    normalized = np.clip(values / 255.0, 0.0, 1.0)
    if source_space == "srgb-code":
        return normalized
    if source_space == "linear-srgb":
        return srgb_to_linear(normalized)
    raise ValueError(f"unknown source space: {source_space}")


def from_working_space(values: FloatArray, source_space: str) -> FloatArray:
    if source_space == "srgb-code":
        normalized = np.clip(values, 0.0, 1.0)
    elif source_space == "linear-srgb":
        normalized = linear_to_srgb(values)
    else:
        raise ValueError(f"unknown source space: {source_space}")
    return normalized * 255.0


@dataclass(frozen=True, slots=True)
class SpatialModel:
    pipeline: str
    source_space: str
    sigmas: FloatArray
    weights: FloatArray
    shift_pixels: float

    def step_response(self, offsets: FloatArray) -> FloatArray:
        response = np.zeros_like(offsets, dtype=np.float64)
        for sigma, weight in zip(self.sigmas, self.weights, strict=True):
            response += weight * ndtr((offsets + 0.5 - self.shift_pixels) / sigma)
        return response

    def line_response(self, offsets: FloatArray) -> FloatArray:
        response = np.zeros_like(offsets, dtype=np.float64)
        left = offsets - self.shift_pixels - 1.5
        right = offsets - self.shift_pixels + 1.5
        for sigma, weight in zip(self.sigmas, self.weights, strict=True):
            response += weight * (ndtr(right / sigma) - ndtr(left / sigma))
        return response

    def transfer_magnitude(self, frequencies: FloatArray) -> FloatArray:
        response = np.zeros_like(frequencies, dtype=np.float64)
        for sigma, weight in zip(self.sigmas, self.weights, strict=True):
            response += weight * np.exp(
                -2.0 * math.pi**2 * sigma**2 * np.square(frequencies)
            )
        return response

    def as_json(self) -> JsonObject:
        return {
            "pipeline": self.pipeline,
            "sourceSpace": self.source_space,
            "components": [
                {
                    "sigmaPixels": float(sigma),
                    "weight": float(weight),
                }
                for sigma, weight in zip(
                    self.sigmas,
                    self.weights,
                    strict=True,
                )
            ],
            "shiftPixels": self.shift_pixels,
        }


@dataclass(frozen=True, slots=True)
class EmpiricalKernel:
    source_space: str
    positions: FloatArray
    weights: FloatArray
    step_offsets: FloatArray
    step_values: FloatArray

    def transfer(self, frequencies: FloatArray) -> NDArray[np.complex128]:
        return np.sum(
            self.weights[:, np.newaxis]
            * np.exp(
                -2j
                * math.pi
                * self.positions[:, np.newaxis]
                * frequencies[np.newaxis, :]
            ),
            axis=0,
        )

    def line_response(self, offsets: FloatArray) -> FloatArray:
        right = np.interp(
            offsets + 1.0,
            self.step_offsets,
            self.step_values,
            left=0.0,
            right=1.0,
        )
        left = np.interp(
            offsets - 2.0,
            self.step_offsets,
            self.step_values,
            left=0.0,
            right=1.0,
        )
        return right - left


@dataclass(slots=True)
class CaptureSet:
    root: Path
    manifest: JsonObject
    records: dict[tuple[str, str, str, str], JsonObject]
    scenes: dict[str, JsonObject]
    prefix: str = ""
    archive: ZipFile | None = None

    @classmethod
    def open(cls, root: Path) -> "CaptureSet":
        root = root.resolve()
        archive = None
        prefix = ""
        if root.is_dir():
            manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        else:
            archive = ZipFile(root)
            manifests = [
                name for name in archive.namelist() if name.endswith("manifest.json")
            ]
            if len(manifests) != 1:
                archive.close()
                raise ValueError(
                    f"archive has {len(manifests)} manifests; expected one"
                )
            manifest_name = manifests[0]
            prefix = manifest_name.removesuffix("manifest.json")
            manifest = json.loads(archive.read(manifest_name))
        if not isinstance(manifest, dict):
            if archive is not None:
                archive.close()
            raise ValueError("manifest must be a JSON object")
        records = {
            (
                str(record["background"]),
                str(record["scene"]),
                str(record["overlay"]),
                str(record["appearance"]),
            ): record
            for record in manifest.get("captures", [])
        }
        scenes = {str(scene["name"]): scene for scene in manifest.get("scenes", [])}
        return cls(
            root=root,
            manifest=manifest,
            records=records,
            scenes=scenes,
            prefix=prefix,
            archive=archive,
        )

    def close(self) -> None:
        if self.archive is not None:
            self.archive.close()

    def image_file(self, relative: str) -> Image.Image:
        if self.archive is not None:
            return Image.open(BytesIO(self.archive.read(f"{self.prefix}{relative}")))
        return Image.open(self.root / relative)

    def image(
        self,
        background: str,
        scene: str,
        overlay: str,
        appearance: str,
    ) -> FloatArray:
        record = self.records[(background, scene, overlay, appearance)]
        with self.image_file(str(record["file"])) as image:
            return np.asarray(image.convert("RGB"), dtype=np.float64)

    def reference_image(self, background: str) -> FloatArray:
        records = {
            str(record["background"]): record
            for record in self.manifest.get("references", [])
        }
        record = records[background]
        with self.image_file(str(record["file"])) as image:
            return np.asarray(image.convert("RGB"), dtype=np.float64)

    def scene_center(self, scene: str) -> tuple[int, int]:
        shapes = self.scenes[scene].get("shapes")
        if not isinstance(shapes, list) or len(shapes) != 1:
            raise ValueError(f"{scene} must contain exactly one shape")
        shape = shapes[0]
        scale = float(self.manifest["backingScaleFactor"])
        return (
            round(float(shape["centerX"]) * scale),
            round(float(shape["centerY"]) * scale),
        )

    def axis_profile(
        self,
        background: str,
        appearance: str,
        *,
        axis: str,
        half_cross_axis: int = 128,
    ) -> tuple[FloatArray, FloatArray]:
        scene = "circle-4000-center"
        image = self.image(background, scene, "regular", appearance)
        center_x, center_y = self.scene_center(scene)
        if axis == "x":
            strip = image[
                center_y - half_cross_axis : center_y + half_cross_axis + 1,
                :,
                :,
            ]
            profile = np.mean(strip, axis=(0, 2))
            offsets = np.arange(image.shape[1], dtype=np.float64) - center_x
        elif axis == "y":
            strip = image[
                :,
                center_x - half_cross_axis : center_x + half_cross_axis + 1,
                :,
            ]
            profile = np.mean(strip, axis=(1, 2))
            offsets = np.arange(image.shape[0], dtype=np.float64) - center_y
        else:
            raise ValueError(f"unknown axis: {axis}")
        return offsets, profile


def tone_curve(
    measurements: JsonObject,
    appearance: str,
    *,
    axis: str,
) -> FloatArray:
    record = measurements["denseToneTransfer"][f"{appearance}/regular"]
    values = np.asarray(record["orientationOutputCodes"][axis], dtype=np.float64)
    if values.shape != (256,):
        raise ValueError("dense tone orientation must contain 256 codes")
    return values


def isotonic_increasing(values: FloatArray) -> FloatArray:
    block_values: list[float] = []
    block_weights: list[int] = []
    block_starts: list[int] = []
    for index, value in enumerate(values):
        block_values.append(float(value))
        block_weights.append(1)
        block_starts.append(index)
        while len(block_values) >= 2 and block_values[-2] > block_values[-1]:
            weight = block_weights[-2] + block_weights[-1]
            merged = (
                block_values[-2] * block_weights[-2]
                + block_values[-1] * block_weights[-1]
            ) / weight
            block_values[-2:] = [merged]
            block_weights[-2:] = [weight]
            block_starts.pop()
    fitted = np.empty_like(values, dtype=np.float64)
    for block, start in enumerate(block_starts):
        stop = block_starts[block + 1] if block + 1 < len(block_starts) else values.size
        fitted[start:stop] = block_values[block]
    return fitted


def inverse_tone(values: FloatArray, curve: FloatArray) -> FloatArray:
    monotonic = isotonic_increasing(curve)
    unique_values, first_indexes, counts = np.unique(
        monotonic,
        return_index=True,
        return_counts=True,
    )
    inverse_codes = first_indexes + (counts - 1) / 2.0
    return np.interp(
        values,
        unique_values,
        inverse_codes,
        left=0.0,
        right=255.0,
    )


def empirical_kernel(
    captures: CaptureSet,
    measurements: JsonObject,
    appearance: str,
    *,
    axis: str,
    half_width: int = 900,
) -> EmpiricalKernel:
    offsets, actual = captures.axis_profile(
        f"edge-{axis}",
        appearance,
        axis=axis,
    )
    mask = np.abs(offsets) <= half_width
    selected_offsets = offsets[mask]
    equivalent = inverse_tone(
        actual[mask],
        tone_curve(measurements, appearance, axis=axis),
    )
    tail = max(16, selected_offsets.size // 10)
    low = float(np.median(equivalent[:tail]))
    high = float(np.median(equivalent[-tail:]))
    if high <= low:
        raise ValueError("edge probe does not have increasing plateaus")
    step = isotonic_increasing(np.clip((equivalent - low) / (high - low), 0.0, 1.0))
    step = (step - step[0]) / (step[-1] - step[0])
    weights = np.diff(step)
    positions = (selected_offsets[:-1] + selected_offsets[1:]) / 2.0
    keep = weights > np.finfo(float).eps
    weights = weights[keep]
    weights /= weights.sum()
    return EmpiricalKernel(
        source_space="srgb-code",
        positions=positions[keep],
        weights=weights,
        step_offsets=selected_offsets,
        step_values=step,
    )


def decode_parameters(
    parameters: FloatArray,
    *,
    pipeline: str,
    source_space: str,
    components: int,
) -> SpatialModel:
    sigmas = np.exp(parameters[:components])
    if components == 1:
        weights = np.ones(1, dtype=np.float64)
    else:
        logits = np.append(
            parameters[components : 2 * components - 1],
            0.0,
        )
        logits -= logits.max()
        exponentials = np.exp(logits)
        weights = exponentials / exponentials.sum()
    return SpatialModel(
        pipeline=pipeline,
        source_space=source_space,
        sigmas=sigmas,
        weights=weights,
        shift_pixels=float(parameters[-1]),
    )


def predict_step(
    model: SpatialModel,
    offsets: FloatArray,
    curve: FloatArray,
) -> FloatArray:
    if model.pipeline == "spatial-filter-after-tone":
        response = model.step_response(offsets)
        return curve[0] + response * (curve[-1] - curve[0])
    if model.pipeline != "tone-after-spatial-filter":
        raise ValueError(f"unknown pipeline: {model.pipeline}")
    equivalent_input = from_working_space(
        model.step_response(offsets),
        model.source_space,
    )
    return np.interp(equivalent_input, np.arange(256), curve)


def predict_line(
    model: SpatialModel,
    offsets: FloatArray,
    curve: FloatArray,
) -> FloatArray:
    if model.pipeline == "spatial-filter-after-tone":
        response = model.line_response(offsets)
        return curve[0] + response * (curve[-1] - curve[0])
    if model.pipeline != "tone-after-spatial-filter":
        raise ValueError(f"unknown pipeline: {model.pipeline}")
    equivalent_input = from_working_space(
        model.line_response(offsets),
        model.source_space,
    )
    return np.interp(equivalent_input, np.arange(256), curve)


def fit_edge_candidate(
    captures: CaptureSet,
    measurements: JsonObject,
    *,
    appearances: tuple[str, ...],
    pipeline: str,
    source_space: str,
    components: int,
    half_width: int,
) -> tuple[SpatialModel, JsonObject]:
    lower_sigma, upper_sigma = SIGMA_BOUNDS[components]
    initial_sigmas = np.sqrt(np.asarray(lower_sigma) * np.asarray(upper_sigma))
    parameter_count = 2 * components
    initial = np.zeros(parameter_count, dtype=np.float64)
    initial[:components] = np.log(initial_sigmas)
    initial[-1] = 0.0
    lower = np.full(parameter_count, -10.0, dtype=np.float64)
    upper = np.full(parameter_count, 10.0, dtype=np.float64)
    lower[:components] = np.log(lower_sigma)
    upper[:components] = np.log(upper_sigma)
    lower[-1] = -20.0
    upper[-1] = 20.0

    training: dict[str, tuple[FloatArray, FloatArray, FloatArray]] = {}
    for appearance in appearances:
        offsets, actual = captures.axis_profile(
            "edge-x",
            appearance,
            axis="x",
        )
        mask = np.abs(offsets) <= half_width
        training[appearance] = (
            offsets[mask],
            actual[mask],
            tone_curve(measurements, appearance, axis="x"),
        )

    def residual(parameters: FloatArray) -> FloatArray:
        model = decode_parameters(
            parameters,
            pipeline=pipeline,
            source_space=source_space,
            components=components,
        )
        return np.concatenate(
            [
                predict_step(model, offsets, curve) - actual
                for offsets, actual, curve in training.values()
            ]
        )

    fit = least_squares(
        residual,
        initial,
        bounds=(lower, upper),
        loss="linear",
        max_nfev=20_000,
        x_scale="jac",
    )
    model = decode_parameters(
        fit.x,
        pipeline=pipeline,
        source_space=source_space,
        components=components,
    )
    errors = residual(fit.x)
    samples = errors.size
    rss = float(np.dot(errors, errors))
    free_parameters = 2 * components
    bic = samples * math.log(max(rss / samples, np.finfo(float).tiny))
    bic += free_parameters * math.log(samples)
    report = {
        "model": model.as_json(),
        "trainingAppearances": list(appearances),
        "converged": bool(fit.success),
        "optimizerStatus": int(fit.status),
        "optimizerMessage": fit.message,
        "functionEvaluations": int(fit.nfev),
        "freeParameters": free_parameters,
        "trainingSamples": samples,
        "trainingError": error_summary(np.zeros_like(errors), errors),
        "residualSumSquares": rss,
        "bayesianInformationCriterion": bic,
    }
    return model, report


def edge_validation(
    captures: CaptureSet,
    measurements: JsonObject,
    model: SpatialModel,
    *,
    axis: str,
    half_width: int,
) -> JsonObject:
    result: JsonObject = {}
    background = f"edge-{axis}"
    for appearance in APPEARANCES:
        offsets, actual = captures.axis_profile(
            background,
            appearance,
            axis=axis,
        )
        mask = np.abs(offsets) <= half_width
        predicted = predict_step(
            model,
            offsets[mask],
            tone_curve(measurements, appearance, axis=axis),
        )
        result[appearance] = error_summary(actual[mask], predicted)
    return result


def line_validation(
    captures: CaptureSet,
    measurements: JsonObject,
    model: SpatialModel,
    *,
    axis: str,
    half_width: int,
) -> JsonObject:
    result: JsonObject = {}
    background = f"line-{axis}"
    for appearance in APPEARANCES:
        offsets, actual = captures.axis_profile(
            background,
            appearance,
            axis=axis,
        )
        mask = np.abs(offsets) <= half_width
        predicted = predict_line(
            model,
            offsets[mask],
            tone_curve(measurements, appearance, axis=axis),
        )
        result[appearance] = error_summary(actual[mask], predicted)
    return result


def periodic_filter(
    values: FloatArray,
    model: SpatialModel,
) -> FloatArray:
    frequencies = np.fft.fftfreq(values.size)
    response = model.transfer_magnitude(frequencies)
    filtered = np.fft.ifft(np.fft.fft(values) * response)
    return np.asarray(filtered.real, dtype=np.float64)


def empirical_periodic_filter(
    values: FloatArray,
    kernel: EmpiricalKernel,
) -> FloatArray:
    frequencies = np.fft.fftfreq(values.size)
    response = kernel.transfer(frequencies)
    filtered = np.fft.ifft(np.fft.fft(values) * response)
    return np.asarray(filtered.real, dtype=np.float64)


def sine_codes(period: int, phase: float) -> FloatArray:
    positions = np.arange(period, dtype=np.float64)
    values = 0.5 + 0.5 * np.sin(2.0 * math.pi * (positions / period + phase))
    return np.floor(values * 255.0 + 0.5)


def predict_phase_amplitude(
    model: SpatialModel,
    curve: FloatArray,
    *,
    period: int,
) -> float:
    sources = [sine_codes(period, phase) for phase in (0.0, 0.25, 0.5, 0.75)]
    outputs: list[FloatArray] = []
    for source in sources:
        if model.pipeline == "spatial-filter-after-tone":
            material_output = np.interp(source, np.arange(256), curve)
            outputs.append(periodic_filter(material_output, model))
        elif model.pipeline == "tone-after-spatial-filter":
            working = to_working_space(source, model.source_space)
            filtered = periodic_filter(working, model)
            equivalent_input = from_working_space(filtered, model.source_space)
            outputs.append(np.interp(equivalent_input, np.arange(256), curve))
        else:
            raise ValueError(f"unknown pipeline: {model.pipeline}")
    source_complex = (sources[0] - sources[2]) + 1j * (sources[1] - sources[3])
    output_complex = (outputs[0] - outputs[2]) + 1j * (outputs[1] - outputs[3])
    transfer = np.vdot(source_complex, output_complex) / np.vdot(
        source_complex,
        source_complex,
    )
    return float(abs(transfer))


def phase_validation(
    measurements: JsonObject,
    models: dict[str, SpatialModel],
) -> JsonObject:
    giant = measurements["phaseResponse"]["scenes"]["circle-4000-center"]
    result: JsonObject = {}
    for appearance in APPEARANCES:
        model = models[appearance]
        axes: JsonObject = {}
        for axis in ("x", "y"):
            curve = tone_curve(measurements, appearance, axis=axis)
            periods: JsonObject = {}
            actual_values: list[float] = []
            predicted_values: list[float] = []
            for period in PERIODS:
                actual = float(
                    giant[f"{appearance}/regular"][axis][str(period)][
                        "centerAmplitudeRatio"
                    ]
                )
                predicted = predict_phase_amplitude(
                    model,
                    curve,
                    period=period,
                )
                actual_values.append(actual * 255.0)
                predicted_values.append(predicted * 255.0)
                periods[str(period)] = {
                    "actualAmplitudeRatio": actual,
                    "predictedAmplitudeRatio": predicted,
                    "absoluteErrorRatio": abs(actual - predicted),
                }
            axes[axis] = {
                "periods": periods,
                "amplitudeError": error_summary(
                    np.asarray(actual_values),
                    np.asarray(predicted_values),
                ),
            }
        result[appearance] = axes
    return result


def fit_phase_candidate(
    measurements: JsonObject,
    appearance: str,
    *,
    source_space: str,
    components: int,
) -> tuple[SpatialModel, JsonObject]:
    lower_sigma, upper_sigma = SIGMA_BOUNDS[components]
    initial_sigmas = np.sqrt(np.asarray(lower_sigma) * np.asarray(upper_sigma))
    parameter_count = 2 * components - 1
    initial = np.zeros(parameter_count, dtype=np.float64)
    initial[:components] = np.log(initial_sigmas)
    lower = np.full(parameter_count, -10.0, dtype=np.float64)
    upper = np.full(parameter_count, 10.0, dtype=np.float64)
    lower[:components] = np.log(lower_sigma)
    upper[:components] = np.log(upper_sigma)
    giant = measurements["phaseResponse"]["scenes"]["circle-4000-center"]
    actual = np.asarray(
        [
            giant[f"{appearance}/regular"]["x"][str(period)]["centerAmplitudeRatio"]
            for period in PERIODS
        ],
        dtype=np.float64,
    )
    curve = tone_curve(measurements, appearance, axis="x")

    def model_for(parameters: FloatArray) -> SpatialModel:
        with_shift = np.append(parameters, 0.0)
        return decode_parameters(
            with_shift,
            pipeline="tone-after-spatial-filter",
            source_space=source_space,
            components=components,
        )

    def residual(parameters: FloatArray) -> FloatArray:
        model = model_for(parameters)
        predicted = np.asarray(
            [predict_phase_amplitude(model, curve, period=period) for period in PERIODS]
        )
        return (predicted - actual) * 255.0

    fit = least_squares(
        residual,
        initial,
        bounds=(lower, upper),
        loss="linear",
        max_nfev=20_000,
        x_scale="jac",
    )
    model = model_for(fit.x)
    errors = residual(fit.x)
    samples = errors.size
    rss = float(np.dot(errors, errors))
    free_parameters = parameter_count
    bic = samples * math.log(max(rss / samples, np.finfo(float).tiny))
    bic += free_parameters * math.log(samples)
    return model, {
        "model": model.as_json(),
        "appearance": appearance,
        "converged": bool(fit.success),
        "functionEvaluations": int(fit.nfev),
        "freeParameters": free_parameters,
        "trainingSamples": samples,
        "trainingError": error_summary(np.zeros_like(errors), errors),
        "residualSumSquares": rss,
        "bayesianInformationCriterion": bic,
    }


def frequency_trained_validation(
    captures: CaptureSet,
    measurements: JsonObject,
) -> tuple[JsonObject, dict[str, SpatialModel]]:
    result: JsonObject = {}
    selected_models: dict[str, SpatialModel] = {}
    for appearance in APPEARANCES:
        candidates = [
            fit_phase_candidate(
                measurements,
                appearance,
                source_space=source_space,
                components=components,
            )
            for source_space in SOURCE_SPACES
            for components in (1, 2, 3)
        ]
        for model, report in candidates:
            selection_line = line_validation(
                captures,
                measurements,
                model,
                axis="x",
                half_width=700,
            )[appearance]
            report["modelSelectionLineError"] = selection_line
        model, selected = min(
            candidates,
            key=lambda candidate: (
                float(candidate[1]["modelSelectionLineError"]["rootMeanSquareCodes"]),
                float(candidate[1]["bayesianInformationCriterion"]),
                int(candidate[1]["freeParameters"]),
            ),
        )
        phase = phase_validation(
            measurements,
            {appearance_name: model for appearance_name in APPEARANCES},
        )[appearance]
        selected_models[appearance] = model
        result[appearance] = {
            "method": (
                "fit only the giant-circle x-axis six-frequency amplitudes; "
                "use line-x for model selection; reserve y-axis frequency, "
                "edge, and line probes"
            ),
            "candidates": [report for _, report in candidates],
            "selected": selected,
            "validation": {
                "orthogonalPhase": phase["y"],
                "trainingAxisEdge": edge_validation(
                    captures,
                    measurements,
                    model,
                    axis="x",
                    half_width=700,
                )[appearance],
                "orthogonalEdge": edge_validation(
                    captures,
                    measurements,
                    model,
                    axis="y",
                    half_width=700,
                )[appearance],
                "trainingAxisLine": line_validation(
                    captures,
                    measurements,
                    model,
                    axis="x",
                    half_width=700,
                )[appearance],
                "orthogonalLine": line_validation(
                    captures,
                    measurements,
                    model,
                    axis="y",
                    half_width=700,
                )[appearance],
            },
        }
    return result, selected_models


def filter_image_periodic(
    values: FloatArray,
    model: SpatialModel,
) -> FloatArray:
    height, width = values.shape
    vertical = np.fft.fftfreq(height)[:, np.newaxis]
    horizontal = np.fft.rfftfreq(width)[np.newaxis, :]
    squared_frequency = np.square(vertical) + np.square(horizontal)
    response = np.zeros_like(squared_frequency)
    for sigma, weight in zip(model.sigmas, model.weights, strict=True):
        response += weight * np.exp(-2.0 * math.pi**2 * sigma**2 * squared_frequency)
    transformed = np.fft.rfft2(values)
    return np.fft.irfft2(transformed * response, s=values.shape)


def noise_validation(
    captures: CaptureSet,
    measurements: JsonObject,
    models: dict[str, SpatialModel],
) -> JsonObject:
    source_rgb = captures.reference_image("noise-gray")
    source = np.mean(source_rgb, axis=2)
    del source_rgb
    result: JsonObject = {}
    for appearance in APPEARANCES:
        model = models[appearance]
        working = to_working_space(source, model.source_space)
        filtered = filter_image_periodic(working, model)
        equivalent_input = from_working_space(filtered, model.source_space)
        curve = np.asarray(
            measurements["denseToneTransfer"][f"{appearance}/regular"]["outputCodes"],
            dtype=np.float64,
        )
        predicted = np.interp(
            equivalent_input,
            np.arange(256),
            curve,
        )
        actual_rgb = captures.image(
            "noise-gray",
            "circle-4000-center",
            "regular",
            appearance,
        )
        actual = np.mean(actual_rgb, axis=2)
        del actual_rgb
        margin = max(32, math.ceil(4.0 * float(model.sigmas.max())))
        if margin * 2 >= min(actual.shape):
            raise ValueError("noise validation margin consumes the image")
        region = (
            slice(margin, actual.shape[0] - margin),
            slice(margin, actual.shape[1] - margin),
        )
        actual_region = actual[region]
        predicted_region = predicted[region]
        actual_centered = actual_region - actual_region.mean()
        predicted_centered = predicted_region - predicted_region.mean()
        denominator = math.sqrt(
            float(np.sum(np.square(actual_centered)))
            * float(np.sum(np.square(predicted_centered)))
        )
        result[appearance] = {
            "model": model.as_json(),
            "boundaryExclusionPixels": margin,
            "evaluatedPixels": int(actual_region.size),
            "predictionError": error_summary(
                actual_region,
                predicted_region,
            ),
            "actualCodes": {
                "mean": float(actual_region.mean()),
                "standardDeviation": float(actual_region.std()),
                "minimum": float(actual_region.min()),
                "maximum": float(actual_region.max()),
            },
            "predictedCodes": {
                "mean": float(predicted_region.mean()),
                "standardDeviation": float(predicted_region.std()),
                "minimum": float(predicted_region.min()),
                "maximum": float(predicted_region.max()),
            },
            "centeredCorrelation": (
                float(np.sum(actual_centered * predicted_centered)) / denominator
                if denominator
                else None
            ),
            "interpretation": (
                "full-spectrum grayscale holdout; the model was fitted only "
                "from sine amplitudes and selected by the three-pixel line"
            ),
        }
    return result


def empirical_kernel_validation(
    captures: CaptureSet,
    measurements: JsonObject,
) -> JsonObject:
    result: JsonObject = {}
    giant = measurements["phaseResponse"]["scenes"]["circle-4000-center"]
    for appearance in APPEARANCES:
        kernel = empirical_kernel(
            captures,
            measurements,
            appearance,
            axis="x",
        )
        line_errors: JsonObject = {}
        for axis in ("x", "y"):
            offsets, actual = captures.axis_profile(
                f"line-{axis}",
                appearance,
                axis=axis,
            )
            mask = np.abs(offsets) <= 900
            equivalent = kernel.line_response(offsets[mask])
            predicted = np.interp(
                equivalent * 255.0,
                np.arange(256),
                tone_curve(measurements, appearance, axis=axis),
            )
            line_errors[axis] = error_summary(actual[mask], predicted)

        phase_result: JsonObject = {}
        for axis in ("x", "y"):
            curve = tone_curve(measurements, appearance, axis=axis)
            actual_codes: list[float] = []
            predicted_codes: list[float] = []
            period_results: JsonObject = {}
            for period in PERIODS:
                sources = [
                    sine_codes(period, phase) for phase in (0.0, 0.25, 0.5, 0.75)
                ]
                outputs = []
                for source in sources:
                    filtered = empirical_periodic_filter(
                        source / 255.0,
                        kernel,
                    )
                    outputs.append(
                        np.interp(
                            np.clip(filtered, 0.0, 1.0) * 255.0,
                            np.arange(256),
                            curve,
                        )
                    )
                source_complex = (sources[0] - sources[2]) + 1j * (
                    sources[1] - sources[3]
                )
                output_complex = (outputs[0] - outputs[2]) + 1j * (
                    outputs[1] - outputs[3]
                )
                transfer = np.vdot(source_complex, output_complex) / np.vdot(
                    source_complex,
                    source_complex,
                )
                predicted = float(abs(transfer))
                actual = float(
                    giant[f"{appearance}/regular"][axis][str(period)][
                        "centerAmplitudeRatio"
                    ]
                )
                actual_codes.append(actual * 255.0)
                predicted_codes.append(predicted * 255.0)
                period_results[str(period)] = {
                    "actualAmplitudeRatio": actual,
                    "predictedAmplitudeRatio": predicted,
                    "absoluteErrorRatio": abs(actual - predicted),
                }
            phase_result[axis] = {
                "periods": period_results,
                "amplitudeError": error_summary(
                    np.asarray(actual_codes),
                    np.asarray(predicted_codes),
                ),
            }

        result[appearance] = {
            "derivation": (
                "invert the measured regular tone curve on edge-x, project the "
                "result to the nearest monotone step response, and differentiate"
            ),
            "nonzeroKernelSamples": int(kernel.weights.size),
            "centerOfMassPixels": float(np.dot(kernel.positions, kernel.weights)),
            "standardDeviationPixels": float(
                np.sqrt(
                    np.dot(
                        np.square(
                            kernel.positions - np.dot(kernel.positions, kernel.weights)
                        ),
                        kernel.weights,
                    )
                )
            ),
            "lineHoldouts": line_errors,
            "phaseHoldouts": phase_result,
        }
    return result


def chart_filter_inputs(
    chart: JsonObject,
    model: SpatialModel,
    appearance: str,
    *,
    width: int,
    height: int,
) -> FloatArray:
    layout = chart["layout"]
    columns = int(layout["columns"])
    rows = int(layout["rows"])
    sample_count = columns * rows
    captured_controls = chart.get("capturedControlInputCodes", {})
    spatial_codes = captured_controls.get(appearance, chart["inputCodes"])
    codes = np.asarray(spatial_codes, dtype=np.float64)
    if codes.shape != (sample_count, 3):
        raise ValueError("color chart controls do not match its layout")
    working = to_working_space(codes, model.source_space).reshape(
        rows,
        columns,
        3,
    )
    geometry = chart["sampleGeometry"]
    if len(geometry) != sample_count:
        raise ValueError("color chart geometry does not match its layout")

    x_boundaries = np.ceil(np.arange(columns + 1, dtype=np.float64) * width / columns)
    y_boundaries = np.ceil(np.arange(rows + 1, dtype=np.float64) * height / rows)
    x_left = x_boundaries[:-1] - 0.5
    x_right = x_boundaries[1:] - 0.5
    y_top = y_boundaries[:-1] - 0.5
    y_bottom = y_boundaries[1:] - 0.5
    # The backdrop layer is edge-clamped. Extending the outer tiles models
    # that boundary condition without allocating a full 3200x2000 raster.
    x_left[0] = -np.inf
    x_right[-1] = np.inf
    y_top[0] = -np.inf
    y_bottom[-1] = np.inf

    filtered = np.zeros_like(codes)
    for sample_index, sample in enumerate(geometry):
        x = float(sample["x"])
        y = float(sample["y"])
        value = np.zeros(3, dtype=np.float64)
        for sigma, weight in zip(model.sigmas, model.weights, strict=True):
            horizontal = ndtr((x_right - x) / sigma) - ndtr((x_left - x) / sigma)
            vertical = ndtr((y_bottom - y) / sigma) - ndtr((y_top - y) / sigma)
            component = np.einsum(
                "r,rcd,c->d",
                vertical,
                working,
                horizontal,
                optimize=True,
            )
            value += weight * component
        filtered[sample_index] = from_working_space(
            value,
            model.source_space,
        )
    return filtered


def polynomial_exponents(degree: int) -> list[tuple[int, int, int]]:
    return [
        (red, green, blue)
        for red in range(degree + 1)
        for green in range(degree + 1 - red)
        for blue in range(degree + 1 - red - green)
    ]


def polynomial_design(
    inputs: FloatArray,
    exponents: list[tuple[int, int, int]],
) -> FloatArray:
    normalized = inputs / 127.5 - 1.0
    return np.column_stack(
        [
            np.prod(
                np.power(
                    normalized,
                    np.asarray(exponent, dtype=np.int64),
                ),
                axis=1,
            )
            for exponent in exponents
        ]
    )


def fit_polynomial_transfer(
    inputs: FloatArray,
    outputs: FloatArray,
    *,
    degree: int,
) -> tuple[list[tuple[int, int, int]], FloatArray]:
    exponents = polynomial_exponents(degree)
    design = polynomial_design(inputs, exponents)
    coefficients = np.linalg.lstsq(design, outputs, rcond=None)[0]
    return exponents, coefficients


def predict_polynomial_transfer(
    inputs: FloatArray,
    exponents: list[tuple[int, int, int]],
    coefficients: FloatArray,
) -> FloatArray:
    return np.clip(
        polynomial_design(inputs, exponents) @ coefficients,
        0.0,
        255.0,
    )


def ordered_by_source(
    source_inputs: FloatArray,
    values: FloatArray,
) -> dict[tuple[float, float, float], FloatArray]:
    result = {
        tuple(source.tolist()): value
        for source, value in zip(source_inputs, values, strict=True)
    }
    if len(result) != source_inputs.shape[0]:
        raise ValueError("color chart contains duplicate source colors")
    return result


def chart_context_shift(
    left: JsonObject,
    left_filtered: FloatArray,
    right: JsonObject,
    right_filtered: FloatArray,
) -> JsonObject:
    left_by_source = ordered_by_source(
        np.asarray(left["inputCodes"], dtype=np.float64),
        left_filtered,
    )
    right_inputs = np.asarray(right["inputCodes"], dtype=np.float64)
    reordered = np.asarray(
        [left_by_source[tuple(source.tolist())] for source in right_inputs],
        dtype=np.float64,
    )
    return error_summary(right_filtered, reordered)


def per_scale_polynomial_design(
    scale_inputs: list[FloatArray],
    *,
    degree: int,
) -> FloatArray:
    exponents = [
        exponent for exponent in polynomial_exponents(degree) if sum(exponent) > 0
    ]
    columns = [np.ones(scale_inputs[0].shape[0], dtype=np.float64)]
    for inputs in scale_inputs:
        normalized = inputs / 127.5 - 1.0
        columns.extend(
            np.prod(
                np.power(
                    normalized,
                    np.asarray(exponent, dtype=np.int64),
                ),
                axis=1,
            )
            for exponent in exponents
        )
    return np.column_stack(columns)


def ridge_fit(
    design: FloatArray,
    outputs: FloatArray,
    *,
    penalty: float,
) -> FloatArray:
    gram = design.T @ design
    regularizer = np.eye(gram.shape[0], dtype=np.float64) * penalty
    regularizer[0, 0] = 0.0
    return np.linalg.solve(
        gram + regularizer,
        design.T @ outputs,
    )


def values_in_chart_order(
    reference_chart: JsonObject,
    candidate_chart: JsonObject,
    candidate_values: FloatArray,
) -> FloatArray:
    candidate_by_source = ordered_by_source(
        np.asarray(candidate_chart["inputCodes"], dtype=np.float64),
        candidate_values,
    )
    return np.asarray(
        [
            candidate_by_source[tuple(source)]
            for source in reference_chart["inputCodes"]
        ],
        dtype=np.float64,
    )


def context_pair_design(
    reference_chart: JsonObject,
    reference_design: FloatArray,
    candidate_chart: JsonObject,
    candidate_design: FloatArray,
) -> FloatArray:
    reordered = values_in_chart_order(
        reference_chart,
        candidate_chart,
        candidate_design,
    )
    # The per-scale design's first column is a constant and cancels between
    # contexts. Restore one explicit intercept for a possible layout bias.
    return np.column_stack(
        (
            np.ones(reference_design.shape[0], dtype=np.float64),
            reordered[:, 1:] - reference_design[:, 1:],
        )
    )


def paired_context_effect_validation(
    charts: dict[str, JsonObject],
    outputs: dict[str, FloatArray],
    designs: dict[tuple[str, int, str], FloatArray],
    *,
    penalties: tuple[float, ...],
) -> JsonObject:
    source = np.asarray(charts["fitting"]["inputCodes"], dtype=np.int64)
    fold_ids = (source[:, 0] * 17 + source[:, 1] * 31 + source[:, 2] * 43) % 5
    candidates: list[JsonObject] = []
    for source_space in SOURCE_SPACES:
        for degree in (1, 2, 3):
            reference_design = designs[(source_space, degree, "fitting")]
            pair_design = context_pair_design(
                charts["fitting"],
                reference_design,
                charts["modelSelectionContext"],
                designs[(source_space, degree, "modelSelectionContext")],
            )
            reordered_output = values_in_chart_order(
                charts["fitting"],
                charts["modelSelectionContext"],
                outputs["modelSelectionContext"],
            )
            difference = reordered_output - outputs["fitting"]
            for penalty in penalties:
                predictions = np.empty_like(difference)
                for fold in range(5):
                    validation = fold_ids == fold
                    training = ~validation
                    coefficients = ridge_fit(
                        pair_design[training],
                        difference[training],
                        penalty=penalty,
                    )
                    predictions[validation] = pair_design[validation] @ coefficients
                candidates.append(
                    {
                        "sourceSpace": source_space,
                        "degreePerScale": degree,
                        "terms": int(pair_design.shape[1]),
                        "ridgePenalty": penalty,
                        "fiveFoldColorError": error_summary(
                            difference,
                            predictions,
                        ),
                    }
                )
    selected = min(
        candidates,
        key=lambda candidate: (
            float(candidate["fiveFoldColorError"]["meanAbsoluteCodes"]),
            int(candidate["terms"]),
            float(candidate["ridgePenalty"]),
        ),
    )
    source_space = str(selected["sourceSpace"])
    degree = int(selected["degreePerScale"])
    penalty = float(selected["ridgePenalty"])
    reference_design = designs[(source_space, degree, "fitting")]
    training_design = context_pair_design(
        charts["fitting"],
        reference_design,
        charts["modelSelectionContext"],
        designs[(source_space, degree, "modelSelectionContext")],
    )
    training_output = (
        values_in_chart_order(
            charts["fitting"],
            charts["modelSelectionContext"],
            outputs["modelSelectionContext"],
        )
        - outputs["fitting"]
    )
    coefficients = ridge_fit(
        training_design,
        training_output,
        penalty=penalty,
    )

    independent_design = context_pair_design(
        charts["fitting"],
        reference_design,
        charts["independentContextHoldout"],
        designs[(source_space, degree, "independentContextHoldout")],
    )
    independent_actual = (
        values_in_chart_order(
            charts["fitting"],
            charts["independentContextHoldout"],
            outputs["independentContextHoldout"],
        )
        - outputs["fitting"]
    )
    independent_prediction = independent_design @ coefficients

    off_grid_reference = designs[(source_space, degree, "offGridHoldout")]
    off_grid_design = context_pair_design(
        charts["offGridHoldout"],
        off_grid_reference,
        charts["offGridContextHoldout"],
        designs[(source_space, degree, "offGridContextHoldout")],
    )
    off_grid_actual = (
        values_in_chart_order(
            charts["offGridHoldout"],
            charts["offGridContextHoldout"],
            outputs["offGridContextHoldout"],
        )
        - outputs["offGridHoldout"]
    )
    off_grid_prediction = off_grid_design @ coefficients
    return {
        "method": (
            "paired fixed effects remove the unknown pointwise color transform; "
            "five-fold source-color validation selects a multiscale context "
            "operator from ordered-versus-affine differences, then independent "
            "shuffled and off-grid context deltas remain untouched holdouts"
        ),
        "selected": selected,
        "candidateCount": len(candidates),
        "bestCandidates": sorted(
            candidates,
            key=lambda candidate: float(
                candidate["fiveFoldColorError"]["meanAbsoluteCodes"]
            ),
        )[:8],
        "independentShuffledDeltaError": error_summary(
            independent_actual,
            independent_prediction,
        ),
        "offGridShuffledDeltaError": error_summary(
            off_grid_actual,
            off_grid_prediction,
        ),
        "observedContextDelta": {
            "independentShuffled": error_summary(
                np.zeros_like(independent_actual),
                independent_actual,
            ),
            "offGridShuffled": error_summary(
                np.zeros_like(off_grid_actual),
                off_grid_actual,
            ),
        },
    }


def multiscale_color_validation(
    charts: dict[str, JsonObject],
    outputs: dict[str, FloatArray],
    *,
    appearance: str,
    width: int,
    height: int,
) -> JsonObject:
    sigmas = (0.25, 4.0, 8.0, 16.0, 32.0, 64.0, 128.0, 256.0, 512.0)
    penalties = (1e-6, 1e-4, 1e-2, 0.1, 1.0, 10.0, 100.0)
    designs: dict[tuple[str, int, str], FloatArray] = {}

    for source_space in SOURCE_SPACES:
        scale_inputs: dict[str, list[FloatArray]] = {name: [] for name in charts}
        for sigma in sigmas:
            scale_model = SpatialModel(
                pipeline="tone-after-spatial-filter",
                source_space=source_space,
                sigmas=np.asarray([sigma], dtype=np.float64),
                weights=np.ones(1, dtype=np.float64),
                shift_pixels=0.0,
            )
            for name, chart in charts.items():
                scale_inputs[name].append(
                    chart_filter_inputs(
                        chart,
                        scale_model,
                        appearance,
                        width=width,
                        height=height,
                    )
                )
        for degree in (1, 2, 3):
            for name, values in scale_inputs.items():
                designs[(source_space, degree, name)] = per_scale_polynomial_design(
                    values, degree=degree
                )

    candidates: list[JsonObject] = []
    for source_space in SOURCE_SPACES:
        for degree in (1, 2, 3):
            fitting_design = designs[(source_space, degree, "fitting")]
            selection_design = designs[(source_space, degree, "modelSelectionContext")]
            for penalty in penalties:
                coefficients = ridge_fit(
                    fitting_design,
                    outputs["fitting"],
                    penalty=penalty,
                )
                training_prediction = np.clip(
                    fitting_design @ coefficients,
                    0.0,
                    255.0,
                )
                selection_prediction = np.clip(
                    selection_design @ coefficients,
                    0.0,
                    255.0,
                )
                candidates.append(
                    {
                        "sourceSpace": source_space,
                        "degreePerScale": degree,
                        "scalesPixels": list(sigmas),
                        "terms": int(fitting_design.shape[1]),
                        "ridgePenalty": penalty,
                        "trainingError": error_summary(
                            outputs["fitting"],
                            training_prediction,
                        ),
                        "modelSelectionContextError": error_summary(
                            outputs["modelSelectionContext"],
                            selection_prediction,
                        ),
                    }
                )
    selected = min(
        candidates,
        key=lambda candidate: (
            float(candidate["modelSelectionContextError"]["meanAbsoluteCodes"]),
            int(candidate["terms"]),
            float(candidate["ridgePenalty"]),
        ),
    )
    source_space = str(selected["sourceSpace"])
    degree = int(selected["degreePerScale"])
    penalty = float(selected["ridgePenalty"])
    fitting_design = designs[(source_space, degree, "fitting")]
    selection_design = designs[(source_space, degree, "modelSelectionContext")]
    combined_design = np.vstack((fitting_design, selection_design))
    combined_outputs = np.vstack((outputs["fitting"], outputs["modelSelectionContext"]))
    coefficients = ridge_fit(
        combined_design,
        combined_outputs,
        penalty=penalty,
    )
    independent_errors = {}
    for name in (
        "independentContextHoldout",
        "offGridHoldout",
        "offGridContextHoldout",
    ):
        prediction = np.clip(
            designs[(source_space, degree, name)] @ coefficients,
            0.0,
            255.0,
        )
        independent_errors[name] = error_summary(outputs[name], prediction)
    return {
        "method": (
            "sum of per-scale RGB polynomials over a fixed isotropic Gaussian "
            "bank; fit only on the ordered chart, select on the affine context, "
            "then refit those two before independent shuffled/off-grid tests"
        ),
        "selected": selected,
        "candidateCount": len(candidates),
        "bestCandidates": sorted(
            candidates,
            key=lambda candidate: float(
                candidate["modelSelectionContextError"]["meanAbsoluteCodes"]
            ),
        )[:8],
        "finalFitSamples": int(combined_design.shape[0]),
        "independentErrors": independent_errors,
        "pairedContextEffect": paired_context_effect_validation(
            charts,
            outputs,
            designs,
            penalties=penalties,
        ),
    }


def color_context_validation(
    captures: CaptureSet,
    measurements: JsonObject,
    models: dict[str, SpatialModel],
) -> JsonObject:
    chart_names = {
        "fitting": "denseColorTransfer",
        "modelSelectionContext": "denseColorContextRepeat",
        "independentContextHoldout": "denseColorContextHoldout",
        "offGridHoldout": "denseColorHoldout",
        "offGridContextHoldout": "denseColorHoldoutContextRepeat",
    }
    charts = {name: measurements[key] for name, key in chart_names.items()}
    width = int(captures.manifest["windowPoints"][0])
    height = int(captures.manifest["windowPoints"][1])
    scale = float(captures.manifest["backingScaleFactor"])
    width = round(width * scale)
    height = round(height * scale)
    result: JsonObject = {}

    for appearance in APPEARANCES:
        model = models[appearance]
        if model.pipeline != "tone-after-spatial-filter":
            result[appearance] = {
                "available": False,
                "reason": (
                    "a filter-after-color model requires a coupled spatial/color "
                    "fit and cannot be evaluated as filtered RGB inputs"
                ),
            }
            continue
        filtered = {
            name: chart_filter_inputs(
                chart,
                model,
                appearance,
                width=width,
                height=height,
            )
            for name, chart in charts.items()
        }
        outputs = {
            name: np.asarray(
                chart[f"{appearance}/regular"]["outputCodes"],
                dtype=np.float64,
            )
            for name, chart in charts.items()
        }
        training_inputs = filtered["fitting"]
        training_outputs = outputs["fitting"]
        selection_inputs = filtered["modelSelectionContext"]
        selection_outputs = outputs["modelSelectionContext"]

        degree_candidates: JsonObject = {}
        fitted_candidates: dict[
            int,
            tuple[list[tuple[int, int, int]], FloatArray],
        ] = {}
        for degree in range(1, 8):
            exponents, coefficients = fit_polynomial_transfer(
                training_inputs,
                training_outputs,
                degree=degree,
            )
            fitted_candidates[degree] = (exponents, coefficients)
            training_prediction = predict_polynomial_transfer(
                training_inputs,
                exponents,
                coefficients,
            )
            selection_prediction = predict_polynomial_transfer(
                selection_inputs,
                exponents,
                coefficients,
            )
            degree_candidates[str(degree)] = {
                "terms": len(exponents),
                "trainingError": error_summary(
                    training_outputs,
                    training_prediction,
                ),
                "modelSelectionContextError": error_summary(
                    selection_outputs,
                    selection_prediction,
                ),
            }
        selected_degree = min(
            range(1, 8),
            key=lambda degree: (
                float(
                    degree_candidates[str(degree)]["modelSelectionContextError"][
                        "meanAbsoluteCodes"
                    ]
                ),
                degree,
            ),
        )
        combined_inputs = np.vstack((training_inputs, selection_inputs))
        combined_outputs = np.vstack((training_outputs, selection_outputs))
        exponents, coefficients = fit_polynomial_transfer(
            combined_inputs,
            combined_outputs,
            degree=selected_degree,
        )

        final_errors = {}
        for name in (
            "independentContextHoldout",
            "offGridHoldout",
            "offGridContextHoldout",
        ):
            prediction = predict_polynomial_transfer(
                filtered[name],
                exponents,
                coefficients,
            )
            final_errors[name] = error_summary(outputs[name], prediction)

        result[appearance] = {
            "available": True,
            "spatialModel": model.as_json(),
            "polynomialCandidates": degree_candidates,
            "selectedDegree": selected_degree,
            "selectedTerms": len(exponents),
            "finalFitSamples": int(combined_inputs.shape[0]),
            "contextShiftEquivalentInputCodes": {
                "modelSelectionContext": chart_context_shift(
                    charts["fitting"],
                    filtered["fitting"],
                    charts["modelSelectionContext"],
                    filtered["modelSelectionContext"],
                ),
                "independentContextHoldout": chart_context_shift(
                    charts["fitting"],
                    filtered["fitting"],
                    charts["independentContextHoldout"],
                    filtered["independentContextHoldout"],
                ),
                "offGridContextHoldout": chart_context_shift(
                    charts["offGridHoldout"],
                    filtered["offGridHoldout"],
                    charts["offGridContextHoldout"],
                    filtered["offGridContextHoldout"],
                ),
            },
            "independentErrors": final_errors,
            "multiscaleRegression": multiscale_color_validation(
                charts,
                outputs,
                appearance=appearance,
                width=width,
                height=height,
            ),
        }
    return result


def fit_report(
    captures: CaptureSet,
    measurements: JsonObject,
    *,
    half_width: int = 700,
) -> JsonObject:
    shared_candidates: list[tuple[SpatialModel, JsonObject]] = []
    for source_space in SOURCE_SPACES:
        for components in SIGMA_BOUNDS:
            shared_candidates.append(
                fit_edge_candidate(
                    captures,
                    measurements,
                    appearances=APPEARANCES,
                    pipeline="tone-after-spatial-filter",
                    source_space=source_space,
                    components=components,
                    half_width=half_width,
                )
            )

    independent_candidates: dict[
        tuple[str, str, str],
        list[tuple[SpatialModel, JsonObject]],
    ] = {}
    for pipeline in PIPELINES:
        spaces = (
            SOURCE_SPACES
            if pipeline == "tone-after-spatial-filter"
            else ("material-output",)
        )
        for source_space in spaces:
            for appearance in APPEARANCES:
                key = (pipeline, source_space, appearance)
                independent_candidates[key] = [
                    fit_edge_candidate(
                        captures,
                        measurements,
                        appearances=(appearance,),
                        pipeline=pipeline,
                        source_space=source_space,
                        components=components,
                        half_width=half_width,
                    )
                    for components in SIGMA_BOUNDS
                ]

    architectures: list[JsonObject] = []
    architecture_models: list[dict[str, SpatialModel]] = []
    for model, report in shared_candidates:
        architectures.append(
            {
                "kind": "shared",
                "models": {appearance: model.as_json() for appearance in APPEARANCES},
                "trainingSamples": report["trainingSamples"],
                "freeParameters": report["freeParameters"],
                "residualSumSquares": report["residualSumSquares"],
                "bayesianInformationCriterion": report["bayesianInformationCriterion"],
                "memberReports": [report],
            }
        )
        architecture_models.append({appearance: model for appearance in APPEARANCES})

    for pipeline in PIPELINES:
        spaces = (
            SOURCE_SPACES
            if pipeline == "tone-after-spatial-filter"
            else ("material-output",)
        )
        for source_space in spaces:
            selected_members: list[tuple[SpatialModel, JsonObject]] = []
            for appearance in APPEARANCES:
                compatible = independent_candidates[
                    (pipeline, source_space, appearance)
                ]
                selected_members.append(
                    min(
                        compatible,
                        key=lambda candidate: float(
                            candidate[1]["bayesianInformationCriterion"]
                        ),
                    )
                )
            samples = sum(
                int(report["trainingSamples"]) for _, report in selected_members
            )
            parameters = sum(
                int(report["freeParameters"]) for _, report in selected_members
            )
            rss = sum(
                float(report["residualSumSquares"]) for _, report in selected_members
            )
            bic = samples * math.log(max(rss / samples, np.finfo(float).tiny))
            bic += parameters * math.log(samples)
            architectures.append(
                {
                    "kind": "appearance-specific",
                    "pipeline": pipeline,
                    "sourceSpace": source_space,
                    "models": {
                        appearance: model.as_json()
                        for appearance, (model, _) in zip(
                            APPEARANCES,
                            selected_members,
                            strict=True,
                        )
                    },
                    "trainingSamples": samples,
                    "freeParameters": parameters,
                    "residualSumSquares": rss,
                    "bayesianInformationCriterion": bic,
                    "memberReports": [report for _, report in selected_members],
                }
            )
            architecture_models.append(
                {
                    appearance: model
                    for appearance, (model, _) in zip(
                        APPEARANCES,
                        selected_members,
                        strict=True,
                    )
                }
            )

    def validate_edges(
        models: dict[str, SpatialModel],
        axis: str,
    ) -> JsonObject:
        return {
            appearance: edge_validation(
                captures,
                measurements,
                models[appearance],
                axis=axis,
                half_width=half_width,
            )[appearance]
            for appearance in APPEARANCES
        }

    def validate_lines(
        models: dict[str, SpatialModel],
        axis: str,
    ) -> JsonObject:
        return {
            appearance: line_validation(
                captures,
                measurements,
                models[appearance],
                axis=axis,
                half_width=half_width,
            )[appearance]
            for appearance in APPEARANCES
        }

    def aggregate_rmse(records: JsonObject) -> float:
        squares = [
            float(record["rootMeanSquareCodes"]) ** 2 for record in records.values()
        ]
        return math.sqrt(sum(squares) / len(squares))

    for architecture, models in zip(
        architectures,
        architecture_models,
        strict=True,
    ):
        training_line = validate_lines(models, "x")
        architecture["modelSelectionLineError"] = training_line
        architecture["modelSelectionLineRootMeanSquareCodes"] = aggregate_rmse(
            training_line
        )

    selected_index = min(
        range(len(architectures)),
        key=lambda candidate: (
            float(architectures[candidate]["modelSelectionLineRootMeanSquareCodes"]),
            float(architectures[candidate]["bayesianInformationCriterion"]),
        ),
    )
    selected_models = architecture_models[selected_index]
    selected_report = architectures[selected_index]
    frequency_report, frequency_models = frequency_trained_validation(
        captures,
        measurements,
    )

    return {
        "spatialFitSchemaVersion": 3,
        "analysisImplementation": {
            "file": "analysis/liquid_glass_spatial_fit.py",
            "sha256": file_sha256(Path(__file__).resolve()),
            "python": platform.python_version(),
            "numpy": package_version("numpy"),
            "scipy": package_version("scipy"),
            "Pillow": package_version("Pillow"),
        },
        "source": {
            "captureArtifact": {
                "rigVersion": captures.manifest.get("rigVersion"),
                "ciCommit": captures.manifest.get("ciCommit"),
                "requestedSuite": captures.manifest.get("requestedSuite"),
                "references": len(captures.manifest.get("references", [])),
                "staticCaptures": len(captures.manifest.get("captures", [])),
            },
            "measurementReport": {
                "analysisSchemaVersion": measurements.get("analysisSchemaVersion"),
                "analysisImplementation": measurements.get("analysisImplementation"),
                "artifact": measurements.get("artifact"),
            },
        },
        "method": {
            "training": (
                "isotropic Gaussian-mixture fits to the giant-circle edge-x captures"
            ),
            "selection": (
                "BIC chooses component counts from edge-x; the independent "
                "line-x response chooses shared versus appearance-specific "
                "models, filtering order, and working color space"
            ),
            "holdouts": (
                "edge-y, line-y, and all giant-circle six-frequency phase captures"
            ),
            "productionShaderModified": False,
        },
        "architectures": architectures,
        "individualCandidates": [
            report
            for members in independent_candidates.values()
            for _, report in members
        ],
        "selected": selected_report,
        "validation": {
            "trainingAxisEdge": validate_edges(selected_models, "x"),
            "modelSelectionLine": validate_lines(selected_models, "x"),
            "orthogonalEdge": validate_edges(selected_models, "y"),
            "orthogonalLine": validate_lines(selected_models, "y"),
            "phaseResponse": phase_validation(measurements, selected_models),
            "empiricalEdgeKernel": empirical_kernel_validation(
                captures,
                measurements,
            ),
            "frequencyTrainedKernel": frequency_report,
            "noiseHoldout": noise_validation(
                captures,
                measurements,
                frequency_models,
            ),
            "colorContexts": color_context_validation(
                captures,
                measurements,
                frequency_models,
            ),
        },
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fit and cross-validate the Liquid Glass interior kernel.",
    )
    parser.add_argument("captures", type=Path)
    parser.add_argument("measurements", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    measurements = json.loads(args.measurements.read_text(encoding="utf-8"))
    if not isinstance(measurements, dict):
        raise ValueError("measurement report must be a JSON object")
    captures = CaptureSet.open(args.captures)
    try:
        report = fit_report(captures, measurements)
    finally:
        captures.close()
    report["source"]["captureArtifact"].update(
        {
            "file": args.captures.name,
            "sha256": (file_sha256(args.captures) if args.captures.is_file() else None),
        }
    )
    report["source"]["measurementReport"].update(
        {
            "file": args.measurements.name,
            "sha256": file_sha256(args.measurements),
        }
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
