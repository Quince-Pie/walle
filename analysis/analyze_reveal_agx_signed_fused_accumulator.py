#!/usr/bin/env python3.14
"""Discriminate fused signed AGX setup-product accumulation.

The established setup model is exact whenever the tile constant contains zero
or one displacement product.  Its only wide-tile failures contain two products
with opposite signs.  This output-blind analyzer therefore keeps every measured
front-end and reciprocal stage fixed and changes only the point at which the two
signed partial-product streams are joined.
"""

from __future__ import annotations

import argparse
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
type Product = tuple[int, int, int, int]


def _product_streams(
    vertices: tuple[tuple[float, ...], ...],
    component: int,
    tile_position: tuple[int, int],
) -> tuple[Fraction, int, tuple[Product, ...]]:
    positions = accumulator.setup._fixed_positions(vertices)  # noqa: SLF001
    determinant = accumulator.setup._determinant(positions)  # noqa: SLF001
    anchor_index = accumulator.top_left._top_left(positions)  # noqa: SLF001
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
    products: list[Product] = []
    for axis in range(2):
        numerator = sum(
            (
                accumulator.setup._first_product(  # noqa: SLF001
                    accumulator.setup._float32(  # noqa: SLF001
                        values[index] - values[anchor_index]
                    ),
                    edges[axis][index] / 256.0,
                    bias_units=15,
                )
                for index in range(3)
                if index != anchor_index
            ),
            start=Fraction(),
        )
        sign, numerator_index, numerator_exponent = accumulator.setup._normalize_signed(  # noqa: SLF001
            numerator, precision_bits=27, rounding="nearest-even"
        )
        displacement = Fraction(
            tile_position[axis] * 32 * 256 - positions[anchor_index][axis], 256
        )
        if sign == 0 or displacement == 0:
            continue
        distance_bits = accumulator.setup._float_bits(float(abs(displacement)))  # noqa: SLF001
        distance_index, distance_exponent = accumulator._positive_float_components(
            distance_bits
        )  # noqa: SLF001
        product_sign = sign * (-1 if displacement < 0 else 1)
        products.append(
            (
                product_sign,
                numerator_index,
                distance_index,
                numerator_exponent + distance_exponent,
            )
        )
    anchor = accumulator.export._fraction(  # noqa: SLF001
        accumulator.setup._float_bits(values[anchor_index])  # noqa: SLF001
    )
    return anchor, determinant, tuple(products)


def _align(values: tuple[tuple[int, int], ...]) -> tuple[int, int]:
    exponent = min(value[1] for value in values)
    return sum(
        value << (term_exponent - exponent) for value, term_exponent in values
    ), exponent


