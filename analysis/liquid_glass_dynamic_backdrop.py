#!/usr/bin/env python3
"""Audit Apple's transition backdrop producer, crop, and source mapping."""

import argparse
import hashlib
import json
import platform
import struct
from pathlib import Path
from typing import Any

from liquid_glass_profile_matrix import decode_profile


type JsonObject = dict[str, Any]
type Vertex = tuple[float, float, float, float, float, float, float, float]

COPY_BASE_PIPELINE = "com.apple.coreanimation.variable_blur_copy_base_mip_compute"
GLASS_FRAGMENT_PREFIX = "glass_background_sdf"
BACKDROP_LAYER_PATH = (1, 0, 1, 0)
VERTEX_STRIDE = 48
MAIN_VERTEX_COUNT = 6
SHADOW_VERTEX_COUNT = 16


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1 << 20):
            digest.update(block)
    return digest.hexdigest()


def float32(value: float) -> float:
    return struct.unpack("<f", struct.pack("<f", value))[0]


def float32_bits(value: float) -> int:
    return struct.unpack("<I", struct.pack("<f", value))[0]


def _ordered_float32(bits: int) -> int:
    return 0x8000_0000 - bits if bits & 0x8000_0000 else 0x8000_0000 + bits


def float32_ulp_distance(left: float, right: float) -> int:
    left_bits = float32_bits(left)
    right_bits = float32_bits(right)
    return abs(_ordered_float32(left_bits) - _ordered_float32(right_bits))


def _single(records: list[JsonObject], description: str) -> JsonObject:
    if len(records) != 1:
        raise ValueError(f"expected one {description}; found {len(records)}")
    return records[0]


def _pipeline_label(record: JsonObject) -> str:
    pipeline = record.get("pipeline")
    if not isinstance(pipeline, dict):
        return ""
    label = pipeline.get("label")
    return label if isinstance(label, str) else ""


def _pipeline_fragment(record: JsonObject) -> str:
    pipeline = record.get("pipeline")
    if not isinstance(pipeline, dict):
        return ""
    descriptor = pipeline.get("creationDescriptor")
    if not isinstance(descriptor, dict):
        return ""
    fragment = descriptor.get("fragmentFunction")
    return fragment if isinstance(fragment, str) else ""


def _payload(record: JsonObject) -> bytes:
    payload = record.get("payload")
    encoded = payload.get("hex") if isinstance(payload, dict) else None
    if not isinstance(encoded, str):
        raise ValueError("captured buffer has no hexadecimal payload")
    value = bytes.fromhex(encoded)
    length = payload.get("lengthBytes")
    if isinstance(length, int) and length != len(value):
        raise ValueError("captured payload length differs from its metadata")
    return value


def _records(render: JsonObject) -> list[JsonObject]:
    probe = render.get("metalUniformProbe")
    records = probe.get("records") if isinstance(probe, dict) else None
    if not isinstance(records, list) or not all(
        isinstance(record, dict) for record in records
    ):
        raise ValueError("Metal uniform records are incomplete")
    return records


def _buffer_snapshots(render: JsonObject) -> list[JsonObject]:
    evidence = render.get("metalBufferSnapshots")
    snapshots = evidence.get("snapshots") if isinstance(evidence, dict) else None
    if not isinstance(snapshots, list) or not all(
        isinstance(snapshot, dict) for snapshot in snapshots
    ):
        raise ValueError("Metal buffer snapshots are incomplete")
    return snapshots


def _texture_snapshots(render: JsonObject) -> list[JsonObject]:
    evidence = render.get("metalTextureSnapshots")
    snapshots = evidence.get("snapshots") if isinstance(evidence, dict) else None
    if not isinstance(snapshots, list) or not all(
        isinstance(snapshot, dict) for snapshot in snapshots
    ):
        raise ValueError("Metal texture snapshots are incomplete")
    return snapshots


