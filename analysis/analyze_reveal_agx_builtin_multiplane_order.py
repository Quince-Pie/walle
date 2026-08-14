#!/usr/bin/env python3.14
"""Recover AGX built-in multi-plane guard order from raw LDCF slopes.

For each of the 24 guard-plane orders, this analysis sequentially applies the
already authenticated endpoint arithmetic, fans the resulting polygon from
vertex zero (the independently established built-in rule), selects the child
containing the captured interior sample, and compares public-input slope
predictions with the M1 coefficient export.  No rendered output is read.
"""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import struct
import sys
from collections import Counter
from pathlib import Path
from typing import Final

import numpy as np


ROOT: Final = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "analysis"))

import analyze_reveal_agx_basis_phase as phase  # noqa: E402
import analyze_reveal_agx_clip_setup_split as setup  # noqa: E402
import analyze_reveal_agx_guard_fan_diagonal as fan  # noqa: E402
import analyze_reveal_agx_guard_order_ldcf as order_capture  # noqa: E402
import analyze_reveal_agx_ldcf_export as export  # noqa: E402
import analyze_reveal_agx_top_left_setup as top_left  # noqa: E402


type Vertex = tuple[float, ...]
type Triangle = tuple[Vertex, Vertex, Vertex]
type JsonObject = dict[str, object]

P25: Final = setup.P25_PATH
OUTPUT: Final = (
    ROOT
    / "build"
    / "analysis-agx-basis"
    / "builtin-multiplane-order"
    / "result.json"
)


def _fan(polygon: tuple[Vertex, ...]) -> tuple[Triangle, ...]:
    return tuple(
        (polygon[0], polygon[index], polygon[index + 1])
        for index in range(1, len(polygon) - 1)
    )


def _selected_child(
    polygon: tuple[Vertex, ...], pixel: tuple[int, int]
) -> tuple[Triangle | None, int]:
    matches = tuple(
        triangle for triangle in _fan(polygon) if fan._triangle_contains_sample(triangle, pixel)  # noqa: SLF001
    )
    return (matches[0] if len(matches) == 1 else None), len(matches)


