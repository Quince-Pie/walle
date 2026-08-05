#!/usr/bin/env python3
"""Measure Apple's private Liquid Glass materialize state law.

The focused ``lg-test`` transition artifact pairs real WindowServer pixels
with presentation-layer state immediately before and after each acquisition.
This module verifies every lossless frame, extracts the shared material scalar,
and tests explicit geometry and filter equations.  Empirical polynomial fits
are diagnostics only; they are never promoted to parity claims.
"""

import argparse
import hashlib
import json
import math
import platform
import re
import statistics
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image


type JsonObject = dict[str, Any]

REPORT_NAME = "transition-timeline.json"
STATE_KEYS = (
    "presentationStateBeforeCapture",
    "presentationStateAfterCapture",
)
EXPECTED_PROFILES = {
    ("clear", "dark"),
    ("clear", "light"),
    ("regular", "dark"),
    ("regular", "light"),
}
EXPECTED_SCHEMA4_SCENARIOS = {
    (direction, material, appearance, "circle-800-center")
    for direction in ("dematerialize", "materialize")
    for material, appearance in EXPECTED_PROFILES
} | {
    (direction, "clear", "light", geometry)
    for direction in ("dematerialize", "materialize")
    for geometry in (
        "circle-256-center",
        "circle-512-offset",
        "circle-640-fractional",
        "circle-1536-center",
    )
}
_NUMBER = re.compile(
    r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[-+]?\d+)?",
    re.IGNORECASE,
)


@dataclass(slots=True)
class Residual:
    count: int = 0
    absolute_sum: float = 0
    squared_sum: float = 0
    maximum_absolute: float = 0

    def observe(self, measured: float, expected: float) -> None:
        error = float(measured) - float(expected)
        absolute = abs(error)
        self.count += 1
        self.absolute_sum += absolute
        self.squared_sum += error * error
        self.maximum_absolute = max(self.maximum_absolute, absolute)

    def as_json(self) -> JsonObject:
        if self.count == 0:
            return {
                "count": 0,
                "maximumAbsoluteResidual": None,
                "meanAbsoluteResidual": None,
                "rootMeanSquareResidual": None,
            }
        return {
            "count": self.count,
            "maximumAbsoluteResidual": self.maximum_absolute,
            "meanAbsoluteResidual": self.absolute_sum / self.count,
            "rootMeanSquareResidual": math.sqrt(
                self.squared_sum / self.count
            ),
        }


@dataclass(slots=True)
class ProfileMeasurements:
    geometry: dict[str, Residual] = field(default_factory=dict)
    background: dict[str, Residual] = field(default_factory=dict)
    foreground: dict[str, Residual] = field(default_factory=dict)
    colors: dict[str, Residual] = field(default_factory=dict)
    scheduled_progress: Residual = field(default_factory=Residual)
    state_bracket_remaining: Residual = field(default_factory=Residual)
    clamp_samples: list[tuple[float, float]] = field(
        default_factory=list
    )
    boolean_ranges: dict[str, dict[bool, list[float]]] = field(
        default_factory=dict
    )
    numeric_inputs: set[str] = field(default_factory=set)
    modeled_inputs: set[str] = field(default_factory=set)
    background_input_keys: set[str] = field(default_factory=set)
    modeled_background_input_keys: set[str] = field(
        default_factory=set
    )
    foreground_input_keys: set[str] = field(default_factory=set)
    modeled_foreground_input_keys: set[str] = field(
        default_factory=set
    )
    structured_inputs: dict[str, set[str]] = field(default_factory=dict)
    vibrant_matrix_hex: set[str] = field(default_factory=set)
    color_presence: dict[str, dict[str, int]] = field(
        default_factory=dict
    )
    exact_discrete_rules: dict[str, int] = field(default_factory=dict)

    @staticmethod
    def _accumulator(
        mapping: dict[str, Residual],
        name: str,
    ) -> Residual:
        return mapping.setdefault(name, Residual())

    def observe_geometry(
        self,
        name: str,
        measured: float,
        expected: float,
    ) -> None:
        self._accumulator(
            self.geometry,
            name,
        ).observe(measured, expected)

    def observe_background(
        self,
        name: str,
        measured: float,
        expected: float,
    ) -> None:
        self._accumulator(
            self.background,
            name,
        ).observe(measured, expected)

    def observe_foreground(
        self,
        name: str,
        measured: float,
        expected: float,
    ) -> None:
        self._accumulator(
            self.foreground,
            name,
        ).observe(measured, expected)

    def observe_color(
        self,
        name: str,
        measured: float,
        expected: float,
    ) -> None:
        self._accumulator(
            self.colors,
            name,
        ).observe(measured, expected)

    def observe_color_presence(
        self,
        name: str,
        present: bool,
    ) -> None:
        counts = self.color_presence.setdefault(
            name,
            {"present": 0, "absent": 0},
        )
        counts["present" if present else "absent"] += 1

    def observe_exact_discrete_rule(self, name: str) -> None:
        self.exact_discrete_rules[name] = (
            self.exact_discrete_rules.get(name, 0) + 1
        )


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def parse_rect(value: str) -> tuple[float, float, float, float]:
    components = tuple(float(number) for number in _NUMBER.findall(value))
    if len(components) != 4:
        raise ValueError(f"invalid NSString rect: {value!r}")
    return components


def _record_by_path(
    state: JsonObject,
) -> dict[tuple[int, ...], JsonObject]:
    records = state.get("records")
    if not isinstance(records, list):
        raise ValueError("presentation state has no records")
    return {
        tuple(int(index) for index in record["path"]): record
        for record in records
        if isinstance(record, dict)
        and isinstance(record.get("path"), list)
    }


def named_filter(
    state: JsonObject,
    name: str,
) -> JsonObject | None:
    records = state.get("records", [])
    if not isinstance(records, list):
        return None
    for record in records:
        if not isinstance(record, dict):
            continue
        for key in ("filters", "backgroundFilters"):
            values = record.get(key, [])
            if not isinstance(values, list):
                continue
            for value in values:
                if (
                    isinstance(value, dict)
                    and value.get("knownValues", {}).get("name")
                    == name
                ):
                    return value
        value = record.get("compositingFilter")
        if (
            isinstance(value, dict)
            and value.get("knownValues", {}).get("name") == name
        ):
            return value
    return None