def decode_copy_base_uniform(payload: bytes) -> JsonObject:
    if len(payload) < 32:
        raise ValueError("copy-base uniform payload is shorter than 32 bytes")
    return {
        "textureCoordinateBase": list(struct.unpack_from("<2h", payload, 0)),
        "textureCoordinateClamp": list(struct.unpack_from("<4h", payload, 8)),
        "destinationLevel0Size": list(struct.unpack_from("<2H", payload, 16)),
        "destinationLevel1Size": list(struct.unpack_from("<2H", payload, 20)),
        "destinationLevel1": struct.unpack_from("<H", payload, 24)[0],
        "noBaseMip": bool(payload[26]),
    }


def recover_crop_origin(
    mvp: tuple[float, ...],
    *,
    width: int,
    height: int,
) -> JsonObject:
    if len(mvp) != 16 or width <= 0 or height <= 0:
        raise ValueError("invalid producer MVP or extent")
    raw_x = -(mvp[12] + 1.0) * width / 2.0
    raw_y = (mvp[13] - 1.0) * height / 2.0
    origin_x = round(raw_x)
    origin_y = round(raw_y)
    return {
        "origin": [origin_x, origin_y],
        "raw": [raw_x, raw_y],
        "maximumIntegralResidual": max(
            abs(raw_x - origin_x),
            abs(raw_y - origin_y),
        ),
        "orthographicScaleBitsExact": (
            float32_bits(mvp[0]) == float32_bits(2.0 / width)
            and float32_bits(mvp[5]) == float32_bits(-2.0 / height)
        ),
    }


def _texture(record: JsonObject) -> JsonObject:
    texture = record.get("texture")
    if isinstance(texture, dict):
        return texture
    if isinstance(record.get("address"), str):
        return {
            name: record.get(name)
            for name in (
                "address",
                "width",
                "height",
                "depth",
                "arrayLength",
                "mipmapLevelCount",
                "sampleCount",
                "pixelFormat",
                "textureType",
                "usage",
                "storageMode",
            )
        }
    raise ValueError("texture binding has no texture descriptor")


def _render_attachment_address(record: JsonObject) -> str | None:
    attachments = record.get("colorAttachments")
    if not isinstance(attachments, list):
        return None
    for attachment in attachments:
        if not isinstance(attachment, dict) or attachment.get("index") != 0:
            continue
        texture = attachment.get("texture")
        address = texture.get("address") if isinstance(texture, dict) else None
        return address if isinstance(address, str) else None
    return None


def _vertices(snapshot: JsonObject, count: int) -> list[Vertex]:
    payload = _payload(snapshot)
    required = count * VERTEX_STRIDE
    if len(payload) < required:
        raise ValueError(
            f"vertex payload has {len(payload)} bytes; expected {required}"
        )
    return [
        struct.unpack_from("<8f", payload, index * VERTEX_STRIDE)
        for index in range(count)
    ]


def _draw_vertices(
    render: JsonObject,
    *,
    render_pass: JsonObject,
) -> tuple[JsonObject, list[Vertex], tuple[float, ...], JsonObject]:
    records = _records(render)
    snapshots = _buffer_snapshots(render)
    encoder = render_pass.get("encoder")
    pass_sequence = int(render_pass["sequence"])
    draws = [
        record
        for record in records
        if record.get("encoder") == encoder
        and record.get("kind") == "drawIndexedPrimitives"
        and int(record.get("sequence", -1)) > pass_sequence
    ]
    draw = _single(draws, "producer indexed draw")
    draw_sequence = int(draw["sequence"])
    pipeline = _single(
        [
            record
            for record in records
            if record.get("encoder") == encoder
            and record.get("kind") == "pipeline"
            and pass_sequence < int(record.get("sequence", -1)) < draw_sequence
        ],
        "producer pipeline",
    )
    vertex_snapshot = _single(
        [
            snapshot
            for snapshot in snapshots
            if snapshot.get("stage") == "vertex"
            and snapshot.get("index") == 1
            and pass_sequence < int(snapshot.get("sequence", -1)) < draw_sequence
        ],
        "producer vertex buffer",
    )
    mvp_snapshot = _single(
        [
            snapshot
            for snapshot in snapshots
            if snapshot.get("stage") == "vertex"
            and snapshot.get("index") == 2
            and pass_sequence < int(snapshot.get("sequence", -1)) < draw_sequence
        ],
        "producer MVP buffer",
    )
    index_snapshot = _single(
        [
            snapshot
            for snapshot in snapshots
            if snapshot.get("stage") == "index"
            and int(snapshot.get("sequence", -1)) == draw_sequence
        ],
        "producer index buffer",
    )
    index_count = int(draw["indexCount"])
    index_payload = _payload(index_snapshot)
    if len(index_payload) < index_count * 2:
        raise ValueError("producer index payload is incomplete")
    indices = struct.unpack_from(f"<{index_count}H", index_payload)
    vertex_count = max(indices) + 1
    mvp_payload = _payload(mvp_snapshot)
    if len(mvp_payload) < 64:
        raise ValueError("producer MVP payload is incomplete")
    return (
        pipeline,
        _vertices(vertex_snapshot, vertex_count),
        struct.unpack_from("<16f", mvp_payload),
        draw,
    )


