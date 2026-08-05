#!/usr/bin/env python3
"""Reproduce Apple's Liquid Glass transition color matrices bit-for-bit."""

from __future__ import annotations

import math
import struct
from collections.abc import Mapping, Sequence
from typing import Any


type ColorMatrix = tuple[float, ...]
type JsonObject = dict[str, Any]

APPLE_MATRIX_BASIS_HEX = (
    "d0b3593e5917373f98dd933d000000000000000068b3eabd2653c5be0000003f"
    "000000000000003f0000003fe78ce8bec8983bbd000000000000003f00000000"
    "00000000000000000000803f000000000000803f000000000c93c93f00000000"
    "0c9349bf0000803fddd13fbef3adefbe0000000071cba73e0000803f4d84ed3f"
    "00000000000000004d846dbf0000000000000000000000000000803f00000000"
)
APPLE_MATRIX_BASIS_SHA256 = (
    "2e7c891ab05c7c09bfb80bfedf648cb7ab2a61a54934c61db8c064300bc1d6c4"
)
APPLE_MATRIX_BASIS_BYTES = bytes.fromhex(APPLE_MATRIX_BASIS_HEX)
(
    APPLE_RGB_TO_LUMA_CHROMA,
    APPLE_LUMA_CHROMA_TO_RGB,
) = (
    struct.unpack_from("<20f", APPLE_MATRIX_BASIS_BYTES, offset)
    for offset in (0, 80)
)

MATRIX_FIELD_GROUPS = (
    (
        "face",
        "inputFaceColorMatrix",
        (
            "face_matrix_0",
            "face_matrix_1",
            "face_matrix_2",
        ),
    ),
    (
        "bleed",
        "inputBleedColorMatrix",
        (
            "bleed_matrix_0",
            "bleed_matrix_1",
            "bleed_matrix_2",
        ),
    ),
    (
        "shadow",
        "inputShadowColorMatrix",
        (
            "shadow_matrix_0",
            "shadow_matrix_1",
            "shadow_matrix_2",
        ),
    ),
)
PACKED_RGB_ROWS = (
    (0, 1, 2, 4),
    (5, 6, 7, 9),
    (10, 11, 12, 14),
)


def float32(value: float | int) -> float:
    """Round one value to IEEE-754 binary32."""

    return struct.unpack("<f", struct.pack("<f", value))[0]


def float32_bits(value: float | int) -> int:
    return struct.unpack("<I", struct.pack("<f", value))[0]


def float32_add(left: float, right: float) -> float:
    return float32(float32(left) + float32(right))


def float32_subtract(left: float, right: float) -> float:
    return float32(float32(left) - float32(right))


def float32_multiply(left: float, right: float) -> float:
    return float32(float32(left) * float32(right))


def float32_fma(
    left: float,
    right: float,
    addend: float,
) -> float:
    return float32(
        math.fma(float32(left), float32(right), float32(addend))
    )


def concatenate_color_matrices(
    left: Sequence[float],
    right: Sequence[float],
) -> ColorMatrix:
    """Match QuartzCore's captured scalar ARM64 concatenation sequence."""

    if len(left) != 20 or len(right) != 20:
        raise ValueError("color matrices must contain exactly 20 floats")
    result = [0.0] * 20
    for row in range(4):
        row_offset = row * 5
        for column in range(4):
            accumulator = float32_multiply(
                left[row_offset + 3],
                right[15 + column],
            )
            for inner in (2, 1, 0):
                accumulator = float32_fma(
                    left[row_offset + inner],
                    right[inner * 5 + column],
                    accumulator,
                )
            result[row_offset + column] = accumulator

        accumulator = float32_fma(
            left[row_offset + 3],
            right[19],
            left[row_offset + 4],
        )
        for inner in (2, 1, 0):
            accumulator = float32_fma(
                left[row_offset + inner],
                right[inner * 5 + 4],
                accumulator,
            )
        result[row_offset + 4] = accumulator
    return tuple(result)


