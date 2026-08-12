#!/usr/bin/env python3
"""Locate the first regular-material divergence on opened source fields."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from apple_glass_reference_renderer import AppleGlassReferenceRenderer
from liquid_glass_exact_specialization_gate import default_fixtures
from liquid_glass_glsl_end_to_end_gate import (
    configure_recovered_material,
    unpack_stage_trace,
)


type JsonObject = dict[str, Any]
type HalfTrace = NDArray[np.uint16]
type CodeImage = NDArray[np.uint8]

CAPTURE_WIDTH = 1024
CAPTURE_HEIGHT = 1024
PROSPECTIVE_PATTERNS = (
    "prospective-opaque-seeded-v1",
    "prospective-premultiplied-seeded-v1",
)


def load_half_trace(path: Path) -> HalfTrace:
    values = np.fromfile(path, dtype="<u2")
    expected = CAPTURE_WIDTH * CAPTURE_HEIGHT * 4
    if values.size != expected:
        raise ValueError(
            f"{path} has {values.size} half values; expected {expected}"
        )
    return values.reshape(CAPTURE_HEIGHT, CAPTURE_WIDTH, 4)


def load_bgra(path: Path) -> CodeImage:
    values = np.fromfile(path, dtype=np.uint8)
    expected = CAPTURE_WIDTH * CAPTURE_HEIGHT * 4
    if values.size != expected:
        raise ValueError(
            f"{path} has {values.size} bytes; expected {expected}"
        )
    bgra = values.reshape(CAPTURE_HEIGHT, CAPTURE_WIDTH, 4)
    return np.ascontiguousarray(bgra[..., [2, 1, 0, 3]])


def compare_values(
    reference: NDArray[np.unsignedinteger[Any]],
    candidate: NDArray[np.unsignedinteger[Any]],
) -> JsonObject:
    if reference.shape != candidate.shape:
        raise ValueError(
            f"comparison shapes differ: {reference.shape} != {candidate.shape}"
        )
    difference = candidate.astype(np.int64) - reference.astype(np.int64)
    changed = difference != 0
    changed_pixels = np.any(changed, axis=2)
    locations = np.argwhere(changed_pixels)
    first = None
    if locations.size:
        y, x = (int(value) for value in locations[0])
        first = {
            "x": x,
            "y": y,
            "reference": [int(value) for value in reference[y, x]],
            "candidate": [int(value) for value in candidate[y, x]],
        }
    return {
        "exact": not bool(np.any(changed)),
        "observedValues": int(changed.size),
        "mismatchedValues": int(np.count_nonzero(changed)),
        "mismatchedPixels": int(np.count_nonzero(changed_pixels)),
        "mismatchedChannels": [
            int(np.count_nonzero(changed[..., channel]))
            for channel in range(reference.shape[2])
        ],
        "maximumEncodingDistance": int(
            np.abs(difference).max(initial=0)
        ),
        "firstMismatch": first,
    }


def trace_path(capture: Path, pattern: str, trace: str) -> Path:
    return capture / (
        "carenderer-live-tree-glass-source-"
        f"{pattern}-{trace}-numeric-trace-rgba16f.raw"
    )


def source_mips(
    capture: Path,
    record: JsonObject,
) -> dict[int, bytes]:
    result: dict[int, bytes] = {}
    for level in record.get("construction", {}).get("levels", []):
        index = level.get("level")
        filename = level.get("rawFile")
        if not isinstance(index, int) or not isinstance(filename, str):
            raise ValueError("source mip metadata is incomplete")
        path = capture / filename
        raw = path.read_bytes()
        if len(raw) != level.get("rawBytes"):
            raise ValueError(f"source mip byte count differs: {path}")
        result[index] = raw
    if not result:
        raise ValueError("source mip inventory is empty")
    return result


def profile_fixture(capture: Path, runtime: JsonObject):
    profile = runtime.get("materialProfileEvidence", {})
    name = f"{profile.get('material')}-{profile.get('requestedAppearance')}"
    fixtures = {fixture.name: fixture for fixture in default_fixtures()}
    try:
        return fixtures[name]
    except KeyError as error:
        raise ValueError(f"unsupported capture profile: {name}") from error


def analyze_pattern(
    capture: Path,
    record: JsonObject,
    *,
    intrinsic_table: Path,
    coefficient_table: Path,
    source_slope_bits: int,
    device_index: int | None,
) -> JsonObject:
    pattern = record.get("name")
    if pattern not in PROSPECTIVE_PATTERNS:
        raise ValueError(f"unsupported calibration pattern: {pattern}")
    context_arguments: dict[str, object] = {}
    if device_index is not None:
        context_arguments["device_index"] = device_index

    stages: list[JsonObject] = []
    with AppleGlassReferenceRenderer(
        capture,
        intrinsic_table=intrinsic_table,
        interpolant_coefficient_table=coefficient_table,
        interpolant_source_slope_bits=source_slope_bits,
        load_interpolant_trace=False,
        load_interpolant_axis_trace=False,
        load_diagnostic_traces=False,
        source_mip_bgra_overrides=source_mips(capture, record),
        context_arguments=context_arguments,
    ) as renderer:
        configure_recovered_material(renderer)
        renderer.program["CoordinateMode"].value = 5

        stage_a_path = capture / (
            "carenderer-live-tree-glass-source-"
            f"{pattern}-color-stages-a-numeric-trace-rgba32ui.raw"
        )
        stage_b_path = capture / (
            "carenderer-live-tree-glass-source-"
            f"{pattern}-color-stages-b-numeric-trace-rgba32ui.raw"
        )
        references = {
            "primary-sample": load_half_trace(
                trace_path(capture, pattern, "sample")
            ),
            "source-color": unpack_stage_trace(
                stage_a_path, upper_pair=False
            ),
            "face": unpack_stage_trace(stage_a_path, upper_pair=True),
            "edge-sample": load_half_trace(
                trace_path(capture, pattern, "edge-sample")
            ),
            "post-edge-bleed": load_half_trace(
                trace_path(capture, pattern, "bleed")
            ),
            "shadow-sample": load_half_trace(
                trace_path(capture, pattern, "shadow-sample")
            ),
            "shadow-layer": load_half_trace(
                trace_path(capture, pattern, "shadow-layer")
            ),
            "pre-holding-composite": unpack_stage_trace(
                stage_b_path, upper_pair=False
            ),
            "post-holding": unpack_stage_trace(
                stage_b_path, upper_pair=True
            ),
            "final-pre-blend": load_half_trace(
                trace_path(capture, pattern, "final-color")
            ),
        }
        trace_numbers = {
            "primary-sample": (4, False),
            "source-color": (10, False),
            "face": (11, False),
            "edge-sample": (19, False),
            "post-edge-bleed": (14, False),
            "shadow-sample": (25, True),
            "shadow-layer": (23, True),
            "pre-holding-composite": (12, False),
            "post-holding": (13, False),
            "final-pre-blend": (9, False),
        }
        for name, (trace_number, include_shadow_draw) in trace_numbers.items():
            comparison = compare_values(
                references[name],
                renderer.render_numeric_trace(
                    trace_number,
                    include_shadow_draw=include_shadow_draw,
                ),
            )
            stages.append({"name": name, **comparison})

        final_reference = load_bgra(
            capture
            / (
                "carenderer-live-tree-source-"
                f"{pattern}-apple-bgra8.raw"
            )
        )
        stages.append({
            "name": "final-bgra8",
            **compare_values(final_reference, renderer.render()),
        })
        implementation = renderer.implementation

    first_divergent = next(
        (stage["name"] for stage in stages if not stage["exact"]),
        None,
    )
    return {
        "pattern": pattern,
        "implementation": implementation,
        "stages": stages,
        "firstDivergentStage": first_divergent,
        "allStagesExact": first_divergent is None,
    }


def analyze(
    capture: Path,
    *,
    intrinsic_table: Path,
    device_index: int | None,
) -> JsonObject:
    runtime_path = capture / "runtime.json"
    runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
    fixture = profile_fixture(capture, runtime)
    source_differential = runtime["carendererEvidence"]["exactPassReplay"][
        "independentGlassReplay"
    ]["sourceTextureDifferential"]
    if source_differential.get("schemaVersion") != 3:
        raise ValueError("source differential schema is not 3")
    records = {
        record.get("name"): record
        for record in source_differential.get("records", [])
        if isinstance(record, dict)
    }
    if not set(PROSPECTIVE_PATTERNS) <= records.keys():
        raise ValueError("prospective calibration records are incomplete")
    patterns = [
        analyze_pattern(
            capture,
            records[pattern],
            intrinsic_table=intrinsic_table,
            coefficient_table=fixture.coefficient_table,
            source_slope_bits=fixture.source_slope_bits,
            device_index=device_index,
        )
        for pattern in PROSPECTIVE_PATTERNS
    ]
    return {
        "liquidGlassRegularSourceCalibrationSchemaVersion": 1,
        "capture": str(capture),
        "profile": fixture.name,
        "patterns": patterns,
        "firstDivergentStages": {
            result["pattern"]: result["firstDivergentStage"]
            for result in patterns
        },
        "exact": all(bool(result["allStagesExact"]) for result in patterns),
        "role": "opened-holdout-calibration-not-prospective-validation",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture", type=Path)
    parser.add_argument(
        "--intrinsic-table",
        type=Path,
        default=Path("artifacts/apple-float-intrinsics-r8-30556057571.bin"),
    )
    parser.add_argument("--device-index", type=int)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    report = analyze(
        arguments.capture,
        intrinsic_table=arguments.intrinsic_table,
        device_index=arguments.device_index,
    )
    encoded = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if arguments.output is None:
        print(encoded, end="")
    else:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(encoded, encoding="utf-8")
        print(arguments.output)
    return 0 if report["exact"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