def _binding_raw_snapshot(
    render: JsonObject,
    binding: JsonObject,
) -> JsonObject | None:
    sequence = binding.get("sequence")
    index = binding.get("index")
    matches = [
        snapshot
        for snapshot in _texture_snapshots(render)
        if snapshot.get("sequence") == sequence and snapshot.get("index") == index
    ]
    if len(matches) > 1:
        raise ValueError("texture binding has multiple raw snapshots")
    return matches[0] if matches else None


def _raw_snapshot_summary(snapshot: JsonObject | None) -> JsonObject:
    if snapshot is None:
        return {"captured": False, "reason": "binding was not retained"}
    return {
        "captured": snapshot.get("rawCapture") is True,
        "rawFile": snapshot.get("rawFile"),
        "rawBytes": snapshot.get("rawBytes"),
        "width": snapshot.get("width"),
        "height": snapshot.get("height"),
        "pixelFormat": snapshot.get("pixelFormat"),
        "mipmapLevelCount": snapshot.get("mipmapLevelCount"),
    }


def _producer_boundary_snapshots(
    render: JsonObject,
    *,
    producer_input: JsonObject,
    copy_source: JsonObject,
) -> tuple[JsonObject | None, JsonObject | None]:
    evidence = render.get("dynamicBackdropProducerBoundary")
    if evidence is None:
        return (
            _binding_raw_snapshot(render, producer_input),
            _binding_raw_snapshot(render, copy_source),
        )
    if not isinstance(evidence, dict):
        raise ValueError("dynamic backdrop producer boundary is not an object")
    boundaries = evidence.get("records")
    if (
        evidence.get("schemaVersion") != 1
        or evidence.get("boundaryCount") != 1
        or not isinstance(boundaries, list)
        or len(boundaries) != 1
        or not isinstance(boundaries[0], dict)
    ):
        raise ValueError("dynamic backdrop producer boundary is incomplete")
    boundary = boundaries[0]
    input_snapshot = boundary.get("input")
    output_snapshot = boundary.get("output")
    if (
        boundary.get("capturePoint")
        != "blit-after-producer-render-before-copy-base-compute"
        or boundary.get("producerInputAddress")
        != _texture(producer_input).get("address")
        or boundary.get("producerInputBindingSequence")
        != producer_input.get("sequence")
        or boundary.get("producerOutputAddress") != _texture(copy_source).get("address")
        or boundary.get("copyBaseBindingSequence") != copy_source.get("sequence")
        or not isinstance(input_snapshot, dict)
        or not isinstance(output_snapshot, dict)
    ):
        raise ValueError("dynamic backdrop producer boundary join differs")
    return input_snapshot, output_snapshot


