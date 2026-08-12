#!/usr/bin/env python3
"""Bit-gate portable coordinate models on Apple geometry captures.

This is deliberately an end-to-end image gate.  The Apple interpolant trace is
used only by named localization controls; portable candidates never read it.
"""

import argparse
import hashlib
import json
import math
import platform
import struct
import time
import zlib
from pathlib import Path
from typing import Any

import numpy as np

from apple_glass_reference_renderer import (
    CAPTURE_HEIGHT,
    CAPTURE_WIDTH,
    AppleGlassReferenceRenderer,
    bgra_raw,
    compare_images,
)
from liquid_glass_geometry_transfer import (
    _glass_snapshots,
    _source_origin,
    _source_texture,
    _vertices,
)
from liquid_glass_glsl_end_to_end_gate import configure_recovered_material


type JsonObject = dict[str, Any]

COORDINATE_MODE = 7
VARIANTS = {
    "direct-multiply": 0,
    "direct-sdf-apple-source": 1,
    "apple-sdf-direct-source": 2,
    "anchor-fma-source": 3,
    "tile-iterator": 4,
    "tile-sdf-direct-source": 5,
    "direct-sdf-tile-source": 6,
    "apple-control": 7,
    "direct-divide": 8,
    "internal-tile-iterator": 9,
    "direct-sdf-internal-tile-source": 10,
    "internal-tile-sdf-apple-source": 11,
    "apple-sdf-internal-tile-source": 12,
    "physical-rebase-source": 13,
    "apple-sdf-physical-rebase-source": 14,
}

SELECTOR_TABLE_PATH = Path(
    "lg-test/Analysis/raster_fractional_subpixel_resolved_selectors.zlib"
)
SELECTOR_TABLE_COMPRESSED_SHA256 = (
    "2b49309da4283726cc894f7aada3c25db41cf8ca71a4c278c952407e9e1eedd3"
)
SELECTOR_TABLE_RAW_SHA256 = (
    "b0990c2ce17fff5ebf06124497a38d38c9cf22e7e9210ccb6f95adb2c6834d53"
)
SELECTOR_TABLE_COUNT = 2_097_153
FIRST_STAGE_OUTPUT_BITS = 27
FIRST_STAGE_TRUNCATION_BITS = 16
FIRST_STAGE_BIAS_UNITS = 14
SECOND_STAGE_OUTPUT_BITS = 27
SECOND_STAGE_TRUNCATION_BITS = 19
SECOND_STAGE_BIAS_UNITS = 20
REBASE_STAGE_OUTPUT_BITS = 27
REBASE_STAGE_TRUNCATION_BITS = 15
REBASE_STAGE_BIAS_UNITS = 108


def float32(value: float) -> float:
    return struct.unpack("<f", struct.pack("<f", value))[0]


def float32_bits(value: float) -> int:
    return struct.unpack("<I", struct.pack("<f", value))[0]


def bits_float32(bits: int) -> float:
    return struct.unpack("<f", struct.pack("<I", bits))[0]


def shifted_float_bits(bits: int, offset: int) -> int:
    if not 0 < bits < 0x7F80_0000:
        raise ValueError("a positive normal binary32 slope is required")
    shifted = bits + offset
    if not 0 < shifted < 0x7F80_0000:
        raise ValueError("slope ULP offset leaves the finite positive domain")
    return shifted


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_selector_table(path: Path) -> np.ndarray:
    compressed = path.read_bytes()
    if sha256_bytes(compressed) != SELECTOR_TABLE_COMPRESSED_SHA256:
        raise ValueError("fractional raster-selector archive differs")
    raw = zlib.decompress(compressed)
    if sha256_bytes(raw) != SELECTOR_TABLE_RAW_SHA256:
        raise ValueError("fractional raster-selector table differs")
    table = np.frombuffer(raw, dtype="<u4")
    if len(table) != SELECTOR_TABLE_COUNT:
        raise ValueError("fractional raster-selector count differs")
    return table


