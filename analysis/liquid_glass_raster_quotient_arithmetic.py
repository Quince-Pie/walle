#!/usr/bin/env python3
"""Compare exposed Metal arithmetic with Apple's fixed-function raster quotient."""

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray


type JsonObject = dict[str, Any]
type UIntArray = NDArray[np.uint32]
type SignedArray = NDArray[np.int64]

SCHEMA_VERSION = 21
RIG_VERSION = "metal-raster-interpolant-probe-21.0.0"
HOLDOUT_WIDTHS = tuple(range(37, 128, 6))
DISCOVERY_WIDTHS = tuple(
    width for width in range(32, 128) if width not in HOLDOUT_WIDTHS
)
NUMERATOR_LOWER = 32_768
NUMERATOR_UPPER = 65_535
NUMERATOR_COUNT = NUMERATOR_UPPER - NUMERATOR_LOWER + 1
COMPONENTS = (
    "operatorDivide",
    "fastDivide",
    "preciseDivide",
    "fastReciprocalProduct",
    "preciseReciprocalProduct",
    "operatorNormalizedIntegerDivide",
    "fastNormalizedIntegerDivide",
    "preciseNormalizedIntegerDivide",
    "fastReciprocalWidth",
    "preciseReciprocalWidth",
    "operatorReciprocalWidth",
    "deltaControl",
)
QUOTIENT_COMPONENT_COUNT = 8
RECIPROCAL_COMPONENT_INDICES = (8, 9, 10)
DELTA_COMPONENT_INDEX = 11


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def distribution(values: SignedArray) -> JsonObject:
    unique, counts = np.unique(values, return_counts=True)
    return {
        str(int(value)): int(count) for value, count in zip(unique, counts, strict=True)
    }


def comparison(reference: UIntArray, candidate: UIntArray) -> JsonObject:
    errors = reference.astype(np.int64) - candidate.astype(np.int64)
    sample_count = int(reference.size)
    match_count = int(np.count_nonzero(errors == 0))
    return {
        "sampleCount": sample_count,
        "matchCount": match_count,
        "mismatchCount": sample_count - match_count,
        "matchRate": match_count / sample_count,
        "referenceMinusCandidateFloatUlpDistribution": distribution(errors),
        "exact": match_count == sample_count,
    }


def equivalence_classes(
    values: UIntArray,
    *,
    component_names: tuple[str, ...],
) -> list[list[str]]:
    if values.shape[-1] != len(component_names):
        raise ValueError("component names do not describe the array")
    remaining = list(range(len(component_names)))
    classes: list[list[str]] = []
    while remaining:
        representative = remaining.pop(0)
        equivalent = [representative]
        distinct: list[int] = []
        for candidate in remaining:
            if np.array_equal(
                values[..., representative],
                values[..., candidate],
            ):
                equivalent.append(candidate)
            else:
                distinct.append(candidate)
        classes.append([component_names[index] for index in equivalent])
        remaining = distinct
    return classes


def expected_arithmetic_bytes() -> int:
    return (
        len(DISCOVERY_WIDTHS)
        * NUMERATOR_COUNT
        * len(COMPONENTS)
        * np.dtype("<u4").itemsize
    )


def validate_manifest(root: Path) -> tuple[JsonObject, Path]:
    manifest_path = root / "manifest.json"
    manifest: JsonObject = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (
        manifest.get("schemaVersion") != SCHEMA_VERSION
        or manifest.get("rigVersion") != RIG_VERSION
    ):
        raise ValueError("raster quotient arithmetic schema 21 is required")

    probe = manifest.get("quotientArithmeticProbe", {})
    arithmetic_path = root / str(probe.get("file", ""))
    if (
        probe.get("role") != "discovery"
        or probe.get("widths") != list(DISCOVERY_WIDTHS)
        or probe.get("holdoutWidthsExcluded") != list(HOLDOUT_WIDTHS)
        or probe.get("numeratorLowerInclusive") != NUMERATOR_LOWER
        or probe.get("numeratorUpperInclusive") != NUMERATOR_UPPER
        or probe.get("deltaDenominator") != 65_536
        or probe.get("vectorsPerSample") != 3
        or probe.get("components") != list(COMPONENTS)
        or probe.get("ordering") != "width-major,numerator-major,component-major"
        or probe.get("bytes") != expected_arithmetic_bytes()
        or not arithmetic_path.is_file()
        or arithmetic_path.stat().st_size != expected_arithmetic_bytes()
        or sha256_file(arithmetic_path) != probe.get("sha256")
    ):
        raise ValueError("raster quotient arithmetic metadata differs")
    return manifest, arithmetic_path


