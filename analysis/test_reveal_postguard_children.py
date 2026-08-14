#!/usr/bin/env python3
"""Audit the bounded C post-guard constructor against exact rational clipping."""

import argparse
import ctypes
import hashlib
import json
import random
import struct
import subprocess
import sys
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
LG_ANALYSIS = ROOT / "lg-test" / "Analysis"
sys.path[:0] = [str(ROOT), str(LG_ANALYSIS)]

import score_reveal_v74_public_geometry as public_geometry  # noqa: E402
import score_reveal_v74_public_raster as public_raster  # noqa: E402


MAX_CHILD_COUNT = 90
EXPECTED_SCORE = {
    "mismatchedPixels": 91,
    "absoluteError": 91,
    "maximumError": 1,
    "completeExactFrameCount": 52,
    "unsupportedPostGuardSetupCount": 95,
    "perStateMismatchCountSHA256": (
        "d6c006d789b551e875555f3e8ef32f0c46c3ec3911802fea405ef9d3458edb5d"
    ),
}

type Vertex = tuple[float, ...]
type ChildBits = tuple[tuple[int, ...], tuple[int, ...], tuple[int, ...]]


@dataclass(frozen=True, slots=True)
class Constructed:
    status: int
    guards: tuple[int, int, int, int]
    children: tuple[ChildBits, ...]
    metadata: tuple[tuple[int, int], ...]


def _bits(value: float) -> int:
    return struct.unpack("<I", struct.pack("<f", value))[0]


def _float(bits: int) -> float:
    return struct.unpack("<f", struct.pack("<I", bits))[0]


def _f32(value: float) -> float:
    return _float(_bits(value))


def _round_positive_fraction(value: Fraction) -> int:
    quotient, remainder = divmod(value.numerator, value.denominator)
    comparison = 2 * remainder - value.denominator
    return quotient + (comparison > 0 or (comparison == 0 and quotient & 1))


def _rounded_fraction(value: Fraction) -> float:
    if value == 0:
        return 0.0
    negative = value < 0
    magnitude = -value if negative else value
    exponent = magnitude.numerator.bit_length() - magnitude.denominator.bit_length()
    if exponent >= 0:
        if magnitude.numerator < magnitude.denominator << exponent:
            exponent -= 1
    elif magnitude.numerator << -exponent < magnitude.denominator:
        exponent -= 1
    if exponent >= -126:
        scale = 23 - exponent
        scaled = (
            Fraction(magnitude.numerator << scale, magnitude.denominator)
            if scale >= 0
            else Fraction(magnitude.numerator, magnitude.denominator << -scale)
        )
        significand = _round_positive_fraction(scaled)
        if significand == 1 << 24:
            significand >>= 1
            exponent += 1
        if exponent > 127:
            raise ValueError("exact fraction overflows binary32")
        bits = (exponent + 127) << 23 | (significand & 0x7FFFFF)
    else:
        significand = _round_positive_fraction(
            Fraction(magnitude.numerator << 149, magnitude.denominator)
        )
        if significand > 1 << 23:
            raise ValueError("subnormal rounding crossed more than one binade")
        bits = significand
    if negative:
        bits |= 0x80000000
    return _float(bits)


