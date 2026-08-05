#!/usr/bin/env python3
"""Replay Apple's regular-material backdrop pyramid byte for byte."""

import argparse
import hashlib
import json
import platform
import struct
from pathlib import Path
from typing import Any

from liquid_glass_backdrop_pyramid import (
    comparison,
    read_texture,
    replay_agx2_software,
    replay_copy_base_mip_software,
    replay_live_copy_base_software,
    replay_regular_base_producer_software,
    sha256_file,
    unorm8,
)


type JsonObject = dict[str, Any]

COPY_PIPELINE = (
    "com.apple.coreanimation.variable_blur_copy_base_mip_compute"
)
AGX2_PIPELINE = (
    "com.apple.coreanimation.variable_blur_downsample_compute_agx2"
)
REGULAR_FRAGMENT = "glass_background_sdf_lph"


def _pipeline_label(record: JsonObject) -> str:
    pipeline = record.get("pipeline", {})
    return (
        str(pipeline.get("label", ""))
        if isinstance(pipeline, dict)
        else ""
    )


def _fragment_function(record: JsonObject) -> str:
    pipeline = record.get("pipeline", {})
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


def _one(
    values: list[JsonObject],
    *,
    description: str,
) -> JsonObject:
    if len(values) != 1:
        raise ValueError(
            f"found {len(values)} {description} records; expected one"
        )
    return values[0]


def _snapshots(runtime: JsonObject) -> list[JsonObject]:
    values = runtime.get("carendererEvidence", {}).get(
        "metalTextureSnapshots",
        {},
    ).get("snapshots", [])
    return [
        value
        for value in values
        if isinstance(value, dict)
    ]


def _command_records(runtime: JsonObject) -> list[JsonObject]:
    values = runtime.get("carendererEvidence", {}).get(
        "metalCommandProvenance",
        {},
    ).get("records", [])
    return [
        value
        for value in values
        if isinstance(value, dict)
    ]


def _payload_prefix(record: JsonObject, size: int) -> bytes:
    payload = record.get("payload", {})
    encoded = (
        payload.get("hex")
        if isinstance(payload, dict)
        else None
    )
    if not isinstance(encoded, str):
        raise ValueError("command record has no hexadecimal payload")
    value = bytes.fromhex(encoded)
    if len(value) < size:
        raise ValueError(
            f"command payload has {len(value)} bytes; expected {size}"
        )
    return value[:size]


def _copy_provenance(records: list[JsonObject]) -> JsonObject:
    uniform = _one(
        [
            record
            for record in records
            if _pipeline_label(record) == COPY_PIPELINE
            and record.get("kind") == "buffer"
            and record.get("index") == 0
        ],
        description="copy-base uniform",
    )
    values = struct.unpack("<14h", _payload_prefix(uniform, 28))
    decoded = {
        "base": [values[0], values[1]],
        "clamp": [
            values[2],
            values[3],
            values[6],
            values[7],
        ],
        "mipZeroSize": [values[8], values[9]],
        "mipOneSize": [values[10], values[11]],
        "destinationLevel": values[12],
        "noBase": bool(values[13]),
    }
    dispatch = _one(
        [
            record
            for record in records
            if _pipeline_label(record) == COPY_PIPELINE
            and record.get("kind") == "dispatchThreadgroups"
        ],
        description="copy-base dispatch",
    )
    decoded.update({
        "uniformSequence": uniform.get("sequence"),
        "uniformPrefixHex":
            _payload_prefix(uniform, 28).hex(),
        "grid": dispatch.get("grid"),
        "threadsPerThreadgroup":
            dispatch.get("threadsPerThreadgroup"),
        "exactExpectedTopology": (
            decoded["base"] == [-64, -64]
            and decoded["clamp"] == [0, 0, 255, 255]
            and decoded["mipZeroSize"] == [384, 384]
            and decoded["mipOneSize"] == [192, 192]
            and decoded["destinationLevel"] == 1
            and decoded["noBase"] is False
            and dispatch.get("grid") == [12, 12, 1]
            and dispatch.get("threadsPerThreadgroup")
            == [20, 20, 1]
        ),
    })
    return decoded