def expected_geometry(
    *,
    diameter: float,
    center_x: float,
    center_y: float,
    remaining: float,
    window_center_x: float | None = None,
    window_center_y: float | None = None,
) -> dict[str, float]:
    progress = 1 - remaining
    effect_extent = diameter + 16 * progress
    snapped_inset = round(0.5 * diameter * progress)
    if window_center_x is None:
        window_center_x = center_x
    if window_center_y is None:
        window_center_y = center_y
    effect_origin_x = (
        round(center_x)
        - window_center_x
        - snapped_inset
        - 8 * progress
    )
    effect_origin_y = (
        round(center_y)
        - window_center_y
        - snapped_inset
        - 8 * progress
    )
    return {
        "outerOriginX":
            window_center_x - 0.5 * diameter * remaining,
        "outerOriginY":
            window_center_y - 0.5 * diameter * remaining,
        "outerWidth": diameter * remaining,
        "outerHeight": diameter * remaining,
        "effectOriginX": effect_origin_x,
        "effectOriginY": effect_origin_y,
        "effectWidth": effect_extent,
        "effectHeight": effect_extent,
        "effectCornerRadius": 0.5 * effect_extent,
    }


def expected_foreground_inputs(progress: float) -> dict[str, float]:
    return {
        "inputAberrationAmount": -5 * progress,
        "inputAberrationAngle": 0.5 * math.pi * progress,
        "inputAberrationHeight": 0,
        "inputAberrationOffset": 0,
        "inputEdgeEnd": 0,
        "inputEdgeOpacityEnd": progress,
        "inputEdgeOpacityStart": 0,
        "inputEdgeStart": 0,
        "inputRefractionAmount": 0,
        "inputRefractionHeight": 16 * progress,
        "inputRefractionOffset": -3.3 * progress,
    }


def extended_srgb_to_linear(value: float) -> float:
    if value <= 0.04045:
        return value / 12.92
    return ((value + 0.055) / 1.055) ** 2.4


def expected_clamp(face_white: float) -> float:
    return max(1.0, extended_srgb_to_linear(face_white))


def expected_background_inputs(
    *,
    material: str,
    appearance: str,
    diameter: float,
    remaining: float,
) -> dict[str, float]:
    k = remaining
    progress = 1 - k
    effect_extent = diameter + 16 * progress
    expected = {
        "inputBleedDistance0": k,
        "inputBleedDistance1": 0,
        "inputBlurDistance0": -0.5 * effect_extent * k,
        "inputBlurDistance1": -k,
        "inputBlurDistance2": 0,
        "inputBlurDistance3": 0,
        "inputBlurOpacity0": k,
        "inputBlurOpacity1": 0.2 * k + 0.3 * k * k,
        "inputBlurOpacity2": 0.2 * k + 0.3 * k * k,
        "inputBlurOpacity3": 0.4 * k + 0.6 * k * k,
        "inputBlurOpacity4": 0.4 * k + 0.6 * k * k,
        "inputFaceOpacity": k,
        "inputInnerRefractionAmount": -60 * k,
        "inputInnerRefractionHeight": 20 * k,
        "inputMaxHeadroom": 1.2 + 9_997.8 * k,
        "inputOuterRefractionAmount": 0.2 * effect_extent * k,
        "inputOuterRefractionHeight": 0.125 * effect_extent * k,
        "inputRefractionDistance0": -k,
        "inputRefractionDistance1": 0,
        "inputSDRGradientDistance0": -2 * k,
        "inputSDRGradientDistance1": -k,
        "inputSDRHoldingToneWhite": 1 - 0.03 * k,
        "inputSDRShadowOpacity": 0.24 * k,
        "inputShadowAmount": 75 * k,
        "inputShadowColorMatrixBlack": 0,
        "inputShadowDistanceOffset": 0,
        "inputShadowHeight": 0.4 * effect_extent * k,
    }
    if material == "clear":
        expected |= {
            "inputBleedAmount": 0,
            "inputBleedBlurRadius": 0,
            "inputBleedColorMatrixBlack": 0.75 * k,
            "inputBleedColorMatrixSaturation": 1 + 0.2 * k,
            "inputBleedColorMatrixWhite": 1,
            "inputBleedHeight": 0,
            "inputBleedOpacity": 0,
            "inputBlurDistance4": 0,
            "inputBlurRadius": k,
            "inputFaceColorMatrixBlack": 0.075 * k,
            "inputFaceColorMatrixSaturation": 1 + 0.06 * k,
            "inputFaceColorMatrixWhite": 1 + 0.15 * k,
            "inputRefractionOpacity": 0,
            "inputShadowBlurRadius": 0,
            "inputShadowColorMatrixSaturation": 1 + 0.2 * k,
            "inputShadowColorMatrixWhite": 1,
            "inputShadowOpacity": 0,
            "inputShadowRadius": 0,
            "inputShadowVibrancyContribution": 0,
        }
        expected["inputClamp"] = expected_clamp(
            expected["inputFaceColorMatrixWhite"]
        )
        return expected
    if material != "regular":
        raise ValueError(f"unsupported material: {material!r}")
    expected |= {
        "inputBleedAmount": 0.35 * effect_extent * k,
        "inputBleedBlurRadius": 160 * k,
        "inputBleedHeight": 0.35 * effect_extent * k,
        "inputBlurDistance4": 0.2 * effect_extent * k,
        "inputBlurRadius": 4 * k,
        "inputFaceColorMatrixSaturation": 1,
        "inputRefractionOpacity": 0.3 * k,
        "inputShadowBlurRadius": 40 * k,
        "inputShadowOpacity": 0.25 * k,
        "inputShadowRadius": 24 * k,
        "inputShadowVibrancyContribution": k,
    }
    match appearance:
        case "light":
            expected |= {
                "inputBleedColorMatrixBlack": 0.9 * k,
                "inputBleedColorMatrixSaturation": 1 + 0.2 * k,
                "inputBleedColorMatrixWhite": 1,
                "inputBleedOpacity": 0.5 * k,
                "inputFaceColorMatrixBlack": 0.5 * k,
                "inputFaceColorMatrixWhite": 1 + 0.03 * k,
                "inputShadowColorMatrixSaturation": 1 + 0.8 * k,
                "inputShadowColorMatrixWhite": 1,
            }
        case "dark":
            expected |= {
                "inputBleedColorMatrixBlack": 0,
                "inputBleedColorMatrixSaturation": 1,
                "inputBleedColorMatrixWhite": 1 - 0.5 * k,
                "inputBleedOpacity": 0.8 * k,
                "inputFaceColorMatrixBlack": 0.2 * k,
                "inputFaceColorMatrixWhite": 1 - 0.4 * k,
                "inputShadowColorMatrixSaturation": 1,
                "inputShadowColorMatrixWhite": 1 - 0.5 * k,
            }
        case _:
            raise ValueError(f"unsupported appearance: {appearance!r}")
    expected["inputClamp"] = expected_clamp(
        expected["inputFaceColorMatrixWhite"]
    )
    return expected


