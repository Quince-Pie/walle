#!/usr/bin/env python3
"""Recover Apple's discrete dynamic-backdrop crop and allocation policy."""

import argparse
import hashlib
import json
import math
import platform
from pathlib import Path
from typing import Any

import liquid_glass_dynamic_backdrop as dynamic


type JsonObject = dict[str, Any]

COPY_BASE_PIPELINE = dynamic.COPY_BASE_PIPELINE
ALLOCATION_QUANTUM = 64
OPENED_SCOPE = {
    "appearance": "light",
    "direction": "materialize",
    "material": "clear",
    "geometry": {
        "centerX": 512,
        "centerY": 512,
        "height": 800,
        "shape": "circle",
        "width": 800,
        "windowHeight": 1_024,
        "windowWidth": 1_024,
    },
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1 << 20):
            digest.update(block)
    return digest.hexdigest()


def align_up(value: float, alignment: int = ALLOCATION_QUANTUM) -> int:
    if not math.isfinite(value) or value <= 0 or alignment <= 0:
        raise ValueError("allocation extent must be finite and positive")
    return alignment * math.ceil(value / alignment)


def _axis_policy(
    *,
    frame_minimum: float,
    frame_extent: float,
    window_extent: float,
    scale: float,
    invert: bool,
) -> JsonObject:
    if (
        not all(
            math.isfinite(value)
            for value in (
                frame_minimum,
                frame_extent,
                window_extent,
                scale,
            )
        )
        or frame_extent <= 0
        or window_extent <= 0
        or scale <= 0
    ):
        raise ValueError("invalid nominal-frame axis")

    if invert:
        unclipped_lower = window_extent - (frame_minimum + frame_extent)
        unclipped_upper = window_extent - frame_minimum
    else:
        unclipped_lower = frame_minimum
        unclipped_upper = frame_minimum + frame_extent
    clipped_lower = max(0.0, unclipped_lower)
    clipped_upper = min(window_extent, unclipped_upper)
    if clipped_upper <= clipped_lower:
        raise ValueError("nominal frame does not intersect the window")

    scaled_lower = scale * clipped_lower
    scaled_upper = scale * clipped_upper
    # The producer uses opposite edge conventions on the two axes. X is
    # floor-plus-one; the Metal-inverted Y origin is a ceiling. Keeping this
    # asymmetry is necessary at integral endpoints.
    crop_origin = math.ceil(scaled_lower) if invert else math.floor(scaled_lower) + 1
    clamp_maximum = math.floor(scaled_upper) - crop_origin - 1
    if clamp_maximum < 0:
        raise ValueError("predicted producer clamp is empty")

    return {
        "unclippedBounds": [unclipped_lower, unclipped_upper],
        "clippedBounds": [clipped_lower, clipped_upper],
        "scaledBounds": [scaled_lower, scaled_upper],
        "cropOrigin": crop_origin,
        "clampMaximum": clamp_maximum,
        "producerExtent": align_up(clamp_maximum + 1),
        "destinationExtent": align_up(scale * (clipped_upper - clipped_lower)),
    }


def predict_policy(
    geometry: JsonObject,
    *,
    remaining: float,
    scale: float,
) -> JsonObject:
    if not 0.0 < remaining <= 1.0:
        raise ValueError("opened dynamic states require 0 < remaining <= 1")
    width = float(geometry["width"])
    height = float(geometry["height"])
    center_x = float(geometry["centerX"])
    center_y = float(geometry["centerY"])
    window_width = float(geometry["windowWidth"])
    window_height = float(geometry["windowHeight"])

    # The presentation carrier grows from zero to the requested dimensions.
    # The backdrop crop nevertheless uses the full requested extent, placed
    # at the growing carrier's minimum. This is the nominal frame whose
    # clipped span controls both copy-base and producer allocations.
    frame_x = center_x - width * remaining / 2.0
    frame_y = center_y - height * remaining / 2.0
    x_axis = _axis_policy(
        frame_minimum=frame_x,
        frame_extent=width,
        window_extent=window_width,
        scale=scale,
        invert=False,
    )
    y_axis = _axis_policy(
        frame_minimum=frame_y,
        frame_extent=height,
        window_extent=window_height,
        scale=scale,
        invert=True,
    )
    return {
        "nominalFrameMinimum": [frame_x, frame_y],
        "cropOrigin": [x_axis["cropOrigin"], y_axis["cropOrigin"]],
        "textureCoordinateClamp": [
            0,
            0,
            x_axis["clampMaximum"],
            y_axis["clampMaximum"],
        ],
        "producerExtent": [
            x_axis["producerExtent"],
            y_axis["producerExtent"],
        ],
        "destinationExtent": [
            x_axis["destinationExtent"],
            y_axis["destinationExtent"],
        ],
        "axes": {"x": x_axis, "y": y_axis},
    }


