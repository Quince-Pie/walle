#!/usr/bin/env python3
"""Compare Apple raster slopes with captured Metal arithmetic paths."""

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import numpy as np

import liquid_glass_raster_interpolant as raster
import liquid_glass_raster_reciprocal_sweep as sweep
import liquid_glass_raster_tomography as tomography


type JsonObject = dict[str, Any]


def component_path(component: str) -> tuple[str, str]:
    if not component or component[-1] not in {"X", "Y"}:
        raise ValueError(
            f"arithmetic component has no axis suffix: {component}"
        )
    return component[:-1], component[-1].lower()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def dimension_law_report(
    observations_by_key: dict[
        tuple[int, int],
        list[JsonObject],
    ],
) -> JsonObject:
    groups: list[JsonObject] = []
    conflicting_groups = 0
    repeated_groups = 0
    for (dimension, delta_index), observations in sorted(
        observations_by_key.items()
    ):
        observed_bits = sorted({
            int(record["bits"])
            for record in observations
        })
        conflicting = len(observed_bits) != 1
        conflicting_groups += conflicting
        repeated_groups += len(observations) > 1
        groups.append({
            "dimension": dimension,
            "deltaIndex": delta_index,
            "observationCount": len(observations),
            "axes": sorted({
                str(record["axis"])
                for record in observations
            }),
            "observedBits": [
                f"0x{bits:08x}"
                for bits in observed_bits
            ],
            "consistent": not conflicting,
        })
    return {
        "expression": (
            "slopeBits = hiddenDivide(delta, axisDimension)"
        ),
        "groups": groups,
        "groupCount": len(groups),
        "repeatedGroupCount": repeated_groups,
        "conflictingGroupCount": conflicting_groups,
        "exact": conflicting_groups == 0,
        "fullyDetermined": conflicting_groups == 0,
    }