def _observe_geometry(
    measurements: ProfileMeasurements,
    state: JsonObject,
    expected: dict[str, float],
) -> None:
    records = _record_by_path(state)
    try:
        outer = parse_rect(records[(1,)]["frame"])
        effect = parse_rect(records[(1, 0, 1)]["frame"])
        element = records[(1, 0, 1, 0, 0, 0, 0)]
    except KeyError as error:
        raise ValueError(
            f"transition layer path is missing: {error}"
        ) from error
    measured = {
        "outerOriginX": outer[0],
        "outerOriginY": outer[1],
        "outerWidth": outer[2],
        "outerHeight": outer[3],
        "effectOriginX": effect[0],
        "effectOriginY": effect[1],
        "effectWidth": effect[2],
        "effectHeight": effect[3],
        "effectCornerRadius": float(element["cornerRadius"]),
    }
    for name, value in measured.items():
        measurements.observe_geometry(name, value, expected[name])


def expected_boolean_inputs(
    *,
    material: str,
    appearance: str,
    remaining: float,
) -> dict[str, bool]:
    if material == "regular":
        bleed_darken = appearance == "light"
    elif material == "clear":
        bleed_darken = (
            appearance == "light" or remaining >= 0.5
        )
    else:
        raise ValueError(f"unsupported material: {material!r}")
    return {
        "inputBleedDarkenBlend": bleed_darken,
        "inputClampPreserveHue": False,
        "inputSDRHoldingToneEnabled": True,
    }


def _observe_filter_inputs(
    measurements: ProfileMeasurements,
    *,
    background: JsonObject,
    foreground: JsonObject | None,
    expected_background: dict[str, float],
    expected_booleans: dict[str, bool],
    expected_foreground: dict[str, float],
    remaining: float,
) -> None:
    values = background.get("inputValues")
    if not isinstance(values, dict):
        raise ValueError("glassBackground has no inputValues")
    measurements.background_input_keys.update(values)
    for name, value in values.items():
        if isinstance(value, bool):
            ranges = measurements.boolean_ranges.setdefault(
                name,
                {False: [], True: []},
            )
            ranges[value].append(remaining)
        elif isinstance(value, (int, float)):
            measurements.numeric_inputs.add(name)
        elif isinstance(value, dict):
            class_name = str(value.get("class", "unknown"))
            measurements.structured_inputs.setdefault(
                name,
                set(),
            ).add(class_name)
    for name, expected in expected_background.items():
        value = values.get(name)
        if not (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
        ):
            raise ValueError(
                f"glassBackground {name} is not numeric: {value!r}"
            )
        measurements.modeled_inputs.add(name)
        measurements.modeled_background_input_keys.add(name)
        measurements.observe_background(
            name,
            float(value),
            expected,
        )
    clamp = values.get("inputClamp")
    if not (
        isinstance(clamp, (int, float))
        and not isinstance(clamp, bool)
    ):
        raise ValueError("glassBackground inputClamp is not numeric")
    measurements.clamp_samples.append((remaining, float(clamp)))
    for name, expected in expected_booleans.items():
        value = values.get(name)
        if not isinstance(value, bool):
            raise ValueError(
                f"glassBackground {name} is not boolean: {value!r}"
            )
        if value is not expected:
            raise ValueError(
                f"glassBackground {name}={value!r}; "
                f"expected {expected!r}"
            )
        measurements.modeled_background_input_keys.add(name)
        measurements.observe_exact_discrete_rule(name)

    if foreground is None:
        if remaining != 1:
            raise ValueError(
                "glassForeground is absent away from full material state"
            )
        return
    foreground_values = foreground.get("inputValues")
    if not isinstance(foreground_values, dict):
        raise ValueError("glassForeground has no inputValues")
    measurements.foreground_input_keys.update(foreground_values)
    for name, expected in expected_foreground.items():
        value = foreground_values.get(name)
        if not (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
        ):
            raise ValueError(
                f"glassForeground {name} is not numeric: {value!r}"
            )
        measurements.observe_foreground(
            name,
            float(value),
            expected,
        )
        measurements.modeled_foreground_input_keys.add(name)
    if foreground_values.get("inputSourceSublayerName") != "@0":
        raise ValueError("glassForeground source sublayer is not @0")
    if foreground_values.get("inputRefractionAngle") is not None:
        raise ValueError("glassForeground refraction angle is not nil")
    measurements.modeled_foreground_input_keys.update(
        {"inputSourceSublayerName", "inputRefractionAngle"}
    )
    measurements.observe_exact_discrete_rule(
        "glassForeground.inputSourceSublayerName=@0"
    )
    measurements.observe_exact_discrete_rule(
        "glassForeground.inputRefractionAngle=nil"
    )
    if (
        measurements.foreground_input_keys
        - measurements.modeled_foreground_input_keys
    ):
        raise ValueError(
            "glassForeground has unmodeled inputs: "
            f"{sorted(measurements.foreground_input_keys - measurements.modeled_foreground_input_keys)}"
        )


def expected_color_inputs(
    *,
    material: str,
    appearance: str,
    remaining: float,
) -> dict[str, tuple[float, float, float, float] | None]:
    if material == "clear":
        return {
            "inputBleedColorMatrixFillColor": None,
            "inputFaceColorMatrixFillColor":
                (1, 1, 1, 0) if remaining == 1 else None,
            "inputShadowColorMatrixFillColor":
                (0, 0, 0, 0.1 * remaining),
        }
    if material != "regular":
        raise ValueError(f"unsupported material: {material!r}")
    if appearance == "light":
        return {
            "inputBleedColorMatrixFillColor": None,
            "inputFaceColorMatrixFillColor":
                (1, 1, 1, 0.4 * remaining),
            "inputShadowColorMatrixFillColor":
                (0, 0, 0, 0.12 * remaining),
        }
    if appearance == "dark":
        return {
            "inputBleedColorMatrixFillColor": None,
            "inputFaceColorMatrixFillColor":
                (0, 0, 0, 0.4 * remaining),
            "inputShadowColorMatrixFillColor": None,
        }
    raise ValueError(f"unsupported appearance: {appearance!r}")


def _observe_color_inputs(
    measurements: ProfileMeasurements,
    *,
    values: JsonObject,
    expected: dict[
        str,
        tuple[float, float, float, float] | None,
    ],
) -> None:
    component_names = ("red", "green", "blue", "alpha")
    for name, expected_components in expected.items():
        measurements.modeled_background_input_keys.add(name)
        value = values.get(name)
        present = isinstance(value, dict)
        measurements.observe_color_presence(name, present)
        if expected_components is None:
            if value is not None:
                raise ValueError(
                    f"{name} should be nil, found {value!r}"
                )
            continue
        if not isinstance(value, dict):
            raise ValueError(
                f"{name} has no exact CGColor components"
            )
        components = value.get("components")
        if (
            not isinstance(components, list)
            or len(components) != 4
            or value.get("numberOfComponents") != 4
        ):
            raise ValueError(
                f"{name} is not four-component RGBA: {value!r}"
            )
        if value.get("colorSpaceName") != "kCGColorSpaceExtendedSRGB":
            raise ValueError(
                f"{name} does not use extended sRGB"
            )
        for component_name, measured, expected_component in zip(
            component_names,
            components,
            expected_components,
            strict=True,
        ):
            if not isinstance(measured, (int, float)):
                raise ValueError(
                    f"{name}.{component_name} is not numeric"
                )
            measurements.observe_color(
                f"{name}.{component_name}",
                float(measured),
                expected_component,
            )
        alpha = value.get("alpha")
        if not isinstance(alpha, (int, float)):
            raise ValueError(f"{name}.alpha metadata is not numeric")
        measurements.observe_color(
            f"{name}.alphaMetadata",
            float(alpha),
            float(components[3]),
        )


