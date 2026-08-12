#!/usr/bin/env python3
"""Recover Apple clear Liquid Glass source-allocation geometry."""

import argparse
import json
import platform
import statistics
from pathlib import Path
from typing import Any

from liquid_glass_geometry_policy import (
    MAIN_VERTEX_COUNT,
    SOURCE_ORIGIN_TOLERANCE,
    Vertex,
    align_down,
    align_up,
    bounds,
    glass_vertex_snapshots,
    recover_source_origin,
    report_parts,
    report_path,
    sha256_bytes,
    sha256_file,
    source_texture,
    vertex_float_component_sha256,
    vertices,
)


type JsonObject = dict[str, Any]

CLEAR_FRAGMENT = "glass_background_sdf_no_bleed_lph"
VIRTUAL_EXTENT_TOLERANCE = 1.0 / 1024.0
ALIGNMENT_CANDIDATES = (256, 128, 64, 32, 16, 8, 4, 2, 1)
CLEAR_ORIGIN_ALIGNMENT = 8
CLEAR_ORIGIN_GUARD = 2
CLEAR_EXTENT_ALIGNMENT = 128
CLEAR_EXTENT_PADDING = 16
CLEAR_WINDOW_PADDING = 128
CLEAR_DOWNSAMPLE_FACTOR = 2
CLEAR_MIPMAP_LEVEL_COUNT = 2


def material_profile(report: JsonObject) -> JsonObject:
    value = (
        report.get("materialProfile")
        if report.get("probe") == "compact-geometry-policy"
        else report.get("materialProfileEvidence")
    )
    if not isinstance(value, dict):
        raise ValueError("material profile is missing")
    return value


def inferred_virtual_axis(
    vertex_values: list[Vertex],
    *,
    position_offset: int,
    uv_offset: int,
) -> tuple[int, float]:
    candidates = []
    for left_index, left in enumerate(vertex_values):
        for right in vertex_values[left_index + 1:]:
            uv_delta = right[uv_offset] - left[uv_offset]
            position_delta = (
                right[position_offset] - left[position_offset]
            )
            if abs(uv_delta) <= 1.0e-12:
                continue
            candidates.append(abs(position_delta / uv_delta))
    if not candidates:
        raise ValueError("source UVs expose no nonzero axis slope")
    estimate = statistics.median(candidates)
    rounded = round(estimate)
    residual = max(
        abs(candidate - rounded)
        for candidate in candidates
    )
    if residual > VIRTUAL_EXTENT_TOLERANCE:
        raise ValueError(
            f"virtual source extent residual {residual} exceeds tolerance"
        )
    return rounded, residual


def source_alignment(value: int) -> int:
    return next(
        alignment
        for alignment in ALIGNMENT_CANDIDATES
        if value % alignment == 0
    )


def clear_crop_from_geometry(capture: JsonObject) -> JsonObject:
    geometry = capture["geometry"]
    main = capture["mainBounds"]
    result: JsonObject = {}
    for axis, dimension_name, extent_name, window_name in (
        ("X", "width", "Width", "windowWidth"),
        ("Y", "height", "Height", "windowHeight"),
    ):
        window_extent = int(geometry[window_name])
        virtual_extent = min(
            window_extent + CLEAR_WINDOW_PADDING,
            align_up(
                float(geometry[dimension_name])
                + CLEAR_EXTENT_PADDING,
                CLEAR_EXTENT_ALIGNMENT,
            ),
        )
        origin = align_down(
            max(0.0, float(main[f"minimum{axis}"]))
            - CLEAR_ORIGIN_GUARD,
            CLEAR_ORIGIN_ALIGNMENT,
        )
        result[f"origin{axis}"] = origin
        result[f"virtual{extent_name}"] = virtual_extent
    return result


