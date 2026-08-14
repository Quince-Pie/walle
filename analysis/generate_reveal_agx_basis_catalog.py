#!/usr/bin/env python3
"""Generate an output-blind catalog for the AGX reveal coefficient probe.

The catalog contains only public reveal geometry, exact-rational guard-clipped
children, and interior sample coordinates.  It never opens the retained image
corpus.  A Metal probe can redraw each original source primitive with one-hot
vertex varyings and recover the rasterizer-generated coefficient planes at the
listed child samples.
"""

import argparse
import hashlib
import json
import math
import struct
import sys
from fractions import Fraction
from pathlib import Path
from typing import Final


ROOT: Final = Path(__file__).resolve().parent.parent
LG_ANALYSIS: Final = ROOT / "lg-test" / "Analysis"
sys.path[:0] = [str(ROOT / "analysis"), str(LG_ANALYSIS)]

import score_reveal_v74_public_geometry as public_geometry  # noqa: E402
import test_reveal_postguard_children as postguard  # noqa: E402


SAMPLE_COUNT_PER_CHILD: Final = 3

type Vertex = tuple[float, ...]
type Child = tuple[Vertex, Vertex, Vertex]


def _bits(value: float) -> int:
    return struct.unpack("<I", struct.pack("<f", value))[0]


def _fraction(value: float) -> Fraction:
    return Fraction.from_float(value)


def _cross(a: Vertex, b: Vertex, x: Fraction, y: Fraction) -> Fraction:
    return (_fraction(b[0]) - _fraction(a[0])) * (y - _fraction(a[1])) - (
        _fraction(b[1]) - _fraction(a[1])
    ) * (x - _fraction(a[0]))


def _strictly_inside(triangle: Child, x: int, y: int) -> tuple[bool, Fraction]:
    point_x = Fraction(2 * x + 1, 2)
    point_y = Fraction(2 * y + 1, 2)
    edges = tuple(
        _cross(triangle[index], triangle[(index + 1) % 3], point_x, point_y)
        for index in range(3)
    )
    positive = all(value > 0 for value in edges)
    negative = all(value < 0 for value in edges)
    if not positive and not negative:
        return False, Fraction(0)
    area = abs(
        _cross(
            triangle[0],
            triangle[1],
            _fraction(triangle[2][0]),
            _fraction(triangle[2][1]),
        )
    )
    if area == 0:
        return False, Fraction(0)
    return True, min(abs(value) for value in edges) / area


def _row_interval(triangle: Child, y: int) -> tuple[Fraction, Fraction] | None:
    point_y = Fraction(2 * y + 1, 2)
    intersections: list[Fraction] = []
    for index in range(3):
        start = triangle[index]
        end = triangle[(index + 1) % 3]
        start_y = _fraction(start[1])
        end_y = _fraction(end[1])
        if start_y == end_y:
            continue
        if not (min(start_y, end_y) < point_y < max(start_y, end_y)):
            continue
        ratio = (point_y - start_y) / (end_y - start_y)
        intersections.append(
            _fraction(start[0]) + ratio * (_fraction(end[0]) - _fraction(start[0]))
        )
    if len(intersections) < 2:
        return None
    intersections.sort()
    return intersections[0], intersections[-1]


def _candidate_x_values(low: Fraction, high: Fraction) -> tuple[int, ...]:
    midpoint = (low + high) / 2
    values = {
        math.floor(midpoint),
        math.ceil(low - Fraction(1, 2)),
        math.floor(high - Fraction(1, 2)),
    }
    return tuple(sorted(values))