def _joint_middle(products: tuple[Product, ...], mode: str) -> Fraction:
    if not products:
        return Fraction()
    streams: list[tuple[int, int]] = []
    exact_streams: list[tuple[int, int]] = []
    for sign, multiplicand, multiplier, exponent in products:
        partial = accumulator.tile.partial_product_sum(multiplicand, multiplier, 19)
        exact = multiplicand * multiplier
        streams.append((sign * partial, exponent))
        exact_streams.append((sign * exact, exponent))
    partial_sum, exponent = _align(tuple(streams))
    exact_sum, exact_exponent = _align(tuple(exact_streams))
    if exponent != exact_exponent:
        raise ValueError("aligned product exponents differ")
    if partial_sum == 0:
        return Fraction()

    if mode == "opposite-sign-retained-guard-p28":
        established_terms: list[Fraction] = []
        for sign, multiplicand, multiplier, product_exponent in products:
            index, output_exponent = accumulator.coefficient.column_product_stage(
                multiplicand,
                product_exponent,
                multiplier,
                0,
                output_bits=27,
                truncation_bits=19,
                bias_units=10,
                carry_mode="top-columns",
                propagated_column_count=1,
                sticky_carry_limit=1,
            )
            established_terms.append(
                sign * index * accumulator._power_of_two(output_exponent)  # noqa: SLF001
            )
        if len(products) != 2 or products[0][0] == products[1][0]:
            return accumulator._quantize_signed(  # noqa: SLF001
                sum(established_terms, start=Fraction()),
                28,
                rounding="nearest-even",
            )
        retained = tuple(
            (
                sign
                * (
                    accumulator.tile.partial_product_sum(multiplicand, multiplier, 19)
                    + (
                        accumulator.coefficient.propagated_discarded_carry(
                            multiplicand,
                            multiplier,
                            19,
                            1,
                        )
                        + 10
                    )
                    * (1 << 19)
                ),
                product_exponent,
            )
            for sign, multiplicand, multiplier, product_exponent in products
        )
        joined, joined_exponent = _align(retained)
        return accumulator._quantize_signed(  # noqa: SLF001
            joined * accumulator._power_of_two(joined_exponent),  # noqa: SLF001
            28,
            rounding="nearest-even",
        )

    match mode:
        case "exact-p28":
            value = exact_sum * accumulator._power_of_two(exponent)  # noqa: SLF001
            return accumulator._quantize_signed(  # noqa: SLF001
                value, 28, rounding="nearest-even"
            )
        case "partial-p28":
            value = partial_sum * accumulator._power_of_two(exponent)  # noqa: SLF001
            return accumulator._quantize_signed(  # noqa: SLF001
                value, 28, rounding="nearest-even"
            )
        case "partial-single-bias-p28":
            # The measured one-product stage adds ten units at discarded
            # column 19.  Apply that bias once after the signed streams join.
            bias = (10 << 19) * (1 if partial_sum > 0 else -1)
            value = (partial_sum + bias) * accumulator._power_of_two(exponent)  # noqa: SLF001
            return accumulator._quantize_signed(  # noqa: SLF001
                value, 28, rounding="nearest-even"
            )
        case "partial-per-term-bias-p28":
            biased = tuple(
                (
                    sign
                    * (
                        accumulator.tile.partial_product_sum(
                            multiplicand, multiplier, 19
                        )
                        + (10 << 19)
                    ),
                    product_exponent,
                )
                for sign, multiplicand, multiplier, product_exponent in products
            )
            joined, joined_exponent = _align(biased)
            value = joined * accumulator._power_of_two(joined_exponent)  # noqa: SLF001
            return accumulator._quantize_signed(  # noqa: SLF001
                value, 28, rounding="nearest-even"
            )
        case "partial-carry-per-term-bias-p28":
            retained = tuple(
                (
                    sign
                    * (
                        accumulator.tile.partial_product_sum(
                            multiplicand, multiplier, 19
                        )
                        + (
                            accumulator.coefficient.propagated_discarded_carry(
                                multiplicand,
                                multiplier,
                                19,
                                1,
                            )
                            + 10
                        )
                        * (1 << 19)
                    ),
                    product_exponent,
                )
                for sign, multiplicand, multiplier, product_exponent in products
            )
            joined, joined_exponent = _align(retained)
            value = joined * accumulator._power_of_two(joined_exponent)  # noqa: SLF001
            return accumulator._quantize_signed(  # noqa: SLF001
                value, 28, rounding="nearest-even"
            )
        case "partial-carry-single-bias-p28":
            retained = tuple(
                (
                    sign
                    * (
                        accumulator.tile.partial_product_sum(
                            multiplicand, multiplier, 19
                        )
                        + accumulator.coefficient.propagated_discarded_carry(
                            multiplicand,
                            multiplier,
                            19,
                            1,
                        )
                        * (1 << 19)
                    ),
                    product_exponent,
                )
                for sign, multiplicand, multiplier, product_exponent in products
            )
            joined, joined_exponent = _align(retained)
            if joined:
                joined += (10 << 19) * (1 if joined > 0 else -1)
            value = joined * accumulator._power_of_two(joined_exponent)  # noqa: SLF001
            return accumulator._quantize_signed(  # noqa: SLF001
                value, 28, rounding="nearest-even"
            )
        case _:
            raise ValueError(f"unknown fused mode: {mode}")


