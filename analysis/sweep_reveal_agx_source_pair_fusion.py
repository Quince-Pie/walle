#!/usr/bin/env python3.14
"""Score fused source-product reducers against single-axis M1 p28 intervals."""

import argparse
import json
from collections import Counter, defaultdict
from fractions import Fraction
from pathlib import Path
from typing import Final

import numpy as np

import analyze_reveal_agx_setup_accumulator as accumulator
import analyze_reveal_agx_setup_tile_sweep as sweep
import analyze_reveal_agx_two_product_ruler as ruler
import analyze_reveal_agx_two_product_tomography as tomography
import sweep_reveal_agx_source_product_reduction as source


type JsonObject = dict[str, object]
type Product = tuple[int, int, int, int]
type Prepared = tuple[
    str,
    set[int],
    tuple[Product, Product],
    int,
    int,
    Fraction,
    tuple[int, int, int],
]

ROOT: Final = Path(__file__).resolve().parent.parent
CAPTURE_ROOT: Final = (
    ROOT / "build" / "analysis-agx-basis" / "macos-single-axis-multi-anchor-v1"
)
PLAN: Final = CAPTURE_ROOT / "reveal-agx-setup-accumulator-plan.json"
VERTICES: Final = CAPTURE_ROOT / "reveal-agx-setup-accumulator-vertices.bin"
RAW: Final = CAPTURE_ROOT / "capture" / "reveal-agx-setup-accumulator.raw"
OUTPUT: Final = (
    ROOT
    / "build"
    / "analysis-agx-basis"
    / "source-pair-fusion-sweep"
    / "result.json"
)
DRAW_COUNT: Final = 5_060
RECORD_VECTOR_COUNT: Final = 101


def _power(exponent: int) -> Fraction:
    return accumulator._power_of_two(exponent)  # noqa: SLF001


def _align(values: tuple[tuple[int, int], ...]) -> tuple[int, int]:
    exponent = min(value[1] for value in values)
    return sum(value << (term_exponent - exponent) for value, term_exponent in values), exponent


def _numerator(
    products: tuple[Product, Product],
    *,
    mode: str,
    precision: int,
    bias: int,
    carry_columns: int,
) -> tuple[int, int, int]:
    if mode == "independent":
        terms: list[Fraction] = []
        for sign, multiplicand, multiplier, exponent in products:
            index, product_exponent = accumulator.coefficient.column_product_stage(
                multiplicand,
                exponent,
                multiplier,
                0,
                output_bits=27,
                truncation_bits=16,
                bias_units=15,
                carry_mode="top-columns",
                propagated_column_count=0,
                sticky_carry_limit=1,
            )
            terms.append(sign * index * _power(product_exponent))
        return accumulator.setup._normalize_signed(  # noqa: SLF001
            sum(terms, start=Fraction()), precision_bits=precision, rounding="nearest-even"
        )

    streams: list[tuple[int, int]] = []
    for sign, multiplicand, multiplier, exponent in products:
        exact = multiplicand * multiplier
        partial = accumulator.tile.partial_product_sum(multiplicand, multiplier, 16)
        carry = accumulator.coefficient.propagated_discarded_carry(
            multiplicand, multiplier, 16, carry_columns
        )
        match mode:
            case "exact":
                adjusted = exact
            case "partial-shared-bias":
                adjusted = partial + (carry << 16)
            case "partial-per-term-bias":
                adjusted = partial + ((carry + bias) << 16)
            case _:
                raise ValueError(f"unknown source pair mode: {mode}")
        streams.append((sign * adjusted, exponent))
    joined, exponent = _align(tuple(streams))
    if mode == "partial-shared-bias" and joined:
        joined += (bias << 16) * (1 if joined > 0 else -1)
    return accumulator.setup._normalize_signed(  # noqa: SLF001
        joined * _power(exponent), precision_bits=precision, rounding="nearest-even"
    )


