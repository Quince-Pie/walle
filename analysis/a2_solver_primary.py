#!/usr/bin/env python3
"""Per-pixel PRIMARY f16 for a reveal state, from walle's public CPU model.

The public raster scorer (lg-test/Analysis/score_reveal_v74_public_raster.py)
renders bytes; the plane solve needs the binary16 alpha BEFORE the second-stage
multiply, so this module reproduces the same two code paths (scissor body and
post-guard border overlay) while retaining half bits.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Final

import numpy as np

ROOT: Final = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "analysis"), str(ROOT / "lg-test/Analysis")]

import liquid_glass_runtime_raster_coefficients as raster  # noqa: E402


class SelectorTableOverride:
    """Measured selector table with one determinant's entry replaced.

    The raster module reads a selector table through len() and one index only;
    the p25 bitmap supplies the entry for the quad actually being set up.  The
    prototype scripts still ask for this helper by its old module path.
    """

    __slots__ = ("_base", "_index", "_selector")

    def __init__(self, base: tuple[int, ...], index: int, selector: int) -> None:
        self._base = base
        self._index = index
        self._selector = selector

    def __len__(self) -> int:
        return len(self._base)

    def __getitem__(self, index: int) -> int:
        return self._selector if index == self._index else self._base[index]


if not hasattr(raster, "SelectorTableOverride"):
    raster.SelectorTableOverride = SelectorTableOverride  # type: ignore[attr-defined]

import _analyze_reveal_captured_a2_geometry as prototype  # noqa: E402
import _analyze_reveal_raster_trace as reveal  # noqa: E402
import _analyze_reveal_second_stage as second_stage  # noqa: E402
import score_reveal_v74_public_geometry as public_geometry  # noqa: E402

WIDTH: Final = public_geometry.DEFAULT_WIDTH
HEIGHT: Final = public_geometry.DEFAULT_HEIGHT
CORPUS: Final = (
    ROOT
    / "artifacts/liquid-glass-reveal-coverage-01421a3-v1/capture/sweeps"
    / "sweep__wallpaper-reveal__regular__dark"
)


def _render_body(
    vertices: list[tuple[float, ...]],
    indices: tuple[int, ...],
    *,
    scissor: tuple[int, int, int, int],
    base: tuple[int, ...],
    bitmap: bytes,
) -> tuple[np.ndarray, np.ndarray]:
    """second_stage.render_primary_half, also returning the binary32 alpha."""
    crop_left, crop_top, crop_width, crop_height = scissor
    result = np.zeros((crop_height, crop_width), dtype=np.uint16)
    exact = np.zeros((crop_height, crop_width), dtype=np.float32)
    covered = np.zeros(result.shape, dtype=np.bool_)
    for draw in range(9):
        draw_indices = indices[draw * 6 : draw * 6 + 6]
        draw_vertices = [vertices[index] for index in draw_indices]
        try:
            quad = raster.runtime_quad_from_vertices(
                draw_vertices,
                name=f"circle-draw-{draw}",
            )
        except ValueError:
            continue
        table = reveal.selector_table_for_quad(quad, base, bitmap)
        left, top, right, bottom = raster.visible_pixel_bounds(quad.case)
        target_left = max(left, crop_left)
        target_top = max(top, crop_top)
        target_right = min(right, crop_left + crop_width)
        target_bottom = min(bottom, crop_top + crop_height)
        if target_left >= target_right or target_top >= target_bottom:
            continue
        xs = np.arange(target_left, target_right, dtype=np.uint32)
        ys = np.arange(target_top, target_bottom, dtype=np.uint32)
        yy, xx = np.meshgrid(ys, xs, indexing="ij")
        primitives = raster.primitive_ids(quad, xx, yy)
        half_bits = np.empty(xx.shape, dtype=np.uint16)
        alpha_bits = np.empty(xx.shape, dtype=np.float32)
        for primitive in (0, 1):
            selected = primitives == primitive
            if not np.any(selected):
                continue
            x_partners = np.where((xx & 1) == 0, xx + 1, xx - 1)
            y_partners = np.where((yy & 1) == 0, yy + 1, yy - 1)
            x_start = min(target_left, int(x_partners.min()))
            x_end = max(target_right, int(x_partners.max()) + 1)
            y_start = min(target_top, int(y_partners.min()))
            y_end = max(target_bottom, int(y_partners.max()) + 1)
            x_values = raster.coordinate_axis_bits(
                quad,
                channel=2,
                primitive=primitive,
                coordinates=range(x_start, x_end),
                selector_table=table,
            ).view(np.float32)
            y_values = raster.coordinate_axis_bits(
                quad,
                channel=3,
                primitive=primitive,
                coordinates=range(y_start, y_end),
                selector_table=table,
            ).view(np.float32)
            px = x_values[xx - x_start]
            py = y_values[yy - y_start]
            partner_x = x_values[x_partners - x_start]
            partner_y = y_values[y_partners - y_start]
            distance = reveal.circle_distance(px, py)
            distance_x = reveal.circle_distance(partner_x, py)
            distance_y = reveal.circle_distance(px, partner_y)
            feather = np.maximum(
                np.asarray(
                    np.abs(distance_x - distance) + np.abs(distance_y - distance),
                    dtype=np.float32,
                ),
                np.float32(1e-4),
            )
            alpha = np.clip(
                np.asarray(
                    (np.float32(1) - distance) / feather + np.float32(0.5),
                    dtype=np.float32,
                ),
                0,
                1,
            )
            half_bits[selected] = alpha.astype(np.float16).view(np.uint16)[selected]
            alpha_bits[selected] = alpha[selected]
        destination_x = slice(target_left - crop_left, target_right - crop_left)
        destination_y = slice(target_top - crop_top, target_bottom - crop_top)
        result[destination_y, destination_x] = half_bits
        exact[destination_y, destination_x] = alpha_bits
        covered[destination_y, destination_x] = True
    if not np.all(covered):
        raise ValueError("circle mesh left uncovered pixels")
    return result, exact


def _overlay_triangle_half(
    half: np.ndarray,
    written: np.ndarray,
    triangle: list[tuple[float, ...]],
    *,
    exact: np.ndarray | None = None,
    channels: tuple[int, int],
    scissor: dict[str, int],
    base: tuple[int, ...],
    bitmap: bytes,
) -> None:
    """prototype._overlay_triangle, retaining binary16 alpha instead of bytes."""
    quad = prototype._completed_quad(triangle, active_ordinal=0)
    active_primitive = prototype._active_geometric_primitive(quad, triangle)
    selector_table = reveal.selector_table_for_quad(quad, base, bitmap)
    left, top, right, bottom = raster.visible_pixel_bounds(quad.case)
    target_left = max(left, scissor["x"])
    target_top = max(top, scissor["y"])
    target_right = min(right, scissor["x"] + scissor["width"])
    target_bottom = min(bottom, scissor["y"] + scissor["height"])
    if target_left >= target_right or target_top >= target_bottom:
        return

    xs = np.arange(target_left, target_right, dtype=np.uint32)
    ys = np.arange(target_top, target_bottom, dtype=np.uint32)
    yy, xx = np.meshgrid(ys, xs, indexing="ij")
    selected = raster.primitive_ids(quad, xx, yy) == active_primitive
    if not np.any(selected):
        return
    partner_xs = np.where((xx & 1) == 0, xx + 1, xx - 1)
    partner_ys = np.where((yy & 1) == 0, yy + 1, yy - 1)
    x_start = min(target_left, int(partner_xs.min()))
    x_end = max(target_right, int(partner_xs.max()) + 1)
    y_start = min(target_top, int(partner_ys.min()))
    y_end = max(target_bottom, int(partner_ys.max()) + 1)
    x_values = raster.coordinate_axis_bits(
        quad,
        channel=channels[0],
        primitive=active_primitive,
        coordinates=range(x_start, x_end),
        selector_table=selector_table,
    ).view(np.float32)
    y_values = raster.coordinate_axis_bits(
        quad,
        channel=channels[1],
        primitive=active_primitive,
        coordinates=range(y_start, y_end),
        selector_table=selector_table,
    ).view(np.float32)
    px = x_values[xx - x_start]
    py = y_values[yy - y_start]
    partner_x = x_values[partner_xs - x_start]
    partner_y = y_values[partner_ys - y_start]
    distance = reveal.circle_distance(px, py)
    distance_x = reveal.circle_distance(partner_x, py)
    distance_y = reveal.circle_distance(px, partner_y)
    feather = np.maximum(
        np.asarray(
            np.abs(distance_x - distance) + np.abs(distance_y - distance),
            dtype=np.float32,
        ),
        np.float32(1e-4),
    )
    alpha32 = np.clip(
        np.asarray(
            (np.float32(1) - distance) / feather + np.float32(0.5),
            dtype=np.float32,
        ),
        0,
        1,
    )
    alpha = alpha32.astype(np.float16)
    bits = alpha.view(np.uint16)
    window = np.s_[target_top:target_bottom, target_left:target_right]
    destination = half[window]
    destination[selected] = bits[selected]
    flag = written[window]
    flag[selected] = True
    if exact is not None:
        exact_window = exact[window]
        exact_window[selected] = alpha32[selected]


def _overlay_border_guard_half(
    half: np.ndarray,
    written: np.ndarray,
    indices: list[int],
    scissor: dict[str, int],
    vertices: list[tuple[float, ...]],
    *,
    exact: np.ndarray | None = None,
    base: tuple[int, ...],
    bitmap: bytes,
) -> int:
    unsupported = 0
    for group in range(min(len(indices) // 6, 4)):
        offset = group * 6
        first = [vertices[int(index)] for index in indices[offset : offset + 3]]
        second = [vertices[int(index)] for index in indices[offset + 3 : offset + 6]]
        for triangle in (first, second):
            if all(
                prototype.GUARD_LOW <= vertex[0] <= prototype.GUARD_HIGH
                and prototype.GUARD_LOW <= vertex[1] <= prototype.GUARD_HIGH
                for vertex in triangle
            ):
                continue
            import score_reveal_v74_public_raster as public_raster

            clipped = public_raster._clip_triangle_preserving_start(triangle)
            generated = (
                (clipped,)
                if len(clipped) == 3
                else (
                    (clipped[0], clipped[1], clipped[2]),
                    (clipped[0], clipped[2], clipped[3]),
                )
                if len(clipped) == 4
                else ()
            )
            for generated_triangle in generated:
                triangle_list = list(generated_triangle)
                if prototype._triangle_area(triangle_list) == 0:
                    continue
                try:
                    _overlay_triangle_half(
                        half,
                        written,
                        triangle_list,
                        exact=exact,
                        channels=(2, 3),
                        scissor=scissor,
                        base=base,
                        bitmap=bitmap,
                    )
                except ValueError as error:
                    if str(error) not in {
                        "active compact primitive is not a right triangle",
                        (
                            "compact-boundary channel 3 is not axis-separable "
                            "at its edges"
                        ),
                    }:
                        raise
                    unsupported += 1
    return unsupported


def render_state_half(
    state: int,
    *,
    base: tuple[int, ...],
    bitmap: bytes,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, int]:
    """Return (binary16 alpha bits, covered mask, binary32 alpha, unsupported)."""
    geometry = public_geometry.construct_state_geometry(state)
    half = np.zeros((HEIGHT, WIDTH), dtype=np.uint16)
    covered = np.zeros((HEIGHT, WIDTH), dtype=np.bool_)
    exact = np.zeros((HEIGHT, WIDTH), dtype=np.float32)
    if geometry is None:
        return half, covered, exact, 0
    if geometry.family != "border-grid":
        raise NotImplementedError(f"state {state} family {geometry.family}")
    scissor = geometry.scissor
    if scissor.width == 0 or scissor.height == 0:
        return half, covered, exact, 0
    vertices = [tuple(vertex) for vertex in geometry.vertices]
    body, body_exact = _render_body(
        vertices,
        geometry.indices,
        scissor=(scissor.x, scissor.y, scissor.width, scissor.height),
        base=base,
        bitmap=bitmap,
    )
    window = np.s_[
        scissor.y : scissor.y + scissor.height,
        scissor.x : scissor.x + scissor.width,
    ]
    half[window] = body
    exact[window] = body_exact
    covered[window] = True
    unsupported = _overlay_border_guard_half(
        half,
        covered,
        list(geometry.indices),
        {
            "x": scissor.x,
            "y": scissor.y,
            "width": scissor.width,
            "height": scissor.height,
        },
        vertices,
        exact=exact,
        base=base,
        bitmap=bitmap,
    )
    return half, covered, exact, unsupported


def load_tables() -> tuple[tuple[int, ...], bytes]:
    return (
        reveal.raster_arithmetic.load_selector_table(),
        reveal.P25_BITMAP.read_bytes(),
    )


def observed_frame(state: int) -> np.ndarray:
    from PIL import Image

    rgba = np.asarray(Image.open(CORPUS / f"frame-{state:04}.png").convert("RGBA"))
    return np.ascontiguousarray(rgba[..., 0])


def packed_bytes(half: np.ndarray, multiplier_bits: int) -> np.ndarray:
    return second_stage.packed(half, multiplier_bits)


def _main(argv: list[str]) -> int:
    states = [int(value) for value in argv[1:]] or [42]
    base, bitmap = load_tables()
    for state in states:
        half, covered, _, unsupported = render_state_half(
            state, base=base, bitmap=bitmap
        )
        candidate = np.where(covered, packed_bytes(half, 0x3C00), np.uint8(0))
        observed = observed_frame(state)
        delta = candidate.astype(np.int16) - observed.astype(np.int16)
        mismatched = int(np.count_nonzero(delta))
        print(
            f"state {state}: mismatched={mismatched} unsupported={unsupported} "
            f"max|d|={int(np.abs(delta).max())}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