def _constant_bits(
    vertices: tuple[tuple[float, ...], ...],
    component: int,
    tile_position: tuple[int, int],
    bitmap: bytes,
    mode: str,
) -> int:
    anchor, determinant, products = _product_streams(vertices, component, tile_position)
    joined = _joint_middle(products, mode)
    if joined == 0:
        return accumulator.composite.quantize_composite_constant_bits(anchor)
    sign, index, exponent = accumulator.setup._normalize_signed(  # noqa: SLF001
        joined, precision_bits=28, rounding="nearest-even"
    )
    selector, selector_exponent = accumulator.setup._p25_selector(  # noqa: SLF001
        determinant, bitmap
    )
    output_index, output_exponent = accumulator.tile.product_stage(
        index,
        exponent,
        selector,
        selector_exponent,
        output_bits=27,
        truncation_bits=20,
        bias_units=20,
    )
    coefficient = (
        sign * output_index * accumulator._power_of_two(output_exponent)  # noqa: SLF001
    )
    return accumulator.composite.quantize_composite_constant_bits(anchor + coefficient)


def _summary(deltas: list[int]) -> JsonObject:
    histogram = Counter(deltas)
    return {
        "count": len(deltas),
        "exact": histogram[0],
        "withinOne": sum(
            count for delta, count in histogram.items() if abs(delta) <= 1
        ),
        "minimum": min(deltas),
        "maximum": max(deltas),
        "histogram": {
            str(delta): count
            for delta, count in sorted(histogram.items())
            if abs(delta) <= 32
        },
    }


def analyze() -> JsonObject:
    plans = sweep._load_plans()  # noqa: SLF001
    captures = sweep._load_captures(plans)  # noqa: SLF001
    bitmap = accumulator.setup.P25_PATH.read_bytes()
    modes = (
        "exact-p28",
        "partial-p28",
        "partial-single-bias-p28",
        "partial-per-term-bias-p28",
        "partial-carry-per-term-bias-p28",
        "partial-carry-single-bias-p28",
        "opposite-sign-retained-guard-p28",
    )
    deltas: dict[str, list[int]] = {mode: [] for mode in modes}
    by_term_count: dict[str, dict[int, list[int]]] = {
        mode: {0: [], 1: [], 2: []} for mode in modes
    }
    for batch, (plan, words) in enumerate(zip(plans, captures, strict=True)):
        vertex_path = (
            sweep.PLAN_ROOT
            / f"batch-{batch}"
            / "reveal-agx-setup-accumulator-vertices.bin"
        )
        vertex_words = np.fromfile(vertex_path, dtype="<u4").reshape(-1, 3, 8)
        for draw_value in sweep._require_list(plan.get("draws"), "draws"):  # noqa: SLF001
            draw = sweep._require_dict(draw_value, "draw")  # noqa: SLF001
            record = sweep._require_int(draw.get("recordIndex"), "record")  # noqa: SLF001
            vertices = sweep._vertices(vertex_words, record)  # noqa: SLF001
            tile = (
                sweep._require_int(draw.get("tileX"), "tile x"),  # noqa: SLF001
                sweep._require_int(draw.get("tileY"), "tile y"),  # noqa: SLF001
            )
            triples = accumulator._triples(words[record])  # noqa: SLF001
            for component, triple in enumerate(triples):
                _anchor, _determinant, products = _product_streams(
                    vertices, component, tile
                )
                term_count = len(products)
                for mode in modes:
                    predicted = _constant_bits(vertices, component, tile, bitmap, mode)
                    delta = accumulator.export._float_ulp_delta(  # noqa: SLF001
                        triple[2], predicted
                    )
                    deltas[mode].append(delta)
                    by_term_count[mode][term_count].append(delta)
    return {
        "schema": "walle-reveal-agx-signed-fused-accumulator-diagnostic-v1",
        "authority": {
            "readsReferencePixels": False,
            "usesM1CoefficientExport": True,
            "productionAuthorized": False,
        },
        "results": {
            mode: {
                "overall": _summary(values),
                "byTermCount": {
                    str(count): _summary(by_term_count[mode][count])
                    for count in range(3)
                },
            }
            for mode, values in deltas.items()
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT
        / "build"
        / "analysis-agx-basis"
        / "signed-fused-accumulator"
        / "result.json",
    )
    arguments = parser.parse_args()
    result = analyze()
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(result["results"], indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
