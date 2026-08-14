#!/usr/bin/env python3.14
"""Generate determinant-amplified probes for the AGX two-product join."""

import argparse
import hashlib
import json
import struct
import sys
from fractions import Fraction
from pathlib import Path
from typing import Final


ROOT: Final = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "analysis"), str(ROOT / "lg-test" / "Analysis")]

import analyze_reveal_agx_basis_phase as phase  # noqa: E402
import analyze_reveal_agx_join_preimage as preimage  # noqa: E402
import analyze_reveal_agx_setup_accumulator as accumulator  # noqa: E402
import generate_reveal_agx_two_product_tomography_plan as tomography  # noqa: E402


type JsonObject = dict[str, object]
type Vertex = tuple[float, ...]

OUTPUT_DEFAULT: Final = (
    ROOT / "build" / "analysis-agx-basis" / "two-product-amplification-plan-v1"
)
ULP_OFFSETS: Final = (-256, -64, -16, -1, 0, 1, 16, 64, 256)
DETERMINANT_RATIOS: Final = tuple(
    Fraction(value)
    for value in (
        "1/19",
        "3/28",
        "7/32",
        "1/591",
        "13/32",
        "3/29",
        "1/22",
        "5/28",
        "1/29",
        "9/32",
    )
)
ANCHOR_PROBE_OFFSETS: Final = (
    -512,
    -384,
    -256,
    -192,
    -128,
    -64,
    -32,
    -16,
    -8,
    0,
    8,
    16,
    32,
    64,
    128,
    192,
    256,
    384,
    512,
)
INTERIOR_DENOMINATOR: Final = 64
GUARD_LOW_FIXED: Final = -512 * 256
GUARD_HIGH_FIXED: Final = 2_560 * 256
VERTEX: Final = struct.Struct("<8I")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(4 * 1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _extended_gcd(left: int, right: int) -> tuple[int, int, int]:
    if right == 0:
        return abs(left), 1 if left >= 0 else -1, 0
    divisor, x, y = _extended_gcd(right, left % right)
    return divisor, y, x - (left // right) * y


def _cross(
    first: tuple[int, int], second: tuple[int, int], point: tuple[int, int]
) -> int:
    return (second[0] - first[0]) * (point[1] - first[1]) - (second[1] - first[1]) * (
        point[0] - first[0]
    )


def _strictly_inside(
    positions: tuple[tuple[int, int], ...], point: tuple[int, int]
) -> bool:
    signs = tuple(
        _cross(positions[index], positions[(index + 1) % 3], point)
        for index in range(3)
    )
    return all(value > 0 for value in signs) or all(value < 0 for value in signs)


def _amplified_geometry(case: JsonObject, ratio: Fraction) -> tuple[Vertex, ...]:
    positions_value = case.get("positions")
    pixel_value = case.get("pixel")
    if not isinstance(positions_value, list) or not isinstance(pixel_value, list):
        raise ValueError("base-case geometry differs")
    source_vertices = tuple(
        (float(position[0]), float(position[1]), 0.0, 0.0, 0.0, 0.0)
        for position in positions_value
    )
    positions = accumulator.setup._fixed_positions(source_vertices)  # noqa: SLF001
    anchor = accumulator.top_left._top_left(positions)  # noqa: SLF001
    anchor_position = positions[anchor]
    sample = (int(pixel_value[0]) * 256 + 128, int(pixel_value[1]) * 256 + 128)
    displacement = (
        sample[0] - anchor_position[0],
        sample[1] - anchor_position[1],
    )
    if any(value % INTERIOR_DENOMINATOR for value in displacement):
        raise ValueError("sample displacement does not fill the interior lattice")
    radial = tuple(value // INTERIOR_DENOMINATOR for value in displacement)
    divisor, coefficient_x, coefficient_y = _extended_gcd(*radial)
    source_determinant = accumulator.setup._determinant(positions)  # noqa: SLF001
    target_magnitude = round(abs(source_determinant) * ratio)
    target_cross = max(divisor, round(target_magnitude / 129 / divisor) * divisor)
    target_cross *= -1 if source_determinant > 0 else 1

    # radial.x * width.y - radial.y * width.x == target_cross
    width_x = -coefficient_y * (target_cross // divisor)
    width_y = coefficient_x * (target_cross // divisor)
    homogeneous = (radial[0] // divisor, radial[1] // divisor)
    denominator = homogeneous[0] ** 2 + homogeneous[1] ** 2
    phase_index = round(
        -(width_x * homogeneous[0] + width_y * homogeneous[1]) / denominator
    )
    width = (
        width_x + homogeneous[0] * phase_index,
        width_y + homogeneous[1] * phase_index,
    )
    first = (
        anchor_position[0] + displacement[0] + width[0],
        anchor_position[1] + displacement[1] + width[1],
    )
    second = (
        anchor_position[0] + displacement[0] + radial[0] - width[0],
        anchor_position[1] + displacement[1] + radial[1] - width[1],
    )
    amplified_positions: list[tuple[int, int] | None] = [None, None, None]
    amplified_positions[anchor] = anchor_position
    nonanchors = [index for index in range(3) if index != anchor]
    amplified_positions[nonanchors[0]] = first
    amplified_positions[nonanchors[1]] = second
    if any(position is None for position in amplified_positions):
        raise AssertionError("amplified position assignment is incomplete")
    concrete = tuple(
        position for position in amplified_positions if position is not None
    )
    determinant = accumulator.setup._determinant(concrete)  # noqa: SLF001
    if determinant != 0 and (determinant > 0) != (source_determinant > 0):
        amplified_positions[nonanchors[0]], amplified_positions[nonanchors[1]] = (
            amplified_positions[nonanchors[1]],
            amplified_positions[nonanchors[0]],
        )
        concrete = tuple(
            position for position in amplified_positions if position is not None
        )
    if any(
        not GUARD_LOW_FIXED <= coordinate <= GUARD_HIGH_FIXED
        for position in concrete
        for coordinate in position
    ) or not _strictly_inside(concrete, sample):
        raise ValueError("amplified triangle escaped the guard or sample interior")
    result = tuple((x / 256.0, y / 256.0, 0.0, 0.0, 0.0, 0.0) for x, y in concrete)
    fixed = accumulator.setup._fixed_positions(result)  # noqa: SLF001
    determinant = accumulator.setup._determinant(fixed)  # noqa: SLF001
    if (
        fixed != concrete
        or accumulator.top_left._top_left(fixed) != anchor  # noqa: SLF001
        or determinant == 0
        or (determinant > 0) != (source_determinant > 0)
    ):
        raise ValueError(
            "amplified triangle materialization differs: "
            f"source={positions} fixed={fixed} anchor={anchor} "
            f"actual_anchor={accumulator.top_left._top_left(fixed)} "  # noqa: SLF001
            f"determinant={determinant} source_determinant={source_determinant}"
        )
    return result


def _lane_values(
    case: JsonObject,
    geometry: tuple[Vertex, ...],
    first_offset: int,
    second_offset: int,
    bitmap: bytes,
) -> tuple[list[tuple[float, ...]], JsonObject] | None:
    values_value = case.get("values")
    tile_value = case.get("tile")
    if not isinstance(values_value, list) or not isinstance(tile_value, list):
        raise ValueError("base-case values differ")
    values = [
        accumulator.setup._float32(float(value))  # noqa: SLF001
        for value in values_value
    ]
    positions = accumulator.setup._fixed_positions(geometry)  # noqa: SLF001
    anchor = accumulator.top_left._top_left(positions)  # noqa: SLF001
    nonanchors = [index for index in range(3) if index != anchor]
    values[nonanchors[0]] = tomography._perturb(values[nonanchors[0]], first_offset)  # noqa: SLF001
    values[nonanchors[1]] = tomography._perturb(  # noqa: SLF001
        values[nonanchors[1]], second_offset
    )
    differences = tuple(
        accumulator.setup._float32(value - values[anchor])  # noqa: SLF001
        for value in values
    )
    zero_vertices = tuple(
        vertex[:2] + (differences[index], 0.0, 0.0, 0.0)
        for index, vertex in enumerate(geometry)
    )
    tile = (int(tile_value[0]), int(tile_value[1]))
    try:
        zero_anchor, determinant, terms = preimage._middle_terms(  # noqa: SLF001
            zero_vertices, 0, tile
        )
        sign, index, exponent = preimage._joined_index(terms)  # noqa: SLF001
    except ValueError:
        return None
    if zero_anchor != 0 or len(terms) != 2 or terms[0][0] == terms[1][0] or sign == 0:
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
    for anchor_offset in ANCHOR_PROBE_OFFSETS:
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
    if len(candidates) < 4:
        return None
    selected = tomography._evenly_spaced(candidates, 4)  # noqa: SLF001
    return [candidate[2] for candidate in selected], {
        "determinant": determinant,
        "firstNonanchorUlpOffset": first_offset,
        "secondNonanchorUlpOffset": second_offset,
        "middleTerms": [
            {"sign": term_sign, "index": term_index, "exponent": term_exponent}
            for term_sign, term_index, term_exponent in terms
        ],
        "predictedJoin": {"sign": sign, "index": index, "exponent": exponent},
        "anchors": [
            {"anchorUlpOffset": offset, "anchorBits": f"0x{bits:08x}"}
            for offset, bits, _values in selected
        ],
    }


def _with_lane_values(
    geometry: tuple[Vertex, ...], lane_values: list[tuple[float, ...]]
) -> tuple[Vertex, ...]:
    return tuple(
        vertex[:2] + tuple(values[vertex_index] for values in lane_values)
        for vertex_index, vertex in enumerate(geometry)
    )


def generate(output_directory: Path) -> JsonObject:
    if output_directory.exists():
        raise FileExistsError(output_directory)
    bitmap = accumulator.setup.P25_PATH.read_bytes()
    cases = tomography._base_cases()  # noqa: SLF001
    vertices = bytearray()
    draws: list[JsonObject] = []
    experiments: list[JsonObject] = []
    skipped = 0
    split_counts = {"discovery": 0, "holdout": 0}

    for case_index, case in enumerate(cases):
        for ratio_index, ratio in enumerate(DETERMINANT_RATIOS):
            geometry = _amplified_geometry(case, ratio)
            for first_offset in ULP_OFFSETS:
                for second_offset in ULP_OFFSETS:
                    result = _lane_values(
                        case,
                        geometry,
                        first_offset,
                        second_offset,
                        bitmap,
                    )
                    if result is None:
                        skipped += 1
                        continue
                    lane_values, metadata = result
                    semantic = json.dumps(
                        {
                            "case": case_index,
                            "first": first_offset,
                            "second": second_offset,
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode()
                    split = (
                        "holdout"
                        if hashlib.sha256(semantic).digest()[0] < 64
                        else "discovery"
                    )
                    record = len(draws)
                    submitted = _with_lane_values(geometry, lane_values)
                    for vertex in submitted:
                        vertices.extend(
                            VERTEX.pack(
                                accumulator.setup._float_bits(vertex[0]),  # noqa: SLF001
                                accumulator.setup._float_bits(vertex[1]),  # noqa: SLF001
                                0,
                                0,
                                *(
                                    accumulator.setup._float_bits(value)  # noqa: SLF001
                                    for value in vertex[2:]
                                ),
                            )
                        )
                    pixel = case["pixel"]
                    tile = case["tile"]
                    experiments.append(
                        {
                            "recordIndex": record,
                            "caseIndex": case_index,
                            "ratioIndex": ratio_index,
                            "determinantRatio": str(ratio),
                            "split": split,
                            **metadata,
                        }
                    )
                    draws.append(
                        {
                            "recordIndex": record,
                            "targetIndex": int(case["targetIndex"]),
                            "targetRecordIndex": int(case["targetRecordIndex"]),
                            "sampleRecordIndex": int(case["sourceRecordIndex"]),
                            "sampleOrdinal": 0,
                            "patternIndex": record,
                            "x": pixel[0],  # type: ignore[index]
                            "y": pixel[1],  # type: ignore[index]
                            "tileX": tile[0],  # type: ignore[index]
                            "tileY": tile[1],  # type: ignore[index]
                        }
                    )
                    split_counts[split] += 1

    output_directory.mkdir(parents=True)
    vertex_path = output_directory / "reveal-agx-setup-accumulator-vertices.bin"
    vertex_path.write_bytes(vertices)
    plan_path = output_directory / "reveal-agx-setup-accumulator-plan.json"
    census = {
        "targetCount": 8,
        "baseCaseCount": len(cases),
        "offsetCountPerNonanchor": len(ULP_OFFSETS),
        "determinantRatioCount": len(DETERMINANT_RATIOS),
        "candidateCount": len(cases) * len(ULP_OFFSETS) ** 2 * len(DETERMINANT_RATIOS),
        "skippedCount": skipped,
        "patternCount": len(draws),
        "drawCount": len(draws),
        "coefficientTripleCount": len(draws) * 4,
        "discoveryPatternCount": split_counts["discovery"],
        "holdoutPatternCount": split_counts["holdout"],
    }
    plan: JsonObject = {
        "schema": "walle-reveal-agx-setup-accumulator-plan-v1",
        "authority": {
            "opensReferencePixels": False,
            "usesOutputFeedback": False,
            "usesKnownWideTileOperandFamilies": True,
            "usesDeterminantAmplification": True,
            "establishesTwoProductInteractionLaw": False,
        },
        "target": {"width": 2_048, "height": 2_048},
        "offsets": list(ULP_OFFSETS),
        "determinantRatios": [str(ratio) for ratio in DETERMINANT_RATIOS],
        "vertexData": {
            "file": vertex_path.name,
            "bytes": len(vertices),
            "sha256": _sha256(vertex_path),
            "recordCount": len(draws),
            "verticesPerRecord": 3,
            "wordsPerVertex": 8,
            "layout": "positionXY,pad2,varyingRGBA; little-endian uint32",
        },
        "experiments": experiments,
        "draws": draws,
        "census": census,
    }
    plan_path.write_text(
        json.dumps(plan, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    manifest: JsonObject = {
        "schema": "walle-reveal-agx-two-product-amplification-plan-manifest-v1",
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
    result = generate(arguments.output)
    print(json.dumps(result["census"], indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
