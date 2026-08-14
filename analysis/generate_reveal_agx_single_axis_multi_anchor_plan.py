#!/usr/bin/env python3.14
"""Generate all-anchor single-axis M1 probes for the retained p28 setup term."""

import argparse
import hashlib
import json
import struct
from pathlib import Path
from typing import Final

import analyze_reveal_agx_basis_phase as phase
import analyze_reveal_agx_join_preimage as preimage
import analyze_reveal_agx_setup_accumulator as accumulator
import generate_reveal_agx_public_child_mantissa_ruler_plan as ruler
import generate_reveal_agx_two_product_tomography_plan as tomography


type JsonObject = dict[str, object]
type Vertex = tuple[float, ...]

ROOT: Final = Path(__file__).resolve().parent.parent
OUTPUT_DEFAULT: Final = (
    ROOT / "build" / "analysis-agx-basis" / "single-axis-multi-anchor-plan-v1"
)
VERTEX: Final = struct.Struct("<8I")
GROUP_STARTS: Final = (0, 4, 8, 12, 15)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(4 * 1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _all_candidates(
    geometry: tuple[Vertex, ...],
    base_values: tuple[float, ...],
    tile: tuple[int, int],
    variable_offset: int,
    bitmap: bytes,
) -> tuple[list[tuple[int, int, tuple[float, ...]]], JsonObject] | None:
    positions = accumulator.setup._fixed_positions(geometry)  # noqa: SLF001
    anchor = accumulator.top_left._top_left(positions)  # noqa: SLF001
    nonanchors = [index for index in range(3) if index != anchor]
    values = [accumulator.setup._float32(value) for value in base_values]  # noqa: SLF001
    values[nonanchors[1]] = tomography._perturb(  # noqa: SLF001
        values[nonanchors[1]], variable_offset
    )
    differences = tuple(
        accumulator.setup._float32(value - values[anchor])  # noqa: SLF001
        for value in values
    )
    zero_vertices = tuple(
        vertex[:2] + (differences[index], 0.0, 0.0, 0.0)
        for index, vertex in enumerate(geometry)
    )
    try:
        zero_anchor, determinant, terms = preimage._middle_terms(  # noqa: SLF001
            zero_vertices, 0, tile
        )
        sign, index, exponent = preimage._joined_index(terms)  # noqa: SLF001
    except ValueError:
        return None
    if zero_anchor != 0 or len(terms) != 1 or sign == 0:
        return None
    selector, selector_exponent = accumulator.setup._p25_selector(  # noqa: SLF001
        determinant, bitmap
    )
    zero_coefficient = preimage._constant_from_join(  # noqa: SLF001
        0, sign, index, exponent, selector, selector_exponent
    )
    if zero_coefficient is None:
        return None
    cancellation_anchor = accumulator.setup._float32(  # noqa: SLF001
        float(-accumulator.export._fraction(zero_coefficient))  # noqa: SLF001
    )
    center_key = tomography._ordered_key(  # noqa: SLF001
        accumulator.setup._float_bits(cancellation_anchor)  # noqa: SLF001
    )
    candidates: list[tuple[int, int, tuple[float, ...]]] = []
    for anchor_offset in tomography.ANCHOR_PROBE_OFFSETS:
        key = center_key + anchor_offset
        if not 0 <= key <= 0xFFFF_FFFF:
            continue
        common = accumulator.setup._float32(  # noqa: SLF001
            phase._float(tomography._bits_from_ordered_key(key))  # noqa: SLF001
        )
        submitted_values = tuple(
            accumulator.setup._float32(common + difference)  # noqa: SLF001
            for difference in differences
        )
        submitted = tuple(
            vertex[:2] + (submitted_values[index], 0.0, 0.0, 0.0)
            for index, vertex in enumerate(geometry)
        )
        try:
            actual_anchor, actual_determinant, actual_terms = preimage._middle_terms(  # noqa: SLF001
                submitted, 0, tile
            )
        except ValueError:
            continue
        if actual_determinant == determinant and actual_terms == terms:
            candidates.append((anchor_offset, actual_anchor, submitted_values))
    if len(candidates) != len(tomography.ANCHOR_PROBE_OFFSETS):
        return None
    return candidates, {
        "variableUlpOffset": variable_offset,
        "determinant": determinant,
        "middleTerm": {
            "sign": terms[0][0],
            "index": terms[0][1],
            "exponent": terms[0][2],
        },
    }


def generate(output_directory: Path) -> JsonObject:
    if output_directory.exists():
        raise FileExistsError(output_directory)
    anchor_x, anchor_y = ruler.GEOMETRY[1]
    x_geometry_unordered = tuple(
        ((anchor_x - x) / 16.0, y, 0.0, 0.0, 0.0, 0.0) for x, y in ruler.GEOMETRY
    )
    variants = (
        {
            "name": "x-zero-y-only",
            "zeroAxis": 0,
            "geometry": (
                x_geometry_unordered[2],
                x_geometry_unordered[1],
                x_geometry_unordered[0],
            ),
            "baseValues": (
                ruler.BASE_VALUES[2],
                ruler.BASE_VALUES[1],
                ruler.BASE_VALUES[0],
            ),
            "pixel": (28, 1664),
            "tile": (0, 52),
        },
        {
            "name": "y-zero-x-only",
            "zeroAxis": 1,
            "geometry": tuple(
                (x, 608.0 + (y - anchor_y) / 64.0, 0.0, 0.0, 0.0, 0.0)
                for x, y in ruler.GEOMETRY
            ),
            "baseValues": ruler.BASE_VALUES,
            "pixel": (31, 624),
            "tile": (0, 19),
        },
    )
    bitmap = accumulator.setup.P25_PATH.read_bytes()
    vertices = bytearray()
    draws: list[JsonObject] = []
    experiments: list[JsonObject] = []
    variant_input_counts: dict[str, int] = {}
    split_input_counts = {"discovery": 0, "holdout": 0}

    for variant in variants:
        name = str(variant["name"])
        geometry = variant["geometry"]
        base_values = variant["baseValues"]
        tile = variant["tile"]
        pixel = variant["pixel"]
        if not all(
            isinstance(value, tuple) for value in (geometry, base_values, tile, pixel)
        ):
            raise AssertionError("variant shape differs")
        input_count = 0
        for variable_offset in ruler.VARIABLE_ULP_OFFSETS:
            result = _all_candidates(
                geometry, base_values, tile, variable_offset, bitmap
            )
            if result is None:
                continue
            candidates, metadata = result
            semantic = f"{name}:{variable_offset}".encode()
            split = (
                "holdout" if hashlib.sha256(semantic).digest()[0] < 64 else "discovery"
            )
            input_ordinal = input_count
            for group_index, start in enumerate(GROUP_STARTS):
                selected = candidates[start : start + 4]
                record = len(draws)
                for vertex_index, vertex in enumerate(geometry):
                    vertices.extend(
                        VERTEX.pack(
                            accumulator.setup._float_bits(vertex[0]),  # noqa: SLF001
                            accumulator.setup._float_bits(vertex[1]),  # noqa: SLF001
                            0,
                            0,
                            *(
                                accumulator.setup._float_bits(values[vertex_index])  # noqa: SLF001
                                for _offset, _bits, values in selected
                            ),
                        )
                    )
                experiments.append(
                    {
                        "recordIndex": record,
                        "inputOrdinal": input_ordinal,
                        "anchorGroupIndex": group_index,
                        "variant": name,
                        "zeroAxis": variant["zeroAxis"],
                        "split": split,
                        **metadata,
                        "anchors": [
                            {"anchorUlpOffset": offset, "anchorBits": f"0x{bits:08x}"}
                            for offset, bits, _values in selected
                        ],
                    }
                )
                draws.append(
                    {
                        "recordIndex": record,
                        "targetIndex": 7,
                        "targetRecordIndex": 484,
                        "sampleRecordIndex": 2528,
                        "sampleOrdinal": 0,
                        "patternIndex": record,
                        "x": pixel[0],
                        "y": pixel[1],
                        "tileX": tile[0],
                        "tileY": tile[1],
                    }
                )
            input_count += 1
            split_input_counts[split] += 1
        variant_input_counts[name] = input_count

    output_directory.mkdir(parents=True)
    vertex_path = output_directory / "reveal-agx-setup-accumulator-vertices.bin"
    vertex_path.write_bytes(vertices)
    plan_path = output_directory / "reveal-agx-setup-accumulator-plan.json"
    input_count = sum(variant_input_counts.values())
    census = {
        "targetCount": 8,
        "candidateInputCount": len(variants) * len(ruler.VARIABLE_ULP_OFFSETS),
        "retainedInputCount": input_count,
        "skippedInputCount": len(variants) * len(ruler.VARIABLE_ULP_OFFSETS)
        - input_count,
        "anchorCountPerInput": len(tomography.ANCHOR_PROBE_OFFSETS),
        "drawsPerInput": len(GROUP_STARTS),
        "patternCount": len(draws),
        "drawCount": len(draws),
        "coefficientTripleCount": len(draws) * 4,
        "discoveryInputCount": split_input_counts["discovery"],
        "holdoutInputCount": split_input_counts["holdout"],
        "variantInputCounts": variant_input_counts,
    }
    plan: JsonObject = {
        "schema": "walle-reveal-agx-setup-accumulator-plan-v1",
        "authority": {
            "opensReferencePixels": False,
            "usesOutputFeedback": False,
            "usesSingleAxisRealChildGeometry": True,
            "establishesRetainedP28Law": False,
        },
        "target": {"width": 2_048, "height": 2_048},
        "vertexData": {
            "file": vertex_path.name,
            "bytes": len(vertices),
            "sha256": _sha256(vertex_path),
            "recordCount": len(draws),
            "verticesPerRecord": 3,
            "wordsPerVertex": 8,
            "layout": "positionXY,pad2,varyingRGBA; little-endian uint32",
        },
        "variants": [
            {
                "name": variant["name"],
                "zeroAxis": variant["zeroAxis"],
                "geometry": [list(vertex[:2]) for vertex in variant["geometry"]],  # type: ignore[union-attr]
                "baseValueBits": [
                    f"0x{accumulator.setup._float_bits(value):08x}"  # noqa: SLF001
                    for value in variant["baseValues"]  # type: ignore[union-attr]
                ],
                "pixel": list(variant["pixel"]),  # type: ignore[arg-type]
                "tile": list(variant["tile"]),  # type: ignore[arg-type]
            }
            for variant in variants
        ],
        "experiments": experiments,
        "draws": draws,
        "census": census,
    }
    plan_path.write_text(
        json.dumps(plan, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    manifest: JsonObject = {
        "schema": "walle-reveal-agx-single-axis-multi-anchor-plan-manifest-v1",
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
        "census": census,
    }
    (output_directory / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT_DEFAULT)
    arguments = parser.parse_args()
    print(json.dumps(generate(arguments.output)["census"], indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
