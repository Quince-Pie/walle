#!/usr/bin/env python3.14
"""Score phase-sensitive AGX setup-slope candidates against the M1 export.

This is an output-blind diagnostic.  It consumes public vertex inputs and raw
``LDCF`` coefficient triples; it never opens reveal reference pixels.  The
focused capture duplicates two geometries four times, so candidate selection
uses targets 0/1/4/5 and targets 2/3/6/7 are an exact holdout.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import struct
import sys
from collections import Counter
from fractions import Fraction
from pathlib import Path
from typing import Final

import numpy as np


ROOT: Final = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "analysis"), str(ROOT / "lg-test" / "Analysis")]

import analyze_reveal_agx_clip_setup_split as setup  # noqa: E402
import analyze_reveal_agx_ldcf_export as export  # noqa: E402
import analyze_reveal_agx_setup_accumulator as accumulator  # noqa: E402
import raster_tile_selector_model as tile_model  # noqa: E402


type Vertex = tuple[float, ...]
type Sample = tuple[tuple[Vertex, Vertex, Vertex], int, int, int, int, int]

CAPTURE_ROOT: Final = (
    ROOT / "build" / "analysis-agx-basis" / "final-two-setup-plan-v2"
)
PLAN: Final = CAPTURE_ROOT / "reveal-agx-setup-accumulator-plan.json"
VERTICES: Final = CAPTURE_ROOT / "reveal-agx-setup-accumulator-vertices.bin"
RAW: Final = CAPTURE_ROOT / "capture" / "reveal-agx-setup-accumulator.raw"
RAW_SHA256: Final = "feebdc12f29df7a967c757701270ef06e5eef8890b8f4506b60aa2bce26c1841"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _float(word: int) -> float:
    return struct.unpack("<f", struct.pack("<I", word))[0]


def _samples() -> tuple[Sample, ...]:
    if _sha256(RAW) != RAW_SHA256:
        raise ValueError("focused M1 capture differs")
    plan = json.loads(PLAN.read_text(encoding="utf-8"))
    draws = plan["draws"]
    raw = np.fromfile(RAW, dtype="<u4").reshape(-1, 101, 4)
    words = np.fromfile(VERTICES, dtype="<u4").reshape(-1, 3, 8)
    result: list[Sample] = []
    for record, draw in enumerate(draws):
        vertices = tuple(
            tuple(_float(int(word)) for word in vertex[[0, 1, 4, 5, 6, 7]])
            for vertex in words[record]
        )
        triples = accumulator._triples(raw[record])  # noqa: SLF001
        for component, triple in enumerate(triples):
            for axis in range(2):
                result.append(
                    (
                        vertices,  # type: ignore[arg-type]
                        component,
                        axis,
                        int(triple[axis]),
                        int(draw["x"]) & 1,
                        int(draw["targetIndex"]),
                    )
                )
    return tuple(result)


def _first_product(
    left: float,
    right: float,
    *,
    output_bits: int,
    truncation_bits: int,
    bias_units: int,
) -> Fraction:
    left = setup._float32(left)  # noqa: SLF001
    right = setup._float32(right)  # noqa: SLF001
    # The setup front end flushes varying differences below the normal range;
    # constant controls then serialize the documented min-subnormal sentinel.
    if abs(left) < 2.0**-126 or abs(right) < 2.0**-126:
        return Fraction()
    sign = -1 if (left < 0.0) != (right < 0.0) else 1
    left_index, left_exponent = accumulator._positive_float_components(  # noqa: SLF001
        setup._float_bits(abs(left))  # noqa: SLF001
    )
    right_index, right_exponent = accumulator._positive_float_components(  # noqa: SLF001
        setup._float_bits(abs(right))  # noqa: SLF001
    )
    index, exponent = tile_model.product_stage(
        left_index,
        left_exponent,
        right_index,
        right_exponent,
        output_bits=output_bits,
        truncation_bits=truncation_bits,
        bias_units=bias_units,
    )
    return sign * index * accumulator._power_of_two(exponent)  # noqa: SLF001


def _predict(
    sample: Sample,
    bitmap: bytes,
    *,
    first_output: int = 27,
    first_truncation: int = 16,
    first_bias: int = 15,
    normalize_precision: int = 27,
    normalize_rounding: str = "nearest-even",
    reciprocal_output: int = 27,
    reciprocal_truncation: int = 19,
    reciprocal_bias: int = 20,
) -> int:
    vertices, component, axis, _actual, _phase, _target = sample
    positions = setup._fixed_positions(vertices)  # noqa: SLF001
    determinant = setup._determinant(positions)  # noqa: SLF001
    anchor = min(range(3), key=lambda index: (positions[index][1], positions[index][0]))
    values = tuple(setup._float32(vertex[2 + component]) for vertex in vertices)  # noqa: SLF001
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
    )[axis]
    numerator = sum(
        (
            _first_product(
                setup._float32(values[index] - values[anchor]),  # noqa: SLF001
                edges[index] / 256.0,
                output_bits=first_output,
                truncation_bits=first_truncation,
                bias_units=first_bias,
            )
            for index in range(3)
            if index != anchor
        ),
        start=Fraction(),
    )
    sign, index, exponent = setup._normalize_signed(  # noqa: SLF001
        numerator,
        precision_bits=normalize_precision,
        rounding=normalize_rounding,
    )
    if sign == 0:
        return 1
    if determinant < 0:
        sign = -sign
    selector, selector_exponent = setup._p25_selector(determinant, bitmap)  # noqa: SLF001
    output_index, output_exponent = tile_model.product_stage(
        index,
        exponent,
        selector,
        selector_exponent,
        output_bits=reciprocal_output,
        truncation_bits=reciprocal_truncation,
        bias_units=reciprocal_bias,
    )
    # ``output_index`` is at most 29 bits, so binary64 represents this scaled
    # power-of-two product exactly before the binary32 materialization.
    return setup._float_bits(  # noqa: SLF001
        setup._float32(math.ldexp(sign * output_index, output_exponent))  # noqa: SLF001
    )


def _score(samples: tuple[Sample, ...], bitmap: bytes, **policy: object) -> dict[str, object]:
    deltas: list[int] = []
    discovery: list[int] = []
    holdout: list[int] = []
    groups: Counter[tuple[int, int, int]] = Counter()
    for sample in samples:
        predicted = _predict(sample, bitmap, **policy)  # type: ignore[arg-type]
        delta = export._float_ulp_delta(sample[3], predicted)  # noqa: SLF001
        deltas.append(delta)
        (discovery if sample[5] in {0, 1, 4, 5} else holdout).append(delta)
        groups[(sample[4], sample[2], delta)] += 1
    return {
        "policy": policy,
        "exact": deltas.count(0),
        "withinOne": sum(abs(delta) <= 1 for delta in deltas),
        "discoveryExact": discovery.count(0),
        "holdoutExact": holdout.count(0),
        "smallHistogram": {
            str(delta): count
            for delta, count in sorted(Counter(deltas).items())
            if abs(delta) <= 8
        },
        "phaseAxisExact": {
            f"phase{phase}-axis{axis}": groups[(phase, axis, 0)]
            for phase in range(2)
            for axis in range(2)
        },
    }


def analyze() -> dict[str, object]:
    samples = _samples()
    bitmap = setup.P25_PATH.read_bytes()
    baseline = _score(samples, bitmap)
    sweeps: dict[str, list[dict[str, object]]] = {}
    dimensions = {
        "firstOutput": ("first_output", range(25, 30)),
        "firstTruncation": ("first_truncation", range(12, 23)),
        "firstBias": ("first_bias", range(0, 33)),
        "normalizePrecision": ("normalize_precision", range(25, 30)),
        "normalizeRounding": (
            "normalize_rounding",
            ("nearest-even", "down", "up"),
        ),
        "reciprocalOutput": ("reciprocal_output", range(25, 30)),
        "reciprocalTruncation": ("reciprocal_truncation", range(15, 25)),
        "reciprocalBias": ("reciprocal_bias", range(0, 41)),
    }
    for label, (name, values) in dimensions.items():
        candidates = [_score(samples, bitmap, **{name: value}) for value in values]
        sweeps[label] = sorted(
            candidates,
            key=lambda candidate: (
                -int(candidate["discoveryExact"]),
                -int(candidate["holdoutExact"]),
                -int(candidate["exact"]),
            ),
        )[:8]
    return {
        "schema": "walle-reveal-agx-coefficient-phase-diagnostic-v1",
        "authority": {
            "readsReferencePixels": False,
            "usesM1CoefficientExport": True,
            "establishesExactLaw": False,
            "productionAuthorized": False,
        },
        "inputs": {
            "planSha256": _sha256(PLAN),
            "verticesSha256": _sha256(VERTICES),
            "rawSha256": _sha256(RAW),
        },
        "sampleCount": len(samples),
        "baseline": baseline,
        "oneDimensionSweeps": sweeps,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT
        / "build"
        / "analysis-agx-basis"
        / "coefficient-phase-analysis"
        / "result.json",
    )
    arguments = parser.parse_args()
    result = analyze()
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(result["baseline"], indent=2, sort_keys=True))
    for name, candidates in result["oneDimensionSweeps"].items():
        print(name, candidates[0])


if __name__ == "__main__":
    main()