class Constructor:
    def __init__(self, library: Path) -> None:
        function = ctypes.CDLL(str(library)).walle_test_postguard_construct
        u32_pointer = ctypes.POINTER(ctypes.c_uint32)
        function.argtypes = [
            ctypes.c_uint8,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_uint32,
            u32_pointer,
            ctypes.POINTER(ctypes.c_uint16),
            ctypes.c_uint32,
            u32_pointer,
            u32_pointer,
            u32_pointer,
            ctypes.POINTER(ctypes.c_uint8),
        ]
        function.restype = ctypes.c_uint32
        self._function = function

    def construct(
        self,
        *,
        family: int,
        width: int,
        height: int,
        vertices: tuple[Vertex, ...],
        indices: tuple[int, ...],
        capacity: int = MAX_CHILD_COUNT,
    ) -> Constructed:
        vertex_words = (ctypes.c_uint32 * max(1, len(vertices) * 8))()
        for vertex_index, vertex in enumerate(vertices):
            if len(vertex) < 8:
                raise ValueError("test vertex has fewer than eight components")
            for component, value in enumerate(vertex[:8]):
                vertex_words[vertex_index * 8 + component] = _bits(value)
        index_words = (ctypes.c_uint16 * max(1, len(indices)))(*indices)
        guard_words = (ctypes.c_uint32 * 4)()
        child_count = ctypes.c_uint32()
        child_words = (ctypes.c_uint32 * (MAX_CHILD_COUNT * 18))()
        metadata_words = (ctypes.c_uint8 * (MAX_CHILD_COUNT * 2))()
        status = self._function(
            family,
            width,
            height,
            len(vertices),
            len(indices),
            vertex_words,
            index_words,
            capacity,
            guard_words,
            ctypes.byref(child_count),
            child_words,
            metadata_words,
        )
        children = tuple(
            tuple(
                tuple(
                    int(child_words[child * 18 + vertex * 6 + component])
                    for component in range(6)
                )
                for vertex in range(3)
            )
            for child in range(child_count.value)
        )
        metadata = tuple(
            (
                int(metadata_words[child * 2]),
                int(metadata_words[child * 2 + 1]),
            )
            for child in range(child_count.value)
        )
        return Constructed(
            status=int(status),
            guards=tuple(int(value) for value in guard_words),  # type: ignore[arg-type]
            children=children,  # type: ignore[arg-type]
            metadata=metadata,
        )


def _guard_value(extent: int, multiplier: int, *, negative: bool = False) -> float:
    value = _rounded_fraction(Fraction(extent * multiplier, 4))
    return -value if negative else value


def _guard_bits(width: int, height: int) -> tuple[int, int, int, int]:
    return (
        _bits(_guard_value(width, 1, negative=True)),
        _bits(_guard_value(width, 5)),
        _bits(_guard_value(height, 1, negative=True)),
        _bits(_guard_value(height, 5)),
    )


def _selected_vertex(vertex: Vertex, family: int) -> Vertex:
    coordinates = vertex[4:6] if family == 2 else vertex[6:8]
    return tuple(vertex[:4]) + tuple(coordinates)


def _intersection(start: Vertex, end: Vertex, *, axis: int, edge: float) -> Vertex:
    start_axis = Fraction.from_float(start[axis])
    end_axis = Fraction.from_float(end[axis])
    fraction = (Fraction.from_float(edge) - start_axis) / (end_axis - start_axis)
    result: list[float] = []
    for component in range(6):
        if component == axis:
            result.append(edge)
            continue
        low = Fraction.from_float(start[component])
        high = Fraction.from_float(end[component])
        result.append(_rounded_fraction(low + fraction * (high - low)))
    return tuple(result)


def _clip_triangle(
    triangle: tuple[Vertex, Vertex, Vertex],
    *,
    width: int,
    height: int,
) -> tuple[Vertex, ...]:
    polygon: tuple[Vertex, ...] = triangle
    planes = (
        (0, _guard_value(width, 1, negative=True), True),
        (0, _guard_value(width, 5), False),
        (1, _guard_value(height, 1, negative=True), True),
        (1, _guard_value(height, 5), False),
    )
    for axis, edge, keep_greater in planes:
        if not polygon:
            break
        clipped: list[Vertex] = []
        previous = polygon[-1]
        previous_inside = (
            previous[axis] >= edge if keep_greater else previous[axis] <= edge
        )
        for current in polygon:
            current_inside = (
                current[axis] >= edge if keep_greater else current[axis] <= edge
            )
            if current_inside:
                if not previous_inside:
                    clipped.append(
                        _intersection(previous, current, axis=axis, edge=edge)
                    )
                clipped.append(current)
            elif previous_inside:
                clipped.append(_intersection(previous, current, axis=axis, edge=edge))
            previous = current
            previous_inside = current_inside
        deduplicated: list[Vertex] = []
        for vertex in clipped:
            if not deduplicated or vertex[:2] != deduplicated[-1][:2]:
                deduplicated.append(vertex)
        if len(deduplicated) > 1 and deduplicated[0][:2] == deduplicated[-1][:2]:
            deduplicated.pop()
        polygon = tuple(deduplicated)
    return polygon