def resolve_report_file(report_path: Path, recorded_path: str) -> Path:
    path = Path(recorded_path)
    candidates = (
        path,
        report_path.parent / path,
        report_path.parent.parent / path,
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise ValueError(f"recorded truth-table file is absent: {recorded_path}")


def validate_truth_table(
    report_path: Path,
) -> tuple[JsonObject, Path, UIntArray]:
    report: JsonObject = json.loads(report_path.read_text(encoding="utf-8"))
    truth = report.get("truthTable", {})
    truth_path = resolve_report_file(report_path, str(truth.get("file", "")))
    expected_bytes = len(DISCOVERY_WIDTHS) * NUMERATOR_COUNT * np.dtype("<u4").itemsize
    if (
        report.get("selectedRole") != "discovery"
        or report.get("holdoutOpened") is not False
        or truth.get("discoveryDomainFullyEnumerated") is not True
        or truth.get("dtype") != "little-endian uint32 float bits"
        or truth.get("shape") != [len(DISCOVERY_WIDTHS), NUMERATOR_COUNT]
        or truth.get("ordering") != "width-major,numerator-major"
        or truth.get("widths") != list(DISCOVERY_WIDTHS)
        or truth.get("numeratorLowerInclusive") != NUMERATOR_LOWER
        or truth.get("numeratorUpperInclusive") != NUMERATOR_UPPER
        or truth.get("bytes") != expected_bytes
        or truth_path.stat().st_size != expected_bytes
        or sha256_file(truth_path) != truth.get("sha256")
    ):
        raise ValueError("fixed-function raster truth-table metadata differs")
    table = np.memmap(
        truth_path,
        dtype="<u4",
        mode="r",
        shape=(len(DISCOVERY_WIDTHS), NUMERATOR_COUNT),
    )
    return report, truth_path, table


def analyze(root: Path, *, truth_report_path: Path) -> JsonObject:
    manifest, arithmetic_path = validate_manifest(root)
    truth_report, truth_path, truth = validate_truth_table(truth_report_path)
    arithmetic = np.memmap(
        arithmetic_path,
        dtype="<u4",
        mode="r",
        shape=(len(DISCOVERY_WIDTHS), NUMERATOR_COUNT, len(COMPONENTS)),
    )

    expected_delta = np.asarray(
        np.arange(NUMERATOR_LOWER, NUMERATOR_UPPER + 1, dtype=np.float32)
        * np.float32(2.0**-16),
        dtype="<f4",
    ).view("<u4")
    delta_exact = bool(
        np.array_equal(
            arithmetic[..., DELTA_COMPONENT_INDEX],
            np.broadcast_to(expected_delta, truth.shape),
        )
    )
    if not delta_exact:
        raise ValueError("quotient arithmetic delta control differs")

    reciprocal_values = arithmetic[..., RECIPROCAL_COMPONENT_INDICES]
    reciprocal_classes = equivalence_classes(
        reciprocal_values,
        component_names=tuple(
            COMPONENTS[index] for index in RECIPROCAL_COMPONENT_INDICES
        ),
    )
    reciprocal_constant_by_width = bool(
        np.all(reciprocal_values == reciprocal_values[:, :1, :])
    )
    if not reciprocal_constant_by_width:
        raise ValueError("an exposed reciprocal changed within a width")

    quotient_values = arithmetic[..., :QUOTIENT_COMPONENT_COUNT]
    component_reports = {
        COMPONENTS[index]: comparison(truth, quotient_values[..., index])
        for index in range(QUOTIENT_COMPONENT_COUNT)
    }
    quotient_classes = equivalence_classes(
        quotient_values,
        component_names=COMPONENTS[:QUOTIENT_COMPONENT_COUNT],
    )

    widths: list[JsonObject] = []
    for width_index, width in enumerate(DISCOVERY_WIDTHS):
        widths.append(
            {
                "width": width,
                "components": {
                    COMPONENTS[index]: comparison(
                        truth[width_index],
                        quotient_values[width_index, :, index],
                    )
                    for index in range(QUOTIENT_COMPONENT_COUNT)
                },
                "reciprocalBits": {
                    COMPONENTS[index]: (
                        f"0x{int(arithmetic[width_index, 0, index]):08x}"
                    )
                    for index in RECIPROCAL_COMPONENT_INDICES
                },
            }
        )

    no_exposed_path_exact = not any(
        component["exact"] for component in component_reports.values()
    )
    return {
        "liquidGlassRasterQuotientArithmeticAnalysisSchemaVersion": 1,
        "probe": str(root),
        "manifestSha256": sha256_file(root / "manifest.json"),
        "arithmeticSha256": sha256_file(arithmetic_path),
        "truthReport": str(truth_report_path),
        "truthReportSha256": sha256_file(truth_report_path),
        "truthTable": str(truth_path),
        "truthTableSha256": sha256_file(truth_path),
        "selectedRole": "discovery",
        "holdoutOpened": False,
        "holdoutAuthorized": False,
        "measurement": {
            "widthCount": len(DISCOVERY_WIDTHS),
            "normalizedNumeratorCountPerWidth": NUMERATOR_COUNT,
            "sampleCount": int(truth.size),
            "deltaControlExact": delta_exact,
        },
        "quotientComponentEquivalenceClasses": quotient_classes,
        "reciprocalComponentEquivalenceClasses": reciprocal_classes,
        "reciprocalComponentsConstantWithinWidth": reciprocal_constant_by_width,
        "components": component_reports,
        "widths": widths,
        "conclusions": {
            "noExposedMetalArithmeticPathMatchesFixedFunctionExactly": (
                no_exposed_path_exact
            ),
            "fixedFunctionRasterQuotientDistinctFromExposedMetalArithmetic": (
                no_exposed_path_exact
            ),
            "portableFixedFunctionSelectorFullyDetermined": False,
            "fixedFunctionTruthTableFullyDeterminedForDiscoveryDomain": (
                truth_report.get("dividerTruthTableFullyDeterminedForDiscoveryDomain")
                is True
            ),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("--truth-report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    report = analyze(
        arguments.root,
        truth_report_path=arguments.truth_report,
    )
    arguments.output.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
