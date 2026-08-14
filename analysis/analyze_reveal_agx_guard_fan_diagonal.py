#!/usr/bin/env python3
"""Discriminate AGX's built-in guard fan diagonal from raw coefficients.

This analysis is deliberately output-blind.  It selects source triangles that
cross exactly one viewport-guard plane and produce a four-vertex polygon.  The
generated endpoint values are reconstructed with the independently measured
AGX reciprocal, 24/18/17 partial-product, and binary32-add pipeline.  The two
legal quad diagonals are then submitted to the already selected top-left AGX
triangle-setup slope model and compared with raw ``LDCF`` coefficient exports.

The built-in guard and explicit ``[[clip_distance]]`` order 1203 are compared
against the same two predictions.  Reference images and rendered pixels are
never opened.
"""

import argparse
import hashlib
import json
import struct
import sys
from collections import Counter
from pathlib import Path
from typing import Final

import numpy as np
from numpy.typing import NDArray


ROOT: Final = Path(__file__).resolve().parent.parent
ANALYSIS: Final = ROOT / "analysis"
sys.path.insert(0, str(ANALYSIS))

import analyze_reveal_agx_basis_phase as phase  # noqa: E402
import analyze_reveal_agx_clip_setup_split as setup  # noqa: E402
import analyze_reveal_agx_clip_weight_tomography as weight  # noqa: E402
import analyze_reveal_agx_direct_clip_reciprocal as reciprocal  # noqa: E402
import analyze_reveal_agx_endpoint_isolation as endpoint  # noqa: E402
import analyze_reveal_agx_guard_order_ldcf as order_analysis  # noqa: E402
import analyze_reveal_agx_ldcf_export as export  # noqa: E402
import analyze_reveal_agx_top_left_setup as top_left  # noqa: E402


type JsonValue = (
    None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]
)
type JsonObject = dict[str, JsonValue]
type Vertex = tuple[float, ...]
type Triangle = tuple[Vertex, Vertex, Vertex]
type UInt32Array = NDArray[np.uint32]

