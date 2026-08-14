#!/usr/bin/env python3
"""Recover AGX post-clip coefficient triples from one-hot basis pulls.

The capture is output-blind: it redraws public reveal source triangles with
one-hot vertex varyings and records only Metal interpolation results.  At each
sampled tile, the X0 and Y0 phase lattices evaluate the affine coefficient
record from the tile origin, so exact binary32 interval inversion can recover
the three words ``(A, B, C)`` without consulting a reference image.  The two
half-axis lattices are held out as an exact replay check of AGX's nested FFMA
order.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Final

import numpy as np
from numpy.typing import NDArray


ROOT: Final = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "analysis"), str(ROOT / "lg-test" / "Analysis")]

import recover_reveal_postguard_plane_setup as recovery  # noqa: E402


type JsonObject = dict[str, object]
type Vertex = tuple[float, ...]
type BitsVertex = tuple[int, ...]
type CoefficientTriple = tuple[int, int, int]
type PullWords = NDArray[np.uint32]

EXPECTED_CATALOG_SHA256: Final = (
    "bc8b96dc4d3dc7c2fb6383dda49baa839eb207b60128739604ad8ddcd9402bd6"
)
CAPTURE_SCHEMA: Final = "walle-reveal-agx-basis-phase-capture-v2"
CATALOG_SCHEMA: Final = "walle-reveal-agx-basis-catalog-v1"
PULL_PHASE_COUNT: Final = 16
RECORD_VECTOR_COUNT: Final = 101
RECORD_WORD_COUNT: Final = RECORD_VECTOR_COUNT * 4
X_ZERO_START: Final = 5
Y_ZERO_START: Final = 21
X_FAR_START: Final = 37
Y_FAR_START: Final = 53
X_HALF_START: Final = 69
Y_HALF_START: Final = 85
GUARD_LOW: Final = -512.0
GUARD_HIGH: Final = 2_560.0


@dataclass(frozen=True, slots=True)
class Sample:
    case_index: int
    state: int
    source_primitive: int
    child_ordinal: int
    child_ordinal_within_source: int
    sample_ordinal: int
    record_index: int
    pixel: tuple[int, int]
    tile: tuple[int, int]
    source_vertices: tuple[BitsVertex, BitsVertex, BitsVertex]
    generated_vertices: tuple[BitsVertex, BitsVertex, BitsVertex]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _require_dict(value: object, name: str) -> JsonObject:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ValueError(f"{name} is not an object")
    return value  # type: ignore[return-value]


def _require_list(value: object, name: str) -> list[object]:
    if not isinstance(value, list):
        raise ValueError(f"{name} is not an array")
    return value


def _require_int(value: object, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{name} is not an integer")
    return value


def _require_str(value: object, name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} is not a string")
    return value


def _float(bits: int) -> float:
    return struct.unpack("<f", struct.pack("<I", bits))[0]


def _bits(value: float) -> int:
    return struct.unpack("<I", struct.pack("<f", value))[0]


def _fma(coordinate: Fraction, slope_bits: int, constant_bits: int) -> int:
    return recovery._round_fraction(  # noqa: SLF001
        coordinate * recovery._fraction(slope_bits)  # noqa: SLF001
        + recovery._fraction(constant_bits)  # noqa: SLF001
    )


def _nested_value(
    x: Fraction,
    y: Fraction,
    triple: CoefficientTriple,
) -> int:
    slope_x, slope_y, constant = triple
    inner = _fma(y, slope_y, constant)
    return _fma(x, slope_x, inner)


def _line_candidates(
    lines: tuple[tuple[tuple[int, ...], int], ...],
    *,
    candidate_limit: int,
) -> dict[int, tuple[int, ...]]:
    seed_values, seed_pixel = lines[0]
    slopes = recovery._accepted_slopes(  # noqa: SLF001
        seed_values,
        local_pixel=seed_pixel,
        candidate_limit=candidate_limit,
    )
    result: dict[int, tuple[int, ...]] = {}
    for slope_bits in slopes:
        slope = recovery._fraction(slope_bits)  # noqa: SLF001
        lower: Fraction | None = None
        upper: Fraction | None = None
        for values, local_pixel in lines:
            for phase, expected in enumerate(values):
                value_lower, value_upper = recovery._rounding_interval(  # noqa: SLF001
                    expected
                )
                coordinate = Fraction(local_pixel * 16 + phase, 16)
                candidate_lower = value_lower - coordinate * slope
                candidate_upper = value_upper - coordinate * slope
                lower = (
                    candidate_lower if lower is None else max(lower, candidate_lower)
                )
                upper = (
                    candidate_upper if upper is None else min(upper, candidate_upper)
                )
        if lower is None or upper is None or lower > upper:
            continue
        constants = recovery._float_candidates_between(  # noqa: SLF001
            lower,
            upper,
            limit=256,
        )
        accepted = tuple(
            constant_bits
            for constant_bits in constants
            if all(
                _fma(
                    Fraction(local_pixel * 16 + phase, 16),
                    slope_bits,
                    constant_bits,
                )
                == expected
                for values, local_pixel in lines
                for phase, expected in enumerate(values)
            )
        )
        if accepted:
            result[slope_bits] = accepted
    return result


def _recover_triples(
    x_zero: tuple[int, ...],
    y_zero: tuple[int, ...],
    x_far: tuple[int, ...],
    y_far: tuple[int, ...],
    x_half: tuple[int, ...],
    y_half: tuple[int, ...],
    *,
    candidate_limit: int,
) -> tuple[CoefficientTriple, ...]:
    x_candidates = _line_candidates(
        ((x_zero, 0), (x_far, 31)),
        candidate_limit=candidate_limit,
    )
    y_candidates = _line_candidates(
        ((y_zero, 0), (y_far, 31)),
        candidate_limit=candidate_limit,
    )
    triples: list[CoefficientTriple] = []
    for slope_x, x_constants in x_candidates.items():
        x_constant_set = set(x_constants)
        for slope_y, y_constants in y_candidates.items():
            for constant in x_constant_set.intersection(y_constants):
                triple = (slope_x, slope_y, constant)
                if all(
                    _nested_value(Fraction(phase, 16), Fraction(0), triple)
                    == x_zero[phase]
                    and _nested_value(Fraction(0), Fraction(phase, 16), triple)
                    == y_zero[phase]
                    and _nested_value(
                        Fraction(31 * 16 + phase, 16), Fraction(0), triple
                    )
                    == x_far[phase]
                    and _nested_value(
                        Fraction(0), Fraction(31 * 16 + phase, 16), triple
                    )
                    == y_far[phase]
                    and _nested_value(Fraction(phase, 16), Fraction(1, 2), triple)
                    == x_half[phase]
                    and _nested_value(Fraction(1, 2), Fraction(phase, 16), triple)
                    == y_half[phase]
                    for phase in range(PULL_PHASE_COUNT)
                ):
                    triples.append(triple)
    return tuple(
        sorted(
            set(triples),
            key=lambda triple: tuple(
                recovery._ordered_float_key(value)
                for value in triple  # noqa: SLF001
            ),
        )
    )


def _round_fraction(value: Fraction) -> float:
    return _float(recovery._round_fraction(value))  # noqa: SLF001


def _intersection(start: Vertex, end: Vertex, *, axis: int, edge: float) -> Vertex:
    start_axis = Fraction.from_float(start[axis])
    end_axis = Fraction.from_float(end[axis])
    ratio = (Fraction.from_float(edge) - start_axis) / (end_axis - start_axis)
    result: list[float] = []
    for component, (low_value, high_value) in enumerate(zip(start, end, strict=True)):
        if component == axis:
            result.append(edge)
        else:
            low = Fraction.from_float(low_value)
            high = Fraction.from_float(high_value)
            result.append(_round_fraction(low + ratio * (high - low)))
    return tuple(result)


def _clip_triangle(triangle: tuple[Vertex, Vertex, Vertex]) -> tuple[Vertex, ...]:
    polygon: tuple[Vertex, ...] = triangle
    for axis, edge, keep_greater in (
        (0, GUARD_LOW, True),
        (0, GUARD_HIGH, False),
        (1, GUARD_LOW, True),
        (1, GUARD_HIGH, False),
    ):
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


def _area(vertices: tuple[Vertex, Vertex, Vertex]) -> Fraction:
    x0, y0 = map(Fraction.from_float, vertices[0][:2])
    x1, y1 = map(Fraction.from_float, vertices[1][:2])
    x2, y2 = map(Fraction.from_float, vertices[2][:2])
    return (x1 - x0) * (y2 - y0) - (y1 - y0) * (x2 - x0)


def _canonical_children(sample: Sample) -> tuple[tuple[Vertex, Vertex, Vertex], ...]:
    source: list[Vertex] = []
    for vertex_index, vertex_bits in enumerate(sample.source_vertices):
        basis = tuple(
            1.0 if component == vertex_index else 0.0 for component in range(3)
        )
        linear = float(1 << vertex_index)
        source.append((_float(vertex_bits[0]), _float(vertex_bits[1]), *basis, linear))
    polygon = _clip_triangle(tuple(source))  # type: ignore[arg-type]
    children = tuple(
        (polygon[0], polygon[fan], polygon[fan + 1])
        for fan in range(1, len(polygon) - 1)
        if _area((polygon[0], polygon[fan], polygon[fan + 1])) != 0
    )
    for child_index, child in enumerate(children):
        expected = (
            sample.generated_vertices
            if child_index == sample.child_ordinal_within_source
            else None
        )
        if expected is not None:
            actual_positions = tuple(
                tuple(_bits(value) for value in vertex[:2]) for vertex in child
            )
            expected_positions = tuple(tuple(vertex[:2]) for vertex in expected)
            if actual_positions != expected_positions:
                raise ValueError(
                    f"canonical child positions differ at record {sample.record_index}"
                )
    return children


def _plane_slope_bits(
    vertices: tuple[Vertex, Vertex, Vertex],
    *,
    axis: int,
    component: int,
) -> int:
    return recovery._plane_slope_bits(  # noqa: SLF001
        vertices,
        axis=axis,
        component=component,
    )


def _load_catalog(path: Path) -> tuple[JsonObject, tuple[Sample, ...]]:
    if _sha256(path) != EXPECTED_CATALOG_SHA256:
        raise ValueError("catalog SHA-256 differs")
    root = _require_dict(json.loads(path.read_text(encoding="utf-8")), "catalog")
    if root.get("schema") != CATALOG_SCHEMA:
        raise ValueError("catalog schema differs")
    samples: list[Sample] = []
    for case_index, case_value in enumerate(_require_list(root.get("cases"), "cases")):
        case = _require_dict(case_value, f"case {case_index}")
        state = _require_int(case.get("state"), "state")
        source_primitive = _require_int(case.get("sourcePrimitive"), "source primitive")
        source_vertices = tuple(
            tuple(
                _require_int(word, "source vertex word")
                for word in _require_list(vertex, "source vertex")
            )
            for vertex in _require_list(case.get("sourceVertexBits"), "source vertices")
        )
        if len(source_vertices) != 3:
            raise ValueError("source vertex count differs")
        for child_value in _require_list(case.get("children"), "children"):
            child = _require_dict(child_value, "child")
            child_ordinal = _require_int(
                child.get("childOrdinalInState"), "child ordinal"
            )
            child_within = _require_int(
                child.get("childOrdinalWithinSource"), "child source ordinal"
            )
            generated = tuple(
                tuple(
                    _require_int(word, "generated vertex word")
                    for word in _require_list(vertex, "generated vertex")
                )
                for vertex in _require_list(
                    child.get("generatedVertexBits"), "generated vertices"
                )
            )
            if len(generated) != 3:
                raise ValueError("generated vertex count differs")
            for sample_value in _require_list(child.get("samples"), "samples"):
                entry = _require_dict(sample_value, "sample")
                pixel_values = _require_list(entry.get("pixel"), "pixel")
                tile_values = _require_list(entry.get("tile"), "tile")
                if len(pixel_values) != 2 or len(tile_values) != 2:
                    raise ValueError("sample coordinate shape differs")
                samples.append(
                    Sample(
                        case_index=case_index,
                        state=state,
                        source_primitive=source_primitive,
                        child_ordinal=child_ordinal,
                        child_ordinal_within_source=child_within,
                        sample_ordinal=_require_int(
                            entry.get("sampleOrdinal"), "sample ordinal"
                        ),
                        record_index=_require_int(
                            entry.get("recordIndex"), "record index"
                        ),
                        pixel=tuple(
                            _require_int(value, "pixel coordinate")
                            for value in pixel_values
                        ),  # type: ignore[arg-type]
                        tile=tuple(
                            _require_int(value, "tile coordinate")
                            for value in tile_values
                        ),  # type: ignore[arg-type]
                        source_vertices=source_vertices,  # type: ignore[arg-type]
                        generated_vertices=generated,  # type: ignore[arg-type]
                    )
                )
    samples.sort(key=lambda sample: sample.record_index)
    if [sample.record_index for sample in samples] != list(range(len(samples))):
        raise ValueError("catalog record indices are not contiguous")
    return root, tuple(samples)


def _load_capture(
    directory: Path,
    *,
    catalog_path: Path,
    record_count: int,
) -> tuple[JsonObject, PullWords, Path]:
    manifest_path = directory / "manifest.json"
    manifest = _require_dict(
        json.loads(manifest_path.read_text(encoding="utf-8")), "manifest"
    )
    if manifest.get("schema") != CAPTURE_SCHEMA:
        raise ValueError("capture schema differs")
    catalog = _require_dict(manifest.get("catalog"), "manifest catalog")
    if catalog.get("sha256") != _sha256(catalog_path):
        raise ValueError("manifest catalog identity differs")
    capture = _require_dict(manifest.get("capture"), "manifest capture")
    if _require_int(capture.get("recordCount"), "capture record count") != record_count:
        raise ValueError("capture record count differs")
    if (
        _require_int(capture.get("recordVectorCount"), "record vector count")
        != RECORD_VECTOR_COUNT
    ):
        raise ValueError("capture record vector count differs")
    raw_path = directory / _require_str(capture.get("file"), "capture file")
    if _sha256(raw_path) != _require_str(capture.get("sha256"), "capture SHA-256"):
        raise ValueError("capture SHA-256 differs")
    words = np.fromfile(raw_path, dtype="<u4")
    if words.size != record_count * RECORD_WORD_COUNT:
        raise ValueError("capture word count differs")
    return manifest, words.reshape(record_count, RECORD_VECTOR_COUNT, 4), raw_path


def _pull(record: PullWords, start: int, component: int) -> tuple[int, ...]:
    return tuple(int(record[start + phase, component]) for phase in range(16))


def analyze(
    catalog_path: Path,
    capture_directory: Path,
    *,
    candidate_limit: int,
) -> JsonObject:
    catalog, samples = _load_catalog(catalog_path)
    manifest, words, raw_path = _load_capture(
        capture_directory,
        catalog_path=catalog_path,
        record_count=len(samples),
    )
    records: list[JsonObject] = []
    grouped: dict[tuple[int, int, int], list[tuple[int, CoefficientTriple]]] = (
        defaultdict(list)
    )
    statuses: Counter[str] = Counter()
    exact_source_axes = 0
    exact_child_axes = 0
    unique_axis_count = 0
    for sample in samples:
        record = words[sample.record_index]
        expected_header = (
            sample.pixel[0],
            sample.pixel[1],
            0,
            sample.case_index,
        )
        expected_identity = (
            sample.record_index,
            sample.state,
            sample.source_primitive,
            sample.child_ordinal,
        )
        if tuple(int(value) for value in record[0]) != expected_header:
            raise ValueError(f"record {sample.record_index} header differs")
        if tuple(int(value) for value in record[1]) != expected_identity:
            raise ValueError(f"record {sample.record_index} identity differs")
        canonical = _canonical_children(sample)[sample.child_ordinal_within_source]
        source_vertices: tuple[Vertex, Vertex, Vertex] = tuple(
            (
                _float(vertex[0]),
                _float(vertex[1]),
                *(1.0 if component == vertex_index else 0.0 for component in range(3)),
                float(1 << vertex_index),
            )
            for vertex_index, vertex in enumerate(sample.source_vertices)
        )  # type: ignore[assignment]
        component_results: list[JsonObject] = []
        unique_triples: list[CoefficientTriple | None] = []
        for component in range(4):
            try:
                triples = _recover_triples(
                    _pull(record, X_ZERO_START, component),
                    _pull(record, Y_ZERO_START, component),
                    _pull(record, X_FAR_START, component),
                    _pull(record, Y_FAR_START, component),
                    _pull(record, X_HALF_START, component),
                    _pull(record, Y_HALF_START, component),
                    candidate_limit=candidate_limit,
                )
                status = (
                    "unique"
                    if len(triples) == 1
                    else "ambiguous"
                    if triples
                    else "inconsistent"
                )
            except OverflowError:
                triples = ()
                status = "uninformative"
            statuses[status] += 1
            recovered = triples[0] if len(triples) == 1 else None
            unique_triples.append(recovered)
            source_x = _plane_slope_bits(
                source_vertices, axis=0, component=2 + component
            )
            source_y = _plane_slope_bits(
                source_vertices, axis=1, component=2 + component
            )
            child_x = _plane_slope_bits(canonical, axis=0, component=2 + component)
            child_y = _plane_slope_bits(canonical, axis=1, component=2 + component)
            if recovered is not None:
                unique_axis_count += 2
                exact_source_axes += int(recovered[0] == source_x) + int(
                    recovered[1] == source_y
                )
                exact_child_axes += int(recovered[0] == child_x) + int(
                    recovered[1] == child_y
                )
                grouped[(sample.case_index, sample.child_ordinal, component)].append(
                    (sample.record_index, recovered)
                )
            component_results.append(
                {
                    "component": component,
                    "status": status,
                    "candidateCount": len(triples),
                    "candidateBits": [
                        [f"0x{value:08x}" for value in triple]
                        for triple in triples[:32]
                    ],
                    "sourceSlopeBits": [f"0x{source_x:08x}", f"0x{source_y:08x}"],
                    "canonicalChildSlopeBits": [f"0x{child_x:08x}", f"0x{child_y:08x}"],
                    "recoveredMinusSourceFloatUlps": (
                        [
                            recovery._ordered_float_key(recovered[axis])  # noqa: SLF001
                            - recovery._ordered_float_key((source_x, source_y)[axis])  # noqa: SLF001
                            for axis in range(2)
                        ]
                        if recovered is not None
                        else None
                    ),
                    "recoveredMinusCanonicalChildFloatUlps": (
                        [
                            recovery._ordered_float_key(recovered[axis])  # noqa: SLF001
                            - recovery._ordered_float_key((child_x, child_y)[axis])  # noqa: SLF001
                            for axis in range(2)
                        ]
                        if recovered is not None
                        else None
                    ),
                }
            )
        records.append(
            {
                "recordIndex": sample.record_index,
                "caseIndex": sample.case_index,
                "state": sample.state,
                "sourcePrimitive": sample.source_primitive,
                "childOrdinal": sample.child_ordinal,
                "childOrdinalWithinSource": sample.child_ordinal_within_source,
                "sampleOrdinal": sample.sample_ordinal,
                "pixel": list(sample.pixel),
                "tile": list(sample.tile),
                "components": component_results,
                "allComponentsUnique": all(
                    triple is not None for triple in unique_triples
                ),
            }
        )

    groups: list[JsonObject] = []
    for key, entries in sorted(grouped.items()):
        slopes_x = {triple[0] for _, triple in entries}
        slopes_y = {triple[1] for _, triple in entries}
        groups.append(
            {
                "caseIndex": key[0],
                "childOrdinal": key[1],
                "component": key[2],
                "recordIndices": [record_index for record_index, _ in entries],
                "uniqueTileCount": len(entries),
                "slopeXBitCount": len(slopes_x),
                "slopeYBitCount": len(slopes_y),
                "slopeXBits": [f"0x{value:08x}" for value in sorted(slopes_x)],
                "slopeYBits": [f"0x{value:08x}" for value in sorted(slopes_y)],
            }
        )

    return {
        "schemaVersion": 1,
        "classification": "output-blind AGX one-hot post-clip coefficient recovery",
        "authority": {
            "referencePixelsRead": False,
            "usesPublicRevealGeometryOnly": True,
            "captureEstablishesHardwareCoefficients": True,
            "inputOnlySetupLawRecovered": False,
        },
        "inputs": {
            "catalog": str(catalog_path),
            "catalogSha256": _sha256(catalog_path),
            "captureDirectory": str(capture_directory),
            "captureSha256": _sha256(raw_path),
            "manifestSha256": _sha256(capture_directory / "manifest.json"),
            "captureExecutableSha256": _require_dict(
                manifest.get("executable"), "manifest executable"
            ).get("sha256"),
        },
        "census": {
            "recordCount": len(samples),
            "componentRecordCount": len(samples) * 4,
            "statusCounts": dict(sorted(statuses.items())),
            "uniqueRecoveredAxisCount": unique_axis_count,
            "exactSourceSlopeAxisCount": exact_source_axes,
            "exactCanonicalChildSlopeAxisCount": exact_child_axes,
            "groupCount": len(groups),
            "groupWithTileVaryingSlopeXCount": sum(
                group["slopeXBitCount"] != 1 for group in groups
            ),
            "groupWithTileVaryingSlopeYCount": sum(
                group["slopeYBitCount"] != 1 for group in groups
            ),
        },
        "coefficientEvaluation": "ffma(x, A, ffma(y, B, C))",
        "pullLattices": {
            "identification": [
                "tile-origin+(phase/16,0)",
                "tile-origin+(0,phase/16)",
                "tile-origin+(31+phase/16,0)",
                "tile-origin+(0,31+phase/16)",
            ],
            "heldOutReplay": [
                "tile-origin+(phase/16,0.5)",
                "tile-origin+(0.5,phase/16)",
            ],
        },
        "catalogCensus": _require_dict(catalog.get("census"), "catalog census"),
        "records": records,
        "groups": groups,
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("catalog", type=Path)
    parser.add_argument("capture_directory", type=Path)
    parser.add_argument("--candidate-limit", type=int, default=262_144)
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def main() -> int:
    arguments = _parse_args()
    if arguments.candidate_limit <= 0:
        raise ValueError("candidate limit must be positive")
    report = analyze(
        arguments.catalog,
        arguments.capture_directory,
        candidate_limit=arguments.candidate_limit,
    )
    serialized = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if arguments.output is None:
        print(serialized, end="")
    else:
        arguments.output.write_text(serialized, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
