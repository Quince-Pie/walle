#!/usr/bin/env python3
"""Generate an output-blind AGX triangle-setup accumulator probe plan.

The plan repeats the eight direct-child records that distinguish the remaining
ordinary-setup constant errors.  Geometry and sample locations come only from
the public reveal catalog.  Vertex varyings sweep constant offsets, scales,
and signed cancellation patterns; no rendered image or reference pixel is
opened while constructing the plan.
"""

import argparse
import hashlib
import json
import math
import struct
import sys
from pathlib import Path
from typing import Final


ROOT: Final = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "analysis"))

import analyze_reveal_agx_basis_phase as phase  # noqa: E402


type JsonObject = dict[str, object]
type Pattern = tuple[tuple[float, float, float], ...]

CATALOG_DEFAULT: Final = (
    ROOT / "build" / "analysis-agx-basis" / "reveal-agx-basis-catalog.json"
)
OUTPUT_DEFAULT: Final = (
    ROOT / "build" / "analysis-agx-basis" / "setup-accumulator-plan-v1"
)
TARGET_RECORDS: Final = (15, 149, 168, 269, 292, 295, 333, 484)
VERTEX = struct.Struct("<8I")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _f32(value: float) -> float:
    return struct.unpack("<f", struct.pack("<f", value))[0]


def _float_bits(value: float) -> int:
    return struct.unpack("<I", struct.pack("<f", value))[0]


def _float_from_bits(bits: int) -> float:
    return struct.unpack("<f", struct.pack("<I", bits))[0]


def _add(left: float, right: float) -> float:
    return _f32(left + right)


def _scale(value: float, multiplier: int) -> float:
    return _f32(value * multiplier)


def _base_bits() -> tuple[int, ...]:
    values = {0x0000_0000, 0x8000_0000}
    for exponent in (-24, -20, -16, -12, -8, -4, -2, -1, 0, 1, 2, 4, 7):
        positive = _float_bits(math.ldexp(1.0, exponent))
        values.add(positive)
        values.add(positive ^ 0x8000_0000)
    values.update(range(0x3F7F_FFF8, 0x3F80_0009))
    values.update(range(0x3EFF_FFFC, 0x3F00_0005))
    return tuple(sorted(values))


def _scale_bits() -> tuple[int, ...]:
    values: set[int] = set()
    for exponent in (-20, -16, -12, -8, -4, -2, -1, 0, 1, 2, 4):
        positive = _float_bits(math.ldexp(1.0, exponent))
        values.add(positive)
        values.add(positive ^ 0x8000_0000)
    values.update((0x3F7F_FFFF, 0x3F80_0001, 0xBF7F_FFFF, 0xBF80_0001))
    return tuple(sorted(values))


def _constant_bits() -> tuple[int, ...]:
    return (
        0x0000_0000,
        0x8000_0000,
        0x3380_0000,
        0x3E80_0000,
        0x3F00_0000,
        0x3F7F_FFFF,
        0x3F80_0000,
        0x3F80_0001,
        0x4000_0000,
        0xBF80_0000,
        0xC000_0000,
    )


def _basis_pattern(base: float, scale: float) -> Pattern:
    one = _scale(scale, 1)
    two = _scale(scale, 2)
    four = _scale(scale, 4)
    return (
        (_add(base, one), base, base),
        (base, _add(base, one), base),
        (base, base, _add(base, one)),
        (_add(base, one), _add(base, two), _add(base, four)),
    )


def _signed_pattern(scale: float) -> Pattern:
    base = _f32(1.0)
    one = _scale(scale, 1)
    two = _scale(scale, 2)
    four = _scale(scale, 4)
    return (
        (_add(base, one), _add(base, -one), base),
        (base, _add(base, one), _add(base, -one)),
        (_add(base, -one), base, _add(base, one)),
        (_add(base, one), _add(base, -two), _add(base, four)),
    )


def _constant_pattern(values: tuple[float, float, float, float]) -> Pattern:
    return tuple((value, value, value) for value in values)


def _patterns() -> tuple[JsonObject, ...]:
    result: list[JsonObject] = []
    for bits in _base_bits():
        base = _float_from_bits(bits)
        result.append(
            {
                "kind": "unit-onehot-plus-base",
                "baseBits": f"0x{bits:08x}",
                "values": _basis_pattern(base, _f32(1.0)),
            }
        )
    for bits in _scale_bits():
        scale = _float_from_bits(bits)
        result.append(
            {
                "kind": "scaled-onehot-plus-one",
                "scaleBits": f"0x{bits:08x}",
                "values": _basis_pattern(_f32(1.0), scale),
            }
        )
        result.append(
            {
                "kind": "signed-cancellation-about-one",
                "scaleBits": f"0x{bits:08x}",
                "values": _signed_pattern(scale),
            }
        )
    constants = tuple(_float_from_bits(bits) for bits in _constant_bits())
    for offset in range(0, len(constants), 4):
        group = constants[offset : offset + 4]
        if len(group) < 4:
            group += (group[-1],) * (4 - len(group))
        result.append(
            {
                "kind": "constant-control",
                "constantBits": [f"0x{_float_bits(value):08x}" for value in group],
                "values": _constant_pattern(group),
            }
        )
    return tuple(result)


