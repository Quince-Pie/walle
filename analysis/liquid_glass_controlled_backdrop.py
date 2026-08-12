#!/usr/bin/env python3
"""Bit-gate Apple's controlled dynamic-backdrop producer and copy chain."""

import argparse
import hashlib
import json
import platform
import struct
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

import liquid_glass_dynamic_backdrop as dynamic
import liquid_glass_runtime_raster_coefficients as raster
from liquid_glass_backdrop_pyramid import (
    comparison,
    replay_copy_base_mip_software,
    replay_live_copy_base_software,
    unorm8,
)


type JsonObject = dict[str, Any]
type UInt8Array = NDArray[np.uint8]
type Vertex = tuple[float, float, float, float, float, float, float, float]

CONTROLLED_SIDE = 1_024
CHANNELS = 4
CONTROLLED_INPUT_SHA256 = (
    "3ac65697c38c44ed6332911c83e2f13a0b4b6958df49fa88365fbe6327cc1f88"
)
PRODUCER_FRAGMENTS = frozenset({"A2Xghfc", "TimgA2Xhfc_Isrc"})
PRODUCER_SHADER_PAIRS = {
    "A2Xghfc": "VfxXgh",
    "TimgA2Xhfc_Isrc": "VfxU10Xh",
}
WHITE_HALF_BITS = (0x3C00,) * CHANNELS
QUAD_INDICES = (0, 1, 2, 2, 3, 0)


def _mapping(value: object, name: str) -> JsonObject:
    if not isinstance(value, dict):
        raise ValueError(f"{name} is not an object")
    return value


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1 << 20):
            digest.update(block)
    return digest.hexdigest()


def _raw_path(snapshot: JsonObject, *, root: Path, name: str) -> Path:
    filename = snapshot.get("rawFile")
    if snapshot.get("rawCapture") is not True or not isinstance(filename, str):
        raise ValueError(f"{name} has no retained raw file")
    resolved_root = root.resolve()
    path = (resolved_root / filename).resolve()
    if not path.is_relative_to(resolved_root):
        raise ValueError(f"{name} raw path escapes the artifact")
    return path


def _raw_texture(
    snapshot: JsonObject,
    *,
    root: Path,
    name: str,
) -> tuple[UInt8Array, JsonObject]:
    width = snapshot.get("width")
    height = snapshot.get("height")
    if (
        not isinstance(width, int)
        or not isinstance(height, int)
        or width <= 0
        or height <= 0
        or snapshot.get("pixelFormat") != 80
        or snapshot.get("bytesPerRow") != width * CHANNELS
        or snapshot.get("rawBytes") != width * height * CHANNELS
    ):
        raise ValueError(f"{name} is not tightly packed BGRA8")
    path = _raw_path(snapshot, root=root, name=name)
    payload = path.read_bytes()
    if len(payload) != width * height * CHANNELS:
        raise ValueError(f"{name} raw byte count differs")
    values = np.frombuffer(payload, dtype=np.uint8).reshape(
        height,
        width,
        CHANNELS,
    )
    pixels = values.view("<u4").reshape(-1)
    return values, {
        "rawFile": str(path.relative_to(root.resolve())),
        "rawBytes": len(payload),
        "sha256": _sha256_bytes(payload),
        "uniqueBGRA8PixelCount": int(np.unique(pixels).size),
    }


def controlled_input() -> UInt8Array:
    """Construct the preregistered opaque coordinate-hash field."""

    y, x = np.mgrid[:CONTROLLED_SIDE, :CONTROLLED_SIDE]
    x = x.astype(np.uint32)
    y = y.astype(np.uint32)
    result = np.empty(
        (CONTROLLED_SIDE, CONTROLLED_SIDE, CHANNELS),
        dtype=np.uint8,
    )
    result[..., 0] = (37 * x + 17 * y + 13).astype(np.uint8)
    result[..., 1] = ((11 * x) ^ (29 * y) ^ 0x5A).astype(np.uint8)
    result[..., 2] = (3 * x + 5 * y + (x * y) % 251).astype(np.uint8)
    result[..., 3] = 255
    return result


