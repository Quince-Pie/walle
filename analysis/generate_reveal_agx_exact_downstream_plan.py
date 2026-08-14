#!/usr/bin/env python3
"""Generate a same-sign first-join probe whose downstream stages are exact.

Attributing a coefficient disagreement to one setup stage has been ambiguous
because the middle product and the reciprocal both round.  This plan removes
that ambiguity by construction:

  * the triangle's determinant is a power of two, so the P25 selector is
    exactly 2^24 and the reciprocal product is exact;
  * the sampled tile sits at zero displacement on one axis and a power-of-two
    displacement on the other, so the surviving middle product is exact; and
  * both surviving first products carry the same sign, which is the only
    regime the retained captures never covered.

The observed constant therefore probes the first-product join alone.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import struct
import sys
from pathlib import Path
from typing import Final

sys.path[:0] = ["/tmp/walle"]
import _sweep_fused_join_lattice as model

ROOT: Final = Path(__file__).resolve().parent.parent
OUTPUT_DEFAULT: Final = (
    ROOT / "build" / "analysis-agx-basis" / "exact-downstream-plan-v1"
)
VERTEX: Final = struct.Struct("<8I")

# Determinant 3*3 - 1*1 = 8 scaled by 64 pixels gives exactly 2^31.
BASE_GEOMETRY: Final = ((0.0, 0.0), (192.0, 64.0), (64.0, 192.0))
GEOMETRY: Final = BASE_GEOMETRY
PIXEL: Final = (20, 40)
TILE: Final = (0, 1)
BASE_VALUES: Final = (0.0, -1.0, 1.0)
SWEEP: Final = tuple(range(-4096, 4096))
# Sub-pixel translations keep the determinant a power of two while making the
# surviving tile displacement arbitrary, which isolates the middle product.
TRANSLATIONS: Final = tuple(index / 256.0 for index in range(256))
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
    key = model.ordered_key(bits) + offset
    return model.key_to_bits(key)


def _setup(values: tuple[float, float, float]):
    """Return (numerator, coefficient exponent) facts for the active axis."""
    positions = [(int(round(x * 256.0)), int(round(y * 256.0)))
                 for x, y in GEOMETRY]
    determinant = ((positions[1][0] - positions[0][0])
                   * (positions[2][1] - positions[0][1])
                   - (positions[1][1] - positions[0][1])
                   * (positions[2][0] - positions[0][0]))
    anchor = min(range(3), key=lambda i: (positions[i][1], positions[i][0], i))
    edges = (
        (positions[1][1] - positions[2][1],
         positions[2][1] - positions[0][1],
         positions[0][1] - positions[1][1]),
        (positions[2][0] - positions[1][0],
         positions[0][0] - positions[2][0],
         positions[1][0] - positions[0][0]),
    )
    return positions, determinant, anchor, edges


def generate(output_directory: Path) -> dict:
    if output_directory.exists():
        raise FileExistsError(output_directory)
    positions, determinant, anchor, edges = _setup(BASE_VALUES)
    TRANSLATE = os.environ.get("TRANSLATE") == "1"
    if determinant & (determinant - 1):
        raise ValueError(f"determinant {determinant} is not a power of two")
    active = 1
    displacement = TILE[active] * 32 * 256 - positions[anchor][active]
    if not TRANSLATE and displacement & (displacement - 1):
        raise ValueError("active displacement is not a power of two")
    if TILE[1 - active] * 32 * 256 - positions[anchor][1 - active] != 0:
        raise ValueError("inactive displacement is not zero")

    vertices = bytearray()
    draws: list[dict] = []
    experiments: list[dict] = []
    retained = 0
    skipped = 0
    split_counts = {"discovery": 0, "holdout": 0}

    for translation in TRANSLATIONS if TRANSLATE else (0.0,):
      geometry = tuple((x, y + translation) for x, y in BASE_GEOMETRY)
      for offset in (SWEEP[::256] if TRANSLATE else SWEEP):
          values = list(BASE_VALUES)
          nonanchors = [i for i in range(3) if i != anchor]
          values[nonanchors[1]] = model.bits_f32(
              _perturb(model.f32_bits(values[nonanchors[1]]), offset))
          facts = model.exact_downstream_facts(
              geometry, tuple(values), TILE, active)
          if facts is None:
              skipped += 1
              continue
          signs, numerator, coefficient = facts
          if signs[0] != signs[1]:
              skipped += 1
              continue
          anchor_bits = model.cancellation_anchor(coefficient)
          if anchor_bits is None:
              skipped += 1
              continue
          candidates = []
          for anchor_offset in ANCHOR_OFFSETS:
              bits = _perturb(anchor_bits, anchor_offset)
              common = model.bits_f32(bits)
              submitted = tuple(
                  model.f32(common + (value - values[anchor])) for value in values)
              candidates.append((anchor_offset, bits, submitted))
          if len(candidates) != len(ANCHOR_OFFSETS):
              skipped += 1
              continue

          semantic = f"exact-downstream:{translation}:{offset}".encode()
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
                  "variant": "same-sign-exact-downstream",
                  "zeroAxis": 1 - active,
                  "split": split,
                  "variableUlpOffset": offset,
                  "translation": translation,
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
        "candidateCount": len(SWEEP),
        "retainedInputCount": retained,
        "skippedCount": skipped,
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
            "downstreamStagesAreExact": True,
            "establishesFirstJoinLaw": False,
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
        "geometry": [list(vertex) for vertex in GEOMETRY],
        "baseValues": list(BASE_VALUES),
        "pixel": list(PIXEL),
        "tile": list(TILE),
        "experiments": experiments,
        "draws": draws,
        "census": census,
    }
    plan_path = output_directory / "reveal-agx-setup-accumulator-plan.json"
    plan_path.write_text(json.dumps(plan, indent=2, sort_keys=True) + "\n",
                         encoding="utf-8")
    manifest = {
        "schema": "walle-reveal-agx-exact-downstream-plan-manifest-v1",
        "generator": {
            "path": Path(__file__).relative_to(ROOT).as_posix(),
            "sha256": _sha256(Path(__file__)),
        },
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
