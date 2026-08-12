#!/usr/bin/env python3
"""Separate Apple's local SDF scale from opacity-dependent source generation."""

import argparse
import json
import platform
import resource
import tempfile
import time
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from liquid_glass_fixed_resource_lod import (
    difference_metrics,
)
from liquid_glass_lod_cross_match import (
    exact_catalog_candidates,
)
from liquid_glass_lod_sweep import (
    LodSweep,
)
from liquid_glass_sdf_scale import (
    DEFAULT_RADIUS_FOUR_STATE,
    SCALE_HALF_BITS_MAXIMUM,
    SCALE_HALF_BITS_MINIMUM,
    STATE_COUNT,
    SdfScaleSweep,
    _candidate_summary,
    _catalog_words,
    _normalized_circle_prediction,
    _prediction_metrics,
    _state_signature_words,
    _unique_radial_fit,
    _write_maps,
    sha256_file,
)


type JsonObject = dict[str, Any]
type UInt8Array = NDArray[np.uint8]


def _curve_difference(
    pinned: UInt8Array,
    all_opacity: UInt8Array,
) -> JsonObject:
    if pinned.shape != all_opacity.shape:
        raise ValueError("SDF scale curve shapes differ")
    state_records: list[JsonObject] = []
    total_values = 0
    total_changed = 0
    total_changed_pixels = 0
    maximum = 0
    for state_index in range(STATE_COUNT):
        left = pinned[:, state_index]
        right = all_opacity[:, state_index]
        changed = left != right
        changed_pixels = np.any(changed, axis=-1)
        distance = np.abs(
            left.astype(np.int16) - right.astype(np.int16)
        )
        values = int(changed.size)
        changed_values = int(np.count_nonzero(changed))
        changed_pixel_count = int(
            np.count_nonzero(changed_pixels)
        )
        current_maximum = int(distance.max(initial=0))
        total_values += values
        total_changed += changed_values
        total_changed_pixels += changed_pixel_count
        maximum = max(maximum, current_maximum)
        state_records.append({
            "index": state_index,
            "halfBits":
                f"{SCALE_HALF_BITS_MINIMUM + state_index:04x}",
            "changedValues": changed_values,
            "changedPixels": changed_pixel_count,
            "maximumAbsoluteCodes": current_maximum,
            "exact": changed_values == 0,
        })
    exact_states = [
        record["index"]
        for record in state_records
        if record["exact"]
    ]
    return {
        "values": total_values,
        "changedValues": total_changed,
        "exactValueFraction":
            1 - total_changed / total_values,
        "changedPixels": total_changed_pixels,
        "maximumAbsoluteCodes": maximum,
        "exact": total_changed == 0,
        "exactStateIndices": exact_states,
        "changedStateCount":
            STATE_COUNT - len(exact_states),
        "states": state_records,
    }


