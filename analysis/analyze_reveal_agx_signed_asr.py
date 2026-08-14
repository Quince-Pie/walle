#!/usr/bin/env python3.14
"""Test signed arithmetic-shift AGX tile-product candidates.

The authenticated wide-tile capture leaves only 24 errors in 30,816 exported
constant words.  Every error has two nonzero displacement products of opposite
sign.  This analyzer keeps the measured numerator, P25 reciprocal, and final
composite stages fixed, and replaces only a negative middle product's
sign-magnitude truncation with two's-complement partial products followed by
arithmetic right shifts.  It reads coefficient exports, never rendered output.
"""

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
import analyze_reveal_agx_signed_fused_accumulator as fused  # noqa: E402


type JsonObject = dict[str, object]
type Product = tuple[int, int, int, int]

OUTPUT: Final = (
    ROOT / "build" / "analysis-agx-basis" / "signed-asr-analysis" / "result.json"
)


def _signed_partial_product_sum(
    multiplicand: int, multiplier: int, truncation_bits: int
) -> int:
    """Sum sign-extended partial products after arithmetic truncation."""

    return sum(
        ((multiplicand << bit) >> truncation_bits) << truncation_bits
        for bit in range(multiplier.bit_length())
        if multiplier & (1 << bit)
    )


def _middle_term(
    product: Product,
    *,
    negative_offset_units: int | None,
    include_positive_carry: bool,
) -> Fraction:
    sign, multiplicand, multiplier, exponent = product
    if sign > 0 or negative_offset_units is None:
        index, output_exponent = accumulator.coefficient.column_product_stage(
            multiplicand,
            exponent,
            multiplier,
            0,
            output_bits=27,
            truncation_bits=19,
            bias_units=10,
            carry_mode="top-columns",
            propagated_column_count=1,
            sticky_carry_limit=1,
        )
        return sign * index * accumulator._power_of_two(output_exponent)  # noqa: SLF001

    signed_product = -multiplicand * multiplier
    product_shift = abs(signed_product).bit_length() - 27
    if product_shift < 0:
        raise ValueError("signed product does not fill p27")
    partial = _signed_partial_product_sum(-multiplicand, multiplier, 19)
    carry = (
        accumulator.coefficient.propagated_discarded_carry(
            multiplicand, multiplier, 19, 1
        )
        if include_positive_carry
        else 0
    )
    adjusted = partial + ((negative_offset_units + carry) << 19)
    index = adjusted >> product_shift
    return index * accumulator._power_of_two(exponent + product_shift)  # noqa: SLF001


def _constant_bits(
    anchor: Fraction,
    anchor_bits: int,
    determinant: int,
    products: tuple[Product, ...],
    bitmap: bytes,
    *,
    negative_offset_units: int | None,
    include_positive_carry: bool,
) -> int:
    joined = sum(
        (
            _middle_term(
                product,
                negative_offset_units=negative_offset_units,
                include_positive_carry=include_positive_carry,
            )
            for product in products
        ),
        start=Fraction(),
    )
    if joined == 0:
        return anchor_bits
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
        sign
        * output_index
        * accumulator._power_of_two(  # noqa: SLF001
            output_exponent
        )
    )
    return accumulator.composite.quantize_composite_constant_bits(anchor + coefficient)


def _summary(deltas: list[int]) -> JsonObject:
    histogram = Counter(deltas)
    return {
        "count": len(deltas),
        "exactCount": histogram[0],
        "withinOneUlpCount": sum(
            count for delta, count in histogram.items() if abs(delta) <= 1
        ),
        "minimumUlpDelta": min(deltas),
        "maximumUlpDelta": max(deltas),
        "smallDeltaHistogram": {
            str(delta): count
            for delta, count in sorted(histogram.items())
            if abs(delta) <= 32
        },
    }