def _backdrop_scale(report: JsonObject, sample_index: int) -> float:
    samples = report.get("samples")
    if not isinstance(samples, list) or not 0 <= sample_index < len(samples):
        raise ValueError("timeline presentation samples are incomplete")
    sample = samples[sample_index]
    if not isinstance(sample, dict):
        raise ValueError("presentation sample is not an object")
    state = sample.get("presentationStateBeforeCapture")
    records = state.get("records") if isinstance(state, dict) else None
    if not isinstance(records, list):
        raise ValueError("presentation layer records are incomplete")
    matches = [
        record
        for record in records
        if isinstance(record, dict)
        and tuple(record.get("path", ())) == BACKDROP_LAYER_PATH
        and record.get("class") == "CABackdropLayer"
    ]
    layer = _single(matches, "presentation CABackdropLayer")
    known = layer.get("knownRuntimeValues")
    scale = known.get("scale") if isinstance(known, dict) else None
    if not isinstance(scale, (int, float)) or isinstance(scale, bool):
        raise ValueError("presentation CABackdropLayer has no numeric scale")
    return float(scale)


def _glass_fragment(render: JsonObject) -> str:
    fragments = {
        _pipeline_fragment(record)
        for record in _records(render)
        if record.get("kind") == "pipeline"
        and _pipeline_fragment(record).startswith(GLASS_FRAGMENT_PREFIX)
    }
    if len(fragments) != 1:
        raise ValueError(f"expected one glass fragment; found {sorted(fragments)}")
    return fragments.pop()


def _glass_geometry(
    render: JsonObject,
    fragment: str,
) -> tuple[list[Vertex], list[Vertex]]:
    snapshots = sorted(
        (
            snapshot
            for snapshot in _buffer_snapshots(render)
            if snapshot.get("stage") == "vertex"
            and snapshot.get("index") == 1
            and _pipeline_fragment(snapshot) == fragment
        ),
        key=lambda snapshot: int(snapshot["sequence"]),
    )
    if len(snapshots) != 2:
        raise ValueError(
            f"expected main and shadow glass vertex buffers; found {len(snapshots)}"
        )
    return (
        _vertices(snapshots[0], MAIN_VERTEX_COUNT),
        _vertices(snapshots[1], SHADOW_VERTEX_COUNT),
    )


def _glass_profile(render: JsonObject, fragment: str) -> JsonObject:
    bindings = render.get("glassFragmentUniformBindings")
    if not isinstance(bindings, list):
        raise ValueError("glass fragment uniform bindings are incomplete")
    matches = sorted(
        (
            binding
            for binding in bindings
            if isinstance(binding, dict) and _pipeline_fragment(binding) == fragment
        ),
        key=lambda binding: int(binding["sequence"]),
    )
    if len(matches) != 2:
        raise ValueError(f"expected two glass profile bindings; found {len(matches)}")
    return decode_profile(_payload(matches[0]))


def _glass_source_binding(render: JsonObject, fragment: str) -> JsonObject:
    return _single(
        [
            record
            for record in _records(render)
            if record.get("kind") == "texture"
            and record.get("stage") == "fragment"
            and record.get("index") == 3
            and _pipeline_fragment(record) == fragment
        ],
        "glass source texture binding",
    )


def _fma_then_divide_source(
    position: float,
    *,
    scale: float,
    origin: int,
    extent: int,
) -> float:
    numerator = float32(float32(position) * float32(scale) - float32(float(origin)))
    return float32(numerator / float32(float(extent)))


def _affine_fma_source(
    position: float,
    *,
    scale: float,
    origin: int,
    extent: int,
) -> float:
    slope = float32(scale / extent)
    intercept = float32(-origin / extent)
    return float32(float32(position) * slope + intercept)