def _predict_order(
    samples: tuple[phase.Sample, ...],
    actual: np.ndarray,
    order: tuple[int, ...],
    reciprocal_table: np.ndarray,
    p25: bytes,
) -> JsonObject:
    deltas: list[int] = []
    predictions: list[int] = []
    actual_words: list[int] = []
    skipped_no_clip = 0
    skipped_ambiguous = 0
    selected_records = 0
    multi_plane_records = 0
    by_active_plane_count: dict[int, list[int]] = {1: [], 2: [], 3: [], 4: []}
    first_mismatches: list[JsonObject] = []
    representative: dict[tuple[int, int], phase.Sample] = {}
    for sample in samples:
        representative.setdefault((sample.case_index, sample.child_ordinal), sample)

    for sample in representative.values():
        source = fan._source_vertices(sample)  # noqa: SLF001
        active = fan._active_planes(source)  # noqa: SLF001
        if not active:
            skipped_no_clip += 1
            continue
        active_indices = {
            index for index, plane in enumerate(fan.GUARD_PLANES) if plane in active
        }
        polygon: tuple[Vertex, ...] = source
        for plane_index in order:
            if plane_index not in active_indices:
                continue
            axis, edge, keep_greater = fan.GUARD_PLANES[plane_index]
            polygon = fan._clip_one_plane(  # noqa: SLF001
                polygon,
                axis=axis,
                edge=edge,
                keep_greater=keep_greater,
                table=reciprocal_table,
            )
        if len(polygon) < 3:
            continue
        triangle, match_count = _selected_child(polygon, sample.pixel)
        if triangle is None:
            skipped_ambiguous += 1
            continue
        selected_records += 1
        if len(active) > 1:
            multi_plane_records += 1
        positions = setup._fixed_positions(triangle)  # noqa: SLF001
        anchor = top_left._top_left(positions)  # noqa: SLF001
        for component in range(4):
            for coefficient_axis in range(2):
                predicted = top_left._anchor_slope(  # noqa: SLF001
                    triangle, component, coefficient_axis, p25, anchor
                )
                observed = int(
                    actual[
                        sample.record_index,
                        component * 3 + coefficient_axis,
                    ]
                )
                delta = export._float_ulp_delta(observed, predicted)  # noqa: SLF001
                predictions.append(predicted)
                actual_words.append(observed)
                deltas.append(delta)
                by_active_plane_count[len(active)].append(delta)
                if delta and len(first_mismatches) < 12:
                    first_mismatches.append(
                        {
                            "state": sample.state,
                            "sourcePrimitive": sample.source_primitive,
                            "childOrdinalWithinSource": sample.child_ordinal_within_source,
                            "pixel": list(sample.pixel),
                            "activePlaneCount": len(active),
                            "polygonVertexCount": len(polygon),
                            "component": component,
                            "axis": coefficient_axis,
                            "actualBits": f"0x{observed:08x}",
                            "predictedBits": f"0x{predicted:08x}",
                            "actualMinusPredictedFloatUlps": delta,
                            "childMatchCount": match_count,
                        }
                    )

    histogram = Counter(deltas)
    return {
        "order": "".join(str(value) for value in order),
        "selectedRecordCount": selected_records,
        "multiPlaneRecordCount": multi_plane_records,
        "skippedNoClipRecordCount": skipped_no_clip,
        "skippedAmbiguousRecordCount": skipped_ambiguous,
        "slopeWordCount": len(deltas),
        "exactCount": histogram[0],
        "withinOneUlpCount": sum(
            count for delta, count in histogram.items() if abs(delta) <= 1
        ),
        "minimumUlpDelta": min(deltas),
        "maximumUlpDelta": max(deltas),
        "byActivePlaneCount": {
            str(count): {
                "count": len(values),
                "exactCount": values.count(0),
                "withinOneUlpCount": sum(abs(value) <= 1 for value in values),
            }
            for count, values in by_active_plane_count.items()
            if values
        },
        "predictionSha256": hashlib.sha256(
            struct.pack(f"<{len(predictions)}I", *predictions)
        ).hexdigest(),
        "actualSha256": hashlib.sha256(
            struct.pack(f"<{len(actual_words)}I", *actual_words)
        ).hexdigest(),
        "smallDeltaHistogram": {
            str(delta): count
            for delta, count in sorted(histogram.items())
            if abs(delta) <= 16
        },
        "firstMismatches": first_mismatches,
    }


def analyze() -> JsonObject:
    catalog, samples = phase._load_catalog(order_capture.CATALOG)  # noqa: SLF001
    _manifest, builtin_words, _raw = phase._load_capture(  # noqa: SLF001
        order_capture.BUILTIN_CAPTURE,
        catalog_path=order_capture.CATALOG,
        record_count=len(samples),
    )
    actual = order_capture._capture_coefficients(builtin_words)  # noqa: SLF001
    reciprocal_table = np.fromfile(fan.endpoint.RECIPROCAL_TABLE, dtype="<u4")
    p25 = P25.read_bytes()
    results = [
        _predict_order(samples, actual, order, reciprocal_table, p25)
        for order in itertools.permutations(range(4))
    ]
    results.sort(
        key=lambda result: (
            -int(result["exactCount"]),
            -int(result["withinOneUlpCount"]),
            str(result["order"]),
        )
    )
    return {
        "schema": "walle-reveal-agx-builtin-multiplane-order-analysis-v1",
        "authority": {
            "readsReferencePixels": False,
            "usesM1CoefficientExport": True,
            "usesMeasuredEndpointArithmetic": True,
            "establishesExactMultiPlaneLaw": False,
            "productionAuthorized": False,
        },
        "planeIndex": {
            str(index): {"axis": axis, "edge": edge, "keepGreater": keep}
            for index, (axis, edge, keep) in enumerate(fan.GUARD_PLANES)
        },
        "rankedOrders": results,
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
    print(json.dumps(result["rankedOrders"][:8], indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
