#!/usr/bin/env python3
"""Generate the static circle draw meshes without replaying Apple buffers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np
from numpy.typing import NDArray

from liquid_glass_geometry_transfer import _source_coordinate, float32


type Material = Literal["clear", "regular"]


SHADOW_INDICES = np.asarray(
    (
        0, 1, 5, 5, 4, 0,
        3, 7, 6, 6, 2, 3,
        10, 11, 15, 15, 14, 10,
        9, 13, 12, 12, 8, 9,
        1, 2, 6, 6, 5, 1,
        4, 5, 9, 9, 8, 4,
        6, 7, 11, 11, 10, 6,
        9, 10, 14, 14, 13, 9,
    ),
    dtype=np.uint16,
)


@dataclass(frozen=True, slots=True)
class StaticCircleGeometryRequest:
    material: Material
    center_x: float
    center_y: float
    width: float
    height: float
    source_origin_x: int
    source_origin_y: int
    source_virtual_width: int
    source_virtual_height: int


@dataclass(frozen=True, slots=True)
class StaticCircleGeometry:
    main_vertices: NDArray[np.float32]
    shadow_vertices: NDArray[np.float32]
    shadow_indices: NDArray[np.uint16]


def canonical_static_circle_geometry_request(
    material: Material,
) -> StaticCircleGeometryRequest:
    if material == "clear":
        source_origin = 104
        virtual_extent = 896
    elif material == "regular":
        source_origin = -256
        virtual_extent = 1536
    else:
        raise ValueError(f"unsupported material: {material!r}")
    return StaticCircleGeometryRequest(
        material=material,
        center_x=512.0,
        center_y=512.0,
        width=800.0,
        height=800.0,
        source_origin_x=source_origin,
        source_origin_y=source_origin,
        source_virtual_width=virtual_extent,
        source_virtual_height=virtual_extent,
    )


def _vertex(
    request: StaticCircleGeometryRequest,
    x: float,
    y: float,
) -> tuple[float, float, float, float, float, float, float, float]:
    return (
        float32(x),
        float32(y),
        0.0,
        1.0,
        float32(x - request.center_x),
        float32(request.center_y - y),
        _source_coordinate(
            x,
            origin=request.source_origin_x,
            extent=request.source_virtual_width,
        ),
        _source_coordinate(
            y,
            origin=request.source_origin_y,
            extent=request.source_virtual_height,
        ),
    )


def build_static_circle_geometry(
    request: StaticCircleGeometryRequest,
) -> StaticCircleGeometry:
    if request.material not in ("clear", "regular"):
        raise ValueError(f"unsupported material: {request.material!r}")
    if (
        request.width <= 0.0
        or request.height <= 0.0
        or request.source_virtual_width <= 0
        or request.source_virtual_height <= 0
    ):
        raise ValueError("geometry extents must be positive")

    half_width = request.width / 2.0
    half_height = request.height / 2.0
    left = request.center_x - half_width
    right = request.center_x + half_width
    bottom = request.center_y - half_height
    top = request.center_y + half_height
    main_positions = (
        (left, top),
        (right, top),
        (right, bottom),
        (right, bottom),
        (left, bottom),
        (left, top),
    )
    main = np.asarray(
        [_vertex(request, x, y) for x, y in main_positions],
        dtype=np.float32,
    )

    if request.material == "clear":
        x_values = (left, left, right, right)
        y_values = (top, top, bottom, bottom - 8.0)
    else:
        x_values = (left - 48.0, left, right, right + 48.0)
        y_values = (top + 40.0, top, bottom, bottom - 56.0)
    shadow = np.asarray(
        [
            _vertex(request, x, y)
            for y in y_values
            for x in x_values
        ],
        dtype=np.float32,
    )
    return StaticCircleGeometry(
        main_vertices=main,
        shadow_vertices=shadow,
        shadow_indices=SHADOW_INDICES.copy(),
    )
