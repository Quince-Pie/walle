#!/usr/bin/env python3.14
"""Generate single-axis real-child M1 probes for the two-source-product law."""

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
    ROOT / "build" / "analysis-agx-basis" / "single-axis-real-child-plan-v1"
)
ANCHOR_COUNT: Final = 4
VERTEX: Final = struct.Struct("<8I")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(4 * 1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _lane_values(
    geometry: tuple[Vertex, ...],
    tile: tuple[int, int],
    variable_offset: int,
    bitmap: bytes,
) -> tuple[list[tuple[float, ...]], JsonObject] | None:
    positions = accumulator.setup._fixed_positions(geometry)  # noqa: SLF001
    anchor = accumulator.top_left._top_left(positions)  # noqa: SLF001
    nonanchors = [index for index in range(3) if index != anchor]
    values = [
        accumulator.setup._float32(value)  # noqa: SLF001
        for value in ruler.BASE_VALUES
    ]
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
    if len(candidates) < ANCHOR_COUNT:
        return None
    selected = tomography._evenly_spaced(candidates, ANCHOR_COUNT)  # noqa: SLF001
    return [candidate[2] for candidate in selected], {
        "variableUlpOffset": variable_offset,
        "determinant": determinant,
        "middleTerm": {
            "sign": terms[0][0],
            "index": terms[0][1],
            "exponent": terms[0][2],
        },
        "anchors": [
            {"anchorUlpOffset": offset, "anchorBits": f"0x{bits:08x}"}
            for offset, bits, _values in selected
        ],
    }


def generate(output_directory: Path) -> JsonObject:
    if output_directory.exists():
        raise FileExistsError(output_directory)
    anchor_x, anchor_y = ruler.GEOMETRY[1]
    variants = (
        {
            "name": "x-zero-y-only",
            "zeroAxis": 0,
            "geometry": tuple(
                ((anchor_x - x) / 16.0, y, 0.0, 0.0, 0.0, 0.0)
                for x, y in ruler.GEOMETRY
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
            "pixel": (31, 624),
            "tile": (0, 19),
        },
    )
    bitmap = accumulator.setup.P25_PATH.read_bytes()
    vertices = bytearray()
    draws: list[JsonObject] = []
    experiments: list[JsonObject] = []
    variant_counts: dict[str, int] = {}
    split_counts = {"discovery": 0, "holdout": 0}

    for variant in variants:
        name = str(variant["name"])
        geometry = variant["geometry"]
        tile = variant["tile"]
        pixel = variant["pixel"]
        if (
            not isinstance(geometry, tuple)
            or not isinstance(tile, tuple)
            or not isinstance(pixel, tuple)
        ):
            raise AssertionError("variant shape differs")
        variant_count = 0
        for variable_offset in ruler.VARIABLE_ULP_OFFSETS:
            result = _lane_values(geometry, tile, variable_offset, bitmap)
            if result is None:
                continue
            lane_values, metadata = result
            semantic = f"{name}:{variable_offset}".encode()
            split = (
                "holdout" if hashlib.sha256(semantic).digest()[0] < 64 else "discovery"
            )
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
                            for values in lane_values
                        ),
                    )
                )
            experiments.append(
                {
                    "recordIndex": record,
                    "variant": name,
                    "zeroAxis": variant["zeroAxis"],
                    "split": split,
                    **metadata,
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
            variant_count += 1
            split_counts[split] += 1
        variant_counts[name] = variant_count

    output_directory.mkdir(parents=True)
    vertex_path = output_directory / "reveal-agx-setup-accumulator-vertices.bin"
    vertex_path.write_bytes(vertices)
    plan_path = output_directory / "reveal-agx-setup-accumulator-plan.json"
    census = {
        "targetCount": 8,
        "candidateCount": len(variants) * len(ruler.VARIABLE_ULP_OFFSETS),
        "skippedCount": len(variants) * len(ruler.VARIABLE_ULP_OFFSETS) - len(draws),
        "patternCount": len(draws),
        "drawCount": len(draws),
        "coefficientTripleCount": len(draws) * 4,
        "discoveryPatternCount": split_counts["discovery"],
        "holdoutPatternCount": split_counts["holdout"],
        "variantDrawCounts": variant_counts,
    }
    plan: JsonObject = {
        "schema": "walle-reveal-agx-setup-accumulator-plan-v1",
        "authority": {
            "opensReferencePixels": False,
            "usesOutputFeedback": False,
            "usesSingleAxisRealChildGeometry": True,
            "establishesTwoSourceProductLaw": False,
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
        "schema": "walle-reveal-agx-single-axis-real-child-plan-manifest-v1",
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
