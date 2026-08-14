#!/usr/bin/env python3.14
"""Generate cancellation-sensitive M1 probes for each AGX setup product."""

import argparse
import hashlib
import json
import math
import struct
import sys
from pathlib import Path
from typing import Final


ROOT: Final = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "analysis"), str(ROOT / "lg-test" / "Analysis")]

import analyze_reveal_agx_basis_phase as phase  # noqa: E402
import analyze_reveal_agx_join_preimage as preimage  # noqa: E402
import analyze_reveal_agx_setup_accumulator as accumulator  # noqa: E402
import analyze_reveal_agx_setup_tile_sweep as sweep  # noqa: E402
import generate_reveal_agx_setup_accumulator_plan as generator  # noqa: E402


type JsonObject = dict[str, object]
type Vertex = tuple[float, ...]

OUTPUT_DEFAULT: Final = (
    ROOT / "build" / "analysis-agx-basis" / "single-axis-product-plan-v1"
)
JOIN_RESULT: Final = (
    ROOT / "build" / "analysis-agx-basis" / "join-preimage" / "result.json"
)
SCALE_EXPONENTS: Final = tuple(range(-20, -7))
ANCHOR_ULP_OFFSETS: Final = (-8, -4, -2, -1, 0, 1, 2, 4, 8)
VERTEX: Final = struct.Struct("<8I")
SEARCH_RADIUS: Final = 64


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _ordered_key(bits: int) -> int:
    return (~bits & 0xFFFF_FFFF) if bits & 0x8000_0000 else bits | 0x8000_0000


def _bits_from_ordered_key(key: int) -> int:
    if not 0 <= key <= 0xFFFF_FFFF:
        raise ValueError("perturbed binary32 key escaped uint32")
    return (~key & 0xFFFF_FFFF) if key < 0x8000_0000 else key & 0x7FFF_FFFF


def _perturb(value: float, offset: int) -> float:
    bits = accumulator.setup._float_bits(value)  # noqa: SLF001
    result = accumulator.setup._float32(  # noqa: SLF001
        phase._float(_bits_from_ordered_key(_ordered_key(bits) + offset))  # noqa: SLF001
    )
    if not math.isfinite(result):
        raise ValueError("anchor perturbation generated a non-finite value")
    return result


def _with_lane_values(
    child: tuple[Vertex, ...], values: list[list[float]]
) -> tuple[Vertex, ...]:
    return tuple(
        tuple(vertex[:2]) + tuple(values[component][index] for component in range(4))
        for index, vertex in enumerate(child)
    )


def _single_lane_vertices(
    child: tuple[Vertex, ...], component: int, lane_values: tuple[float, float, float]
) -> tuple[Vertex, ...]:
    values = [[0.0, 0.0, 0.0] for _ in range(4)]
    values[component] = list(lane_values)
    return _with_lane_values(child, values)