def _scope(report: JsonObject) -> JsonObject:
    geometry = report.get("geometry")
    if not isinstance(geometry, dict):
        raise ValueError("timeline geometry is missing")
    selected_geometry = {name: geometry.get(name) for name in OPENED_SCOPE["geometry"]}
    return {
        "appearance": report.get("appearance"),
        "direction": report.get("direction"),
        "material": report.get("material"),
        "geometry": selected_geometry,
    }


def _copy_records(
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
            and dynamic._pipeline_label(record) == COPY_BASE_PIPELINE
        ],
        "copy-base source texture",
    )
    destination = dynamic._single(
        [
            record
            for record in records
            if record.get("kind") == "texture"
            and record.get("stage") == "compute"
            and record.get("index") == 1
            and dynamic._pipeline_label(record) == COPY_BASE_PIPELINE
        ],
        "copy-base destination texture",
    )
    uniform = dynamic._single(
        [
            snapshot
            for snapshot in snapshots
            if snapshot.get("stage") == "compute"
            and snapshot.get("index") == 0
            and dynamic._pipeline_label(snapshot) == COPY_BASE_PIPELINE
        ],
        "copy-base uniform buffer",
    )
    return source, destination, uniform


def _observed_policy(
    report: JsonObject,
    record: JsonObject,
) -> JsonObject:
    render = record.get("render")
    if not isinstance(render, dict) or render.get("executed") is not True:
        raise ValueError("dynamic CARenderer state did not execute")
    source, destination, uniform_snapshot = _copy_records(render)
    source_texture = dynamic._texture(source)
    destination_texture = dynamic._texture(destination)
    source_address = source_texture.get("address")
    records = dynamic._records(render)
    producer_pass = dynamic._single(
        [
            item
            for item in records
            if item.get("kind") == "renderPass"
            and dynamic._render_attachment_address(item) == source_address
        ],
        "dynamic backdrop producer pass",
    )
    _, _, mvp, _ = dynamic._draw_vertices(
        render,
        render_pass=producer_pass,
    )
    producer_extent = [
        int(source_texture["width"]),
        int(source_texture["height"]),
    ]
    crop = dynamic.recover_crop_origin(
        mvp,
        width=producer_extent[0],
        height=producer_extent[1],
    )
    uniform = dynamic.decode_copy_base_uniform(dynamic._payload(uniform_snapshot))
    destination_extent = [
        int(destination_texture["width"]),
        int(destination_texture["height"]),
    ]
    if uniform["destinationLevel0Size"] != destination_extent:
        raise ValueError("copy-base uniform and destination extent differ")
    copy_offset = [int(value) for value in uniform["textureCoordinateBase"]]
    crop_origin = [int(value) for value in crop["origin"]]
    return {
        "cropOrigin": crop_origin,
        "textureCoordinateClamp": [
            int(value) for value in uniform["textureCoordinateClamp"]
        ],
        "producerExtent": producer_extent,
        "destinationExtent": destination_extent,
        "copyOffset": copy_offset,
        "effectiveOrigin": [
            crop_origin[0] + copy_offset[0],
            crop_origin[1] + copy_offset[1],
        ],
        "producerCropMaximumIntegralResidual": crop["maximumIntegralResidual"],
    }


def _comparison(
    prediction: JsonObject,
    observed: JsonObject,
) -> JsonObject:
    fields = (
        "cropOrigin",
        "textureCoordinateClamp",
        "producerExtent",
        "destinationExtent",
    )
    return {
        name: {
            "componentCount": len(prediction[name]),
            "mismatchedComponents": sum(
                predicted != actual
                for predicted, actual in zip(
                    prediction[name],
                    observed[name],
                    strict=True,
                )
            ),
            "exact": prediction[name] == observed[name],
        }
        for name in fields
    }