def _agx2_provenance(records: list[JsonObject]) -> JsonObject:
    uniforms = [
        record
        for record in records
        if _pipeline_label(record) == AGX2_PIPELINE
        and record.get("kind") == "buffer"
        and record.get("index") == 0
    ]
    dispatches = [
        record
        for record in records
        if _pipeline_label(record) == AGX2_PIPELINE
        and record.get("kind") == "dispatchThreadgroups"
    ]
    if len(uniforms) != 4 or len(dispatches) != 4:
        raise ValueError(
            "regular AGX2 chain does not contain four uniform/dispatch pairs"
        )
    expected_sizes = (96, 48, 24, 12)
    expected_grids = (
        [6, 3, 1],
        [3, 2, 1],
        [2, 1, 1],
        [1, 1, 1],
    )
    decoded = []
    exact = True
    for index, (uniform, dispatch, size, grid) in enumerate(
        zip(
            uniforms,
            dispatches,
            expected_sizes,
            expected_grids,
            strict=True,
        ),
        start=1,
    ):
        source_level, destination_level, width, height, dx, dy = (
            struct.unpack("<4H2f", _payload_prefix(uniform, 16))
        )
        current_exact = (
            source_level == index
            and destination_level == index + 1
            and width == size
            and height == size
            and struct.pack("<f", dx)
            == struct.pack("<f", 1.0 / size)
            and struct.pack("<f", dy)
            == struct.pack("<f", 1.0 / size)
            and dispatch.get("grid") == grid
            and dispatch.get("threadsPerThreadgroup")
            == [16, 16, 1]
        )
        exact = exact and current_exact
        decoded.append({
            "sourceLevel": source_level,
            "destinationLevel": destination_level,
            "destinationSize": [width, height],
            "inverseDestinationSize": [dx, dy],
            "uniformPrefixHex":
                _payload_prefix(uniform, 16).hex(),
            "grid": dispatch.get("grid"),
            "threadsPerThreadgroup":
                dispatch.get("threadsPerThreadgroup"),
            "exactExpectedTopology": current_exact,
        })
    return {
        "stages": decoded,
        "exactExpectedTopology": exact,
    }


def _read_snapshot(
    artifact: Path,
    snapshot: JsonObject,
) -> tuple[Path, Any]:
    path = artifact / str(snapshot["rawFile"])
    return path, read_texture(
        path,
        width=int(snapshot["width"]),
        height=int(snapshot["height"]),
    )