def _sample_pixels(
    triangle: Child,
    *,
    width: int,
    height: int,
    count: int = SAMPLE_COUNT_PER_CHILD,
) -> tuple[tuple[int, int], ...]:
    low_y = max(0, math.floor(min(vertex[1] for vertex in triangle)))
    high_y = min(height - 1, math.ceil(max(vertex[1] for vertex in triangle)) - 1)
    candidates: list[tuple[Fraction, int, int]] = []
    for y in range(low_y, high_y + 1):
        interval = _row_interval(triangle, y)
        if interval is None:
            continue
        for x in _candidate_x_values(*interval):
            if not 0 <= x < width:
                continue
            inside, margin = _strictly_inside(triangle, x, y)
            if inside:
                candidates.append((margin, x, y))
    candidates.sort(key=lambda item: (-item[0], item[2], item[1]))
    selected: list[tuple[int, int]] = []
    selected_tiles: set[tuple[int, int]] = set()
    for _, x, y in candidates:
        tile = (x // 32, y // 32)
        if tile in selected_tiles:
            continue
        selected.append((x, y))
        selected_tiles.add(tile)
        if len(selected) == count:
            return tuple(selected)
    for _, x, y in candidates:
        point = (x, y)
        if point in selected:
            continue
        selected.append(point)
        if len(selected) == count:
            break
    return tuple(selected)


def _vertex_bits(vertex: Vertex, count: int) -> list[int]:
    return [_bits(value) for value in vertex[:count]]


def _source_triangle(
    geometry: public_geometry.RevealGeometry,
    primitive: int,
) -> tuple[Vertex, Vertex, Vertex]:
    selected = tuple(tuple(vertex[:8]) for vertex in geometry.vertices)
    return tuple(
        selected[geometry.indices[primitive * 3 + local]] for local in range(3)
    )  # type: ignore[return-value]


def generate_catalog() -> dict[str, object]:
    cases: list[dict[str, object]] = []
    child_count = 0
    sampled_child_count = 0
    sample_count = 0
    next_record = 0
    per_state_children: list[int] = []
    per_state_sampled: list[int] = []

    for state in range(public_geometry.DEFAULT_STATE_COUNT):
        geometry = public_geometry.construct_state_geometry(state)
        if geometry is None:
            per_state_children.append(0)
            per_state_sampled.append(0)
            continue
        family = 2 if geometry.family == "compact-visible-arcs" else 1
        vertices = tuple(tuple(vertex[:8]) for vertex in geometry.vertices)
        children, metadata, _ = postguard._reference_children(  # noqa: SLF001
            family=family,
            width=public_geometry.DEFAULT_WIDTH,
            height=public_geometry.DEFAULT_HEIGHT,
            vertices=vertices,
            indices=geometry.indices,
        )
        state_child_count = len(children)
        state_sampled_count = 0
        by_primitive: dict[int, list[tuple[int, postguard.ChildBits]]] = {}
        for child_ordinal, (child_bits, child_metadata) in enumerate(
            zip(children, metadata, strict=True)
        ):
            primitive, policy = child_metadata
            if policy != 1:
                raise AssertionError("unexpected post-guard owner policy")
            by_primitive.setdefault(primitive, []).append((child_ordinal, child_bits))

        for primitive, primitive_children in sorted(by_primitive.items()):
            source = _source_triangle(geometry, primitive)
            case_children: list[dict[str, object]] = []
            for ordinal_within_source, (child_ordinal, child_bits) in enumerate(
                primitive_children
            ):
                child = tuple(
                    tuple(postguard._float(word) for word in vertex_bits)  # noqa: SLF001
                    for vertex_bits in child_bits
                )
                samples = _sample_pixels(
                    child,  # type: ignore[arg-type]
                    width=public_geometry.DEFAULT_WIDTH,
                    height=public_geometry.DEFAULT_HEIGHT,
                )
                sample_records = []
                for sample_ordinal, (x, y) in enumerate(samples):
                    sample_records.append(
                        {
                            "recordIndex": next_record,
                            "sampleOrdinal": sample_ordinal,
                            "pixel": [x, y],
                            "tile": [x // 32, y // 32],
                        }
                    )
                    next_record += 1
                if samples:
                    sampled_child_count += 1
                    state_sampled_count += 1
                    sample_count += len(samples)
                case_children.append(
                    {
                        "childOrdinalInState": child_ordinal,
                        "childOrdinalWithinSource": ordinal_within_source,
                        "generatedVertexBits": [list(vertex) for vertex in child_bits],
                        "samples": sample_records,
                    }
                )
            cases.append(
                {
                    "state": state,
                    "family": geometry.family,
                    "sourcePrimitive": primitive,
                    "sourceVertexIndices": list(
                        geometry.indices[primitive * 3 : primitive * 3 + 3]
                    ),
                    "sourceVertexBits": [_vertex_bits(vertex, 8) for vertex in source],
                    "stateScissor": [
                        geometry.scissor.x,
                        geometry.scissor.y,
                        geometry.scissor.width,
                        geometry.scissor.height,
                    ],
                    "children": case_children,
                }
            )
        child_count += state_child_count
        per_state_children.append(state_child_count)
        per_state_sampled.append(state_sampled_count)

    if child_count != 271:
        raise AssertionError(f"expected 271 post-guard children, found {child_count}")
    if sampled_child_count > child_count or sample_count < sampled_child_count:
        raise AssertionError("invalid sample census")

    state_count_encoding = (
        json.dumps(per_state_children, separators=(",", ":")) + "\n"
    ).encode()
    return {
        "schema": "walle-reveal-agx-basis-catalog-v1",
        "authority": {
            "usesPublicRevealInputsOnly": True,
            "opensRetainedGeometry": False,
            "opensReferencePixels": False,
            "generatedChildrenAreHypothesesUntilComparedWithAppleSetup": True,
        },
        "target": {
            "width": public_geometry.DEFAULT_WIDTH,
            "height": public_geometry.DEFAULT_HEIGHT,
            "center": [
                public_geometry.DEFAULT_CENTER_X,
                public_geometry.DEFAULT_CENTER_Y,
            ],
            "stateCount": public_geometry.DEFAULT_STATE_COUNT,
        },
        "census": {
            "sourceCaseCount": len(cases),
            "postGuardChildCount": child_count,
            "sampledChildCount": sampled_child_count,
            "childWithoutVisibleSampleCount": child_count - sampled_child_count,
            "sampleRecordCount": sample_count,
            "maximumSamplesPerChild": SAMPLE_COUNT_PER_CHILD,
            "perStateChildCounts": per_state_children,
            "perStateSampledChildCounts": per_state_sampled,
            "perStateChildCountSHA256": hashlib.sha256(
                state_count_encoding
            ).hexdigest(),
        },
        "cases": cases,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    arguments = parser.parse_args()
    catalog = generate_catalog()
    encoded = (json.dumps(catalog, indent=2, sort_keys=True) + "\n").encode()
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_bytes(encoded)
    print(
        json.dumps(
            {
                "output": str(arguments.output),
                "sha256": hashlib.sha256(encoded).hexdigest(),
                **catalog["census"],  # type: ignore[arg-type]
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
