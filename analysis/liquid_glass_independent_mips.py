#!/usr/bin/env python3
"""Generate the measured Liquid Glass source-mip sequence from mip zero."""

import json
import struct
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from liquid_glass_backdrop_pyramid import (
    replay_base_producer_software,
    replay_agx2_software,
    replay_copy_base_mip_software,
    replay_live_copy_base_software,
    replay_regular_base_producer_software,
    unorm8,
)


type JsonObject = dict[str, Any]
type CodeImage = NDArray[np.uint8]

COPY_BASE_PIPELINE = "com.apple.coreanimation.variable_blur_copy_base_mip_compute"


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


def source_snapshot(runtime: JsonObject) -> JsonObject:
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
            "capture must have one Liquid Glass source texture; "
            f"found {len(matches)}"
        )
    return matches[0]


def wallpaper_source_snapshot(runtime: JsonObject) -> JsonObject:
    snapshots = runtime["carendererEvidence"]["metalTextureSnapshots"][
        "snapshots"
    ]
    matches = [
        snapshot
        for snapshot in snapshots
        if snapshot.get("index") == 3
        and snapshot.get("pixelFormat") == 70
        and snapshot.get("width") == 1024
        and snapshot.get("height") == 1024
        and _pipeline_fragment(snapshot) == "TimgA2Xhfc_Ixrg"
    ]
    if len(matches) != 1:
        raise ValueError(
            "capture must have one 1024x1024 wallpaper source; "
            f"found {len(matches)}"
        )
    return matches[0]


def _pipeline_label(snapshot: JsonObject) -> str:
    pipeline = snapshot.get("pipeline", {})
    return str(pipeline.get("label", "")) if isinstance(pipeline, dict) else ""


def copy_base_source_snapshot(runtime: JsonObject) -> JsonObject:
    snapshots = runtime["carendererEvidence"]["metalTextureSnapshots"][
        "snapshots"
    ]
    matches = [
        snapshot
        for snapshot in snapshots
        if snapshot.get("index") == 0
        and _pipeline_label(snapshot) == COPY_BASE_PIPELINE
        and isinstance(snapshot.get("rawFile"), str)
    ]
    if len(matches) != 1:
        raise ValueError(
            "capture must have one copy-base producer texture; "
            f"found {len(matches)}"
        )
    return matches[0]


def copy_base_uniform(runtime: JsonObject) -> JsonObject:
    snapshots = runtime["carendererEvidence"]["metalBufferSnapshots"][
        "snapshots"
    ]
    payloads: set[bytes] = set()
    for snapshot in snapshots:
        if (
            snapshot.get("stage") != "compute"
            or snapshot.get("index") != 0
            or _pipeline_label(snapshot) != COPY_BASE_PIPELINE
        ):
            continue
        payload = snapshot.get("payload", {})
        hexadecimal = payload.get("hex") if isinstance(payload, dict) else None
        if isinstance(hexadecimal, str):
            raw = bytes.fromhex(hexadecimal)
            if len(raw) >= 32:
                payloads.add(raw[:32])
    if len(payloads) != 1:
        raise ValueError(
            "capture must have one copy-base uniform prefix; "
            f"found {len(payloads)}"
        )
    raw = payloads.pop()
    return {
        "textureCoordinateBase": struct.unpack_from("<2h", raw, 0),
        "textureCoordinateClamp": struct.unpack_from("<4h", raw, 8),
        "destinationLevel0Size": struct.unpack_from("<2H", raw, 16),
    }


def read_codes(path: Path, *, width: int, height: int) -> CodeImage:
    values = np.fromfile(path, dtype=np.uint8)
    expected = width * height * 4
    if values.size != expected:
        raise ValueError(f"{path} has {values.size} bytes; expected {expected}")
    return values.reshape(height, width, 4)


def generate_source_mips(base: CodeImage, *, level_count: int) -> tuple[CodeImage, ...]:
    if (
        base.ndim != 3
        or base.shape[2] != 4
        or base.dtype != np.uint8
        or level_count < 1
    ):
        raise ValueError("mip zero must be a uint8 four-channel image")

    levels = [base.copy()]
    if level_count == 1:
        return tuple(levels)

    current = unorm8(replay_copy_base_mip_software(levels[0]))
    levels.append(current)
    while len(levels) < level_count:
        current = unorm8(replay_agx2_software(current))
        levels.append(current)
    return tuple(levels)


