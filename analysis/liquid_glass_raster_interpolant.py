#!/usr/bin/env python3
"""Recover and bit-gate Apple AGX raster interpolation coefficients."""

import argparse
import hashlib
import json
import math
import statistics
import struct
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any, Literal

import numpy as np
from numpy.typing import NDArray


type Axis = Literal["x", "y"]
type JsonObject = dict[str, Any]
type UIntSurface = NDArray[np.uint32]

OFFSETS_FOUR = (0.0, 0.0625, 0.5, 0.9375)
OFFSETS_PAIR = (0.0, 0.9375)
LIVE_TRACE_WIDTH = 1024
LIVE_TRACE_HEIGHT = 1024
LIVE_ACTIVE_START = 112
LIVE_ACTIVE_SIZE = 800
AXIS_TRACE_ROWS = 2
AXIS_TRACE_COMPONENTS = 4
LIVE_TILE_START = LIVE_ACTIVE_START // 32
LIVE_TILE_COUNT = (
    (LIVE_ACTIVE_START + LIVE_ACTIVE_SIZE - 1) // 32
    - LIVE_TILE_START
    + 1
)


def float32(value: float) -> float:
    return struct.unpack("<f", struct.pack("<f", value))[0]


def float32_bits(value: float) -> int:
    return struct.unpack("<I", struct.pack("<f", value))[0]


def bits_float32(bits: int) -> float:
    return struct.unpack("<f", struct.pack("<I", bits))[0]


def power_of_two(exponent: int) -> Fraction:
    if exponent >= 0:
        return Fraction(1 << exponent)
    return Fraction(1, 1 << -exponent)


def float32_bits_fraction(bits: int) -> Fraction:
    """Return the exact rational represented by one finite binary32."""

    sign = -1 if bits & 0x80000000 else 1
    exponent = (bits >> 23) & 0xff
    significand = bits & 0x7fffff
    if exponent == 0xff:
        raise ValueError("non-finite binary32 has no rational value")
    if exponent == 0:
        return sign * significand * power_of_two(-149)
    return sign * (
        (1 << 23) | significand
    ) * power_of_two(exponent - 127 - 23)


def floor_binary_exponent(value: Fraction) -> int:
    """Return floor(log2(value)) for a positive rational."""

    if value <= 0:
        raise ValueError("binary exponent requires a positive value")
    exponent = (
        value.numerator.bit_length()
        - value.denominator.bit_length()
    )
    if value < power_of_two(exponent):
        exponent -= 1
    return exponent


def round_fraction_to_integer_nearest_even(value: Fraction) -> int:
    """Round an exact rational to an integer, ties to even."""

    if value < 0:
        return -round_fraction_to_integer_nearest_even(-value)
    quotient, remainder = divmod(
        value.numerator,
        value.denominator,
    )
    doubled = 2 * remainder
    if doubled > value.denominator or (
        doubled == value.denominator and quotient & 1
    ):
        quotient += 1
    return quotient


def quantize_binary_significand(
    value: Fraction,
    precision_bits: int,
    *,
    lattice_offset: int = 0,
) -> Fraction:
    """Round a normal binary value to a fixed significant-bit lattice."""

    if value <= 0:
        raise ValueError("significand quantization requires a positive value")
    if precision_bits < 2:
        raise ValueError("precision must contain at least two bits")
    exponent = floor_binary_exponent(value)
    step = power_of_two(exponent - precision_bits + 1)
    index = round_fraction_to_integer_nearest_even(value / step)
    index += lattice_offset
    if index <= 0:
        raise ValueError("lattice offset produced a nonpositive value")
    return index * step


def quantize_binary_significand_directed(
    value: Fraction,
    precision_bits: int,
    rounding: Literal["down", "nearest-even", "up"],
) -> Fraction:
    """Quantize a positive value on the same lattice with named rounding."""

    if value <= 0:
        raise ValueError("significand quantization requires a positive value")
    if precision_bits < 2:
        raise ValueError("precision must contain at least two bits")
    exponent = floor_binary_exponent(value)
    step = power_of_two(exponent - precision_bits + 1)
    scaled = value / step
    quotient, remainder = divmod(
        scaled.numerator,
        scaled.denominator,
    )
    if rounding == "nearest-even":
        quotient = round_fraction_to_integer_nearest_even(scaled)
    elif rounding == "up":
        quotient += remainder != 0
    elif rounding != "down":
        raise ValueError(f"unknown directed rounding mode: {rounding}")
    return quotient * step


def round_fraction_to_float32_bits(value: Fraction) -> int:
    """Round an exact normal-range rational to binary32, ties to even."""

    if value == 0:
        return 0
    sign = 0x80000000 if value < 0 else 0
    magnitude = abs(value)
    if magnitude < power_of_two(-126):
        raise ValueError("subnormal rounding is outside this probe")
    rounded = quantize_binary_significand(magnitude, 24)
    exponent = floor_binary_exponent(rounded)
    if exponent > 127:
        return sign | 0x7f800000
    significand = int(rounded / power_of_two(exponent - 23))
    if significand == 1 << 24:
        significand >>= 1
        exponent += 1
    if not 1 << 23 <= significand < 1 << 24:
        raise ValueError("invalid normalized binary32 significand")
    return (
        sign
        | ((exponent + 127) << 23)
        | (significand - (1 << 23))
    )


