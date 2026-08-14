#!/usr/bin/env python3.14
"""Sweep extra product guard precision before the AGX signed pair reduction."""

from __future__ import annotations

import argparse
import json
import multiprocessing
import os
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from fractions import Fraction
from pathlib import Path
from typing import Final

import analyze_reveal_agx_signed_fused_accumulator as fused
import analyze_reveal_agx_two_product_amplification as amplification
import analyze_reveal_agx_setup_accumulator as accumulator
import analyze_reveal_agx_setup_tile_sweep as sweep


type JsonObject = dict[str, object]
type Product = tuple[int, int, int, int]
type PreparedRecord = tuple[
    str, tuple[int, ...], tuple[Product, ...], tuple[int, int, int]
]

ROOT: Final = Path(__file__).resolve().parent.parent
INPUT: Final = (
    ROOT
    / "build"
    / "analysis-agx-basis"
    / "two-product-amplification-analysis"
    / "result.json"
)
OUTPUT: Final = (
    ROOT / "build" / "analysis-agx-basis" / "pair-guard-reduction-sweep" / "result.json"
)

_PREPARED: list[PreparedRecord] = []


def _power(exponent: int) -> Fraction:
    return accumulator._power_of_two(exponent)  # noqa: SLF001


def _candidate_offset(
    products: tuple[Product, ...],
    *,
    output_bits: int,
    bias_units: int,
    carry_columns: int,
    baseline_sign: int,
    baseline_index: int,
    baseline_exponent: int,
) -> int | None:
    terms: list[Fraction] = []
    for sign, multiplicand, multiplier, exponent in products:
        index, product_exponent = accumulator.coefficient.column_product_stage(
            multiplicand,
            exponent,
            multiplier,
            0,
            output_bits=output_bits,
            truncation_bits=19,
            bias_units=bias_units,
            carry_mode="top-columns",
            propagated_column_count=carry_columns,
            sticky_carry_limit=1,
        )
        terms.append(sign * index * _power(product_exponent))
    joined = sum(terms, start=Fraction())
    if joined == 0:
        return None
    sign, index, exponent = accumulator.setup._normalize_signed(  # noqa: SLF001
        joined,
        precision_bits=28,
        rounding="nearest-even",
    )
    if sign != baseline_sign:
        return None
    difference = sign * index * _power(
        exponent
    ) - baseline_sign * baseline_index * _power(baseline_exponent)
    offset = difference / (baseline_sign * _power(baseline_exponent))
    return int(offset) if offset.denominator == 1 else None


def _score_candidate(candidate: tuple[int, int, int]) -> JsonObject:
    output_bits, carry_columns, bias_units = candidate
    accepted = Counter[str]()
    offsets = Counter[int | None]()
    for split, interval, products, baseline in _PREPARED:
        offset = _candidate_offset(
            products,
            output_bits=output_bits,
            bias_units=bias_units,
            carry_columns=carry_columns,
            baseline_sign=baseline[0],
            baseline_index=baseline[1],
            baseline_exponent=baseline[2],
        )
        offsets[offset] += 1
        if offset is not None and offset in interval:
            accepted[split] += 1
    return {
        "outputBits": output_bits,
        "carryColumns": carry_columns,
        "biasUnits": bias_units,
        "discoveryAccepted": accepted["discovery"],
        "holdoutAccepted": accepted["holdout"],
        "totalAccepted": sum(accepted.values()),
        "integralOffsetCount": len(_PREPARED) - offsets[None],
    }


