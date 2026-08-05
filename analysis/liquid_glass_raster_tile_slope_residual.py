#!/usr/bin/env python3
"""Recover slope-lattice intervals not explained by schema-3 named laws."""

import argparse
import json
from pathlib import Path
from typing import Any

import liquid_glass_raster_interpolant as raster
import liquid_glass_raster_tile_numerator as recovery
import liquid_glass_raster_tile_selector as selector
import liquid_glass_raster_tile_slope_selector as slope_selector
import validate_raster_tile_numerator as capture


type JsonObject = dict[str, Any]


def analyze(root: Path, report_path: Path, *, radius: int) -> JsonObject:
    source = json.loads(report_path.read_text(encoding="utf-8"))
    if source.get("scope", {}).get("sealedHoldoutRead") is not False:
        raise ValueError("a discovery-only slope report is required")
    unresolved = [
        setup
        for setup in source["measurement"]["setups"]
        if not setup["acceptedModels"]
    ]
    records = selector.raw_records(root)
    cases = {case.name: (index, case) for index, case in enumerate(capture.CASES)}
    endpoints = {
        endpoint.name: (index, endpoint)
        for index, endpoint in enumerate(capture.ENDPOINTS)
    }
    reports: list[JsonObject] = []

    for setup in unresolved:
        case_index, capture_case = cases[str(setup["case"])]
        if capture_case.role == "sealed-holdout":
            raise ValueError("sealed holdout was routed into slope recovery")
        endpoint_index, endpoint = endpoints[str(setup["endpoint"])]
        axis = 0 if setup["axis"] == "x" else 1
        primitive = int(setup["primitive"])
        extent = capture_case.width if axis == 0 else capture_case.height
        delta = raster.float32_bits_fraction(
            endpoint.highBits
        ) - raster.float32_bits_fraction(endpoint.lowBits)
        ideal = delta / extent
        centered = recovery.signed_quantized_slope(ideal)
        step = recovery.slope_step(ideal)
        sample_groups = [
            samples
            for (
                group_axis,
                group_primitive,
                _tile,
            ), samples in selector.paired_sample_groups(capture_case).items()
            if group_axis == axis and group_primitive == primitive
        ]
        accepted_offsets: list[int] = []
        for offset in range(-radius, radius + 1):
            slope = float(centered + offset * step)
            if all(
                slope_selector.constant_candidates(
                    records,
                    case_index=case_index,
                    endpoint_index=endpoint_index,
                    samples=samples,
                    axis=axis,
                    slope=slope,
                )
                for samples in sample_groups
            ):
                accepted_offsets.append(offset)
        reports.append(
            {
                **setup,
                "idealSlope": str(ideal),
                "centeredSlopeHex": float(centered).hex(),
                "slopeStepHex": float(step).hex(),
                "searchRadius": radius,
                "acceptedOffsetCount": len(accepted_offsets),
                "acceptedOffsets": accepted_offsets,
                "boundaryTouched": bool(accepted_offsets)
                and (accepted_offsets[0] == -radius or accepted_offsets[-1] == radius),
            }
        )

    signatures: dict[tuple[str, str, str], list[set[int]]] = {}
    for report in reports:
        endpoint = endpoints[str(report["endpoint"])][1]
        delta = raster.float32_bits_fraction(
            endpoint.highBits
        ) - raster.float32_bits_fraction(endpoint.lowBits)
        key = (str(report["case"]), str(report["axis"]), str(delta))
        signatures.setdefault(key, []).append(set(report["acceptedOffsets"]))
    intersections = [
        {
            "case": key[0],
            "axis": key[1],
            "delta": key[2],
            "setupCount": len(sets),
            "acceptedOffsets": sorted(set.intersection(*sets)),
        }
        for key, sets in sorted(signatures.items())
    ]

    return {
        "liquidGlassRasterTileSlopeResidualAnalysisSchemaVersion": 1,
        "source": str(root),
        "sourceReport": str(report_path),
        "scope": {"sealedHoldoutRead": False, "unresolvedSetupsOnly": True},
        "measurement": {
            "setupCount": len(reports),
            "allRecovered": all(report["acceptedOffsetCount"] for report in reports),
            "boundaryTouchedCount": sum(
                report["boundaryTouched"] for report in reports
            ),
            "setups": reports,
            "equalDeltaIntersections": intersections,
        },
        "conclusions": {
            "selectorLawEstablished": False,
            "sealedHoldoutAuthorized": False,
            "productionShaderAuthorized": False,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("probe", type=Path)
    parser.add_argument("report", type=Path)
    parser.add_argument("--radius", type=int, default=512)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    arguments.output.write_text(
        json.dumps(
            analyze(arguments.probe, arguments.report, radius=arguments.radius),
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