def _sample_bgra8_linear(
    texture: UInt8Array,
    *,
    coordinates_x: NDArray[np.float32],
    coordinates_y: NDArray[np.float32],
) -> UInt8Array:
    """Replay Apple's measured RGBA8-UNORM bilinear sampler and store."""

    if (
        texture.ndim != 3
        or texture.shape[2] != CHANNELS
        or texture.dtype != np.uint8
        or coordinates_x.ndim != 1
        or coordinates_y.ndim != 1
    ):
        raise ValueError("invalid BGRA8 sampler input")
    height, width, _ = texture.shape
    position_x = coordinates_x * np.float32(width) - np.float32(0.5)
    position_y = coordinates_y * np.float32(height) - np.float32(0.5)
    floor_x = np.floor(position_x)
    floor_y = np.floor(position_y)
    origin_x = floor_x.astype(np.int64)
    origin_y = floor_y.astype(np.int64)
    fraction_x = position_x - floor_x
    fraction_y = position_y - floor_y
    weight_x = np.floor(fraction_x * np.float32(256) + np.float32(0.5)).astype(
        np.uint64
    )
    weight_y = np.floor(fraction_y * np.float32(256) + np.float32(0.5)).astype(
        np.uint64
    )
    inverse_x = 256 - weight_x
    inverse_y = 256 - weight_y

    x_zero = np.clip(origin_x, 0, width - 1)
    x_one = np.clip(origin_x + 1, 0, width - 1)
    y_zero = np.clip(origin_y, 0, height - 1)
    y_one = np.clip(origin_y + 1, 0, height - 1)
    codes = texture.astype(np.uint64)
    texels = np.stack(
        (
            codes[y_zero[:, None], x_zero[None, :]],
            codes[y_zero[:, None], x_one[None, :]],
            codes[y_one[:, None], x_zero[None, :]],
            codes[y_one[:, None], x_one[None, :]],
        ),
        axis=2,
    )
    weights = np.stack(
        (
            inverse_y[:, None] * inverse_x[None, :],
            inverse_y[:, None] * weight_x[None, :],
            weight_y[:, None] * inverse_x[None, :],
            weight_y[:, None] * weight_x[None, :],
        ),
        axis=2,
    )
    weighted_codes = (weights[..., None] * texels).sum(axis=2)

    # Apple reduces the exact 16.16 code-domain sum to sixteenths of one
    # UNORM8 code with midpoint ties upward, then converts once to binary16.
    fixed_sixteenths = (weighted_codes + np.uint64(2_048)) // np.uint64(4_096)
    sampled_half = (fixed_sixteenths.astype(np.float64) / (255 * 16)).astype(np.float16)
    # The BGRA8 render-target conversion is nearest-even in normalized space.
    return np.clip(
        np.rint(sampled_half.astype(np.float64) * 255),
        0,
        255,
    ).astype(np.uint8)


def _producer_index_payload(
    render: JsonObject,
    *,
    draw: JsonObject,
) -> tuple[int, ...]:
    draw_sequence = draw.get("sequence")
    snapshots = dynamic._buffer_snapshots(render)
    snapshot = dynamic._single(
        [
            item
            for item in snapshots
            if item.get("stage") == "index" and item.get("sequence") == draw_sequence
        ],
        "producer index snapshot",
    )
    count = draw.get("indexCount")
    if not isinstance(count, int) or count <= 0:
        raise ValueError("producer draw has no indices")
    payload = dynamic._payload(snapshot)
    if len(payload) < count * 2:
        raise ValueError("producer index snapshot is truncated")
    return struct.unpack_from(f"<{count}H", payload)