def generate(catalog_path: Path, output_directory: Path) -> JsonObject:
    catalog, samples = phase._load_catalog(catalog_path)  # noqa: SLF001
    if output_directory.exists():
        raise FileExistsError(output_directory)
    sample_by_record = {sample.record_index: sample for sample in samples}
    if any(record not in sample_by_record for record in TARGET_RECORDS):
        raise ValueError("target record is absent from catalog")

    patterns = _patterns()
    targets: list[JsonObject] = []
    target_samples: list[tuple[phase.Sample, tuple[phase.Sample, ...]]] = []
    for record_index in TARGET_RECORDS:
        target = sample_by_record[record_index]
        siblings = tuple(
            sample
            for sample in samples
            if sample.case_index == target.case_index
            and sample.child_ordinal == target.child_ordinal
        )
        if not siblings or len(siblings) > 3:
            raise ValueError(f"target {record_index} sibling census differs")
        if target not in siblings:
            raise ValueError(f"target {record_index} is absent from its sibling set")
        target_samples.append((target, siblings))
        targets.append(
            {
                "targetRecordIndex": record_index,
                "caseIndex": target.case_index,
                "state": target.state,
                "sourcePrimitive": target.source_primitive,
                "childOrdinal": target.child_ordinal,
                "childOrdinalWithinSource": target.child_ordinal_within_source,
                "sampleRecords": [sample.record_index for sample in siblings],
                "pixels": [list(sample.pixel) for sample in siblings],
                "tiles": [list(sample.tile) for sample in siblings],
            }
        )

    vertices = bytearray()
    draws: list[JsonObject] = []
    for target_index, (target, siblings) in enumerate(target_samples):
        children = phase._canonical_children(target)  # noqa: SLF001
        child = children[target.child_ordinal_within_source]
        if len(child) != 3 or any(len(vertex) != 6 for vertex in child):
            raise ValueError(f"target {target.record_index} child shape differs")
        for sample in siblings:
            for pattern_index, pattern in enumerate(patterns):
                values = pattern["values"]
                if not isinstance(values, tuple) or len(values) != 4:
                    raise ValueError("pattern shape differs")
                record_index = len(draws)
                for local_vertex, vertex in enumerate(child):
                    lane_values = tuple(values[lane][local_vertex] for lane in range(4))
                    if not all(math.isfinite(value) for value in lane_values):
                        raise ValueError("pattern contains non-finite vertex value")
                    vertices.extend(
                        VERTEX.pack(
                            phase._bits(vertex[0]),  # noqa: SLF001
                            phase._bits(vertex[1]),  # noqa: SLF001
                            0,
                            0,
                            *(phase._bits(value) for value in lane_values),  # noqa: SLF001
                        )
                    )
                draws.append(
                    {
                        "recordIndex": record_index,
                        "targetIndex": target_index,
                        "targetRecordIndex": target.record_index,
                        "sampleRecordIndex": sample.record_index,
                        "sampleOrdinal": sample.sample_ordinal,
                        "patternIndex": pattern_index,
                        "x": sample.pixel[0],
                        "y": sample.pixel[1],
                        "tileX": sample.tile[0],
                        "tileY": sample.tile[1],
                    }
                )

    output_directory.mkdir(parents=False)
    vertex_path = output_directory / "reveal-agx-setup-accumulator-vertices.bin"
    vertex_path.write_bytes(vertices)
    plan_path = output_directory / "reveal-agx-setup-accumulator-plan.json"
    plan: JsonObject = {
        "schema": "walle-reveal-agx-setup-accumulator-plan-v1",
        "authority": {
            "usesPublicRevealGeometryOnly": True,
            "opensReferencePixels": False,
            "usesOutputFeedback": False,
            "establishesAGXAccumulatorLaw": False,
        },
        "target": {"width": 2048, "height": 2048},
        "catalog": {
            "path": str(catalog_path),
            "bytes": catalog_path.stat().st_size,
            "sha256": _sha256(catalog_path),
        },
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
        "census": {
            "targetCount": len(targets),
            "patternCount": len(patterns),
            "drawCount": len(draws),
            "coefficientTripleCount": len(draws) * 4,
        },
    }
    plan_path.write_text(json.dumps(plan, indent=2, sort_keys=True) + "\n")

    script_path = Path(__file__).resolve()
    manifest: JsonObject = {
        "schema": "walle-reveal-agx-setup-accumulator-plan-manifest-v1",
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
        "generator": {
            "path": str(script_path),
            "bytes": script_path.stat().st_size,
            "sha256": _sha256(script_path),
        },
        "catalogCensus": catalog["census"],
    }
    manifest_path = output_directory / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, default=CATALOG_DEFAULT)
    parser.add_argument("--output", type=Path, default=OUTPUT_DEFAULT)
    arguments = parser.parse_args()
    print(json.dumps(generate(arguments.catalog, arguments.output), indent=2))


if __name__ == "__main__":
    main()