def float32_rounding_interval(bits: int) -> tuple[Fraction, Fraction]:
    """Return the midpoint-bounded RN-even interval for a positive float."""

    if not 0 < bits < 0x7f7fffff:
        raise ValueError("a positive finite nonzero binary32 is required")
    value = float32_bits_fraction(bits)
    previous = float32_bits_fraction(bits - 1)
    following = float32_bits_fraction(bits + 1)
    return (
        (previous + value) / 2,
        (value + following) / 2,
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def round_toward_zero_float32(value: float) -> float:
    """Round an exact binary64 value to binary32 toward zero."""

    rounded = np.float32(value)
    if (value > 0.0 and float(rounded) > value) or (
        value < 0.0 and float(rounded) < value
    ):
        rounded = np.nextafter(rounded, np.float32(0.0))
    return float(rounded)


def apple_iterator_bits(position: float, slope: float, constant: float) -> int:
    # A float32 times a <= 32 pixel coordinate plus another float32 is exact
    # in binary64. Directed conversion therefore reproduces AGX `iter`.
    exact = position * slope + constant
    return float32_bits(round_toward_zero_float32(exact))


def pull_iterator_bits(position: float, slope: float, constant: float) -> int:
    return float32_bits(float32(math.fma(position, slope, constant)))


def neighboring_float32_bits(value: float, radius: int) -> set[int]:
    center = np.float32(value)
    lower = center
    upper = center
    result = {float32_bits(float(center))}
    for _ in range(radius):
        lower = np.nextafter(lower, np.float32(-np.inf))
        upper = np.nextafter(upper, np.float32(np.inf))
        result.add(float32_bits(float(lower)))
        result.add(float32_bits(float(upper)))
    return result


def recover_constant_bits(
    positions: list[float],
    target_bits: list[int],
    slope: float,
    *,
    rounding: Literal["nearest", "toward-zero"],
    search_radius: int = 32,
) -> list[int]:
    if len(positions) != len(target_bits) or not positions:
        raise ValueError("positions and targets must be nonempty and aligned")
    candidates: set[int] = set()
    for position, bits in zip(positions, target_bits, strict=True):
        residual = float32(
            bits_float32(bits) - position * slope
        )
        candidates.update(
            neighboring_float32_bits(residual, search_radius)
        )
    operation = (
        pull_iterator_bits
        if rounding == "nearest"
        else apple_iterator_bits
    )
    return sorted(
        candidate
        for candidate in candidates
        if all(
            operation(position, slope, bits_float32(candidate)) == expected
            for position, expected in zip(
                positions, target_bits, strict=True
            )
        )
    )


@dataclass(frozen=True, slots=True)
class ProbeCase:
    root: Path
    record: JsonObject

    @property
    def name(self) -> str:
        return str(self.record["name"])

    @property
    def width(self) -> int:
        return int(self.record["crop"]["width"])

    @property
    def height(self) -> int:
        return int(self.record["crop"]["height"])

    @property
    def origin_x(self) -> int:
        return int(self.record["crop"]["originX"])

    @property
    def origin_y(self) -> int:
        return int(self.record["crop"]["originY"])

    def surface(self, field: str) -> UIntSurface:
        values = np.fromfile(
            self.root / str(self.record[field]),
            dtype="<u4",
        )
        expected = self.width * self.height * 4
        if values.size != expected:
            raise ValueError(
                f"{self.name} {field} has {values.size} values; "
                f"expected {expected}"
            )
        return values.reshape(self.height, self.width, 4)

    def endpoint(self, axis: Axis, side: Literal["low", "high"]) -> float:
        key = {
            ("x", "low"): "left",
            ("x", "high"): "right",
            ("y", "low"): "top",
            ("y", "high"): "bottom",
        }[(axis, side)]
        return bits_float32(
            int(self.record["sourceEndpointBits"][key], 16)
        )


@dataclass(frozen=True, slots=True)
class AxisModel:
    axis: Axis
    primitive: int
    origin: int
    dimension: int
    slope_bits: int
    constants: dict[int, int]

    @property
    def slope(self) -> float:
        return bits_float32(self.slope_bits)

    def value_bits(self, coordinate: int) -> int:
        global_coordinate = self.origin + coordinate
        tile = global_coordinate // 32
        position = float(global_coordinate % 32) + 0.5
        return apple_iterator_bits(
            position,
            self.slope,
            bits_float32(self.constants[tile]),
        )


@dataclass(frozen=True, slots=True)
class SlopeObservation:
    case: ProbeCase
    axis: Axis
    primitive: int
    kind: Literal["basis", "source"]
    accepted_magnitude_bits: frozenset[int]

    @property
    def other_dimension(self) -> int:
        return self.case.height if self.axis == "x" else self.case.width

    @property
    def numerator(self) -> Fraction:
        if self.kind == "basis":
            return Fraction(self.other_dimension)
        low = self.case.endpoint(self.axis, "low")
        high = self.case.endpoint(self.axis, "high")
        delta = float32(high - low)
        scaled = float32(delta * self.other_dimension)
        return abs(
            float32_bits_fraction(float32_bits(scaled))
        )


def predicted_slope_bits(
    observation: SlopeObservation,
    reciprocal: Fraction,
) -> int:
    return round_fraction_to_float32_bits(
        observation.numerator * reciprocal
    )


def reciprocal_interval(
    observation: SlopeObservation,
) -> tuple[Fraction, Fraction]:
    intervals = [
        float32_rounding_interval(bits)
        for bits in observation.accepted_magnitude_bits
    ]
    return (
        min(lower for lower, _ in intervals)
        / observation.numerator,
        max(upper for _, upper in intervals)
        / observation.numerator,
    )


def fraction_text(value: Fraction) -> str:
    return f"{value.numerator}/{value.denominator}"


def matching_lattice_offsets(
    observations: list[SlopeObservation],
    *,
    precision_bits: int,
    radius: int,
) -> list[int]:
    if not observations:
        raise ValueError("at least one slope observation is required")
    area = observations[0].case.width * observations[0].case.height
    exact = Fraction(1, area)
    return [
        offset
        for offset in range(-radius, radius + 1)
        if all(
            predicted_slope_bits(
                observation,
                quantize_binary_significand(
                    exact,
                    precision_bits,
                    lattice_offset=offset,
                ),
            )
            in observation.accepted_magnitude_bits
            for observation in observations
        )
    ]


def inverse_area_hypothesis_report(
    observations: list[SlopeObservation],
) -> JsonObject:
    """Evaluate explicit reciprocal/setup hypotheses without tolerance."""

    precision_bits = 27
    basis_by_geometry_axis: dict[
        tuple[int, int, Axis],
        SlopeObservation,
    ] = {}
    for observation in observations:
        if observation.kind != "basis":
            continue
        key = (
            observation.case.width,
            observation.case.height,
            observation.axis,
        )
        previous = basis_by_geometry_axis.get(key)
        if previous is None:
            basis_by_geometry_axis[key] = observation
            continue
        common = (
            previous.accepted_magnitude_bits
            & observation.accepted_magnitude_bits
        )
        if not common:
            raise ValueError(
                f"inconsistent basis slopes for geometry {key}"
            )
        basis_by_geometry_axis[key] = SlopeObservation(
            case=previous.case,
            axis=previous.axis,
            primitive=previous.primitive,
            kind=previous.kind,
            accepted_magnitude_bits=frozenset(common),
        )

    basis = list(basis_by_geometry_axis.values())
    source = [
        observation
        for observation in observations
        if observation.kind == "source"
    ]

    def mode_measurement(
        rounding: Literal["down", "nearest-even", "up"],
    ) -> JsonObject:
        def matches(observation: SlopeObservation) -> bool:
            area = observation.case.width * observation.case.height
            reciprocal = quantize_binary_significand_directed(
                Fraction(1, area),
                precision_bits,
                rounding,
            )
            return (
                predicted_slope_bits(observation, reciprocal)
                in observation.accepted_magnitude_bits
            )

        basis_matches = sum(matches(item) for item in basis)
        source_matches = sum(matches(item) for item in source)
        return {
            "basisGradientRecords": len(basis),
            "basisGradientMatches": basis_matches,
            "sourceGradientRecords": len(source),
            "sourceGradientMatches": source_matches,
            "allExact": (
                basis_matches == len(basis)
                and source_matches == len(source)
            ),
        }

    nearest_mismatches: list[JsonObject] = []
    for observation in [*basis, *source]:
        area = observation.case.width * observation.case.height
        reciprocal = quantize_binary_significand(
            Fraction(1, area),
            precision_bits,
        )
        predicted = predicted_slope_bits(observation, reciprocal)
        if predicted not in observation.accepted_magnitude_bits:
            nearest_mismatches.append({
                "case": observation.case.name,
                "width": observation.case.width,
                "height": observation.case.height,
                "kind": observation.kind,
                "axis": observation.axis,
                "primitive": observation.primitive,
                "predictedMagnitudeBits": f"0x{predicted:08x}",
                "acceptedMagnitudeBits": [
                    f"0x{bits:08x}"
                    for bits in sorted(
                        observation.accepted_magnitude_bits
                    )
                ],
            })

    by_geometry: dict[
        tuple[int, int],
        list[SlopeObservation],
    ] = {}
    for observation in [*basis, *source]:
        key = (observation.case.width, observation.case.height)
        by_geometry.setdefault(key, []).append(observation)

    geometries: list[JsonObject] = []
    for (width, height), records in sorted(by_geometry.items()):
        area = width * height
        exact = Fraction(1, area)
        exponent = floor_binary_exponent(exact)
        step = power_of_two(exponent - precision_bits + 1)
        nearest = quantize_binary_significand(
            exact,
            precision_bits,
        )
        lower = max(
            reciprocal_interval(record)[0]
            for record in records
        )
        upper = min(
            reciprocal_interval(record)[1]
            for record in records
        )
        offsets = matching_lattice_offsets(
            records,
            precision_bits=precision_bits,
            radius=64,
        )
        geometries.append({
            "width": width,
            "height": height,
            "area": area,
            "reciprocalBinaryExponent": exponent,
            "latticeStepBinaryExponent":
                exponent - precision_bits + 1,
            "nearestLatticeValueHex": float(nearest).hex(),
            "exactMinusNearestInLatticeSteps": float(
                (exact - nearest) / step
            ),
            "feasibleContinuousInterval": {
                "lowerExclusiveOrTieEven": fraction_text(lower),
                "upperExclusiveOrTieEven": fraction_text(upper),
                "lowerHexApproximation": float(lower).hex(),
                "upperHexApproximation": float(upper).hex(),
                "widthInLatticeSteps": float(
                    (upper - lower) / step
                ),
                "containsExactReciprocal": lower <= exact <= upper,
                "containsNearestLatticeValue":
                    lower <= nearest <= upper,
            },
            "matchingLatticeOffsetsFromNearest": offsets,
            "matchingLatticeOffsetSearchRadius": 64,
        })

    return {
        "status": "candidate-not-portable-law",
        "triangleSetupExpression": (
            "A = roundFloat32(roundFloat32("
            "(high-low)*oppositeEdge) * inverseArea)"
        ),
        "inverseAreaCandidate": (
            "1/(width*height) quantized to a 27-significant-bit "
            "binary lattice"
        ),
        "precisionBits": precision_bits,
        "roundingModeMeasurements": {
            "down": mode_measurement("down"),
            "nearestEven": mode_measurement("nearest-even"),
            "up": mode_measurement("up"),
        },
        "nearestEvenMismatches": nearest_mismatches,
        "geometries": geometries,
        "fullyDetermined": False,
    }


def load_probe_cases(root: Path) -> list[ProbeCase]:
    manifest = json.loads(
        (root / "manifest.json").read_text(encoding="utf-8")
    )
    if manifest.get("schemaVersion") not in {
        5,
        6,
        7,
        8,
        9,
        10,
        11,
        12,
        13,
        14,
        15,
    }:
        raise ValueError(
            "raster probe schema 5 through 15 is required"
        )
    return [
        ProbeCase(root=root, record=record)
        for record in manifest["cases"]
    ]


def primitive_mask(case: ProbeCase) -> NDArray[np.uint32]:
    return case.surface("barycentricFile")[..., 3]


def axis_records(
    surface: UIntSurface,
    mask: NDArray[np.uint32],
    *,
    axis: Axis,
    primitive: int,
    channel: int,
) -> dict[int, int]:
    dimension = surface.shape[1] if axis == "x" else surface.shape[0]
    result: dict[int, int] = {}
    for coordinate in range(dimension):
        values = (
            surface[:, coordinate, channel][
                mask[:, coordinate] == primitive
            ]
            if axis == "x"
            else surface[coordinate, :, channel][
                mask[coordinate, :] == primitive
            ]
        )
        if values.size == 0:
            continue
        if np.any(values != values[0]):
            raise ValueError(
                f"{axis} channel {channel} is not separable for "
                f"primitive {primitive} at {coordinate}"
            )
        result[coordinate] = int(values[0])
    return result


def tile_coordinates(
    records: dict[int, int],
    *,
    origin: int,
) -> dict[int, list[int]]:
    result: dict[int, list[int]] = {}
    for coordinate in records:
        tile = (origin + coordinate) // 32
        result.setdefault(tile, []).append(coordinate)
    return result


def source_slope_candidate_groups(
    case: ProbeCase,
    axis: Axis,
) -> dict[int, list[str]]:
    dimension = case.width if axis == "x" else case.height
    other_dimension = case.height if axis == "x" else case.width
    area = case.width * case.height
    reciprocal = float32(1.0 / dimension)
    area_reciprocal = float32(1.0 / area)
    low = case.endpoint(axis, "low")
    high = case.endpoint(axis, "high")
    delta = float32(high - low)
    triangle_numerator = float32(delta * other_dimension)
    candidates = {
        "rounded-delta/divide-dimension":
            float32(delta / dimension),
        "rounded-delta*rounded-reciprocal-dimension":
            float32(delta * reciprocal),
        "rounded-scaled-endpoints-difference":
            float32(
                float32(high * reciprocal)
                - float32(low * reciprocal)
            ),
        "rounded-triangle-numerator/divide-area":
            float32(triangle_numerator / area),
        "rounded-triangle-numerator*rounded-reciprocal-area":
            float32(triangle_numerator * area_reciprocal),
    }
    grouped: dict[int, list[str]] = {}
    for name, slope in candidates.items():
        grouped.setdefault(float32_bits(slope), []).append(name)
    return grouped


def raster_primitive_ids(size: int) -> UIntSurface:
    """Return Apple's observed primitive ID for the two-triangle quad."""

    y, x = np.indices((size, size))
    return (x + y < size - 1).astype(np.uint32)


def compress_axis_trace(active: UIntSurface) -> UIntSurface:
    """Losslessly separate a square RGBA interpolant trace by raster axis."""

    if (
        active.ndim != 3
        or active.shape[0] != active.shape[1]
        or active.shape[2] != 4
    ):
        raise ValueError(
            "active interpolant trace must have shape (size, size, 4)"
        )
    size = active.shape[0]
    primitive = raster_primitive_ids(size)
    table = np.zeros(
        (AXIS_TRACE_ROWS, size, AXIS_TRACE_COMPONENTS),
        dtype=np.uint32,
    )
    for axis, channel in (
        ("x", 0),
        ("y", 1),
        ("x", 2),
        ("y", 3),
    ):
        for primitive_id in (0, 1):
            for coordinate in range(size):
                selected = (
                    active[:, coordinate, channel][
                        primitive[:, coordinate] == primitive_id
                    ]
                    if axis == "x"
                    else active[coordinate, :, channel][
                        primitive[coordinate, :] == primitive_id
                    ]
                )
                if selected.size == 0:
                    continue
                if np.any(selected != selected[0]):
                    raise ValueError(
                        f"channel {channel} {axis} is not separable at "
                        f"{coordinate} for primitive {primitive_id}"
                    )
                table[primitive_id, coordinate, channel] = selected[0]

    # Primitive 1 never reaches the final x or y coordinate. Canonicalize
    # the unreachable texel so the artifact is deterministic.
    table[1, -1, :] = table[0, -1, :]
    reconstructed = reconstruct_axis_trace(table)
    if np.any(reconstructed != active):
        raise ValueError("interpolant trace is not losslessly separable")
    return table


def reconstruct_axis_trace(table: UIntSurface) -> UIntSurface:
    if (
        table.ndim != 3
        or table.shape[0] != AXIS_TRACE_ROWS
        or table.shape[1] == 0
        or table.shape[2] != AXIS_TRACE_COMPONENTS
    ):
        raise ValueError(
            "axis interpolant table must have shape (2, size, 4)"
        )
    size = table.shape[1]
    primitive = raster_primitive_ids(size)
    y, x = np.indices((size, size))
    reconstructed = np.empty((size, size, 4), dtype=np.uint32)
    reconstructed[..., 0] = table[primitive, x, 0]
    reconstructed[..., 1] = table[primitive, y, 1]
    reconstructed[..., 2] = table[primitive, x, 2]
    reconstructed[..., 3] = table[primitive, y, 3]
    return reconstructed


def load_live_interpolant_trace(path: Path) -> UIntSurface:
    values = np.fromfile(path, dtype="<u4")
    expected = LIVE_TRACE_WIDTH * LIVE_TRACE_HEIGHT * 4
    if values.size != expected:
        raise ValueError(
            f"{path} has {values.size} components; expected {expected}"
        )
    pixels = values.reshape(LIVE_TRACE_HEIGHT, LIVE_TRACE_WIDTH, 4)
    return pixels[
        LIVE_ACTIVE_START : LIVE_ACTIVE_START + LIVE_ACTIVE_SIZE,
        LIVE_ACTIVE_START : LIVE_ACTIVE_START + LIVE_ACTIVE_SIZE,
    ]


def load_axis_trace_table(
    path: Path,
    *,
    active_size: int = LIVE_ACTIVE_SIZE,
) -> UIntSurface:
    values = np.fromfile(path, dtype="<u4")
    expected = AXIS_TRACE_ROWS * active_size * AXIS_TRACE_COMPONENTS
    if values.size != expected:
        raise ValueError(
            f"{path} has {values.size} components; expected {expected}"
        )
    return values.reshape(
        AXIS_TRACE_ROWS,
        active_size,
        AXIS_TRACE_COMPONENTS,
    )


def write_axis_trace_artifact(
    source: Path,
    output: Path,
    *,
    manifest: Path | None = None,
) -> JsonObject:
    active = load_live_interpolant_trace(source)
    table = compress_axis_trace(active)
    output.parent.mkdir(parents=True, exist_ok=True)
    table.astype("<u4", copy=False).tofile(output)
    output_bytes = output.stat().st_size
    source_bytes = source.stat().st_size
    report: JsonObject = {
        "liquidGlassInterpolantAxisTableSchemaVersion": 1,
        "source": {
            "path": str(source),
            "bytes": source_bytes,
            "sha256": sha256_file(source),
        },
        "axisTable": {
            "path": str(output),
            "format": "RGBA32UI",
            "width": LIVE_ACTIVE_SIZE,
            "height": AXIS_TRACE_ROWS,
            "bytes": output_bytes,
            "sha256": sha256_file(output),
            "rowMapping": [
                "primitive-0",
                "primitive-1",
            ],
            "componentMapping": [
                "sdf-x",
                "sdf-y",
                "source-x",
                "source-y",
            ],
        },
        "measurement": {
            "activeComponents": int(active.size),
            "mismatchedComponents": 0,
            "exact": True,
            "compressionRatio": source_bytes / output_bytes,
            "bytesRemoved": source_bytes - output_bytes,
            "byteReductionPercent": (
                100.0 * (source_bytes - output_bytes) / source_bytes
            ),
        },
    }
    if manifest is not None:
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    return report


def recover_live_coefficient_table(
    axis_table: UIntSurface,
    *,
    source_slope_bits: int,
) -> UIntSurface:
    if axis_table.shape != (
        AXIS_TRACE_ROWS,
        LIVE_ACTIVE_SIZE,
        AXIS_TRACE_COMPONENTS,
    ):
        raise ValueError(
            "live axis table must have shape (2, 800, 4)"
        )
    slopes = (
        1.0,
        -1.0,
        bits_float32(source_slope_bits),
        bits_float32(source_slope_bits),
    )
    coefficients = np.empty(
        (2, LIVE_TILE_COUNT, 4),
        dtype=np.uint32,
    )
    for primitive in (0, 1):
        for channel, slope in enumerate(slopes):
            for tile_offset in range(LIVE_TILE_COUNT):
                tile = LIVE_TILE_START + tile_offset
                coordinates = [
                    coordinate
                    for coordinate in range(LIVE_ACTIVE_SIZE)
                    if (
                        (LIVE_ACTIVE_START + coordinate) // 32
                        == tile
                        and not (
                            primitive == 1
                            and coordinate == LIVE_ACTIVE_SIZE - 1
                        )
                    )
                ]
                positions = [
                    float(
                        (LIVE_ACTIVE_START + coordinate) % 32
                    )
                    + 0.5
                    for coordinate in coordinates
                ]
                targets = [
                    int(axis_table[primitive, coordinate, channel])
                    for coordinate in coordinates
                ]
                candidates = recover_constant_bits(
                    positions,
                    targets,
                    slope,
                    rounding="toward-zero",
                    search_radius=64,
                )
                if not candidates:
                    raise ValueError(
                        f"no coefficient for primitive {primitive}, "
                        f"channel {channel}, tile {tile}"
                    )
                residuals = [
                    bits_float32(bits) - position * slope
                    for position, bits in zip(
                        positions, targets, strict=True
                    )
                ]
                nominal = statistics.median(residuals)
                coefficients[
                    primitive,
                    tile_offset,
                    channel,
                ] = min(
                    candidates,
                    key=lambda bits: (
                        abs(bits_float32(bits) - nominal),
                        bits,
                    ),
                )

    reconstructed = reconstruct_live_coefficient_trace(
        coefficients,
        source_slope_bits=source_slope_bits,
    )
    primitive = raster_primitive_ids(LIVE_ACTIVE_SIZE)
    y, x = np.indices((LIVE_ACTIVE_SIZE, LIVE_ACTIVE_SIZE))
    expected = np.empty_like(reconstructed)
    expected[..., 0] = axis_table[primitive, x, 0]
    expected[..., 1] = axis_table[primitive, y, 1]
    expected[..., 2] = axis_table[primitive, x, 2]
    expected[..., 3] = axis_table[primitive, y, 3]
    if np.any(reconstructed != expected):
        raise ValueError("recovered coefficient table is not exact")
    return coefficients


def reconstruct_live_coefficient_trace(
    coefficients: UIntSurface,
    *,
    source_slope_bits: int,
) -> UIntSurface:
    if coefficients.shape != (2, LIVE_TILE_COUNT, 4):
        raise ValueError(
            f"coefficient table must have shape (2, {LIVE_TILE_COUNT}, 4)"
        )
    slopes = (
        1.0,
        -1.0,
        bits_float32(source_slope_bits),
        bits_float32(source_slope_bits),
    )
    primitive = raster_primitive_ids(LIVE_ACTIVE_SIZE)
    y, x = np.indices((LIVE_ACTIVE_SIZE, LIVE_ACTIVE_SIZE))
    result = np.empty(
        (LIVE_ACTIVE_SIZE, LIVE_ACTIVE_SIZE, 4),
        dtype=np.uint32,
    )
    for channel, coordinates in (
        (0, x),
        (1, y),
        (2, x),
        (3, y),
    ):
        global_coordinates = LIVE_ACTIVE_START + coordinates
        tile_offsets = global_coordinates // 32 - LIVE_TILE_START
        positions = global_coordinates % 32 + 0.5
        for primitive_id in (0, 1):
            selected = primitive == primitive_id
            position_values = positions[selected]
            tile_values = tile_offsets[selected]
            constants = coefficients[
                primitive_id,
                tile_values,
                channel,
            ]
            result[..., channel][selected] = [
                apple_iterator_bits(
                    float(position),
                    slopes[channel],
                    bits_float32(int(constant)),
                )
                for position, constant in zip(
                    position_values, constants, strict=True
                )
            ]
    return result


def load_live_coefficient_table(path: Path) -> UIntSurface:
    values = np.fromfile(path, dtype="<u4")
    expected = 2 * LIVE_TILE_COUNT * 4
    if values.size != expected:
        raise ValueError(
            f"{path} has {values.size} components; expected {expected}"
        )
    return values.reshape(2, LIVE_TILE_COUNT, 4)


def write_live_coefficient_artifact(
    axis_source: Path,
    output: Path,
    *,
    source_slope_bits: int,
    manifest: Path | None = None,
) -> JsonObject:
    axis_table = load_axis_trace_table(axis_source)
    coefficients = recover_live_coefficient_table(
        axis_table,
        source_slope_bits=source_slope_bits,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    coefficients.astype("<u4", copy=False).tofile(output)
    output_bytes = output.stat().st_size
    axis_bytes = axis_source.stat().st_size
    full_bytes = LIVE_TRACE_WIDTH * LIVE_TRACE_HEIGHT * 4 * 4
    report: JsonObject = {
        "liquidGlassInterpolantCoefficientTableSchemaVersion": 1,
        "source": {
            "path": str(axis_source),
            "bytes": axis_bytes,
            "sha256": sha256_file(axis_source),
        },
        "coefficientTable": {
            "path": str(output),
            "format": "RGBA32UI",
            "width": LIVE_TILE_COUNT,
            "height": 2,
            "bytes": output_bytes,
            "sha256": sha256_file(output),
            "tileStart": LIVE_TILE_START,
            "sourceSlopeBits":
                f"0x{source_slope_bits:08x}",
            "slopeBits": [
                "0x3f800000",
                "0xbf800000",
                f"0x{source_slope_bits:08x}",
                f"0x{source_slope_bits:08x}",
            ],
        },
        "measurement": {
            "activeComponents":
                LIVE_ACTIVE_SIZE * LIVE_ACTIVE_SIZE * 4,
            "mismatchedComponents": 0,
            "exact": True,
            "axisCompressionRatio": axis_bytes / output_bytes,
            "fullCompressionRatio": full_bytes / output_bytes,
            "fullByteReductionPercent": (
                100.0 * (full_bytes - output_bytes) / full_bytes
            ),
        },
    }
    if manifest is not None:
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    return report


def build_live_correction_surface(
    axis_table: UIntSurface,
    *,
    source_low_bits: int,
    source_slope_bits: int,
) -> NDArray[np.int8]:
    if axis_table.shape != (
        AXIS_TRACE_ROWS,
        LIVE_ACTIVE_SIZE,
        AXIS_TRACE_COMPONENTS,
    ):
        raise ValueError(
            "live axis table must have shape (2, 800, 4)"
        )
    coordinate = np.arange(LIVE_ACTIVE_SIZE, dtype=np.float32)
    source_low = bits_float32(source_low_bits)
    source_slope = bits_float32(source_slope_bits)
    source = np.array(
        [
            float32_bits(
                float32(
                    math.fma(
                        float(value) + 0.5,
                        source_slope,
                        source_low,
                    )
                )
            )
            for value in range(LIVE_ACTIVE_SIZE)
        ],
        dtype=np.uint32,
    )
    baseline = (
        (coordinate - np.float32(399.5)).view(np.uint32),
        (np.float32(399.5) - coordinate).view(np.uint32),
        source,
        source,
    )
    primitive = raster_primitive_ids(LIVE_ACTIVE_SIZE)
    y, x = np.indices((LIVE_ACTIVE_SIZE, LIVE_ACTIVE_SIZE))
    corrections = np.empty(
        (LIVE_ACTIVE_SIZE, LIVE_ACTIVE_SIZE, 4),
        dtype=np.int8,
    )
    reconstructed = np.empty_like(corrections, dtype=np.uint32)
    for channel, coordinates in (
        (0, x),
        (1, y),
        (2, x),
        (3, y),
    ):
        expected = axis_table[
            primitive,
            coordinates,
            channel,
        ]
        base = baseline[channel][coordinates]
        delta = expected.astype(np.int64) - base.astype(np.int64)
        if delta.min() < -128 or delta.max() > 127:
            raise ValueError(
                f"channel {channel} correction range "
                f"{delta.min()}...{delta.max()} does not fit int8"
            )
        corrections[..., channel] = delta.astype(np.int8)
        reconstructed[..., channel] = (
            base.astype(np.int64) + delta
        ).astype(np.uint32)
        if np.any(reconstructed[..., channel] != expected):
            raise ValueError(
                f"channel {channel} correction surface is not exact"
            )
    return corrections


def load_live_correction_surface(
    path: Path,
) -> NDArray[np.int8]:
    values = np.fromfile(path, dtype=np.int8)
    expected = LIVE_ACTIVE_SIZE * LIVE_ACTIVE_SIZE * 4
    if values.size != expected:
        raise ValueError(
            f"{path} has {values.size} components; expected {expected}"
        )
    return values.reshape(LIVE_ACTIVE_SIZE, LIVE_ACTIVE_SIZE, 4)


def write_live_correction_artifact(
    axis_source: Path,
    output: Path,
    *,
    source_low_bits: int,
    source_slope_bits: int,
    manifest: Path | None = None,
) -> JsonObject:
    axis_table = load_axis_trace_table(axis_source)
    corrections = build_live_correction_surface(
        axis_table,
        source_low_bits=source_low_bits,
        source_slope_bits=source_slope_bits,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    corrections.tofile(output)
    output_bytes = output.stat().st_size
    full_bytes = LIVE_TRACE_WIDTH * LIVE_TRACE_HEIGHT * 4 * 4
    ranges = [
        {
            "minimum": int(corrections[..., channel].min()),
            "maximum": int(corrections[..., channel].max()),
        }
        for channel in range(4)
    ]
    report: JsonObject = {
        "liquidGlassInterpolantCorrectionSurfaceSchemaVersion": 1,
        "source": {
            "path": str(axis_source),
            "bytes": axis_source.stat().st_size,
            "sha256": sha256_file(axis_source),
        },
        "correctionSurface": {
            "path": str(output),
            "format": "RGBA8I",
            "width": LIVE_ACTIVE_SIZE,
            "height": LIVE_ACTIVE_SIZE,
            "bytes": output_bytes,
            "sha256": sha256_file(output),
            "sourceLowBits": f"0x{source_low_bits:08x}",
            "sourceSlopeBits": f"0x{source_slope_bits:08x}",
            "componentRanges": ranges,
        },
        "measurement": {
            "activeComponents": int(corrections.size),
            "mismatchedComponents": 0,
            "exact": True,
            "fullCompressionRatio": full_bytes / output_bytes,
            "fullByteReductionPercent": (
                100.0 * (full_bytes - output_bytes) / full_bytes
            ),
        },
    }
    if manifest is not None:
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    return report


def recover_axis_model(
    case: ProbeCase,
    *,
    axis: Axis,
    primitive: int,
    kind: Literal["basis", "source"],
) -> tuple[AxisModel, JsonObject]:
    mask = primitive_mask(case)
    dimension = case.width if axis == "x" else case.height
    origin = case.origin_x if axis == "x" else case.origin_y

    if kind == "basis":
        pull_field = (
            "basisPullNoPerspectiveXFile"
            if axis == "x"
            else "basisPullNoPerspectiveYFile"
        )
        pull_channels = (0, 1, 2, 3)
        offsets = OFFSETS_FOUR
        standard_field = "basisNoPerspectiveFile"
        standard_channel = 0 if axis == "x" else 2
        basis_slope = float32(
            (-1.0 if primitive == 0 else 1.0) / dimension
        )
        center_bits = float32_bits(basis_slope)
        slope_groups = {
            candidate: [
                "rounded-signed-reciprocal-dimension"
                if candidate == center_bits
                else (
                    "measured-neighbor-of-rounded-signed-reciprocal"
                    f"[{int(candidate) - int(center_bits):+d} bits]"
                )
            ]
            for candidate in neighboring_float32_bits(
                basis_slope,
                radius=8,
            )
        }
    else:
        pull_field = "sourcePullNoPerspectiveFile"
        pull_channels = (0, 1) if axis == "x" else (2, 3)
        offsets = OFFSETS_PAIR
        standard_field = "varyingFile"
        standard_channel = 2 if axis == "x" else 3
        slope_groups = source_slope_candidate_groups(case, axis)

    pull = case.surface(pull_field)
    standard = case.surface(standard_field)
    standard_records = axis_records(
        standard,
        mask,
        axis=axis,
        primitive=primitive,
        channel=standard_channel,
    )
    coordinates_by_tile = tile_coordinates(
        standard_records,
        origin=origin,
    )

    def recover_constants(slope: float) -> dict[int, int] | None:
        constants: dict[int, int] = {}
        for tile, coordinates in coordinates_by_tile.items():
            positions: list[float] = []
            targets: list[int] = []
            for coordinate in coordinates:
                values = (
                    pull[:, coordinate, :][
                        mask[:, coordinate] == primitive
                    ][0]
                    if axis == "x"
                    else pull[coordinate, :, :][
                        mask[coordinate, :] == primitive
                    ][0]
                )
                tile_position = float((origin + coordinate) % 32)
                for channel, offset in zip(
                    pull_channels, offsets, strict=True
                ):
                    positions.append(tile_position + offset)
                    targets.append(int(values[channel]))
            candidates = recover_constant_bits(
                positions,
                targets,
                slope,
                rounding="nearest",
            )
            if kind == "source" and len(candidates) > 1:
                candidates = [
                    candidate
                    for candidate in candidates
                    if all(
                        apple_iterator_bits(
                            float((origin + coordinate) % 32) + 0.5,
                            slope,
                            bits_float32(candidate),
                        )
                        == standard_records[coordinate]
                        for coordinate in coordinates
                    )
                ]
            if len(candidates) != 1:
                return None
            constants[tile] = candidates[0]
        return constants

    accepted = [
        (
            slope_bits,
            formulas,
            constants,
        )
        for slope_bits, formulas in slope_groups.items()
        if (
            constants := recover_constants(bits_float32(slope_bits))
        ) is not None
    ]
    if not accepted:
        raise ValueError(
            f"{case.name} {kind} {axis} primitive {primitive} has "
            "no exact slope candidate"
        )
    accepted.sort(key=lambda item: item[0])
    slope_bits, slope_formulas, constants = accepted[0]
    slope = bits_float32(slope_bits)

    model = AxisModel(
        axis=axis,
        primitive=primitive,
        origin=origin,
        dimension=dimension,
        slope_bits=float32_bits(slope),
        constants=constants,
    )
    mismatches = sum(
        model.value_bits(coordinate) != expected
        for coordinate, expected in standard_records.items()
    )
    weighted_mismatches = 0
    observed = 0
    for coordinate, expected in standard_records.items():
        count = int(np.count_nonzero(
            mask[:, coordinate] == primitive
            if axis == "x"
            else mask[coordinate, :] == primitive
        ))
        observed += count
        if model.value_bits(coordinate) != expected:
            weighted_mismatches += count
    constant_bytes = b"".join(
        struct.pack("<II", tile, bits)
        for tile, bits in sorted(constants.items())
    )
    return model, {
        "axis": axis,
        "primitive": primitive,
        "kind": kind,
        "slopeBits": f"0x{model.slope_bits:08x}",
        "slopeCandidateFormulas": slope_formulas,
        "slopeCandidateFormulaCount": len(slope_formulas),
        "acceptedSlopeCandidates": [
            {
                "slopeBits": f"0x{candidate_bits:08x}",
                "formulas": formulas,
            }
            for candidate_bits, formulas, _ in accepted
        ],
        "slopeFullyDetermined": len(accepted) == 1,
        "tileCount": len(constants),
        "tileCoefficientSha256": hashlib.sha256(
            constant_bytes
        ).hexdigest(),
        "observedAxisCoordinates": len(standard_records),
        "mismatchedAxisCoordinates": mismatches,
        "observedFloatValues": observed,
        "mismatchedFloatValues": weighted_mismatches,
        "exact": weighted_mismatches == 0,
    }


def analyze_probe(root: Path) -> JsonObject:
    cases: list[JsonObject] = []
    observations: list[SlopeObservation] = []
    total_values = 0
    total_mismatches = 0
    ambiguous_slopes = 0
    for case in load_probe_cases(root):
        models: list[JsonObject] = []
        for kind in ("basis", "source"):
            for axis in ("x", "y"):
                for primitive in (0, 1):
                    _, result = recover_axis_model(
                        case,
                        axis=axis,
                        primitive=primitive,
                        kind=kind,
                    )
                    models.append(result)
                    observations.append(SlopeObservation(
                        case=case,
                        axis=axis,
                        primitive=primitive,
                        kind=kind,
                        accepted_magnitude_bits=frozenset(
                            int(candidate["slopeBits"], 16)
                            & 0x7fffffff
                            for candidate in result[
                                "acceptedSlopeCandidates"
                            ]
                        ),
                    ))
                    total_values += int(result["observedFloatValues"])
                    total_mismatches += int(
                        result["mismatchedFloatValues"]
                    )
                    ambiguous_slopes += not bool(
                        result["slopeFullyDetermined"]
                    )
        cases.append({
            "name": case.name,
            "width": case.width,
            "height": case.height,
            "origin": [case.origin_x, case.origin_y],
            "models": models,
            "exact": all(bool(model["exact"]) for model in models),
        })
    return {
        "liquidGlassRasterInterpolantAnalysisSchemaVersion": 2,
        "probe": str(root),
        "rule": (
            "roundTowardZero(A * pixelWithin32x32Tile + C)"
        ),
        "coefficientSetup": (
            "A candidates are selected from explicit float32 "
            "triangle-setup expressions and accepted only when their "
            "exact bit patterns reconstruct every pull and center sample"
        ),
        "inverseAreaHypothesis":
            inverse_area_hypothesis_report(observations),
        "cases": cases,
        "measurement": {
            "observedFloatValues": total_values,
            "mismatchedFloatValues": total_mismatches,
            "exact": total_mismatches == 0,
            "ambiguousSlopeModels": ambiguous_slopes,
            "portableCoefficientSetupFullyDetermined": False,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Recover and bit-gate Apple raster interpolation."
    )
    parser.add_argument("probe", type=Path)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    report = analyze_probe(arguments.probe)
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if arguments.output is None:
        print(rendered, end="")
    else:
        arguments.output.write_text(rendered, encoding="utf-8")
    return 0 if report["measurement"]["exact"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
