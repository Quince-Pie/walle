#!/usr/bin/env python3.14
"""Score fused pair-reduction policies against the dense public-child M1 ruler."""

import argparse
import json
import multiprocessing
import os
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Final

import analyze_reveal_agx_public_child_mantissa_ruler as public_child
import analyze_reveal_agx_setup_tile_sweep as sweep
import analyze_reveal_agx_signed_fused_accumulator as fused
import analyze_reveal_agx_two_product_ruler as ruler
import sweep_reveal_agx_pair_guard_reduction as guard


type JsonObject = dict[str, object]
type Product = tuple[int, int, int, int]
type PreparedRecord = tuple[str, int | None, tuple[Product, ...], tuple[int, int, int]]

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
    / "public-child-pair-reduction-sweep"
    / "result.json"
)

_PREPARED: list[PreparedRecord] = []


def _score(candidate: tuple[int, int, int]) -> JsonObject:
    output_bits, carry_columns, bias_units = candidate
    accepted = Counter[str]()
    offsets = Counter[int | None]()
    for split, target, products, baseline in _PREPARED:
        offset = guard._candidate_offset(  # noqa: SLF001
            products,
            output_bits=output_bits,
            bias_units=bias_units,
            carry_columns=carry_columns,
            baseline_sign=baseline[0],
            baseline_index=baseline[1],
            baseline_exponent=baseline[2],
        )
        offsets[offset] += 1
        if offset == target:
            accepted[split] += 1
    return {
        "outputBits": output_bits,
        "carryColumns": carry_columns,
        "biasUnits": bias_units,
        "discoveryExact": accepted["discovery"],
        "holdoutExact": accepted["holdout"],
        "totalExact": sum(accepted.values()),
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
    target_offsets = Counter[int | None]()
    for record_value, draw_value in zip(records, draws, strict=True):
        record = sweep._require_dict(record_value, "record")  # noqa: SLF001
        draw = sweep._require_dict(draw_value, "draw")  # noqa: SLF001
        record_index = sweep._require_int(draw.get("recordIndex"), "record")  # noqa: SLF001
        vertices = sweep._vertices(vertex_words, record_index)  # noqa: SLF001
        tile = (
            sweep._require_int(draw.get("tileX"), "tile x"),  # noqa: SLF001
            sweep._require_int(draw.get("tileY"), "tile y"),  # noqa: SLF001
        )
        _anchor, _determinant, products = fused._product_streams(  # noqa: SLF001
            vertices, 0, tile
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
        adjusted = (
            terms[0],
            (terms[1][0], terms[1][1] + correction, terms[1][2]),
        )
        target = ruler._offset_from_terms(adjusted, baseline)  # noqa: SLF001
        split = record.get("split")
        if split not in {"discovery", "holdout"}:
            raise ValueError("dense ruler split differs")
        prepared.append((split, target, products, baseline))
        target_offsets[target] += 1

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
        "schema": "walle-reveal-agx-public-child-pair-reduction-sweep-v1",
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
            "targetJoinOffsetHistogram": {
                str(key): value for key, value in sorted(target_offsets.items())
            },
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