def analyze() -> JsonObject:
    plans = sweep._load_plans()  # noqa: SLF001
    captures = sweep._load_captures(plans)  # noqa: SLF001
    bitmap = accumulator.setup.P25_PATH.read_bytes()
    policies: tuple[tuple[str, int | None, bool], ...] = (
        ("established-sign-magnitude", None, False),
        *tuple(
            (f"signed-asr-offset-{offset:+d}", offset, False)
            for offset in range(-16, 17)
        ),
        *tuple(
            (f"signed-asr-positive-carry-offset-{offset:+d}", offset, True)
            for offset in range(-16, 17)
        ),
    )
    deltas: dict[str, list[int]] = {name: [] for name, _offset, _carry in policies}
    by_batch: dict[str, dict[int, list[int]]] = {
        name: {batch: [] for batch in range(len(plans))}
        for name, _offset, _carry in policies
    }
    by_term_count: dict[str, dict[int, list[int]]] = {
        name: {0: [], 1: [], 2: []} for name, _offset, _carry in policies
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
                anchor, determinant, products = fused._product_streams(  # noqa: SLF001
                    vertices, component, tile
                )
                positions = accumulator.setup._fixed_positions(vertices)  # noqa: SLF001
                anchor_index = accumulator.top_left._top_left(positions)  # noqa: SLF001
                anchor_bits = accumulator.setup._float_bits(  # noqa: SLF001
                    accumulator.setup._float32(  # noqa: SLF001
                        vertices[anchor_index][2 + component]
                    )
                )
                for name, offset, carry in policies:
                    predicted = _constant_bits(
                        anchor,
                        anchor_bits,
                        determinant,
                        products,
                        bitmap,
                        negative_offset_units=offset,
                        include_positive_carry=carry,
                    )
                    delta = accumulator.export._float_ulp_delta(  # noqa: SLF001
                        triple[2], predicted
                    )
                    deltas[name].append(delta)
                    by_batch[name][batch].append(delta)
                    by_term_count[name][len(products)].append(delta)

    candidates = []
    for name, _offset, _carry in policies:
        candidates.append(
            {
                "name": name,
                "overall": _summary(deltas[name]),
                "byBatch": {
                    str(batch): _summary(values)
                    for batch, values in by_batch[name].items()
                },
                "byTermCount": {
                    str(count): _summary(values)
                    for count, values in by_term_count[name].items()
                },
            }
        )
    candidates.sort(
        key=lambda candidate: (
            -int(candidate["overall"]["exactCount"]),  # type: ignore[index]
            -int(candidate["overall"]["withinOneUlpCount"]),  # type: ignore[index]
            str(candidate["name"]),
        )
    )
    baseline = next(
        candidate
        for candidate in candidates
        if candidate["name"] == "established-sign-magnitude"
    )
    if baseline["overall"] != {
        "count": 30_816,
        "exactCount": 30_792,
        "withinOneUlpCount": 30_811,
        "minimumUlpDelta": -32,
        "maximumUlpDelta": 32,
        "smallDeltaHistogram": {
            "-32": 1,
            "-8": 1,
            "-2": 1,
            "-1": 13,
            "0": 30_792,
            "1": 6,
            "16": 1,
            "32": 1,
        },
    }:
        raise ValueError(
            f"established coefficient baseline differs: {baseline['overall']}"
        )
    winner = candidates[0]
    return {
        "schema": "walle-reveal-agx-signed-asr-analysis-v1",
        "authority": {
            "readsReferencePixels": False,
            "usesM1CoefficientExport": True,
            "productionAuthorized": False,
        },
        "candidateCount": len(candidates),
        "rankedCandidates": candidates,
        "conclusion": (
            "Signed arithmetic-shift partial products improve the authenticated "
            "coefficient census."
            if int(winner["overall"]["exactCount"]) > 30_792  # type: ignore[index]
            else "Signed arithmetic-shift partial products do not improve the authenticated coefficient census."
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
    print(result["conclusion"])


if __name__ == "__main__":
    main()