def _observe_static_background_inputs(
    measurements: ProfileMeasurements,
    values: JsonObject,
) -> None:
    if values.get("inputSourceSublayerName") != "@0":
        raise ValueError("glassBackground source sublayer is not @0")
    measurements.modeled_background_input_keys.add(
        "inputSourceSublayerName"
    )
    measurements.observe_exact_discrete_rule(
        "glassBackground.inputSourceSublayerName=@0"
    )

    shadow_offset = values.get("inputShadowOffset")
    if not isinstance(shadow_offset, dict):
        raise ValueError("glassBackground shadow offset is not NSValue")
    if (
        shadow_offset.get("objCType") != "{CGSize=dd}"
        or shadow_offset.get("hex")
        != "00000000000000000000000000002040"
    ):
        raise ValueError(
            "glassBackground shadow offset is not CGSize(0, 8)"
        )
    measurements.modeled_background_input_keys.add(
        "inputShadowOffset"
    )
    measurements.observe_exact_discrete_rule(
        "glassBackground.inputShadowOffset=CGSize(0,8)"
    )


def _observe_vibrant_matrix(
    measurements: ProfileMeasurements,
    state: JsonObject,
) -> None:
    color_filter = named_filter(state, "vibrantColorMatrix")
    if color_filter is None:
        return
    values = color_filter.get("inputValues", {})
    matrix = (
        values.get("inputColorMatrix")
        if isinstance(values, dict)
        else None
    )
    encoded = matrix.get("hex") if isinstance(matrix, dict) else None
    if isinstance(encoded, str):
        measurements.vibrant_matrix_hex.add(encoded)


def _fit_polynomial(
    samples: list[tuple[float, float]],
    degree: int,
) -> JsonObject:
    x = np.asarray([sample[0] for sample in samples], dtype=np.float64)
    y = np.asarray([sample[1] for sample in samples], dtype=np.float64)
    coefficients = np.polynomial.polynomial.polyfit(x, y, degree)
    prediction = np.polynomial.polynomial.polyval(x, coefficients)
    residual = np.abs(prediction - y)
    return {
        "degree": degree,
        "coefficientOrder": "constant-to-highest-power",
        "coefficients": coefficients.tolist(),
        "maximumAbsoluteResidual": float(residual.max(initial=0)),
        "meanAbsoluteResidual": float(residual.mean()),
        "diagnosticOnly": True,
    }


def _profile_json(
    measurements: ProfileMeasurements,
) -> JsonObject:
    boolean_ranges = {
        name: {
            str(value).lower(): {
                "count": len(samples),
                "minimumRemaining": min(samples) if samples else None,
                "maximumRemaining": max(samples) if samples else None,
            }
            for value, samples in ranges.items()
        }
        for name, ranges in sorted(measurements.boolean_ranges.items())
    }
    return {
        "geometryLaws": {
            name: residual.as_json()
            for name, residual in sorted(
                measurements.geometry.items()
            )
        },
        "backgroundFilterLaws": {
            name: residual.as_json()
            for name, residual in sorted(
                measurements.background.items()
            )
        },
        "foregroundFilterLaws": {
            name: residual.as_json()
            for name, residual in sorted(
                measurements.foreground.items()
            )
        },
        "exactColorLaws": {
            name: residual.as_json()
            for name, residual in sorted(
                measurements.colors.items()
            )
        },
        "exactColorPresence": {
            name: counts
            for name, counts in sorted(
                measurements.color_presence.items()
            )
        },
        "scheduledProgressVsPrivateProgress":
            measurements.scheduled_progress.as_json(),
        "stateBeforeVsAfterRemaining":
            measurements.state_bracket_remaining.as_json(),
        "clampTransferLaw": {
            "equation":
                "max(1, extended_sRGB_EOTF("
                "inputFaceColorMatrixWhite))",
            "piecewiseThreshold": 0.04045,
            "highBranch": "((x + 0.055) / 1.055) ^ 2.4",
            "lowBranch": "x / 12.92",
            "residual":
                measurements.background.get(
                    "inputClamp",
                    Residual(),
                ).as_json(),
        },
        "clampCubicDiagnostic":
            _fit_polynomial(measurements.clamp_samples, 3),
        "numericInputCoverage": {
            "observed": len(measurements.numeric_inputs),
            "modeled": len(measurements.modeled_inputs),
            "unmodeled": sorted(
                measurements.numeric_inputs
                - measurements.modeled_inputs
            ),
        },
        "allInputCoverage": {
            "background": {
                "observed": len(
                    measurements.background_input_keys
                ),
                "modeled": len(
                    measurements.modeled_background_input_keys
                ),
                "unmodeled": sorted(
                    measurements.background_input_keys
                    - measurements.modeled_background_input_keys
                ),
            },
            "foreground": {
                "observed": len(
                    measurements.foreground_input_keys
                ),
                "modeled": len(
                    measurements.modeled_foreground_input_keys
                ),
                "unmodeled": sorted(
                    measurements.foreground_input_keys
                    - measurements.modeled_foreground_input_keys
                ),
            },
        },
        "exactDiscreteRules": {
            name: count
            for name, count in sorted(
                measurements.exact_discrete_rules.items()
            )
        },
        "booleanRanges": boolean_ranges,
        "structuredInputClasses": {
            name: sorted(classes)
            for name, classes in sorted(
                measurements.structured_inputs.items()
            )
        },
        "distinctVibrantColorMatrices":
            len(measurements.vibrant_matrix_hex),
        "vibrantColorMatrixHex": sorted(
            measurements.vibrant_matrix_hex
        ),
    }


def _safe_artifact_file(
    root: Path,
    parent: Path,
    relative: object,
) -> Path:
    if not isinstance(relative, str):
        raise ValueError(f"artifact path is not a string: {relative!r}")
    root = root.resolve()
    candidate = (parent / relative).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as error:
        raise ValueError(
            f"artifact path escapes root: {relative!r}"
        ) from error
    return candidate


