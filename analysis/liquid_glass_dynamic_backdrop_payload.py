#!/usr/bin/env python3
"""Audit point-in-time dynamic-backdrop payloads and command-buffer joins."""

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


type JsonObject = dict[str, Any]

COPY_BASE_PIPELINE = "com.apple.coreanimation.variable_blur_copy_base_mip_compute"


def _mapping(value: object, name: str) -> JsonObject:
    if not isinstance(value, dict):
        raise ValueError(f"{name} is not an object")
    return value


def _records(render: JsonObject) -> list[JsonObject]:
    probe = _mapping(render.get("metalUniformProbe"), "metalUniformProbe")
    records = probe.get("records")
    if not isinstance(records, list) or not all(
        isinstance(record, dict) for record in records
    ):
        raise ValueError("Metal uniform records are incomplete")
    return records


def _pipeline_label(record: JsonObject) -> str:
    pipeline = record.get("pipeline")
    if not isinstance(pipeline, dict):
        return ""
    label = pipeline.get("label")
    return label if isinstance(label, str) else ""


def _color_zero_address(record: JsonObject) -> str | None:
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


def _raw_payload(
    snapshot: JsonObject,
    *,
    root: Path,
    name: str,
) -> JsonObject:
    filename = snapshot.get("rawFile")
    if snapshot.get("rawCapture") is not True or not isinstance(filename, str):
        raise ValueError(f"{name} was not captured")
    root = root.resolve()
    path = (root / filename).resolve()
    if not path.is_relative_to(root):
        raise ValueError(f"{name} raw path escapes the artifact")
    payload = path.read_bytes()
    expected_bytes = snapshot.get("rawBytes")
    if not isinstance(expected_bytes, int) or len(payload) != expected_bytes:
        raise ValueError(f"{name} raw size differs")
    if len(payload) % 4:
        raise ValueError(f"{name} is not tightly packed BGRA8")
    nonzero_bytes = sum(byte != 0 for byte in payload)
    return {
        "rawFile": filename,
        "rawBytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "nonzeroByteCount": nonzero_bytes,
        "allZero": nonzero_bytes == 0,
        "uniqueBGRA8PixelCount": len(set(memoryview(payload).cast("I"))),
    }


def _analyze_state(record: JsonObject, *, root: Path) -> JsonObject:
    sample_index = record.get("sampleIndex")
    if not isinstance(sample_index, int):
        raise ValueError("dynamic state has no integer sample index")
    render = _mapping(record.get("render"), "dynamic render")
    evidence = _mapping(
        render.get("dynamicBackdropProducerBoundary"),
        "dynamicBackdropProducerBoundary",
    )
    boundaries = evidence.get("records")
    if (
        evidence.get("schemaVersion") != 1
        or evidence.get("boundaryCount") != 1
        or not isinstance(boundaries, list)
        or len(boundaries) != 1
    ):
        raise ValueError("dynamic producer boundary is incomplete")
    boundary = _mapping(boundaries[0], "dynamic producer boundary")
    records = _records(render)

    producer_encoder = boundary.get("producerEncoder")
    producer_output = boundary.get("producerOutputAddress")
    copy_base_encoder = boundary.get("copyBaseEncoder")
    producer_passes = [
        item
        for item in records
        if item.get("kind") == "renderPass"
        and item.get("encoder") == producer_encoder
        and _color_zero_address(item) == producer_output
    ]
    if len(producer_passes) != 1:
        raise ValueError("producer render pass join is not unique")
    copy_base_encoders = [
        item
        for item in records
        if item.get("kind") == "computeEncoder"
        and item.get("encoder") == copy_base_encoder
    ]
    copy_base_bindings = [
        item
        for item in records
        if item.get("kind") == "texture"
        and item.get("stage") == "compute"
        and item.get("index") == 0
        and item.get("encoder") == copy_base_encoder
        and _pipeline_label(item) == COPY_BASE_PIPELINE
        and item.get("sequence") == boundary.get("copyBaseBindingSequence")
    ]
    if not copy_base_encoders or len(copy_base_bindings) != 1:
        raise ValueError("copy-base command join is incomplete")

    producer_command_buffer = producer_passes[0].get("commandBuffer")
    copy_command_buffers = sorted(
        {
            value
            for item in copy_base_encoders
            if isinstance((value := item.get("commandBuffer")), str)
        }
    )
    if not isinstance(producer_command_buffer, str) or not copy_command_buffers:
        raise ValueError("command-buffer identity is unavailable")

    input_payload = _raw_payload(
        _mapping(boundary.get("input"), "producer input snapshot"),
        root=root,
        name=f"sample {sample_index} producer input",
    )
    output_payload = _raw_payload(
        _mapping(boundary.get("output"), "producer output snapshot"),
        root=root,
        name=f"sample {sample_index} producer output",
    )
    return {
        "sampleIndex": sample_index,
        "capturePoint": boundary.get("capturePoint"),
        "inputInterventionPresent": "inputIntervention" in boundary,
        "producerCommandBuffer": producer_command_buffer,
        "copyBaseCommandBuffers": copy_command_buffers,
        "producerAndCopyBaseShareCommandBuffer": (
            copy_command_buffers == [producer_command_buffer]
        ),
        "input": input_payload,
        "output": output_payload,
    }


def analyze(path: Path) -> JsonObject:
    report_path = path / "transition-timeline.json" if path.is_dir() else path
    root = report_path.parent
    report = json.loads(report_path.read_text(encoding="utf-8"))
    dynamic = _mapping(
        report.get("dynamicBackgroundUniforms"),
        "dynamicBackgroundUniforms",
    )
    records = dynamic.get("records")
    if (
        not isinstance(records, list)
        or not records
        or not all(isinstance(record, dict) for record in records)
    ):
        raise ValueError("dynamic background records are incomplete")
    states = [_analyze_state(record, root=root) for record in records]
    input_nonzero_states = sum(not state["input"]["allZero"] for state in states)
    output_nonzero_states = sum(not state["output"]["allZero"] for state in states)
    shared_command_buffer_states = sum(
        state["producerAndCopyBaseShareCommandBuffer"] for state in states
    )
    informative = input_nonzero_states == len(states) and output_nonzero_states == len(
        states
    )
    return {
        "liquidGlassDynamicBackdropPayloadAuditSchemaVersion": 1,
        "artifact": str(root),
        "timeline": str(report_path),
        "timelineSHA256": hashlib.sha256(report_path.read_bytes()).hexdigest(),
        "dynamicUniformSchemaVersion": dynamic.get("schemaVersion"),
        "stateCount": len(states),
        "states": states,
        "aggregate": {
            "producerAndCopyBaseSharedCommandBufferStateCount": (
                shared_command_buffer_states
            ),
            "nonzeroProducerInputStateCount": input_nonzero_states,
            "nonzeroProducerOutputStateCount": output_nonzero_states,
        },
        "conclusion": {
            "pointInTimeBoundaryJoinCapturedEveryState": True,
            "crossCommandBufferRaceObserved": (
                shared_command_buffer_states != len(states)
            ),
            "payloadInformativeForProducerResampling": informative,
            "unmodifiedLocalBackdropWasTransparent": (
                input_nonzero_states == 0 and output_nonzero_states == 0
            ),
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