def _source_coordinate_comparison(
    vertices: list[Vertex],
    *,
    scale: float,
    origin: tuple[int, int],
    extent: tuple[int, int],
) -> JsonObject:
    primary_mismatches = 0
    affine_mismatches = 0
    candidate_union_mismatches = 0
    maximum_origin_residual = 0.0
    components = 0
    for vertex in vertices:
        for axis in range(2):
            position = vertex[axis]
            actual = vertex[6 + axis]
            primary = _fma_then_divide_source(
                position,
                scale=scale,
                origin=origin[axis],
                extent=extent[axis],
            )
            affine = _affine_fma_source(
                position,
                scale=scale,
                origin=origin[axis],
                extent=extent[axis],
            )
            primary_exact = float32_bits(primary) == float32_bits(actual)
            affine_exact = float32_bits(affine) == float32_bits(actual)
            primary_mismatches += not primary_exact
            affine_mismatches += not affine_exact
            candidate_union_mismatches += not (primary_exact or affine_exact)
            recovered_origin = position * scale - actual * extent[axis]
            maximum_origin_residual = max(
                maximum_origin_residual,
                abs(recovered_origin - origin[axis]),
            )
            components += 1
    return {
        "componentCount": components,
        "fmaThenDivideMismatchedComponents": primary_mismatches,
        "affineFmaMismatchedComponents": affine_mismatches,
        "twoCandidateUnionMismatchedComponents": candidate_union_mismatches,
        "twoCandidateUnionExact": candidate_union_mismatches == 0,
        "maximumRecoveredOriginResidual": maximum_origin_residual,
    }


def _producer_scale_comparison(
    vertices: list[Vertex],
    *,
    inverse_scale: float,
) -> JsonObject:
    if len(vertices) < 4:
        raise ValueError("producer mesh has fewer than four primary vertices")
    mismatches = 0
    maximum_ulp = 0
    for vertex in vertices[:4]:
        for axis in range(2):
            predicted = float32(vertex[axis] * inverse_scale)
            actual = vertex[4 + axis]
            distance = float32_ulp_distance(predicted, actual)
            mismatches += distance != 0
            maximum_ulp = max(maximum_ulp, distance)
    return {
        "componentCount": 8,
        "roundedProductMismatchedComponents": mismatches,
        "maximumUlpDistance": maximum_ulp,
    }