def _verify_frame(
    *,
    root: Path,
    report_path: Path,
    output: JsonObject,
) -> tuple[int, int]:
    path = _safe_artifact_file(
        root,
        report_path.parent,
        output.get("pngFile"),
    )
    encoded = path.read_bytes()
    if len(encoded) != output.get("pngBytes"):
        raise ValueError(f"{path}: PNG length differs")
    if sha256_bytes(encoded) != output.get("pngSHA256"):
        raise ValueError(f"{path}: PNG SHA-256 differs")
    with Image.open(path) as source:
        image = source.convert("RGBA")
        width, height = image.size
        pixels = image.tobytes()
    if width != output.get("width") or height != output.get("height"):
        raise ValueError(f"{path}: pixel dimensions differ")
    if len(pixels) != output.get("pixelBytes"):
        raise ValueError(f"{path}: canonical byte length differs")
    if sha256_bytes(pixels) != output.get("pixelSHA256"):
        raise ValueError(f"{path}: canonical RGBA SHA-256 differs")
    return width, height


def _analyze_schema3(root: Path) -> JsonObject:
    root = root.resolve()
    report_paths = sorted(root.glob(f"*/{REPORT_NAME}"))
    if len(report_paths) != 4:
        raise ValueError(
            f"{root} has {len(report_paths)} transition reports; expected 4"
        )

    reports: dict[tuple[str, str], tuple[Path, JsonObject]] = {}
    for path in report_paths:
        report = json.loads(path.read_text(encoding="utf-8"))
        if report.get("schemaVersion") != 3:
            raise ValueError(f"{path}: schema is not 3")
        profile = (
            str(report.get("material")),
            str(report.get("appearance")),
        )
        if profile in reports:
            raise ValueError(f"duplicate transition profile: {profile}")
        reports[profile] = (path, report)
    if reports.keys() != EXPECTED_PROFILES:
        raise ValueError(
            "transition profile matrix differs: "
            f"{sorted(reports)}"
        )

    frame_count = 0
    capture_durations: list[float] = []
    bracket_durations: list[float] = []
    maximum_schedule_drift = 0.0
    endpoint_hashes: set[str] = set()
    initial_hashes: set[str] = set()
    profiles: JsonObject = {}

    for (material, appearance), (
        report_path,
        report,
    ) in sorted(reports.items()):
        samples = report.get("samples")
        if not isinstance(samples, list) or len(samples) != 33:
            raise ValueError(f"{report_path}: expected 33 samples")
        geometry = report.get("geometry", {})
        diameter = float(geometry["width"])
        center_x = float(geometry["centerX"])
        center_y = float(geometry["centerY"])
        measurements = ProfileMeasurements()

        for sample_index, sample in enumerate(samples):
            if not isinstance(sample, dict):
                raise ValueError(
                    f"{report_path}: sample {sample_index} is invalid"
                )
            output = sample.get("windowCapture")
            if not isinstance(output, dict):
                raise ValueError(
                    f"{report_path}: sample {sample_index} has no output"
                )
            _verify_frame(
                root=root,
                report_path=report_path,
                output=output,
            )
            frame_count += 1
            capture_durations.append(
                float(output["captureDurationSeconds"])
            )
            bracket_durations.append(
                float(sample["stateBracketSeconds"])
            )
            requested = float(sample["progress"])
            actual = float(sample["actualProgress"])
            maximum_schedule_drift = max(
                maximum_schedule_drift,
                abs(actual - requested),
            )
            if sample_index == 0:
                initial_hashes.add(str(output["pixelSHA256"]))
            if sample_index == len(samples) - 1:
                endpoint_hashes.add(str(output["pixelSHA256"]))

            for state_key in STATE_KEYS:
                state = sample.get(state_key)
                if not isinstance(state, dict):
                    raise ValueError(
                        f"{report_path}: sample {sample_index} lacks "
                        f"{state_key}"
                    )
                background = named_filter(
                    state,
                    "glassBackground",
                )
                if background is None:
                    if sample_index != len(samples) - 1:
                        raise ValueError(
                            f"{report_path}: glassBackground disappeared "
                            f"at sample {sample_index}"
                        )
                    _observe_vibrant_matrix(measurements, state)
                    continue
                values = background.get("inputValues")
                if not isinstance(values, dict):
                    raise ValueError(
                        f"{report_path}: glassBackground has no values"
                    )
                remaining = float(values["inputFaceOpacity"])
                progress = 1 - remaining
                expected_background = expected_background_inputs(
                    material=material,
                    appearance=appearance,
                    diameter=diameter,
                    remaining=remaining,
                )
                expected_foreground = expected_foreground_inputs(
                    progress
                )
                expected_booleans = expected_boolean_inputs(
                    material=material,
                    appearance=appearance,
                    remaining=remaining,
                )
                _observe_geometry(
                    measurements,
                    state,
                    expected_geometry(
                        diameter=diameter,
                        center_x=center_x,
                        center_y=center_y,
                        remaining=remaining,
                    ),
                )
                _observe_filter_inputs(
                    measurements,
                    background=background,
                    foreground=named_filter(
                        state,
                        "glassForeground",
                    ),
                    expected_background=expected_background,
                    expected_booleans=expected_booleans,
                    expected_foreground=expected_foreground,
                    remaining=remaining,
                )
                _observe_static_background_inputs(
                    measurements,
                    values,
                )
                _observe_vibrant_matrix(measurements, state)
                if state_key == STATE_KEYS[0]:
                    measurements.scheduled_progress.observe(
                        progress,
                        actual,
                    )

        profile_name = f"{material}-{appearance}"
        profiles[profile_name] = _profile_json(measurements)

    return {
        "schemaVersion": 1,
        "analysis": "private-liquid-glass-transition-state-law",
        "artifact": str(root),
        "implementation": {
            "python": platform.python_version(),
            "numpy": np.__version__,
            "pillow": Image.__version__,
        },
        "integrity": {
            "reports": len(report_paths),
            "frames": frame_count,
            "pngAndCanonicalRGBAExact": True,
            "distinctNoGlassEndpointHashes": len(endpoint_hashes),
            "noGlassEndpointHashes": sorted(endpoint_hashes),
            "distinctInitialGlassHashes": len(initial_hashes),
        },
        "timing": {
            "minimumCaptureSeconds": min(capture_durations),
            "maximumCaptureSeconds": max(capture_durations),
            "meanCaptureSeconds": statistics.fmean(
                capture_durations
            ),
            "minimumStateBracketSeconds": min(bracket_durations),
            "maximumStateBracketSeconds": max(bracket_durations),
            "meanStateBracketSeconds": statistics.fmean(
                bracket_durations
            ),
            "maximumRequestedProgressDrift":
                maximum_schedule_drift,
        },
        "stateDefinition": {
            "remainingSymbol": "k",
            "progressSymbol": "p",
            "identity": "p = 1 - k",
            "kSource": "glassBackground.inputFaceOpacity",
            "direction": "dematerialize",
        },
        "profiles": profiles,
        "remainingExactEvidenceGap": {
            "inputsWithoutExactComponents": [
                "glassBackground.inputFaceColorMatrixFillColor",
                "glassBackground.inputShadowColorMatrixFillColor",
            ],
            "reason":
                "schema 3 serialized CGColor descriptions but not exact "
                "component values",
            "directionNotYetIntrospected": "materialize",
            "geometryGeneralizationNotYetIntrospected":
                "only circle-800-center",
            "nonlinearClampLaw":
                "cubic fits are diagnostics, not accepted parity laws",
        },
    }