OUTPUT: Final = (
    ROOT
    / "build"
    / "analysis-agx-basis"
    / "guard-fan-diagonal-analysis"
    / "reveal-agx-guard-fan-diagonal-result.json"
)
EXPLICIT_ORDER: Final = "1203"
EXPLICIT_CAPTURE: Final = (
    order_analysis.ORDER_ROOT / f"order-{EXPLICIT_ORDER}" / "capture"
)
EXPECTED_DEPENDENCIES: Final = {
    ANALYSIS
    / "analyze_reveal_agx_guard_order_ldcf.py": "c4ebd02880e1b196be328144a4f690aef21fc600048cff29061654ff6220e6ba",
    ANALYSIS
    / "analyze_reveal_agx_top_left_setup.py": "b5540da8bf406a0ffd48c07fb2e04e60ab37cbcb5bd465a1106aabd92f9f4f48",
    ANALYSIS
    / "analyze_reveal_agx_clip_setup_split.py": "591f9a9fef2caafe43d4d1464377deeacaf2fd5c057cb1337703ac3a1f4f820c",
    ANALYSIS
    / "analyze_reveal_agx_endpoint_isolation.py": "5154d8c5d8b63110a3b2760ebd3d3667d419e0b271f64aa1971c0911212bf239",
    ANALYSIS
    / "analyze_reveal_agx_direct_clip_reciprocal.py": "810e94394a6026d9174cda8d9c99594cc0985c2ce0ee77bdd6a5fd86656e399b",
    order_analysis.CATALOG: "bc8b96dc4d3dc7c2fb6383dda49baa839eb207b60128739604ad8ddcd9402bd6",
    order_analysis.BUILTIN_CAPTURE
    / "manifest.json": "1f9fc1b85c2a31807b0b634cf34d660d724ded5d1d1b6abd2cfcc77a837f4960",
    order_analysis.BUILTIN_CAPTURE
    / "reveal-agx-basis-phase.raw": "5706080d724791becd73148d6c5238761bcec9ce77ed8c414c22d375b1ce6e13",
    EXPLICIT_CAPTURE
    / "manifest.json": "d7e7ba53a3f5efa4810029576b65b1cc6d7a06be2b81eff10b1890f290dd0e8a",
    EXPLICIT_CAPTURE
    / "reveal-agx-basis-phase.raw": "5e416512edbb9ea06503d71a6e31877cdc1dfc494599fd92e3ddeb1bbe7e8ab1",
    endpoint.RECIPROCAL_TABLE: "7381fe62080a7187016d3f32299ea93fbbbe9d974ad8338033c5d161be25720b",
    setup.P25_PATH: "9fbc083dfd9c89fc0bcdc89308acfc4530d408e93789a7dab89ee59ff60a198f",
    order_analysis.OUTPUT: "447db00ed10d65b91fd85a6dd5036cd14155780eb1647d4c356bf0535b897b4e",
    top_left.OUTPUT: "f4534e490b2d91a89d2ec97270506ee9661afccb3e33d84cb2e7a37933511fd4",
}
GUARD_PLANES: Final = (
    (0, -512.0, True),
    (0, 2_560.0, False),
    (1, -512.0, True),
    (1, 2_560.0, False),
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(4 * 1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _relative(path: Path) -> str:
    return path.resolve().relative_to(ROOT).as_posix()


def _identity(path: Path) -> JsonObject:
    return {
        "path": _relative(path),
        "bytes": path.stat().st_size,
        "sha256": _sha256(path),
    }


def _verify_dependencies() -> list[JsonObject]:
    verified: list[JsonObject] = []
    for path, expected in EXPECTED_DEPENDENCIES.items():
        actual = _sha256(path)
        if actual != expected:
            raise ValueError(f"SHA-256 differs for {_relative(path)}: {actual}")
        verified.append(_identity(path))
    return verified


def _bits(value: float) -> int:
    return struct.unpack("<I", struct.pack("<f", value))[0]


def _float(bits: int) -> float:
    return struct.unpack("<f", struct.pack("<I", bits))[0]


def _add_bits(left: int, right: int) -> int:
    return weight._round_fraction(  # noqa: SLF001
        weight._float_fraction(left) + weight._float_fraction(right)  # noqa: SLF001
    )


def _subtract_bits(left: int, right: int) -> int:
    return weight._round_fraction(  # noqa: SLF001
        weight._float_fraction(left) - weight._float_fraction(right)  # noqa: SLF001
    )


def _scale_positive_power_of_two(bits: int, exponent_delta: int) -> int:
    exponent = (bits >> 23) & 0xFF
    shifted = exponent + exponent_delta
    if bits & 0x8000_0000 or exponent in {0, 0xFF} or not 1 <= shifted < 0xFF:
        raise ValueError("reciprocal scaling escaped positive normal binary32")
    return (bits & 0x807F_FFFF) | (shifted << 23)


def _fast_reciprocal(bits: int, table: UInt32Array) -> int:
    exponent = (bits >> 23) & 0xFF
    if bits & 0x8000_0000 or exponent in {0, 0xFF}:
        raise ValueError("reciprocal input is not positive normal binary32")
    normalized = int(table[bits & 0x007F_FFFF])
    return _scale_positive_power_of_two(normalized, 143 - exponent)


def _signed_product_bits(left: int, right: int) -> int:
    sign = (left ^ right) & 0x8000_0000
    left_magnitude = left & 0x7FFF_FFFF
    right_magnitude = right & 0x7FFF_FFFF
    if left_magnitude == 0 or right_magnitude == 0:
        return sign
    return reciprocal.truncated_product_bits(left_magnitude, right_magnitude) ^ sign


def _distance_bits(value: float, edge: float) -> int:
    return _subtract_bits(_bits(value), _bits(edge)) & 0x7FFF_FFFF


def _interpolate_endpoint(
    outside: Vertex,
    inside: Vertex,
    *,
    axis: int,
    edge: float,
    table: UInt32Array,
) -> Vertex:
    outside_distance = _distance_bits(outside[axis], edge)
    inside_distance = _distance_bits(inside[axis], edge)
    denominator = _add_bits(outside_distance, inside_distance)
    reciprocal_bits = _fast_reciprocal(denominator, table)
    outside_weight = _signed_product_bits(reciprocal_bits, inside_distance)
    inside_weight = _signed_product_bits(reciprocal_bits, outside_distance)

    generated: list[float] = []
    for component, (outside_value, inside_value) in enumerate(
        zip(outside, inside, strict=True)
    ):
        if component == axis:
            generated.append(edge)
            continue
        outside_term = _signed_product_bits(outside_weight, _bits(outside_value))
        inside_term = _signed_product_bits(inside_weight, _bits(inside_value))
        generated.append(_float(_add_bits(outside_term, inside_term)))
    return tuple(generated)


def _inside(vertex: Vertex, *, axis: int, edge: float, keep_greater: bool) -> bool:
    return vertex[axis] >= edge if keep_greater else vertex[axis] <= edge


def _clip_one_plane(
    polygon: tuple[Vertex, ...],
    *,
    axis: int,
    edge: float,
    keep_greater: bool,
    table: UInt32Array,
) -> tuple[Vertex, ...]:
    clipped: list[Vertex] = []
    previous = polygon[-1]
    previous_inside = _inside(
        previous,
        axis=axis,
        edge=edge,
        keep_greater=keep_greater,
    )
    for current in polygon:
        current_inside = _inside(
            current,
            axis=axis,
            edge=edge,
            keep_greater=keep_greater,
        )
        if current_inside:
            if not previous_inside:
                clipped.append(
                    _interpolate_endpoint(
                        previous,
                        current,
                        axis=axis,
                        edge=edge,
                        table=table,
                    )
                )
            clipped.append(current)
        elif previous_inside:
            clipped.append(
                _interpolate_endpoint(
                    current,
                    previous,
                    axis=axis,
                    edge=edge,
                    table=table,
                )
            )
        previous = current
        previous_inside = current_inside
    return tuple(clipped)


def _source_vertices(sample: phase.Sample) -> Triangle:
    return tuple(
        (
            phase._float(vertex[0]),  # noqa: SLF001
            phase._float(vertex[1]),  # noqa: SLF001
            *(1.0 if component == vertex_index else 0.0 for component in range(3)),
            float(1 << vertex_index),
        )
        for vertex_index, vertex in enumerate(sample.source_vertices)
    )  # type: ignore[return-value]


def _active_planes(source: Triangle) -> tuple[tuple[int, float, bool], ...]:
    return tuple(
        (axis, edge, keep_greater)
        for axis, edge, keep_greater in GUARD_PLANES
        if any(
            not _inside(
                vertex,
                axis=axis,
                edge=edge,
                keep_greater=keep_greater,
            )
            for vertex in source
        )
    )


def _triangle_contains_sample(triangle: Triangle, pixel: tuple[int, int]) -> bool:
    positions = setup._fixed_positions(triangle)  # noqa: SLF001
    sample_x = pixel[0] * 256 + 128
    sample_y = pixel[1] * 256 + 128
    edge_values = []
    for first, second in ((0, 1), (1, 2), (2, 0)):
        edge_values.append(
            (positions[second][0] - positions[first][0])
            * (sample_y - positions[first][1])
            - (positions[second][1] - positions[first][1])
            * (sample_x - positions[first][0])
        )
    determinant = setup._determinant(positions)  # noqa: SLF001
    return (
        all(value >= 0 for value in edge_values)
        if determinant > 0
        else all(value <= 0 for value in edge_values)
    )


def _fan_triangles(polygon: tuple[Vertex, ...], diagonal: str) -> tuple[Triangle, ...]:
    if len(polygon) != 4:
        raise ValueError("fan discriminator requires a four-vertex polygon")
    match diagonal:
        case "vertex-0-to-2":
            return (
                (polygon[0], polygon[1], polygon[2]),
                (polygon[0], polygon[2], polygon[3]),
            )
        case "vertex-1-to-3":
            return (
                (polygon[0], polygon[1], polygon[3]),
                (polygon[1], polygon[2], polygon[3]),
            )
        case _:
            raise ValueError(f"unknown fan diagonal: {diagonal}")


def _selected_triangle(
    polygon: tuple[Vertex, ...],
    *,
    diagonal: str,
    pixel: tuple[int, int],
) -> Triangle:
    matches = tuple(
        triangle
        for triangle in _fan_triangles(polygon, diagonal)
        if _triangle_contains_sample(triangle, pixel)
    )
    if len(matches) != 1:
        raise ValueError(
            f"sample {pixel} belongs to {len(matches)} triangles under {diagonal}"
        )
    return matches[0]


def _geometry_bits(triangle: Triangle) -> tuple[tuple[int, int], ...]:
    return tuple((_bits(vertex[0]), _bits(vertex[1])) for vertex in triangle)


def _comparison(
    *,
    samples: tuple[phase.Sample, ...],
    catalog: JsonObject,
    actual_coefficients: UInt32Array,
    diagonal: str,
    reciprocal_table: UInt32Array,
    p25_bitmap: bytes,
) -> JsonObject:
    cases_value = catalog.get("cases")
    if not isinstance(cases_value, list):
        raise ValueError("catalog cases are not an array")
    representative: dict[tuple[int, int], phase.Sample] = {}
    for sample in samples:
        representative.setdefault((sample.case_index, sample.child_ordinal), sample)

    deltas: list[int] = []
    predictions: list[int] = []
    actual_words: list[int] = []
    first_mismatches: list[JsonObject] = []
    selected_source_cases: set[int] = set()
    geometry_source_cases: set[int] = set()
    geometry_deltas: list[int] = []
    for (case_index, _child_ordinal), sample in sorted(representative.items()):
        source = _source_vertices(sample)
        active = _active_planes(source)
        if len(active) != 1:
            continue
        axis, edge, keep_greater = active[0]
        outside_count = sum(
            not _inside(
                vertex,
                axis=axis,
                edge=edge,
                keep_greater=keep_greater,
            )
            for vertex in source
        )
        if outside_count != 1:
            continue
        polygon = _clip_one_plane(
            source,
            axis=axis,
            edge=edge,
            keep_greater=keep_greater,
            table=reciprocal_table,
        )
        if len(polygon) != 4:
            raise ValueError("one-plane/one-outside source did not produce a quad")
        selected_source_cases.add(case_index)

        if diagonal == "vertex-0-to-2" and case_index not in geometry_source_cases:
            geometry_source_cases.add(case_index)
            case = cases_value[case_index]
            if not isinstance(case, dict):
                raise ValueError("catalog case is not an object")
            children = case.get("children")
            if not isinstance(children, list) or len(children) != 2:
                raise ValueError("one-plane source does not have two catalog children")
            for child_index, triangle in enumerate(_fan_triangles(polygon, diagonal)):
                child = children[child_index]
                if not isinstance(child, dict):
                    raise ValueError("catalog child is not an object")
                generated = child.get("generatedVertexBits")
                if not isinstance(generated, list):
                    raise ValueError("catalog generated vertices are not an array")
                expected = tuple(
                    tuple(int(word) for word in vertex[:2]) for vertex in generated
                )
                for predicted_vertex, expected_vertex in zip(
                    _geometry_bits(triangle), expected, strict=True
                ):
                    for predicted, actual in zip(
                        predicted_vertex, expected_vertex, strict=True
                    ):
                        geometry_deltas.append(
                            export._float_ulp_delta(actual, predicted)  # noqa: SLF001
                        )

        triangle = _selected_triangle(
            polygon,
            diagonal=diagonal,
            pixel=sample.pixel,
        )
        positions = setup._fixed_positions(triangle)  # noqa: SLF001
        anchor = top_left._top_left(positions)  # noqa: SLF001
        for component in range(4):
            for coefficient_axis in range(2):
                predicted = top_left._anchor_slope(  # noqa: SLF001
                    triangle,
                    component,
                    coefficient_axis,
                    p25_bitmap,
                    anchor,
                )
                actual = int(
                    actual_coefficients[
                        sample.record_index,
                        component * 3 + coefficient_axis,
                    ]
                )
                delta = export._float_ulp_delta(actual, predicted)  # noqa: SLF001
                predictions.append(predicted)
                actual_words.append(actual)
                deltas.append(delta)
                if delta and len(first_mismatches) < 16:
                    first_mismatches.append(
                        {
                            "caseIndex": case_index,
                            "state": sample.state,
                            "sourcePrimitive": sample.source_primitive,
                            "childOrdinalWithinSource": sample.child_ordinal_within_source,
                            "component": component,
                            "axis": coefficient_axis,
                            "actualBits": f"0x{actual:08x}",
                            "predictedBits": f"0x{predicted:08x}",
                            "actualMinusPredictedFloatUlps": delta,
                        }
                    )

    distribution = Counter(deltas)
    geometry_distribution = Counter(geometry_deltas)
    if len(selected_source_cases) != 48 or len(deltas) != 704:
        raise ValueError("one-plane fan census differs")
    if diagonal == "vertex-0-to-2" and (
        len(geometry_source_cases) != 48
        or len(geometry_deltas) != 576
        or geometry_distribution != {0: 520, -1: 31, 1: 22, -2: 2, 2: 1}
    ):
        raise ValueError("canonical child position comparison differs")
    return {
        "diagonal": diagonal,
        "onePlaneSourceCaseCount": len(selected_source_cases),
        "sampledChildCount": len(deltas) // 8,
        "slopeWordCount": len(deltas),
        "exactCount": distribution[0],
        "withinOneUlpCount": sum(
            count for delta, count in distribution.items() if abs(delta) <= 1
        ),
        "canonicalChildPositionWordCount": len(geometry_deltas),
        "canonicalChildPositionDeltaDistribution": {
            str(delta): count for delta, count in sorted(geometry_distribution.items())
        },
        "predictionSha256": hashlib.sha256(
            struct.pack(f"<{len(predictions)}I", *predictions)
        ).hexdigest(),
        "actualSha256": hashlib.sha256(
            struct.pack(f"<{len(actual_words)}I", *actual_words)
        ).hexdigest(),
        "deltaDistribution": {
            str(delta): count for delta, count in sorted(distribution.items())
        },
        "firstMismatches": first_mismatches,
    }


def _validate_prior_reports() -> JsonObject:
    order_report = order_analysis.analyze()
    order_encoded = (json.dumps(order_report, indent=2, sort_keys=True) + "\n").encode()
    if (
        hashlib.sha256(order_encoded).hexdigest()
        != EXPECTED_DEPENDENCIES[order_analysis.OUTPUT]
    ):
        raise ValueError("recomputed explicit-order report differs")

    top_left_report = top_left.analyze()
    top_left_encoded = (
        json.dumps(top_left_report, indent=2, sort_keys=True) + "\n"
    ).encode()
    if (
        hashlib.sha256(top_left_encoded).hexdigest()
        != EXPECTED_DEPENDENCIES[top_left.OUTPUT]
    ):
        raise ValueError("recomputed top-left report differs")
    return {
        "explicitOrderReportRecomputedSha256": hashlib.sha256(
            order_encoded
        ).hexdigest(),
        "topLeftSetupReportRecomputedSha256": hashlib.sha256(
            top_left_encoded
        ).hexdigest(),
    }


def analyze() -> JsonObject:
    dependencies = _verify_dependencies()
    prior_reports = _validate_prior_reports()
    catalog, samples = phase._load_catalog(order_analysis.CATALOG)  # noqa: SLF001
    _builtin_manifest, builtin_words, _builtin_raw = phase._load_capture(  # noqa: SLF001
        order_analysis.BUILTIN_CAPTURE,
        catalog_path=order_analysis.CATALOG,
        record_count=len(samples),
    )
    _explicit_manifest, explicit_words, _explicit_raw = phase._load_capture(  # noqa: SLF001
        EXPLICIT_CAPTURE,
        catalog_path=order_analysis.CATALOG,
        record_count=len(samples),
    )
    builtin_coefficients = order_analysis._capture_coefficients(builtin_words)  # noqa: SLF001
    explicit_coefficients = order_analysis._capture_coefficients(explicit_words)  # noqa: SLF001

    reciprocal_table = np.fromfile(endpoint.RECIPROCAL_TABLE, dtype="<u4")
    if reciprocal_table.size != endpoint.RECIPROCAL_ENTRY_COUNT:
        raise ValueError("reciprocal table entry count differs")
    p25_bitmap = setup.P25_PATH.read_bytes()

    comparisons: dict[str, JsonObject] = {}
    for capture_name, coefficients in (
        ("built-in-guard", builtin_coefficients),
        (f"explicit-order-{EXPLICIT_ORDER}", explicit_coefficients),
    ):
        for diagonal in ("vertex-0-to-2", "vertex-1-to-3"):
            comparisons[f"{capture_name}:{diagonal}"] = _comparison(
                samples=samples,
                catalog=catalog,
                actual_coefficients=coefficients,
                diagonal=diagonal,
                reciprocal_table=reciprocal_table,
                p25_bitmap=p25_bitmap,
            )

    expected = {
        "built-in-guard:vertex-0-to-2": (702, 704),
        "built-in-guard:vertex-1-to-3": (529, 626),
        f"explicit-order-{EXPLICIT_ORDER}:vertex-0-to-2": (536, 632),
        f"explicit-order-{EXPLICIT_ORDER}:vertex-1-to-3": (689, 698),
    }
    for name, (exact_count, within_one_count) in expected.items():
        comparison = comparisons[name]
        if (
            comparison["exactCount"] != exact_count
            or comparison["withinOneUlpCount"] != within_one_count
        ):
            raise ValueError(f"fan diagonal comparison differs for {name}")
    script = Path(__file__).resolve()
    return {
        "schemaVersion": 1,
        "classification": (
            "output-blind one-plane AGX viewport-guard fan-diagonal discriminator"
        ),
        "authority": {
            "referencePixelsRead": False,
            "renderedCoverageRead": False,
            "usesPublicRevealSourceGeometryOnly": True,
            "endpointArithmeticIndependentlyRecovered": True,
            "builtInOnePlaneCanonicalFanDiagonalEstablished": True,
            "explicitClipOppositeFanSetupStronglySupported": True,
            "tileConstantPipelineRecovered": False,
            "multiPlaneMaterializationRecovered": False,
            "productionIntegrationAuthorized": False,
        },
        "inputs": {
            "analyzer": _identity(script),
            "dependencies": dependencies,
            "priorReports": prior_reports,
        },
        "model": {
            "endpoint": (
                "captured exhaustive reciprocal, 24/18/17 truncated products, "
                "two weighted endpoint products, one binary32 addition"
            ),
            "positionMaterialization": "one binary32 endpoint materialization",
            "triangleSetup": (
                "1/256 positions, raster-order top-left anchor, 27-bit/P25 "
                "two-product slope setup"
            ),
            "canonicalDiagonal": "polygon vertices 0-2",
            "oppositeDiagonal": "polygon vertices 1-3",
        },
        "comparisons": comparisons,
        "conclusion": (
            "For all 88 sampled children of the 48 one-plane/one-outside quad "
            "sources, the built-in viewport guard selects the canonical vertex-0 "
            "to vertex-2 fan: 702 of 704 slope words are exact and all 704 are "
            "within one float ULP. The opposite diagonal is decisively worse. "
            "Explicit clip order 1203 has the complementary signature: its "
            "opposite diagonal explains 689 of 704 words exactly. The remaining "
            "built-in target is therefore setup accumulator/constant arithmetic "
            "and multi-plane materialization, not the one-plane fan diagonal."
        ),
        "nextExperiment": (
            "Recover the tile-local C accumulator from the same one-plane canonical "
            "children, then extend the endpoint lineage through multi-plane clips."
        ),
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=OUTPUT)
    return parser.parse_args()


def main() -> None:
    arguments = _parse_args()
    report = analyze()
    encoded = (json.dumps(report, indent=2, sort_keys=True) + "\n").encode()
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_bytes(encoded)
    print(
        json.dumps(
            {
                "output": str(arguments.output),
                "sha256": hashlib.sha256(encoded).hexdigest(),
                "builtInCanonicalExact": report["comparisons"][  # type: ignore[index]
                    "built-in-guard:vertex-0-to-2"
                ]["exactCount"],
                "builtInCanonicalWithinOneUlp": report["comparisons"][  # type: ignore[index]
                    "built-in-guard:vertex-0-to-2"
                ]["withinOneUlpCount"],
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