def _isolated_values(
    child: tuple[Vertex, ...],
    tile: tuple[int, int],
    axis: int,
    direction: int,
    scale_exponent: int,
    anchor_ulp_offset: int,
    bitmap: bytes,
) -> tuple[tuple[float, float, float], JsonObject]:
    positions = accumulator.setup._fixed_positions(child)  # noqa: SLF001
    anchor = accumulator.top_left._top_left(positions)  # noqa: SLF001
    nonanchors = [index for index in range(3) if index != anchor]
    first, second = nonanchors
    edges = (
        (
            positions[1][1] - positions[2][1],
            positions[2][1] - positions[0][1],
            positions[0][1] - positions[1][1],
        ),
        (
            positions[2][0] - positions[1][0],
            positions[0][0] - positions[2][0],
            positions[1][0] - positions[0][0],
        ),
    )
    killed_axis = 1 - axis
    scale = math.ldexp(float(direction), scale_exponent)
    deltas = [0.0, 0.0, 0.0]
    deltas[first] = accumulator.setup._float32(  # noqa: SLF001
        edges[killed_axis][second] * scale
    )
    deltas[second] = accumulator.setup._float32(  # noqa: SLF001
        -edges[killed_axis][first] * scale
    )

    zero_anchor_vertices = _single_lane_vertices(child, 0, tuple(deltas))
    anchor_bits, determinant, terms = preimage._middle_terms(  # noqa: SLF001
        zero_anchor_vertices, 0, tile
    )
    if anchor_bits != 0 or len(terms) != 1:
        raise ValueError("zero-anchor pattern did not isolate exactly one product")
    sign, index, exponent = preimage._joined_index(terms)  # noqa: SLF001
    selector, selector_exponent = accumulator.setup._p25_selector(  # noqa: SLF001
        determinant, bitmap
    )
    coefficient_bits = preimage._constant_from_join(  # noqa: SLF001
        0, sign, index, exponent, selector, selector_exponent
    )
    if coefficient_bits is None:
        raise ValueError("isolated coefficient escaped the final product domain")
    coefficient = accumulator.export._fraction(coefficient_bits)  # noqa: SLF001
    cancellation_anchor = _perturb(
        accumulator.setup._float32(float(-coefficient)),  # noqa: SLF001
        anchor_ulp_offset,
    )
    lane_values = tuple(
        accumulator.setup._float32(cancellation_anchor + delta)  # noqa: SLF001
        for delta in deltas
    )
    submitted = _single_lane_vertices(child, 0, lane_values)
    actual_anchor_bits, actual_determinant, actual_terms = preimage._middle_terms(  # noqa: SLF001
        submitted, 0, tile
    )
    if actual_determinant != determinant or len(actual_terms) != 1:
        raise ValueError("cancellation anchor destroyed single-product isolation")
    actual_sign, actual_index, actual_exponent = preimage._joined_index(  # noqa: SLF001
        actual_terms
    )
    actual_selector, actual_selector_exponent = accumulator.setup._p25_selector(  # noqa: SLF001
        actual_determinant, bitmap
    )
    predicted = preimage._constant_from_join(  # noqa: SLF001
        actual_anchor_bits,
        actual_sign,
        actual_index,
        actual_exponent,
        actual_selector,
        actual_selector_exponent,
    )
    if predicted is None:
        raise ValueError("cancellation-sensitive coefficient escaped the domain")
    neighbors = {
        preimage._constant_from_join(  # noqa: SLF001
            actual_anchor_bits,
            actual_sign,
            actual_index + offset,
            actual_exponent,
            actual_selector,
            actual_selector_exponent,
        )
        for offset in range(-SEARCH_RADIUS, SEARCH_RADIUS + 1)
        if actual_index + offset > 0
    }
    neighbors.discard(None)
    if len(neighbors) < 16:
        raise ValueError("cancellation pattern does not expose enough product bits")
    return lane_values, {
        "isolatedAxis": axis,
        "direction": direction,
        "scaleExponent": scale_exponent,
        "anchorUlpOffset": anchor_ulp_offset,
        "anchorVertex": anchor,
        "predictedAnchorBits": f"0x{actual_anchor_bits:08x}",
        "predictedMiddleTerm": {
            "sign": actual_sign,
            "index": actual_index,
            "exponent": actual_exponent,
        },
        "predictedConstantBits": f"0x{predicted:08x}",
        "distinctConstantsAcrossPlusMinus64MiddleUlps": len(neighbors),
    }


def _load_targets(
    catalog_path: Path,
) -> tuple[list[JsonObject], dict[int, tuple[Vertex, ...]]]:
    result = json.loads(JOIN_RESULT.read_text(encoding="utf-8"))
    records = result.get("records")
    if (
        result.get("schema") != "walle-reveal-agx-join-preimage-analysis-v1"
        or not isinstance(records, list)
        or len(records) != 24
    ):
        raise ValueError("join-preimage evidence differs")
    _catalog, samples = phase._load_catalog(catalog_path)  # noqa: SLF001
    sample_by_record = {sample.record_index: sample for sample in samples}
    children: dict[int, tuple[Vertex, ...]] = {}
    targets: list[JsonObject] = []
    for target_index, target_record in enumerate(generator.TARGET_RECORDS):
        sample = sample_by_record[target_record]
        child = phase._canonical_children(sample)[  # noqa: SLF001
            sample.child_ordinal_within_source
        ]
        children[target_index] = child
        targets.append(
            {
                "targetIndex": target_index,
                "targetRecordIndex": target_record,
                "caseIndex": sample.case_index,
                "state": sample.state,
                "sourcePrimitive": sample.source_primitive,
                "childOrdinal": sample.child_ordinal,
                "childOrdinalWithinSource": sample.child_ordinal_within_source,
            }
        )
    return targets, children