def analyze_state(
    report: JsonObject,
    record: JsonObject,
) -> JsonObject:
    render = record.get("render")
    if not isinstance(render, dict) or render.get("executed") is not True:
        raise ValueError("dynamic CARenderer state did not execute")
    sample_index = int(record["sampleIndex"])
    remaining = float(record["remaining"])
    filter_record = record.get("filter")
    input_values = (
        filter_record.get("inputValues") if isinstance(filter_record, dict) else None
    )
    face_opacity = (
        input_values.get("inputFaceOpacity") if isinstance(input_values, dict) else None
    )
    if not isinstance(face_opacity, (int, float)) or isinstance(face_opacity, bool):
        raise ValueError("dynamic filter has no numeric face opacity")

    scale = _backdrop_scale(report, sample_index)
    expected_scale = 1.0 - remaining / 2.0
    inverse_scale = 1.0 / scale
    records = _records(render)
    snapshots = _buffer_snapshots(render)
    copy_source = _single(
        [
            item
            for item in records
            if item.get("kind") == "texture"
            and item.get("stage") == "compute"
            and item.get("index") == 0
            and _pipeline_label(item) == COPY_BASE_PIPELINE
        ],
        "copy-base source texture",
    )
    copy_destination = _single(
        [
            item
            for item in records
            if item.get("kind") == "texture"
            and item.get("stage") == "compute"
            and item.get("index") == 1
            and _pipeline_label(item) == COPY_BASE_PIPELINE
        ],
        "copy-base destination texture",
    )
    copy_snapshot = _single(
        [
            snapshot
            for snapshot in snapshots
            if snapshot.get("stage") == "compute"
            and snapshot.get("index") == 0
            and _pipeline_label(snapshot) == COPY_BASE_PIPELINE
        ],
        "copy-base uniform buffer",
    )
    copy_uniform = decode_copy_base_uniform(_payload(copy_snapshot))
    source_texture = _texture(copy_source)
    destination_texture = _texture(copy_destination)
    source_address = source_texture.get("address")
    if not isinstance(source_address, str):
        raise ValueError("copy-base source texture has no address")
    producer_pass = _single(
        [
            item
            for item in records
            if item.get("kind") == "renderPass"
            and _render_attachment_address(item) == source_address
        ],
        "render pass producing the copy-base source",
    )
    producer_pipeline, producer_vertices, producer_mvp, producer_draw = _draw_vertices(
        render, render_pass=producer_pass
    )
    producer_encoder = producer_pass.get("encoder")
    producer_input = _single(
        [
            item
            for item in records
            if item.get("kind") == "texture"
            and item.get("stage") == "fragment"
            and item.get("index") == 3
            and item.get("encoder") == producer_encoder
            and int(item.get("sequence", -1)) < int(producer_draw["sequence"])
        ],
        "producer source texture",
    )

    producer_extent = (
        int(source_texture["width"]),
        int(source_texture["height"]),
    )
    destination_extent = tuple(
        int(value) for value in copy_uniform["destinationLevel0Size"]
    )
    if destination_extent != (
        int(destination_texture["width"]),
        int(destination_texture["height"]),
    ):
        raise ValueError("copy-base uniform and destination texture extents differ")
    crop = recover_crop_origin(
        producer_mvp,
        width=producer_extent[0],
        height=producer_extent[1],
    )
    crop_origin = tuple(int(value) for value in crop["origin"])
    copy_offset = tuple(int(value) for value in copy_uniform["textureCoordinateBase"])
    effective_origin = (
        crop_origin[0] + copy_offset[0],
        crop_origin[1] + copy_offset[1],
    )

    fragment = _glass_fragment(render)
    main, shadow = _glass_geometry(render, fragment)
    profile = _glass_profile(render, fragment)
    displacement = profile["fields"]["displacement_matrix"]["values"]
    expected_displacement = (
        float32(scale / destination_extent[0]),
        float32(-scale / destination_extent[1]),
    )
    glass_binding = _glass_source_binding(render, fragment)
    glass_texture = _texture(glass_binding)
    if glass_texture.get("address") != destination_texture.get("address"):
        raise ValueError("copy-base destination is not the glass source texture")

    main_comparison = _source_coordinate_comparison(
        main,
        scale=scale,
        origin=effective_origin,
        extent=destination_extent,
    )
    shadow_comparison = _source_coordinate_comparison(
        shadow,
        scale=scale,
        origin=effective_origin,
        extent=destination_extent,
    )
    producer_input_snapshot, producer_output_snapshot = _producer_boundary_snapshots(
        render,
        producer_input=producer_input,
        copy_source=copy_source,
    )
    return {
        "sampleIndex": sample_index,
        "remaining": remaining,
        "inputFaceOpacity": float(face_opacity),
        "runtimeScale": scale,
        "expectedRuntimeScale": expected_scale,
        "runtimeScaleLawExact": (
            remaining == float(face_opacity) and scale == expected_scale
        ),
        "inverseRuntimeScaleQ": inverse_scale,
        "producer": {
            "pipelineLabel": _pipeline_label(producer_pipeline),
            "fragmentFunction": _pipeline_fragment(producer_pipeline),
            "outputExtent": list(producer_extent),
            "vertexCount": len(producer_vertices),
            "indexCount": int(producer_draw["indexCount"]),
            "crop": crop,
            "sourceScaleComparison": _producer_scale_comparison(
                producer_vertices,
                inverse_scale=inverse_scale,
            ),
            "inputTexture": {
                **{
                    name: _texture(producer_input).get(name)
                    for name in (
                        "width",
                        "height",
                        "pixelFormat",
                        "mipmapLevelCount",
                    )
                },
                "raw": _raw_snapshot_summary(producer_input_snapshot),
            },
            "outputTexture": {
                **{
                    name: source_texture.get(name)
                    for name in (
                        "width",
                        "height",
                        "pixelFormat",
                        "mipmapLevelCount",
                    )
                },
                "raw": _raw_snapshot_summary(producer_output_snapshot),
            },
        },
        "copyBase": {
            "uniform": copy_uniform,
            "producerCropOrigin": list(crop_origin),
            "copyOffset": list(copy_offset),
            "effectiveOrigin": list(effective_origin),
            "destinationExtent": list(destination_extent),
        },
        "glass": {
            "fragmentFunction": fragment,
            "sourceTextureAddressMatchesCopyDestination": True,
            "virtualExtent": [
                inverse_scale * destination_extent[0],
                inverse_scale * destination_extent[1],
            ],
            "displacementMatrixScaleBitsExact": (
                float32_bits(float(displacement[0]))
                == float32_bits(expected_displacement[0])
                and float32_bits(float(displacement[3]))
                == float32_bits(expected_displacement[1])
            ),
            "mainSourceCoordinates": main_comparison,
            "shadowSourceCoordinates": shadow_comparison,
        },
    }