def reciprocal_selector(
    determinant: int,
    selector_table: np.ndarray,
) -> int:
    if determinant <= 0:
        raise ValueError("a positive integer determinant is required")
    exponent = determinant.bit_length() - 1
    if exponent > 23:
        raise ValueError("large determinant normalization is not implemented")
    normalized = determinant << (23 - exponent)
    mantissa = normalized - (1 << 23)
    quantized = ((mantissa + 2) // 4) * 4
    return int(selector_table[quantized // 4])


def float_significand_and_lsb_exponent(bits: int) -> tuple[int, int]:
    exponent = (bits >> 23) & 0xFF
    fraction = bits & 0x7F_FFFF
    if bits >> 31 or not 0 < exponent < 0xFF:
        raise ValueError("a positive normal binary32 is required")
    return (1 << 23) | fraction, exponent - 127 - 23


def partial_product_sum(
    multiplicand: int,
    multiplier: int,
    truncation_bits: int,
) -> int:
    return sum(
        ((multiplicand << bit) >> truncation_bits) << truncation_bits
        for bit in range(multiplier.bit_length())
        if multiplier & (1 << bit)
    )


def product_stage(
    multiplicand: int,
    multiplicand_lsb_exponent: int,
    multiplier: int,
    multiplier_lsb_exponent: int,
    *,
    output_bits: int,
    truncation_bits: int,
    bias_units: int,
) -> tuple[int, int]:
    product_shift = (multiplicand * multiplier).bit_length() - output_bits
    if product_shift < 0:
        raise ValueError("product does not fill the requested precision")
    product_index = (
        partial_product_sum(
            multiplicand,
            multiplier,
            truncation_bits,
        )
        + (bias_units << truncation_bits)
    ) >> product_shift
    return (
        product_index,
        multiplicand_lsb_exponent
        + multiplier_lsb_exponent
        + product_shift,
    )


def internal_slope(
    delta: float,
    *,
    opposite_edge: int,
    determinant: int,
    reciprocal_index: int,
) -> float:
    sign = -1.0 if delta < 0.0 else 1.0
    delta_bits = float32_bits(float32(abs(delta)))
    delta_significand, delta_exponent = (
        float_significand_and_lsb_exponent(delta_bits)
    )
    edge_significand, edge_exponent = (
        float_significand_and_lsb_exponent(
            float32_bits(float(opposite_edge))
        )
    )
    numerator_index, numerator_exponent = product_stage(
        delta_significand,
        delta_exponent,
        edge_significand,
        edge_exponent,
        output_bits=FIRST_STAGE_OUTPUT_BITS,
        truncation_bits=FIRST_STAGE_TRUNCATION_BITS,
        bias_units=FIRST_STAGE_BIAS_UNITS,
    )
    reciprocal_exponent = -(determinant - 1).bit_length() - 24
    coefficient_index, coefficient_exponent = product_stage(
        numerator_index,
        numerator_exponent,
        reciprocal_index,
        reciprocal_exponent,
        output_bits=SECOND_STAGE_OUTPUT_BITS,
        truncation_bits=SECOND_STAGE_TRUNCATION_BITS,
        bias_units=SECOND_STAGE_BIAS_UNITS,
    )
    return math.copysign(
        math.ldexp(coefficient_index, coefficient_exponent),
        sign,
    )


def split_internal_slope(value: float) -> tuple[float, float]:
    high = float32(value)
    low = float32(value - high)
    if high + low != value:
        raise ValueError("internal slope does not split exactly into two floats")
    return high, low


def physical_rebase_constant(
    anchor_value: float,
    anchor_position: float,
    tile_origin: int,
    slope: float,
) -> float:
    displacement = float32(float(tile_origin) - anchor_position)
    if displacement == 0.0 or slope == 0.0:
        return float32(anchor_value)
    slope_mantissa, slope_exponent = math.frexp(abs(slope))
    slope_index = int(slope_mantissa * (1 << 27))
    slope_lsb_exponent = slope_exponent - 27
    if math.ldexp(slope_index, slope_lsb_exponent) != abs(slope):
        raise ValueError("rebase slope is not a 27-bit binary value")
    displacement_significand, displacement_exponent = (
        float_significand_and_lsb_exponent(
            float32_bits(abs(displacement))
        )
    )
    product_index, product_exponent = product_stage(
        slope_index,
        slope_lsb_exponent,
        displacement_significand,
        displacement_exponent,
        output_bits=REBASE_STAGE_OUTPUT_BITS,
        truncation_bits=REBASE_STAGE_TRUNCATION_BITS,
        bias_units=REBASE_STAGE_BIAS_UNITS,
    )
    product = math.ldexp(product_index, product_exponent)
    if (slope < 0.0) != (displacement < 0.0):
        product = -product
    return float32(anchor_value + product)


def physical_plane_constant(
    anchor_value: float,
    delta: float,
    anchor_position: float,
    tile_origin: int,
    *,
    opposite_edge: int,
    determinant: int,
    reciprocal_index: int,
) -> float:
    """Evaluate a tile's plane numerator through the measured AGX stages."""

    displacement = tile_origin - int(anchor_position)
    if float(displacement) != float(tile_origin) - anchor_position:
        raise ValueError("fractional plane anchors are not implemented")
    if displacement == 0 or delta == 0.0:
        return float32(anchor_value)
    term = internal_slope(
        math.copysign(abs(delta), delta * displacement),
        opposite_edge=opposite_edge * abs(displacement),
        determinant=determinant,
        reciprocal_index=reciprocal_index,
    )
    return float32(anchor_value + term)


def instrumented_shader_source(path: Path) -> str:
    source = path.read_text(encoding="utf-8")
    declaration = "uniform int CoordinateMode;"
    replacement = """uniform int CoordinateMode;
uniform int GenericCoordinateVariant;
uniform vec2 GenericSdfCenter;
uniform vec2 GenericSourceOrigin;
uniform vec2 GenericSourceExtent;
uniform vec2 GenericSourceInverseExtent;
uniform vec2 GenericSourceAnchorPosition;
uniform vec2 GenericSourceAnchorValue;
uniform vec4 GenericSdfAnchorPositions;
uniform vec4 GenericSdfAnchorValues;
uniform vec4 GenericSourceAnchorPositions;
uniform vec4 GenericSourceAnchorValues;
uniform vec2 GenericSdfSlopeHigh;
uniform vec2 GenericSdfSlopeLow;
uniform vec2 GenericSourceSlopeHigh;
uniform vec2 GenericSourceSlopeLow;
uniform vec2 GenericPhysicalSourceConstants[64];
uniform highp uvec2 GenericSourceSlopeBits;"""
    if source.count(declaration) != 1:
        raise ValueError("coordinate-mode declaration is not unique")
    source = source.replace(declaration, replacement, 1)

    branch = "        if (CoordinateMode == 4) {"
    instrumented = """        if (CoordinateMode == 7) {
            vec2 screen_position = gl_FragCoord.xy;
            vec2 metal_position = vec2(
                gl_FragCoord.x,
                1024.0 - gl_FragCoord.y
            );
            precise vec2 direct_sdf =
                screen_position - GenericSdfCenter;
            precise vec2 direct_source =
                (metal_position - GenericSourceOrigin)
                * GenericSourceInverseExtent;
            uvec4 apple_bits = texelFetch(
                AppleInterpolantTrace,
                ivec2(gl_FragCoord.xy),
                0
            );
            vec2 apple_sdf = uintBitsToFloat(apple_bits.xy);
            vec2 apple_source = uintBitsToFloat(apple_bits.zw);
            vec2 source_slope =
                uintBitsToFloat(GenericSourceSlopeBits);
            precise vec2 anchor_source = vec2(
                fma(
                    metal_position.x
                        - GenericSourceAnchorPosition.x,
                    source_slope.x,
                    GenericSourceAnchorValue.x
                ),
                fma(
                    metal_position.y
                        - GenericSourceAnchorPosition.y,
                    source_slope.y,
                    GenericSourceAnchorValue.y
                )
            );

            ivec2 metal_pixel = ivec2(floor(metal_position));
            vec2 tile_position =
                vec2(metal_pixel & 31) + vec2(0.5);
            vec2 tile_origin = vec2(metal_pixel & ~31);
            precise vec4 tile_slope = vec4(
                1.0,
                -1.0,
                source_slope
            );
            precise vec4 tile_constant = vec4(
                tile_origin.x - GenericSdfCenter.x,
                (1024.0 - GenericSdfCenter.y) - tile_origin.y,
                (tile_origin - GenericSourceOrigin)
                    * GenericSourceInverseExtent
            );
            precise vec4 tile_nearest = vec4(
                fma(
                    tile_position.x,
                    tile_slope.x,
                    tile_constant.x
                ),
                fma(
                    tile_position.y,
                    tile_slope.y,
                    tile_constant.y
                ),
                fma(
                    tile_position.x,
                    tile_slope.z,
                    tile_constant.z
                ),
                fma(
                    tile_position.y,
                    tile_slope.w,
                    tile_constant.w
                )
            );
            precise vec4 tile_residual = vec4(
                fma(
                    tile_position.x,
                    tile_slope.x,
                    tile_constant.x - tile_nearest.x
                ),
                fma(
                    tile_position.y,
                    tile_slope.y,
                    tile_constant.y - tile_nearest.y
                ),
                fma(
                    tile_position.x,
                    tile_slope.z,
                    tile_constant.z - tile_nearest.z
                ),
                fma(
                    tile_position.y,
                    tile_slope.w,
                    tile_constant.w - tile_nearest.w
                )
            );
            uvec4 tile_bits = floatBitsToUint(tile_nearest);
            bvec4 rounded_away_from_zero = bvec4(
                (tile_nearest.x > 0.0 && tile_residual.x < 0.0)
                    || (tile_nearest.x < 0.0
                        && tile_residual.x > 0.0),
                (tile_nearest.y > 0.0 && tile_residual.y < 0.0)
                    || (tile_nearest.y < 0.0
                        && tile_residual.y > 0.0),
                (tile_nearest.z > 0.0 && tile_residual.z < 0.0)
                    || (tile_nearest.z < 0.0
                        && tile_residual.z > 0.0),
                (tile_nearest.w > 0.0 && tile_residual.w < 0.0)
                    || (tile_nearest.w < 0.0
                        && tile_residual.w > 0.0)
            );
            tile_bits -= uvec4(rounded_away_from_zero);
            vec4 tile_coordinate = uintBitsToFloat(tile_bits);

            bool internal_primitive_zero = gl_PrimitiveID == 0;
            precise vec2 internal_sdf_anchor_position =
                internal_primitive_zero
                ? GenericSdfAnchorPositions.xy
                : GenericSdfAnchorPositions.zw;
            precise vec2 internal_sdf_anchor_value =
                internal_primitive_zero
                ? GenericSdfAnchorValues.xy
                : GenericSdfAnchorValues.zw;
            precise vec2 internal_source_anchor_position =
                internal_primitive_zero
                ? GenericSourceAnchorPositions.xy
                : GenericSourceAnchorPositions.zw;
            precise vec2 internal_source_anchor_value =
                internal_primitive_zero
                ? GenericSourceAnchorValues.xy
                : GenericSourceAnchorValues.zw;
            precise vec4 internal_anchor = vec4(
                internal_sdf_anchor_value,
                internal_source_anchor_value
            );
            precise vec4 internal_displacement = vec4(
                tile_origin - internal_sdf_anchor_position,
                tile_origin - internal_source_anchor_position
            );
            precise vec4 internal_slope_high = vec4(
                GenericSdfSlopeHigh,
                GenericSourceSlopeHigh
            );
            precise vec4 internal_slope_low = vec4(
                GenericSdfSlopeLow,
                GenericSourceSlopeLow
            );
            precise vec4 internal_constant_nearest = fma(
                internal_displacement,
                internal_slope_high,
                internal_anchor
            );
            precise vec4 internal_constant_residual = fma(
                internal_displacement,
                internal_slope_high,
                internal_anchor - internal_constant_nearest
            );
            precise vec4 internal_constant_correction = fma(
                internal_displacement,
                internal_slope_low,
                internal_constant_residual
            );
            precise vec4 internal_tile_constant =
                internal_constant_nearest
                + internal_constant_correction;
            precise vec4 internal_tile_position = vec4(
                tile_position.x,
                tile_position.y,
                tile_position.x,
                tile_position.y
            );
            precise vec4 internal_tile_nearest = fma(
                internal_tile_position,
                internal_slope_high,
                internal_tile_constant
            );
            precise vec4 internal_tile_residual = fma(
                internal_tile_position,
                internal_slope_high,
                internal_tile_constant - internal_tile_nearest
            );
            uvec4 internal_tile_bits =
                floatBitsToUint(internal_tile_nearest);
            bvec4 internal_rounded_away_from_zero = bvec4(
                (internal_tile_nearest.x > 0.0
                    && internal_tile_residual.x < 0.0)
                    || (internal_tile_nearest.x < 0.0
                        && internal_tile_residual.x > 0.0),
                (internal_tile_nearest.y > 0.0
                    && internal_tile_residual.y < 0.0)
                    || (internal_tile_nearest.y < 0.0
                        && internal_tile_residual.y > 0.0),
                (internal_tile_nearest.z > 0.0
                    && internal_tile_residual.z < 0.0)
                    || (internal_tile_nearest.z < 0.0
                        && internal_tile_residual.z > 0.0),
                (internal_tile_nearest.w > 0.0
                    && internal_tile_residual.w < 0.0)
                    || (internal_tile_nearest.w < 0.0
                        && internal_tile_residual.w > 0.0)
            );
            internal_tile_bits -=
                uvec4(internal_rounded_away_from_zero);
            vec4 internal_tile_coordinate =
                uintBitsToFloat(internal_tile_bits);

            int physical_primitive_offset =
                internal_primitive_zero ? 0 : 32;
            precise vec2 physical_source_constant = vec2(
                GenericPhysicalSourceConstants[
                    physical_primitive_offset
                    + (metal_pixel.x >> 5)
                ].x,
                GenericPhysicalSourceConstants[
                    physical_primitive_offset
                    + (metal_pixel.y >> 5)
                ].y
            );
            precise vec2 physical_source_nearest = fma(
                tile_position,
                GenericSourceSlopeHigh,
                physical_source_constant
            );
            precise vec2 physical_source_residual = fma(
                tile_position,
                GenericSourceSlopeHigh,
                physical_source_constant - physical_source_nearest
            );
            uvec2 physical_source_bits =
                floatBitsToUint(physical_source_nearest);
            bvec2 physical_rounded_away_from_zero = bvec2(
                (physical_source_nearest.x > 0.0
                    && physical_source_residual.x < 0.0)
                    || (physical_source_nearest.x < 0.0
                        && physical_source_residual.x > 0.0),
                (physical_source_nearest.y > 0.0
                    && physical_source_residual.y < 0.0)
                    || (physical_source_nearest.y < 0.0
                        && physical_source_residual.y > 0.0)
            );
            physical_source_bits -=
                uvec2(physical_rounded_away_from_zero);
            vec2 physical_source_coordinate =
                uintBitsToFloat(physical_source_bits);

            if (GenericCoordinateVariant == 0) {
                replay_sdf_uv = direct_sdf;
                replay_source_uv = direct_source;
            } else if (GenericCoordinateVariant == 1) {
                replay_sdf_uv = direct_sdf;
                replay_source_uv = apple_source;
            } else if (GenericCoordinateVariant == 2) {
                replay_sdf_uv = apple_sdf;
                replay_source_uv = direct_source;
            } else if (GenericCoordinateVariant == 3) {
                replay_sdf_uv = direct_sdf;
                replay_source_uv = anchor_source;
            } else if (GenericCoordinateVariant == 4) {
                replay_sdf_uv = tile_coordinate.xy;
                replay_source_uv = tile_coordinate.zw;
            } else if (GenericCoordinateVariant == 5) {
                replay_sdf_uv = tile_coordinate.xy;
                replay_source_uv = direct_source;
            } else if (GenericCoordinateVariant == 6) {
                replay_sdf_uv = direct_sdf;
                replay_source_uv = tile_coordinate.zw;
            } else if (GenericCoordinateVariant == 7) {
                replay_sdf_uv = apple_sdf;
                replay_source_uv = apple_source;
            } else if (GenericCoordinateVariant == 8) {
                replay_sdf_uv = direct_sdf;
                replay_source_uv =
                    (metal_position - GenericSourceOrigin)
                    / GenericSourceExtent;
            } else if (GenericCoordinateVariant == 9) {
                replay_sdf_uv = internal_tile_coordinate.xy;
                replay_source_uv = internal_tile_coordinate.zw;
            } else if (GenericCoordinateVariant == 10) {
                replay_sdf_uv = direct_sdf;
                replay_source_uv = internal_tile_coordinate.zw;
            } else if (GenericCoordinateVariant == 11) {
                replay_sdf_uv = internal_tile_coordinate.xy;
                replay_source_uv = apple_source;
            } else if (GenericCoordinateVariant == 12) {
                replay_sdf_uv = apple_sdf;
                replay_source_uv = internal_tile_coordinate.zw;
            } else if (GenericCoordinateVariant == 13) {
                replay_sdf_uv = internal_tile_coordinate.xy;
                replay_source_uv = physical_source_coordinate;
            } else {
                replay_sdf_uv = apple_sdf;
                replay_source_uv = physical_source_coordinate;
            }
        } else if (CoordinateMode == 4) {"""
    if source.count(branch) != 1:
        raise ValueError("coordinate-mode branch is not unique")
    return source.replace(branch, instrumented, 1)


def capture_parameters(
    capture: Path,
    selector_table: np.ndarray,
) -> JsonObject:
    runtime = json.loads(
        (capture / "runtime.json").read_text(encoding="utf-8")
    )
    geometry = runtime["geometryEvidence"]
    snapshots = _glass_snapshots(runtime, stage="vertex", index=1)
    main = _vertices(snapshots[0], 6)
    texture = _source_texture(runtime)
    virtual_width = int(texture["width"]) * 4
    virtual_height = int(texture["height"]) * 4
    origin_x, origin_y, residual = _source_origin(
        main,
        virtual_width=virtual_width,
        virtual_height=virtual_height,
    )
    minimum_x = min(vertex[0] for vertex in main)
    minimum_y = min(vertex[1] for vertex in main)
    maximum_x = max(vertex[0] for vertex in main)
    maximum_y = max(vertex[1] for vertex in main)
    sdf_x = next(vertex[4] for vertex in main if vertex[0] == minimum_x)
    sdf_y = next(vertex[5] for vertex in main if vertex[1] == minimum_y)
    source_x = next(
        vertex[6] for vertex in main if vertex[0] == minimum_x
    )
    source_y = next(
        vertex[7] for vertex in main if vertex[1] == minimum_y
    )
    width = int(geometry["width"])
    height = int(geometry["height"])
    if float(geometry["width"]) != width:
        raise ValueError("fractional geometry width is not implemented")
    if float(geometry["height"]) != height:
        raise ValueError("fractional geometry height is not implemented")
    if maximum_x - minimum_x != width:
        raise ValueError("captured x extent differs from geometry width")
    if maximum_y - minimum_y != height:
        raise ValueError("captured y extent differs from geometry height")

    determinant = width * height
    reciprocal_index = reciprocal_selector(determinant, selector_table)
    sdf_delta_x = float32(
        next(vertex[4] for vertex in main if vertex[0] == maximum_x)
        - sdf_x
    )
    sdf_delta_y = float32(
        next(vertex[5] for vertex in main if vertex[1] == maximum_y)
        - sdf_y
    )
    source_delta_x = float32(
        next(vertex[6] for vertex in main if vertex[0] == maximum_x)
        - source_x
    )
    source_delta_y = float32(
        next(vertex[7] for vertex in main if vertex[1] == maximum_y)
        - source_y
    )
    sdf_internal = (
        internal_slope(
            sdf_delta_x,
            opposite_edge=height,
            determinant=determinant,
            reciprocal_index=reciprocal_index,
        ),
        internal_slope(
            sdf_delta_y,
            opposite_edge=width,
            determinant=determinant,
            reciprocal_index=reciprocal_index,
        ),
    )
    source_internal = (
        internal_slope(
            source_delta_x,
            opposite_edge=height,
            determinant=determinant,
            reciprocal_index=reciprocal_index,
        ),
        internal_slope(
            source_delta_y,
            opposite_edge=width,
            determinant=determinant,
            reciprocal_index=reciprocal_index,
        ),
    )
    sdf_split = tuple(split_internal_slope(value) for value in sdf_internal)
    source_split = tuple(
        split_internal_slope(value) for value in source_internal
    )
    if len(main) != 6:
        raise ValueError("the coordinate gate requires two explicit triangles")
    sdf_anchors = (main[2], main[4])
    source_anchors = sdf_anchors
    physical_source_constants = [
        physical_plane_constant(
            anchor[6 + axis],
            (source_delta_x, source_delta_y)[axis],
            anchor[axis],
            tile * 32,
            opposite_edge=(height, width)[axis],
            determinant=determinant,
            reciprocal_index=reciprocal_index,
        )
        for anchor in source_anchors
        for tile in range(32)
        for axis in range(2)
    ]
    return {
        "name": str(geometry["name"]),
        "geometryWidth": width,
        "geometryHeight": height,
        "sdfCenter": [
            float(round(float(geometry["centerX"]))),
            float(round(float(geometry["centerY"]))),
        ],
        "sourceOrigin": [float(origin_x), float(origin_y)],
        "sourceExtent": [float(virtual_width), float(virtual_height)],
        "sourceInverseExtent": [
            float32(1.0 / virtual_width),
            float32(1.0 / virtual_height),
        ],
        "sourceAnchorPosition": [minimum_x, minimum_y],
        "sourceAnchorValue": [source_x, source_y],
        "sourceDelta": [source_delta_x, source_delta_y],
        "sdfAnchorPositions": [
            value
            for vertex in sdf_anchors
            for value in vertex[0:2]
        ],
        "sdfAnchorValues": [
            value
            for vertex in sdf_anchors
            for value in vertex[4:6]
        ],
        "sourceAnchorPositions": [
            value
            for vertex in source_anchors
            for value in vertex[0:2]
        ],
        "sourceAnchorValues": [
            value
            for vertex in source_anchors
            for value in vertex[6:8]
        ],
        "directSourceSlopeBits": [
            float32_bits(float32(source_delta_x / width)),
            float32_bits(float32(source_delta_y / height)),
        ],
        "rasterDeterminant": determinant,
        "rasterReciprocalIndex": reciprocal_index,
        "sdfSlopeHigh": [value[0] for value in sdf_split],
        "sdfSlopeLow": [value[1] for value in sdf_split],
        "sourceSlopeHigh": [value[0] for value in source_split],
        "sourceSlopeLow": [value[1] for value in source_split],
        "physicalSourceConstants": physical_source_constants,
        "internalSdfSlopeHex": [value.hex() for value in sdf_internal],
        "internalSourceSlopeHex": [
            value.hex() for value in source_internal
        ],
        "sourceOriginRecoveryResidual": residual,
    }


def mismatch_examples(
    reference: np.ndarray,
    candidate: np.ndarray,
    *,
    limit: int = 32,
) -> list[JsonObject]:
    changed = reference != candidate
    coordinates = np.argwhere(np.any(changed, axis=2))
    examples: list[JsonObject] = []
    for y, x in coordinates[:limit]:
        channels = np.flatnonzero(changed[y, x]).tolist()
        examples.append({
            "x": int(x),
            "metalY": int(y),
            "channels": channels,
            "referenceRgba": reference[y, x].tolist(),
            "candidateRgba": candidate[y, x].tolist(),
        })
    return examples


def evaluate_capture(
    capture: Path,
    *,
    shader_source: str,
    intrinsic_table: Path,
    selector_table: np.ndarray,
    slope_offsets: range,
) -> JsonObject:
    parameters = capture_parameters(capture, selector_table)
    reference = bgra_raw(
        capture / "carenderer-live-tree-glass-prefix-reference-bgra8.raw",
        width=CAPTURE_WIDTH,
        height=CAPTURE_HEIGHT,
    )
    measurements: list[JsonObject] = []
    with AppleGlassReferenceRenderer(
        capture,
        fragment_shader_source=shader_source,
        intrinsic_table=intrinsic_table,
        load_interpolant_axis_trace=False,
        load_diagnostic_traces=False,
    ) as renderer:
        configure_recovered_material(renderer)
        renderer.program["CoordinateMode"].value = COORDINATE_MODE
        for name in (
            "GenericSdfCenter",
            "GenericSourceOrigin",
            "GenericSourceExtent",
            "GenericSourceInverseExtent",
            "GenericSourceAnchorPosition",
            "GenericSourceAnchorValue",
            "GenericSdfAnchorPositions",
            "GenericSdfAnchorValues",
            "GenericSourceAnchorPositions",
            "GenericSourceAnchorValues",
            "GenericSdfSlopeHigh",
            "GenericSdfSlopeLow",
            "GenericSourceSlopeHigh",
            "GenericSourceSlopeLow",
        ):
            key = name.removeprefix("Generic")
            key = key[0].lower() + key[1:]
            renderer.program[name].value = tuple(parameters[key])
        renderer.program["GenericPhysicalSourceConstants"].write(
            struct.pack(
                "<128f",
                *parameters["physicalSourceConstants"],
            )
        )

        direct_slopes = parameters["directSourceSlopeBits"]
        for offset in slope_offsets:
            slope_bits = tuple(
                shifted_float_bits(int(bits), offset)
                for bits in direct_slopes
            )
            renderer.program["GenericSourceSlopeBits"].value = slope_bits
            for name, variant in VARIANTS.items():
                if name in {
                    "direct-multiply",
                    "direct-sdf-apple-source",
                    "apple-sdf-direct-source",
                    "direct-divide",
                    "apple-control",
                    "internal-tile-iterator",
                    "direct-sdf-internal-tile-source",
                    "internal-tile-sdf-apple-source",
                    "apple-sdf-internal-tile-source",
                    "physical-rebase-source",
                    "apple-sdf-physical-rebase-source",
                } and offset != 0:
                    continue
                renderer.program["GenericCoordinateVariant"].value = variant
                candidate = renderer.render()
                comparison = compare_images(reference, candidate)
                measurements.append({
                    "variant": name,
                    "sourceSlopeUlpOffset": offset,
                    "sourceSlopeBits": [
                        f"0x{bits:08x}" for bits in slope_bits
                    ],
                    **comparison.as_json(),
                    "mismatchExamples": mismatch_examples(
                        reference,
                        candidate,
                    ),
                })
    portable = [
        value
        for value in measurements
        if value["variant"] not in {
            "direct-sdf-apple-source",
            "apple-sdf-direct-source",
            "apple-control",
            "internal-tile-sdf-apple-source",
            "apple-sdf-internal-tile-source",
            "apple-sdf-physical-rebase-source",
        }
    ]
    best = min(
        portable,
        key=lambda value: (
            int(value["mismatchedBytes"]),
            int(value["mismatchedPixels"]),
            abs(int(value["sourceSlopeUlpOffset"])),
            str(value["variant"]),
        ),
    )
    apple_control = next(
        value for value in measurements if value["variant"] == "apple-control"
    )
    return {
        "capture": str(capture),
        "parameters": parameters,
        "measurements": measurements,
        "bestPortableCandidate": best,
        "appleControlExact": bool(apple_control["exact"]),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("captures", nargs="+", type=Path)
    parser.add_argument(
        "--intrinsic-table",
        type=Path,
        default=Path(
            "artifacts/apple-float-intrinsics-r8-30556057571.bin"
        ),
    )
    parser.add_argument(
        "--shader",
        type=Path,
        default=Path("analysis/apple_glass_reference.frag.glsl"),
    )
    parser.add_argument(
        "--selector-table",
        type=Path,
        default=SELECTOR_TABLE_PATH,
    )
    parser.add_argument("--minimum-slope-offset", type=int, default=-2)
    parser.add_argument("--maximum-slope-offset", type=int, default=4)
    parser.add_argument("-o", "--output", type=Path)
    arguments = parser.parse_args()
    if arguments.minimum_slope_offset > arguments.maximum_slope_offset:
        parser.error("minimum slope offset exceeds maximum slope offset")

    started = time.perf_counter()
    shader_source = instrumented_shader_source(arguments.shader)
    selector_table = load_selector_table(arguments.selector_table)
    captures = [
        evaluate_capture(
            capture,
            shader_source=shader_source,
            intrinsic_table=arguments.intrinsic_table,
            selector_table=selector_table,
            slope_offsets=range(
                arguments.minimum_slope_offset,
                arguments.maximum_slope_offset + 1,
            ),
        )
        for capture in arguments.captures
    ]
    report: JsonObject = {
        "liquidGlassGeometryCoordinateGateSchemaVersion": 1,
        "implementation": {
            "file": "analysis/liquid_glass_geometry_coordinate_gate.py",
            "python": platform.python_version(),
            "numpy": np.__version__,
        },
        "captures": captures,
        "measurement": {
            "captureCount": len(captures),
            "appleControlsExact": all(
                bool(capture["appleControlExact"])
                for capture in captures
            ),
            "portableCandidateExactForEveryCapture": all(
                bool(capture["bestPortableCandidate"]["exact"])
                for capture in captures
            ),
            "elapsedSeconds": time.perf_counter() - started,
        },
        "productionShaderAuthorized": False,
    }
    serialized = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if arguments.output is None:
        print(serialized, end="")
    else:
        arguments.output.write_text(serialized, encoding="utf-8")
        print(arguments.output)
    return 0 if report["measurement"]["appleControlsExact"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