def _validate_quad_indices(
    indices: tuple[int, ...],
    *,
    vertex_count: int,
) -> None:
    if vertex_count not in {4, 16} or len(indices) != 6 * (vertex_count // 4):
        raise ValueError("producer mesh does not contain complete quads")
    expected = tuple(
        base + index for base in range(0, vertex_count, 4) for index in QUAD_INDICES
    )
    if indices != expected:
        raise ValueError("producer mesh index topology differs")


def _producer_vertex_payload(
    render: JsonObject,
    *,
    render_pass: JsonObject,
    draw: JsonObject,
) -> bytes:
    pass_sequence = int(render_pass["sequence"])
    draw_sequence = int(draw["sequence"])
    snapshot = dynamic._single(
        [
            item
            for item in dynamic._buffer_snapshots(render)
            if item.get("stage") == "vertex"
            and item.get("index") == 1
            and pass_sequence < int(item.get("sequence", -1)) < draw_sequence
        ],
        "producer vertex snapshot",
    )
    return dynamic._payload(snapshot)


def _vertex_normalization(
    render: JsonObject,
    *,
    render_pass: JsonObject,
    draw: JsonObject,
) -> tuple[float, float]:
    pass_sequence = int(render_pass["sequence"])
    draw_sequence = int(draw["sequence"])
    snapshot = dynamic._single(
        [
            item
            for item in dynamic._buffer_snapshots(render)
            if item.get("stage") == "vertex"
            and item.get("index") == 3
            and pass_sequence < int(item.get("sequence", -1)) < draw_sequence
        ],
        "producer texture normalization snapshot",
    )
    payload = dynamic._payload(snapshot)
    if len(payload) < 8:
        raise ValueError("producer texture normalization is truncated")
    result = struct.unpack_from("<2f", payload)
    expected = np.float32(1 / CONTROLLED_SIDE)
    if any(
        dynamic.float32_bits(value) != dynamic.float32_bits(float(expected))
        for value in result
    ):
        raise ValueError("producer texture normalization differs")
    return result


def _normalized_quad_vertices(
    vertices: list[Vertex],
    *,
    normalization: tuple[float, float],
) -> list[list[float]]:
    result: list[list[float]] = []
    for index in QUAD_INDICES:
        values = list(vertices[index])
        values[4] = float(
            np.float32(np.float32(values[4]) * np.float32(normalization[0]))
        )
        values[5] = float(
            np.float32(np.float32(values[5]) * np.float32(normalization[1]))
        )
        # The runtime raster model accepts four channels. The producer uses
        # only the separable U/V pair, so duplicate it into the unused pair.
        values[6] = values[4]
        values[7] = values[5]
        result.append(values)
    return result


def _draw_producer_quad(
    target: UInt8Array,
    source: UInt8Array,
    vertices: list[Vertex],
    *,
    mvp: tuple[float, ...],
    viewport: tuple[float, float, float, float],
    scissor: tuple[int, int, int, int],
    normalization: tuple[float, float],
    selector_table: tuple[int, ...],
    name: str,
) -> JsonObject:
    quad = raster.runtime_quad_from_vertices(
        _normalized_quad_vertices(
            vertices,
            normalization=normalization,
        ),
        name=name,
        mvp_payload=struct.pack("<16f", *mvp),
        viewport=viewport,
    )
    left, bottom, right, top = raster.visible_pixel_bounds(quad.case)
    scissor_x, scissor_y, scissor_width, scissor_height = scissor
    draw_left = max(left, scissor_x, 0)
    draw_bottom = max(bottom, scissor_y, 0)
    draw_right = min(right, scissor_x + scissor_width, target.shape[1])
    draw_top = min(top, scissor_y + scissor_height, target.shape[0])
    if draw_left >= draw_right or draw_bottom >= draw_top:
        return {
            "rasterBounds": [left, bottom, right, top],
            "clippedBounds": [draw_left, draw_bottom, draw_right, draw_top],
            "coveredPixelCount": 0,
        }

    x_coordinates = np.arange(draw_left, draw_right, dtype=np.uint32)
    y_coordinates = np.arange(draw_bottom, draw_top, dtype=np.uint32)
    grid_x, grid_y = np.meshgrid(x_coordinates, y_coordinates)
    primitive = raster.primitive_ids(quad, grid_x, grid_y)
    block = np.empty(
        (draw_top - draw_bottom, draw_right - draw_left, CHANNELS),
        dtype=np.uint8,
    )
    for primitive_index in (0, 1):
        coordinate_x = raster.coordinate_axis_bits(
            quad,
            channel=0,
            primitive=primitive_index,
            coordinates=range(draw_left, draw_right),
            selector_table=selector_table,
        ).view(np.float32)
        coordinate_y = raster.coordinate_axis_bits(
            quad,
            channel=1,
            primitive=primitive_index,
            coordinates=range(draw_bottom, draw_top),
            selector_table=selector_table,
        ).view(np.float32)
        sampled = _sample_bgra8_linear(
            source,
            coordinates_x=coordinate_x,
            coordinates_y=coordinate_y,
        )
        selected = primitive == primitive_index
        block[selected] = sampled[selected]
    target[draw_bottom:draw_top, draw_left:draw_right] = block
    return {
        "rasterBounds": [left, bottom, right, top],
        "clippedBounds": [draw_left, draw_bottom, draw_right, draw_top],
        "coveredPixelCount": int(block.shape[0] * block.shape[1]),
        "ascendingDiagonal": quad.ascendingDiagonal,
    }


def replay_producer(
    source: UInt8Array,
    *,
    destination_width: int,
    destination_height: int,
    vertices: list[Vertex],
    indices: tuple[int, ...],
    mvp: tuple[float, ...],
    viewport: tuple[float, float, float, float],
    scissor: tuple[int, int, int, int],
    normalization: tuple[float, float],
    selector_table: tuple[int, ...] | None = None,
    name: str = "dynamic-backdrop-producer",
) -> tuple[UInt8Array, list[JsonObject]]:
    """Replay the direct-sample producer, including its boundary quads."""

    _validate_quad_indices(indices, vertex_count=len(vertices))
    if (
        destination_width <= 0
        or destination_height <= 0
        or len(mvp) != 16
        or viewport[2:] != (destination_width, destination_height)
    ):
        raise ValueError("invalid producer target geometry")
    selectors = selector_table or raster.arithmetic.load_selector_table()
    target = np.zeros(
        (destination_height, destination_width, CHANNELS),
        dtype=np.uint8,
    )
    reports: list[JsonObject] = []
    for quad_index, start in enumerate(range(0, len(vertices), 4)):
        reports.append(
            _draw_producer_quad(
                target,
                source,
                vertices[start : start + 4],
                mvp=mvp,
                viewport=viewport,
                scissor=scissor,
                normalization=normalization,
                selector_table=selectors,
                name=f"{name}-quad-{quad_index}",
            )
        )
    return target, reports


def _texture_descriptor(record: JsonObject) -> JsonObject:
    return dynamic._texture(record)


def _producer_pass(
    render: JsonObject,
    *,
    output_address: str,
) -> JsonObject:
    return dynamic._single(
        [
            record
            for record in dynamic._records(render)
            if record.get("kind") == "renderPass"
            and dynamic._render_attachment_address(record) == output_address
        ],
        "dynamic producer render pass",
    )


def _copy_base_records(
    render: JsonObject,
) -> tuple[JsonObject, JsonObject, JsonObject]:
    records = dynamic._records(render)
    snapshots = dynamic._buffer_snapshots(render)
    source = dynamic._single(
        [
            record
            for record in records
            if record.get("kind") == "texture"
            and record.get("stage") == "compute"
            and record.get("index") == 0
            and dynamic._pipeline_label(record) == dynamic.COPY_BASE_PIPELINE
        ],
        "copy-base source",
    )
    destination = dynamic._single(
        [
            record
            for record in records
            if record.get("kind") == "texture"
            and record.get("stage") == "compute"
            and record.get("index") == 1
            and dynamic._pipeline_label(record) == dynamic.COPY_BASE_PIPELINE
        ],
        "copy-base destination",
    )
    uniform = dynamic._single(
        [
            snapshot
            for snapshot in snapshots
            if snapshot.get("stage") == "compute"
            and snapshot.get("index") == 0
            and dynamic._pipeline_label(snapshot) == dynamic.COPY_BASE_PIPELINE
        ],
        "copy-base uniform",
    )
    return source, destination, uniform


def _backdrop_pyramid_snapshot(render: JsonObject) -> JsonObject:
    return dynamic._single(
        [
            snapshot
            for snapshot in dynamic._texture_snapshots(render)
            if snapshot.get("pixelFormat") == 80
            and isinstance(snapshot.get("mipmapLevelCount"), int)
            and snapshot["mipmapLevelCount"] >= 2
            and snapshot.get("index") == 3
            and dynamic._pipeline_fragment(snapshot).startswith("glass_background")
        ],
        "complete glass backdrop pyramid",
    )


def _viewport_and_scissor(
    render: JsonObject,
    *,
    render_pass: JsonObject,
    destination_width: int,
    destination_height: int,
) -> tuple[
    tuple[float, float, float, float],
    tuple[int, int, int, int],
]:
    encoder = render_pass.get("encoder")
    records = dynamic._records(render)
    viewport_record = dynamic._single(
        [
            record
            for record in records
            if record.get("encoder") == encoder and record.get("kind") == "viewport"
        ],
        "producer viewport",
    )
    scissor_record = dynamic._single(
        [
            record
            for record in records
            if record.get("encoder") == encoder and record.get("kind") == "scissorRect"
        ],
        "producer scissor",
    )
    viewport = tuple(
        float(viewport_record[name])
        for name in ("originX", "originY", "width", "height")
    )
    scissor = tuple(int(scissor_record[name]) for name in ("x", "y", "width", "height"))
    if viewport != (0.0, 0.0, destination_width, destination_height):
        raise ValueError("producer viewport differs from its attachment")
    if (
        scissor[0] < 0
        or scissor[1] < 0
        or scissor[2] <= 0
        or scissor[3] <= 0
        or scissor[0] + scissor[2] > destination_width
        or scissor[1] + scissor[3] > destination_height
    ):
        raise ValueError("producer scissor exceeds its attachment")
    return viewport, scissor


def _validate_vertex_colors(payload: bytes, *, vertex_count: int) -> None:
    required = vertex_count * dynamic.VERTEX_STRIDE
    if len(payload) < required:
        raise ValueError("producer vertex payload is truncated")
    colors = [
        struct.unpack_from("<4H", payload, index * dynamic.VERTEX_STRIDE + 32)
        for index in range(vertex_count)
    ]
    if any(color != WHITE_HALF_BITS for color in colors):
        raise ValueError("producer vertex color modulates the controlled input")


def _mip_replay(
    base: UInt8Array,
    pyramid: JsonObject,
    *,
    root: Path,
) -> tuple[list[JsonObject], int]:
    untyped_levels = pyramid.get("mipSnapshots")
    if not isinstance(untyped_levels, list):
        raise ValueError("backdrop pyramid has no mip snapshots")
    levels = sorted(
        (_mapping(level, "backdrop mip") for level in untyped_levels),
        key=lambda level: int(level["level"]),
    )
    if [level.get("level") for level in levels] != list(range(len(levels))):
        raise ValueError("backdrop mip levels are not contiguous")
    if len(levels) != pyramid.get("mipmapLevelCount"):
        raise ValueError("backdrop mip count differs")

    current = base
    reports: list[JsonObject] = []
    mismatches = 0
    for level in levels[1:]:
        predicted = unorm8(replay_copy_base_mip_software(current))
        actual, summary = _raw_texture(
            {**level, "pixelFormat": pyramid.get("pixelFormat")},
            root=root,
            name=f"backdrop mip {level['level']}",
        )
        metrics = comparison(predicted, actual)
        mismatches += int(metrics["mismatchedBytes"])
        reports.append(
            {
                "level": level["level"],
                "extent": [actual.shape[1], actual.shape[0]],
                "raw": summary,
                "comparison": metrics,
            }
        )
        current = predicted
    return reports, mismatches


def _analyze_state(
    report: JsonObject,
    record: JsonObject,
    *,
    root: Path,
    selector_table: tuple[int, ...],
) -> JsonObject:
    sample_index = record.get("sampleIndex")
    remaining = record.get("remaining")
    if not isinstance(sample_index, int) or not isinstance(remaining, (int, float)):
        raise ValueError("dynamic backdrop state metadata is incomplete")
    render = _mapping(record.get("render"), "dynamic backdrop render")
    evidence = _mapping(
        render.get("dynamicBackdropProducerBoundary"),
        "dynamicBackdropProducerBoundary",
    )
    boundaries = evidence.get("records")
    if (
        evidence.get("schemaVersion") != 2
        or evidence.get("boundaryCount") != 1
        or not isinstance(boundaries, list)
        or len(boundaries) != 1
    ):
        raise ValueError("controlled producer boundary is incomplete")
    boundary = _mapping(boundaries[0], "controlled producer boundary")
    intervention = _mapping(
        boundary.get("inputIntervention"),
        "controlled producer intervention",
    )
    if (
        intervention.get("name") != "opaque-coordinate-hash-v1"
        or intervention.get("sha256") != CONTROLLED_INPUT_SHA256
        or intervention.get("applied") is not True
        or boundary.get("capturePoint")
        != "controlled-input-before-producer-draw-and-blit-after-"
        "producer-render-before-copy-base-compute"
    ):
        raise ValueError("controlled producer intervention differs")

    input_snapshot = _mapping(boundary.get("input"), "producer input snapshot")
    output_snapshot = _mapping(boundary.get("output"), "producer output snapshot")
    producer_input, input_summary = _raw_texture(
        input_snapshot,
        root=root,
        name=f"sample {sample_index} producer input",
    )
    if input_summary["sha256"] != CONTROLLED_INPUT_SHA256 or not np.array_equal(
        producer_input,
        controlled_input(),
    ):
        raise ValueError("controlled producer input bytes differ")
    producer_output, output_summary = _raw_texture(
        output_snapshot,
        root=root,
        name=f"sample {sample_index} producer output",
    )

    copy_source, copy_destination, copy_uniform_snapshot = _copy_base_records(render)
    source_descriptor = _texture_descriptor(copy_source)
    destination_descriptor = _texture_descriptor(copy_destination)
    output_address = boundary.get("producerOutputAddress")
    if (
        not isinstance(output_address, str)
        or source_descriptor.get("address") != output_address
        or source_descriptor.get("width") != producer_output.shape[1]
        or source_descriptor.get("height") != producer_output.shape[0]
    ):
        raise ValueError("producer output/copy-base source join differs")
    render_pass = _producer_pass(render, output_address=output_address)
    pipeline, vertices, mvp, draw = dynamic._draw_vertices(
        render,
        render_pass=render_pass,
    )
    creation = _mapping(
        _mapping(pipeline.get("pipeline"), "producer pipeline").get(
            "creationDescriptor"
        ),
        "producer pipeline descriptor",
    )
    fragment = creation.get("fragmentFunction")
    if (
        fragment not in PRODUCER_FRAGMENTS
        or creation.get("vertexFunction") != PRODUCER_SHADER_PAIRS[fragment]
    ):
        raise ValueError("producer shader pair differs")
    indices = _producer_index_payload(render, draw=draw)
    _validate_quad_indices(indices, vertex_count=len(vertices))
    vertex_payload = _producer_vertex_payload(
        render,
        render_pass=render_pass,
        draw=draw,
    )
    # A2X consumes an explicit white half4 vertex color. The first Timg draw
    # uses a different source path whose retained color bytes are not stable;
    # its complete direct-sample behavior is still gated by the output bytes.
    if fragment == "A2Xghfc":
        _validate_vertex_colors(vertex_payload, vertex_count=len(vertices))
    normalization = _vertex_normalization(
        render,
        render_pass=render_pass,
        draw=draw,
    )
    viewport, scissor = _viewport_and_scissor(
        render,
        render_pass=render_pass,
        destination_width=producer_output.shape[1],
        destination_height=producer_output.shape[0],
    )
    predicted_producer, quad_reports = replay_producer(
        producer_input,
        destination_width=producer_output.shape[1],
        destination_height=producer_output.shape[0],
        vertices=vertices,
        indices=indices,
        mvp=mvp,
        viewport=viewport,
        scissor=scissor,
        normalization=normalization,
        selector_table=selector_table,
        name=f"dynamic-backdrop-producer-{sample_index}",
    )
    producer_comparison = comparison(predicted_producer, producer_output)

    copy_uniform = dynamic.decode_copy_base_uniform(
        dynamic._payload(copy_uniform_snapshot)
    )
    destination_extent = tuple(
        int(value) for value in copy_uniform["destinationLevel0Size"]
    )
    if destination_extent != (
        destination_descriptor.get("width"),
        destination_descriptor.get("height"),
    ):
        raise ValueError("copy-base destination extent differs")
    pyramid = _backdrop_pyramid_snapshot(render)
    glass_fragment = dynamic._glass_fragment(render)
    glass_binding = dynamic._glass_source_binding(render, glass_fragment)
    glass_descriptor = _texture_descriptor(glass_binding)
    if (
        pyramid.get("width") != destination_extent[0]
        or pyramid.get("height") != destination_extent[1]
        or pyramid.get("sequence") != glass_binding.get("sequence")
        or glass_descriptor.get("address") != destination_descriptor.get("address")
    ):
        raise ValueError("copy-base destination/backdrop pyramid join differs")
    actual_copy_base, copy_base_summary = _raw_texture(
        pyramid,
        root=root,
        name=f"sample {sample_index} backdrop mip zero",
    )
    predicted_copy_base = replay_live_copy_base_software(
        predicted_producer,
        destination_width=destination_extent[0],
        destination_height=destination_extent[1],
        base_x=int(copy_uniform["textureCoordinateBase"][0]),
        base_y=int(copy_uniform["textureCoordinateBase"][1]),
        clamp=tuple(int(value) for value in copy_uniform["textureCoordinateClamp"]),
    )
    copy_base_comparison = comparison(predicted_copy_base, actual_copy_base)
    mip_reports, mip_mismatches = _mip_replay(
        predicted_copy_base,
        pyramid,
        root=root,
    )

    crop = dynamic.recover_crop_origin(
        mvp,
        width=producer_output.shape[1],
        height=producer_output.shape[0],
    )
    crop_origin = [int(value) for value in crop["origin"]]
    copy_offset = [int(value) for value in copy_uniform["textureCoordinateBase"]]
    runtime_scale = dynamic._backdrop_scale(report, sample_index)
    return {
        "sampleIndex": sample_index,
        "remaining": float(remaining),
        "runtimeScale": runtime_scale,
        "dynamicResamplingScaleQ": 1.0 / runtime_scale,
        "producer": {
            "fragmentFunction": fragment,
            "whiteVertexColorVerified": fragment == "A2Xghfc",
            "extent": [producer_output.shape[1], producer_output.shape[0]],
            "vertexCount": len(vertices),
            "indexCount": len(indices),
            "quadCount": len(vertices) // 4,
            "viewport": list(viewport),
            "scissor": list(scissor),
            "cropOrigin": crop_origin,
            "input": input_summary,
            "output": output_summary,
            "quads": quad_reports,
            "comparison": producer_comparison,
        },
        "copyBase": {
            "destinationExtent": list(destination_extent),
            "textureCoordinateBase": copy_offset,
            "textureCoordinateClamp": copy_uniform["textureCoordinateClamp"],
            "effectiveOrigin": [
                crop_origin[0] + copy_offset[0],
                crop_origin[1] + copy_offset[1],
            ],
            "output": copy_base_summary,
            "comparison": copy_base_comparison,
        },
        "mips": mip_reports,
        "mipMismatchedBytes": mip_mismatches,
    }


def _implementation_hashes() -> JsonObject:
    modules = {
        "controlledBackdrop": Path(__file__),
        "dynamicBackdropParser": Path(dynamic.__file__),
        "runtimeRaster": Path(raster.__file__),
        "rasterCoefficientBase": Path(raster.coefficient_base.__file__),
        "rasterCoefficientV3": Path(raster.coefficients.__file__),
        "rasterIterator": Path(raster.iterator.__file__),
        "rasterSelector": Path(raster.arithmetic.__file__),
        "rasterSelectorV4": Path(raster.composite.__file__),
    }
    return {
        name: {
            "file": str(path.resolve()),
            "sha256": _sha256_file(path),
        }
        for name, path in modules.items()
    }


def analyze(path: Path) -> JsonObject:
    report_path = path / "transition-timeline.json" if path.is_dir() else path
    root = report_path.parent
    report = json.loads(report_path.read_text(encoding="utf-8"))
    dynamic_uniforms = _mapping(
        report.get("dynamicBackgroundUniforms"),
        "dynamicBackgroundUniforms",
    )
    records = dynamic_uniforms.get("records")
    if (
        dynamic_uniforms.get("schemaVersion") != 7
        or not isinstance(records, list)
        or not records
        or not all(isinstance(record, dict) for record in records)
    ):
        raise ValueError("controlled dynamic backdrop records are incomplete")
    selector_table = raster.arithmetic.load_selector_table()
    states = [
        _analyze_state(
            report,
            record,
            root=root,
            selector_table=selector_table,
        )
        for record in records
    ]
    producer_bytes = sum(
        int(state["producer"]["comparison"]["observedBytes"]) for state in states
    )
    producer_mismatches = sum(
        int(state["producer"]["comparison"]["mismatchedBytes"]) for state in states
    )
    copy_bytes = sum(
        int(state["copyBase"]["comparison"]["observedBytes"]) for state in states
    )
    copy_mismatches = sum(
        int(state["copyBase"]["comparison"]["mismatchedBytes"]) for state in states
    )
    mip_bytes = sum(
        int(mip["comparison"]["observedBytes"])
        for state in states
        for mip in state["mips"]
    )
    mip_mismatches = sum(int(state["mipMismatchedBytes"]) for state in states)
    all_exact = not (producer_mismatches or copy_mismatches or mip_mismatches)
    return {
        "liquidGlassControlledBackdropAnalysisSchemaVersion": 1,
        "artifact": str(root.resolve()),
        "timeline": str(report_path.resolve()),
        "timelineSHA256": _sha256_file(report_path),
        "implementation": {
            "python": platform.python_version(),
            "dependencies": _implementation_hashes(),
        },
        "scope": {
            "material": report.get("material"),
            "appearance": report.get("appearance"),
            "direction": report.get("direction"),
            "controlledInput": "opaque-coordinate-hash-v1",
            "stateCount": len(states),
            "sampleIndices": [state["sampleIndex"] for state in states],
            "openedDiscoveryCalibration": True,
            "prospectiveHoldout": False,
        },
        "states": states,
        "aggregate": {
            "producerObservedBytes": producer_bytes,
            "producerMismatchedBytes": producer_mismatches,
            "producerExactEveryState": producer_mismatches == 0,
            "copyBaseObservedBytes": copy_bytes,
            "copyBaseMismatchedBytes": copy_mismatches,
            "copyBaseExactEveryState": copy_mismatches == 0,
            "mipObservedBytes": mip_bytes,
            "mipMismatchedBytes": mip_mismatches,
            "mipExactEveryState": mip_mismatches == 0,
        },
        "conclusion": {
            "capturedMeshProducerReplayBitExact": producer_mismatches == 0,
            "copyBaseReplayBitExact": copy_mismatches == 0,
            "generatedMipReplayBitExact": mip_mismatches == 0,
            "controlledDynamicBackdropChainBitExact": all_exact,
            "independentCropAllocationPolicyRecovered": False,
            "prospectiveSeededInputHoldoutPassed": False,
            "productionShaderAuthorized": False,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture", type=Path)
    parser.add_argument("-o", "--output", type=Path)
    arguments = parser.parse_args()
    result = analyze(arguments.capture)
    encoded = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if arguments.output is None:
        print(encoded, end="")
    else:
        arguments.output.write_text(encoded, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
