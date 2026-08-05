#!/usr/bin/env python3
"""Audit transition-time Apple geometry and moving-backdrop evidence."""

import argparse
import hashlib
import json
import platform
import struct
from pathlib import Path
from typing import Any

import numpy as np

from apple_glass_reference_renderer import DrawGeometry
from liquid_glass_profile_matrix import GLASS_FRAGMENTS, decode_profile
from liquid_glass_transition_geometry import transition_circle_geometry


type JsonObject = dict[str, Any]

REPORT_NAME = "transition-timeline.json"
EXPECTED_SAMPLE_INDICES = (1, 4, 8, 12, 16, 20, 24, 28, 32)
VERTEX_STRIDE = 48
CARRIER_CRITICAL_PATHS = (
    (),
    (0,),
    (1,),
    (1, 0),
    (1, 0, 0),
    (1, 0, 1),
    (1, 0, 1, 0),
    (1, 0, 1, 0, 0),
    (1, 0, 1, 0, 0, 0),
    (1, 0, 1, 0, 0, 0, 0),
    (1, 0, 1, 2),
    (1, 0, 1, 2, 0),
)
TRANSITION_ONLY_FOREGROUND_PATHS = (
    (1, 0, 1, 1),
    (1, 0, 1, 1, 0),
    (1, 0, 1, 1, 0, 0),
    (1, 0, 1, 1, 0, 0, 0),
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _fragment(record: JsonObject) -> str:
    pipeline = record.get("pipeline")
    if not isinstance(pipeline, dict):
        return ""
    descriptor = pipeline.get("creationDescriptor")
    if not isinstance(descriptor, dict):
        return ""
    return str(descriptor.get("fragmentFunction", ""))


def _payload(record: JsonObject) -> bytes:
    payload = record.get("payload")
    encoded = payload.get("hex") if isinstance(payload, dict) else None
    if not isinstance(encoded, str):
        raise ValueError("captured buffer has no hexadecimal payload")
    value = bytes.fromhex(encoded)
    if payload.get("lengthBytes") != len(value):
        raise ValueError("captured buffer payload length differs")
    return value


def _vertices(record: JsonObject, count: int) -> np.ndarray:
    source = _payload(record)
    required = count * VERTEX_STRIDE
    if len(source) < required:
        raise ValueError(
            f"vertex payload has {len(source)} bytes; expected at least {required}"
        )
    values = np.empty((count, 8), dtype=np.float32)
    for index in range(count):
        values[index] = struct.unpack_from("<8f", source, index * VERTEX_STRIDE)
    return values


def _buffer_records(render: JsonObject) -> list[JsonObject]:
    buffers = render.get("metalBufferSnapshots")
    records = buffers.get("snapshots") if isinstance(buffers, dict) else None
    if not isinstance(records, list) or not all(
        isinstance(record, dict) for record in records
    ):
        raise ValueError("render buffer snapshots are incomplete")
    return records


def _background_geometry(
    render: JsonObject, fragment: str
) -> tuple[DrawGeometry, DrawGeometry]:
    records = _buffer_records(render)
    vertex_records = sorted(
        (
            record
            for record in records
            if record.get("stage") == "vertex"
            and record.get("index") == 1
            and _fragment(record) == fragment
        ),
        key=lambda record: int(record["sequence"]),
    )
    index_records = [
        record
        for record in records
        if record.get("stage") == "index"
        and record.get("index") == -1
        and _fragment(record) == fragment
    ]
    if len(vertex_records) != 2 or len(index_records) != 1:
        raise ValueError(
            "background draw must contain main/shadow vertices and one index buffer"
        )
    indices = np.frombuffer(_payload(index_records[0]), dtype="<u2", count=48).copy()
    return (
        DrawGeometry(vertices=_vertices(vertex_records[0], 6), indices=None),
        DrawGeometry(vertices=_vertices(vertex_records[1], 16), indices=indices),
    )


def _background_mvp(render: JsonObject, fragment: str) -> bytes:
    records = [
        record
        for record in _buffer_records(render)
        if record.get("stage") == "vertex"
        and record.get("index") == 2
        and _fragment(record) == fragment
    ]
    if len(records) != 1:
        raise ValueError(f"background draw has {len(records)} MVP buffers")
    payload = _payload(records[0])
    if len(payload) < 64:
        raise ValueError("background MVP payload is incomplete")
    return payload[:64]


def _highlight_geometry(render: JsonObject) -> DrawGeometry:
    records = _buffer_records(render)

    def latest(stage: str, index: int) -> JsonObject:
        matches = [
            record
            for record in records
            if record.get("stage") == stage
            and record.get("index") == index
            and _fragment(record) == "A2Xghfc"
        ]
        if not matches:
            raise ValueError(f"final highlight has no {stage} buffer at {index}")
        return max(matches, key=lambda record: int(record["sequence"]))

    indices = np.frombuffer(
        _payload(latest("index", -1)),
        dtype="<u2",
        count=6,
    ).copy()
    return DrawGeometry(
        vertices=_vertices(latest("vertex", 1), 4),
        indices=indices,
    )


def _uniform_payloads(render: JsonObject, fragment: str) -> tuple[bytes, bytes]:
    records = render.get("glassFragmentUniformBindings")
    if not isinstance(records, list):
        raise ValueError("glass fragment uniform bindings are incomplete")
    background = sorted(
        (
            record
            for record in records
            if isinstance(record, dict) and _fragment(record) == fragment
        ),
        key=lambda record: int(record["sequence"]),
    )
    highlights = [
        record
        for record in records
        if isinstance(record, dict) and _fragment(record) == "A2Xghfc"
    ]
    if len(background) != 2 or not highlights:
        raise ValueError("background or final-highlight uniforms are incomplete")
    main = _payload(background[0])
    shadow = _payload(background[1])
    if main[48:258] != shadow[48:258]:
        raise ValueError("main and shadow material profiles differ")
    highlight = _payload(max(highlights, key=lambda record: int(record["sequence"])))
    return main, highlight


def _source_texture(render: JsonObject, fragment: str) -> JsonObject:
    textures = render.get("metalTextureSnapshots")
    snapshots = textures.get("snapshots") if isinstance(textures, dict) else None
    if not isinstance(snapshots, list):
        raise ValueError("render texture snapshots are incomplete")
    matches = [
        snapshot
        for snapshot in snapshots
        if isinstance(snapshot, dict)
        and snapshot.get("index") == 3
        and snapshot.get("pixelFormat") == 80
        and type(snapshot.get("mipmapLevelCount")) is int
        and snapshot["mipmapLevelCount"] >= 2
        and _fragment(snapshot) == fragment
    ]
    if len(matches) != 1:
        raise ValueError(f"render has {len(matches)} complete backdrop pyramids")
    return matches[0]


def _raw_record(root: Path, record: JsonObject, name: str) -> JsonObject:
    filename = record.get("rawFile")
    byte_count = record.get("rawBytes")
    if (
        record.get("rawCapture") is not True
        or not isinstance(filename, str)
        or not isinstance(byte_count, int)
    ):
        raise ValueError(f"{name} raw metadata is incomplete")
    path = root / filename
    if not path.is_file() or path.stat().st_size != byte_count:
        raise ValueError(f"{name} raw file differs: {path}")
    return {
        "path": str(path),
        "bytes": byte_count,
        "sha256": sha256_file(path),
    }


def _pyramid_record(root: Path, source: JsonObject) -> JsonObject:
    levels = source.get("mipSnapshots")
    if not isinstance(levels, list) or len(levels) != source["mipmapLevelCount"]:
        raise ValueError("backdrop pyramid level count differs")
    return {
        "width": source["width"],
        "height": source["height"],
        "pixelFormat": source["pixelFormat"],
        "levels": [
            {
                "level": level["level"],
                "width": level["width"],
                "height": level["height"],
                **_raw_record(root, level, f"backdrop mip {level['level']}"),
            }
            for level in levels
            if isinstance(level, dict)
        ],
    }


def _component_comparison(actual: np.ndarray, expected: np.ndarray) -> JsonObject:
    if actual.shape != expected.shape:
        raise ValueError(f"geometry shapes differ: {actual.shape} != {expected.shape}")
    actual_bits = actual.astype("<f4", copy=False).view("<u4")
    expected_bits = expected.astype("<f4", copy=False).view("<u4")
    changed = actual_bits != expected_bits
    groups = {
        "position": slice(0, 2),
        "clip": slice(2, 4),
        "sdf": slice(4, 6),
        "source": slice(6, 8),
    }
    return {
        "exact": not bool(changed.any()),
        "mismatchedComponents": int(np.count_nonzero(changed)),
        "groups": {
            name: {
                "exact": not bool(changed[:, components].any()),
                "mismatchedComponents": int(np.count_nonzero(changed[:, components])),
            }
            for name, components in groups.items()
        },
    }


def _source_mapping(
    main: DrawGeometry, profile: JsonObject, source: JsonObject
) -> JsonObject:
    displacement = profile["fields"]["displacement_matrix"]["values"]
    scale_x = abs(float(displacement[0]))
    scale_y = abs(float(displacement[3]))
    if scale_x == 0.0 or scale_y == 0.0:
        raise ValueError("captured displacement matrix is singular")
    virtual_width = round(1.0 / scale_x)
    virtual_height = round(1.0 / scale_y)
    vertices = main.vertices.astype(np.float64)
    origins_x = vertices[:, 0] - vertices[:, 6] * virtual_width
    origins_y = vertices[:, 1] - vertices[:, 7] * virtual_height
    origin_x = float(origins_x.mean())
    origin_y = float(origins_y.mean())
    return {
        "virtualWidth": virtual_width,
        "virtualHeight": virtual_height,
        "textureScaleX": virtual_width / int(source["width"]),
        "textureScaleY": virtual_height / int(source["height"]),
        "originX": origin_x,
        "originY": origin_y,
        "originNearestInteger": [round(origin_x), round(origin_y)],
        "maximumOriginResidual": float(
            max(
                np.max(np.abs(origins_x - origin_x)),
                np.max(np.abs(origins_y - origin_y)),
            )
        ),
    }


def _static_templates(capture: Path) -> tuple[DrawGeometry, DrawGeometry]:
    runtime = json.loads((capture / "runtime.json").read_text(encoding="utf-8"))
    render = runtime.get("carendererEvidence")
    if not isinstance(render, dict):
        raise ValueError("static capture has no CARenderer evidence")
    material = str(runtime.get("materialProfileEvidence", {}).get("material"))
    try:
        fragment = GLASS_FRAGMENTS[material]
    except KeyError as error:
        raise ValueError(f"unsupported static material: {material}") from error
    return _background_geometry(render, fragment)


def _report_paths(root: Path) -> list[Path]:
    direct = root / REPORT_NAME
    if direct.is_file():
        return [direct]
    return sorted(root.glob(f"*/{REPORT_NAME}"))


def analyze_report(
    path: Path,
    *,
    main_template: DrawGeometry,
    shadow_template: DrawGeometry,
) -> JsonObject:
    report = json.loads(path.read_text(encoding="utf-8"))
    material = str(report.get("material"))
    try:
        fragment = GLASS_FRAGMENTS[material]
    except KeyError as error:
        raise ValueError(f"{path}: unsupported material {material}") from error
    geometry = report.get("geometry")
    uniforms = report.get("dynamicBackgroundUniforms")
    if not isinstance(geometry, dict) or not isinstance(uniforms, dict):
        raise ValueError(f"{path}: transition evidence is incomplete")
    records = uniforms.get("records")
    if (
        uniforms.get("schemaVersion") != 5
        or uniforms.get("executed") is not True
        or uniforms.get("presentationLayerReplayed") is not True
        or uniforms.get("presentationLayerAssignedToCARenderer") is not False
        or uniforms.get("freshStaticCarrier") is not True
        or uniforms.get("detachedLayerTreeCopies") is not False
        or tuple(
            tuple(path) for path in uniforms.get("carrierCriticalPaths", ())
        )
        != CARRIER_CRITICAL_PATHS
        or uniforms.get("transitionForegroundFilterCaptured") is not True
        or uniforms.get("transitionForegroundFilterReplayedOnCarrier") is not False
        or not isinstance(records, list)
        or [record.get("sampleIndex") for record in records if isinstance(record, dict)]
        != list(EXPECTED_SAMPLE_INDICES)
    ):
        raise ValueError(f"{path}: dynamic backdrop evidence is incomplete")

    states: list[JsonObject] = []
    for record in records:
        if not isinstance(record, dict):
            raise ValueError(f"{path}: dynamic state is not an object")
        render = record.get("render")
        sample_index = record.get("sampleIndex")
        expected_skipped = (
            () if sample_index == 32 else TRANSITION_ONLY_FOREGROUND_PATHS
        )
        if (
            record.get("freshStaticCarrier") is not True
            or record.get("detachedLayerTreeCopy") is not False
            or record.get("presentationLayerAssignedToCARenderer") is not False
            or record.get("backgroundFilterReplayedOnCarrier") is not True
            or record.get("foregroundFilterReplayedOnCarrier") is not False
            or tuple(
                tuple(path)
                for path in record.get("installedCriticalCarrierPaths", ())
            )
            != CARRIER_CRITICAL_PATHS
            or record.get("missingCriticalCarrierPaths") != []
            or tuple(
                tuple(path) for path in record.get("skippedCarrierPaths", ())
            )
            != expected_skipped
            or not isinstance(render, dict)
            or render.get("executed") is not True
        ):
            raise ValueError(f"{path}: dynamic render is incomplete")
        main, shadow = _background_geometry(render, fragment)
        mvp = _background_mvp(render, fragment)
        highlight_geometry = _highlight_geometry(render)
        profile_payload, highlight_payload = _uniform_payloads(render, fragment)
        profile = decode_profile(profile_payload)
        source = _source_texture(render, fragment)
        remaining = float(record["remaining"])
        generated = transition_circle_geometry(
            main_template=main_template,
            shadow_template=shadow_template,
            diameter=float(geometry["width"]),
            requested_center=(
                float(geometry["centerX"]),
                float(geometry["centerY"]),
            ),
            window_extent=(
                float(geometry["windowWidth"]),
                float(geometry["windowHeight"]),
            ),
            remaining=remaining,
        )
        output = render.get("output")
        if not isinstance(output, dict):
            raise ValueError(f"{path}: dynamic output metadata is incomplete")
        states.append(
            {
                "sampleIndex": record["sampleIndex"],
                "requestedProgress": record["requestedProgress"],
                "remaining": remaining,
                "snapshotLayerSource": record["snapshotLayerSource"],
                "renderDurationSeconds": render["durationSeconds"],
                "generatedEffectOrigin": list(generated.effect_origin),
                "generatedEffectExtent": generated.effect_extent,
                "geometryComparison": {
                    "main": _component_comparison(
                        main.vertices, generated.main.vertices
                    ),
                    "shadow": _component_comparison(
                        shadow.vertices,
                        generated.shadow.vertices,
                    ),
                },
                "mvpBits": [f"0x{value:08x}" for value in struct.unpack("<16I", mvp)],
                "sourceMapping": _source_mapping(main, profile, source),
                "backdropPyramid": _pyramid_record(path.parent, source),
                "output": {
                    "width": output["width"],
                    "height": output["height"],
                    "pixelFormat": output["pixelFormat"],
                    **_raw_record(path.parent, output, "CARenderer output"),
                },
                "backgroundProfileSHA256": hashlib.sha256(
                    profile_payload[48:258]
                ).hexdigest(),
                "finalHighlightUniformSHA256": hashlib.sha256(
                    highlight_payload
                ).hexdigest(),
                "finalHighlightGeometrySHA256": hashlib.sha256(
                    highlight_geometry.vertices.tobytes()
                    + highlight_geometry.indices.tobytes()
                ).hexdigest(),
            }
        )
    return {
        "artifact": str(path.parent),
        "timelineSHA256": sha256_file(path),
        "material": material,
        "appearance": report.get("appearance"),
        "geometry": geometry,
        "states": states,
    }


def analyze(root: Path, *, static_capture: Path) -> JsonObject:
    main_template, shadow_template = _static_templates(static_capture)
    reports = [
        analyze_report(
            path,
            main_template=main_template,
            shadow_template=shadow_template,
        )
        for path in _report_paths(root)
    ]
    reports.sort(
        key=lambda report: (str(report["material"]), str(report["appearance"]))
    )
    states = [state for report in reports for state in report["states"]]
    position_sdf_exact = all(
        state["geometryComparison"][draw]["groups"][group]["exact"]
        for state in states
        for draw in ("main", "shadow")
        for group in ("position", "clip", "sdf")
    )
    return {
        "liquidGlassDynamicCaptureAnalysisSchemaVersion": 1,
        "implementation": {
            "file": "analysis/liquid_glass_dynamic_capture.py",
            "python": platform.python_version(),
            "numpy": np.__version__,
        },
        "artifact": str(root),
        "staticTemplateCapture": str(static_capture),
        "profiles": reports,
        "conclusion": {
            "profileCount": len(reports),
            "stateCount": len(states),
            "allGeneratedPositionClipAndSdfBitsExact": position_sdf_exact,
            "movingBackdropBytesCapturedForEveryState": len(states) > 0,
            "dynamicRenderParityProven": False,
            "productionShaderAuthorized": False,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact", type=Path)
    parser.add_argument("--static-capture", required=True, type=Path)
    parser.add_argument("-o", "--output", type=Path)
    arguments = parser.parse_args()
    report = analyze(arguments.artifact, static_capture=arguments.static_capture)
    encoded = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if arguments.output is None:
        print(encoded, end="")
    else:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(encoded, encoding="utf-8")
        print(arguments.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
