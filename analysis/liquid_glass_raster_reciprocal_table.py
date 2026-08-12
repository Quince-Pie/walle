#!/usr/bin/env python3
"""Build the complete measured Apple raster-reciprocal table."""

import argparse
import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

import liquid_glass_raster_reciprocal_sweep as sweep


type JsonObject = dict[str, Any]
type UIntArray = NDArray[np.uint32]

NORMALIZED_DENOMINATOR_LOWER = 8_192
NORMALIZED_DENOMINATOR_UPPER = 16_383
CANONICAL_CLASS_COUNT = 8_192


@dataclass(frozen=True)
class Partition:
    role: str
    report_path: Path
    table_path: Path
    report_sha256: str
    table_sha256: str
    widths: tuple[int, ...]
    selected_by_class: dict[int, int]
    coefficient_count: int
    scale_equivalence_comparisons: int


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def normalized_denominator(width: int) -> int:
    normalized = sweep.normalization_class(width)
    if normalized & 1:
        raise ValueError(f"width {width} has an odd normalization class")
    denominator = normalized >> 1
    if not (
        NORMALIZED_DENOMINATOR_LOWER
        <= denominator
        <= NORMALIZED_DENOMINATOR_UPPER
    ):
        raise ValueError(f"width {width} lies outside the canonical domain")
    return denominator


def load_partition(
    *,
    role: str,
    report_path: Path,
    table_path: Path,
) -> Partition:
    report: JsonObject = json.loads(report_path.read_text(encoding="utf-8"))
    expected_widths = tuple(
        sweep.selected_widths(holdout=role == "holdout")
    )
    measurement = report.get("measurement", {})
    truth = report.get("reciprocalTruthTable", {})
    records = report.get("widths")
    table = np.fromfile(table_path, dtype="<u4")
    if (
        role not in {"discovery", "holdout"}
        or report.get("selectedRole") != role
        or not isinstance(records, list)
        or len(records) != len(expected_widths)
        or table.shape != (len(expected_widths),)
        or truth.get("sha256") != sha256_path(table_path)
        or measurement.get("widthCount") != len(expected_widths)
        or measurement.get("candidateMatchCountDistribution")
        != {"1": len(expected_widths)}
        or measurement.get("physicalProductMismatchCount") != 0
        or measurement.get("exact") is not True
    ):
        raise ValueError(f"{role} reciprocal report differs")
    if role == "holdout" and (
        report.get("holdoutOpeningClassification")
        != "calibration-not-prospective-validation"
    ):
        raise ValueError("holdout report loses its calibration classification")

    selected_by_class: dict[int, int] = {}
    for index, (width, record) in enumerate(
        zip(expected_widths, records, strict=True)
    ):
        if (
            not isinstance(record, dict)
            or record.get("width") != width
            or record.get("normalizationClass")
            != sweep.normalization_class(width)
        ):
            raise ValueError(f"{role} width record {index} differs")
        selected = record.get("selectedReciprocal25Index")
        if (
            not isinstance(selected, int)
            or selected != int(table[index])
            or record.get("nearestEvenOffset") not in {-1, 0, 1}
        ):
            raise ValueError(f"{role} selector record {index} differs")
        denominator = normalized_denominator(width)
        previous = selected_by_class.setdefault(denominator, selected)
        if previous != selected:
            raise ValueError(
                f"{role} class {denominator} has conflicting selectors"
            )

    return Partition(
        role=role,
        report_path=report_path,
        table_path=table_path,
        report_sha256=sha256_path(report_path),
        table_sha256=sha256_path(table_path),
        widths=expected_widths,
        selected_by_class=selected_by_class,
        coefficient_count=int(measurement["coefficientCount"]),
        scale_equivalence_comparisons=int(
            measurement["scaleEquivalenceComparisonCount"]
        ),
    )


def combine_partitions(
    discovery: Partition,
    holdout: Partition,
) -> UIntArray:
    discovery_classes = set(discovery.selected_by_class)
    holdout_classes = set(holdout.selected_by_class)
    if discovery.role != "discovery" or holdout.role != "holdout":
        raise ValueError("reciprocal partitions have the wrong roles")
    if discovery_classes & holdout_classes:
        raise ValueError("reciprocal class leaked across partitions")
    combined = discovery.selected_by_class | holdout.selected_by_class
    expected = set(
        range(
            NORMALIZED_DENOMINATOR_LOWER,
            NORMALIZED_DENOMINATOR_UPPER + 1,
        )
    )
    if set(combined) != expected:
        missing = sorted(expected - set(combined))
        extra = sorted(set(combined) - expected)
        raise ValueError(
            f"canonical reciprocal domain differs: "
            f"missing={missing[:8]}, extra={extra[:8]}"
        )
    return np.asarray(
        [
            combined[denominator]
            for denominator in range(
                NORMALIZED_DENOMINATOR_LOWER,
                NORMALIZED_DENOMINATOR_UPPER + 1,
            )
        ],
        dtype="<u4",
    )