def _candidate_offset(
    row: Prepared,
    *,
    mode: str,
    precision: int,
    bias: int,
    carry_columns: int,
) -> int | None:
    _split, _interval, products, distance_index, distance_exponent, displacement, baseline = row
    sign, numerator, numerator_exponent = _numerator(
        products,
        mode=mode,
        precision=precision,
        bias=bias,
        carry_columns=carry_columns,
    )
    middle_index, middle_exponent = accumulator.coefficient.column_product_stage(
        numerator,
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
    candidate = sign * (-1 if displacement < 0 else 1) * middle_index * _power(middle_exponent)
    baseline_value = baseline[0] * (baseline[1] * 2) * _power(baseline[2] - 1)
    unit = baseline[0] * _power(baseline[2] - 1)
    offset = (candidate - baseline_value) / unit
    return int(offset) if offset.denominator == 1 else None


def _prepare() -> list[Prepared]:
    plan = json.loads(PLAN.read_text(encoding="utf-8"))
    words = np.fromfile(RAW, dtype="<u4").reshape(DRAW_COUNT, RECORD_VECTOR_COUNT, 4)
    vertex_words = np.fromfile(VERTICES, dtype="<u4").reshape(DRAW_COUNT, 3, 8)
    experiments = sweep._require_list(plan.get("experiments"), "experiments")  # noqa: SLF001
    draws = sweep._require_list(plan.get("draws"), "draws")  # noqa: SLF001
    bitmap = accumulator.setup.P25_PATH.read_bytes()
    groups: dict[tuple[str, int], list[tuple[int, JsonObject]]] = defaultdict(list)
    for index, experiment_value in enumerate(experiments):
        experiment = sweep._require_dict(experiment_value, "experiment")  # noqa: SLF001
        groups[(str(experiment["variant"]), int(experiment["inputOrdinal"]))].append((index, experiment))

    prepared: list[Prepared] = []
    for records in groups.values():
        compatible: list[set[int]] = []
        for record_index, experiment in records:
            vertices = sweep._vertices(vertex_words, record_index)  # noqa: SLF001
            determinant = accumulator.setup._determinant(  # noqa: SLF001
                accumulator.setup._fixed_positions(vertices)  # noqa: SLF001
            )
            term = experiment["middleTerm"]
            if not isinstance(term, dict):
                raise ValueError("middle term differs")
            selector, selector_exponent = accumulator.setup._p25_selector(  # noqa: SLF001
                determinant, bitmap
            )
            for anchor_value, triple in zip(
                sweep._require_list(experiment.get("anchors"), "anchors"),  # noqa: SLF001
                accumulator._triples(words[record_index]),  # noqa: SLF001
                strict=True,
            ):
                anchor = sweep._require_dict(anchor_value, "anchor")  # noqa: SLF001
                compatible.append(
                    tomography._compatible_offsets(  # noqa: SLF001
                        int(triple[2]),
                        int(str(anchor["anchorBits"]), 16),
                        int(term["sign"]),
                        int(term["index"]) * 2,
                        int(term["exponent"]) - 1,
                        selector,
                        selector_exponent,
                    )
                )
        interval = set.intersection(*compatible)
        record_index, experiment = records[0]
        draw = sweep._require_dict(draws[record_index], "draw")  # noqa: SLF001
        vertices = sweep._vertices(vertex_words, record_index)  # noqa: SLF001
        positions = accumulator.setup._fixed_positions(vertices)  # noqa: SLF001
        anchor_index = accumulator.top_left._top_left(positions)  # noqa: SLF001
        active_axis = 1 - int(experiment["zeroAxis"])
        products = source._raw_source_products(vertices, active_axis)  # noqa: SLF001
        tile = (int(draw["tileX"]), int(draw["tileY"]))
        displacement = Fraction(
            tile[active_axis] * 32 * 256 - positions[anchor_index][active_axis], 256
        )
        distance_index, distance_exponent = accumulator._positive_float_components(  # noqa: SLF001
            accumulator.setup._float_bits(float(abs(displacement)))  # noqa: SLF001
        )
        term = experiment["middleTerm"]
        assert isinstance(term, dict)
        prepared.append(
            (
                str(experiment["split"]),
                interval,
                products,
                distance_index,
                distance_exponent,
                displacement,
                (int(term["sign"]), int(term["index"]), int(term["exponent"])),
            )
        )
    return prepared


def analyze() -> JsonObject:
    prepared = _prepare()
    candidates: list[JsonObject] = []
    for mode in ("independent", "exact", "partial-shared-bias", "partial-per-term-bias"):
        for precision in range(27, 32):
            for carry_columns in range(5):
                for bias in range(32):
                    if mode in {"independent", "exact"} and (carry_columns != 0 or bias != 0):
                        continue
                    accepted = Counter[str]()
                    integral = 0
                    for row in prepared:
                        offset = _candidate_offset(
                            row,
                            mode=mode,
                            precision=precision,
                            bias=bias,
                            carry_columns=carry_columns,
                        )
                        integral += offset is not None
                        if offset in row[1]:
                            accepted[row[0]] += 1
                    candidates.append(
                        {
                            "mode": mode,
                            "precisionBits": precision,
                            "carryColumns": carry_columns,
                            "biasUnits": bias,
                            "discoveryAccepted": accepted["discovery"],
                            "holdoutAccepted": accepted["holdout"],
                            "totalAccepted": sum(accepted.values()),
                            "integralOffsetCount": integral,
                        }
                    )
    candidates.sort(
        key=lambda item: (int(item["discoveryAccepted"]), int(item["holdoutAccepted"])),
        reverse=True,
    )
    return {
        "schema": "walle-reveal-agx-source-pair-fusion-sweep-v1",
        "authority": {
            "readsReferencePixels": False,
            "usesM1CoefficientExports": True,
            "authorizesProductionMutation": False,
        },
        "census": {
            "inputCount": len(prepared),
            "discoveryInputCount": sum(row[0] == "discovery" for row in prepared),
            "holdoutInputCount": sum(row[0] == "holdout" for row in prepared),
            "candidateCount": len(candidates),
        },
        "topCandidates": candidates[:128],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    arguments = parser.parse_args()
    result = analyze()
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
