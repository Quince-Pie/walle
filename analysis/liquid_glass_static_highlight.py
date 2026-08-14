#!/usr/bin/env python3
"""Construct the static circle's final key-fill/highlight draw inputs."""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Literal

import numpy as np
from numpy.typing import NDArray


type Material = Literal["clear", "regular"]
type Appearance = Literal["light", "dark"]


HIGHLIGHT_INDICES = np.asarray((0, 1, 2, 2, 3, 0), dtype=np.uint16)

VIBRANT_WORDS: dict[Appearance, tuple[int, ...]] = {
    "light": (
        0x3CCF, 0xB4C3, 0xB4C3, 0x0000,
        0xBC01, 0x37FB, 0xBC01, 0x0000,
        0xAE77, 0xAE78, 0x3D98, 0x0000,
        0x0000, 0x0000, 0x0000, 0x3C00,
        0x3B33, 0x3B33, 0x3B33, 0x0000,
        0x3C00, 0x0000, 0x0000, 0x0000,
    ),
    "dark": (
        0x414C, 0xB59C, 0xB59D, 0x0000,
        0xBCB9, 0x3F48, 0xBCB8, 0x0000,
        0xAF9C, 0xAFA1, 0x41C3, 0x0000,
        0x0000, 0x0000, 0x0000, 0x3C00,
        0x30CD, 0x30CD, 0x30CD, 0x0000,
        0x3C00, 0x0000, 0x0000, 0x0000,
    ),
}

SECONDARY_WORDS: dict[tuple[Material, Appearance], tuple[int, ...]] = {
    ("clear", "light"): (
        0x1A6B, 0x2185, 0x3C3F, 0x2CCD,
        0x3BFC, 0xB970, 0xAC62, 0x3A00,
        0xB276, 0x382A, 0xAC64, 0x3A00,
        0xB277, 0xB96F, 0x3C87, 0x3A00,
        0x3A1D, 0xAE0C, 0xA0D9, 0x0000,
        0xA72F, 0x3995, 0xA0E3, 0x0000,
        0xA732, 0xAE0A, 0x3A42, 0x0000,
        0x0000, 0x0000, 0x147B, 0x3EAE,
    ),
    ("clear", "dark"): (
        0x1A6B, 0x2185, 0x3C3F, 0x2CCD,
        0x3BFC, 0xB970, 0xAC62, 0x3A00,
        0xB276, 0x382A, 0xAC64, 0x3A00,
        0xB277, 0xB96F, 0x3C87, 0x3A00,
        0x3A1D, 0xAE0C, 0xA0D9, 0x0000,
        0xA72F, 0x3995, 0xA0E3, 0x0000,
        0xA732, 0xAE0A, 0x3A42, 0x0000,
        0x0000, 0x0000, 0x147B, 0x3EAE,
    ),
    ("regular", "light"): (
        0xABAE, 0xB274, 0x38A3, 0x399A,
        0x3BBB, 0xBA4B, 0xAD14, 0x3B33,
        0xB37C, 0x369D, 0xAD16, 0x3B33,
        0xB37C, 0xBA4B, 0x3C7B, 0x3B33,
        0x3C2C, 0xB5DC, 0xA8B9, 0x0000,
        0xAEF7, 0x3A49, 0xA8BC, 0x0000,
        0xAEF8, 0xB5DC, 0x3C76, 0x0000,
        0x0000, 0x3F80, 0x51EB, 0x3EB8,
    ),
    ("regular", "dark"): (
        0xACE6, 0xB41E, 0x3898, 0x2FAE,
        0x3B26, 0xB5B9, 0xA89C, 0x0000,
        0xAECE, 0x3924, 0xA8A0, 0x0000,
        0xAECE, 0xB5B9, 0x3BB6, 0x0000,
        0x396F, 0xB459, 0xA702, 0x0000,
        0xAD2C, 0x37D0, 0xA707, 0x0000,
        0xAD2C, 0xB459, 0x39DC, 0x0000,
        0x0000, 0x3F80, 0xC28F, 0x3E75,
    ),
}

KEY_FILL_WORDS = (
    0x3C00, 0xBB84, 0x0000, 0xB9A8,
    0xB9A8, 0x3C00, 0xBB84, 0x0000,
    0x39A8, 0x39A8, 0x399A, 0x0000,
    0x3C00, 0x3C00, 0x3C00, 0x3C00,
    0x3C00, 0x3C00, 0x3C00, 0x3C00,
)


@dataclass(frozen=True, slots=True)
class StaticHighlight:
    vertices: NDArray[np.float32]
    indices: NDArray[np.uint16]
    uniform_payload: bytes


def _source_coordinates(material: Material) -> tuple[tuple[float, float], ...]:
    if material == "clear":
        denominator = np.float32(26.0)
        numerators = ((0, 25), (0, 25), (25, 25), (25, 25))
    elif material == "regular":
        denominator = np.float32(176.0)
        numerators = ((29, 140), (35, 140), (135, 140), (141, 140))
    else:
        raise ValueError(f"unsupported material: {material!r}")
    reciprocal = np.float32(np.float32(1.0) / denominator)
    return tuple(
        (
            float(np.float32(np.float32(x) * reciprocal)),
            float(np.float32(np.float32(y) * reciprocal)),
        )
        for x, y in numerators
    )


def _uniform_payload(material: Material, appearance: Appearance) -> bytes:
    if material not in ("clear", "regular"):
        raise ValueError(f"unsupported material: {material!r}")
    if appearance not in ("light", "dark"):
        raise ValueError(f"unsupported appearance: {appearance!r}")

    payload = bytearray(0xF8)
    struct.pack_into(
        "<4f",
        payload,
        0x00,
        400.0,
        400.0,
        4.0,
        0.5 if material == "regular" else 0.0,
    )
    struct.pack_into("<4f", payload, 0x10, 1.0, 0.0, 0.0, 1.0)
    struct.pack_into("<4f", payload, 0x20, 1.0, 1.0, 400.0, 0.0)
    struct.pack_into("<24H", payload, 0x60, *VIBRANT_WORDS[appearance])
    struct.pack_into(
        "<32H",
        payload,
        0x90,
        *SECONDARY_WORDS[(material, appearance)],
    )
    struct.pack_into("<20H", payload, 0xD0, *KEY_FILL_WORDS)
    return bytes(payload)


def build_static_highlight(
    material: Material,
    appearance: Appearance,
) -> StaticHighlight:
    positions = (
        (103.0, 103.0, -409.0, 409.0),
        (921.0, 103.0, 409.0, 409.0),
        (921.0, 921.0, 409.0, -409.0),
        (103.0, 921.0, -409.0, -409.0),
    )
    source = _source_coordinates(material)
    vertices = np.asarray(
        [
            (x, y, 0.0, 1.0, sdf_x, sdf_y, source_u, source_v)
            for (x, y, sdf_x, sdf_y), (source_u, source_v) in zip(
                positions,
                source,
                strict=True,
            )
        ],
        dtype=np.float32,
    )
    return StaticHighlight(
        vertices=vertices,
        indices=HIGHLIGHT_INDICES.copy(),
        uniform_payload=_uniform_payload(material, appearance),
    )
