#!/usr/bin/env python3
"""Per-pixel hardware value capture at every corpus residual pixel.

For each residual byte (and the one known collateral pixel) every drawn
triangle whose exact rasterization covers the pixel is replayed with its
production varyings, one draw per (pixel, triangle).  The variant probe
records interpolate_at_center() plus the four +-1-pixel offset values, so
a single capture yields the hardware's center AND partner words for every
candidate owner triple - simultaneously pinning the per-pixel evaluation
law and the winner-selection semantics at the only pixels where they are
still unproven.
"""

from __future__ import annotations

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
    ROOT / "build" / "analysis-agx-basis" / "residual-value-plan-v1"
)
VERTEX: Final = struct.Struct("<8I")
SCRATCH: Final = Path(
    "/tmp/nix-shell.PFgUGF/claude-1000/-tmp-walle/"
    "4ccfbce8-33b2-4b5f-8e29-93486397c8a4/scratchpad")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(4 * 1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def load_children() -> dict:
    out: dict = {}
    for line in (SCRATCH / "childgeo_all_residual_states.txt") \
            .read_text().splitlines():
        if "CHILDSDF" not in line:
            continue
        t = line[line.index("CHILDSDF"):].split()
        state, ordinal = int(t[1]), int(t[2])
        out.setdefault(state, {})[ordinal] = [
            [int(x, 16) for x in t[3 + 4 * v:7 + 4 * v]]
            for v in range(3)]
    return out


def fixed(word: int) -> int:
    return int(round(model.bits_f32(word) * 256.0))


def contains(verts, x: int, y: int) -> bool:
    """Mirror of walle_lg_reveal_general_contains (top-left fill rule)."""
    fx = [(fixed(v[0]), fixed(v[1])) for v in verts]
    det = ((fx[1][0] - fx[0][0]) * (fx[2][1] - fx[0][1])
           - (fx[1][1] - fx[0][1]) * (fx[2][0] - fx[0][0]))
    if det == 0:
        return False
    expected = -1 if det < 0 else 1
    cx, cy = 256 * x + 128, 256 * y + 128
    for edge in range(3):
        nxt = (edge + 1) % 3
        ex = fx[nxt][0] - fx[edge][0]
        ey = fx[nxt][1] - fx[edge][1]
        cross = ex * (cy - fx[edge][1]) - ey * (cx - fx[edge][0])
        if cross == 0:
            ox = -ex if expected < 0 else ex
            oy = -ey if expected < 0 else ey
            top = oy == 0 and ox < 0
            left = oy > 0
            if not (top or left):
                return False
            continue
        if (1 if cross > 0 else -1) != expected:
            return False
    return True


def generate(output_directory: Path) -> dict:
    if output_directory.exists():
        raise FileExistsError(output_directory)
    children = load_children()
    pixels: list[tuple[int, int, int, int, int]] = []
    for line in (ROOT / "build" / "_residual_list.txt") \
            .read_text().splitlines():
        st, x, y, wb, ab = map(int, line.split())
        pixels.append((st, x, y, wb, ab))
    # the collateral break discovered by the corrected-flag PRED sweep
    pixels.append((35, 1525, 5, 239, 239))

    vertices = bytearray()
    draws: list[dict] = []
    experiments: list[dict] = []
    for st, x, y, wb, ab in pixels:
        cands = [(o, v) for o, v in sorted(children[st].items())
                 if contains(v, x, y)]
        if not cands:
            print(f"state {st} ({x},{y}): NO candidate triangle",
                  file=sys.stderr)
            continue
        for ordinal, verts in cands:
            record = len(draws)
            for vertex in verts:
                vertices.extend(VERTEX.pack(
                    vertex[0], vertex[1], 0, 0,
                    vertex[2], vertex[3], vertex[2], vertex[3]))
            experiments.append({
                "recordIndex": record,
                "variant": "residual-value",
                "state": st,
                "x": x,
                "y": y,
                "walleByte": wb,
                "appleByte": ab,
                "drawOrdinal": ordinal,
            })
            draws.append({
                "recordIndex": record,
                "targetIndex": 0,
                "targetRecordIndex": 0,
                "sampleRecordIndex": 0,
                "sampleOrdinal": 0,
                "patternIndex": record,
                "x": x,
                "y": y,
                "tileX": x // 32,
                "tileY": y // 32,
            })
    output_directory.mkdir(parents=True)
    vertex_path = output_directory / "reveal-agx-setup-accumulator-vertices.bin"
    vertex_path.write_bytes(vertices)
    census = {
        "targetCount": 8,
        "patternCount": len(draws),
        "drawCount": len(draws),
        "coefficientTripleCount": len(draws) * 4,
    }
    plan = {
        "schema": "walle-reveal-agx-setup-accumulator-plan-v1",
        "authority": {
            "opensReferencePixels": False,
            "usesOutputFeedback": False,
            "establishesDegenerateChildSetupLaw": False,
            "establishesPerPixelEvaluationLaw": True,
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
        "schema": "walle-reveal-agx-residual-value-plan-manifest-v1",
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


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=OUTPUT_DEFAULT)
    arguments = parser.parse_args()
    manifest = generate(arguments.output)
    print(json.dumps(manifest["census"], indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