def analyze(path: Path) -> JsonObject:
    report_path = path / "transition-timeline.json" if path.is_dir() else path
    report = json.loads(report_path.read_text(encoding="utf-8"))
    dynamic = report.get("dynamicBackgroundUniforms")
    records = dynamic.get("records") if isinstance(dynamic, dict) else None
    if not isinstance(records, list) or not records:
        raise ValueError("transition timeline has no dynamic background records")
    states = [
        analyze_state(report, record) for record in records if isinstance(record, dict)
    ]
    if len(states) != len(records):
        raise ValueError("dynamic background record is not an object")
    main_components = sum(
        int(state["glass"]["mainSourceCoordinates"]["componentCount"])
        for state in states
    )
    shadow_components = sum(
        int(state["glass"]["shadowSourceCoordinates"]["componentCount"])
        for state in states
    )
    primary_mismatches = sum(
        int(state["glass"][draw]["fmaThenDivideMismatchedComponents"])
        for state in states
        for draw in ("mainSourceCoordinates", "shadowSourceCoordinates")
    )
    candidate_union_mismatches = sum(
        int(state["glass"][draw]["twoCandidateUnionMismatchedComponents"])
        for state in states
        for draw in ("mainSourceCoordinates", "shadowSourceCoordinates")
    )
    producer_inputs_captured = sum(
        state["producer"]["inputTexture"]["raw"]["captured"] is True for state in states
    )
    producer_outputs_captured = sum(
        state["producer"]["outputTexture"]["raw"]["captured"] is True
        for state in states
    )
    return {
        "liquidGlassDynamicBackdropAnalysisSchemaVersion": 1,
        "implementation": {
            "file": "analysis/liquid_glass_dynamic_backdrop.py",
            "python": platform.python_version(),
        },
        "artifact": str(report_path.parent),
        "timeline": str(report_path),
        "timelineSHA256": sha256_file(report_path),
        "material": report.get("material"),
        "appearance": report.get("appearance"),
        "stateCount": len(states),
        "states": states,
        "aggregate": {
            "runtimeScaleLawExactEveryState": all(
                state["runtimeScaleLawExact"] for state in states
            ),
            "producerOrthographicScaleBitsExactEveryState": all(
                state["producer"]["crop"]["orthographicScaleBitsExact"]
                for state in states
            ),
            "displacementMatrixScaleBitsExactEveryState": all(
                state["glass"]["displacementMatrixScaleBitsExact"] for state in states
            ),
            "mainSourceComponentCount": main_components,
            "shadowSourceComponentCount": shadow_components,
            "fmaThenDivideMismatchedSourceComponents": primary_mismatches,
            "twoCandidateUnionMismatchedSourceComponents": (candidate_union_mismatches),
            "producerInputRawStateCount": producer_inputs_captured,
            "producerOutputRawStateCount": producer_outputs_captured,
        },
        "conclusion": {
            "dynamicResamplingAndCropAlgebraRecovered": True,
            "exactSourceCoordinateStagingRecovered": False,
            "independentCropAllocationPolicyRecovered": False,
            "producerInputBytesCapturedEveryState": (
                producer_inputs_captured == len(states)
            ),
            "producerOutputBytesCapturedEveryState": (
                producer_outputs_captured == len(states)
            ),
            "independentDynamicBackdropReplayProven": False,
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
