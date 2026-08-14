#!/usr/bin/env python3
"""Score the recovered top-left-anchor AGX triangle setup hypothesis.

The production scorer already completes every post-guard child whose varying
planes are axis separable.  This analysis overlays only the remaining
arbitrary children.  Their slopes are constructed from public vertex inputs
with the output-blind setup law recovered from direct ``LDCF`` exports:

* quantize positions to 1/256 pixel;
* select the minimum ``(y, x)`` vertex as the subtraction anchor;
* form the other two binary32 deltas;
* run the measured 27-bit partial-product and P25 reciprocal stages.

The plane is evaluated from the selected anchor with binary32 fused multiply
adds.  A reference frame is opened only after the complete candidate exists.
This file is an experiment, not production authority.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import struct
import sys
import time
from fractions import Fraction
from pathlib import Path
from typing import Final

import numpy as np
from numpy.typing import NDArray
from PIL import Image


ROOT: Final = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "analysis"), str(ROOT / "lg-test" / "Analysis")]

import analyze_reveal_agx_clip_setup_split as setup  # noqa: E402
import score_reveal_v74_public_raster as public  # noqa: E402


type Vertex = tuple[float, ...]
type U8Plane = NDArray[np.uint8]
type F32Plane = NDArray[np.float32]


def _bits_float(bits: int) -> float:
    return struct.unpack("<f", struct.pack("<I", bits))[0]


def _subpixel_fixed(value: float) -> int:
    return math.floor(float(np.float32(value)) * 256.0 + 0.5)


def _slope_bits(
    triangle: tuple[Vertex, Vertex, Vertex],
    *,
    component: int,
    axis: int,
    p25_bitmap: bytes,
) -> tuple[int, int, tuple[tuple[int, int], ...]]:
    positions = tuple(
        (_subpixel_fixed(vertex[0]), _subpixel_fixed(vertex[1]))
        for vertex in triangle
    )
    anchor = min(range(3), key=lambda index: (positions[index][1], positions[index][0]))
    values = tuple(setup._float32(vertex[component]) for vertex in triangle)  # noqa: SLF001
    if axis == 0:
        edges = (
            (positions[1][1] - positions[2][1]) / 256.0,
            (positions[2][1] - positions[0][1]) / 256.0,
            (positions[0][1] - positions[1][1]) / 256.0,
        )
    else:
        edges = (
            (positions[2][0] - positions[1][0]) / 256.0,
            (positions[0][0] - positions[2][0]) / 256.0,
            (positions[1][0] - positions[0][0]) / 256.0,
        )
    numerator = sum(
        (
            setup._first_product(  # noqa: SLF001
                setup._float32(values[index] - values[anchor]),  # noqa: SLF001
                edges[index],
                bias_units=15,
            )
            for index in range(3)
            if index != anchor
        ),
        Fraction(),
    )
    normalized = setup._normalize_signed(  # noqa: SLF001
        numerator,
        precision_bits=27,
        rounding="nearest-even",
    )
    slope = setup._reciprocal_product(  # noqa: SLF001
        normalized,
        setup._determinant(positions),  # noqa: SLF001
        p25_bitmap,
    )
    return slope, anchor, positions


def _plane(
    slope_x: float,
    slope_y: float,
    anchor_value: float,
    anchor_x: float,
    anchor_y: float,
    x: NDArray[np.float64],
    y: NDArray[np.float64],
) -> F32Plane:
    # A binary64 product and add exactly contain one binary32 FMA before the
    # explicit binary32 materialization at each nested stage.
    inner = np.asarray(
        (y - anchor_y) * np.float64(slope_y) + np.float64(anchor_value),
        dtype=np.float32,
    )
    return np.asarray(
        (x - anchor_x) * np.float64(slope_x) + inner.astype(np.float64),
        dtype=np.float32,
    )


def _inside_triangle(
    fixed: NDArray[np.int64],
    pixel_x: NDArray[np.int64],
    pixel_y: NDArray[np.int64],
) -> NDArray[np.bool_]:
    edges: list[NDArray[np.int64]] = []
    for first, second in ((0, 1), (1, 2), (2, 0)):
        edges.append(
            (fixed[second, 0] - fixed[first, 0]) * (pixel_y - fixed[first, 1])
            - (fixed[second, 1] - fixed[first, 1]) * (pixel_x - fixed[first, 0])
        )
    determinant = (
        (fixed[1, 0] - fixed[0, 0]) * (fixed[2, 1] - fixed[0, 1])
        - (fixed[1, 1] - fixed[0, 1]) * (fixed[2, 0] - fixed[0, 0])
    )
    if determinant > 0:
        return (edges[0] >= 0) & (edges[1] >= 0) & (edges[2] >= 0)
    return (edges[0] <= 0) & (edges[1] <= 0) & (edges[2] <= 0)


def _overlay_arbitrary_triangle(
    candidate: U8Plane,
    triangle: tuple[Vertex, Vertex, Vertex],
    *,
    scissor: dict[str, int],
    p25_bitmap: bytes,
) -> None:
    fixed = np.asarray(
        [(_subpixel_fixed(vertex[0]), _subpixel_fixed(vertex[1])) for vertex in triangle],
        dtype=np.int64,
    )
    left = max(scissor["x"], int(math.floor(int(fixed[:, 0].min()) / 256)) - 1, 0)
    top = max(scissor["y"], int(math.floor(int(fixed[:, 1].min()) / 256)) - 1, 0)
    right = min(
        scissor["x"] + scissor["width"],
        int(math.ceil(int(fixed[:, 0].max()) / 256)) + 1,
        public.WIDTH,
    )
    bottom = min(
        scissor["y"] + scissor["height"],
        int(math.ceil(int(fixed[:, 1].max()) / 256)) + 1,
        public.HEIGHT,
    )
    if left >= right or top >= bottom:
        return

    yy, xx = np.meshgrid(
        np.arange(top, bottom, dtype=np.int64),
        np.arange(left, right, dtype=np.int64),
        indexing="ij",
    )
    selected = _inside_triangle(fixed, xx * 256 + 128, yy * 256 + 128)
    if not bool(np.any(selected)):
        return

    partner_x = np.where((xx & 1) == 0, xx + 1, xx - 1)
    partner_y = np.where((yy & 1) == 0, yy + 1, yy - 1)
    x = xx.astype(np.float64) + 0.5
    y = yy.astype(np.float64) + 0.5
    x_partner = partner_x.astype(np.float64) + 0.5
    y_partner = partner_y.astype(np.float64) + 0.5

    center_components: list[F32Plane] = []
    x_components: list[F32Plane] = []
    y_components: list[F32Plane] = []
    # Border-family circle coordinates are the second varying pair.  The
    # first pair (components 4/5) is the texture/source coordinate.
    for component in (6, 7):
        slope_x_bits, anchor, positions = _slope_bits(
            triangle,
            component=component,
            axis=0,
            p25_bitmap=p25_bitmap,
        )
        slope_y_bits, anchor_y_index, positions_y = _slope_bits(
            triangle,
            component=component,
            axis=1,
            p25_bitmap=p25_bitmap,
        )
        if anchor_y_index != anchor or positions_y != positions:
            raise AssertionError("triangle setup anchor changed between axes")
        anchor_x = positions[anchor][0] / 256.0
        anchor_y = positions[anchor][1] / 256.0
        slope_x = _bits_float(slope_x_bits)
        slope_y = _bits_float(slope_y_bits)
        anchor_value = setup._float32(triangle[anchor][component])  # noqa: SLF001
        center_components.append(
            _plane(slope_x, slope_y, anchor_value, anchor_x, anchor_y, x, y)
        )
        x_components.append(
            _plane(slope_x, slope_y, anchor_value, anchor_x, anchor_y, x_partner, y)
        )
        y_components.append(
            _plane(slope_x, slope_y, anchor_value, anchor_x, anchor_y, x, y_partner)
        )

    distance = public.reveal.circle_distance(*center_components)
    distance_x = public.reveal.circle_distance(x_components[0], center_components[1])
    distance_y = public.reveal.circle_distance(center_components[0], y_components[1])
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
    ).astype(np.float16).astype(np.float32)
    encoded = np.rint(alpha * np.float32(255)).astype(np.uint8)
    destination = candidate[top:bottom, left:right]
    destination[selected] = encoded[selected]


def render_state(
    state: int,
    *,
    base: tuple[int, ...],
    p25_bitmap: bytes,
) -> tuple[U8Plane, int]:
    candidate, expected_unsupported = public.render_public_state(
        state,
        base=base,
        bitmap=p25_bitmap,
    )
    geometry = public.public_geometry.construct_state_geometry(state)
    if geometry is None or geometry.family != "border-grid":
        return candidate, 0
    record = public._record_from_public_geometry(geometry)  # noqa: SLF001
    indices = record["indices"]
    scissor = record["scissor"]
    if not isinstance(indices, list) or not isinstance(scissor, dict):
        raise TypeError("public raster record has an invalid shape")
    vertices = [tuple(vertex) for vertex in geometry.vertices]
    recovered = 0
    for group in range(min(int(record["indexCount"]) // 6, 4)):
        for offset in (group * 6, group * 6 + 3):
            triangle = [vertices[int(index)] for index in indices[offset : offset + 3]]
            if all(
                public.raster_prototype.GUARD_LOW <= vertex[0] <= public.raster_prototype.GUARD_HIGH
                and public.raster_prototype.GUARD_LOW <= vertex[1] <= public.raster_prototype.GUARD_HIGH
                for vertex in triangle
            ):
                continue
            polygon = public._clip_triangle_preserving_start(triangle)  # noqa: SLF001
            generated = (
                (polygon,)
                if len(polygon) == 3
                else (
                    (polygon[0], polygon[1], polygon[2]),
                    (polygon[0], polygon[2], polygon[3]),
                )
                if len(polygon) == 4
                else ()
            )
            for child in generated:
                child_list = list(child)
                if public.raster_prototype._triangle_area(child_list) == 0:  # noqa: SLF001
                    continue
                try:
                    # Reapplying an already supported owner is byte-idempotent.
                    public.raster_prototype._overlay_triangle(  # noqa: SLF001
                        candidate,
                        child_list,
                        channels=(2, 3),
                        scissor=scissor,
                        base=base,
                        bitmap=p25_bitmap,
                    )
                except ValueError as error:
                    if str(error) not in {
                        "active compact primitive is not a right triangle",
                        "compact-boundary channel 3 is not axis-separable at its edges",
                    }:
                        raise
                    _overlay_arbitrary_triangle(
                        candidate,
                        tuple(child),
                        scissor=scissor,
                        p25_bitmap=p25_bitmap,
                    )
                    recovered += 1
    if recovered != expected_unsupported:
        raise ValueError(
            f"state {state} recovered {recovered} of {expected_unsupported} unsupported children"
        )
    return candidate, recovered


def score(*, state_only: int | None = None) -> dict[str, object]:
    base = public.reveal.raster_arithmetic.load_selector_table()
    p25_bitmap = setup.P25_PATH.read_bytes()
    states = range(public.public_geometry.DEFAULT_STATE_COUNT) if state_only is None else (state_only,)
    frames: list[dict[str, object]] = []
    candidate_hash = hashlib.sha256()
    render_seconds = 0.0
    for state in states:
        started = time.perf_counter()
        candidate, recovered = render_state(state, base=base, p25_bitmap=p25_bitmap)
        render_seconds += time.perf_counter() - started
        candidate_hash.update(candidate.tobytes())

        observed_rgba = np.asarray(
            Image.open(public.DEFAULT_CORPUS / f"frame-{state:04}.png").convert("RGBA")
        )
        if not (
            observed_rgba.shape == (public.HEIGHT, public.WIDTH, 4)
            and np.array_equal(observed_rgba[..., 0], observed_rgba[..., 1])
            and np.array_equal(observed_rgba[..., 0], observed_rgba[..., 2])
            and bool(np.all(observed_rgba[..., 3] == np.uint8(255)))
        ):
            raise ValueError(f"state {state} reference is not opaque grayscale")
        observed = observed_rgba[..., 0]
        signed = candidate.astype(np.int16) - observed.astype(np.int16)
        absolute = np.abs(signed)
        frames.append(
            {
                "state": state,
                "mismatchedPixels": int(np.count_nonzero(signed)),
                "absoluteError": int(absolute.sum()),
                "maximumError": int(absolute.max(initial=0)),
                "recoveredArbitraryChildCount": recovered,
                "positiveDeltaCount": int(np.count_nonzero(signed > 0)),
                "negativeDeltaCount": int(np.count_nonzero(signed < 0)),
            }
        )
    mismatch_counts = [int(frame["mismatchedPixels"]) for frame in frames]
    return {
        "schema": "walle-reveal-agx-top-left-setup-score-v1",
        "authority": {
            "candidateCompletedBeforeObservedFrameOpen": True,
            "opensReferencePixelsOnlyForFinalScore": True,
            "perStateOrPixelCorrectionUsed": False,
            "productionAuthorityGranted": False,
        },
        "states": list(states),
        "candidateInventorySha256": candidate_hash.hexdigest(),
        "mismatchedPixels": sum(mismatch_counts),
        "absoluteError": sum(int(frame["absoluteError"]) for frame in frames),
        "maximumError": max((int(frame["maximumError"]) for frame in frames), default=0),
        "exactFrameCount": sum(count == 0 for count in mismatch_counts),
        "recoveredArbitraryChildCount": sum(
            int(frame["recoveredArbitraryChildCount"]) for frame in frames
        ),
        "perStateMismatchCountSha256": hashlib.sha256(
            (json.dumps(mismatch_counts, separators=(",", ":")) + "\n").encode()
        ).hexdigest(),
        "renderSeconds": render_seconds,
        "frames": frames,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", type=int)
    arguments = parser.parse_args()
    print(json.dumps(score(state_only=arguments.state), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