def analyze_artifact(artifact: Path) -> JsonObject:
    runtime_path = artifact / "runtime.json"
    runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
    profile = runtime.get("materialProfileEvidence", {})
    if profile.get("material") != "regular":
        raise ValueError(f"{artifact} is not a regular-material capture")

    snapshots = _snapshots(runtime)
    diagnostic = _one(
        [
            snapshot
            for snapshot in snapshots
            if snapshot.get("pixelFormat") == 70
            and snapshot.get("width") == 1024
            and snapshot.get("height") == 1024
            and "PBGRAXm_TimgA2Xhfc_Ixrg"
            in _pipeline_label(snapshot)
        ],
        description="diagnostic source",
    )
    producer = _one(
        [
            snapshot
            for snapshot in snapshots
            if snapshot.get("pixelFormat") == 80
            and snapshot.get("width") == 256
            and snapshot.get("height") == 256
            and snapshot.get("mipmapLevelCount") == 1
            and _pipeline_label(snapshot) == COPY_PIPELINE
            and snapshot.get("index") == 0
        ],
        description="regular downsample-four output",
    )
    pyramid = _one(
        [
            snapshot
            for snapshot in snapshots
            if snapshot.get("pixelFormat") == 80
            and snapshot.get("width") == 384
            and snapshot.get("height") == 384
            and snapshot.get("mipmapLevelCount") == 6
            and _fragment_function(snapshot) == REGULAR_FRAGMENT
        ],
        description="regular backdrop pyramid",
    )

    diagnostic_path, diagnostic_codes = _read_snapshot(
        artifact,
        diagnostic,
    )
    producer_path, producer_codes = _read_snapshot(
        artifact,
        producer,
    )
    predicted_producer = replay_regular_base_producer_software(
        diagnostic_codes
    )
    producer_comparison = comparison(
        predicted_producer,
        producer_codes,
    )

    copy = _copy_provenance(_command_records(runtime))
    mip_snapshots = sorted(
        (
            snapshot
            for snapshot in pyramid.get("mipSnapshots", [])
            if isinstance(snapshot, dict)
        ),
        key=lambda snapshot: int(snapshot["level"]),
    )
    if [snapshot.get("level") for snapshot in mip_snapshots] != list(
        range(6)
    ):
        raise ValueError("regular backdrop does not contain raw mips 0-5")
    measured = []
    mip_files = []
    for snapshot in mip_snapshots:
        path = artifact / str(snapshot["rawFile"])
        measured.append(
            read_texture(
                path,
                width=int(snapshot["width"]),
                height=int(snapshot["height"]),
            )
        )
        mip_files.append({
            "level": snapshot["level"],
            "file": str(path),
            "sha256": sha256_file(path),
            "size": [snapshot["width"], snapshot["height"]],
        })

    predicted = replay_live_copy_base_software(
        producer_codes,
        destination_width=copy["mipZeroSize"][0],
        destination_height=copy["mipZeroSize"][1],
        base_x=copy["base"][0],
        base_y=copy["base"][1],
        clamp=tuple(copy["clamp"]),
    )
    comparisons = [{
        "stage": "copy-base-mip-zero",
        "sourceLevel": None,
        "destinationLevel": 0,
        **comparison(predicted, measured[0]),
    }]
    predicted = unorm8(replay_copy_base_mip_software(predicted))
    comparisons.append({
        "stage": "copy-base-mip-one",
        "sourceLevel": 0,
        "destinationLevel": 1,
        **comparison(predicted, measured[1]),
    })
    for level in range(2, 6):
        predicted = unorm8(replay_agx2_software(predicted))
        comparisons.append({
            "stage": "agx2",
            "sourceLevel": level - 1,
            "destinationLevel": level,
            **comparison(predicted, measured[level]),
        })

    total_observed = (
        int(producer_comparison["observedBytes"])
        + sum(
            int(record["observedBytes"])
            for record in comparisons
        )
    )
    total_mismatched = (
        int(producer_comparison["mismatchedBytes"])
        + sum(
            int(record["mismatchedBytes"])
            for record in comparisons
        )
    )
    agx2 = _agx2_provenance(_command_records(runtime))
    exact = (
        producer_comparison["exact"] is True
        and all(record["exact"] is True for record in comparisons)
    )
    return {
        "artifact": str(artifact),
        "runtimeSchemaVersion": runtime.get("schemaVersion"),
        "runtimeJsonSha256":
            hashlib.sha256(runtime_path.read_bytes()).hexdigest(),
        "material": profile.get("material"),
        "requestedAppearance": profile.get("requestedAppearance"),
        "fragmentFunction": _fragment_function(pyramid),
        "diagnosticSource": {
            "file": str(diagnostic_path),
            "sha256": sha256_file(diagnostic_path),
            "size": [1024, 1024],
            "pixelFormat": diagnostic.get("pixelFormat"),
        },
        "downsampleFourOutput": {
            "file": str(producer_path),
            "sha256": sha256_file(producer_path),
            "size": [256, 256],
            "comparison": producer_comparison,
        },
        "copyBaseProvenance": copy,
        "agx2Provenance": agx2,
        "mips": mip_files,
        "stageComparisons": comparisons,
        "totals": {
            "observedBytes": total_observed,
            "mismatchedBytes": total_mismatched,
            "exact": exact,
        },
        "conclusion": {
            "nativeTopologyCaptured": (
                copy["exactExpectedTopology"] is True
                and agx2["exactExpectedTopology"] is True
            ),
            "downsampleFourProducerExact":
                producer_comparison["exact"] is True,
            "allSixMipsExact": all(
                record["exact"] is True
                for record in comparisons
            ),
            "portableRegularBackdropRecovered": exact,
            "productionShaderAuthorized": False,
        },
    }


def analyze(artifacts: list[Path]) -> JsonObject:
    captures = [analyze_artifact(path) for path in artifacts]
    payload_hashes = {
        (
            capture["downsampleFourOutput"]["sha256"],
            tuple(mip["sha256"] for mip in capture["mips"]),
        )
        for capture in captures
    }
    all_exact = all(
        capture["conclusion"]["nativeTopologyCaptured"]
        and capture["conclusion"]["portableRegularBackdropRecovered"]
        for capture in captures
    )
    return {
        "liquidGlassRegularBackdropAnalysisSchemaVersion": 1,
        "implementation": {
            "file": "analysis/liquid_glass_regular_backdrop.py",
            "python": platform.python_version(),
        },
        "captures": captures,
        "crossCapture": {
            "captureCount": len(captures),
            "distinctOutputPayloads": len(payload_hashes),
            "appearanceInvariant": len(payload_hashes) == 1,
        },
        "conclusion": {
            "allCapturesExact": all_exact,
            "regularBackdropAppearanceInvariant":
                len(payload_hashes) == 1,
            "portableRegularBackdropRecovered": (
                all_exact and len(payload_hashes) == 1
            ),
            "productionShaderAuthorized": False,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Replay the regular Liquid Glass backdrop producer and mips."
        )
    )
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
    return (
        0
        if report["conclusion"][
            "portableRegularBackdropRecovered"
        ]
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