def construct_color_matrix(
    *,
    white: float,
    black: float,
    saturation: float,
    premultiplied_fill: Sequence[float],
) -> ColorMatrix:
    """Match QuartzCore's private GlassBackgroundFilter constructor."""

    if len(premultiplied_fill) != 4:
        raise ValueError("premultiplied fill must contain RGBA")

    affine = [0.0] * 20
    affine[0] = float32_subtract(white, black)
    affine[4] = float32(black)
    affine[6] = 1.0
    affine[12] = 1.0
    affine[18] = 1.0

    saturation32 = float32(saturation)
    chroma_offset = float32(
        math.fma(-float(saturation32), 0.5, 0.5)
    )
    chroma = [0.0] * 20
    chroma[0] = 1.0
    chroma[6] = saturation32
    chroma[9] = chroma_offset
    chroma[12] = saturation32
    chroma[14] = chroma_offset
    chroma[18] = 1.0

    matrix = concatenate_color_matrices(
        affine,
        APPLE_RGB_TO_LUMA_CHROMA,
    )
    matrix = concatenate_color_matrices(chroma, matrix)
    matrix = concatenate_color_matrices(
        APPLE_LUMA_CHROMA_TO_RGB,
        matrix,
    )

    fill = tuple(float32(value) for value in premultiplied_fill)
    inverse_alpha = float32_subtract(1.0, fill[3])
    result = [
        float32_multiply(value, inverse_alpha)
        for value in matrix
    ]
    for offset, component in zip(
        (4, 9, 14, 19),
        fill,
        strict=True,
    ):
        result[offset] = float32_add(result[offset], component)
    return tuple(result)


def _numeric(values: Mapping[str, Any], key: str) -> float:
    value = values.get(key)
    if not isinstance(value, int | float) or isinstance(value, bool):
        raise ValueError(f"filter input {key} is not numeric")
    return float(value)


def _premultiplied_fill(
    value: object,
    *,
    alpha_addend: float = 0.0,
) -> tuple[float, float, float, float]:
    if value is None:
        components = (0.0, 0.0, 0.0, 0.0)
    elif isinstance(value, Mapping):
        candidate = value.get("components")
        if (
            not isinstance(candidate, list)
            or len(candidate) != 4
            or any(
                not isinstance(component, int | float)
                or isinstance(component, bool)
                for component in candidate
            )
        ):
            raise ValueError("matrix fill color components differ")
        components = tuple(float(component) for component in candidate)
    else:
        raise ValueError("matrix fill color is not serialized")

    alpha = float32(components[3])
    return (
        float32_multiply(components[0], alpha),
        float32_multiply(components[1], alpha),
        float32_multiply(components[2], alpha),
        float32_add(alpha, alpha_addend),
    )


def _matrix_for_prefix(
    values: Mapping[str, Any],
    prefix: str,
    *,
    alpha_addend: float = 0.0,
) -> ColorMatrix:
    return construct_color_matrix(
        white=_numeric(values, f"{prefix}White"),
        black=_numeric(values, f"{prefix}Black"),
        saturation=_numeric(values, f"{prefix}Saturation"),
        premultiplied_fill=_premultiplied_fill(
            values.get(f"{prefix}FillColor"),
            alpha_addend=alpha_addend,
        ),
    )


def packed_matrix_half_words(
    matrix: Sequence[float],
) -> tuple[tuple[int, int, int, int], ...]:
    if len(matrix) != 20:
        raise ValueError("color matrix must contain exactly 20 floats")
    return tuple(
        tuple(
            struct.unpack("<H", struct.pack("<e", matrix[index]))[0]
            for index in row
        )
        for row in PACKED_RGB_ROWS
    )


def expected_matrix_field_bits(
    values: Mapping[str, Any],
) -> JsonObject:
    """Return Apple's nine packed matrix rows as binary16 bit strings."""

    expected: JsonObject = {}
    for kind, prefix, field_names in MATRIX_FIELD_GROUPS:
        alpha_addend = (
            _numeric(values, "inputSDRShadowOpacity")
            if kind == "shadow"
            else 0.0
        )
        rows = packed_matrix_half_words(
            _matrix_for_prefix(
                values,
                prefix,
                alpha_addend=alpha_addend,
            )
        )
        expected.update({
            field: [f"0x{word:04x}" for word in row]
            for field, row in zip(field_names, rows, strict=True)
        })
    return expected
