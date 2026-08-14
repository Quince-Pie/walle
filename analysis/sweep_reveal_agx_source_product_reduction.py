#!/usr/bin/env python3.14
"""Score source-product reduction policies against the dense public-child ruler."""

import argparse
import json
import multiprocessing
import os
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from fractions import Fraction
from pathlib import Path
from typing import Final

import analyze_reveal_agx_public_child_mantissa_ruler as public_child
import analyze_reveal_agx_setup_accumulator as accumulator
import analyze_reveal_agx_setup_tile_sweep as sweep
import analyze_reveal_agx_two_product_ruler as ruler


type JsonObject = dict[str, object]
type FloatProduct = tuple[int, int, int, int]
type PreparedAxis = tuple[tuple[FloatProduct, FloatProduct], Fraction]
type PreparedRecord = tuple[
    str,
    int | None,
    tuple[PreparedAxis, PreparedAxis],
    tuple[int, int, int],
]

ROOT: Final = Path(__file__).resolve().parent.parent
INPUT: Final = (
    ROOT
    / "build"
    / "analysis-agx-basis"
    / "public-child-mantissa-ruler-analysis"
    / "result-with-slope-inversion.json"
)
OUTPUT: Final = (
    ROOT
    / "build"
    / "analysis-agx-basis"
    / "source-product-reduction-sweep"
    / "result.json"
)

_PREPARED: list[PreparedRecord] = []


def _power(exponent: int) -> Fraction:
    return accumulator._power_of_two(exponent)  # noqa: SLF001


def _raw_source_products(
    vertices: tuple[tuple[float, ...], ...], axis: int
) -> tuple[FloatProduct, FloatProduct]:
    positions = accumulator.setup._fixed_positions(vertices)  # noqa: SLF001
    anchor = accumulator.top_left._top_left(positions)  # noqa: SLF001
    values = tuple(
        accumulator.setup._float32(vertex[2])  # noqa: SLF001
        for vertex in vertices
    )
    edges = (
        (
            positions[1][1] - positions[2][1],
            positions[2][1] - positions[0][1],
            positions[0][1] - positions[1][1],
        )
        if axis == 0
        else (
            positions[2][0] - positions[1][0],
            positions[0][0] - positions[2][0],
            positions[1][0] - positions[0][0],
        )
    )
    products: list[FloatProduct] = []
    for index in range(3):
        if index == anchor:
            continue
        left = accumulator.setup._float32(values[index] - values[anchor])  # noqa: SLF001
        right = accumulator.setup._float32(edges[index] / 256.0)  # noqa: SLF001
        sign = -1 if (left < 0.0) != (right < 0.0) else 1
        left_index, left_exponent = accumulator._positive_float_components(  # noqa: SLF001
            accumulator.setup._float_bits(abs(left))  # noqa: SLF001
        )
        right_index, right_exponent = accumulator._positive_float_components(  # noqa: SLF001
            accumulator.setup._float_bits(abs(right))  # noqa: SLF001
        )
        products.append((sign, left_index, right_index, left_exponent + right_exponent))
    if len(products) != 2:
        raise ValueError("slope does not contain two source products")
    return products[0], products[1]


def _candidate_middle_terms(
    axes: tuple[PreparedAxis, PreparedAxis],
    *,
    output_bits: int,
    carry_columns: int,
    bias_units: int,
) -> tuple[tuple[int, int, int], ...]:
    terms: list[tuple[int, int, int]] = []
    for products, displacement in axes:
        source_terms: list[Fraction] = []
        for sign, multiplicand, multiplier, exponent in products:
            index, product_exponent = accumulator.coefficient.column_product_stage(
                multiplicand,
                exponent,
                multiplier,
                0,
                output_bits=output_bits,
                truncation_bits=16,
                bias_units=bias_units,
                carry_mode="top-columns",
                propagated_column_count=carry_columns,
                sticky_carry_limit=1,
            )
            source_terms.append(sign * index * _power(product_exponent))
        numerator = sum(source_terms, start=Fraction())
        sign, numerator_index, numerator_exponent = accumulator.setup._normalize_signed(  # noqa: SLF001
            numerator, precision_bits=27, rounding="nearest-even"
        )
        if sign == 0 or displacement == 0:
            continue
        distance_index, distance_exponent = accumulator._positive_float_components(  # noqa: SLF001
            accumulator.setup._float_bits(float(abs(displacement)))  # noqa: SLF001
        )
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
    return tuple(terms)


def _candidate_offset(
    axes: tuple[PreparedAxis, PreparedAxis],
    baseline: tuple[int, int, int],
    *,
    output_bits: int,
    carry_columns: int,
    bias_units: int,
) -> int | None:
    terms = _candidate_middle_terms(
        axes,
        output_bits=output_bits,
        carry_columns=carry_columns,
        bias_units=bias_units,
    )
    joined = sum(
        (sign * index * _power(exponent) for sign, index, exponent in terms),
        start=Fraction(),
    )
    if joined == 0:
        return None
    sign, index, exponent = accumulator.setup._normalize_signed(  # noqa: SLF001
        joined, precision_bits=28, rounding="nearest-even"
    )
    baseline_sign, baseline_index, baseline_exponent = baseline
    if sign != baseline_sign:
        return None
    difference = sign * index * _power(
        exponent
    ) - baseline_sign * baseline_index * _power(baseline_exponent)
    offset = difference / (baseline_sign * _power(baseline_exponent))
    return int(offset) if offset.denominator == 1 else None