def analyze_capture(path: Path) -> JsonObject:
    selected_path = report_path(path)
    report = json.loads(selected_path.read_text(encoding="utf-8"))
    geometry, render, capture_kind = report_parts(report)
    profile = material_profile(report)
    if profile.get("material") != "clear":
        raise ValueError(f"{path} is not a clear-material capture")

    vertex_snapshots = glass_vertex_snapshots(render)
    fragment = (
        vertex_snapshots[0]
        .get("pipeline", {})
        .get("creationDescriptor", {})
        .get("fragmentFunction")
    )
    if fragment != CLEAR_FRAGMENT:
        raise ValueError(f"unexpected clear fragment {fragment!r}")
    main = vertices(vertex_snapshots[0], MAIN_VERTEX_COUNT)
    main_bounds = bounds(main, 0)
    virtual_width, width_residual = inferred_virtual_axis(
        main,
        position_offset=0,
        uv_offset=6,
    )
    virtual_height, height_residual = inferred_virtual_axis(
        main,
        position_offset=1,
        uv_offset=7,
    )
    source = source_texture(render)
    texture_width = int(source["width"])
    texture_height = int(source["height"])
    factor_x_value = virtual_width / texture_width
    factor_y_value = virtual_height / texture_height
    factor_x = round(factor_x_value)
    factor_y = round(factor_y_value)
    factor_residual = max(
        abs(factor_x_value - factor_x),
        abs(factor_y_value - factor_y),
    )
    origin_x, origin_y, origin_residual = recover_source_origin(
        main,
        virtual_width=virtual_width,
        virtual_height=virtual_height,
    )

    window_height = int(geometry["windowHeight"])
    observed_frame_origin_x = round(float(main_bounds["minimumX"]))
    observed_frame_origin_y = round(
        window_height - float(main_bounds["maximumY"])
    )
    width = float(geometry["width"])
    height = float(geometry["height"])
    observed_center = [
        observed_frame_origin_x + width / 2.0,
        observed_frame_origin_y + height / 2.0,
    ]
    live_components = b"".join(
        value.hex().encode()
        for vertex in main
        for value in vertex
    )
    return {
        "artifact": str(path),
        "report": str(selected_path),
        "reportSha256": sha256_file(selected_path),
        "captureKind": capture_kind,
        "materialProfile": profile,
        "geometry": geometry,
        "fragment": fragment,
        "mainBounds": main_bounds,
        "observedFrameOrigin": [
            observed_frame_origin_x,
            observed_frame_origin_y,
        ],
        "observedCenter": observed_center,
        "mainVertexFloatComponentSha256":
            vertex_float_component_sha256(
                vertex_snapshots[0],
                MAIN_VERTEX_COUNT,
            ),
        "mainVertexValueSha256": sha256_bytes(live_components),
        "sourceCrop": {
            "originX": origin_x,
            "originY": origin_y,
            "virtualWidth": virtual_width,
            "virtualHeight": virtual_height,
            "textureWidth": texture_width,
            "textureHeight": texture_height,
            "mipmapLevelCount": int(source["mipmapLevelCount"]),
            "downsampleFactor": [factor_x, factor_y],
            "maximumFactorIntegralResidual": factor_residual,
            "maximumVirtualExtentResidual": max(
                width_residual,
                height_residual,
            ),
            "maximumOriginRecoveryResidual": origin_residual,
            "originAlignment": [
                source_alignment(origin_x),
                source_alignment(origin_y),
            ],
            "extentAlignment": [
                source_alignment(virtual_width),
                source_alignment(virtual_height),
            ],
        },
    }


def control_signature(capture: JsonObject) -> JsonObject:
    crop = capture["sourceCrop"]
    return {
        "fragment": capture["fragment"],
        "observedFrameOrigin": capture["observedFrameOrigin"],
        "mainVertexFloatComponentSha256":
            capture["mainVertexFloatComponentSha256"],
        "sourceCrop": {
            name: crop[name]
            for name in (
                "originX",
                "originY",
                "virtualWidth",
                "virtualHeight",
                "textureWidth",
                "textureHeight",
                "mipmapLevelCount",
                "downsampleFactor",
            )
        },
    }


def duplicate_controls(
    captures: list[JsonObject],
) -> list[JsonObject]:
    by_name: dict[str, list[JsonObject]] = {}
    for capture in captures:
        name = str(capture["geometry"]["name"])
        by_name.setdefault(name, []).append(capture)
    result = []
    for name, group in sorted(by_name.items()):
        kinds = {str(capture["captureKind"]) for capture in group}
        if not {"compact-policy", "full-introspection"} <= kinds:
            continue
        signatures = [
            control_signature(capture)
            for capture in group
        ]
        result.append({
            "geometry": name,
            "exact": all(
                signature == signatures[0]
                for signature in signatures[1:]
            ),
            "captureCount": len(group),
        })
    return result