def generated_source_mip_overrides(capture: Path) -> dict[int, bytes]:
    runtime = json.loads((capture / "runtime.json").read_text(encoding="utf-8"))
    snapshots = sorted(
        source_snapshot(runtime)["mipSnapshots"],
        key=lambda level: int(level["level"]),
    )
    if [int(level["level"]) for level in snapshots] != list(
        range(len(snapshots))
    ):
        raise ValueError("source mip levels must be consecutive from zero")

    first = snapshots[0]
    base = read_codes(
        capture / str(first["rawFile"]),
        width=int(first["width"]),
        height=int(first["height"]),
    )
    generated = generate_source_mips(base, level_count=len(snapshots))
    overrides: dict[int, bytes] = {}
    for snapshot, pixels in zip(snapshots, generated, strict=True):
        expected_shape = (
            int(snapshot["height"]),
            int(snapshot["width"]),
            4,
        )
        if pixels.shape != expected_shape:
            raise ValueError(
                f"generated mip {snapshot['level']} shape differs: "
                f"{pixels.shape} != {expected_shape}"
            )
        overrides[int(snapshot["level"])] = pixels.tobytes()
    return overrides


def generated_copy_and_mips_from_producer(capture: Path) -> dict[int, bytes]:
    runtime = json.loads((capture / "runtime.json").read_text(encoding="utf-8"))
    producer_snapshot = copy_base_source_snapshot(runtime)
    producer = read_codes(
        capture / str(producer_snapshot["rawFile"]),
        width=int(producer_snapshot["width"]),
        height=int(producer_snapshot["height"]),
    )
    uniform = copy_base_uniform(runtime)
    destination_width, destination_height = uniform["destinationLevel0Size"]
    base_x, base_y = uniform["textureCoordinateBase"]
    mip_zero = replay_live_copy_base_software(
        producer,
        destination_width=destination_width,
        destination_height=destination_height,
        base_x=base_x,
        base_y=base_y,
        clamp=uniform["textureCoordinateClamp"],
    )
    snapshots = source_snapshot(runtime)["mipSnapshots"]
    generated = generate_source_mips(mip_zero, level_count=len(snapshots))
    return {
        level: pixels.tobytes()
        for level, pixels in enumerate(generated)
    }


def generated_static_source_pyramid(capture: Path) -> dict[int, bytes]:
    runtime = json.loads((capture / "runtime.json").read_text(encoding="utf-8"))
    wallpaper_snapshot = wallpaper_source_snapshot(runtime)
    wallpaper = read_codes(
        capture / str(wallpaper_snapshot["rawFile"]),
        width=1024,
        height=1024,
    )
    material = runtime.get("materialProfileEvidence", {}).get("material")
    if material == "clear":
        producer = replay_base_producer_software(wallpaper)
        mip_zero = replay_live_copy_base_software(producer)
    elif material == "regular":
        producer = replay_regular_base_producer_software(wallpaper)
        mip_zero = replay_live_copy_base_software(
            producer,
            destination_width=384,
            destination_height=384,
            base_x=-64,
            base_y=-64,
            clamp=(0, 0, 255, 255),
        )
    else:
        raise ValueError(f"unsupported material profile: {material!r}")

    snapshots = source_snapshot(runtime)["mipSnapshots"]
    generated = generate_source_mips(mip_zero, level_count=len(snapshots))
    return {
        level: pixels.tobytes()
        for level, pixels in enumerate(generated)
    }


def generated_static_source_pyramid_from_wallpaper(
    wallpaper: CodeImage,
    *,
    material: str,
) -> dict[int, bytes]:
    """Generate the canonical static pyramid from caller-owned RGBA8 pixels."""
    return {
        level: raw
        for level, (_, _, raw) in (
            generated_static_source_pyramid_levels_from_wallpaper(
                wallpaper,
                material=material,
            ).items()
        )
    }


def generated_static_source_pyramid_levels_from_wallpaper(
    wallpaper: CodeImage,
    *,
    material: str,
) -> dict[int, tuple[int, int, bytes]]:
    """Generate complete static mip layouts from caller-owned RGBA8 pixels."""
    if wallpaper.shape != (1024, 1024, 4) or wallpaper.dtype != np.uint8:
        raise ValueError("static wallpaper must be 1024x1024 RGBA8")

    if material == "clear":
        producer = replay_base_producer_software(wallpaper)
        mip_zero = replay_live_copy_base_software(producer)
        level_count = 2
    elif material == "regular":
        producer = replay_regular_base_producer_software(wallpaper)
        mip_zero = replay_live_copy_base_software(
            producer,
            destination_width=384,
            destination_height=384,
            base_x=-64,
            base_y=-64,
            clamp=(0, 0, 255, 255),
        )
        level_count = 6
    else:
        raise ValueError(f"unsupported material profile: {material!r}")

    generated = generate_source_mips(mip_zero, level_count=level_count)
    return {
        level: (pixels.shape[1], pixels.shape[0], pixels.tobytes())
        for level, pixels in enumerate(generated)
    }
