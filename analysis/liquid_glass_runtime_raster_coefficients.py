#!/usr/bin/env python3
"""Generate Apple/AGX raster coefficients from runtime quad inputs.

The coefficient arithmetic is the prospectively validated schema-15 model in
``lg-test``.  This module adapts that model to the real vertex buffers retained
by the Liquid Glass geometry captures and verifies the generated coordinates
against Apple's full per-pixel interpolant trace.  No captured coordinate or
coefficient table is read by the predictor.
"""

import argparse
import hashlib
import json
import math
import struct
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray


type JsonObject = dict[str, Any]
type UInt32Array = NDArray[np.uint32]
type AnchorPolicy = tuple[tuple[bool, bool], tuple[bool, bool]]

ROOT = Path(__file__).resolve().parent.parent
LG_ANALYSIS = ROOT / "lg-test" / "Analysis"
if str(LG_ANALYSIS) not in sys.path:
    sys.path.insert(0, str(LG_ANALYSIS))

import raster_tile_coefficient_model as coefficient_base  # noqa: E402
import raster_tile_coefficient_model_v3 as coefficients  # noqa: E402
import raster_tile_iterator_model as iterator  # noqa: E402
import raster_tile_selector_model as arithmetic  # noqa: E402
import raster_tile_selector_model_v4 as composite  # noqa: E402

from liquid_glass_geometry_transfer import (  # noqa: E402
    _glass_snapshots,
    _vertices,
)


TRACE_NAME = "carenderer-live-tree-glass-interpolant-numeric-trace-rgba32ui.raw"
TILE_SIZE = 32
SUBPIXEL_BITS = 8
SUBPIXEL_SCALE = 1 << SUBPIXEL_BITS
CHANNEL_AXES = (0, 1, 0, 1)
VERTEX_COMPONENTS = (4, 5, 6, 7)


@dataclass(frozen=True, slots=True)
class RasterCase:
    """Post-transform axis-aligned quad supplied to raster setup."""

    name: str
    originX: int | float
    originY: int | float
    width: int | float
    height: int | float
    originXFixed: int
    originYFixed: int
    widthFixed: int
    heightFixed: int


@dataclass(frozen=True, slots=True)
class Endpoint:
    """Binary32 varying values at the low and high edge of one axis."""

    name: str
    lowBits: int
    highBits: int


@dataclass(frozen=True, slots=True)
class TileSample:
    """The input fields used by the fixed-function coefficient model."""

    axis: int
    primitive: int
    tile: int


@dataclass(frozen=True, slots=True)
class RuntimeQuad:
    case: RasterCase
    endpoints: tuple[Endpoint, Endpoint, Endpoint, Endpoint]
    channelAxes: tuple[int, int, int, int] = CHANNEL_AXES
    ascendingDiagonal: bool = False


def float32_bits(value: float) -> int:
    return struct.unpack("<I", struct.pack("<f", value))[0]


def _fixed_to_number(value: int) -> int | float:
    quotient, remainder = divmod(value, SUBPIXEL_SCALE)
    return quotient if remainder == 0 else value / SUBPIXEL_SCALE


def _subpixel_fixed(value: float) -> int:
    """Snap one binary32 raster coordinate to Apple's measured 1/256 grid."""

    exact = arithmetic.float32_bits_fraction(float32_bits(value))
    scaled = exact * SUBPIXEL_SCALE
    # The exhaustive Apple sweep selected ties toward positive infinity.
    return (scaled.numerator * 2 + scaled.denominator) // (2 * scaled.denominator)