def analyze(paths: list[Path]) -> JsonObject:
    captures = [analyze_capture(path) for path in paths]
    centered_by_diameter: dict[int, JsonObject] = {}
    for capture in captures:
        geometry = capture["geometry"]
        if (
            float(geometry["centerX"]) != 512.0
            or float(geometry["centerY"]) != 512.0
            or float(geometry["width"]) != float(geometry["height"])
        ):
            continue
        sample = {
            "diameter": int(geometry["width"]),
            **{
                name: capture["sourceCrop"][name]
                for name in (
                    "originX",
                    "originY",
                    "virtualWidth",
                    "virtualHeight",
                    "textureWidth",
                    "textureHeight",
                    "mipmapLevelCount",
                    "downsampleFactor",
                )
            },
        }
        diameter = int(sample["diameter"])
        previous = centered_by_diameter.get(diameter)
        if previous is not None and previous != sample:
            raise ValueError(
                f"diameter {diameter} has conflicting clear policies"
            )
        centered_by_diameter[diameter] = sample
    centered_samples = [
        centered_by_diameter[diameter]
        for diameter in sorted(centered_by_diameter)
    ]
    controls = duplicate_controls(captures)
    factors_integral = all(
        capture["sourceCrop"]["maximumFactorIntegralResidual"]
        <= VIRTUAL_EXTENT_TOLERANCE
        for capture in captures
    )
    extents_integral = all(
        capture["sourceCrop"]["maximumVirtualExtentResidual"]
        <= VIRTUAL_EXTENT_TOLERANCE
        for capture in captures
    )
    origins_exact = all(
        capture["sourceCrop"]["maximumOriginRecoveryResidual"]
        <= SOURCE_ORIGIN_TOLERANCE
        for capture in captures
    )
    allocation_model_exact = all(
        clear_crop_from_geometry(capture) == {
            name: capture["sourceCrop"][name]
            for name in (
                "originX",
                "virtualWidth",
                "originY",
                "virtualHeight",
            )
        }
        for capture in captures
    )
    source_topology_exact = all(
        capture["sourceCrop"]["downsampleFactor"]
        == [
            CLEAR_DOWNSAMPLE_FACTOR,
            CLEAR_DOWNSAMPLE_FACTOR,
        ]
        and capture["sourceCrop"]["mipmapLevelCount"]
        == CLEAR_MIPMAP_LEVEL_COUNT
        for capture in captures
    )
    controls_exact = all(
        control["exact"]
        for control in controls
    )
    selection_law_fully_determined = (
        allocation_model_exact
        and source_topology_exact
        and controls_exact
        and bool(controls)
    )
    return {
        "liquidGlassClearGeometryPolicyAnalysisSchemaVersion": 1,
        "implementation": {
            "file": "analysis/liquid_glass_clear_geometry_policy.py",
            "python": platform.python_version(),
        },
        "captures": captures,
        "centeredSizeSamples": centered_samples,
        "recoveredSourceAllocation": {
            "origin":
                "align max(0, main minimum) minus 2 down to 8",
            "virtualExtent":
                "min(window extent + 128, align dimension + 16 up to 128)",
            "downsampleFactor": CLEAR_DOWNSAMPLE_FACTOR,
            "mipmapLevelCount": CLEAR_MIPMAP_LEVEL_COUNT,
            "exactForEveryCapture": allocation_model_exact,
            "sourceTopologyExactForEveryCapture":
                source_topology_exact,
        },
        "duplicateFullCaptureControls": {
            "comparisons": controls,
            "comparisonCount": len(controls),
            "allExact": all(
                control["exact"]
                for control in controls
            ),
        },
        "conclusion": {
            "captureCount": len(captures),
            "virtualExtentsIntegral": extents_integral,
            "sourceFactorsIntegral": factors_integral,
            "sourceOriginsRecoveredWithinTolerance": origins_exact,
            "selectionLawFullyDetermined":
                selection_law_fully_determined,
            "productionShaderAuthorized": False,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifacts", nargs="+", type=Path)
    parser.add_argument("-o", "--output", type=Path)
    arguments = parser.parse_args()
    report = analyze(arguments.artifacts)
    encoded = json.dumps(
        report,
        indent=2,
        sort_keys=True,
        allow_nan=False,
    ) + "\n"
    if arguments.output is None:
        print(encoded, end="")
    else:
        arguments.output.write_text(encoded, encoding="utf-8")
        print(arguments.output)
    conclusion = report["conclusion"]
    return 0 if (
        conclusion["virtualExtentsIntegral"]
        and conclusion["sourceFactorsIntegral"]
        and conclusion["sourceOriginsRecoveredWithinTolerance"]
    ) else 1


if __name__ == "__main__":
    raise SystemExit(main())
