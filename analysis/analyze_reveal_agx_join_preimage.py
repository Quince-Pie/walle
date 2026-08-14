#!/usr/bin/env python3.14
"""Invert the final AGX setup stages around the remaining p28 join errors.

The wide-tile M1 capture is already exact for every zero- and one-term setup
constant.  This analyzer keeps those established stages fixed and asks which
p28 joined indices could have produced each observed two-term constant.  It
uses raw coefficient exports only; no rendered output is opened.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from fractions import Fraction
from pathlib import Path
from typing import Final

import numpy as np


ROOT: Final = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "analysis"), str(ROOT / "lg-test" / "Analysis")]

import analyze_reveal_agx_setup_accumulator as accumulator  # noqa: E402
import analyze_reveal_agx_setup_tile_sweep as sweep  # noqa: E402


type JsonObject = dict[str, object]
type Vertex = tuple[float, ...]
type MiddleTerm = tuple[int, int, int]

OUTPUT: Final = ROOT / "build" / "analysis-agx-basis" / "join-preimage" / "result.json"
SEARCH_RADIUS: Final = 4_096


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(4 * 1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _middle_terms(
    vertices: tuple[Vertex, Vertex, Vertex],
    component: int,
    tile_position: tuple[int, int],
) -> tuple[int, int, tuple[MiddleTerm, ...]]:
    positions = accumulator.setup._fixed_positions(vertices)  # noqa: SLF001
    determinant = accumulator.setup._determinant(positions)  # noqa: SLF001
    anchor = accumulator.top_left._top_left(positions)  # noqa: SLF001
    values = tuple(
        accumulator.setup._float32(vertex[2 + component])  # noqa: SLF001
        for vertex in vertices
    )
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
    terms: list[MiddleTerm] = []
    for axis in range(2):
        numerator = sum(
            (
                accumulator.setup._first_product(  # noqa: SLF001
                    accumulator.setup._float32(  # noqa: SLF001
                        values[index] - values[anchor]
                    ),
                    edges[axis][index] / 256.0,
                    bias_units=15,
                )
                for index in range(3)
                if index != anchor
            ),
            start=Fraction(),
        )
        sign, numerator_index, numerator_exponent = accumulator.setup._normalize_signed(  # noqa: SLF001
            numerator, precision_bits=27, rounding="nearest-even"
        )
        displacement = Fraction(
            tile_position[axis] * 32 * 256 - positions[anchor][axis], 256
        )
        if sign == 0 or displacement == 0:
            continue
        distance_bits = accumulator.setup._float_bits(  # noqa: SLF001
            float(abs(displacement))
        )
        distance_index, distance_exponent = accumulator._positive_float_components(
            distance_bits
        )  # noqa: SLF001
        middle_index, middle_exponent = accumulator.coefficient.column_product_stage(
            numerator_index,
            numerator_exponent,
            distance_index,
            distance_exponent,
            output_bits=27,
            truncation_bits=19,
            bias_units=10,
            carry_mode="top-columns",
            propagated_column_count=1,
            sticky_carry_limit=1,
        )
        terms.append(
            (
                sign * (-1 if displacement < 0 else 1),
                middle_index,
                middle_exponent,
            )
        )
    return accumulator.setup._float_bits(values[anchor]), determinant, tuple(terms)  # noqa: SLF001


def _joined_index(terms: tuple[MiddleTerm, ...]) -> tuple[int, int, int]:
    joined = sum(
        (
            sign * index * accumulator._power_of_two(exponent)  # noqa: SLF001
            for sign, index, exponent in terms
        ),
        start=Fraction(),
    )
    if joined == 0:
        return 0, 0, 0
    return accumulator.setup._normalize_signed(  # noqa: SLF001
        joined, precision_bits=28, rounding="nearest-even"
    )


def _constant_from_join(
    anchor_bits: int,
    sign: int,
    index: int,
    exponent: int,
    selector: int,
    selector_exponent: int,
) -> int | None:
    if sign == 0:
        return anchor_bits
    try:
        coefficient_index, coefficient_exponent = accumulator.tile.product_stage(
            index,
            exponent,
            selector,
            selector_exponent,
            output_bits=27,
            truncation_bits=20,
            bias_units=20,
        )
    except ValueError:
        return None
    coefficient = (
        sign * coefficient_index * accumulator._power_of_two(coefficient_exponent)  # noqa: SLF001
    )
    return accumulator.composite.quantize_composite_constant_bits(
        accumulator.export._fraction(anchor_bits) + coefficient  # noqa: SLF001
    )


def _compatible_offsets(
    actual_bits: int,
    anchor_bits: int,
    sign: int,
    index: int,
    exponent: int,
    selector: int,
    selector_exponent: int,
) -> tuple[int, ...]:
    return tuple(
        offset
        for offset in range(-SEARCH_RADIUS, SEARCH_RADIUS + 1)
        if index + offset > 0
        and _constant_from_join(
            anchor_bits,
            sign,
            index + offset,
            exponent,
            selector,
            selector_exponent,
        )
        == actual_bits
    )


def _identity(path: Path) -> JsonObject:
    return {
        "path": path.relative_to(ROOT).as_posix(),
        "bytes": path.stat().st_size,
        "sha256": _sha256(path),
    }


def analyze() -> JsonObject:
    plans = sweep._load_plans()  # noqa: SLF001
    captures = sweep._load_captures(plans)  # noqa: SLF001
    bitmap = accumulator.setup.P25_PATH.read_bytes()
    records: list[JsonObject] = []
    output_deltas: list[int] = []
    unique_offset_counts = Counter[int]()

    for batch, (plan, words) in enumerate(zip(plans, captures, strict=True)):
        vertex_path = (
            sweep.PLAN_ROOT
            / f"batch-{batch}"
            / "reveal-agx-setup-accumulator-vertices.bin"
        )
        vertex_words = np.fromfile(vertex_path, dtype="<u4").reshape(-1, 3, 8)
        for draw_value in sweep._require_list(plan.get("draws"), "draws"):  # noqa: SLF001
            draw = sweep._require_dict(draw_value, "draw")  # noqa: SLF001
            record_index = sweep._require_int(  # noqa: SLF001
                draw.get("recordIndex"), "record index"
            )
            vertices = sweep._vertices(vertex_words, record_index)  # noqa: SLF001
            tile = (
                sweep._require_int(draw.get("tileX"), "tile x"),  # noqa: SLF001
                sweep._require_int(draw.get("tileY"), "tile y"),  # noqa: SLF001
            )
            for component, triple in enumerate(
                accumulator._triples(words[record_index])  # noqa: SLF001
            ):
                anchor, determinant, terms = _middle_terms(vertices, component, tile)
                sign, index, exponent = _joined_index(terms)
                selector, selector_exponent = accumulator.setup._p25_selector(  # noqa: SLF001
                    determinant, bitmap
                )
                predicted = _constant_from_join(
                    anchor,
                    sign,
                    index,
                    exponent,
                    selector,
                    selector_exponent,
                )
                if predicted is None:
                    raise ValueError("baseline joined index escaped the product domain")
                delta = accumulator.export._float_ulp_delta(  # noqa: SLF001
                    triple[2], predicted
                )
                if delta == 0:
                    continue
                offsets = _compatible_offsets(
                    triple[2],
                    anchor,
                    sign,
                    index,
                    exponent,
                    selector,
                    selector_exponent,
                )
                if not offsets:
                    raise ValueError("p28 preimage escaped the bounded offset search")
                output_deltas.append(delta)
                if len(offsets) == 1:
                    unique_offset_counts[offsets[0]] += 1
                records.append(
                    {
                        "batch": batch,
                        "targetIndex": draw["targetIndex"],
                        "patternIndex": draw["patternIndex"],
                        "component": component,
                        "tile": list(tile),
                        "termCount": len(terms),
                        "termSigns": [term[0] for term in terms],
                        "predictedJoin": {
                            "sign": sign,
                            "index": index,
                            "exponent": exponent,
                        },
                        "actualBits": f"0x{triple[2]:08x}",
                        "predictedBits": f"0x{predicted:08x}",
                        "actualMinusPredictedFloatUlps": delta,
                        "compatibleJoinOffset": {
                            "minimum": offsets[0],
                            "maximum": offsets[-1],
                            "count": len(offsets),
                            "contiguous": offsets
                            == tuple(range(offsets[0], offsets[-1] + 1)),
                        },
                    }
                )

    if len(records) != 24 or any(record["termCount"] != 2 for record in records):
        raise ValueError("remaining wide-tile residual census differs")
    if any(record["termSigns"] not in ([1, -1], [-1, 1]) for record in records):
        raise ValueError("a remaining residual is not opposite-sign cancellation")

    return {
        "schema": "walle-reveal-agx-join-preimage-analysis-v1",
        "classification": "output-blind p28 setup-join preimage inversion",
        "authority": {
            "readsReferencePixels": False,
            "usesM1CoefficientExports": True,
            "recoversExactAccumulatorLaw": False,
            "authorizesProductionMutation": False,
        },
        "inputs": {
            "analyzer": _identity(Path(__file__).resolve()),
            "setupTileSweepResult": _identity(sweep.OUTPUT),
            "p25Selector": _identity(accumulator.setup.P25_PATH),
        },
        "census": {
            "coefficientWordCount": 30_816,
            "baselineExactCount": 30_792,
            "residualCount": len(records),
            "searchRadiusP28Units": SEARCH_RADIUS,
            "uniquePreimageCount": sum(
                record["compatibleJoinOffset"]["count"] == 1  # type: ignore[index]
                for record in records
            ),
            "uniqueOffsetHistogram": {
                str(offset): count
                for offset, count in sorted(unique_offset_counts.items())
            },
            "outputDeltaHistogram": {
                str(delta): count
                for delta, count in sorted(Counter(output_deltas).items())
            },
        },
        "records": records,
        "conclusion": (
            "The observed constants constrain the hidden p28 join to the reported "
            "index intervals. These preimages localize the remaining signed "
            "accumulator behavior without changing any established front-end, "
            "reciprocal, or final materialization stage."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=OUTPUT)
    arguments = parser.parse_args()
    result = analyze()
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(result["census"], indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