def analyze_timeline(path: Path) -> JsonObject:
    report_path = path / "transition-timeline.json" if path.is_dir() else path
    report = json.loads(report_path.read_text(encoding="utf-8"))
    scope = _scope(report)
    if scope != OPENED_SCOPE:
        raise ValueError(f"{report_path} lies outside the frozen opened profile")
    dynamic_uniforms = report.get("dynamicBackgroundUniforms")
    records = (
        dynamic_uniforms.get("records") if isinstance(dynamic_uniforms, dict) else None
    )
    if not isinstance(records, list) or not records:
        raise ValueError("timeline has no dynamic background records")

    states = []
    for record in records:
        if not isinstance(record, dict):
            raise ValueError("dynamic background record is not an object")
        sample_index = int(record["sampleIndex"])
        remaining = float(record["remaining"])
        scale = dynamic._backdrop_scale(report, sample_index)
        prediction = predict_policy(
            scope["geometry"],
            remaining=remaining,
            scale=scale,
        )
        observed = _observed_policy(report, record)
        states.append(
            {
                "sampleIndex": sample_index,
                "remaining": remaining,
                "runtimeScale": scale,
                "prediction": prediction,
                "observed": observed,
                "comparison": _comparison(prediction, observed),
            }
        )
    return {
        "artifact": str(report_path.parent),
        "timeline": str(report_path),
        "timelineSHA256": sha256_file(report_path),
        "dynamicUniformSchemaVersion": dynamic_uniforms.get("schemaVersion"),
        "states": states,
    }


def analyze(paths: list[Path]) -> JsonObject:
    captures = [analyze_timeline(path) for path in paths]
    fields = (
        "cropOrigin",
        "textureCoordinateClamp",
        "producerExtent",
        "destinationExtent",
    )
    aggregate: JsonObject = {}
    for name in fields:
        comparisons = [
            state["comparison"][name]
            for capture in captures
            for state in capture["states"]
        ]
        aggregate[name] = {
            "componentCount": sum(
                int(value["componentCount"]) for value in comparisons
            ),
            "mismatchedComponents": sum(
                int(value["mismatchedComponents"]) for value in comparisons
            ),
            "exactEveryState": all(bool(value["exact"]) for value in comparisons),
        }
    state_count = sum(len(capture["states"]) for capture in captures)
    opened_policy_exact = all(aggregate[name]["exactEveryState"] for name in fields)
    return {
        "liquidGlassDynamicAllocationPolicyAnalysisSchemaVersion": 1,
        "implementation": {
            "file": str(Path(__file__).resolve()),
            "sha256": sha256_file(Path(__file__)),
            "dynamicParserFile": str(Path(dynamic.__file__).resolve()),
            "dynamicParserSHA256": sha256_file(Path(dynamic.__file__)),
            "python": platform.python_version(),
        },
        "scope": {
            **OPENED_SCOPE,
            "evidenceStatus": "retrospective-opened-corpus",
        },
        "captures": captures,
        "aggregate": {
            "captureCount": len(captures),
            "stateCount": state_count,
            **aggregate,
        },
        "recoveredPolicy": {
            "nominalFrame": (
                "full requested extent placed at the growing "
                "presentation carrier minimum"
            ),
            "producerCrop": (
                "X=floor(scale*clippedLowerX)+1; Y=ceil(scale*clippedLowerY)"
            ),
            "copyClampMaximum": ("floor(scale*clippedUpper)-producerCrop-1"),
            "producerExtent": ("align copyClampMaximum+1 up to 64 per axis"),
            "destinationExtent": ("align scale*clippedNominalSpan up to 64 per axis"),
        },
        "conclusion": {
            "openedCircle800AllocationPolicyExact": opened_policy_exact,
            "independentCopyOffsetPolicyRecovered": False,
            "independentProducerMeshPolicyRecovered": False,
            "generalGeometryPolicyRecovered": False,
            "prospectiveGeometryHoldoutPassed": False,
            "productionShaderAuthorized": False,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("captures", nargs="+", type=Path)
    parser.add_argument("-o", "--output", type=Path)
    arguments = parser.parse_args()
    result = analyze(arguments.captures)
    encoded = (
        json.dumps(
            result,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n"
    )
    if arguments.output is None:
        print(encoded, end="")
    else:
        arguments.output.write_text(encoded, encoding="utf-8")
        print(arguments.output)
    return 0 if result["conclusion"]["openedCircle800AllocationPolicyExact"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