def analyze(*, workers: int) -> JsonObject:
    source = json.loads(INPUT.read_text(encoding="utf-8"))
    records_value = source.get("records")
    if not isinstance(records_value, list):
        raise ValueError("amplification records are absent")
    plan, _words, vertex_words = amplification._load()  # noqa: SLF001
    experiments = sweep._require_list(plan.get("experiments"), "experiments")  # noqa: SLF001
    draws = sweep._require_list(plan.get("draws"), "draws")  # noqa: SLF001
    if len(records_value) != len(experiments) or len(records_value) != len(draws):
        raise ValueError("amplification plan and records differ")

    prepared: list[PreparedRecord] = []
    for record_value, experiment_value, draw_value in zip(
        records_value, experiments, draws, strict=True
    ):
        record = sweep._require_dict(record_value, "record")  # noqa: SLF001
        experiment = sweep._require_dict(experiment_value, "experiment")  # noqa: SLF001
        draw = sweep._require_dict(draw_value, "draw")  # noqa: SLF001
        if not record.get("slopesExact"):
            continue
        record_index = sweep._require_int(draw.get("recordIndex"), "record")  # noqa: SLF001
        vertices = sweep._vertices(vertex_words, record_index)  # noqa: SLF001
        tile = (
            sweep._require_int(draw.get("tileX"), "tile x"),  # noqa: SLF001
            sweep._require_int(draw.get("tileY"), "tile y"),  # noqa: SLF001
        )
        _anchor, _determinant, products = fused._product_streams(  # noqa: SLF001
            vertices, 0, tile
        )
        terms_value = record.get("middleTerms")
        interval_value = record.get("compatibleJoinOffsetIntersection")
        if not isinstance(terms_value, list) or not isinstance(interval_value, dict):
            raise ValueError("record terms or interval are absent")
        terms = tuple(
            (
                sweep._require_int(term.get("sign"), "term sign"),  # type: ignore[union-attr]  # noqa: SLF001
                sweep._require_int(term.get("index"), "term index"),  # type: ignore[union-attr]  # noqa: SLF001
                sweep._require_int(term.get("exponent"), "term exponent"),  # type: ignore[union-attr]  # noqa: SLF001
            )
            for term in terms_value
            if isinstance(term, dict)
        )
        baseline = fused._align(  # noqa: SLF001
            tuple((sign * index, exponent) for sign, index, exponent in terms)
        )
        joined = baseline[0] * _power(baseline[1])
        baseline_sign, baseline_index, baseline_exponent = (
            accumulator.setup._normalize_signed(  # noqa: SLF001
                joined,
                precision_bits=28,
                rounding="nearest-even",
            )
        )
        values = interval_value.get("values")
        if not isinstance(values, list):
            raise ValueError("record interval values are absent")
        split = experiment.get("split")
        if split not in {"discovery", "holdout"}:
            raise ValueError("record split differs")
        prepared.append(
            (
                split,
                tuple(int(value) for value in values),
                products,
                (baseline_sign, baseline_index, baseline_exponent),
            )
        )

    global _PREPARED
    _PREPARED = prepared
    candidate_parameters = [
        (output_bits, carry_columns, bias_units)
        for output_bits in range(27, 31)
        for carry_columns in range(0, 5)
        for bias_units in range(0, 32)
    ]
    context = multiprocessing.get_context("fork")
    with ProcessPoolExecutor(max_workers=workers, mp_context=context) as executor:
        candidates = list(executor.map(_score_candidate, candidate_parameters))
    candidates.sort(
        key=lambda candidate: (
            int(candidate["discoveryAccepted"]),
            int(candidate["holdoutAccepted"]),
        ),
        reverse=True,
    )
    best_by_output_bits = {
        str(output_bits): max(
            (
                candidate
                for candidate in candidates
                if candidate["outputBits"] == output_bits
            ),
            key=lambda candidate: (
                int(candidate["discoveryAccepted"]),
                int(candidate["holdoutAccepted"]),
            ),
        )
        for output_bits in range(27, 31)
    }
    return {
        "schema": "walle-reveal-agx-pair-guard-reduction-sweep-v1",
        "authority": {
            "readsReferencePixels": False,
            "usesM1CoefficientExports": True,
            "authorizesProductionMutation": False,
        },
        "census": {
            "cleanRecordCount": len(prepared),
            "discoveryRecordCount": sum(split == "discovery" for split, *_ in prepared),
            "holdoutRecordCount": sum(split == "holdout" for split, *_ in prepared),
            "candidateCount": len(candidates),
            "workerCount": workers,
        },
        "bestByOutputBits": best_by_output_bits,
        "topCandidates": candidates[:64],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
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
