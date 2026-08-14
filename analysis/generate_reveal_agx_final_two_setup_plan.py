#!/usr/bin/env python3
"""Generate a focused M1 setup probe for the last two reveal-mask pixels."""

import argparse
import hashlib
import json
import struct
from pathlib import Path
from typing import Final


type JsonObject = dict[str, object]
type VertexWords = tuple[int, ...]
type Lane = tuple[int, int, int]
type LaneGroup = tuple[Lane, Lane, Lane, Lane]

ROOT: Final = Path(__file__).resolve().parent.parent
CATALOG_DEFAULT: Final = (
    ROOT / "build" / "analysis-agx-basis" / "reveal-agx-basis-catalog.json"
)
OUTPUT_DEFAULT: Final = (
    ROOT / "build" / "analysis-agx-basis" / "final-two-setup-plan-v1"
)
VERTEX: Final = struct.Struct("<8I")
TARGET_WIDTH: Final = 2_048
TARGET_HEIGHT: Final = 2_048


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _ordered_key(bits: int) -> int:
    return (~bits & 0xFFFF_FFFF) if bits & 0x8000_0000 else bits | 0x8000_0000


def _bits_from_ordered_key(key: int) -> int:
    return (~key & 0xFFFF_FFFF) if key < 0x8000_0000 else key & 0x7FFF_FFFF


def _offset(bits: int, ulps: int) -> int:
    key = _ordered_key(bits) + ulps
    if not 0 < key < 0xFFFF_FFFF:
        raise ValueError("ULP perturbation left the finite binary32 domain")
    result = _bits_from_ordered_key(key)
    if result & 0x7F80_0000 == 0x7F80_0000:
        raise ValueError("ULP perturbation produced a non-finite value")
    return result


def _lane(values: tuple[VertexWords, VertexWords, VertexWords], component: int) -> Lane:
    return tuple(vertex[component] for vertex in values)  # type: ignore[return-value]


def _perturbed(base: Lane, vertex: int, ulps: int) -> Lane:
    result = list(base)
    result[vertex] = _offset(result[vertex], ulps)
    return tuple(result)  # type: ignore[return-value]


def _patterns(
    vertices: tuple[VertexWords, VertexWords, VertexWords],
) -> tuple[LaneGroup, ...]:
    sdf_x = _lane(vertices, 6)
    sdf_y = _lane(vertices, 7)
    basis = (
        (0x3F80_0000, 0, 0),
        (0, 0x3F80_0000, 0),
        (0, 0, 0x3F80_0000),
    )
    lanes: list[Lane] = [
        sdf_x,
        sdf_y,
        *basis,
        (0x3F80_0000, 0x4000_0000, 0x4080_0000),
        (0, 0, 0),
        (0x3F80_0000, 0x3F80_0000, 0x3F80_0000),
    ]
    offsets = (-64, -32, -16, -8, -4, -2, -1, 1, 2, 4, 8, 16, 32, 64)
    for base in (sdf_x, sdf_y):
        for vertex in range(3):
            lanes.extend(_perturbed(base, vertex, offset) for offset in offsets)
        for offset in offsets:
            lanes.append(tuple(_offset(value, offset) for value in base))  # type: ignore[arg-type]
    while len(lanes) % 4:
        lanes.append(lanes[-1])
    return tuple(tuple(lanes[index : index + 4]) for index in range(0, len(lanes), 4))  # type: ignore[return-value]


def _inside(
    vertices: tuple[VertexWords, VertexWords, VertexWords], x: int, y: int
) -> bool:
    floats = [
        (
            struct.unpack("<f", struct.pack("<I", vertex[0]))[0],
            struct.unpack("<f", struct.pack("<I", vertex[1]))[0],
        )
        for vertex in vertices
    ]
    signs = []
    point = (x + 0.5, y + 0.5)
    for start, end in zip(floats, floats[1:] + floats[:1], strict=True):
        signs.append(
            (end[0] - start[0]) * (point[1] - start[1])
            - (end[1] - start[1]) * (point[0] - start[0])
        )
    return all(value >= 0 for value in signs) or all(value <= 0 for value in signs)


def _target_case(catalog: JsonObject, state: int, primitive: int) -> JsonObject:
    cases = catalog.get("cases")
    if not isinstance(cases, list):
        raise TypeError("catalog cases are absent")
    matches = [
        case
        for case in cases
        if isinstance(case, dict)
        and case.get("state") == state
        and case.get("sourcePrimitive") == primitive
    ]
    if len(matches) != 1:
        raise ValueError(f"state {state} primitive {primitive} is not unique")
    return matches[0]


def _pixels(
    case: JsonObject, preferred: tuple[int, int]
) -> tuple[tuple[int, int], ...]:
    if not isinstance(case.get("children"), list):
        raise TypeError("case children are absent")
    x, y = preferred
    return preferred, (x ^ 1, y), (x, y ^ 1)


