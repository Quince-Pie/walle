#!/usr/bin/env python3
"""Recover Apple Liquid Glass center snapping and source-crop policy."""

import argparse
import hashlib
import json
import math
import platform
import struct
from pathlib import Path
from typing import Any


type JsonObject = dict[str, Any]
type Vertex = tuple[float, float, float, float, float, float, float, float]

MAIN_VERTEX_COUNT = 6
SHADOW_VERTEX_COUNT = 16
QUAD_VERTEX_COUNT = 4
VERTEX_STRIDE = 48
SOURCE_DOWNSAMPLE = 4
SOURCE_ALIGNMENT = 256
SMALL_REGULAR_CROP_ALIGNMENT = 128
SMALL_REGULAR_EXTENT_ALIGNMENT = 256
VIEWPORT_ALIGNMENT = 64
MAXIMUM_DOWNSAMPLE_VIEWPORT = 256
REGULAR_CROP_RASTER_GUARD = 0.5
NO_BLEED_CROP_ALIGNMENT = 16
NO_BLEED_VIRTUAL_EXTENT = 256
NO_BLEED_SCISSOR_TIER = 33
NO_BLEED_DIAMETER_TIER = 48
INTEGRAL_TOLERANCE = 1.0e-4
SOURCE_ORIGIN_TOLERANCE = 1.0 / 4096.0
GLASS_FRAGMENT_PREFIX = "glass_background_sdf"
REGULAR_GLASS_FRAGMENT = "glass_background_sdf_lph"
DOWNSAMPLE_FRAGMENT = "downsample_4_frag_lph"
COPY_BASE_PIPELINE = (
    "com.apple.coreanimation.variable_blur_copy_base_mip_compute"
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def pipeline_fragment(record: JsonObject) -> str:
    pipeline = record.get("pipeline")
    if not isinstance(pipeline, dict):
        return ""
    descriptor = pipeline.get("creationDescriptor")
    if not isinstance(descriptor, dict):
        return ""
    value = descriptor.get("fragmentFunction")
    return value if isinstance(value, str) else ""


def pipeline_label(record: JsonObject) -> str:
    pipeline = record.get("pipeline")
    if not isinstance(pipeline, dict):
        return ""
    value = pipeline.get("label")
    return value if isinstance(value, str) else ""


def buffer_snapshots(render: JsonObject) -> list[JsonObject]:
    evidence = render.get("metalBufferSnapshots")
    snapshots = (
        evidence.get("snapshots")
        if isinstance(evidence, dict)
        else None
    )
    if not isinstance(snapshots, list):
        raise ValueError("Metal buffer snapshots are missing")
    return [
        snapshot
        for snapshot in snapshots
        if isinstance(snapshot, dict)
    ]


def uniform_records(render: JsonObject) -> list[JsonObject]:
    evidence = render.get("metalUniformProbe")
    records = (
        evidence.get("records")
        if isinstance(evidence, dict)
        else None
    )
    if not isinstance(records, list):
        raise ValueError("Metal uniform records are missing")
    return [
        record
        for record in records
        if isinstance(record, dict)
    ]


def snapshot_bytes(snapshot: JsonObject) -> bytes:
    payload = snapshot.get("payload")
    encoded = payload.get("hex") if isinstance(payload, dict) else None
    if not isinstance(encoded, str):
        raise ValueError("vertex snapshot has no hexadecimal payload")
    return bytes.fromhex(encoded)


def unpack_snapshot(
    snapshot: JsonObject,
    format_code: str,
    *,
    offset: int = 0,
) -> tuple[Any, ...]:
    payload = snapshot_bytes(snapshot)
    required = offset + struct.calcsize(format_code)
    if len(payload) < required:
        raise ValueError(
            f"snapshot has {len(payload)} bytes; expected {required}"
        )
    return struct.unpack_from(format_code, payload, offset)


def vertices(snapshot: JsonObject, count: int) -> list[Vertex]:
    payload = snapshot_bytes(snapshot)
    required = count * VERTEX_STRIDE
    if len(payload) < required:
        raise ValueError(
            f"vertex snapshot has {len(payload)} bytes; expected {required}"
        )
    return [
        struct.unpack_from("<8f", payload, index * VERTEX_STRIDE)
        for index in range(count)
    ]


def vertex_float_component_sha256(
    snapshot: JsonObject,
    count: int,
) -> str:
    encoded = b"".join(
        struct.pack("<8f", *vertex)
        for vertex in vertices(snapshot, count)
    )
    return sha256_bytes(encoded)


def bounds(values: list[Vertex], offset: int) -> JsonObject:
    x_values = [value[offset] for value in values]
    y_values = [value[offset + 1] for value in values]
    return {
        "minimumX": min(x_values),
        "minimumY": min(y_values),
        "maximumX": max(x_values),
        "maximumY": max(y_values),
    }


def round_half_away(value: float) -> int:
    return (
        math.floor(value + 0.5)
        if value >= 0.0
        else math.ceil(value - 0.5)
    )


def rounded_integral(
    value: float,
    *,
    tolerance: float = INTEGRAL_TOLERANCE,
) -> tuple[int, float]:
    rounded = round(value)
    residual = abs(value - rounded)
    if residual > tolerance:
        raise ValueError(
            f"value {value!r} is {residual} from the nearest integer"
        )
    return rounded, residual


def align_down(value: float, alignment: int) -> int:
    return math.floor(value / alignment) * alignment


def align_up(value: float, alignment: int) -> int:
    return math.ceil(value / alignment) * alignment


def viewport_extent_for_scissor(scissor_extent: int) -> int:
    return min(
        MAXIMUM_DOWNSAMPLE_VIEWPORT,
        align_up(scissor_extent, VIEWPORT_ALIGNMENT),
    )


def snap_candidates(
    center: float,
    *,
    width: float,
) -> dict[str, int]:
    origin = center - width / 2.0
    return {
        "frame-origin-nearest-even": round(origin),
        "frame-origin-nearest-away": round_half_away(origin),
        "frame-origin-nearest-ties-positive-infinity":
            math.floor(origin + 0.5),
        "frame-origin-floor": math.floor(origin),
        "frame-origin-ceil": math.ceil(origin),
        "frame-origin-truncate": math.trunc(origin),
    }


def report_path(path: Path) -> Path:
    if path.is_file():
        return path
    compact = path / "geometry-policy.json"
    if compact.is_file():
        return compact
    runtime = path / "runtime.json"
    if runtime.is_file():
        return runtime
    raise ValueError(f"{path} has no geometry-policy.json or runtime.json")


def report_parts(
    report: JsonObject,
) -> tuple[JsonObject, JsonObject, str]:
    if report.get("probe") == "compact-geometry-policy":
        geometry = report.get("geometry")
        render = report.get("carendererEvidence")
        kind = "compact-policy"
    else:
        geometry = report.get("geometryEvidence")
        render = report.get("carendererEvidence")
        kind = "full-introspection"
    if not isinstance(geometry, dict) or not isinstance(render, dict):
        raise ValueError("geometry or CARenderer evidence is missing")
    if render.get("executed") is not True:
        raise ValueError("CARenderer evidence did not execute")
    return geometry, render, kind


def glass_vertex_snapshots(render: JsonObject) -> list[JsonObject]:
    matches = sorted(
        (
            snapshot
            for snapshot in buffer_snapshots(render)
            if snapshot.get("stage") == "vertex"
            and snapshot.get("index") == 1
            and pipeline_fragment(snapshot).startswith(GLASS_FRAGMENT_PREFIX)
        ),
        key=lambda snapshot: int(snapshot["sequence"]),
    )
    if len(matches) != 2:
        raise ValueError(
            f"expected main and shadow vertex snapshots; found {len(matches)}"
        )
    return matches


def source_texture(render: JsonObject) -> JsonObject:
    texture_evidence = render.get("metalTextureSnapshots")
    snapshots = (
        texture_evidence.get("snapshots")
        if isinstance(texture_evidence, dict)
        else None
    )
    if not isinstance(snapshots, list):
        raise ValueError("Metal texture snapshots are missing")
    matches = [
        snapshot
        for snapshot in snapshots
        if isinstance(snapshot, dict)
        and snapshot.get("index") == 3
        and pipeline_fragment(snapshot).startswith(GLASS_FRAGMENT_PREFIX)
    ]
    if len(matches) != 1:
        raise ValueError(
            f"expected one glass source texture; found {len(matches)}"
        )
    return matches[0]


def recover_source_origin(
    main: list[Vertex],
    *,
    virtual_width: int,
    virtual_height: int,
) -> tuple[int, int, float]:
    candidates_x = [
        vertex[0] - vertex[6] * virtual_width
        for vertex in main
    ]
    candidates_y = [
        vertex[1] - vertex[7] * virtual_height
        for vertex in main
    ]
    origin_x = round(sum(candidates_x) / len(candidates_x))
    origin_y = round(sum(candidates_y) / len(candidates_y))
    maximum_residual = max(
        abs(value - origin_x)
        for value in candidates_x
    )
    maximum_residual = max(
        maximum_residual,
        max(abs(value - origin_y) for value in candidates_y),
    )
    return origin_x, origin_y, maximum_residual


def single_record(
    records: list[JsonObject],
    *,
    description: str,
) -> JsonObject:
    if len(records) != 1:
        raise ValueError(f"expected one {description}; found {len(records)}")
    return records[0]


def copy_base_uniform(snapshot: JsonObject) -> JsonObject:
    return {
        "textureCoordinateBase": list(
            unpack_snapshot(snapshot, "<2h")
        ),
        "textureCoordinateClamp": list(
            unpack_snapshot(snapshot, "<4h", offset=8)
        ),
        "destinationLevel0Size": list(
            unpack_snapshot(snapshot, "<2H", offset=16)
        ),
        "destinationLevel1Size": list(
            unpack_snapshot(snapshot, "<2H", offset=20)
        ),
        "destinationLevel1": unpack_snapshot(
            snapshot,
            "<H",
            offset=24,
        )[0],
        "noBaseMip": unpack_snapshot(
            snapshot,
            "<?",
            offset=26,
        )[0],
    }


def requested_bounds(geometry: JsonObject) -> JsonObject:
    center_x = float(geometry["centerX"])
    center_y = (
        float(geometry["windowHeight"])
        - float(geometry["centerY"])
    )
    half_width = float(geometry["width"]) / 2.0
    half_height = float(geometry["height"]) / 2.0
    return {
        "minimumX": center_x - half_width,
        "minimumY": center_y - half_height,
        "maximumX": center_x + half_width,
        "maximumY": center_y + half_height,
    }


def expanded_source_bounds(
    geometry: JsonObject,
    *,
    margin: float,
) -> JsonObject:
    requested = requested_bounds(geometry)
    window_width = int(geometry["windowWidth"])
    window_height = int(geometry["windowHeight"])
    return {
        "minimumX": align_down(
            max(0.0, float(requested["minimumX"]) - margin),
            SOURCE_DOWNSAMPLE,
        ),
        "minimumY": align_down(
            max(0.0, float(requested["minimumY"]) - margin),
            SOURCE_DOWNSAMPLE,
        ),
        "maximumX": align_up(
            min(
                float(window_width),
                float(requested["maximumX"]) + margin,
            ),
            SOURCE_DOWNSAMPLE,
        ),
        "maximumY": align_up(
            min(
                float(window_height),
                float(requested["maximumY"]) + margin,
            ),
            SOURCE_DOWNSAMPLE,
        ),
    }


def exact_bounds(left: JsonObject, right: JsonObject) -> bool:
    return all(left[name] == right[name] for name in left)


def downsample_evidence(
    render: JsonObject,
    geometry: JsonObject,
    *,
    source_crop: JsonObject,
    glass_fragment: str,
) -> JsonObject:
    snapshots = buffer_snapshots(render)
    downsample_vertices = single_record(
        [
            snapshot
            for snapshot in snapshots
            if snapshot.get("stage") == "vertex"
            and snapshot.get("index") == 1
            and pipeline_fragment(snapshot) == DOWNSAMPLE_FRAGMENT
        ],
        description="downsample vertex buffer",
    )
    vertex_values = vertices(
        downsample_vertices,
        QUAD_VERTEX_COUNT,
    )
    position_bounds = bounds(vertex_values, 0)
    source_bounds = bounds(vertex_values, 4)
    source_position_residual = max(
        abs(
            vertex[index + 4]
            - vertex[index] * SOURCE_DOWNSAMPLE
        )
        for vertex in vertex_values
        for index in (0, 1)
    )
    live_vertex_components = b"".join(
        struct.pack("<6f", *vertex[:6])
        for vertex in vertex_values
    )

    mvp_snapshot = single_record(
        [
            snapshot
            for snapshot in snapshots
            if snapshot.get("stage") == "vertex"
            and snapshot.get("index") == 2
            and pipeline_fragment(snapshot) == DOWNSAMPLE_FRAGMENT
        ],
        description="downsample MVP buffer",
    )
    mvp = list(unpack_snapshot(mvp_snapshot, "<16f"))
    texture_matrix_snapshot = single_record(
        [
            snapshot
            for snapshot in snapshots
            if snapshot.get("stage") == "vertex"
            and snapshot.get("index") == 3
            and pipeline_fragment(snapshot) == DOWNSAMPLE_FRAGMENT
        ],
        description="downsample texture matrix buffer",
    )
    texture_matrix = list(
        unpack_snapshot(texture_matrix_snapshot, "<4f")
    )

    records = uniform_records(render)
    pipeline_record = single_record(
        [
            record
            for record in records
            if record.get("kind") == "pipeline"
            and pipeline_fragment(record) == DOWNSAMPLE_FRAGMENT
        ],
        description="downsample pipeline record",
    )
    encoder = pipeline_record.get("encoder")
    pipeline_sequence = int(pipeline_record["sequence"])
    viewport = single_record(
        [
            record
            for record in records
            if record.get("kind") == "viewport"
            and record.get("encoder") == encoder
            and int(record["sequence"]) < pipeline_sequence
        ],
        description="downsample viewport",
    )
    scissor = single_record(
        [
            record
            for record in records
            if record.get("kind") == "scissorRect"
            and pipeline_fragment(record) == DOWNSAMPLE_FRAGMENT
        ],
        description="downsample scissor",
    )
    viewport_width = float(viewport["width"])
    viewport_height = float(viewport["height"])
    screen_shift_x = (
        float(viewport.get("originX", 0.0))
        + (mvp[12] + 1.0) * viewport_width / 2.0
    )
    screen_shift_y = (
        float(viewport.get("originY", 0.0))
        + (1.0 - mvp[13]) * viewport_height / 2.0
    )

    copy_snapshot = single_record(
        [
            snapshot
            for snapshot in snapshots
            if snapshot.get("stage") == "compute"
            and snapshot.get("index") == 0
            and pipeline_label(snapshot) == COPY_BASE_PIPELINE
        ],
        description="copy-base uniform buffer",
    )
    copy_uniform = copy_base_uniform(copy_snapshot)
    texture_coordinate_base = copy_uniform["textureCoordinateBase"]
    algebra_origin_x = SOURCE_DOWNSAMPLE * (
        float(texture_coordinate_base[0]) - screen_shift_x
    )
    algebra_origin_y = SOURCE_DOWNSAMPLE * (
        float(texture_coordinate_base[1]) - screen_shift_y
    )
    algebra_x, algebra_x_residual = rounded_integral(
        algebra_origin_x
    )
    algebra_y, algebra_y_residual = rounded_integral(
        algebra_origin_y
    )
    crop_algebra_exact = (
        algebra_x == source_crop["originX"]
        and algebra_y == source_crop["originY"]
    )

    input_texture = single_record(
        [
            record
            for record in records
            if record.get("kind") == "texture"
            and record.get("stage") == "compute"
            and record.get("index") == 0
            and pipeline_label(record) == COPY_BASE_PIPELINE
        ],
        description="copy-base input texture",
    )["texture"]
    output_texture = single_record(
        [
            record
            for record in records
            if record.get("kind") == "texture"
            and record.get("stage") == "compute"
            and record.get("index") == 1
            and pipeline_label(record) == COPY_BASE_PIPELINE
        ],
        description="copy-base output texture",
    )["texture"]

    profile: JsonObject | None = None
    expansion_candidates: JsonObject = {}
    if glass_fragment == REGULAR_GLASS_FRAGMENT:
        profile_snapshot = sorted(
            (
                snapshot
                for snapshot in snapshots
                if snapshot.get("stage") == "fragment"
                and snapshot.get("index") == 1
                and pipeline_fragment(snapshot) == glass_fragment
            ),
            key=lambda snapshot: int(snapshot["sequence"]),
        )[0]
        blur_radius = float(
            unpack_snapshot(profile_snapshot, "<f", offset=88)[0]
        )
        edge_bleed_amount = float(
            unpack_snapshot(profile_snapshot, "<f", offset=96)[0]
        )
        profile = {
            "blurRadius": blur_radius,
            "edgeBleedAmount": edge_bleed_amount,
        }
        for name, margin in {
            "edgeBleedAmount": edge_bleed_amount,
            "edgeBleedPlusBlur":
                edge_bleed_amount + blur_radius,
            "maximum80OrEdgeBleedPlusBlur":
                max(80.0, edge_bleed_amount + blur_radius),
        }.items():
            predicted = expanded_source_bounds(
                geometry,
                margin=margin,
            )
            expansion_candidates[name] = {
                "margin": margin,
                "predictedSourceBounds": predicted,
                "exact": exact_bounds(source_bounds, predicted),
            }

    return {
        "vertexSequence": downsample_vertices["sequence"],
        "vertexPayloadSha256": sha256_bytes(
            snapshot_bytes(downsample_vertices)[
                : QUAD_VERTEX_COUNT * VERTEX_STRIDE
            ]
        ),
        "vertexFloatComponentSha256":
            vertex_float_component_sha256(
                downsample_vertices,
                QUAD_VERTEX_COUNT,
            ),
        "vertexLiveComponentSha256": sha256_bytes(
            live_vertex_components
        ),
        "positionBounds": position_bounds,
        "sourceBounds": source_bounds,
        "sourcePositionScaleResidual": source_position_residual,
        "mvpSequence": mvp_snapshot["sequence"],
        "mvpPayloadSha256": sha256_bytes(
            snapshot_bytes(mvp_snapshot)[:64]
        ),
        "mvp": mvp,
        "textureMatrixSequence": texture_matrix_snapshot["sequence"],
        "textureMatrixPayloadSha256": sha256_bytes(
            snapshot_bytes(texture_matrix_snapshot)[:16]
        ),
        "textureMatrix": texture_matrix,
        "viewport": {
            name: viewport.get(name)
            for name in (
                "originX",
                "originY",
                "width",
                "height",
                "znear",
                "zfar",
            )
        },
        "scissor": {
            name: scissor.get(name)
            for name in ("x", "y", "width", "height")
        },
        "screenShift": [screen_shift_x, screen_shift_y],
        "copyBase": {
            "sequence": copy_snapshot["sequence"],
            "uniformPayloadSha256": sha256_bytes(
                snapshot_bytes(copy_snapshot)[:32]
            ),
            "uniform": copy_uniform,
            "inputTexture": input_texture,
            "outputTexture": output_texture,
        },
        "cropOriginFromCopyBaseAndMVP": {
            "originX": algebra_x,
            "originY": algebra_y,
            "maximumIntegralResidual": max(
                algebra_x_residual,
                algebra_y_residual,
            ),
            "exact": crop_algebra_exact,
        },
        "regularProfile": profile,
        "sourceExpansionCandidates": expansion_candidates,
    }


def crop_padding_candidates(
    capture: JsonObject,
    *,
    axis: str,
) -> list[int]:
    if capture["glassPath"] != "regular-edge-bleed":
        return []
    downsample = capture["downsample"]["sourceBounds"]
    crop = capture["sourceCrop"]
    minimum = float(downsample[f"minimum{axis}"])
    maximum = float(downsample[f"maximum{axis}"])
    origin = int(crop[f"origin{axis}"])
    extent = int(crop[f"virtual{'Width' if axis == 'X' else 'Height'}"])
    end = origin + extent
    return [
        padding
        for padding in range(129)
        if align_down(
            minimum - padding * SOURCE_DOWNSAMPLE,
            SOURCE_ALIGNMENT,
        ) == origin
        and align_up(
            maximum + padding * SOURCE_DOWNSAMPLE,
            SOURCE_ALIGNMENT,
        ) == end
    ]


def regular_crop_from_geometry(
    capture: JsonObject,
    *,
    padding: int,
) -> JsonObject:
    if capture["glassPath"] != "regular-edge-bleed":
        raise ValueError("regular crop prediction requires regular glass")
    geometry = capture["geometry"]
    main = capture["mainBounds"]
    profile = capture["downsample"]["regularProfile"]
    edge_bleed = float(profile["edgeBleedAmount"])
    result: JsonObject = {}
    for axis, extent_name, window_name in (
        ("X", "Width", "windowWidth"),
        ("Y", "Height", "windowHeight"),
    ):
        window_extent = int(geometry[window_name])
        effect_minimum = max(
            0.0,
            float(main[f"minimum{axis}"])
            - edge_bleed
            - REGULAR_CROP_RASTER_GUARD,
        )
        effect_maximum = min(
            float(window_extent),
            float(main[f"maximum{axis}"])
            + edge_bleed
            + REGULAR_CROP_RASTER_GUARD,
        )
        origin = align_down(
            effect_minimum - padding,
            SOURCE_ALIGNMENT,
        )
        end = align_up(
            effect_maximum + padding,
            SOURCE_ALIGNMENT,
        )
        result[f"origin{axis}"] = origin
        result[f"virtual{extent_name}"] = end - origin
    return result


def regular_geometry_padding_candidates(
    captures: list[JsonObject],
) -> list[int]:
    if not captures:
        return []
    return [
        padding
        for padding in range(513)
        if all(
            regular_crop_from_geometry(
                capture,
                padding=padding,
            ) == {
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
    ]


def small_regular_crop_from_geometry(
    capture: JsonObject,
    *,
    padding: int,
) -> JsonObject:
    if capture["glassPath"] != "regular-edge-bleed":
        raise ValueError("small crop prediction requires regular glass")
    geometry = capture["geometry"]
    main = capture["mainBounds"]
    profile = capture["downsample"]["regularProfile"]
    edge_bleed = float(profile["edgeBleedAmount"])
    result: JsonObject = {}
    for axis, extent_name, window_name in (
        ("X", "Width", "windowWidth"),
        ("Y", "Height", "windowHeight"),
    ):
        window_extent = int(geometry[window_name])
        effect_minimum = max(
            0.0,
            float(main[f"minimum{axis}"])
            - edge_bleed
            - REGULAR_CROP_RASTER_GUARD,
        )
        effect_maximum = min(
            float(window_extent),
            float(main[f"maximum{axis}"])
            + edge_bleed
            + REGULAR_CROP_RASTER_GUARD,
        )
        origin = align_down(
            effect_minimum - padding,
            SMALL_REGULAR_CROP_ALIGNMENT,
        )
        nominal_end = align_up(
            effect_maximum + padding,
            SMALL_REGULAR_CROP_ALIGNMENT,
        )
        result[f"origin{axis}"] = origin
        result[f"virtual{extent_name}"] = align_up(
            nominal_end - origin,
            SMALL_REGULAR_EXTENT_ALIGNMENT,
        )
    return result


def small_regular_geometry_padding_candidates(
    captures: list[JsonObject],
) -> list[int]:
    if not captures:
        return []
    return [
        padding
        for padding in range(513)
        if all(
            small_regular_crop_from_geometry(
                capture,
                padding=padding,
            ) == {
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
    ]


def no_bleed_crop_from_geometry(
    capture: JsonObject,
    *,
    tier_selector: str = "scissor-33",
) -> JsonObject:
    if capture["glassPath"] != "no-bleed":
        raise ValueError("no-bleed crop prediction requires no-bleed glass")
    geometry = capture["geometry"]
    main = capture["mainBounds"]
    result: JsonObject = {}
    diameter = float(geometry["width"])
    for axis, dimension_name, extent_name in (
        ("X", "width", "Width"),
        ("Y", "height", "Height"),
    ):
        axis_dimension = float(geometry[dimension_name])
        if tier_selector == "scissor-33":
            scissor = capture["downsample"]["scissor"]
            upper_tier = (
                int(scissor[dimension_name]) >= NO_BLEED_SCISSOR_TIER
            )
        elif tier_selector == "diameter-48":
            upper_tier = diameter >= NO_BLEED_DIAMETER_TIER
        else:
            raise ValueError(
                f"unknown no-bleed tier selector: {tier_selector}"
            )
        axis_margin = (
            axis_dimension / SOURCE_DOWNSAMPLE
            + REGULAR_CROP_RASTER_GUARD
            if upper_tier
            else axis_dimension * 0.2
        )
        result[f"origin{axis}"] = align_down(
            float(main[f"minimum{axis}"]) - axis_margin,
            NO_BLEED_CROP_ALIGNMENT,
        )
        result[f"virtual{extent_name}"] = NO_BLEED_VIRTUAL_EXTENT
    return result


def matching_snap_policies(
    requested_center: float,
    observed_origin: int,
    *,
    width: float,
) -> list[str]:
    return [
        name
        for name, candidate in snap_candidates(
            requested_center,
            width=width,
        ).items()
        if candidate == observed_origin
    ]


def analyze_capture(path: Path) -> JsonObject:
    selected_path = report_path(path)
    report = json.loads(selected_path.read_text(encoding="utf-8"))
    geometry, render, kind = report_parts(report)
    vertex_snapshots = glass_vertex_snapshots(render)
    main = vertices(vertex_snapshots[0], MAIN_VERTEX_COUNT)
    main_bounds = bounds(main, 0)
    observed_origin_x_value = float(main_bounds["minimumX"])
    window_height = int(geometry["windowHeight"])
    observed_origin_y_value = (
        window_height - float(main_bounds["maximumY"])
    )
    observed_origin_x, origin_x_residual = rounded_integral(
        observed_origin_x_value
    )
    observed_origin_y, origin_y_residual = rounded_integral(
        observed_origin_y_value
    )
    geometry_width = float(geometry["width"])
    geometry_height = float(geometry["height"])
    observed_center_x = observed_origin_x + geometry_width / 2.0
    observed_center_y = observed_origin_y + geometry_height / 2.0

    source = source_texture(render)
    virtual_width = int(source["width"]) * SOURCE_DOWNSAMPLE
    virtual_height = int(source["height"]) * SOURCE_DOWNSAMPLE
    origin_x, origin_y, residual = recover_source_origin(
        main,
        virtual_width=virtual_width,
        virtual_height=virtual_height,
    )
    requested_x = float(geometry["centerX"])
    requested_y = float(geometry["centerY"])
    glass_fragment = pipeline_fragment(vertex_snapshots[0])
    glass_path = (
        "regular-edge-bleed"
        if glass_fragment == REGULAR_GLASS_FRAGMENT
        else "no-bleed"
        if glass_fragment.startswith(GLASS_FRAGMENT_PREFIX)
        else "unknown"
    )
    source_crop = {
        "originX": origin_x,
        "originY": origin_y,
        "virtualWidth": virtual_width,
        "virtualHeight": virtual_height,
        "maximumOriginRecoveryResidual": residual,
        "origin256Aligned": (
            origin_x % SOURCE_ALIGNMENT == 0
            and origin_y % SOURCE_ALIGNMENT == 0
        ),
        "extent256Aligned": (
            virtual_width % SOURCE_ALIGNMENT == 0
            and virtual_height % SOURCE_ALIGNMENT == 0
        ),
        "origin128Aligned": (
            origin_x % 128 == 0
            and origin_y % 128 == 0
        ),
        "extent128Aligned": (
            virtual_width % 128 == 0
            and virtual_height % 128 == 0
        ),
        "origin64Aligned": origin_x % 64 == 0 and origin_y % 64 == 0,
        "extent64Aligned": (
            virtual_width % 64 == 0
            and virtual_height % 64 == 0
        ),
        "origin16Aligned": origin_x % 16 == 0 and origin_y % 16 == 0,
        "extent16Aligned": (
            virtual_width % 16 == 0
            and virtual_height % 16 == 0
        ),
        "textureWidth": int(source["width"]),
        "textureHeight": int(source["height"]),
        "mipmapLevelCount": int(source["mipmapLevelCount"]),
    }
    downsample = downsample_evidence(
        render,
        geometry,
        source_crop=source_crop,
        glass_fragment=glass_fragment,
    )
    return {
        "artifact": str(path),
        "report": str(selected_path),
        "reportSha256": sha256_file(selected_path),
        "captureKind": kind,
        "glassFragment": glass_fragment,
        "glassPath": glass_path,
        "geometry": geometry,
        "observedSwiftUICenter": [
            observed_center_x,
            observed_center_y,
        ],
        "observedSwiftUIFrameOrigin": [
            observed_origin_x,
            observed_origin_y,
        ],
        "maximumFrameOriginIntegralResidual": max(
            origin_x_residual,
            origin_y_residual,
        ),
        "centerDelta": [
            observed_center_x - requested_x,
            observed_center_y - requested_y,
        ],
        "matchingSnapPolicies": {
            "x": matching_snap_policies(
                requested_x,
                observed_origin_x,
                width=geometry_width,
            ),
            "y": matching_snap_policies(
                requested_y,
                observed_origin_y,
                width=geometry_height,
            ),
        },
        "mainBounds": main_bounds,
        "mainVertexPayloadSha256": sha256_bytes(
            snapshot_bytes(vertex_snapshots[0])[
                : MAIN_VERTEX_COUNT * VERTEX_STRIDE
            ]
        ),
        "mainVertexFloatComponentSha256":
            vertex_float_component_sha256(
                vertex_snapshots[0],
                MAIN_VERTEX_COUNT,
            ),
        "shadowVertexPayloadSha256": sha256_bytes(
            snapshot_bytes(vertex_snapshots[1])[
                : SHADOW_VERTEX_COUNT * VERTEX_STRIDE
            ]
        ),
        "shadowVertexFloatComponentSha256":
            vertex_float_component_sha256(
                vertex_snapshots[1],
                SHADOW_VERTEX_COUNT,
            ),
        "sourceCrop": source_crop,
        "downsample": downsample,
    }


def policy_intersection(
    captures: list[JsonObject],
    axis: str,
) -> list[str]:
    policies = {
        name
        for capture in captures
        for name in capture["matchingSnapPolicies"][axis]
    }
    for capture in captures:
        policies &= set(capture["matchingSnapPolicies"][axis])
    return sorted(policies)


def texture_signature(texture: JsonObject) -> JsonObject:
    return {
        name: texture.get(name)
        for name in (
            "width",
            "height",
            "depth",
            "mipmapLevelCount",
            "arrayLength",
            "sampleCount",
            "textureType",
            "pixelFormat",
            "storageMode",
            "usage",
        )
    }


def control_signature(capture: JsonObject) -> JsonObject:
    crop = capture["sourceCrop"]
    downsample = capture["downsample"]
    return {
        "glassFragment": capture["glassFragment"],
        "observedSwiftUIFrameOrigin":
            capture["observedSwiftUIFrameOrigin"],
        "mainVertexFloatComponentSha256":
            capture["mainVertexFloatComponentSha256"],
        "shadowVertexFloatComponentSha256":
            capture["shadowVertexFloatComponentSha256"],
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
            )
        },
        "downsample": {
            name: downsample[name]
            for name in (
                "vertexLiveComponentSha256",
                "mvpPayloadSha256",
                "textureMatrixPayloadSha256",
                "positionBounds",
                "sourceBounds",
                "viewport",
                "scissor",
                "screenShift",
            )
        },
        "copyBase": {
            "uniformPayloadSha256":
                downsample["copyBase"]["uniformPayloadSha256"],
            "uniform": downsample["copyBase"]["uniform"],
            "inputTexture": texture_signature(
                downsample["copyBase"]["inputTexture"]
            ),
            "outputTexture": texture_signature(
                downsample["copyBase"]["outputTexture"]
            ),
        },
    }


def duplicate_control_comparisons(
    captures: list[JsonObject],
) -> list[JsonObject]:
    by_name: dict[str, list[JsonObject]] = {}
    for capture in captures:
        name = str(capture["geometry"]["name"])
        by_name.setdefault(name, []).append(capture)
    comparisons = []
    for name, group in sorted(by_name.items()):
        kinds = {str(capture["captureKind"]) for capture in group}
        if not {"compact-policy", "full-introspection"} <= kinds:
            continue
        signatures = [
            control_signature(capture)
            for capture in group
        ]
        comparisons.append({
            "geometry": name,
            "captures": [
                {
                    "artifact": capture["artifact"],
                    "captureKind": capture["captureKind"],
                    "reportSha256": capture["reportSha256"],
                }
                for capture in group
            ],
            "exact": all(
                signature == signatures[0]
                for signature in signatures[1:]
            ),
            "signatureSha256": sha256_bytes(
                json.dumps(
                    signatures[0],
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                ).encode()
            ),
        })
    return comparisons


def analyze(paths: list[Path]) -> JsonObject:
    captures = [analyze_capture(path) for path in paths]
    x_policies = policy_intersection(captures, "x")
    y_policies = policy_intersection(captures, "y")
    regular_captures = [
        capture
        for capture in captures
        if capture["glassPath"] == "regular-edge-bleed"
    ]
    no_bleed_captures = [
        capture
        for capture in captures
        if capture["glassPath"] == "no-bleed"
    ]
    all_origins_recovered = all(
        capture["sourceCrop"]["maximumOriginRecoveryResidual"]
        <= SOURCE_ORIGIN_TOLERANCE
        for capture in captures
    )
    small_regular_alignment_captures = [
        capture
        for capture in regular_captures
        if int(capture["sourceCrop"]["textureWidth"]) <= 192
        and int(capture["sourceCrop"]["textureHeight"]) <= 192
    ]
    large_regular_alignment_captures = [
        capture
        for capture in regular_captures
        if int(capture["sourceCrop"]["textureWidth"]) >= 256
        and int(capture["sourceCrop"]["textureHeight"]) >= 256
    ]
    regular_large_crops_256_aligned = all(
        capture["sourceCrop"]["origin256Aligned"]
        and capture["sourceCrop"]["extent256Aligned"]
        for capture in large_regular_alignment_captures
    )
    regular_small_crops_128_aligned = all(
        capture["sourceCrop"]["origin128Aligned"]
        and capture["sourceCrop"]["extent128Aligned"]
        for capture in small_regular_alignment_captures
    )
    no_bleed_extents_64_aligned = all(
        capture["sourceCrop"]["extent64Aligned"]
        for capture in no_bleed_captures
    )
    no_bleed_origins_64_aligned = all(
        capture["sourceCrop"]["origin64Aligned"]
        for capture in no_bleed_captures
    )
    no_bleed_crops_16_aligned = all(
        capture["sourceCrop"]["origin16Aligned"]
        and capture["sourceCrop"]["extent16Aligned"]
        for capture in no_bleed_captures
    )
    all_crops_aligned = (
        all_origins_recovered
        and regular_large_crops_256_aligned
        and regular_small_crops_128_aligned
        and no_bleed_extents_64_aligned
        and no_bleed_crops_16_aligned
    )
    padding_x = set(range(129))
    padding_y = set(range(129))
    for capture in regular_captures:
        padding_x &= set(crop_padding_candidates(capture, axis="X"))
        padding_y &= set(crop_padding_candidates(capture, axis="Y"))
    padding_x_values = sorted(padding_x)
    padding_y_values = sorted(padding_y)
    common_padding = sorted(padding_x & padding_y)
    padding_groups: list[JsonObject] = []
    captures_by_mip_count: dict[int, list[JsonObject]] = {}
    for capture in regular_captures:
        mip_count = int(capture["sourceCrop"]["mipmapLevelCount"])
        captures_by_mip_count.setdefault(mip_count, []).append(capture)
    for mip_count, group in sorted(captures_by_mip_count.items()):
        group_x = set(range(129))
        group_y = set(range(129))
        for capture in group:
            group_x &= set(
                crop_padding_candidates(capture, axis="X")
            )
            group_y &= set(
                crop_padding_candidates(capture, axis="Y")
            )
        group_x_values = sorted(group_x)
        group_y_values = sorted(group_y)
        group_common = sorted(group_x & group_y)
        padding_groups.append({
            "mipmapLevelCount": mip_count,
            "captureCount": len(group),
            "x": group_x_values,
            "y": group_y_values,
            "common": group_common,
            "fullResolution": [
                value * SOURCE_DOWNSAMPLE
                for value in group_common
            ],
            "singleSharedCandidate": len(group_common) == 1,
            "uniqueAcrossBothAxes": (
                len(group_common) == 1
                and group_x_values == group_y_values
            ),
        })
    target_padding_groups: list[JsonObject] = []
    captures_by_source_target: dict[
        tuple[int, int, int],
        list[JsonObject],
    ] = {}
    for capture in regular_captures:
        crop = capture["sourceCrop"]
        key = (
            int(crop["textureWidth"]),
            int(crop["textureHeight"]),
            int(crop["mipmapLevelCount"]),
        )
        captures_by_source_target.setdefault(key, []).append(capture)
    for target, group in sorted(captures_by_source_target.items()):
        group_x = set(range(129))
        group_y = set(range(129))
        for capture in group:
            group_x &= set(
                crop_padding_candidates(capture, axis="X")
            )
            group_y &= set(
                crop_padding_candidates(capture, axis="Y")
            )
        group_x_values = sorted(group_x)
        group_y_values = sorted(group_y)
        group_common = sorted(group_x & group_y)
        target_padding_groups.append({
            "sourceTexture": [target[0], target[1]],
            "mipmapLevelCount": target[2],
            "captureCount": len(group),
            "x": group_x_values,
            "y": group_y_values,
            "common": group_common,
            "fullResolution": [
                value * SOURCE_DOWNSAMPLE
                for value in group_common
            ],
            "singleSharedCandidate": len(group_common) == 1,
            "uniqueAcrossBothAxes": (
                len(group_common) == 1
                and group_x_values == group_y_values
            ),
        })
    crop_algebra_exact = all(
        capture["downsample"]["cropOriginFromCopyBaseAndMVP"]["exact"]
        for capture in captures
    )
    downsample_scale_exact = all(
        capture["downsample"]["sourcePositionScaleResidual"]
        <= INTEGRAL_TOLERANCE
        for capture in captures
    )
    expansion_candidate_counts = {
        name: sum(
            capture["downsample"]["sourceExpansionCandidates"][name][
                "exact"
            ]
            for capture in regular_captures
        )
        for name in (
            "edgeBleedAmount",
            "edgeBleedPlusBlur",
            "maximum80OrEdgeBleedPlusBlur",
        )
    }
    small_regular_targets = [
        capture
        for capture in regular_captures
        if int(capture["sourceCrop"]["textureWidth"]) <= 192
        and int(capture["sourceCrop"]["textureHeight"]) <= 192
    ]
    large_regular_targets = [
        capture
        for capture in regular_captures
        if int(capture["sourceCrop"]["textureWidth"]) >= 256
        and int(capture["sourceCrop"]["textureHeight"]) >= 256
    ]
    small_geometry_padding = (
        small_regular_geometry_padding_candidates(
            small_regular_targets
        )
    )
    large_geometry_padding = regular_geometry_padding_candidates(
        large_regular_targets
    )
    no_bleed_model_measurements = {
        selector: all(
            no_bleed_crop_from_geometry(
                capture,
                tier_selector=selector,
            ) == {
                name: capture["sourceCrop"][name]
                for name in (
                    "originX",
                    "virtualWidth",
                    "originY",
                    "virtualHeight",
                )
            }
            for capture in no_bleed_captures
        )
        for selector in (
            "scissor-33",
            "diameter-48",
        )
    }
    exact_no_bleed_models = [
        selector
        for selector, exact in no_bleed_model_measurements.items()
        if exact
    ]
    no_bleed_crop_model_exact = bool(exact_no_bleed_models)
    viewport_alignment_exact = all(
        int(capture["downsample"]["viewport"]["width"])
        == viewport_extent_for_scissor(
            int(capture["downsample"]["scissor"]["width"])
        )
        and int(capture["downsample"]["viewport"]["height"])
        == viewport_extent_for_scissor(
            int(capture["downsample"]["scissor"]["height"])
        )
        for capture in captures
    )
    copy_input_matches_viewport = all(
        int(capture["downsample"]["copyBase"]["inputTexture"]["width"])
        == int(capture["downsample"]["viewport"]["width"])
        and int(
            capture["downsample"]["copyBase"]["inputTexture"]["height"]
        )
        == int(capture["downsample"]["viewport"]["height"])
        for capture in captures
    )
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
            "glassPath": capture["glassPath"],
            "downsampleScissor": [
                int(capture["downsample"]["scissor"]["width"]),
                int(capture["downsample"]["scissor"]["height"]),
            ],
            "downsampleViewport": int(
                capture["downsample"]["viewport"]["width"]
            ),
            "copyInputTexture": [
                int(
                    capture["downsample"]["copyBase"][
                        "inputTexture"
                    ]["width"]
                ),
                int(
                    capture["downsample"]["copyBase"][
                        "inputTexture"
                    ]["height"]
                ),
            ],
            "sourceTexture": [
                int(capture["sourceCrop"]["textureWidth"]),
                int(capture["sourceCrop"]["textureHeight"]),
            ],
            "sourceMipmapLevels": int(
                capture["sourceCrop"]["mipmapLevelCount"]
            ),
        }
        diameter = int(sample["diameter"])
        previous = centered_by_diameter.get(diameter)
        if previous is not None and previous != sample:
            raise ValueError(
                f"centered diameter {diameter} has conflicting policies"
            )
        centered_by_diameter[diameter] = sample
    centered_sizes = [
        centered_by_diameter[diameter]
        for diameter in sorted(centered_by_diameter)
    ]
    transition_fields = (
        "glassPath",
        "downsampleViewport",
        "copyInputTexture",
        "sourceTexture",
        "sourceMipmapLevels",
    )
    target_transitions = []
    for lower, upper in zip(centered_sizes, centered_sizes[1:]):
        changed = [
            field
            for field in transition_fields
            if lower[field] != upper[field]
        ]
        if not changed:
            continue
        target_transitions.append({
            "lowerDiameter": lower["diameter"],
            "upperDiameter": upper["diameter"],
            "adjacent": (
                int(upper["diameter"])
                == int(lower["diameter"]) + 1
            ),
            "changedFields": changed,
            "lowerPolicy": {
                field: lower[field]
                for field in transition_fields
            },
            "upperPolicy": {
                field: upper[field]
                for field in transition_fields
            },
        })
    path_order = {
        "no-bleed": 0,
        "regular-edge-bleed": 1,
    }
    monotonic_targets = all(
        (
            path_order[str(lower["glassPath"])]
            <= path_order[str(upper["glassPath"])]
            and int(lower["downsampleViewport"])
            <= int(upper["downsampleViewport"])
            and int(lower["sourceTexture"][0])
            <= int(upper["sourceTexture"][0])
            and int(lower["sourceMipmapLevels"])
            <= int(upper["sourceMipmapLevels"])
        )
        for lower, upper in zip(centered_sizes, centered_sizes[1:])
    )
    all_observed_transitions_adjacent = bool(
        target_transitions
    ) and all(
        transition["adjacent"]
        for transition in target_transitions
    )
    padding_unique = bool(padding_groups) and all(
        group["uniqueAcrossBothAxes"]
        for group in padding_groups
    )
    centered_target_policy_fully_bracketed = (
        monotonic_targets
        and all_observed_transitions_adjacent
    )
    geometry_crop_policy_fully_determined = (
        len(exact_no_bleed_models) == 1
        and len(small_geometry_padding) == 1
        and len(large_geometry_padding) == 1
    )
    duplicate_controls = duplicate_control_comparisons(captures)
    return {
        "liquidGlassGeometryPolicyAnalysisSchemaVersion": 1,
        "implementation": {
            "file": "analysis/liquid_glass_geometry_policy.py",
            "python": platform.python_version(),
        },
        "captures": captures,
        "snapPolicy": {
            "xCandidates": x_policies,
            "yCandidates": y_policies,
            "uniqueAcrossBothAxes": (
                len(x_policies) == 1
                and x_policies == y_policies
            ),
        },
        "pipelineAndTargetPolicy": {
            "centeredSizeSamples": centered_sizes,
            "observedTransitions": target_transitions,
            "monotonicAcrossCenteredSamples": monotonic_targets,
            "allObservedTransitionsAdjacent":
                all_observed_transitions_adjacent,
            "viewportIsScissorAlignedUpTo64AndCappedAt256":
                viewport_alignment_exact,
            "copyInputTextureMatchesViewport":
                copy_input_matches_viewport,
            "regularCaptureCount": len(regular_captures),
            "noBleedCaptureCount": len(no_bleed_captures),
            "selectionLawFullyDetermined":
                centered_target_policy_fully_bracketed,
        },
        "downsample": {
            "sourceCoordinatesAreFourTimesVertexPositions":
                downsample_scale_exact,
            "regularExpansionCandidateExactCounts":
                expansion_candidate_counts,
        },
        "duplicateFullCaptureControls": {
            "comparisons": duplicate_controls,
            "comparisonCount": len(duplicate_controls),
            "allExact": all(
                comparison["exact"]
                for comparison in duplicate_controls
            ),
        },
        "sourceCrop": {
            "allPathSpecificOriginsAndExtentsAligned":
                all_crops_aligned,
            "allOriginsRecoveredWithinTolerance":
                all_origins_recovered,
            "regularLargeOriginsAndExtents256Aligned":
                regular_large_crops_256_aligned,
            "regularSmallOriginsAndExtents128Aligned":
                regular_small_crops_128_aligned,
            "noBleedExtents64Aligned":
                no_bleed_extents_64_aligned,
            "noBleedOrigins64Aligned":
                no_bleed_origins_64_aligned,
            "noBleedOriginsAndExtents16Aligned":
                no_bleed_crops_16_aligned,
            "originExactlyRecoveredFromCopyBaseAndMVP":
                crop_algebra_exact,
            "symmetricPaddingCandidateModel":
                "one shared downsample-pixel padding around the observed "
                "source bounds before 256-pixel alignment",
            "paddingCandidatesInDownsamplePixels": {
                "x": padding_x_values,
                "y": padding_y_values,
                "common": common_padding,
                "fullResolution": [
                    value * SOURCE_DOWNSAMPLE
                    for value in common_padding
                ],
            },
            "paddingCandidatesByMipmapLevel": padding_groups,
            "symmetricPaddingCandidatesBySourceTarget":
                target_padding_groups,
            "geometryDrivenRegularCropModel": {
                "effectBounds":
                    "observed main bounds expanded by captured edge bleed "
                    "plus a 0.5-pixel raster guard, then clipped to the "
                    "window",
                "allocation":
                    "expand clipped effect bounds by the candidate "
                    "full-resolution padding",
                "smallSourceTargets": {
                    "maximumTextureDimension": 192,
                    "captureCount": len(small_regular_targets),
                    "originAndNominalEndAlignment": 128,
                    "virtualExtentAlignment": 256,
                    "law":
                        "align the padded minimum and maximum outward to "
                        "128, then round the resulting virtual extent up "
                        "to 256 while retaining the aligned origin",
                    "fullResolutionPaddingCandidates":
                        small_geometry_padding,
                    "unique": len(small_geometry_padding) == 1,
                },
                "largeSourceTargets": {
                    "minimumTextureDimension": 256,
                    "captureCount": len(large_regular_targets),
                    "originAndEndAlignment": 256,
                    "law":
                        "align the padded minimum and maximum outward to "
                        "256",
                    "fullResolutionPaddingCandidates":
                        large_geometry_padding,
                    "unique": len(large_geometry_padding) == 1,
                },
            },
            "geometryDrivenNoBleedCropModel": {
                "law":
                    "the lower tier subtracts 0.2 times the axis "
                    "dimension; the upper tier subtracts one quarter "
                    "dimension plus a 0.5-pixel raster guard; align down "
                    "to 16 and use a fixed 256-pixel virtual extent",
                "candidateTierSelectors": {
                    "scissor-33":
                        "select the upper tier independently per axis "
                        "when its downsample scissor extent is at least "
                        "33 pixels",
                    "diameter-48":
                        "select the upper tier on both axes from diameter "
                        "48",
                },
                "measurements": no_bleed_model_measurements,
                "exactCandidateTierSelectors":
                    exact_no_bleed_models,
                "tierSelectorUnique":
                    len(exact_no_bleed_models) == 1,
                "captureCount": len(no_bleed_captures),
                "exact": no_bleed_crop_model_exact,
            },
            "paddingUniqueForEveryMipmapLevel": padding_unique,
            "selectionLawFullyDetermined":
                geometry_crop_policy_fully_determined,
        },
        "conclusion": {
            "captureCount": len(captures),
            "fractionalCenterPolicyFullyDetermined": (
                len(x_policies) == 1
                and x_policies == y_policies
            ),
            "copyBaseCropOriginAlgebraExact": crop_algebra_exact,
            "cropPaddingObservationallyUnique": (
                len(small_geometry_padding) == 1
                and len(large_geometry_padding) == 1
            ),
            "symmetricSourceBoundsPaddingModelAccepted":
                padding_unique,
            "smallSizePipelinePolicyFullyDetermined":
                centered_target_policy_fully_bracketed,
            "sourceCropSelectionPolicyFullyDetermined":
                geometry_crop_policy_fully_determined,
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
    return 0 if (
        report["sourceCrop"][
            "allPathSpecificOriginsAndExtentsAligned"
        ]
        and report["sourceCrop"][
            "originExactlyRecoveredFromCopyBaseAndMVP"
        ]
    ) else 1


if __name__ == "__main__":
    raise SystemExit(main())