def generate(catalog_path: Path, output_directory: Path) -> JsonObject:
    if output_directory.exists():
        raise FileExistsError(output_directory)
    result = json.loads(JOIN_RESULT.read_text(encoding="utf-8"))
    records = result["records"]
    targets, children = _load_targets(catalog_path)
    bitmap = accumulator.setup.P25_PATH.read_bytes()
    target_tiles = sorted(
        {
            (int(record["targetIndex"]), tuple(int(value) for value in record["tile"]))
            for record in records
        }
    )

    vertices = bytearray()
    draws: list[JsonObject] = []
    experiments: list[JsonObject] = []
    for target_index, tile in target_tiles:
        child = children[target_index]
        positions = accumulator.setup._fixed_positions(child)  # noqa: SLF001
        candidates = sweep.generator._interior_tiles(positions)  # noqa: SLF001
        by_tile = {sweep.generator._tile(pixel): pixel for pixel in candidates}  # noqa: SLF001
        if tile not in by_tile:
            raise ValueError("residual tile is not strictly interior")
        pixel = by_tile[tile]
        for scale_exponent in SCALE_EXPONENTS:
            for anchor_ulp_offset in ANCHOR_ULP_OFFSETS:
                lane_values: list[tuple[float, float, float]] = []
                lane_metadata: list[JsonObject] = []
                for axis, direction in ((0, 1), (0, -1), (1, 1), (1, -1)):
                    values, metadata = _isolated_values(
                        child,
                        tile,
                        axis,
                        direction,
                        scale_exponent,
                        anchor_ulp_offset,
                        bitmap,
                    )
                    lane_values.append(values)
                    lane_metadata.append(metadata)
                record_index = len(draws)
                submitted = _with_lane_values(
                    child, [list(values) for values in lane_values]
                )
                for vertex in submitted:
                    vertices.extend(
                        VERTEX.pack(
                            accumulator.setup._float_bits(vertex[0]),  # noqa: SLF001
                            accumulator.setup._float_bits(vertex[1]),  # noqa: SLF001
                            0,
                            0,
                            *(
                                accumulator.setup._float_bits(value)
                                for value in vertex[2:]
                            ),  # noqa: SLF001
                        )
                    )
                experiments.append(
                    {
                        "recordIndex": record_index,
                        "targetIndex": target_index,
                        "tile": list(tile),
                        "pixel": list(pixel),
                        "lanes": lane_metadata,
                    }
                )
                draws.append(
                    {
                        "recordIndex": record_index,
                        "targetIndex": target_index,
                        "targetRecordIndex": generator.TARGET_RECORDS[target_index],
                        "sampleRecordIndex": generator.TARGET_RECORDS[target_index],
                        "sampleOrdinal": 0,
                        "patternIndex": record_index,
                        "x": pixel[0],
                        "y": pixel[1],
                        "tileX": tile[0],
                        "tileY": tile[1],
                    }
                )

    output_directory.mkdir(parents=True)
    vertex_path = output_directory / "reveal-agx-setup-accumulator-vertices.bin"
    vertex_path.write_bytes(vertices)
    plan_path = output_directory / "reveal-agx-setup-accumulator-plan.json"
    plan: JsonObject = {
        "schema": "walle-reveal-agx-setup-accumulator-plan-v1",
        "authority": {
            "opensReferencePixels": False,
            "usesOutputFeedback": False,
            "usesRetainedM1CoefficientResidualLocations": True,
            "establishesAGXProductLaw": False,
        },
        "target": {"width": 2_048, "height": 2_048},
        "catalog": {
            "path": catalog_path.relative_to(ROOT).as_posix(),
            "bytes": catalog_path.stat().st_size,
            "sha256": _sha256(catalog_path),
        },
        "joinPreimageEvidence": {
            "path": JOIN_RESULT.relative_to(ROOT).as_posix(),
            "bytes": JOIN_RESULT.stat().st_size,
            "sha256": _sha256(JOIN_RESULT),
        },
        "vertexData": {
            "file": vertex_path.name,
            "bytes": len(vertices),
            "sha256": _sha256(vertex_path),
            "recordCount": len(draws),
            "verticesPerRecord": 3,
            "wordsPerVertex": 8,
            "layout": "positionXY,pad2,varyingRGBA; little-endian uint32",
        },
        "targets": targets,
        "experiments": experiments,
        "draws": draws,
        "census": {
            "targetCount": 8,
            "targetTileCount": len(target_tiles),
            "patternCount": len(draws),
            "drawCount": len(draws),
            "coefficientTripleCount": len(draws) * 4,
        },
    }
    plan_path.write_text(
        json.dumps(plan, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    manifest: JsonObject = {
        "schema": "walle-reveal-agx-single-axis-product-plan-manifest-v1",
        "generator": {
            "path": Path(__file__).relative_to(ROOT).as_posix(),
            "bytes": Path(__file__).stat().st_size,
            "sha256": _sha256(Path(__file__)),
        },
        "plan": {
            "file": plan_path.name,
            "bytes": plan_path.stat().st_size,
            "sha256": _sha256(plan_path),
        },
        "vertexData": {
            "file": vertex_path.name,
            "bytes": vertex_path.stat().st_size,
            "sha256": _sha256(vertex_path),
        },
        "sourceEvidence": {
            "joinPreimageSha256": _sha256(JOIN_RESULT),
            "catalogSha256": _sha256(catalog_path),
        },
        "census": plan["census"],
    }
    manifest_path = output_directory / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, default=generator.CATALOG_DEFAULT)
    parser.add_argument("--output", type=Path, default=OUTPUT_DEFAULT)
    arguments = parser.parse_args()
    manifest = generate(arguments.catalog, arguments.output)
    print(json.dumps(manifest["census"], indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