def analyze_arithmetic(root: Path) -> JsonObject:
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schemaVersion") != 11:
        raise ValueError("raster probe schema 11 is required")
    arithmetic = manifest.get("arithmeticProbe", {})
    if arithmetic.get("role") != "discovery":
        raise ValueError("discovery-only arithmetic evidence is required")

    components = [
        str(component)
        for component in arithmetic.get("components", [])
    ]
    component_indices = {
        component: index
        for index, component in enumerate(components)
    }
    paths = sorted({
        component_path(component)[0]
        for component in components
    })
    for path in paths:
        for axis in ("X", "Y"):
            if f"{path}{axis}" not in component_indices:
                raise ValueError(
                    f"{path} does not contain both axes"
                )

    arithmetic_cases = list(arithmetic.get("cases", []))
    arithmetic_case_indices = {
        str(record["name"]): index
        for index, record in enumerate(arithmetic_cases)
    }
    if len(arithmetic_case_indices) != len(arithmetic_cases):
        raise ValueError("arithmetic cases are not unique")
    delta_count = len(arithmetic.get("deltaNumerators", []))
    raw_path = root / str(arithmetic["file"])
    if (
        raw_path.stat().st_size != int(arithmetic["bytes"])
        or sha256_file(raw_path) != arithmetic["sha256"]
    ):
        raise ValueError("arithmetic evidence hash or size differs")
    values = np.fromfile(raw_path, dtype="<u4")
    expected_values = (
        len(arithmetic_cases) * delta_count * len(components)
    )
    if values.size != expected_values:
        raise ValueError(
            f"arithmetic evidence has {values.size} values; "
            f"expected {expected_values}"
        )
    values = values.reshape(
        len(arithmetic_cases),
        delta_count,
        len(components),
    )

    candidate_matches = Counter()
    candidate_comparisons = Counter()
    candidate_exact_cases = Counter()
    candidate_ulp_deltas: dict[str, Counter[int]] = {
        path: Counter()
        for path in paths
    }
    dimension_observations_by_scope: dict[
        str,
        dict[tuple[int, int], list[JsonObject]],
    ] = {
        "allDiscovery": defaultdict(list),
        "reciprocalSweep": defaultdict(list),
        "otherControls": defaultdict(list),
    }
    total_observed_values = 0
    total_mismatched_values = 0
    total_ambiguous_slopes = 0
    total_ambiguous_constants = 0
    case_reports: list[JsonObject] = []
    discovery_cases = tomography.load_tomography_cases(
        root,
        role="discovery",
    )
    for case in discovery_cases:
        scope = (
            "reciprocalSweep"
            if "tomography-discovery-reciprocal-bin-" in case.name
            else "otherControls"
        )
        case_index = arithmetic_case_indices.get(case.name)
        if case_index is None:
            raise ValueError(
                f"{case.name} has no arithmetic control"
            )
        observations, measurement = sweep.recover_case_slopes(case)
        total_observed_values += int(
            measurement["observedFloatValues"]
        )
        total_mismatched_values += int(
            measurement["mismatchedFloatValues"]
        )
        total_ambiguous_slopes += int(
            measurement["ambiguousSlopeModels"]
        )
        total_ambiguous_constants += int(
            measurement["ambiguousTileConstants"]
        )
        case_exact = {
            path: True
            for path in paths
        }
        case_slopes: list[JsonObject] = []
        for observation in observations:
            if len(observation.accepted_bits) != 1:
                raise ValueError(
                    f"{case.name} has an ambiguous recovered slope"
                )
            observed_bits = next(iter(observation.accepted_bits))
            axis_suffix = observation.axis.upper()
            dimension = (
                case.width
                if observation.axis == "x"
                else case.height
            )
            dimension_record = {
                "name": case.name,
                "axis": observation.axis,
                "bits": observed_bits,
            }
            for selected_scope in ("allDiscovery", scope):
                dimension_observations_by_scope[selected_scope][
                    (dimension, observation.delta_index)
                ].append(dimension_record)
            for path in paths:
                component_index = component_indices[
                    f"{path}{axis_suffix}"
                ]
                candidate_bits = int(values[
                    case_index,
                    observation.delta_index,
                    component_index,
                ])
                matches = (
                    candidate_bits
                    in observation.accepted_bits
                )
                candidate_comparisons[path] += 1
                candidate_matches[path] += matches
                candidate_ulp_deltas[path][
                    observed_bits - candidate_bits
                ] += 1
                case_exact[path] &= matches
            reference_path = (
                "operatorDivide"
                if "operatorDivide" in paths
                else paths[0]
            )
            reference_bits = int(values[
                case_index,
                observation.delta_index,
                component_indices[
                    f"{reference_path}{axis_suffix}"
                ],
            ])
            case_slopes.append({
                "deltaIndex": observation.delta_index,
                "axis": observation.axis,
                "dimension": dimension,
                "observedBits": f"0x{observed_bits:08x}",
                "correctlyRoundedDivideBits":
                    f"0x{reference_bits:08x}",
                "observedMinusCorrectlyRoundedUlp":
                    observed_bits - reference_bits,
            })
        for path, exact in case_exact.items():
            candidate_exact_cases[path] += exact
        error_distribution = Counter(
            int(slope["observedMinusCorrectlyRoundedUlp"])
            for slope in case_slopes
        )
        case_reports.append({
            "name": case.name,
            "width": case.width,
            "height": case.height,
            "area": case.width * case.height,
            "slopes": case_slopes,
            "correctlyRoundedErrorDistribution": {
                str(delta): count
                for delta, count in sorted(
                    error_distribution.items()
                )
            },
            "mixedErrorSigns": (
                any(delta < 0 for delta in error_distribution)
                and any(delta > 0 for delta in error_distribution)
            ),
        })

    identical_candidate_groups: list[list[str]] = []
    ungrouped = set(paths)
    while ungrouped:
        selected = min(ungrouped)
        selected_x = values[
            :,
            :,
            component_indices[f"{selected}X"],
        ]
        selected_y = values[
            :,
            :,
            component_indices[f"{selected}Y"],
        ]
        group = sorted(
            path
            for path in ungrouped
            if (
                np.array_equal(
                    selected_x,
                    values[
                        :,
                        :,
                        component_indices[f"{path}X"],
                    ],
                )
                and np.array_equal(
                    selected_y,
                    values[
                        :,
                        :,
                        component_indices[f"{path}Y"],
                    ],
                )
            )
        )
        identical_candidate_groups.append(group)
        ungrouped.difference_update(group)

    return {
        "liquidGlassRasterArithmeticAnalysisSchemaVersion": 1,
        "probe": str(root),
        "manifestSha256": sha256_file(manifest_path),
        "selectedRole": "discovery",
        "holdoutOpened": False,
        "candidatePaths": [
            {
                "name": path,
                "matchedSlopes": candidate_matches[path],
                "slopeComparisons":
                    candidate_comparisons[path],
                "exactCaseCount":
                    candidate_exact_cases[path],
                "caseCount": len(discovery_cases),
                "exact": (
                    candidate_matches[path]
                    == candidate_comparisons[path]
                ),
                "observedMinusCandidateUlpDistribution": {
                    str(delta): count
                    for delta, count in sorted(
                        candidate_ulp_deltas[path].items()
                    )
                },
            }
            for path in paths
        ],
        "identicalCandidateBitPatternGroups":
            identical_candidate_groups,
        "cases": case_reports,
        "dimensionLaws": {
            scope: dimension_law_report(observations)
            for scope, observations
            in dimension_observations_by_scope.items()
        },
        "measurement": {
            "caseCount": len(discovery_cases),
            "slopeCount": sum(candidate_comparisons.values())
                // len(paths),
            "observedFloatValues": total_observed_values,
            "mismatchedFloatValues": total_mismatched_values,
            "ambiguousSlopeModels": total_ambiguous_slopes,
            "ambiguousTileConstants": total_ambiguous_constants,
            "exact": total_mismatched_values == 0,
        },
        "hiddenDividerFullyDetermined": False,
        "holdoutAuthorized": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Compare schema-11 raster slopes with Metal arithmetic."
        )
    )
    parser.add_argument("probe", type=Path)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    report = analyze_arithmetic(arguments.probe)
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if arguments.output is None:
        print(rendered, end="")
    else:
        arguments.output.write_text(rendered, encoding="utf-8")
    return 0 if report["measurement"]["exact"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
