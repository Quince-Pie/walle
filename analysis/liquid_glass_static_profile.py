#!/usr/bin/env python3
"""Construct Apple's static circle profile without replaying its buffer."""

from __future__ import annotations

import ctypes
import math
import struct
from dataclasses import dataclass
from typing import Literal


type Material = Literal["clear", "regular"]
type Appearance = Literal["light", "dark"]

PROFILE_BYTE_COUNT = 258


@dataclass(frozen=True, slots=True)
class StaticProfileRequest:
    material: Material
    appearance: Appearance
    width: float
    height: float
    source_virtual_width: int
    source_virtual_height: int


@dataclass(frozen=True, slots=True)
class _MatrixAttributes:
    white: float
    black: float
    saturation: float
    fill: tuple[float, float, float, float]


@dataclass(frozen=True, slots=True)
class _EndpointProfile:
    face: _MatrixAttributes
    bleed: _MatrixAttributes
    shadow: _MatrixAttributes
    edge_opacity: float
    bleed_darken: tuple[float, float]
    shadow_face_opacity: float


def _float32(value: float) -> float:
    return struct.unpack("<f", struct.pack("<f", value))[0]


def _rounded_add(left: float, right: float) -> float:
    return _float32(_float32(left) + _float32(right))


def _rounded_subtract(left: float, right: float) -> float:
    return _float32(_float32(left) - _float32(right))


def _rounded_multiply(left: float, right: float) -> float:
    return _float32(_float32(left) * _float32(right))


def _rounded_divide(left: float, right: float) -> float:
    return _float32(_float32(left) / _float32(right))


_fmaf = ctypes.CDLL(None).fmaf
_fmaf.argtypes = (ctypes.c_float, ctypes.c_float, ctypes.c_float)
_fmaf.restype = ctypes.c_float


def _fma32(left: float, right: float, addend: float) -> float:
    return float(_fmaf(left, right, addend))


_RGB_TO_YCBCR = tuple(map(_float32, (
    0.2126, 0.7152, 0.0722, 0.0, 0.0,
    -0.1146, -0.3854, 0.5, 0.0, 0.5,
    0.5, -0.4542, -0.0458, 0.0, 0.5,
    0.0, 0.0, 0.0, 1.0, 0.0,
)))

_YCBCR_TO_RGB = tuple(map(_float32, (
    1.0, 0.0, 1.5748, 0.0, -0.7874,
    1.0, -0.187324, -0.468124, 0.0, 0.327724,
    1.0, 1.8556, 0.0, 0.0, -0.9278,
    0.0, 0.0, 0.0, 1.0, 0.0,
)))


def _multiply_color_matrices(
    left: list[float] | tuple[float, ...],
    right: list[float] | tuple[float, ...],
) -> list[float]:
    result = [0.0] * 20
    for row in range(4):
        row_offset = row * 5
        for column in range(4):
            accumulator = _rounded_multiply(
                left[row_offset + 3],
                right[15 + column],
            )
            for index in range(2, -1, -1):
                accumulator = _fma32(
                    left[row_offset + index],
                    right[index * 5 + column],
                    accumulator,
                )
            result[row_offset + column] = accumulator

        accumulator = _rounded_multiply(left[row_offset + 3], right[19])
        for index in range(2, -1, -1):
            accumulator = _fma32(
                left[row_offset + index],
                right[index * 5 + 4],
                accumulator,
            )
        result[row_offset + 4] = _rounded_add(
            accumulator,
            left[row_offset + 4],
        )
    return result


def _color_matrix(attributes: _MatrixAttributes) -> tuple[float, ...]:
    luminance = [0.0] * 20
    luminance[0] = _rounded_subtract(attributes.white, attributes.black)
    luminance[4] = _float32(attributes.black)
    luminance[6] = luminance[12] = luminance[18] = 1.0

    saturation = [0.0] * 20
    saturation[0] = saturation[18] = 1.0
    saturation[6] = saturation[12] = _float32(attributes.saturation)
    offset = _float32(0.5 - float(saturation[6]) * 0.5)
    saturation[9] = saturation[14] = offset

    matrix = _multiply_color_matrices(luminance, _RGB_TO_YCBCR)
    matrix = _multiply_color_matrices(saturation, matrix)
    matrix = _multiply_color_matrices(_YCBCR_TO_RGB, matrix)

    scale = _rounded_subtract(1.0, attributes.fill[3])
    matrix = [_rounded_multiply(value, scale) for value in matrix]
    for row, component in enumerate(attributes.fill):
        matrix[row * 5 + 4] = _rounded_add(
            matrix[row * 5 + 4],
            component,
        )
    return tuple(
        matrix[row * 5 + column]
        for row in range(3)
        for column in (0, 1, 2, 4)
    )


