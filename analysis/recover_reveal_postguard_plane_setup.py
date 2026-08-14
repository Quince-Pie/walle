#!/usr/bin/env python3
"""Recover Apple post-guard varying slopes without consulting output pixels.

The retained setup pulls contain sixteen subpixel samples for every active
primitive/tile/axis.  This program joins those samples to the table-free
public reveal geometry and its canonical Sutherland-Hodgman children, then
tests exact plane-equation hypotheses against the recovered slope intervals.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
import sys
from collections import defaultdict
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Final

import numpy as np
from numpy.typing import NDArray


ROOT: Final = Path(__file__).resolve().parent.parent
LG_ANALYSIS: Final = ROOT / "lg-test" / "Analysis"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(LG_ANALYSIS))

import _analyze_reveal_captured_a2_geometry as retained_raster  # noqa: E402
import _analyze_reveal_raster_trace as agx  # noqa: E402
import score_reveal_v74_public_geometry as public_geometry  # noqa: E402
import score_reveal_v74_public_raster as public_raster  # noqa: E402


type Vertex = tuple[float, ...]
type JsonObject = dict[str, object]
type PullWords = NDArray[np.uint32]

PULL_AXIS_COUNT: Final = 2
PULL_PRIMITIVE_COUNT: Final = 32
PULL_TILE_COUNT: Final = 64
PULL_PHASE_COUNT: Final = 16
PULL_COMPONENT_COUNT: Final = 4
PULL_RECORD_WORD_COUNT: Final = 3 + PULL_PHASE_COUNT * PULL_COMPONENT_COUNT
TILE_SIZE: Final = 32
INACTIVE_RECORD: Final = 0xFFFF_FFFF
CLAIMED_RECORD: Final = 0xFFFF_FFFE
SDF_COMPONENTS: Final = (2, 3)
VERTEX_SDF_COMPONENTS: Final = (6, 7)


@dataclass(frozen=True, slots=True)
class Child:
    state: int
    source_primitive: int
    fan_ordinal: int
    vertices: tuple[Vertex, Vertex, Vertex]


@dataclass(frozen=True, slots=True)
class PullRecord:
    state: int
    axis: int
    source_primitive: int
    tile: int
    pixel_x: int
    pixel_y: int
    values: tuple[tuple[int, int, int, int], ...]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _float_bits(value: float) -> int:
    return struct.unpack("<I", struct.pack("<f", value))[0]


def _fraction(bits: int) -> Fraction:
    return agx.raster_arithmetic.float32_bits_fraction(bits)


def _round_fraction(value: Fraction) -> int:
    if value == 0:
        return 0
    sign = 0x8000_0000 if value < 0 else 0
    magnitude = abs(value)
    minimum_normal = Fraction(1, 1 << 126)
    if magnitude < minimum_normal:
        scaled = magnitude * (1 << 149)
        significand = agx.raster_arithmetic.round_fraction_to_integer_nearest_even(
            scaled
        )
        if not 0 <= significand <= 1 << 23:
            raise AssertionError("subnormal rounding escaped the binary32 range")
        return sign | significand
    return agx.raster_arithmetic.round_fraction_to_float32_bits(value)


def _state_directories(root: Path) -> list[tuple[int, Path]]:
    states: list[tuple[int, Path]] = []
    for directory in root.glob("state-*"):
        try:
            state = int(directory.name.removeprefix("state-"))
        except ValueError:
            continue
        if (directory / "reveal-a2-setup-pulls.raw").is_file():
            states.append((state, directory))
    return sorted(states)


def _children(state: int) -> tuple[Child, ...]:
    geometry = public_geometry.construct_state_geometry(state)
    if geometry is None:
        return ()
    result: list[Child] = []
    for source_primitive in range(len(geometry.indices) // 3):
        triangle = [
            geometry.vertices[geometry.indices[source_primitive * 3 + local]]
            for local in range(3)
        ]
        if all(
            retained_raster.GUARD_LOW <= vertex[0] <= retained_raster.GUARD_HIGH
            and retained_raster.GUARD_LOW <= vertex[1] <= retained_raster.GUARD_HIGH
            for vertex in triangle
        ):
            continue
        polygon = public_raster._clip_triangle_preserving_start(triangle)
        for fan in range(1, len(polygon) - 1):
            vertices = (polygon[0], polygon[fan], polygon[fan + 1])
            if retained_raster._triangle_area(list(vertices)) == 0.0:
                continue
            result.append(
                Child(
                    state=state,
                    source_primitive=source_primitive,
                    fan_ordinal=fan - 1,
                    vertices=vertices,
                )
            )
    return tuple(result)


def _load_pulls(state: int, directory: Path) -> tuple[PullRecord, ...]:
    path = directory / "reveal-a2-setup-pulls.raw"
    words = np.fromfile(path, dtype="<u4")
    expected = (
        PULL_AXIS_COUNT
        * PULL_PRIMITIVE_COUNT
        * PULL_TILE_COUNT
        * PULL_RECORD_WORD_COUNT
    )
    if words.size != expected:
        raise ValueError(f"state {state} pull word count differs")
    records = words.reshape(
        PULL_AXIS_COUNT,
        PULL_PRIMITIVE_COUNT,
        PULL_TILE_COUNT,
        PULL_RECORD_WORD_COUNT,
    )
    result: list[PullRecord] = []
    for axis in range(PULL_AXIS_COUNT):
        for primitive in range(PULL_PRIMITIVE_COUNT):
            for tile in range(PULL_TILE_COUNT):
                record = records[axis, primitive, tile]
                marker = int(record[0])
                if marker in {INACTIVE_RECORD, CLAIMED_RECORD}:
                    continue
                values = tuple(
                    tuple(
                        int(value)
                        for value in record[
                            3 + phase * PULL_COMPONENT_COUNT : 3
                            + (phase + 1) * PULL_COMPONENT_COUNT
                        ]
                    )
                    for phase in range(PULL_PHASE_COUNT)
                )
                result.append(
                    PullRecord(
                        state=state,
                        axis=axis,
                        source_primitive=primitive,
                        tile=tile,
                        pixel_x=int(record[1]),
                        pixel_y=int(record[2]),
                        values=values,
                    )
                )
    return tuple(result)


def _ordered_float_key(bits: int) -> int:
    """Map binary32 bits to monotonically increasing unsigned integers."""

    return (~bits & 0xFFFF_FFFF) if bits & 0x8000_0000 else bits | 0x8000_0000


def _bits_from_ordered_float_key(key: int) -> int:
    if not 0 <= key <= 0xFFFF_FFFF:
        raise ValueError("ordered binary32 key is outside uint32")
    return (~key & 0xFFFF_FFFF) if key < 0x8000_0000 else key & 0x7FFF_FFFF


def _rounding_interval(bits: int) -> tuple[Fraction, Fraction]:
    """Return a closed superset of the RN-even preimage of one finite float."""

    if bits & 0x7F80_0000 == 0x7F80_0000:
        raise ValueError("pull value is not finite")
    key = _ordered_float_key(bits)
    if key in {0, 0xFFFF_FFFF}:
        raise ValueError("pull value has no finite binary32 neighbor")
    value = _fraction(bits)
    previous_key = key - 1
    while _fraction(_bits_from_ordered_float_key(previous_key)) == value:
        previous_key -= 1
    following_key = key + 1
    while _fraction(_bits_from_ordered_float_key(following_key)) == value:
        following_key += 1
    previous = _fraction(_bits_from_ordered_float_key(previous_key))
    following = _fraction(_bits_from_ordered_float_key(following_key))
    return ((previous + value) / 2, (value + following) / 2)


def _float_candidates_between(
    lower: Fraction,
    upper: Fraction,
    *,
    limit: int,
) -> tuple[int, ...]:
    """Enumerate finite binary32 values in one exact rational interval."""

    if lower > upper:
        return ()
    lower_key = _ordered_float_key(_round_fraction(lower))
    upper_key = _ordered_float_key(_round_fraction(upper))
    first = max(1, min(lower_key, upper_key) - 2)
    last = min(0xFFFF_FFFE, max(lower_key, upper_key) + 2)
    if last - first + 1 > limit:
        raise OverflowError("pull trace leaves too many binary32 candidates")
    return tuple(
        bits
        for key in range(first, last + 1)
        if (bits := _bits_from_ordered_float_key(key)) & 0x7F80_0000 != 0x7F80_0000
        and lower <= _fraction(bits) <= upper
    )


def _constant_candidates(
    values: tuple[int, ...],
    *,
    local_pixel: int,
    slope_bits: int,
    candidate_limit: int,
) -> tuple[int, ...]:
    slope = _fraction(slope_bits)
    lower: Fraction | None = None
    upper: Fraction | None = None
    for phase, expected in enumerate(values):
        value_lower, value_upper = _rounding_interval(expected)
        coordinate = Fraction(local_pixel * PULL_PHASE_COUNT + phase, PULL_PHASE_COUNT)
        candidate_lower = value_lower - coordinate * slope
        candidate_upper = value_upper - coordinate * slope
        lower = candidate_lower if lower is None else max(lower, candidate_lower)
        upper = candidate_upper if upper is None else min(upper, candidate_upper)
    if lower is None or upper is None or lower > upper:
        return ()
    candidates = _float_candidates_between(lower, upper, limit=candidate_limit)
    return tuple(
        constant_bits
        for constant_bits in candidates
        if all(
            _round_fraction(
                Fraction(local_pixel * PULL_PHASE_COUNT + phase, PULL_PHASE_COUNT)
                * slope
                + _fraction(constant_bits)
            )
            == expected
            for phase, expected in enumerate(values)
        )
    )


def _accepted_slopes(
    values: tuple[int, ...],
    *,
    local_pixel: int,
    candidate_limit: int,
) -> tuple[int, ...]:
    """Recover every float32 slope compatible with one sixteen-phase pull."""

    if len(values) != PULL_PHASE_COUNT:
        raise ValueError("one pull phase vector is required")
    first_lower, first_upper = _rounding_interval(values[0])
    last_lower, last_upper = _rounding_interval(values[-1])
    phase_span = Fraction(PULL_PHASE_COUNT, PULL_PHASE_COUNT - 1)
    slope_lower = (last_lower - first_upper) * phase_span
    slope_upper = (last_upper - first_lower) * phase_span
    candidates = _float_candidates_between(
        slope_lower,
        slope_upper,
        limit=candidate_limit,
    )
    return tuple(
        slope_bits
        for slope_bits in candidates
        if _constant_candidates(
            values,
            local_pixel=local_pixel,
            slope_bits=slope_bits,
            candidate_limit=64,
        )
    )


def _point_in_triangle(child: Child, x: float, y: float) -> bool:
    vertices = child.vertices
    signs = []
    for start, end in zip(vertices, vertices[1:] + vertices[:1], strict=True):
        signs.append(
            (end[0] - start[0]) * (y - start[1]) - (end[1] - start[1]) * (x - start[0])
        )
    return all(value >= 0.0 for value in signs) or all(value <= 0.0 for value in signs)


def _matching_children(
    record: PullRecord, children: tuple[Child, ...]
) -> tuple[Child, ...]:
    candidates = tuple(
        child
        for child in children
        if child.source_primitive == record.source_primitive
        and _point_in_triangle(child, record.pixel_x + 0.5, record.pixel_y + 0.5)
    )
    return candidates


def _plane_slope_bits(
    vertices: tuple[Vertex, Vertex, Vertex], *, axis: int, component: int
) -> int:
    x0, y0 = (_fraction(_float_bits(vertices[0][index])) for index in (0, 1))
    x1, y1 = (_fraction(_float_bits(vertices[1][index])) for index in (0, 1))
    x2, y2 = (_fraction(_float_bits(vertices[2][index])) for index in (0, 1))
    a0 = _fraction(_float_bits(vertices[0][component]))
    a1 = _fraction(_float_bits(vertices[1][component]))
    a2 = _fraction(_float_bits(vertices[2][component]))
    dx1, dy1 = x1 - x0, y1 - y0
    dx2, dy2 = x2 - x0, y2 - y0
    da1, da2 = a1 - a0, a2 - a0
    determinant = dx1 * dy2 - dy1 * dx2
    if determinant == 0:
        raise ValueError("degenerate triangle has no plane slope")
    numerator = da1 * dy2 - da2 * dy1 if axis == 0 else dx1 * da2 - dx2 * da1
    return _round_fraction(numerator / determinant)


def _source_triangle(state: int, primitive: int) -> tuple[Vertex, Vertex, Vertex]:
    geometry = public_geometry.construct_state_geometry(state)
    if geometry is None:
        raise ValueError("empty state has no source triangle")
    return tuple(
        geometry.vertices[geometry.indices[primitive * 3 + local]] for local in range(3)
    )  # type: ignore[return-value]


def _recover_group_slopes(
    records: list[tuple[PullRecord, tuple[int, ...]]],
    *,
    candidate_limit: int,
) -> tuple[tuple[int, ...], int, int, int, int, bool]:
    candidate_sets: list[set[int]] = []
    uninformative = 0
    impossible = 0
    maximum_record_candidates = 0
    for record, values in records:
        local_pixel = (
            record.pixel_x if record.axis == 0 else record.pixel_y
        ) % TILE_SIZE
        try:
            accepted = _accepted_slopes(
                values,
                local_pixel=local_pixel,
                candidate_limit=candidate_limit,
            )
        except OverflowError:
            uninformative += 1
            continue
        maximum_record_candidates = max(maximum_record_candidates, len(accepted))
        if not accepted:
            impossible += 1
            continue
        candidate_sets.append(set(accepted))
    if not candidate_sets:
        return (
            (),
            0,
            uninformative,
            impossible,
            maximum_record_candidates,
            False,
        )
    candidate_sets.sort(key=len)
    intersection = candidate_sets[0]
    for candidates in candidate_sets[1:]:
        intersection &= candidates
        if not intersection:
            break
    return (
        tuple(sorted(intersection, key=_ordered_float_key)),
        len(candidate_sets),
        uninformative,
        impossible,
        maximum_record_candidates,
        not intersection,
    )


def analyze(
    root: Path,
    *,
    candidate_limit: int,
    selected_states: frozenset[int] | None = None,
) -> JsonObject:
    state_inputs = _state_directories(root)
    if selected_states is not None:
        state_inputs = [item for item in state_inputs if item[0] in selected_states]
    if not state_inputs:
        raise ValueError("no retained setup pull states were found")

    totals = {
        "activePullRecords": 0,
        "clippedPrimitivePullRecordCount": 0,
        "uniqueChildMatches": 0,
        "ambiguousChildMatches": 0,
        "unmatchedChildRecords": 0,
        "slopeGroupCount": 0,
        "uniquelyRecoveredSlopeGroupCount": 0,
        "ambiguousRecoveredSlopeGroupCount": 0,
        "uninformativeSlopeGroupCount": 0,
        "evaluatorOutlierSlopeGroupCount": 0,
        "evaluatorOutlierPullRecordCount": 0,
        "inconsistentSlopeGroupCount": 0,
        "exactRoundedChildSlopeGroupCount": 0,
        "exactOriginalPlaneSlopeGroupCount": 0,
    }
    states: list[JsonObject] = []
    slope_groups: list[JsonObject] = []
    pull_hashes: dict[str, str] = {}
    first_failures: list[JsonObject] = []
    for state, directory in state_inputs:
        children = _children(state)
        clipped_primitives = {child.source_primitive for child in children}
        records = _load_pulls(state, directory)
        totals["activePullRecords"] += len(records)
        grouped: dict[
            tuple[int, int, int, int],
            list[tuple[PullRecord, tuple[int, ...]]],
        ] = defaultdict(list)
        state_counts = {
            "state": state,
            "postguardChildCount": len(children),
            "activePullRecordCount": len(records),
            "clippedPrimitivePullRecordCount": 0,
            "uniqueChildMatches": 0,
            "ambiguousChildMatches": 0,
            "unmatchedChildRecords": 0,
            "slopeGroupCount": 0,
            "uniquelyRecoveredSlopeGroupCount": 0,
            "ambiguousRecoveredSlopeGroupCount": 0,
            "uninformativeSlopeGroupCount": 0,
            "evaluatorOutlierSlopeGroupCount": 0,
            "evaluatorOutlierPullRecordCount": 0,
            "inconsistentSlopeGroupCount": 0,
            "exactRoundedChildSlopeGroupCount": 0,
            "exactOriginalPlaneSlopeGroupCount": 0,
        }
        pull_path = directory / "reveal-a2-setup-pulls.raw"
        pull_hashes[str(state)] = _sha256(pull_path)
        for record in records:
            if record.source_primitive not in clipped_primitives:
                continue
            state_counts["clippedPrimitivePullRecordCount"] += 1
            matches = _matching_children(record, children)
            if len(matches) == 1:
                state_counts["uniqueChildMatches"] += 1
            elif matches:
                state_counts["ambiguousChildMatches"] += 1
                continue
            else:
                state_counts["unmatchedChildRecords"] += 1
                if len(first_failures) < 32:
                    first_failures.append(
                        {
                            "kind": "unmatched-child",
                            "state": state,
                            "axis": record.axis,
                            "sourcePrimitive": record.source_primitive,
                            "tile": record.tile,
                            "pixel": [record.pixel_x, record.pixel_y],
                        }
                    )
                continue
            child = matches[0]
            for pull_component in SDF_COMPONENTS:
                values = tuple(phase[pull_component] for phase in record.values)
                key = (
                    child.source_primitive,
                    child.fan_ordinal,
                    record.axis,
                    pull_component,
                )
                grouped[key].append((record, values))

        child_by_key = {
            (child.source_primitive, child.fan_ordinal): child for child in children
        }
        source_by_primitive = {
            primitive: _source_triangle(state, primitive)
            for primitive in clipped_primitives
        }
        for key, group_records in sorted(grouped.items()):
            source_primitive, fan_ordinal, axis, pull_component = key
            vertex_component = VERTEX_SDF_COMPONENTS[
                SDF_COMPONENTS.index(pull_component)
            ]
            child = child_by_key[(source_primitive, fan_ordinal)]
            child_bits = _plane_slope_bits(
                child.vertices,
                axis=axis,
                component=vertex_component,
            )
            source_bits = _plane_slope_bits(
                source_by_primitive[source_primitive],
                axis=axis,
                component=vertex_component,
            )
            (
                recovered,
                informative,
                uninformative,
                impossible,
                maximum_candidates,
                informative_intersection_empty,
            ) = _recover_group_slopes(
                group_records,
                candidate_limit=candidate_limit,
            )
            state_counts["slopeGroupCount"] += 1
            inconsistent = informative_intersection_empty or (
                informative == 0 and impossible > 0
            )
            if impossible > 0:
                state_counts["evaluatorOutlierSlopeGroupCount"] += 1
                state_counts["evaluatorOutlierPullRecordCount"] += impossible
            if inconsistent:
                status = "inconsistent"
                state_counts["inconsistentSlopeGroupCount"] += 1
            elif informative == 0:
                status = "uninformative"
                state_counts["uninformativeSlopeGroupCount"] += 1
            elif len(recovered) == 1:
                status = "unique"
                state_counts["uniquelyRecoveredSlopeGroupCount"] += 1
            elif recovered:
                status = "ambiguous"
                state_counts["ambiguousRecoveredSlopeGroupCount"] += 1
            else:
                raise AssertionError("informative slope group has no classification")
            if not inconsistent and child_bits in recovered:
                state_counts["exactRoundedChildSlopeGroupCount"] += 1
            if not inconsistent and source_bits in recovered:
                state_counts["exactOriginalPlaneSlopeGroupCount"] += 1
            recovered_bits = recovered[0] if status == "unique" else None
            slope_group = {
                "state": state,
                "sourcePrimitive": source_primitive,
                "childFanOrdinal": fan_ordinal,
                "axis": axis,
                "component": pull_component,
                "status": status,
                "pullRecordCount": len(group_records),
                "informativePullRecordCount": informative,
                "uninformativePullRecordCount": uninformative,
                "impossiblePullRecordCount": impossible,
                "informativeCandidateIntersectionEmpty": (
                    informative_intersection_empty
                ),
                "maximumSingleRecordCandidateCount": maximum_candidates,
                "recoveredSlopeCandidateCount": len(recovered),
                "recoveredSlopeBits": [f"0x{bits:08x}" for bits in recovered[:32]],
                "roundedChildSlopeBits": f"0x{child_bits:08x}",
                "originalPlaneSlopeBits": f"0x{source_bits:08x}",
                "recoveredMinusRoundedChildFloatUlps": (
                    _ordered_float_key(recovered_bits) - _ordered_float_key(child_bits)
                    if recovered_bits is not None
                    else None
                ),
                "recoveredMinusOriginalPlaneFloatUlps": (
                    _ordered_float_key(recovered_bits) - _ordered_float_key(source_bits)
                    if recovered_bits is not None
                    else None
                ),
            }
            slope_groups.append(slope_group)
            if inconsistent and len(first_failures) < 32:
                first_failures.append(slope_group)
        for key in totals:
            if key == "activePullRecords":
                continue
            totals[key] += int(state_counts[key])
        states.append(state_counts)

    return {
        "schemaVersion": 2,
        "classification": "output-blind retained Apple coefficient-pull analysis",
        "retainedSetupRoot": str(root),
        "maximumCandidateEnumerationCount": candidate_limit,
        "stateCount": len(state_inputs),
        "pullSha256ByState": pull_hashes,
        "planeEquation": {
            "dx": "((a1-a0)*(y2-y0) - (a2-a0)*(y1-y0)) / det",
            "dy": "((x1-x0)*(a2-a0) - (x2-x0)*(a1-a0)) / det",
            "det": "(x1-x0)*(y2-y0) - (y1-y0)*(x2-x0)",
            "rounding": "one exact-rational binary32 round-to-nearest-ties-even",
        },
        "referencePixelsRead": False,
        "totals": totals,
        "states": states,
        "slopeGroups": slope_groups,
        "firstFailures": first_failures,
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "root",
        nargs="?",
        type=Path,
        default=Path("/tmp/walle-analysis/standalone-A2-setup-v1"),
    )
    parser.add_argument("--candidate-limit", type=int, default=262_144)
    parser.add_argument("--state", type=int, action="append")
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    if args.candidate_limit <= 0:
        raise ValueError("candidate limit must be positive")
    report = analyze(
        args.root,
        candidate_limit=args.candidate_limit,
        selected_states=frozenset(args.state) if args.state else None,
    )
    serialized = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output is None:
        print(serialized, end="")
    else:
        args.output.write_text(serialized, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