def analyze(
    pinned_path: Path,
    all_opacity_path: Path,
    default_path: Path,
    *,
    map_output: Path | None = None,
) -> JsonObject:
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(
        prefix="liquid-glass-pinned-sdf-scale-"
    ) as temporary:
        scratch = Path(temporary)
        pinned_scratch = scratch / "pinned"
        all_opacity_scratch = scratch / "all"
        pinned_scratch.mkdir()
        all_opacity_scratch.mkdir()
        pinned = SdfScaleSweep.open(
            pinned_path,
            scratch=pinned_scratch,
            pinned_pyramid=True,
        )
        all_opacity = SdfScaleSweep.open(
            all_opacity_path,
            scratch=all_opacity_scratch,
        )
        default = LodSweep.open(default_path)
        for key in (
            "osVersion",
            "architecture",
            "sourceDesign",
            "glassShape",
        ):
            if (
                pinned.manifest.get(key)
                != all_opacity.manifest.get(key)
                or pinned.manifest.get(key)
                != default.manifest.get(key)
            ):
                raise ValueError(
                    "pinned, all-opacity and default metadata "
                    f"differ: {key}"
                )

        control_pinned_vs_default = difference_metrics(
            pinned.control,
            default.control,
        )
        control_pinned_vs_all = difference_metrics(
            pinned.control,
            all_opacity.control,
        )
        curve_difference = _curve_difference(
            pinned.identity,
            all_opacity.identity,
        )

        catalog_words = _catalog_words(
            pinned.identity,
            scratch / "pinned-catalog.u64",
        )
        probe_words = _state_signature_words(
            default.identity,
            DEFAULT_RADIUS_FOUR_STATE,
        )
        bounds = exact_catalog_candidates(
            probe_words[np.newaxis, ...],
            catalog_words,
        )
        candidate_summary = _candidate_summary(bounds)
        predictions = {
            name: _normalized_circle_prediction(
                pinned.manifest["sourceDesign"],
                pinned.manifest["glassShape"],
                offset=offset,
            )
            for name, offset in (
                ("offset_minus_half", -0.5),
                ("offset_zero", 0.0),
                ("offset_plus_half", 0.5),
            )
        }
        prediction_reports = {
            name: _prediction_metrics(
                prediction,
                probe_words,
                catalog_words,
            )
            for name, prediction in predictions.items()
        }
        map_record = (
            _write_maps(
                map_output,
                bounds,
                predictions,
            )
            if map_output is not None
            else None
        )
        radial_fit = _unique_radial_fit(
            bounds,
            pinned.manifest["sourceDesign"],
        )
        pinned_record = {
            "path": str(pinned_path),
            "sha256": (
                sha256_file(pinned_path)
                if pinned_path.is_file()
                else None
            ),
            "ciCommit": pinned.manifest["ciCommit"],
            "memberSha256": pinned.member_hashes,
        }
        all_record = {
            "path": str(all_opacity_path),
            "sha256": (
                sha256_file(all_opacity_path)
                if all_opacity_path.is_file()
                else None
            ),
            "ciCommit": all_opacity.manifest["ciCommit"],
            "memberSha256": all_opacity.member_hashes,
        }
        default_record = {
            "path": str(default_path),
            "sha256": (
                sha256_file(default_path)
                if default_path.is_file()
                else None
            ),
            "ciCommit": default.manifest["ciCommit"],
        }

    production_exact = bool(
        curve_difference["states"][-1]["exact"]
    )
    upstream_intervention_observed = any(
        not record["exact"]
        for record in curve_difference["states"][:-1]
    )
    return {
        "liquidGlassPinnedSdfScaleAnalysisSchemaVersion": 1,
        "analysisImplementation": {
            "file":
                "analysis/liquid_glass_pinned_sdf_scale.py",
            "sha256": sha256_file(Path(__file__).resolve()),
            "python": platform.python_version(),
            "numpy": np.__version__,
        },
        "sources": {
            "pinnedProfile": pinned_record,
            "allOpacityProfile": all_record,
            "defaultProfile": default_record,
            "osVersion": default.manifest["osVersion"],
            "architecture": default.manifest["architecture"],
            "sourceAndGeometryExact": True,
        },
        "controls": {
            "pinnedVsDefaultSource":
                control_pinned_vs_default,
            "pinnedVsAllOpacitySource":
                control_pinned_vs_all,
        },
        "profileIntervention": {
            "comparison":
                "The active interior endpoints have the same scale. "
                "Only dormant opacity endpoints two through four and "
                "explicit default distances differ.",
            "sameScaleCurveDifference": curve_difference,
            "productionScaleExact": production_exact,
            "upstreamProfileInterventionObserved":
                upstream_intervention_observed,
        },
        "exactPinnedCatalogMatching": {
            "equality": (
                "All five native RGB8 amplitude responses are packed "
                "losslessly into two uint64 words. Matching is direct "
                "15-byte equality, with no hash, fit, or tolerance."
            ),
            "scaleStates": STATE_COUNT,
            "halfBitsRangeInclusive": [
                f"{SCALE_HALF_BITS_MINIMUM:04x}",
                f"{SCALE_HALF_BITS_MAXIMUM:04x}",
            ],
            "summary": candidate_summary,
            "candidateMaps": map_record,
        },
        "analyticSdfTests": {
            "profileArithmetic": (
                "Apple AIR float32 interval FMA, saturation and "
                "binary16 conversion, followed by ordered binary16 "
                "opacity arithmetic"
            ),
            "normalizedCircle": prediction_reports,
            "uniqueStateRadialFit": radial_fit,
        },
        "conclusion": {
            "captureControlsExact": (
                control_pinned_vs_default["exact"]
                and control_pinned_vs_all["exact"]
            ),
            "dormantEndpointPinningChangesSourcePath":
                upstream_intervention_observed,
            "defaultRadiusFourExplainedByPinnedCatalog":
                candidate_summary["allSignaturesMatched"],
            "normalizedCircleAirModelExact": any(
                report["allSignaturesExact"]
                for report in prediction_reports.values()
            ),
            "productionShaderAuthorized": False,
            "requiredGate":
                "zero unequal channels on protected Apple captures",
        },
        "resourceMeasurements": {
            "analysisSeconds": time.perf_counter() - started,
            "maximumResidentSetKiB":
                resource.getrusage(
                    resource.RUSAGE_SELF
                ).ru_maxrss,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Compare Apple's pinned-pyramid SDF sweep with the "
            "all-opacity response catalog and default profile."
        )
    )
    parser.add_argument("pinned_sdf_scale_sweep", type=Path)
    parser.add_argument("all_opacity_sdf_scale_sweep", type=Path)
    parser.add_argument("default_lod_sweep", type=Path)
    parser.add_argument("--map-output", type=Path)
    parser.add_argument("-o", "--output", type=Path)
    arguments = parser.parse_args()
    result = analyze(
        arguments.pinned_sdf_scale_sweep,
        arguments.all_opacity_sdf_scale_sweep,
        arguments.default_lod_sweep,
        map_output=arguments.map_output,
    )
    encoded = json.dumps(
        result,
        indent=2,
        sort_keys=True,
        allow_nan=False,
    ) + "\n"
    if arguments.output:
        arguments.output.write_text(encoded, encoding="utf-8")
        print(arguments.output)
    else:
        print(encoded, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