def analyze(
    *,
    discovery_report_path: Path,
    discovery_table_path: Path,
    holdout_report_path: Path,
    holdout_table_path: Path,
    canonical_table_path: Path,
) -> JsonObject:
    discovery = load_partition(
        role="discovery",
        report_path=discovery_report_path,
        table_path=discovery_table_path,
    )
    holdout = load_partition(
        role="holdout",
        report_path=holdout_report_path,
        table_path=holdout_table_path,
    )
    canonical = combine_partitions(discovery, holdout)
    canonical_table_path.write_bytes(canonical.tobytes(order="C"))

    offset_counts: Counter[int] = Counter()
    faithful_direction_counts: Counter[str] = Counter()
    for index, selected_value in enumerate(canonical):
        denominator = NORMALIZED_DENOMINATOR_LOWER + index
        selected = int(selected_value)
        nearest = sweep.nearest_even_reciprocal_index(denominator)
        offset_counts[selected - nearest] += 1
        exact_numerator = 1 << (
            24 + (denominator - 1).bit_length()
        )
        error = selected * denominator - exact_numerator
        faithful_direction_counts[
            "exact" if error == 0 else "above" if error > 0 else "below"
        ] += 1

    production_selectors = {
        str(width): int(canonical[normalized_denominator(width) - 8_192])
        for width in sweep.PRODUCTION_HOLDOUT_WIDTHS
    }
    total_width_count = len(discovery.widths) + len(holdout.widths)
    total_coefficient_count = (
        discovery.coefficient_count + holdout.coefficient_count
    )
    return {
        "liquidGlassRasterReciprocalTableAnalysisSchemaVersion": 1,
        "classification": (
            "complete finite-domain calibration; prospective transfer "
            "validation still required"
        ),
        "inputs": {
            "discovery": {
                "report": str(discovery.report_path),
                "reportSha256": discovery.report_sha256,
                "table": str(discovery.table_path),
                "tableSha256": discovery.table_sha256,
                "widthCount": len(discovery.widths),
                "normalizationClassCount": len(
                    discovery.selected_by_class
                ),
            },
            "holdoutCalibration": {
                "report": str(holdout.report_path),
                "reportSha256": holdout.report_sha256,
                "table": str(holdout.table_path),
                "tableSha256": holdout.table_sha256,
                "widthCount": len(holdout.widths),
                "normalizationClassCount": len(holdout.selected_by_class),
                "notProspectiveModelValidation": True,
            },
        },
        "canonicalTable": {
            "file": str(canonical_table_path),
            "sha256": sha256_path(canonical_table_path),
            "bytes": canonical_table_path.stat().st_size,
            "dtype": "little-endian uint32",
            "shape": [CANONICAL_CLASS_COUNT],
            "ordering": (
                "normalized denominator 8192 through 16383 inclusive"
            ),
            "selectorPrecisionBits": 25,
        },
        "measurement": {
            "widthCount": total_width_count,
            "normalizationClassCount": canonical.size,
            "normalizationClassExpectedCount": CANONICAL_CLASS_COUNT,
            "coefficientCount": total_coefficient_count,
            "physicalProductMatchCount": total_coefficient_count,
            "physicalProductMismatchCount": 0,
            "scaleEquivalenceComparisonCount": (
                discovery.scale_equivalence_comparisons
                + holdout.scale_equivalence_comparisons
            ),
            "scaleEquivalenceMismatchCount": 0,
            "canonicalNearestEvenOffsetDistribution": {
                str(offset): count
                for offset, count in sorted(offset_counts.items())
            },
            "canonicalExactReciprocalErrorDirectionDistribution": dict(
                sorted(faithful_direction_counts.items())
            ),
            "complete": canonical.size == CANONICAL_CLASS_COUNT,
            "exactOnMeasuredDomain": True,
        },
        "productionWidthSelectors": production_selectors,
        "prospectiveGate": {
            "passed": False,
            "requiredBeforeProductionParityClaim": True,
            "nextStep": (
                "freeze this table hash and predicted raw outputs for unseen "
                "power-of-two-scaled and geometry-varied cases"
            ),
        },
        "conclusions": {
            "finiteNormalizedSelectorDomainCompletelyMeasured": True,
            "canonicalTableHasNoMissingOrConflictingEntries": True,
            "physicalPartialProductLawExactOnAllMeasuredCoefficients": True,
            "closedFormReciprocalSelectorEstablished": False,
            "prospectiveScaleAndGeometryTransferEstablished": False,
            "endToEndLiquidGlassParityEstablished": False,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--discovery-report", type=Path, required=True)
    parser.add_argument("--discovery-table", type=Path, required=True)
    parser.add_argument("--holdout-report", type=Path, required=True)
    parser.add_argument("--holdout-table", type=Path, required=True)
    parser.add_argument("--canonical-table", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    report = analyze(
        discovery_report_path=arguments.discovery_report,
        discovery_table_path=arguments.discovery_table,
        holdout_report_path=arguments.holdout_report,
        holdout_table_path=arguments.holdout_table,
        canonical_table_path=arguments.canonical_table,
    )
    arguments.output.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