def _distribution(values: list[float]) -> JsonObject:
    samples = np.asarray(values, dtype=np.float64)
    return {
        "count": len(values),
        "minimum": float(samples.min()),
        "p50": float(np.percentile(samples, 50)),
        "p95": float(np.percentile(samples, 95)),
        "p99": float(np.percentile(samples, 99)),
        "maximum": float(samples.max()),
        "mean": statistics.fmean(values),
    }


def _load_rgba(
    root: Path,
    report_path: Path,
    output: JsonObject,
) -> np.ndarray:
    path = _safe_artifact_file(
        root,
        report_path.parent,
        output.get("pngFile"),
    )
    with Image.open(path) as source:
        return np.asarray(source.convert("RGBA"), dtype=np.int16)


def _pixel_difference(
    first: np.ndarray,
    second: np.ndarray,
) -> JsonObject:
    if first.shape != second.shape:
        raise ValueError(
            f"endpoint shapes differ: {first.shape} != {second.shape}"
        )
    delta = np.abs(first - second)
    changed = np.any(delta != 0, axis=2)
    changed_pixels = int(changed.sum())
    pixel_count = int(changed.size)
    squared = np.square(
        first.astype(np.float64) - second.astype(np.float64)
    )
    mean_squared = float(squared.mean())
    if changed_pixels:
        y, x = np.nonzero(changed)
        bounding_box: JsonObject | None = {
            "minimumX": int(x.min()),
            "minimumY": int(y.min()),
            "maximumX": int(x.max()),
            "maximumY": int(y.max()),
        }
    else:
        bounding_box = None
    return {
        "exact": changed_pixels == 0,
        "pixels": pixel_count,
        "changedPixels": changed_pixels,
        "changedPixelFraction": changed_pixels / pixel_count,
        "maximumAbsoluteChannelDelta": int(delta.max(initial=0)),
        "meanAbsoluteChannelDelta": float(delta.mean()),
        "rootMeanSquareChannelDelta": math.sqrt(mean_squared),
        "peakSignalToNoiseRatioDB":
            None
            if mean_squared == 0
            else 10 * math.log10(255**2 / mean_squared),
        "changedPixelBoundingBox": bounding_box,
    }


def _normalized_runtime_value(value: object) -> object:
    if isinstance(value, list):
        return [
            _normalized_runtime_value(component)
            for component in value
        ]
    if not isinstance(value, dict):
        return value
    if (
        isinstance(value.get("components"), list)
        and isinstance(value.get("colorSpaceName"), str)
    ):
        return {
            "colorSpaceName": value["colorSpaceName"],
            "components": value["components"],
        }
    if isinstance(value.get("hex"), str):
        return {
            "hex": value["hex"],
            "objCType": value.get("objCType"),
        }
    ignored = {
        "class",
        "colorSpace",
        "debugDescription",
        "description",
    }
    return {
        key: _normalized_runtime_value(component)
        for key, component in sorted(value.items())
        if key not in ignored
    }


def _full_glass_inputs(
    report: JsonObject,
) -> object:
    direction = str(report["direction"])
    sample = (
        report["samples"][-1]
        if direction == "materialize"
        else report["samples"][0]
    )
    state = sample[STATE_KEYS[0]]
    background = named_filter(state, "glassBackground")
    if background is None:
        raise ValueError("full-glass endpoint has no glassBackground")
    values = background.get("inputValues")
    if not isinstance(values, dict):
        raise ValueError("full-glass endpoint has no inputValues")
    return _normalized_runtime_value(values)


def _scenario_name(
    direction: str,
    material: str,
    appearance: str,
    geometry: str,
) -> str:
    return f"{direction}-{material}-{appearance}-{geometry}"


