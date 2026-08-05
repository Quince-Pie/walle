#!/usr/bin/env python3
"""Generate Apple's observed circular transition draw geometry."""

from dataclasses import dataclass

import numpy as np

from apple_glass_reference_renderer import DrawGeometry


@dataclass(frozen=True, slots=True)
class TransitionGeometry:
    remaining: float
    removed: float
    effect_origin: tuple[float, float]
    effect_extent: float
    effect_center: tuple[float, float]
    metal_effect_center: tuple[float, float]
    main: DrawGeometry
    shadow: DrawGeometry


def _source_axis_mapping(
    vertices: np.ndarray,
    *,
    sdf_component: int,
    source_component: int,
) -> tuple[float, float]:
    coordinates = vertices[:, sdf_component].astype(np.float64)
    source = vertices[:, source_component].astype(np.float64)
    low = float(coordinates.min())
    high = float(coordinates.max())
    if not low < high:
        raise ValueError("template SDF axis has no extent")
    low_values = source[coordinates == low]
    high_values = source[coordinates == high]
    if not low_values.size or not high_values.size:
        raise ValueError("template source endpoints are absent")
    source_low = float(low_values[0])
    source_high = float(high_values[0])
    if not (np.all(low_values == source_low) and np.all(high_values == source_high)):
        raise ValueError("template source endpoints are inconsistent")
    slope = (source_high - source_low) / (high - low)
    intercept = source_low - slope * low
    return slope, intercept


def _transform_vertices(
    template: np.ndarray,
    *,
    base_half_extent: float,
    effect_half_extent: float,
    effect_center: tuple[float, float],
    source_x: tuple[float, float],
    source_y: tuple[float, float],
) -> np.ndarray:
    vertices = template.copy()
    sdf = template[:, 4:6].astype(np.float64)
    absolute = np.abs(sdf)
    extension = np.maximum(absolute - base_half_extent, 0.0)
    transformed_sdf = np.copysign(
        effect_half_extent + extension,
        sdf,
    )
    interior = absolute < base_half_extent
    transformed_sdf[interior] = sdf[interior] * effect_half_extent / base_half_extent
    vertices[:, 0] = effect_center[0] + transformed_sdf[:, 0]
    vertices[:, 1] = effect_center[1] - transformed_sdf[:, 1]
    vertices[:, 4:6] = transformed_sdf
    vertices[:, 6] = template[:, 6] + source_x[0] * (transformed_sdf[:, 0] - sdf[:, 0])
    vertices[:, 7] = template[:, 7] + source_y[0] * (transformed_sdf[:, 1] - sdf[:, 1])
    return np.ascontiguousarray(vertices, dtype=np.float32)


def transition_circle_geometry(
    *,
    main_template: DrawGeometry,
    shadow_template: DrawGeometry,
    diameter: float,
    requested_center: tuple[float, float],
    window_extent: tuple[float, float],
    remaining: float,
) -> TransitionGeometry:
    if (
        not 0.0 <= remaining <= 1.0
        or diameter <= 0.0
        or any(value <= 0.0 for value in window_extent)
    ):
        raise ValueError("invalid circular transition geometry")
    removed = 1.0 - remaining
    window_center = (
        0.5 * window_extent[0],
        0.5 * window_extent[1],
    )
    outer_origin = (
        window_center[0] - 0.5 * diameter * remaining,
        window_center[1] - 0.5 * diameter * remaining,
    )
    snapped_inset = round(0.5 * diameter * removed)
    relative_origin = (
        round(requested_center[0]) - window_center[0] - snapped_inset - 8.0 * removed,
        round(requested_center[1]) - window_center[1] - snapped_inset - 8.0 * removed,
    )
    effect_origin = (
        outer_origin[0] + relative_origin[0],
        outer_origin[1] + relative_origin[1],
    )
    effect_extent = diameter + 16.0 * removed
    effect_half_extent = 0.5 * effect_extent
    effect_center = (
        effect_origin[0] + effect_half_extent,
        effect_origin[1] + effect_half_extent,
    )
    metal_effect_center = (
        effect_center[0],
        window_extent[1] - effect_center[1],
    )

    main_vertices = main_template.vertices
    base_half_extent = float(np.max(np.abs(main_vertices[:, 4:6])))
    if not np.isclose(base_half_extent, 0.5 * diameter):
        raise ValueError("template SDF extent does not match transition diameter")
    source_x = _source_axis_mapping(
        main_vertices,
        sdf_component=4,
        source_component=6,
    )
    source_y = _source_axis_mapping(
        main_vertices,
        sdf_component=5,
        source_component=7,
    )

    def transform(template: DrawGeometry) -> DrawGeometry:
        return DrawGeometry(
            vertices=_transform_vertices(
                template.vertices,
                base_half_extent=base_half_extent,
                effect_half_extent=effect_half_extent,
                effect_center=metal_effect_center,
                source_x=source_x,
                source_y=source_y,
            ),
            indices=(template.indices.copy() if template.indices is not None else None),
        )

    return TransitionGeometry(
        remaining=remaining,
        removed=removed,
        effect_origin=effect_origin,
        effect_extent=effect_extent,
        effect_center=effect_center,
        metal_effect_center=metal_effect_center,
        main=transform(main_template),
        shadow=transform(shadow_template),
    )