def _area_is_zero(triangle: tuple[Vertex, Vertex, Vertex]) -> bool:
    a, b, c = triangle
    fraction = Fraction.from_float
    area = (fraction(b[0]) - fraction(a[0])) * (fraction(c[1]) - fraction(a[1])) - (
        fraction(b[1]) - fraction(a[1])
    ) * (fraction(c[0]) - fraction(a[0]))
    return area == 0


def _reference_children(
    *,
    family: int,
    width: int,
    height: int,
    vertices: tuple[Vertex, ...],
    indices: tuple[int, ...],
) -> tuple[tuple[ChildBits, ...], tuple[tuple[int, int], ...], int]:
    selected = tuple(_selected_vertex(vertex, family) for vertex in vertices)
    low_x = _guard_value(width, 1, negative=True)
    high_x = _guard_value(width, 5)
    low_y = _guard_value(height, 1, negative=True)
    high_y = _guard_value(height, 5)
    children: list[ChildBits] = []
    metadata: list[tuple[int, int]] = []
    maximum_polygon_vertices = 0
    for primitive in range(len(indices) // 3):
        triangle = tuple(selected[indices[primitive * 3 + local]] for local in range(3))
        if all(
            low_x <= vertex[0] <= high_x and low_y <= vertex[1] <= high_y
            for vertex in triangle
        ):
            continue
        polygon = _clip_triangle(triangle, width=width, height=height)
        maximum_polygon_vertices = max(maximum_polygon_vertices, len(polygon))
        for fan in range(1, len(polygon) - 1):
            child = (polygon[0], polygon[fan], polygon[fan + 1])
            if _area_is_zero(child):
                continue
            children.append(
                tuple(
                    tuple(_bits(component) for component in vertex) for vertex in child
                )
            )
            metadata.append((primitive, 1))
    return tuple(children), tuple(metadata), maximum_polygon_vertices  # type: ignore[return-value]


def _public_vertices(geometry: public_geometry.RevealGeometry) -> tuple[Vertex, ...]:
    return tuple(tuple(vertex[:8]) for vertex in geometry.vertices)


def _serialize_constructed(state: int, value: Constructed) -> bytes:
    payload = bytearray(struct.pack("<II4I", state, len(value.children), *value.guards))
    for child, metadata in zip(value.children, value.metadata, strict=True):
        payload.extend(struct.pack("<BB", *metadata))
        payload.extend(
            struct.pack("<18I", *(word for vertex in child for word in vertex))
        )
    return bytes(payload)


def audit_corpus_children(constructor: Constructor) -> dict[str, object]:
    digest = hashlib.sha256()
    child_counts: list[int] = []
    maximum_polygon_vertices = 0
    for state in range(public_geometry.DEFAULT_STATE_COUNT):
        geometry = public_geometry.construct_state_geometry(state)
        if geometry is None:
            child_counts.append(0)
            continue
        family = 2 if geometry.family == "compact-visible-arcs" else 1
        vertices = _public_vertices(geometry)
        constructed = constructor.construct(
            family=family,
            width=public_geometry.DEFAULT_WIDTH,
            height=public_geometry.DEFAULT_HEIGHT,
            vertices=vertices,
            indices=geometry.indices,
        )
        expected, expected_metadata, polygon_vertices = _reference_children(
            family=family,
            width=public_geometry.DEFAULT_WIDTH,
            height=public_geometry.DEFAULT_HEIGHT,
            vertices=vertices,
            indices=geometry.indices,
        )
        if constructed.status != 0:
            raise AssertionError(
                f"state {state}: constructor status {constructed.status}"
            )
        if constructed.guards != _guard_bits(2_048, 2_048):
            raise AssertionError(f"state {state}: guard bits differ")
        if (
            constructed.children != expected
            or constructed.metadata != expected_metadata
        ):
            raise AssertionError(f"state {state}: child bitstream differs")
        if any(policy != 1 for _, policy in constructed.metadata):
            raise AssertionError(f"state {state}: child owner policy differs")
        digest.update(_serialize_constructed(state, constructed))
        child_counts.append(len(constructed.children))
        maximum_polygon_vertices = max(maximum_polygon_vertices, polygon_vertices)
    count_encoding = (json.dumps(child_counts, separators=(",", ":")) + "\n").encode()
    return {
        "stateCount": len(child_counts),
        "totalChildCount": sum(child_counts),
        "maximumChildrenInOneState": max(child_counts),
        "maximumClippedPolygonVertexCount": maximum_polygon_vertices,
        "perStateChildCounts": child_counts,
        "perStateChildCountSHA256": hashlib.sha256(count_encoding).hexdigest(),
        "orderedChildBitstreamSHA256": digest.hexdigest(),
        "allOwnerPoliciesChildScopedCenterFallback": True,
    }


def _synthetic_vertex(
    x: float,
    y: float,
    component_bits: tuple[int, int, int, int],
) -> Vertex:
    return (
        _f32(x),
        _f32(y),
        _float(component_bits[0]),
        _float(component_bits[1]),
        0.0,
        0.0,
        _float(component_bits[2]),
        _float(component_bits[3]),
    )


def audit_synthetic_extents(constructor: Constructor) -> dict[str, object]:
    rng = random.Random(0x57414C4C45)
    extents = (
        (1, 1),
        (3, 5),
        (7, 9),
        (511, 513),
        (2_049, 3_073),
        (16_777_217, 16_777_219),
        (2**32 - 1, 2**32 - 3),
    )
    component_pool = (
        0x00000000,
        0x80000000,
        0x00000001,
        0x007FFFFF,
        0x00800000,
        0x3F800000,
        0xBF800000,
        0x7F7FFFFF,
        0xFF7FFFFF,
    )
    digest = hashlib.sha256()
    case_count = 0
    maximum_polygon_vertices = 0
    for width, height in extents:
        for case in range(256):
            vertices = tuple(
                _synthetic_vertex(
                    rng.uniform(-1.75, 2.75) * width,
                    rng.uniform(-1.75, 2.75) * height,
                    tuple(rng.choice(component_pool) for _ in range(4)),  # type: ignore[arg-type]
                )
                for _ in range(3)
            )
            indices = (0, 1, 2, 0, 0, 0)
            constructed = constructor.construct(
                family=1,
                width=width,
                height=height,
                vertices=vertices,
                indices=indices,
            )
            expected, expected_metadata, polygon_vertices = _reference_children(
                family=1,
                width=width,
                height=height,
                vertices=vertices,
                indices=indices,
            )
            if constructed.status != 0:
                raise AssertionError(
                    f"synthetic {width}x{height} case {case}: status {constructed.status}"
                )
            if constructed.guards != _guard_bits(width, height):
                raise AssertionError(f"synthetic {width}x{height}: guard bits differ")
            if (
                constructed.children != expected
                or constructed.metadata != expected_metadata
            ):
                raise AssertionError(
                    f"synthetic {width}x{height} case {case}: child bits differ"
                )
            digest.update(_serialize_constructed(case_count, constructed))
            maximum_polygon_vertices = max(maximum_polygon_vertices, polygon_vertices)
            case_count += 1

    seven_vertex_triangle = (
        _synthetic_vertex(
            2.989964485168457, -2.5969796180725098, (0, 0x3F800000, 0, 0)
        ),
        _synthetic_vertex(0.4485771954059601, 1.823648452758789, (0, 0x3F800000, 0, 0)),
        _synthetic_vertex(
            -0.6626309752464294, 0.8036954402923584, (0, 0x3F800000, 0, 0)
        ),
    )
    repeated_indices = (0, 1, 2) * 18
    capacity = constructor.construct(
        family=1,
        width=1,
        height=1,
        vertices=seven_vertex_triangle,
        indices=repeated_indices,
    )
    expected, expected_metadata, polygon_vertices = _reference_children(
        family=1,
        width=1,
        height=1,
        vertices=seven_vertex_triangle,
        indices=repeated_indices,
    )
    if capacity.status != 0 or len(capacity.children) != MAX_CHILD_COUNT:
        raise AssertionError("maximum-capacity construction did not emit 90 children")
    if capacity.children != expected or capacity.metadata != expected_metadata:
        raise AssertionError("maximum-capacity construction differs")
    rejected = constructor.construct(
        family=1,
        width=1,
        height=1,
        vertices=seven_vertex_triangle,
        indices=repeated_indices,
        capacity=MAX_CHILD_COUNT - 1,
    )
    if rejected.status != 4:
        raise AssertionError("undersized consumer capacity was not rejected")
    maximum_polygon_vertices = max(maximum_polygon_vertices, polygon_vertices)

    six_then_noop = (
        _synthetic_vertex(
            1.4145907163619995, 0.06496373564004898, (0, 0x3F800000, 0, 0)
        ),
        _synthetic_vertex(
            0.2548840343952179, 0.6997402906417847, (0, 0x3F800000, 0, 0)
        ),
        _synthetic_vertex(
            -0.6580536961555481, -0.8710532188415527, (0, 0x3F800000, 0, 0)
        ),
    )
    noop_constructed = constructor.construct(
        family=1,
        width=1,
        height=1,
        vertices=six_then_noop,
        indices=(0, 1, 2, 0, 0, 0),
    )
    noop_expected, noop_metadata, noop_polygon_vertices = _reference_children(
        family=1,
        width=1,
        height=1,
        vertices=six_then_noop,
        indices=(0, 1, 2, 0, 0, 0),
    )
    if noop_polygon_vertices != 6 or len(noop_constructed.children) != 4:
        raise AssertionError("six-vertex polygon before a no-op top plane differs")
    if (
        noop_constructed.status != 0
        or noop_constructed.children != noop_expected
        or noop_constructed.metadata != noop_metadata
    ):
        raise AssertionError("six-vertex no-op-plane construction differs")
    return {
        "extentCount": len(extents),
        "randomTriangleCount": case_count,
        "extents": [list(extent) for extent in extents],
        "maximumClippedPolygonVertexCount": maximum_polygon_vertices,
        "maximumCapacityChildCount": len(capacity.children),
        "undersizedConsumerStatus": rejected.status,
        "sevenVertexFinalPlaneFanVerified": True,
        "sixVertexThenNoOpPlaneVerified": True,
        "orderedSyntheticBitstreamSHA256": digest.hexdigest(),
    }


def audit_rounding_vectors(constructor: Constructor) -> dict[str, object]:
    vectors = (
        ("negative-half-minsub-to-negative-zero", 0x80000001, 0x00000000, 0x80000000),
        ("positive-half-minsub-to-positive-zero", 0x00000001, 0x00000000, 0x00000000),
        ("subnormal-tie-even-up", 0x00000001, 0x00000002, 0x00000002),
        ("subnormal-tie-even-down", 0x00000002, 0x00000003, 0x00000002),
        ("negative-subnormal-tie-even", 0x80000001, 0x80000002, 0x80000002),
        ("exact-signed-cancellation", 0x80000001, 0x00000001, 0x00000000),
        ("normal-tie-even-down", 0x3F800000, 0x3F800001, 0x3F800000),
        ("normal-tie-even-up", 0x3F800001, 0x3F800002, 0x3F800002),
    )
    observed: dict[str, str] = {}
    guard_left = _guard_bits(1, 1)[0]
    for name, outside_bits, inside_bits, expected_bits in vectors:
        vertices = (
            _synthetic_vertex(-0.75, 0.1, (0, 0x3F800000, outside_bits, 0)),
            _synthetic_vertex(0.25, 0.1, (0, 0x3F800000, inside_bits, 0)),
            _synthetic_vertex(0.25, 0.9, (0, 0x3F800000, inside_bits, 0)),
        )
        constructed = constructor.construct(
            family=1,
            width=1,
            height=1,
            vertices=vertices,
            indices=(0, 1, 2, 0, 0, 0),
        )
        expected, expected_metadata, _ = _reference_children(
            family=1,
            width=1,
            height=1,
            vertices=vertices,
            indices=(0, 1, 2, 0, 0, 0),
        )
        if constructed.status != 0 or constructed.children != expected:
            raise AssertionError(f"rounding vector {name}: exact oracle differs")
        if constructed.metadata != expected_metadata:
            raise AssertionError(f"rounding vector {name}: metadata differs")
        intersection_bits = {
            vertex[4]
            for child in constructed.children
            for vertex in child
            if vertex[0] == guard_left
        }
        if intersection_bits != {expected_bits}:
            raise AssertionError(
                f"rounding vector {name}: {intersection_bits!r} != {{{expected_bits:#x}}}"
            )
        observed[name] = f"{expected_bits:08x}"
    return {
        "vectorCount": len(vectors),
        "observedComponentBits": observed,
        "negativeUnderflowPreservesSign": True,
        "exactCancellationCanonicalizesToPositiveZero": True,
    }


def audit_validation_vectors(constructor: Constructor) -> dict[str, int]:
    valid_vertex = ((-1.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0),)
    zero_extent = constructor.construct(
        family=1,
        width=0,
        height=1,
        vertices=valid_vertex,
        indices=(0, 0, 0, 0, 0, 0),
    ).status
    nonfinite = constructor.construct(
        family=1,
        width=1,
        height=1,
        vertices=((float("nan"), 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0),),
        indices=(0, 0, 0, 0, 0, 0),
    ).status
    out_of_range_index = constructor.construct(
        family=1,
        width=1,
        height=1,
        vertices=valid_vertex,
        indices=(1, 1, 1, 1, 1, 1),
    ).status
    malformed_index_count = constructor.construct(
        family=1,
        width=1,
        height=1,
        vertices=valid_vertex,
        indices=(0, 0, 0),
    ).status
    nonempty_empty_family = constructor.construct(
        family=0,
        width=1,
        height=1,
        vertices=valid_vertex,
        indices=(0, 0, 0, 0, 0, 0),
    ).status
    empty = constructor.construct(
        family=0,
        width=1,
        height=1,
        vertices=(),
        indices=(),
    ).status
    expected = {
        "zeroExtentStatus": 2,
        "nonfiniteVertexStatus": 2,
        "outOfRangeIndexStatus": 2,
        "malformedIndexCountStatus": 2,
        "nonemptyEmptyFamilyStatus": 2,
        "validEmptyFamilyStatus": 0,
    }
    observed = {
        "zeroExtentStatus": zero_extent,
        "nonfiniteVertexStatus": nonfinite,
        "outOfRangeIndexStatus": out_of_range_index,
        "malformedIndexCountStatus": malformed_index_count,
        "nonemptyEmptyFamilyStatus": nonempty_empty_family,
        "validEmptyFamilyStatus": empty,
    }
    if observed != expected:
        raise AssertionError(f"validation status vectors differ: {observed!r}")
    return observed


def score_with_c_children(constructor: Constructor) -> dict[str, object]:
    original_clip = public_raster._clip_triangle_preserving_start

    def c_clip(triangle: list[Vertex]) -> list[Vertex]:
        constructed = constructor.construct(
            family=1,
            width=public_geometry.DEFAULT_WIDTH,
            height=public_geometry.DEFAULT_HEIGHT,
            vertices=tuple(tuple(vertex[:8]) for vertex in triangle),
            indices=(0, 1, 2, 0, 0, 0),
        )
        if constructed.status != 0:
            raise AssertionError(f"scoring constructor status {constructed.status}")
        if not constructed.children:
            return []
        first = constructed.children[0]
        polygon_bits = [first[0], first[1]]
        polygon_bits.extend(child[2] for child in constructed.children)
        return [
            tuple(_float(word) for word in vertex[:4])
            + (0.0, 0.0)
            + tuple(_float(word) for word in vertex[4:6])
            for vertex in polygon_bits
        ]

    public_raster._clip_triangle_preserving_start = c_clip
    try:
        score = public_raster.score_public_raster()
    finally:
        public_raster._clip_triangle_preserving_start = original_clip
    for key, expected in EXPECTED_SCORE.items():
        if score[key] != expected:
            raise AssertionError(f"score field {key}: {score[key]!r} != {expected!r}")
    return {key: score[key] for key in EXPECTED_SCORE} | {
        "exactPixels": score["exactPixels"],
        "totalPixels": score["totalPixels"],
        "exactPixelPercentage": score["exactPixelPercentage"],
        "candidateCompletedBeforeObservedFrameOpen": score[
            "candidateCompletedBeforeObservedFrameOpen"
        ],
    }


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_library(output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "gcc",
            "-std=c23",
            "-O2",
            "-fPIC",
            "-shared",
            "-Wall",
            "-Wextra",
            "-Wpedantic",
            "-Wconversion",
            "-Wsign-conversion",
            "-Wshadow",
            "-Wformat=2",
            "-Wundef",
            "-Wstrict-prototypes",
            "-Wmissing-prototypes",
            "-Werror",
            "-fno-strict-aliasing",
            "analysis/reveal_postguard_children.c",
            "analysis/reveal_postguard_children_test_shim.c",
            "-o",
            str(output),
        ],
        cwd=ROOT,
        check=True,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--library", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--skip-score", action="store_true")
    arguments = parser.parse_args()
    library = arguments.library or (
        ROOT / "build" / "analysis-postguard" / "libreveal_postguard_children.so"
    )
    if arguments.library is None:
        build_library(library)
    constructor = Constructor(library)
    report = {
        "schemaVersion": 1,
        "classification": (
            "analysis-only general-resolution canonical post-guard child constructor"
        ),
        "guardDefinition": "NDC [-1.5,+1.5] maps to [-extent/4,5*extent/4] per axis",
        "intersectionArithmetic": "exact binary32 rational with software RNE",
        "hostLongDoubleUsed": False,
        "generalResolutionAuthority": (
            "mathematical exactness only; retained Apple reveal captures are 2048x2048"
        ),
        "ownerPolicy": (
            "child center locks the slot for all three samples; an active in-bounds XOR "
            "partner re-evaluates within that child, otherwise it falls back to the center "
            "primitive"
        ),
        "corpus": audit_corpus_children(constructor),
        "synthetic": audit_synthetic_extents(constructor),
        "roundingVectors": audit_rounding_vectors(constructor),
        "validationVectors": audit_validation_vectors(constructor),
        "score": None if arguments.skip_score else score_with_c_children(constructor),
        "sourceSHA256": {
            path.name: _sha256(path)
            for path in (
                ROOT / "analysis" / "reveal_postguard_children.h",
                ROOT / "analysis" / "reveal_postguard_children.c",
                ROOT / "analysis" / "reveal_postguard_children_test_shim.h",
                ROOT / "analysis" / "reveal_postguard_children_test_shim.c",
                Path(__file__),
            )
        },
    }
    encoded = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if arguments.output is None:
        print(encoded, end="")
    else:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(encoded, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