def _case_from_fixed(
    *,
    name: str,
    left: int,
    bottom: int,
    right: int,
    top: int,
) -> RasterCase:
    if right <= left or top <= bottom:
        raise ValueError(f"{name} main raster extent is empty")
    return RasterCase(
        name=name,
        originX=_fixed_to_number(left),
        originY=_fixed_to_number(bottom),
        width=_fixed_to_number(right - left),
        height=_fixed_to_number(top - bottom),
        originXFixed=left,
        originYFixed=bottom,
        widthFixed=right - left,
        heightFixed=top - bottom,
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def runtime_quad(capture: Path) -> RuntimeQuad:
    """Decode the main two-triangle quad without reading its raster trace."""

    runtime = json.loads((capture / "runtime.json").read_text(encoding="utf-8"))
    snapshots = _glass_snapshots(runtime, stage="vertex", index=1)
    if len(snapshots) != 2:
        raise ValueError(
            f"{capture} has {len(snapshots)} glass vertex snapshots; expected 2"
        )
    vertices = _vertices(snapshots[0], 6)
    return runtime_quad_from_vertices(vertices, name=capture.name)


def runtime_quad_from_vertices(
    vertices: Sequence[Sequence[float]],
    *,
    name: str,
    mvp_payload: bytes | None = None,
    viewport: tuple[float, float, float, float] = (0.0, 0.0, 1024.0, 1024.0),
) -> RuntimeQuad:
    """Decode a two-triangle quad after Apple's measured raster-grid snap."""
    if len(vertices) != 6 or any(len(vertex) < 8 for vertex in vertices):
        raise ValueError(f"{name} does not contain six complete quad vertices")
    raster_positions = (
        _mvp_viewport_positions(vertices, mvp_payload, viewport=viewport)
        if mvp_payload is not None
        else np.asarray(vertices, dtype=np.float32)[:, :2]
    )
    fixed_positions = [
        (_subpixel_fixed(position[0]), _subpixel_fixed(position[1]))
        for position in raster_positions
    ]
    left = min(position[0] for position in fixed_positions)
    right = max(position[0] for position in fixed_positions)
    bottom = min(position[1] for position in fixed_positions)
    top = max(position[1] for position in fixed_positions)
    case = _case_from_fixed(
        name=name,
        left=left,
        bottom=bottom,
        right=right,
        top=top,
    )

    endpoints: list[Endpoint] = []
    channel_axes: list[int] = []
    for channel, (preferred_axis, component) in enumerate(
        zip(CHANNEL_AXES, VERTEX_COMPONENTS, strict=True)
    ):
        valid_axes: list[tuple[int, set[float], set[float]]] = []
        for axis in (preferred_axis, 1 - preferred_axis):
            low_position = left if axis == 0 else bottom
            high_position = right if axis == 0 else top
            low_values = {
                vertex[component]
                for vertex, position in zip(vertices, fixed_positions, strict=True)
                if position[axis] == low_position
            }
            high_values = {
                vertex[component]
                for vertex, position in zip(vertices, fixed_positions, strict=True)
                if position[axis] == high_position
            }
            if len(low_values) == 1 and len(high_values) == 1:
                valid_axes.append((axis, low_values, high_values))
        if not valid_axes:
            raise ValueError(
                f"{name} channel {channel} is not axis-separable at its edges"
            )
        axis, low_values, high_values = valid_axes[0]
        channel_axes.append(axis)
        endpoints.append(
            Endpoint(
                name=("sdf-x", "sdf-y", "source-x", "source-y")[channel],
                lowBits=float32_bits(low_values.pop()),
                highBits=float32_bits(high_values.pop()),
            )
        )
    first_triangle = set(fixed_positions[:3])
    second_triangle = set(fixed_positions[3:])
    shared_diagonal = first_triangle & second_triangle
    if len(shared_diagonal) != 2:
        raise ValueError(f"{name} triangles do not share one complete diagonal")
    left_diagonal = min(shared_diagonal, key=lambda position: position[0])
    right_diagonal = max(shared_diagonal, key=lambda position: position[0])
    if left_diagonal[0] == right_diagonal[0]:
        raise ValueError(f"{name} shared diagonal has no horizontal extent")
    ascending_diagonal = right_diagonal[1] > left_diagonal[1]
    return RuntimeQuad(
        case=case,
        endpoints=tuple(endpoints),  # type: ignore[arg-type]
        channelAxes=tuple(channel_axes),  # type: ignore[arg-type]
        ascendingDiagonal=ascending_diagonal,
    )


def _float32_fma(left: float, right: float, addend: float) -> np.float32:
    """Evaluate one binary32 fused multiply-add from binary32 inputs."""

    return np.float32(
        float(np.float32(left)) * float(np.float32(right))
        + float(np.float32(addend))
    )


def _mvp_viewport_positions(
    vertices: Sequence[Sequence[float]],
    mvp_payload: bytes,
    *,
    viewport: tuple[float, float, float, float],
) -> NDArray[np.float32]:
    """Reproduce the vertex and viewport transforms before raster-grid snap."""

    if len(mvp_payload) < 64:
        raise ValueError("MVP payload is shorter than sixteen float32 values")
    origin_x, origin_y, width, height = map(np.float32, viewport)
    if width <= 0.0 or height <= 0.0:
        raise ValueError("viewport extent must be positive")
    matrix = np.frombuffer(
        mvp_payload,
        dtype="<f4",
        count=16,
    ).reshape((4, 4), order="F")
    source = np.asarray(vertices, dtype=np.float32)
    positions = np.empty((len(source), 2), dtype=np.float32)
    half_width = np.float32(width * np.float32(0.5))
    half_height = np.float32(height * np.float32(0.5))
    center_x = np.float32(origin_x + half_width)
    center_y = np.float32(origin_y + half_height)
    for index, vertex in enumerate(source):
        clip = np.zeros(4, dtype=np.float32)
        for row in range(4):
            for column in range(4):
                clip[row] = _float32_fma(
                    matrix[row, column],
                    vertex[column],
                    clip[row],
                )
        if clip[3] == 0.0:
            raise ValueError("vertex transform produced zero clip W")
        inverse_w = np.float32(np.float32(1.0) / clip[3])
        ndc_x = np.float32(clip[0] * inverse_w)
        ndc_y = np.float32(clip[1] * inverse_w)
        positions[index, 0] = _float32_fma(
            ndc_x,
            half_width,
            center_x,
        )
        positions[index, 1] = _float32_fma(
            ndc_y,
            -half_height,
            center_y,
        )
    return positions


def determinant_selector_index(
    case: RasterCase,
    *,
    selector_table_length: int,
) -> tuple[int, int]:
    """Return the fractional-table index and reciprocal exponent."""

    determinant_fixed = case.widthFixed * case.heightFixed
    exponent = determinant_fixed.bit_length() - 1
    if exponent <= 23:
        normalized = determinant_fixed << (23 - exponent)
    else:
        normalized = arithmetic.round_fraction_to_integer_nearest_even(
            Fraction(determinant_fixed, 1 << (exponent - 23))
        )
    if normalized == 1 << 24:
        normalized >>= 1
    mantissa = normalized - (1 << 23)
    quantized = ((mantissa + 2) // 4) * 4
    selector_index = quantized // 4
    if not 0 <= selector_index < selector_table_length:
        raise ValueError("raster determinant is outside the measured selector table")
    # The integer determinant carries sixteen extra fractional-coordinate bits:
    # (widthFixed / 256) * (heightFixed / 256).
    reciprocal_exponent = -(determinant_fixed - 1).bit_length() - 24 + 2 * SUBPIXEL_BITS
    return selector_index, reciprocal_exponent


def _determinant_selector(
    case: RasterCase,
    selector_table: tuple[int, ...],
) -> tuple[int, int]:
    """Return the measured selector and reciprocal exponent for a fixed quad."""

    selector_index, reciprocal_exponent = determinant_selector_index(
        case,
        selector_table_length=len(selector_table),
    )
    return selector_table[selector_index], reciprocal_exponent


def _first_stage_numerator(
    case: RasterCase,
    endpoint: Endpoint,
    *,
    axis: int,
    bias_units: int,
) -> tuple[int, int, int]:
    opposite_fixed = case.heightFixed if axis == 0 else case.widthFixed
    delta = arithmetic.float32(
        arithmetic.bits_float32(endpoint.highBits)
        - arithmetic.bits_float32(endpoint.lowBits)
    )
    if delta == 0.0:
        return 0, 0, 0
    delta_index, delta_exponent = arithmetic.float_significand_and_lsb_exponent(
        float32_bits(abs(delta))
    )
    opposite_bits = arithmetic.round_fraction_to_float32_bits(
        Fraction(opposite_fixed, SUBPIXEL_SCALE)
    )
    opposite_index, opposite_exponent = arithmetic.float_significand_and_lsb_exponent(
        opposite_bits
    )
    numerator_index, numerator_exponent = arithmetic.product_stage(
        delta_index,
        delta_exponent,
        opposite_index,
        opposite_exponent,
        output_bits=coefficient_base.FIRST_STAGE_OUTPUT_BITS,
        truncation_bits=coefficient_base.FIRST_STAGE_TRUNCATION_BITS,
        bias_units=bias_units,
    )
    return (-1 if delta < 0.0 else 1), numerator_index, numerator_exponent


def _reciprocal_stage(
    case: RasterCase,
    index: int,
    exponent: int,
    selector_table: tuple[int, ...],
    *,
    policy: coefficients.CoefficientPolicy = coefficients.MEASURED_POLICY,
) -> tuple[int, int]:
    reciprocal_index, reciprocal_exponent = _determinant_selector(
        case,
        selector_table,
    )
    return arithmetic.product_stage(
        index,
        exponent,
        reciprocal_index,
        reciprocal_exponent,
        output_bits=coefficient_base.RECIPROCAL_STAGE_OUTPUT_BITS,
        truncation_bits=policy.reciprocal_truncation_bits,
        bias_units=policy.reciprocal_bias,
    )


def _determinant_slope(
    case: RasterCase,
    endpoint: Endpoint,
    *,
    axis: int,
    selector_table: tuple[int, ...],
    policy: coefficients.CoefficientPolicy = coefficients.MEASURED_POLICY,
) -> float:
    sign, numerator_index, numerator_exponent = _first_stage_numerator(
        case,
        endpoint,
        axis=axis,
        bias_units=policy.slope_first_bias,
    )
    if sign == 0:
        return 0.0
    coefficient_index, coefficient_exponent = _reciprocal_stage(
        case,
        numerator_index,
        numerator_exponent,
        selector_table,
        policy=policy,
    )
    return arithmetic.float32(
        math.ldexp(sign * coefficient_index, coefficient_exponent)
    )


def _tile_term(
    case: RasterCase,
    endpoint: Endpoint,
    *,
    axis: int,
    displacement_fixed: int,
    selector_table: tuple[int, ...],
    policy: coefficients.CoefficientPolicy = coefficients.MEASURED_POLICY,
) -> Fraction:
    sign, numerator_index, numerator_exponent = _first_stage_numerator(
        case,
        endpoint,
        axis=axis,
        bias_units=policy.constant_first_bias,
    )
    if sign == 0 or displacement_fixed == 0:
        return Fraction(0)
    distance_bits = arithmetic.round_fraction_to_float32_bits(
        Fraction(abs(displacement_fixed), SUBPIXEL_SCALE)
    )
    distance_index, distance_exponent = arithmetic.float_significand_and_lsb_exponent(
        distance_bits
    )
    middle_index, middle_exponent = coefficients.column_product_stage(
        numerator_index,
        numerator_exponent,
        distance_index,
        distance_exponent,
        output_bits=coefficient_base.TILE_STAGE_OUTPUT_BITS,
        truncation_bits=policy.tile_truncation_bits,
        bias_units=policy.tile_bias,
        carry_mode=policy.tile_carry_mode,
        propagated_column_count=policy.tile_propagated_column_count,
        sticky_carry_limit=policy.tile_sticky_carry_limit,
    )
    coefficient_index, coefficient_exponent = _reciprocal_stage(
        case,
        middle_index,
        middle_exponent,
        selector_table,
        policy=policy,
    )
    result = Fraction(coefficient_index) * arithmetic.power_of_two(coefficient_exponent)
    return -result if sign * displacement_fixed < 0 else result


def slopes_bits(
    quad: RuntimeQuad,
    selector_table: tuple[int, ...],
    *,
    policy: coefficients.CoefficientPolicy = coefficients.MEASURED_POLICY,
) -> tuple[int, int, int, int]:
    return tuple(
        arithmetic.float32_bits(
            _determinant_slope(
                quad.case,
                endpoint,
                axis=axis,
                selector_table=selector_table,
                policy=policy,
            )
        )
        for endpoint, axis in zip(
            quad.endpoints,
            quad.channelAxes,
            strict=True,
        )
    )  # type: ignore[return-value]


def coefficient_bits(
    quad: RuntimeQuad,
    *,
    channel: int,
    primitive: int,
    tile: int,
    selector_table: tuple[int, ...],
    anchor_high_by_primitive_axis: AnchorPolicy | None = None,
    policy: coefficients.CoefficientPolicy = coefficients.MEASURED_POLICY,
) -> int:
    """Return one runtime constant without consulting captured coordinates."""

    if not 0 <= channel < 4 or primitive not in (0, 1):
        raise ValueError("invalid channel or primitive")
    case = quad.case
    endpoint = quad.endpoints[channel]
    axis = quad.channelAxes[channel]
    # AGX anchors primitive-zero X at the high edge only for the descending
    # split used by the background quad.  Apple's separately indexed
    # highlight quad uses the opposite diagonal and anchors both primitives
    # at the low edge; independent moving-alpha captures distinguish all 16
    # X/Y primitive anchor combinations and select this convention exactly.
    anchor_high = (
        anchor_high_by_primitive_axis[primitive][axis]
        if anchor_high_by_primitive_axis is not None
        else axis == 0 and primitive == 0 and not quad.ascendingDiagonal
    )
    if anchor_high:
        anchor_bits = endpoint.highBits
        anchor_position_fixed = (
            case.originXFixed + case.widthFixed
            if axis == 0
            else case.originYFixed + case.heightFixed
        )
    else:
        anchor_bits = endpoint.lowBits
        anchor_position_fixed = case.originXFixed if axis == 0 else case.originYFixed
    displacement_fixed = tile * TILE_SIZE * SUBPIXEL_SCALE - anchor_position_fixed
    value = arithmetic.float32_bits_fraction(anchor_bits) + _tile_term(
        case,
        endpoint,
        axis=axis,
        displacement_fixed=displacement_fixed,
        selector_table=selector_table,
        policy=policy,
    )
    return composite.quantize_composite_constant_bits(value)


def coefficient_table(
    quad: RuntimeQuad,
    *,
    tile_start: int | None = None,
    tile_count: int | None = None,
    selector_table: tuple[int, ...] | None = None,
    anchor_high_by_primitive_axis: AnchorPolicy | None = None,
    policy: coefficients.CoefficientPolicy = coefficients.MEASURED_POLICY,
) -> tuple[int, UInt32Array]:
    """Build the compact RGBA32UI table consumed by the exact GLSL pass."""

    case = quad.case
    tile_fixed = TILE_SIZE * SUBPIXEL_SCALE
    required_start = min(case.originXFixed, case.originYFixed) // tile_fixed
    required_end = max(
        (case.originXFixed + case.widthFixed - 1) // tile_fixed,
        (case.originYFixed + case.heightFixed - 1) // tile_fixed,
    )
    first = required_start if tile_start is None else tile_start
    count = required_end - first + 1 if tile_count is None else tile_count
    if count <= 0 or first > required_start or first + count - 1 < required_end:
        raise ValueError("coefficient table does not cover the complete quad")
    selectors = selector_table or arithmetic.load_selector_table()
    table = np.empty((2, count, 4), dtype=np.uint32)
    for primitive in (0, 1):
        for tile_offset in range(count):
            tile = first + tile_offset
            for channel in range(4):
                table[primitive, tile_offset, channel] = coefficient_bits(
                    quad,
                    channel=channel,
                    primitive=primitive,
                    tile=tile,
                    selector_table=selectors,
                    anchor_high_by_primitive_axis=anchor_high_by_primitive_axis,
                    policy=policy,
                )
    return first, table


def axis_table(
    quad: RuntimeQuad,
    *,
    selector_table: tuple[int, ...] | None = None,
    anchor_high_by_primitive_axis: AnchorPolicy | None = None,
    policy: coefficients.CoefficientPolicy = coefficients.MEASURED_POLICY,
    helper_lane_halo: int = 0,
) -> tuple[int, UInt32Array]:
    """Build exact per-axis center values for one runtime quad.

    The compact coefficient path is sufficient for most pixels, but evaluating
    its 36-bit AGX center accumulator with binary32 FMA loses a few low bits.
    This table materializes only the separable X/Y axes, not a per-pixel
    surface, so the exact iterator costs tens of KiB for a 1024-pixel target.
    """

    if helper_lane_halo < 0:
        raise ValueError("helper-lane halo cannot be negative")
    left, bottom, right, top = visible_pixel_bounds(quad.case)
    first = min(left, bottom) - helper_lane_halo
    end = max(right, top) + helper_lane_halo
    if end <= first:
        raise ValueError("runtime quad has no visible axis samples")
    coordinates = range(first, end)
    selectors = selector_table or arithmetic.load_selector_table()
    table = np.empty((2, len(coordinates), 4), dtype=np.uint32)
    for primitive in (0, 1):
        for channel in range(4):
            table[primitive, :, channel] = coordinate_axis_bits(
                quad,
                channel=channel,
                primitive=primitive,
                coordinates=coordinates,
                selector_table=selectors,
                anchor_high_by_primitive_axis=anchor_high_by_primitive_axis,
                policy=policy,
            )
    return first, table


def coordinate_axis_bits(
    quad: RuntimeQuad,
    *,
    channel: int,
    primitive: int,
    coordinates: range,
    selector_table: tuple[int, ...],
    anchor_high_by_primitive_axis: AnchorPolicy | None = None,
    policy: coefficients.CoefficientPolicy = coefficients.MEASURED_POLICY,
) -> UInt32Array:
    """Evaluate Apple's 36-bit quad-center iterator along one raster axis."""

    axis = quad.channelAxes[channel]
    endpoint = quad.endpoints[channel]
    slope_float = _determinant_slope(
        quad.case,
        endpoint,
        axis=axis,
        selector_table=selector_table,
        policy=policy,
    )
    slope = arithmetic.float32_bits_fraction(arithmetic.float32_bits(slope_float))
    cache: dict[int, tuple[Any, Any]] = {}
    result = np.empty(len(coordinates), dtype=np.uint32)
    for output_index, coordinate in enumerate(coordinates):
        tile = coordinate // TILE_SIZE
        if tile not in cache:
            bits = coefficient_bits(
                quad,
                channel=channel,
                primitive=primitive,
                tile=tile,
                selector_table=selector_table,
                anchor_high_by_primitive_axis=anchor_high_by_primitive_axis,
                policy=policy,
            )
            constant = arithmetic.float32_bits_fraction(bits)
            step = iterator.significand_step(
                constant,
                iterator.endpoint_step(endpoint),
            )
            cache[tile] = constant, step
        constant, step = cache[tile]
        local_pixel = coordinate - tile * TILE_SIZE
        left, right = iterator.quad_center_pair(
            local_pixel,
            slope,
            constant,
            step,
        )
        result[output_index] = right if local_pixel & 1 else left
    return result


def primitive_ids(
    quad: RuntimeQuad,
    x_coordinates: UInt32Array,
    y_coordinates: UInt32Array,
) -> UInt32Array:
    """Return the top-left-rule primitive for pixel centers in the quad."""

    case = quad.case
    relative_x_fixed = (
        x_coordinates.astype(np.int64) * SUBPIXEL_SCALE
        + SUBPIXEL_SCALE // 2
        - case.originXFixed
    )
    relative_y_fixed = (
        y_coordinates.astype(np.int64) * SUBPIXEL_SCALE
        + SUBPIXEL_SCALE // 2
        - case.originYFixed
    )
    if quad.ascendingDiagonal:
        above_diagonal = (
            relative_y_fixed * case.widthFixed > relative_x_fixed * case.heightFixed
        )
        return above_diagonal.astype(np.uint32)
    descending_diagonal = (
        relative_x_fixed * case.heightFixed + relative_y_fixed * case.widthFixed
    )
    area = case.widthFixed * case.heightFixed
    return (descending_diagonal < area).astype(np.uint32)


def _ceil_div(numerator: int, denominator: int) -> int:
    return -((-numerator) // denominator)


def visible_pixel_bounds(case: RasterCase) -> tuple[int, int, int, int]:
    """Return the half-open pixel-center coverage bounds of an axis-aligned quad."""

    half = SUBPIXEL_SCALE // 2
    return (
        _ceil_div(case.originXFixed - half, SUBPIXEL_SCALE),
        _ceil_div(case.originYFixed - half, SUBPIXEL_SCALE),
        _ceil_div(case.originXFixed + case.widthFixed - half, SUBPIXEL_SCALE),
        _ceil_div(case.originYFixed + case.heightFixed - half, SUBPIXEL_SCALE),
    )


def validate_capture(capture: Path) -> JsonObject:
    quad = runtime_quad(capture)
    trace_path = capture / TRACE_NAME
    values = np.fromfile(trace_path, dtype="<u4")
    runtime = json.loads((capture / "runtime.json").read_text(encoding="utf-8"))
    expected = runtime.get("captureSize", {})
    width = int(expected.get("width", 1024))
    height = int(expected.get("height", 1024))
    if values.size != width * height * 4:
        raise ValueError(
            f"{trace_path} has {values.size} words; expected {width * height * 4}"
        )
    trace = values.reshape(height, width, 4)
    case = quad.case
    raster_left, raster_bottom, raster_right, raster_top = visible_pixel_bounds(case)
    left = max(0, raster_left)
    right = min(width, raster_right)
    bottom = max(0, raster_bottom)
    top = min(height, raster_top)
    if left >= right or bottom >= top:
        raise ValueError(f"{capture} quad does not intersect its render target")

    selector_table = arithmetic.load_selector_table()
    axis_predictions = {
        (channel, primitive): coordinate_axis_bits(
            quad,
            channel=channel,
            primitive=primitive,
            coordinates=(
                range(left, right)
                if quad.channelAxes[channel] == 0
                else range(bottom, top)
            ),
            selector_table=selector_table,
        )
        for channel in range(4)
        for primitive in (0, 1)
    }
    yy, xx = np.indices((top - bottom, right - left), dtype=np.uint32)
    xx += np.uint32(left)
    yy += np.uint32(bottom)
    primitives = primitive_ids(quad, xx, yy)
    candidate = np.empty((top - bottom, right - left, 4), dtype=np.uint32)
    for channel, axis in enumerate(quad.channelAxes):
        indices = xx - np.uint32(left) if axis == 0 else yy - np.uint32(bottom)
        for primitive in (0, 1):
            selected = primitives == primitive
            candidate[..., channel][selected] = axis_predictions[
                channel,
                primitive,
            ][indices[selected]]

    reference = trace[bottom:top, left:right]
    changed = candidate != reference
    pixel_changed = np.any(changed, axis=2)
    example_coordinates = np.argwhere(pixel_changed)[:16]
    return {
        "capture": str(capture),
        "runtimeJsonSha256": sha256_file(capture / "runtime.json"),
        "trace": {
            "path": str(trace_path),
            "sha256": sha256_file(trace_path),
        },
        "quad": {
            "origin": [case.originX, case.originY],
            "extent": [case.width, case.height],
            "fixedBounds": [
                case.originXFixed,
                case.originYFixed,
                case.originXFixed + case.widthFixed,
                case.originYFixed + case.heightFixed,
            ],
            "fixedUnitsPerPixel": SUBPIXEL_SCALE,
            "visibleBounds": [left, bottom, right, top],
            "diagonal": "ascending" if quad.ascendingDiagonal else "descending",
            "endpointBits": [
                {
                    "name": endpoint.name,
                    "low": f"0x{endpoint.lowBits:08x}",
                    "high": f"0x{endpoint.highBits:08x}",
                }
                for endpoint in quad.endpoints
            ],
            "slopeBits": [
                f"0x{bits:08x}" for bits in slopes_bits(quad, selector_table)
            ],
        },
        "comparison": {
            "exact": not bool(np.any(changed)),
            "comparedWords": int(changed.size),
            "mismatchedWords": int(np.count_nonzero(changed)),
            "comparedPixels": int(pixel_changed.size),
            "mismatchedPixels": int(np.count_nonzero(pixel_changed)),
            "mismatchedChannels": [
                int(np.count_nonzero(changed[..., channel])) for channel in range(4)
            ],
            "examples": [
                {
                    "x": int(x + left),
                    "y": int(y + bottom),
                    "predicted": [f"0x{int(value):08x}" for value in candidate[y, x]],
                    "apple": [f"0x{int(value):08x}" for value in reference[y, x]],
                }
                for y, x in example_coordinates
            ],
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("captures", type=Path, nargs="+")
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    captures = [validate_capture(path) for path in arguments.captures]
    report = {
        "liquidGlassRuntimeRasterCoefficientGateSchemaVersion": 1,
        "implementation": {
            "file": "analysis/liquid_glass_runtime_raster_coefficients.py",
            "coefficientModel": (
                "lg-test/Analysis/raster_tile_coefficient_model_v3.py"
            ),
            "model": (
                "1/256-pixel setup with ties toward positive infinity; "
                "27-bit first/tile/reciprocal stages; individually low-19-bit "
                "truncated tile partial products; carry propagated only "
                "through discarded column 18; 28-bit composite; 36-bit "
                "quad-center iterator"
            ),
        },
        "captures": captures,
        "gate": {
            "captureCount": len(captures),
            "comparedWords": sum(
                int(capture["comparison"]["comparedWords"]) for capture in captures
            ),
            "mismatchedWords": sum(
                int(capture["comparison"]["mismatchedWords"]) for capture in captures
            ),
            "exact": all(bool(capture["comparison"]["exact"]) for capture in captures),
            "capturedCoordinateOrCoefficientTableReadByPredictor": False,
            "productionShaderAuthorized": False,
        },
    }
    encoded = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if arguments.output is None:
        print(encoded, end="")
    else:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(encoded, encoding="utf-8")
        print(arguments.output)
    return 0 if report["gate"]["exact"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