def _endpoint_profile(
    material: Material,
    appearance: Appearance,
) -> _EndpointProfile:
    sdr_shadow = _float32(0.24)
    if material == "clear":
        shadow_face_opacity = _rounded_add(0.1, sdr_shadow)
        return _EndpointProfile(
            face=_MatrixAttributes(1.15, 0.075, 1.06, (0.0,) * 4),
            bleed=_MatrixAttributes(1.0, 0.75, 1.2, (0.0,) * 4),
            shadow=_MatrixAttributes(
                1.0,
                0.0,
                1.2,
                (0.0, 0.0, 0.0, shadow_face_opacity),
            ),
            edge_opacity=0.0,
            bleed_darken=(1.0, 0.0),
            shadow_face_opacity=shadow_face_opacity,
        )
    if material != "regular":
        raise ValueError(f"unsupported material: {material!r}")
    if appearance == "light":
        shadow_face_opacity = _rounded_add(0.12, sdr_shadow)
        return _EndpointProfile(
            face=_MatrixAttributes(1.03, 0.5, 1.0, (0.4,) * 4),
            bleed=_MatrixAttributes(1.0, 0.9, 1.2, (0.0,) * 4),
            shadow=_MatrixAttributes(
                1.0,
                0.0,
                1.8,
                (0.0, 0.0, 0.0, shadow_face_opacity),
            ),
            edge_opacity=0.5,
            bleed_darken=(1.0, 0.0),
            shadow_face_opacity=shadow_face_opacity,
        )
    if appearance == "dark":
        return _EndpointProfile(
            face=_MatrixAttributes(0.6, 0.2, 1.0, (0.0, 0.0, 0.0, 0.4)),
            bleed=_MatrixAttributes(0.5, 0.0, 1.0, (0.0,) * 4),
            shadow=_MatrixAttributes(0.5, 0.0, 1.0, (0.0, 0.0, 0.0, 0.24)),
            edge_opacity=0.8,
            bleed_darken=(-1.0, 1.0),
            shadow_face_opacity=_float32(0.24),
        )
    raise ValueError(f"unsupported appearance: {appearance!r}")


def canonical_static_profile_request(
    material: Material,
    appearance: Appearance,
) -> StaticProfileRequest:
    virtual_extent = 1536 if material == "regular" else 896
    return StaticProfileRequest(
        material=material,
        appearance=appearance,
        width=800.0,
        height=800.0,
        source_virtual_width=virtual_extent,
        source_virtual_height=virtual_extent,
    )


def build_static_profile(request: StaticProfileRequest) -> bytes:
    if request.material not in ("clear", "regular"):
        raise ValueError(f"unsupported material: {request.material!r}")
    if request.appearance not in ("light", "dark"):
        raise ValueError(f"unsupported appearance: {request.appearance!r}")
    if (
        not math.isfinite(request.width)
        or not math.isfinite(request.height)
        or request.width <= 0.0
        or request.height <= 0.0
        or request.source_virtual_width <= 0
        or request.source_virtual_height <= 0
    ):
        raise ValueError("profile geometry must have positive finite extents")

    regular = request.material == "regular"
    endpoint = _endpoint_profile(request.material, request.appearance)
    half_width = _rounded_multiply(request.width, 0.5)
    half_height = _rounded_multiply(request.height, 0.5)
    radius = min(half_width, half_height)
    outer_amount = _rounded_multiply(radius, 0.4)
    bleed_amount = _rounded_multiply(radius, 0.7) if regular else 0.0
    shadow_height = _rounded_multiply(radius, 0.8)

    payload = bytearray(PROFILE_BYTE_COUNT)

    def floats(offset: int, *values: float) -> None:
        struct.pack_into(
            f"<{len(values)}f",
            payload,
            offset,
            *map(_float32, values),
        )

    def halves(offset: int, *values: float) -> None:
        struct.pack_into(f"<{len(values)}e", payload, offset, *values)

    floats(0, half_width, half_height, 4.0, 0.5 if regular else 0.0)
    floats(16, 1.0, 0.0, 0.0, 1.0)
    floats(32, 1.0, 1.0, radius, 0.0)
    floats(
        48,
        _rounded_divide(1.0, request.source_virtual_width),
        0.0,
        0.0,
        -_rounded_divide(1.0, request.source_virtual_height),
    )
    floats(64, -60.0)
    floats(68, _rounded_divide(1.0, 20.0))
    floats(72, outer_amount)
    floats(76, _rounded_divide(4.0, radius))
    floats(80, -1.0)
    floats(84, 0.0)
    floats(88, 1.6 if regular else 0.8)
    floats(92, 32.0 if regular else 0.0)
    floats(96, bleed_amount)
    floats(
        100,
        _rounded_divide(1.0, bleed_amount) if regular else math.inf,
    )
    floats(104, 75.0)
    floats(108, _rounded_divide(1.0, shadow_height))
    floats(112, 0.0, -8.0)
    floats(120, 8.0 if regular else 0.0)
    floats(124, _rounded_divide(1.0, 24.0) if regular else math.inf)
    halves(128, *_color_matrix(endpoint.face))
    halves(152, *_color_matrix(endpoint.bleed))
    halves(176, *_color_matrix(endpoint.shadow))
    floats(200, 1.0 if regular else 0.0)
    floats(204, endpoint.shadow_face_opacity)
    halves(208, 1.0, 0.5, 0.0, -0.5)
    halves(216, -radius, -1.0, 0.0, 0.0)
    halves(224, 1.0, 0.0)
    halves(228, endpoint.edge_opacity)
    halves(230, 1.0)
    halves(232, *endpoint.bleed_darken)
    halves(236, 0.0)
    halves(238, 0.25 if regular else 0.0)
    halves(240, 0.3 if regular else 0.0)
    halves(242, 1.0)
    halves(244, -2.0, -1.0)
    clamp_limit = max(
        1.0,
        math.ceil(_float32(endpoint.face.white) * 32.0) / 32.0,
    )
    halves(248, clamp_limit)
    halves(250, 0.0)
    halves(252, 0.97)
    halves(254, 0.0)
    halves(256, 1.0)
    return bytes(payload)