def _score(candidate: tuple[int, int, int]) -> JsonObject:
    output_bits, carry_columns, bias_units = candidate
    exact = Counter[str]()
    offsets = Counter[int | None]()
    for split, target, axes, baseline in _PREPARED:
        offset = _candidate_offset(
            axes,
            baseline,
            output_bits=output_bits,
            carry_columns=carry_columns,
            bias_units=bias_units,
        )
        offsets[offset] += 1
        if offset == target:
            exact[split] += 1
    return {
        "outputBits": output_bits,
        "carryColumns": carry_columns,
        "biasUnits": bias_units,
        "discoveryExact": exact["discovery"],
        "holdoutExact": exact["holdout"],
        "totalExact": sum(exact.values()),
        "integralOffsetCount": len(_PREPARED) - offsets[None],
    }


def analyze(*, workers: int) -> JsonObject:
    public_child.configure_ruler()
    plan, _words, vertex_words = ruler._load()  # noqa: SLF001
    source = json.loads(INPUT.read_text(encoding="utf-8"))
    records = sweep._require_list(source.get("records"), "records")  # noqa: SLF001
    draws = sweep._require_list(plan.get("draws"), "draws")  # noqa: SLF001
    if len(records) != len(draws):
        raise ValueError("dense ruler record census differs")

    prepared: list[PreparedRecord] = []
    for record_value, draw_value in zip(records, draws, strict=True):
        record = sweep._require_dict(record_value, "record")  # noqa: SLF001
        draw = sweep._require_dict(draw_value, "draw")  # noqa: SLF001
        record_index = sweep._require_int(draw.get("recordIndex"), "record")  # noqa: SLF001
        vertices = sweep._vertices(vertex_words, record_index)  # noqa: SLF001
        positions = accumulator.setup._fixed_positions(vertices)  # noqa: SLF001
        anchor = accumulator.top_left._top_left(positions)  # noqa: SLF001
        tile = (
            sweep._require_int(draw.get("tileX"), "tile x"),  # noqa: SLF001
            sweep._require_int(draw.get("tileY"), "tile y"),  # noqa: SLF001
        )
        axes = tuple(
            (
                _raw_source_products(vertices, axis),
                Fraction(tile[axis] * 32 * 256 - positions[anchor][axis], 256),
            )
            for axis in range(2)
        )
        terms_value = sweep._require_list(record.get("middleTerms"), "terms")  # noqa: SLF001
        terms = tuple(
            (
                sweep._require_int(term.get("sign"), "term sign"),  # type: ignore[union-attr]  # noqa: SLF001
                sweep._require_int(term.get("index"), "term index"),  # type: ignore[union-attr]  # noqa: SLF001
                sweep._require_int(term.get("exponent"), "term exponent"),  # type: ignore[union-attr]  # noqa: SLF001
            )
            for term in terms_value
            if isinstance(term, dict)
        )
        correction_value = sweep._require_list(  # noqa: SLF001
            record.get("variableTermCorrectionCandidates"), "correction"
        )
        if len(terms) != 2 or len(correction_value) != 1:
            raise ValueError("dense ruler target is not unique")
        baseline = ruler.preimage._joined_index(terms)  # noqa: SLF001
        correction = int(correction_value[0])
        target = ruler._offset_from_terms(  # noqa: SLF001
            (terms[0], (terms[1][0], terms[1][1] + correction, terms[1][2])),
            baseline,
        )
        split = record.get("split")
        if split not in {"discovery", "holdout"}:
            raise ValueError("dense ruler split differs")
        prepared.append((split, target, axes, baseline))  # type: ignore[arg-type]

    global _PREPARED
    _PREPARED = prepared
    candidates = [
        (output_bits, carry_columns, bias_units)
        for output_bits in range(27, 31)
        for carry_columns in range(5)
        for bias_units in range(32)
    ]
    context = multiprocessing.get_context("fork")
    with ProcessPoolExecutor(max_workers=workers, mp_context=context) as executor:
        scored = list(executor.map(_score, candidates))
    scored.sort(
        key=lambda item: (int(item["discoveryExact"]), int(item["holdoutExact"])),
        reverse=True,
    )
    return {
        "schema": "walle-reveal-agx-source-product-reduction-sweep-v1",
        "authority": {
            "readsReferencePixels": False,
            "usesM1CoefficientExports": True,
            "authorizesProductionMutation": False,
        },
        "census": {
            "recordCount": len(prepared),
            "discoveryRecordCount": sum(row[0] == "discovery" for row in prepared),
            "holdoutRecordCount": sum(row[0] == "holdout" for row in prepared),
            "candidateCount": len(scored),
            "workerCount": workers,
        },
        "topCandidates": scored[:64],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument(
        "--workers", type=int, default=min(32, os.process_cpu_count() or 1)
    )
    arguments = parser.parse_args()
    if arguments.workers < 1:
        parser.error("--workers must be positive")
    result = analyze(workers=arguments.workers)
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
