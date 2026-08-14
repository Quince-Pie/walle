#!/usr/bin/env python3
"""Generate a cancelled-numerator setup probe.

Production child triangles whose two first products cancel exactly (equal
varying deltas against equal-and-opposite edge factors) show a tiny non-zero
x-dependence in the hardware ITER words, while the CPU model emits a slope of
exactly zero.  This plan isolates that regime:

  * the triangle is symmetric so one axis's first products cancel exactly in
    infinite precision (and in the per-term p27 model);
  * the determinant is a power of two, so the P25 selector is exact;
  * the sampled tile has zero displacement on the cancelled axis and a
    power-of-two displacement on the other, so the constant path is exact and
    any exported slope-word disagreement is attributable to the cancelled
    first join alone.

Each record's four varying channels carry independent (anchor value, delta,
near-cancel offset) experiments; the exported LDCF coefficient triples give
the hardware A/B/C words directly.
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
    ROOT / "build" / "analysis-agx-basis" / "cancelled-numerator-plan-v1"
)
VERTEX: Final = struct.Struct("<8I")

# Symmetric triangles: anchor on top, two base vertices at equal y, with
# equal-and-opposite x-numerator edge factors.  All determinants are powers
# of two in subpixel units and every vertex is inside the render target.
GEOMETRIES: Final = (
    # (vertices, pixel, tile)
    (((128.0, 0.0), (256.0, 128.0), (0.0, 128.0)), (140, 80), (4, 2)),
    (((256.0, 0.0), (512.0, 256.0), (0.0, 256.0)), (270, 150), (8, 4)),
    (((64.0, 0.0), (128.0, 64.0), (0.0, 64.0)), (70, 40), (2, 1)),
)

# Anchor-value mantissa patterns (ulp offsets from -1.0).
A_OFFSETS: Final = (0, -1, -2, -4, -8, -16, -32, -64, -128, -256, -512,
                    -1024, -2048, -4096, 1, 7)

# Delta mantissa patterns; the truncating multiplier's behaviour depends on
# the multiplicand's set bits, so cover varied popcounts and alignments.
D_MANTISSAS: Final = (1, 3, 5, 7, 9, 11, 13, 15, 17, 21, 27, 31, 33, 45, 51,
                      63, 85, 87, 99, 127, 173, 255, 341, 511, 683, 1023,
                      1365, 2047, 2731, 4095, 5461, 8191,
                      -1, -3, -5, -7, -11, -15, -21, -45, -85, -173, -341,
                      -683, -1365, -2731, -5461, -8191)
D_EXPONENTS: Final = (-12,)
# Exponent-gap probes reuse a subset of mantissas at other scales.
D_GAP_MANTISSAS: Final = (1, 3, 5, 21, 45, 173, -3, -45)
D_GAP_EXPONENTS: Final = (-6, -18)

# Near-cancel probes: vC = vB + k ulp for a mantissa subset.
K_VALUES: Final = (1, -1, 2, -2)
K_MANTISSAS: Final = (1, 3, 5, 21, 45, 173, -3, -45)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(4 * 1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _perturb(bits: int, offset: int) -> int:
    key = model.ordered_key(bits) + offset
    return model.key_to_bits(key)


def _ulp_offset(value: float, k: int) -> float:
    return model.bits_f32(_perturb(model.f32_bits(value), k))


def _experiment_values() -> list[dict]:
    triples = []
    for a_offset in A_OFFSETS:
        a = model.bits_f32(_perturb(model.f32_bits(-1.0), a_offset))
        for exponent in D_EXPONENTS:
            for mantissa in D_MANTISSAS:
                d = mantissa * 2.0 ** exponent
                b = model.f32(a + d)
                if b == a:
                    continue
                triples.append({"a": a, "b": b, "c": b, "k": 0,
                                "aOffset": a_offset, "dMantissa": mantissa,
                                "dExponent": exponent})
        for exponent in D_GAP_EXPONENTS:
            for mantissa in D_GAP_MANTISSAS:
                d = mantissa * 2.0 ** exponent
                b = model.f32(a + d)
                if b == a:
                    continue
                triples.append({"a": a, "b": b, "c": b, "k": 0,
                                "aOffset": a_offset, "dMantissa": mantissa,
                                "dExponent": exponent})
        for k in K_VALUES:
            for mantissa in K_MANTISSAS:
                d = mantissa * 2.0 ** -12
                b = model.f32(a + d)
                if b == a:
                    continue
                c = _ulp_offset(b, k)
                triples.append({"a": a, "b": b, "c": c, "k": k,
                                "aOffset": a_offset, "dMantissa": mantissa,
                                "dExponent": -12})
    return triples


def generate(output_directory: Path) -> dict:
    if output_directory.exists():
        raise FileExistsError(output_directory)
    values = _experiment_values()

    vertices = bytearray()
    draws: list[dict] = []
    experiments: list[dict] = []

    for geometry_index, (geometry, pixel, tile) in enumerate(GEOMETRIES):
        positions = [(int(round(x * 256.0)), int(round(y * 256.0)))
                     for x, y in geometry]
        determinant = ((positions[1][0] - positions[0][0])
                       * (positions[2][1] - positions[0][1])
                       - (positions[1][1] - positions[0][1])
                       * (positions[2][0] - positions[0][0]))
        if determinant & (determinant - 1):
            raise ValueError(f"geometry {geometry_index}: determinant "
                             f"{determinant} is not a power of two")
        if tile[0] * 32 * 256 - positions[0][0] != 0:
            raise ValueError(f"geometry {geometry_index}: x displacement "
                             "is not zero")
        y_disp = tile[1] * 32 * 256 - positions[0][1]
        if y_disp & (y_disp - 1):
            raise ValueError(f"geometry {geometry_index}: y displacement "
                             f"{y_disp} is not a power of two")

        for start in range(0, len(values), 4):
            chunk = values[start:start + 4]
            while len(chunk) < 4:
                chunk = chunk + [chunk[-1]]
            record = len(draws)
            for vertex_index in range(3):
                channels = []
                for entry in chunk:
                    value = (entry["a"], entry["b"],
                             entry["c"])[vertex_index]
                    channels.append(model.f32_bits(value))
                vertices.extend(VERTEX.pack(
                    model.f32_bits(geometry[vertex_index][0]),
                    model.f32_bits(geometry[vertex_index][1]),
                    0,
                    0,
                    *channels,
                ))
            semantic = f"cancelled-numerator:{geometry_index}:{start}".encode()
            split = ("holdout" if hashlib.sha256(semantic).digest()[0] < 64
                     else "discovery")
            experiments.append({
                "recordIndex": record,
                "inputOrdinal": record,
                "variant": "cancelled-numerator",
                "split": split,
                "geometryIndex": geometry_index,
                "determinant": determinant,
                "cancelledAxis": 0,
                "channels": chunk,
            })
            draws.append({
                "recordIndex": record,
                "targetIndex": 0,
                "targetRecordIndex": 0,
                "sampleRecordIndex": 0,
                "sampleOrdinal": 0,
                "patternIndex": record,
                "x": pixel[0],
                "y": pixel[1],
                "tileX": tile[0],
                "tileY": tile[1],
            })

    output_directory.mkdir(parents=True)
    vertex_path = output_directory / "reveal-agx-setup-accumulator-vertices.bin"
    vertex_path.write_bytes(vertices)
    census = {
        "targetCount": 8,
        "experimentValueCount": len(values),
        "geometryCount": len(GEOMETRIES),
        "patternCount": len(draws),
        "drawCount": len(draws),
        "coefficientTripleCount": len(draws) * 4,
    }
    plan = {
        "schema": "walle-reveal-agx-setup-accumulator-plan-v1",
        "authority": {
            "opensReferencePixels": False,
            "usesOutputFeedback": False,
            "downstreamStagesAreExact": True,
            "establishesCancelledNumeratorLaw": True,
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
        "geometries": [
            {"vertices": [list(vertex) for vertex in geometry],
             "pixel": list(pixel), "tile": list(tile)}
            for geometry, pixel, tile in GEOMETRIES
        ],
        "experiments": experiments,
        "draws": draws,
        "census": census,
    }
    plan_path = output_directory / "reveal-agx-setup-accumulator-plan.json"
    plan_path.write_text(json.dumps(plan, indent=2, sort_keys=True) + "\n",
                         encoding="utf-8")
    manifest = {
        "schema": "walle-reveal-agx-cancelled-numerator-plan-manifest-v1",
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
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT_DEFAULT)
    arguments = parser.parse_args()
    print(json.dumps(generate(arguments.output)["census"], indent=2,
                     sort_keys=True))


if __name__ == "__main__":
    main()
