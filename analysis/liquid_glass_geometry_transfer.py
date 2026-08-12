#!/usr/bin/env python3
"""Recover and verify Apple's static Liquid Glass geometry-transfer rules."""

import argparse
import hashlib
import json
import math
import platform
import struct
from pathlib import Path
from typing import Any

import numpy as np

from liquid_glass_profile_matrix import (
    _changed_fields,
    _glass_uniform_snapshots,
    _payload,
    decode_profile,
)


type JsonObject = dict[str, Any]
type Vertex = tuple[float, float, float, float, float, float, float, float]

SOURCE_DOWNSAMPLE = 4
VERTEX_STRIDE = 48
MAIN_VERTEX_COUNT = 6
SHADOW_VERTEX_COUNT = 16
SHADOW_INDEX_COUNT = 48
SHADOW_LEFT_RIGHT = 48.0
SHADOW_TOP = 40.0
SHADOW_BOTTOM = 56.0

EXPECTED_MVP = (
    1.0 / 512.0,
    0.0,
    0.0,
    0.0,
    0.0,
    -1.0 / 512.0,
    0.0,
    0.0,
    0.0,
    0.0,
    0.0,
    0.0,
    -1.0,
    1.0,
    0.0,
    1.0,
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def float32(value: float) -> float:
    return struct.unpack("<f", struct.pack("<f", value))[0]


def float32_bits(value: float) -> str:
    return f"0x{struct.unpack('<I', struct.pack('<f', value))[0]:08x}"


def _source_coordinate(
    position: float,
    *,
    origin: int,
    extent: int,
) -> float:
    # Core Animation first rounds the reciprocal to float32, then performs
    # the multiply. Direct division differs by one ULP at several captured
    # endpoints.
    return float32(
        float32(position - origin) * float32(1.0 / extent)
    )


def half_bits(value: float) -> str:
    return f"0x{struct.unpack('<H', struct.pack('<e', value))[0]:04x}"


def _pipeline_fragment(snapshot: JsonObject) -> str:
    pipeline = snapshot.get("pipeline", {})
    descriptor = (
        pipeline.get("creationDescriptor", {})
        if isinstance(pipeline, dict)
        else {}
    )
    return (
        str(descriptor.get("fragmentFunction", ""))
        if isinstance(descriptor, dict)
        else ""
    )


def _glass_snapshots(
    runtime: JsonObject,
    *,
    stage: str,
    index: int,
) -> list[JsonObject]:
    snapshots = runtime["carendererEvidence"]["metalBufferSnapshots"][
        "snapshots"
    ]
    return sorted(
        (
            snapshot
            for snapshot in snapshots
            if snapshot.get("stage") == stage
            and snapshot.get("index") == index
            and _pipeline_fragment(snapshot).startswith(
                "glass_background_sdf"
            )
        ),
        key=lambda snapshot: snapshot["sequence"],
    )


def _snapshot_bytes(snapshot: JsonObject) -> bytes:
    encoded = snapshot.get("payload", {}).get("hex")
    if not isinstance(encoded, str):
        raise ValueError("buffer snapshot is missing its hexadecimal payload")
    return bytes.fromhex(encoded)


def _vertices(snapshot: JsonObject, count: int) -> list[Vertex]:
    payload = _snapshot_bytes(snapshot)
    required = count * VERTEX_STRIDE
    if len(payload) < required:
        raise ValueError(
            f"vertex snapshot has {len(payload)} bytes; expected {required}"
        )
    return [
        struct.unpack_from("<8f", payload, offset * VERTEX_STRIDE)
        for offset in range(count)
    ]


def _vertex_component_bits(vertices: list[Vertex]) -> list[list[str]]:
    return [
        [float32_bits(component) for component in vertex]
        for vertex in vertices
    ]


def _vertex_hash(vertices: list[Vertex]) -> str:
    encoded = b"".join(struct.pack("<8f", *vertex) for vertex in vertices)
    return hashlib.sha256(encoded).hexdigest()


def _bounds(
    vertices: list[Vertex],
    first_component: int,
) -> JsonObject:
    x_values = [vertex[first_component] for vertex in vertices]
    y_values = [vertex[first_component + 1] for vertex in vertices]
    return {
        "minimumX": min(x_values),
        "minimumY": min(y_values),
        "maximumX": max(x_values),
        "maximumY": max(y_values),
    }


def _source_texture(runtime: JsonObject) -> JsonObject:
    snapshots = runtime["carendererEvidence"]["metalTextureSnapshots"][
        "snapshots"
    ]
    matches = [
        snapshot
        for snapshot in snapshots
        if snapshot.get("index") == 3
        and _pipeline_fragment(snapshot).startswith("glass_background_sdf")
    ]
    if len(matches) != 1:
        raise ValueError(
            "captured glass pass must have one source texture at index 3; "
            f"found {len(matches)}"
        )
    return matches[0]


def _source_origin(
    vertices: list[Vertex],
    *,
    virtual_width: int,
    virtual_height: int,
) -> tuple[int, int, float]:
    candidates_x = [
        vertex[0] - vertex[6] * virtual_width
        for vertex in vertices
    ]
    candidates_y = [
        vertex[1] - vertex[7] * virtual_height
        for vertex in vertices
    ]
    origin_x = round(sum(candidates_x) / len(candidates_x))
    origin_y = round(sum(candidates_y) / len(candidates_y))
    residual = max(
        [
            abs(value - origin_x)
            for value in candidates_x
        ]
        + [
            abs(value - origin_y)
            for value in candidates_y
        ]
    )
    return origin_x, origin_y, residual


def _expected_main_vertices(
    geometry: JsonObject,
    *,
    source_origin_x: int,
    source_origin_y: int,
    virtual_width: int,
    virtual_height: int,
) -> tuple[list[Vertex], JsonObject]:
    snapped_center_x = round(float(geometry["centerX"]))
    snapped_swiftui_center_y = round(float(geometry["centerY"]))
    metal_center_y = int(geometry["windowHeight"]) - snapped_swiftui_center_y
    half_width = float(geometry["width"]) / 2.0
    half_height = float(geometry["height"]) / 2.0
    left = snapped_center_x - half_width
    right = snapped_center_x + half_width
    bottom = metal_center_y - half_height
    top = metal_center_y + half_height
    positions = (
        (left, top),
        (right, top),
        (right, bottom),
        (right, bottom),
        (left, bottom),
        (left, top),
    )
    vertices = [
        (
            float32(x),
            float32(y),
            0.0,
            1.0,
            float32(x - snapped_center_x),
            float32(metal_center_y - y),
            _source_coordinate(
                x,
                origin=source_origin_x,
                extent=virtual_width,
            ),
            _source_coordinate(
                y,
                origin=source_origin_y,
                extent=virtual_height,
            ),
        )
        for x, y in positions
    ]
    return vertices, {
        "requestedCenter": [
            geometry["centerX"],
            geometry["centerY"],
        ],
        "snappedSwiftUICenter": [
            snapped_center_x,
            snapped_swiftui_center_y,
        ],
        "metalCenter": [snapped_center_x, metal_center_y],
        "swiftUICenterSnapDelta": [
            snapped_center_x - float(geometry["centerX"]),
            snapped_swiftui_center_y - float(geometry["centerY"]),
        ],
    }


def _expected_shadow_vertices(
    main: list[Vertex],
    *,
    source_origin_x: int,
    source_origin_y: int,
    virtual_width: int,
    virtual_height: int,
) -> list[Vertex]:
    position = _bounds(main, 0)
    center_x = (position["minimumX"] + position["maximumX"]) / 2.0
    center_y = (position["minimumY"] + position["maximumY"]) / 2.0
    x_values = (
        position["minimumX"] - SHADOW_LEFT_RIGHT,
        position["minimumX"],
        position["maximumX"],
        position["maximumX"] + SHADOW_LEFT_RIGHT,
    )
    y_values = (
        position["maximumY"] + SHADOW_TOP,
        position["maximumY"],
        position["minimumY"],
        position["minimumY"] - SHADOW_BOTTOM,
    )
    return [
        (
            float32(x),
            float32(y),
            0.0,
            1.0,
            float32(x - center_x),
            float32(center_y - y),
            _source_coordinate(
                x,
                origin=source_origin_x,
                extent=virtual_width,
            ),
            _source_coordinate(
                y,
                origin=source_origin_y,
                extent=virtual_height,
            ),
        )
        for y in y_values
        for x in x_values
    ]


def _vertices_exact(
    actual: list[Vertex],
    expected: list[Vertex],
) -> bool:
    return _vertex_component_bits(actual) == _vertex_component_bits(expected)


def _mvp(runtime: JsonObject) -> tuple[tuple[float, ...], JsonObject]:
    snapshots = _glass_snapshots(runtime, stage="vertex", index=2)
    if len(snapshots) != 1:
        raise ValueError(
            "captured glass pass must have one MVP buffer; "
            f"found {len(snapshots)}"
        )
    values = struct.unpack_from("<16f", _snapshot_bytes(snapshots[0]))
    return values, snapshots[0]


def _trace_active_region(
    artifact: Path,
    runtime: JsonObject,
) -> JsonObject:
    traces = runtime["carendererEvidence"]["exactPassReplay"][
        "independentGlassReplay"
    ]["numericTraces"]
    trace = next(value for value in traces if value["name"] == "interpolant")
    output = trace["replay"]["output"]
    width = int(output["width"])
    height = int(output["height"])
    path = artifact / output["rawFile"]
    values = np.memmap(
        path,
        dtype="<u4",
        mode="r",
        shape=(height, width, 4),
    )
    active = np.any(values != 0, axis=2)
    y_values, x_values = np.nonzero(active)
    if x_values.size == 0:
        raise ValueError("interpolant trace contains no covered fragments")
    return {
        "path": str(path),
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
        "activeBoundsInclusive": {
            "minimumX": int(x_values.min()),
            "minimumY": int(y_values.min()),
            "maximumX": int(x_values.max()),
            "maximumY": int(y_values.max()),
        },
        "activePixels": int(active.sum()),
    }


def _expected_trace_region(
    shadow: list[Vertex],
    *,
    width: int,
    height: int,
) -> JsonObject:
    bounds = _bounds(shadow, 0)
    minimum_x = max(0, math.floor(bounds["minimumX"]))
    minimum_y = max(0, math.floor(bounds["minimumY"]))
    maximum_x = min(width - 1, math.ceil(bounds["maximumX"]) - 1)
    maximum_y = min(height - 1, math.ceil(bounds["maximumY"]) - 1)
    return {
        "activeBoundsInclusive": {
            "minimumX": minimum_x,
            "minimumY": minimum_y,
            "maximumX": maximum_x,
            "maximumY": maximum_y,
        },
        "activePixels": (
            (maximum_x - minimum_x + 1)
            * (maximum_y - minimum_y + 1)
        ),
    }


def _expected_profile_bits(
    *,
    half_width: float,
    half_height: float,
    virtual_width: int,
    virtual_height: int,
) -> JsonObject:
    return {
        "sdf_arg": [
            float32_bits(half_width),
            float32_bits(half_height),
            float32_bits(4.0),
            float32_bits(0.5),
        ],
        "sdf_arg2": [
            float32_bits(1.0),
            float32_bits(1.0),
            float32_bits(min(half_width, half_height)),
            float32_bits(0.0),
        ],
        "displacement_matrix": [
            float32_bits(1.0 / virtual_width),
            float32_bits(0.0),
            float32_bits(0.0),
            float32_bits(-1.0 / virtual_height),
        ],
        "outer_refraction_amount": [
            float32_bits(min(half_width, half_height) * 0.4),
        ],
        "outer_refraction_inverse_height": [
            float32_bits(4.0 / min(half_width, half_height)),
        ],
        "edge_bleed_amount": [
            float32_bits(min(half_width, half_height) * 0.7),
        ],
        "edge_bleed_inverse_height": [
            float32_bits(1.0 / (min(half_width, half_height) * 0.7)),
        ],
        "shadow_inverse_height": [
            float32_bits(1.0 / (min(half_width, half_height) * 0.8)),
        ],
        "blur_distance": [
            half_bits(-min(half_width, half_height)),
            half_bits(-1.0),
            half_bits(0.0),
            half_bits(0.0),
        ],
    }


def _replay_gate(runtime: JsonObject) -> JsonObject:
    exact = runtime["carendererEvidence"]["exactPassReplay"]
    candidates = {
        value["name"]: value
        for value in exact["independentGlassReplay"]["candidates"]
    }
    profile = candidates["custom_profile_fragment_replay"]["comparison"]
    descriptor = candidates["captured_descriptor_rebuild"]["comparison"]
    return {
        "capturedPassExact": (
            exact["executed"] is True
            and exact["exactByteMatch"] is True
            and exact["mismatchedByteCount"] == 0
            and exact["maximumChannelDelta"] == 0
        ),
        "capturedDescriptorRebuildExact": (
            descriptor["exactByteMatch"] is True
            and descriptor["mismatchedByteCount"] == 0
            and descriptor["maximumChannelDelta"] == 0
        ),
        "independentProfileReplayExact": (
            profile["exactByteMatch"] is True
            and profile["mismatchedByteCount"] == 0
            and profile["maximumChannelDelta"] == 0
        ),
        "exactPassReplayFNV1a64": exact["replayOutput"]["fnv1a64"],
    }


def analyze_artifact(
    artifact: Path,
    *,
    baseline_profile: JsonObject | None = None,
) -> JsonObject:
    runtime_path = artifact / "runtime.json"
    runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
    geometry = runtime["geometryEvidence"]
    main_snapshots = _glass_snapshots(runtime, stage="vertex", index=1)
    if len(main_snapshots) != 2:
        raise ValueError(
            "captured glass pass must have main and shadow vertex buffers; "
            f"found {len(main_snapshots)}"
        )
    index_snapshots = _glass_snapshots(runtime, stage="index", index=-1)
    if len(index_snapshots) != 1:
        raise ValueError(
            "captured glass pass must have one shadow index buffer; "
            f"found {len(index_snapshots)}"
        )
    main = _vertices(main_snapshots[0], MAIN_VERTEX_COUNT)
    shadow = _vertices(main_snapshots[1], SHADOW_VERTEX_COUNT)
    source = _source_texture(runtime)
    virtual_width = int(source["width"]) * SOURCE_DOWNSAMPLE
    virtual_height = int(source["height"]) * SOURCE_DOWNSAMPLE
    origin_x, origin_y, origin_residual = _source_origin(
        main,
        virtual_width=virtual_width,
        virtual_height=virtual_height,
    )
    expected_main, snapping = _expected_main_vertices(
        geometry,
        source_origin_x=origin_x,
        source_origin_y=origin_y,
        virtual_width=virtual_width,
        virtual_height=virtual_height,
    )
    expected_shadow = _expected_shadow_vertices(
        expected_main,
        source_origin_x=origin_x,
        source_origin_y=origin_y,
        virtual_width=virtual_width,
        virtual_height=virtual_height,
    )
    mvp, mvp_snapshot = _mvp(runtime)
    _, uniform_snapshots = _glass_uniform_snapshots(runtime)
    uniform_snapshots.sort(key=lambda value: value["sequence"])
    if len(uniform_snapshots) != 2:
        raise ValueError(
            "captured glass pass must have main and shadow uniforms; "
            f"found {len(uniform_snapshots)}"
        )
    profiles = [
        decode_profile(_payload(snapshot))
        for snapshot in uniform_snapshots
    ]
    expected_profile_bits = _expected_profile_bits(
        half_width=float(geometry["width"]) / 2.0,
        half_height=float(geometry["height"]) / 2.0,
        virtual_width=virtual_width,
        virtual_height=virtual_height,
    )
    profile_formula_checks = {
        name: profiles[0]["fields"][name]["bits"] == expected
        for name, expected in expected_profile_bits.items()
    }
    trace = _trace_active_region(artifact, runtime)
    expected_trace = _expected_trace_region(
        expected_shadow,
        width=int(geometry["windowWidth"]),
        height=int(geometry["windowHeight"]),
    )
    indices = _snapshot_bytes(index_snapshots[0])[
        : SHADOW_INDEX_COUNT * 2
    ]
    replay = _replay_gate(runtime)
    changed_fields = (
        _changed_fields(baseline_profile, profiles[0])
        if baseline_profile is not None
        else []
    )
    geometry_checks = {
        "mainVerticesExact": _vertices_exact(main, expected_main),
        "shadowVerticesExact": _vertices_exact(shadow, expected_shadow),
        "sourceOriginIntegral": origin_residual <= 1.0e-4,
        "sourceOrigin256Aligned": (
            origin_x % 256 == 0 and origin_y % 256 == 0
        ),
        "sourceExtent256Aligned": (
            virtual_width % 256 == 0 and virtual_height % 256 == 0
        ),
        "mvpExact": (
            [float32_bits(value) for value in mvp]
            == [float32_bits(value) for value in EXPECTED_MVP]
        ),
        "mainAndShadowProfileExact": (
            profiles[0]["glassHex"] == profiles[1]["glassHex"]
        ),
        "profileScalingBitsExact": all(profile_formula_checks.values()),
        "traceCoverageExact": (
            trace["activeBoundsInclusive"]
            == expected_trace["activeBoundsInclusive"]
            and trace["activePixels"] == expected_trace["activePixels"]
        ),
        "capturedPassExact": replay["capturedPassExact"],
        "capturedDescriptorRebuildExact":
            replay["capturedDescriptorRebuildExact"],
        "independentProfileReplayExact":
            replay["independentProfileReplayExact"],
    }
    return {
        "artifact": str(artifact),
        "runtimeSchemaVersion": runtime["schemaVersion"],
        "runtimeJsonSha256": sha256_file(runtime_path),
        "materialProfileEvidence": runtime["materialProfileEvidence"],
        "requestedGeometry": geometry,
        "snapping": snapping,
        "mainGeometry": {
            "sequence": main_snapshots[0]["sequence"],
            "vertexCount": len(main),
            "positionBounds": _bounds(main, 0),
            "sdfBounds": _bounds(main, 4),
            "sourceUVBounds": _bounds(main, 6),
            "componentSha256": _vertex_hash(main),
            "componentBits": _vertex_component_bits(main),
        },
        "shadowGeometry": {
            "sequence": main_snapshots[1]["sequence"],
            "vertexCount": len(shadow),
            "indexCount": SHADOW_INDEX_COUNT,
            "positionBounds": _bounds(shadow, 0),
            "sdfBounds": _bounds(shadow, 4),
            "sourceUVBounds": _bounds(shadow, 6),
            "componentSha256": _vertex_hash(shadow),
            "indexSha256": hashlib.sha256(indices).hexdigest(),
        },
        "sourceTexture": {
            "width": source["width"],
            "height": source["height"],
            "virtualWidth": virtual_width,
            "virtualHeight": virtual_height,
            "downsampleFactor": SOURCE_DOWNSAMPLE,
            "originX": origin_x,
            "originY": origin_y,
            "maximumOriginRecoveryResidual": origin_residual,
            "mips": source["mipSnapshots"],
        },
        "mvp": {
            "sequence": mvp_snapshot["sequence"],
            "bits": [float32_bits(value) for value in mvp],
        },
        "profile": {
            "sha256": profiles[0]["glassSha256"],
            "changedFieldsFromBaseline": changed_fields,
            "formulaChecks": profile_formula_checks,
            "expectedFormulaBits": expected_profile_bits,
        },
        "interpolantTrace": {
            **trace,
            "expected": expected_trace,
        },
        "nativeReplayGate": replay,
        "checks": geometry_checks,
    }


def analyze(
    artifacts: list[Path],
    *,
    baseline: Path,
) -> JsonObject:
    baseline_runtime = json.loads(
        (baseline / "runtime.json").read_text(encoding="utf-8")
    )
    _, baseline_snapshots = _glass_uniform_snapshots(baseline_runtime)
    baseline_snapshots.sort(key=lambda value: value["sequence"])
    if len(baseline_snapshots) != 2:
        raise ValueError("baseline must contain main and shadow glass draws")
    baseline_profile = decode_profile(_payload(baseline_snapshots[0]))
    captures = [
        analyze_artifact(
            artifact,
            baseline_profile=baseline_profile,
        )
        for artifact in [baseline, *artifacts]
    ]
    all_checks_exact = all(
        all(capture["checks"].values())
        for capture in captures
    )
    changed_field_union = sorted({
        field
        for capture in captures
        for field in capture["profile"]["changedFieldsFromBaseline"]
    })
    expected_changed_fields = {
        "blur_distance",
        "displacement_matrix",
        "edge_bleed_amount",
        "edge_bleed_inverse_height",
        "outer_refraction_amount",
        "outer_refraction_inverse_height",
        "sdf_arg",
        "sdf_arg2",
        "shadow_inverse_height",
    }
    return {
        "liquidGlassGeometryTransferAnalysisSchemaVersion": 1,
        "implementation": {
            "file": "analysis/liquid_glass_geometry_transfer.py",
            "python": platform.python_version(),
            "numpy": np.__version__,
        },
        "baseline": str(baseline),
        "captures": captures,
        "recoveredRules": {
            "swiftUICenter":
                "nearest integer point before the Metal Y inversion",
            "mainMesh":
                "six vertices at snapped center +/- requested half-size",
            "sdfCoordinates":
                "vertex position relative to the snapped center, with Y "
                "inverted",
            "sourceCoordinates":
                "f32(f32(vertex position - captured source origin) * "
                "f32(1 / virtual extent)); virtual extent is 4 * captured "
                "source texture extent",
            "sourceCrop":
                "per-axis origin and extent are aligned to 256 pixels",
            "shadowMesh":
                "4x4 nine-grid; X expands 48 pixels on both sides, Metal "
                "top expands 40, Metal bottom expands 56",
            "profileScaling": {
                "sdfHalfSize": "half-size",
                "outerRefractionAmount": "0.4 * minimum half-size",
                "outerRefractionInverseHeight":
                    "4 / minimum half-size",
                "edgeBleedAmount": "0.7 * minimum half-size",
                "edgeBleedInverseHeight":
                    "1 / (0.7 * minimum half-size)",
                "shadowInverseHeight":
                    "1 / (0.8 * minimum half-size)",
                "blurDistance": "-minimum half-size",
                "displacementDiagonal":
                    "(1 / virtual source width, "
                    "-1 / virtual source height)",
            },
            "mvp":
                "fixed 1024x1024 orthographic transform",
        },
        "conclusion": {
            "captureCount": len(captures),
            "allCapturedRulesBitExact": all_checks_exact,
            "changedFieldUnion": changed_field_union,
            "onlyRecoveredGeometryFieldsChanged": (
                set(changed_field_union) <= expected_changed_fields
            ),
            "nativeReplayExactForEveryGeometry": all(
                capture["nativeReplayGate"]["capturedPassExact"]
                and capture["nativeReplayGate"][
                    "capturedDescriptorRebuildExact"
                ]
                and capture["nativeReplayGate"][
                    "independentProfileReplayExact"
                ]
                for capture in captures
            ),
            "fractionalCenterPolicyFullyDetermined": False,
            "sourceCropSelectionPolicyFullyDetermined": False,
            "productionShaderAuthorized": False,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("artifacts", nargs="+", type=Path)
    parser.add_argument("--baseline", required=True, type=Path)
    parser.add_argument("-o", "--output", type=Path)
    arguments = parser.parse_args()
    report = analyze(arguments.artifacts, baseline=arguments.baseline)
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
        conclusion["allCapturedRulesBitExact"]
        and conclusion["onlyRecoveredGeometryFieldsChanged"]
        and conclusion["nativeReplayExactForEveryGeometry"]
    ) else 1


if __name__ == "__main__":
    raise SystemExit(main())