def generate(catalog_path: Path, output_directory: Path) -> JsonObject:
    if output_directory.exists():
        raise FileExistsError(output_directory)
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    if not isinstance(catalog, dict):
        raise TypeError("catalog root is not an object")
    specifications = ((41, 2, (1897, 606)), (61, 4, (1996, 2046)))
    selected = [
        (state, primitive, _target_case(catalog, state, primitive), preferred)
        for state, primitive, preferred in specifications
    ]
    # The authenticated probe requires eight targets.  Four identical target
    # slots per state retain that ABI while patternIndex remains globally unique.
    targets = tuple(selected[index // 4] for index in range(8))
    pattern_count = len(
        _patterns(tuple(tuple(v) for v in selected[0][2]["sourceVertexBits"]))
    )
    if any(
        len(_patterns(tuple(tuple(v) for v in case["sourceVertexBits"])))
        != pattern_count
        for _state, _primitive, case, _preferred in selected
    ):
        raise ValueError("state pattern counts differ")

    output_directory.mkdir(parents=True)
    vertex_data = bytearray()
    draws: list[JsonObject] = []
    target_records: list[JsonObject] = []
    for target_index, (state, primitive, case, preferred) in enumerate(targets):
        source = tuple(
            tuple(int(word) for word in vertex) for vertex in case["sourceVertexBits"]
        )
        if len(source) != 3 or any(len(vertex) != 8 for vertex in source):
            raise ValueError("source vertex shape differs")
        pixels = _pixels(case, preferred)
        patterns = _patterns(source)  # type: ignore[arg-type]
        target_records.append(
            {
                "state": state,
                "sourcePrimitive": primitive,
                "pixels": [list(pixel) for pixel in pixels],
                "sourceVertexBits": [list(vertex) for vertex in source],
            }
        )
        for sample_ordinal, (x, y) in enumerate(pixels):
            for pattern_index, lanes in enumerate(patterns):
                record_index = len(draws)
                for vertex_index, vertex in enumerate(source):
                    vertex_data.extend(
                        VERTEX.pack(
                            vertex[0],
                            vertex[1],
                            0,
                            0,
                            *(lane[vertex_index] for lane in lanes),
                        )
                    )
                draws.append(
                    {
                        "recordIndex": record_index,
                        "targetIndex": target_index,
                        "targetRecordIndex": target_index,
                        "sampleRecordIndex": sample_ordinal,
                        "sampleOrdinal": sample_ordinal,
                        "patternIndex": pattern_index,
                        "x": x,
                        "y": y,
                        "tileX": x // 32,
                        "tileY": y // 32,
                    }
                )

    vertex_path = output_directory / "reveal-agx-setup-accumulator-vertices.bin"
    vertex_path.write_bytes(vertex_data)
    plan: JsonObject = {
        "schema": "walle-reveal-agx-setup-accumulator-plan-v1",
        "authority": {
            "opensReferencePixels": False,
            "usesOutputFeedback": False,
            "usesRealPublicPreclipTriangles": True,
        },
        "target": {"width": TARGET_WIDTH, "height": TARGET_HEIGHT},
        "vertexData": {
            "file": vertex_path.name,
            "bytes": len(vertex_data),
            "sha256": _sha256(vertex_path),
            "recordCount": len(draws),
            "verticesPerRecord": 3,
            "wordsPerVertex": 8,
            "layout": "positionXY,pad2,varyingRGBA; little-endian uint32",
        },
        "targets": target_records,
        "patterns": {
            "count": pattern_count,
            "description": "actual SDF, one-hot controls, and per-source-value +/-64 ULP perturbations",
        },
        "draws": draws,
        "census": {
            "targetCount": 8,
            "patternCount": pattern_count,
            "drawCount": len(draws),
            "coefficientTripleCount": len(draws) * 4,
        },
    }
    plan_path = output_directory / "reveal-agx-setup-accumulator-plan.json"
    plan_path.write_text(
        json.dumps(plan, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    manifest = {
        "schema": "walle-reveal-agx-final-two-setup-plan-v1",
        "generator": {
            "path": str(Path(__file__).resolve()),
            "sha256": _sha256(Path(__file__).resolve()),
        },
        "catalog": {"path": str(catalog_path), "sha256": _sha256(catalog_path)},
        "plan": {"path": plan_path.name, "sha256": _sha256(plan_path)},
        "vertexData": {"path": vertex_path.name, "sha256": _sha256(vertex_path)},
        "drawCount": len(draws),
        "patternCount": pattern_count,
    }
    manifest_path = output_directory / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, default=CATALOG_DEFAULT)
    parser.add_argument("--output", type=Path, default=OUTPUT_DEFAULT)
    arguments = parser.parse_args()
    print(
        json.dumps(
            generate(arguments.catalog, arguments.output), indent=2, sort_keys=True
        )
    )


if __name__ == "__main__":
    main()
