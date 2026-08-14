#!/usr/bin/env python3.14
"""Generate power-of-two geometry probes for the AGX signed setup join."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import struct
from pathlib import Path
from typing import Final


ROOT: Final = Path(__file__).resolve().parent.parent
OUTPUT_DEFAULT: Final = ROOT / "build" / "analysis-agx-basis" / "synthetic-join-plan-v1"
VERTEX: Final = struct.Struct("<8I")
TARGET_WIDTH: Final = 2_048
TARGET_HEIGHT: Final = 2_048
PERTURBATIONS: Final = (
    -1_024,
    -512,
    -256,
    -128,
    -64,
    -32,
    -16,
    -8,
    -4,
    -2,
    -1,
    0,
    1,
    2,
    4,
    8,
    16,
    32,
    64,
    128,
    256,
    512,
    1_024,
)
TRIANGLES: Final = (
    ((0, 0), (256, 256), ((32, 32), (64, 64), (96, 96))),
    ((0, 0), (512, 512), ((64, 64), (128, 96), (160, 160))),
    ((0, 0), (1_024, 1_024), ((128, 128), (256, 192), (320, 320))),
    ((0, 0), (512, 1_024), ((64, 128), (128, 192), (160, 320))),
    ((0, 0), (1_024, 512), ((128, 64), (256, 96), (320, 160))),
    ((128, 64), (512, 512), ((192, 128), (256, 192), (320, 224))),
    ((3, 5), (256, 512), ((35, 69), (67, 133), (99, 197))),
    ((17, 19), (1_024, 1_024), ((145, 147), (273, 211), (337, 339))),
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _bits(value: float) -> int:
    return struct.unpack("<I", struct.pack("<f", value))[0]


def _float(bits: int) -> float:
    return struct.unpack("<f", struct.pack("<I", bits))[0]


def _ordered_key(bits: int) -> int:
    return (~bits & 0xFFFF_FFFF) if bits & 0x8000_0000 else bits | 0x8000_0000


def _bits_from_ordered_key(key: int) -> int:
    if not 0 <= key <= 0xFFFF_FFFF:
        raise ValueError("perturbed binary32 key escaped uint32")
    return (~key & 0xFFFF_FFFF) if key < 0x8000_0000 else key & 0x7FFF_FFFF


def _perturb(value: float, offset: int) -> float:
    result = _float(_bits_from_ordered_key(_ordered_key(_bits(value)) + offset))
    if not math.isfinite(result):
        raise ValueError("synthetic perturbation produced a non-finite value")
    return result


def _base_groups() -> tuple[tuple[tuple[float, float], ...], ...]:
    next_one = _float(0x3F80_0001)
    small = _float(0x3580_0001)
    large = _float(0x4980_0001)
    return (
        ((1.0, -1.0), (next_one, -1.0), (1.0, -next_one), (next_one, -next_one)),
        ((small, -small), (-small, small), (small, -2 * small), (2 * small, -small)),
        ((large, -large), (-large, large), (large, -2 * large), (2 * large, -large)),
        ((1.0, -0.5), (0.5, -1.0), (-1.0, 0.5), (-0.5, 1.0)),
    )


def _patterns() -> tuple[dict[str, object], ...]:
    result: list[dict[str, object]] = []
    for group_index, group in enumerate(_base_groups()):
        for endpoint in range(2):
            for offset in PERTURBATIONS:
                pairs = [list(pair) for pair in group]
                for lane in range(4):
                    pairs[lane][endpoint] = _perturb(pairs[lane][endpoint], offset)
                result.append(
                    {
                        "group": group_index,
                        "perturbedEndpoint": "x" if endpoint == 0 else "y",
                        "ulpOffset": offset,
                        "values": tuple((0.0, pair[0], pair[1]) for pair in pairs),
                    }
                )
    return tuple(result)


def _inside(
    origin: tuple[int, int], extent: tuple[int, int], pixel: tuple[int, int]
) -> bool:
    local_x = pixel[0] + 0.5 - origin[0]
    local_y = pixel[1] + 0.5 - origin[1]
    return local_x > 0 and local_y > 0 and local_x / extent[0] + local_y / extent[1] < 1


def generate(output_directory: Path) -> dict[str, object]:
    if output_directory.exists():
        raise FileExistsError(output_directory)
    patterns = _patterns()
    vertices = bytearray()
    draws: list[dict[str, object]] = []
    targets: list[dict[str, object]] = []
    for target_index, (origin, extent, pixels) in enumerate(TRIANGLES):
        positions = (
            (origin[0], origin[1]),
            (origin[0] + extent[0], origin[1]),
            (origin[0], origin[1] + extent[1]),
        )
        if not all(_inside(origin, extent, pixel) for pixel in pixels):
            raise ValueError(f"target {target_index} sample is not interior")
        targets.append(
            {
                "targetIndex": target_index,
                "origin": list(origin),
                "extent": list(extent),
                "positions": [list(position) for position in positions],
                "pixels": [list(pixel) for pixel in pixels],
            }
        )
        for sample_ordinal, pixel in enumerate(pixels):
            for pattern_index, pattern in enumerate(patterns):
                values = pattern["values"]
                if not isinstance(values, tuple):
                    raise ValueError("synthetic pattern values are not a tuple")
                record = len(draws)
                for vertex_index, position in enumerate(positions):
                    vertices.extend(
                        VERTEX.pack(
                            _bits(float(position[0])),
                            _bits(float(position[1])),
                            0,
                            0,
                            *(_bits(values[lane][vertex_index]) for lane in range(4)),
                        )
                    )
                draws.append(
                    {
                        "recordIndex": record,
                        "targetIndex": target_index,
                        "targetRecordIndex": target_index,
                        "sampleRecordIndex": target_index * 3 + sample_ordinal,
                        "sampleOrdinal": sample_ordinal,
                        "patternIndex": pattern_index,
                        "x": pixel[0],
                        "y": pixel[1],
                        "tileX": pixel[0] // 32,
                        "tileY": pixel[1] // 32,
                    }
                )

    output_directory.mkdir(parents=True)
    vertex_path = output_directory / "reveal-agx-setup-accumulator-vertices.bin"
    vertex_path.write_bytes(vertices)
    plan_path = output_directory / "reveal-agx-setup-accumulator-plan.json"
    census = {
        "targetCount": len(TRIANGLES),
        "patternCount": len(patterns),
        "drawCount": len(draws),
        "coefficientTripleCount": len(draws) * 4,
    }
    plan: dict[str, object] = {
        "schema": "walle-reveal-agx-setup-accumulator-plan-v1",
        "authority": {
            "usesPublicRevealGeometryOnly": False,
            "usesSyntheticPowerOfTwoGeometry": True,
            "opensReferencePixels": False,
            "usesOutputFeedback": False,
            "establishesAGXAccumulatorLaw": False,
        },
        "target": {"width": TARGET_WIDTH, "height": TARGET_HEIGHT},
        "vertexData": {
            "file": vertex_path.name,
            "bytes": len(vertices),
            "sha256": _sha256(vertex_path),
            "recordCount": len(draws),
            "verticesPerRecord": 3,
            "wordsPerVertex": 8,
            "layout": "positionXY,pad2,varyingRGBA; little-endian uint32",
        },
        "targets": targets,
        "patterns": [
            {key: value for key, value in pattern.items() if key != "values"}
            for pattern in patterns
        ],
        "draws": draws,
        "census": census,
    }
    plan_path.write_text(json.dumps(plan, indent=2, sort_keys=True) + "\n")
    manifest: dict[str, object] = {
        "schema": "walle-reveal-agx-synthetic-join-plan-manifest-v1",
        "generator": {
            "path": Path(__file__).relative_to(ROOT).as_posix(),
            "bytes": Path(__file__).stat().st_size,
            "sha256": _sha256(Path(__file__)),
        },
        "plan": {
            "file": plan_path.name,
            "bytes": plan_path.stat().st_size,
            "sha256": _sha256(plan_path),
        },
        "vertexData": {
            "file": vertex_path.name,
            "bytes": vertex_path.stat().st_size,
            "sha256": _sha256(vertex_path),
        },
        "census": census,
    }
    manifest_path = output_directory / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=OUTPUT_DEFAULT)
    arguments = parser.parse_args()
    manifest = generate(arguments.output)
    print(json.dumps(manifest["census"], indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