def _analyze_schema4(
    root: Path,
    *,
    source_schema: int = 4,
) -> JsonObject:
    root = root.resolve()
    report_paths = sorted(root.glob(f"*/{REPORT_NAME}"))
    if len(report_paths) != 16:
        raise ValueError(
            f"{root} has {len(report_paths)} transition reports; "
            "expected 16"
        )

    reports: dict[
        tuple[str, str, str, str],
        tuple[Path, JsonObject],
    ] = {}
    for path in report_paths:
        report = json.loads(path.read_text(encoding="utf-8"))
        if report.get("schemaVersion") != source_schema:
            raise ValueError(
                f"{path}: schema is not {source_schema}"
            )
        geometry = report.get("geometry")
        if not isinstance(geometry, dict):
            raise ValueError(f"{path}: geometry is missing")
        scenario = (
            str(report.get("direction")),
            str(report.get("material")),
            str(report.get("appearance")),
            str(geometry.get("name")),
        )
        if scenario in reports:
            raise ValueError(f"duplicate transition scenario: {scenario}")
        reports[scenario] = (path, report)
    if reports.keys() != EXPECTED_SCHEMA4_SCENARIOS:
        raise ValueError(
            f"schema-{source_schema} transition matrix differs: "
            f"{sorted(reports)}"
        )

    aggregate = ProfileMeasurements()
    frame_count = 0
    capture_durations: list[float] = []
    bracket_durations: list[float] = []
    frame_dimensions: set[tuple[int, int]] = set()
    maximum_schedule_drift = 0.0
    no_glass_hashes: set[str] = set()
    full_glass_hashes: set[str] = set()
    endpoints: dict[
        tuple[str, str, str],
        dict[str, tuple[Path, JsonObject, JsonObject]],
    ] = {}
    scenarios: JsonObject = {}

    for (
        direction,
        material,
        appearance,
        geometry_name,
    ), (report_path, report) in sorted(reports.items()):
        if report.get("failedSamples") not in (0, [], None):
            raise ValueError(f"{report_path}: reports failed samples")
        samples = report.get("samples")
        if not isinstance(samples, list) or len(samples) != 33:
            raise ValueError(f"{report_path}: expected 33 samples")
        geometry = report["geometry"]
        diameter = float(geometry["width"])
        if float(geometry["height"]) != diameter:
            raise ValueError(f"{report_path}: geometry is not circular")
        center_x = float(geometry["centerX"])
        center_y = float(geometry["centerY"])
        window_center_x = float(geometry["windowWidth"]) / 2
        window_center_y = float(geometry["windowHeight"]) / 2
        measurements = ProfileMeasurements()
        scenario_capture_durations: list[float] = []
        scenario_bracket_durations: list[float] = []
        scenario_hashes: set[str] = set()

        for sample_index, sample in enumerate(samples):
            if not isinstance(sample, dict):
                raise ValueError(
                    f"{report_path}: sample {sample_index} is invalid"
                )
            output = sample.get("windowCapture")
            if not isinstance(output, dict):
                raise ValueError(
                    f"{report_path}: sample {sample_index} has no output"
                )
            dimensions = _verify_frame(
                root=root,
                report_path=report_path,
                output=output,
            )
            frame_dimensions.add(dimensions)
            frame_count += 1
            capture_duration = float(
                output["captureDurationSeconds"]
            )
            bracket_duration = float(sample["stateBracketSeconds"])
            capture_durations.append(capture_duration)
            bracket_durations.append(bracket_duration)
            scenario_capture_durations.append(capture_duration)
            scenario_bracket_durations.append(bracket_duration)
            scenario_hashes.add(str(output["pixelSHA256"]))

            requested = float(sample["progress"])
            actual = float(sample["actualProgress"])
            maximum_schedule_drift = max(
                maximum_schedule_drift,
                abs(actual - requested),
            )
            animation_position = min(1.0, max(0.0, actual))
            expected_remaining = (
                animation_position
                if direction == "materialize"
                else 1 - animation_position
            )
            remaining_before: float | None = None

            for state_key in STATE_KEYS:
                state = sample.get(state_key)
                if not isinstance(state, dict):
                    raise ValueError(
                        f"{report_path}: sample {sample_index} lacks "
                        f"{state_key}"
                    )
                background = named_filter(state, "glassBackground")
                if background is None:
                    expected_absent = (
                        direction == "materialize"
                        and sample_index == 0
                    ) or (
                        direction == "dematerialize"
                        and sample_index == len(samples) - 1
                    )
                    if not expected_absent:
                        raise ValueError(
                            f"{report_path}: glassBackground unexpectedly "
                            f"absent at sample {sample_index}"
                        )
                    for target in (measurements, aggregate):
                        _observe_vibrant_matrix(target, state)
                    continue
                values = background.get("inputValues")
                if not isinstance(values, dict):
                    raise ValueError(
                        f"{report_path}: glassBackground has no values"
                    )
                remaining = float(values["inputFaceOpacity"])
                progress = 1 - remaining
                expected_background = expected_background_inputs(
                    material=material,
                    appearance=appearance,
                    diameter=diameter,
                    remaining=remaining,
                )
                expected_foreground = expected_foreground_inputs(
                    progress
                )
                expected_booleans = expected_boolean_inputs(
                    material=material,
                    appearance=appearance,
                    remaining=remaining,
                )
                expected_colors = expected_color_inputs(
                    material=material,
                    appearance=appearance,
                    remaining=remaining,
                )
                expected_background_keys = (
                    set(expected_background)
                    | set(expected_booleans)
                    | set(expected_colors)
                    | {
                        "inputShadowOffset",
                        "inputSourceSublayerName",
                    }
                )
                if values.keys() != expected_background_keys:
                    raise ValueError(
                        f"{report_path}: glassBackground input set "
                        f"differs at sample {sample_index}: "
                        f"{sorted(set(values) ^ expected_background_keys)}"
                    )
                for target in (measurements, aggregate):
                    _observe_geometry(
                        target,
                        state,
                        expected_geometry(
                            diameter=diameter,
                            center_x=center_x,
                            center_y=center_y,
                            remaining=remaining,
                            window_center_x=window_center_x,
                            window_center_y=window_center_y,
                        ),
                    )
                    _observe_filter_inputs(
                        target,
                        background=background,
                        foreground=named_filter(
                            state,
                            "glassForeground",
                        ),
                        expected_background=expected_background,
                        expected_booleans=expected_booleans,
                        expected_foreground=expected_foreground,
                        remaining=remaining,
                    )
                    _observe_color_inputs(
                        target,
                        values=values,
                        expected=expected_colors,
                    )
                    _observe_static_background_inputs(
                        target,
                        values,
                    )
                    _observe_vibrant_matrix(target, state)
                if state_key == STATE_KEYS[0]:
                    remaining_before = remaining
                    for target in (measurements, aggregate):
                        target.scheduled_progress.observe(
                            remaining,
                            expected_remaining,
                        )
                elif remaining_before is not None:
                    for target in (measurements, aggregate):
                        target.state_bracket_remaining.observe(
                            remaining,
                            remaining_before,
                        )

        if len(scenario_hashes) != len(samples):
            raise ValueError(
                f"{report_path}: transition frames are not all distinct"
            )
        pair_key = (material, appearance, geometry_name)
        endpoints.setdefault(pair_key, {})[direction] = (
            report_path,
            samples[0]["windowCapture"],
            samples[-1]["windowCapture"],
        )
        if direction == "materialize":
            no_glass_hashes.add(
                str(samples[0]["windowCapture"]["pixelSHA256"])
            )
            full_glass_hashes.add(
                str(samples[-1]["windowCapture"]["pixelSHA256"])
            )
        else:
            full_glass_hashes.add(
                str(samples[0]["windowCapture"]["pixelSHA256"])
            )
            no_glass_hashes.add(
                str(samples[-1]["windowCapture"]["pixelSHA256"])
            )
        name = _scenario_name(
            direction,
            material,
            appearance,
            geometry_name,
        )
        scenarios[name] = {
            "direction": direction,
            "material": material,
            "appearance": appearance,
            "geometry": geometry,
            "distinctFrameHashes": len(scenario_hashes),
            "captureSeconds": _distribution(
                scenario_capture_durations
            ),
            "stateBracketSeconds": _distribution(
                scenario_bracket_durations
            ),
            "measurements": _profile_json(measurements),
        }

    endpoint_pairs: JsonObject = {}
    maximum_full_changed_fraction = 0.0
    maximum_full_channel_delta = 0
    maximum_full_mean_delta = 0.0
    minimum_full_psnr = math.inf
    exact_full_pairs = 0
    exact_full_state_pairs = 0
    for pair_key, directions in sorted(endpoints.items()):
        if directions.keys() != {"dematerialize", "materialize"}:
            raise ValueError(
                f"direction pair is incomplete: {pair_key}"
            )
        material, appearance, geometry_name = pair_key
        materialize_path, materialize_start, materialize_end = (
            directions["materialize"]
        )
        dematerialize_path, dematerialize_start, dematerialize_end = (
            directions["dematerialize"]
        )
        no_glass = _pixel_difference(
            _load_rgba(
                root,
                materialize_path,
                materialize_start,
            ),
            _load_rgba(
                root,
                dematerialize_path,
                dematerialize_end,
            ),
        )
        if not no_glass["exact"]:
            raise ValueError(
                f"no-glass endpoints differ: {pair_key}"
            )
        full_glass = _pixel_difference(
            _load_rgba(
                root,
                materialize_path,
                materialize_end,
            ),
            _load_rgba(
                root,
                dematerialize_path,
                dematerialize_start,
            ),
        )
        if full_glass["exact"]:
            exact_full_pairs += 1
        maximum_full_changed_fraction = max(
            maximum_full_changed_fraction,
            float(full_glass["changedPixelFraction"]),
        )
        maximum_full_channel_delta = max(
            maximum_full_channel_delta,
            int(full_glass["maximumAbsoluteChannelDelta"]),
        )
        maximum_full_mean_delta = max(
            maximum_full_mean_delta,
            float(full_glass["meanAbsoluteChannelDelta"]),
        )
        psnr = full_glass["peakSignalToNoiseRatioDB"]
        if isinstance(psnr, (int, float)):
            minimum_full_psnr = min(minimum_full_psnr, float(psnr))

        materialize_report = reports[
            (
                "materialize",
                material,
                appearance,
                geometry_name,
            )
        ][1]
        dematerialize_report = reports[
            (
                "dematerialize",
                material,
                appearance,
                geometry_name,
            )
        ][1]
        high_level_state_exact = (
            _full_glass_inputs(materialize_report)
            == _full_glass_inputs(dematerialize_report)
        )
        if high_level_state_exact:
            exact_full_state_pairs += 1
        pair_name = f"{material}-{appearance}-{geometry_name}"
        endpoint_pairs[pair_name] = {
            "noGlass": no_glass,
            "fullGlass": full_glass,
            "fullGlassHighLevelInputsExact":
                high_level_state_exact,
        }

    return {
        "schemaVersion": 2,
        "analysis": "private-liquid-glass-transition-state-law",
        "artifact": str(root),
        "implementation": {
            "python": platform.python_version(),
            "numpy": np.__version__,
            "pillow": Image.__version__,
        },
        "integrity": {
            "sourceSchemaVersion": source_schema,
            "reports": len(report_paths),
            "directionPairs": len(endpoints),
            "frames": frame_count,
            "pngAndCanonicalRGBAExact": True,
            "frameDimensions": [
                {"width": width, "height": height}
                for width, height in sorted(frame_dimensions)
            ],
            "allFramesDistinctWithinEachTransition": True,
            "distinctNoGlassEndpointHashes": len(no_glass_hashes),
            "noGlassEndpointHashes": sorted(no_glass_hashes),
            "distinctFullGlassEndpointHashes": len(
                full_glass_hashes
            ),
        },
        "timing": {
            "captureSeconds": _distribution(capture_durations),
            "stateBracketSeconds": _distribution(bracket_durations),
            "maximumRequestedProgressDrift":
                maximum_schedule_drift,
        },
        "stateDefinition": {
            "remainingSymbol": "k",
            "removedSymbol": "p",
            "identity": "p = 1 - k",
            "kSource": "glassBackground.inputFaceOpacity",
            "directions": {
                "materialize": "k follows clamped animation position",
                "dematerialize":
                    "k follows 1 - clamped animation position",
            },
        },
        "formulaDefinitions": {
            "outerDiameter": "D * k",
            "outerOrigin": "windowCenter - D * k / 2",
            "effectExtent": "D + 16 * p",
            "effectRelativeOrigin":
                "round(requestedCenter) - windowCenter "
                "- round(D * p / 2) - 8 * p",
            "effectCornerRadius": "(D + 16 * p) / 2",
            "foregroundAberrationAmount": "-5 * p",
            "foregroundAberrationAngle": "pi * p / 2",
            "foregroundRefractionHeight": "16 * p",
            "foregroundRefractionOffset": "-3.3 * p",
            "clamp":
                "max(1, extended_sRGB_EOTF("
                "inputFaceColorMatrixWhite))",
        },
        "aggregateMeasurements": _profile_json(aggregate),
        "directionEndpointPairs": endpoint_pairs,
        "appleFullEndpointRepeatability": {
            "pairs": len(endpoint_pairs),
            "bitExactPairs": exact_full_pairs,
            "highLevelInputExactPairs": exact_full_state_pairs,
            "maximumChangedPixelFraction":
                maximum_full_changed_fraction,
            "maximumAbsoluteChannelDelta":
                maximum_full_channel_delta,
            "maximumMeanAbsoluteChannelDelta":
                maximum_full_mean_delta,
            "minimumPeakSignalToNoiseRatioDB":
                None
                if minimum_full_psnr == math.inf
                else minimum_full_psnr,
            "interpretation":
                "These independently rendered Apple endpoints have "
                "identical private high-level inputs. Their residual "
                "pixel delta is the measured Apple-vs-Apple floor for "
                "cross-run bitwise comparison.",
        },
        "scenarios": scenarios,
        "remainingExactEvidenceGap": {
            "highLevelToMetalUniformMapping": (
                "captured by schema 5; use "
                "liquid_glass_transition_uniforms.py and the "
                "matrix-basis analysis for byte-level packing"
                if source_schema == 5
                else
                "live private filter inputs are known, but dynamic "
                "glassBackground Metal uniform buffers are not yet "
                "captured"
            ),
            "shaderOutputMapping": (
                "static Metal shaders and dynamic background uniforms "
                "are captured; Walle integration remains"
                if source_schema == 5
                else
                "static Metal shaders are recovered; transition-time "
                "uniform packing still requires direct evidence"
            ),
            "walleLiveParity":
                "not yet established for transition pixels",
        },
    }


def analyze(root: Path) -> JsonObject:
    root = root.resolve()
    report_paths = sorted(root.glob(f"*/{REPORT_NAME}"))
    if not report_paths:
        raise ValueError(f"{root} has no transition reports")
    schemas = {
        json.loads(path.read_text(encoding="utf-8")).get(
            "schemaVersion"
        )
        for path in report_paths
    }
    if schemas == {3}:
        return _analyze_schema3(root)
    if schemas == {4}:
        return _analyze_schema4(root, source_schema=4)
    if schemas == {5}:
        return _analyze_schema4(root, source_schema=5)
    raise ValueError(f"{root} has unsupported schemas: {sorted(schemas)}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
    )
    parser.add_argument(
        "artifact",
        type=Path,
        help="downloaded schema-3, schema-4, or schema-5 artifact directory",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="write the sorted JSON report here",
    )
    arguments = parser.parse_args()
    report = analyze(arguments.artifact)
    encoded = json.dumps(
        report,
        indent=2,
        sort_keys=True,
    ) + "\n"
    if arguments.output is None:
        print(encoded, end="")
    else:
        arguments.output.write_text(
            encoded,
            encoding="utf-8",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
