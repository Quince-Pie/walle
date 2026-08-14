#!/usr/bin/env python3
"""Generate a probe that isolates the AGX reciprocal product.

The first-product join and the middle product are already cleared by the
exact-downstream and middle-isolation captures.  This plan keeps their
structure — same-sign first products, zero displacement on one axis, anchors
swept through cancellation — and varies only the triangle's determinant away
from a power of two, so the P25 selector and its product become the only
stage that rounds.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
import sys
from pathlib import Path
from typing import Final

sys.path[:0] = ["/tmp/walle"]
import _sweep_fused_join_lattice as model

ROOT: Final = Path(__file__).resolve().parent.parent
OUTPUT_DEFAULT: Final = (
    ROOT / "build" / "analysis-agx-basis" / "middle-join-arbitrary-plan-v1"
)
VERTEX: Final = struct.Struct("<8I")

PIXEL: Final = (40, 40)
TILE: Final = (1, 1)
BASE_VALUES: Final = (0.0, -1.0, 1.0)
SHAPE_OFFSETS: Final = tuple(range(256))
SWEEP: Final = tuple(range(-16, 16))
ANCHOR_OFFSETS: Final = (
    -512, -384, -256, -192, -128, -64, -32, -16, -8, -4, -2, -1,
    1, 2, 4, 8, 16, 32, 64,
)
GROUP_STARTS: Final = (0, 4, 8, 12, 15)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(4 * 1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _perturb(bits: int, offset: int) -> int:
    return model.key_to_bits(model.ordered_key(bits) + offset)


def generate(output_directory: Path) -> dict:
    if output_directory.exists():
        raise FileExistsError(output_directory)
    vertices = bytearray()
    draws: list[dict] = []
    experiments: list[dict] = []
    retained = 0
    skipped = 0
    split_counts = {"discovery": 0, "holdout": 0}
    determinants: set[int] = set()

    for shape in SHAPE_OFFSETS:
        translation = shape / 256.0
        geometry = (
            (translation, translation),
            (192.0 + translation, 64.0 + translation),
            (64.0 + translation, 192.0 + translation),
        )
        for offset in SWEEP:
            values = list(BASE_VALUES)
            values[2] = model.bits_f32(
                _perturb(model.f32_bits(values[2]), offset))
            facts = model.exact_downstream_facts(
                geometry, tuple(values), TILE, 1)
            if facts is None:
                skipped += 1
                continue
            signs, numerator, coefficient = facts
            # both signs are wanted here: the middle join is the target
            anchor_bits = model.cancellation_anchor(coefficient)
            if anchor_bits is None:
                skipped += 1
                continue
            candidates = []
            for anchor_offset in ANCHOR_OFFSETS:
                bits = _perturb(anchor_bits, anchor_offset)
                common = model.bits_f32(bits)
                candidates.append((anchor_offset, bits, tuple(
                    model.f32(common + (value - values[0])) for value in values)))

            positions = [(int(round(x * 256.0)), int(round(y * 256.0)))
                         for x, y in geometry]
            determinant = ((positions[1][0] - positions[0][0])
                           * (positions[2][1] - positions[0][1])
                           - (positions[1][1] - positions[0][1])
                           * (positions[2][0] - positions[0][0]))
            determinants.add(determinant)
            semantic = f"reciprocal:{shape}:{offset}".encode()
            split = ("holdout" if hashlib.sha256(semantic).digest()[0] < 64
                     else "discovery")
            for group_index, start in enumerate(GROUP_STARTS):
                selected = candidates[start:start + 4]
                record = len(draws)
                for vertex_index in range(3):
                    vertices.extend(VERTEX.pack(
                        model.f32_bits(geometry[vertex_index][0]),
                        model.f32_bits(geometry[vertex_index][1]),
                        0,
                        0,
                        *(model.f32_bits(payload[vertex_index])
                          for _offset, _bits, payload in selected),
                    ))
                experiments.append({
                    "recordIndex": record,
                    "inputOrdinal": retained,
                    "anchorGroupIndex": group_index,
                    "variant": "middle-join-arbitrary",
                    "zeroAxis": 0,
                    "split": split,
                    "shapeOffset": shape,
                    "translation": translation,
                    "variableUlpOffset": offset,
                    "determinant": determinant,
                    "numerator": {"sign": numerator[0], "index": numerator[1],
                                  "exponent": numerator[2]},
                    "anchors": [
                        {"anchorUlpOffset": anchor_offset,
                         "anchorBits": f"0x{bits:08x}"}
                        for anchor_offset, bits, _payload in selected
                    ],
                })
                draws.append({
                    "recordIndex": record,
                    "targetIndex": 0,
                    "targetRecordIndex": 0,
                    "sampleRecordIndex": 0,
                    "sampleOrdinal": 0,
                    "patternIndex": record,
                    "x": PIXEL[0],
                    "y": PIXEL[1],
                    "tileX": TILE[0],
                    "tileY": TILE[1],
                })
            retained += 1
            split_counts[split] += 1

    output_directory.mkdir(parents=True)
    vertex_path = output_directory / "reveal-agx-setup-accumulator-vertices.bin"
    vertex_path.write_bytes(vertices)
    census = {
        "targetCount": 8,
        "candidateCount": len(SHAPE_OFFSETS) * len(SWEEP),
        "retainedInputCount": retained,
        "skippedCount": skipped,
        "distinctDeterminantCount": len(determinants),
        "anchorCountPerInput": len(ANCHOR_OFFSETS),
        "drawsPerInput": len(GROUP_STARTS),
        "patternCount": len(draws),
        "drawCount": len(draws),
        "coefficientTripleCount": len(draws) * 4,
        "discoveryInputCount": split_counts["discovery"],
        "holdoutInputCount": split_counts["holdout"],
    }
    plan = {
        "schema": "walle-reveal-agx-setup-accumulator-plan-v1",
        "authority": {
            "opensReferencePixels": False,
            "usesOutputFeedback": False,
            "isolatesReciprocalProduct": True,
        },
        "target": {"width": 2048, "height": 2048},
        "vertexData": {
            "file": vertex_path.name,
            "bytes": len(vertices),
            "sha256": _sha256(vertex_path),
            "recordCount": len(draws),
            "verticesPerRecord": 3,
            "wordsPerVertex": 8,
            "layout": "positionXY,pad2,varyingRGBA; little-endian uint32",
        },
        "experiments": experiments,
        "draws": draws,
        "census": census,
    }
    plan_path = output_directory / "reveal-agx-setup-accumulator-plan.json"
    plan_path.write_text(json.dumps(plan, indent=2, sort_keys=True) + "\n",
                         encoding="utf-8")
    manifest = {
        "schema": "walle-reveal-agx-reciprocal-isolation-plan-manifest-v1",
        "generator": {"path": Path(__file__).relative_to(ROOT).as_posix(),
                      "sha256": _sha256(Path(__file__))},
        "plan": {"file": plan_path.name, "sha256": _sha256(plan_path)},
        "vertexData": {"file": vertex_path.name,
                       "sha256": _sha256(vertex_path)},
        "census": census,
    }
    (output_directory / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT_DEFAULT)
    arguments = parser.parse_args()
    print(json.dumps(generate(arguments.output)["census"], indent=2,
                     sort_keys=True))


if __name__ == "__main__":
    main()
