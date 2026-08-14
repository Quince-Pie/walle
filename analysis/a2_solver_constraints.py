#!/usr/bin/env python3
"""Dense secondary-selection constraints from the reveal corpus.

For every pixel whose corpus byte would differ between secondary 0x3C00 and
0x3BFF ("sensitive"), the observed byte names apple's secondary exactly, which
is one strict inequality on the A2 transfer draw's interpolated alpha plane.
Pixels are bucketed by the transfer triangle that covers their center under an
exact integer top-left rule.
"""

from __future__ import annotations

import json
import struct
import sys
from pathlib import Path
from typing import Final, NamedTuple

import numpy as np

ROOT: Final = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "analysis"))

import a2_solver_primary as primary  # noqa: E402

GEOMETRY: Final = Path("/tmp/walle-analysis/A2-geometry-sweep-v74")
WIDTH: Final = 2_048
HEIGHT: Final = 2_048

LABEL_NONE: Final = 0
LABEL_HIGH: Final = 1  # apple used 0x3C00: plane value >= 1.0
LABEL_LOW: Final = 2  # apple used 0x3BFF: plane value < 1.0
LABEL_EXCLUDED: Final = 3  # sensitive but apple's byte matches neither


class TransferMesh(NamedTuple):
    vertices: list[tuple[float, ...]]
    indices: list[int]
    scissor: tuple[int, int, int, int]

    def triangle(self, index: int) -> list[tuple[float, float]]:
        return [
            (self.vertices[self.indices[3 * index + k]][0],
             self.vertices[self.indices[3 * index + k]][1])
            for k in range(3)
        ]


def load_mesh(state: int) -> TransferMesh:
    report = json.loads(
        (GEOMETRY / f"state-{state}/reveal-mask-trace.json").read_text(
            encoding="utf-8"
        )
    )
    record = report["nativeScale"]["A2Geometry"]
    payload = bytes.fromhex(record["vertexStreamHex"])
    vertices = [
        struct.unpack_from("<12f", payload, index * record["vertexStride"])
        for index in range(record["vertexCount"])
    ]
    scissor = record["scissor"]
    return TransferMesh(
        vertices,
        list(record["indices"]),
        (scissor["x"], scissor["y"], scissor["width"], scissor["height"]),
    )


def _doubled(value: float) -> int:
    scaled = value * 2.0
    if scaled != int(scaled):
        raise ValueError(f"transfer vertex {value} is not a half-integer")
    return int(scaled)


def triangle_map(mesh: TransferMesh) -> np.ndarray:
    """Return the covering transfer triangle per pixel (-1 where none)."""
    result = np.full((HEIGHT, WIDTH), -1, dtype=np.int8)
    px = (2 * np.arange(WIDTH, dtype=np.int64) + 1)[None, :]
    py = (2 * np.arange(HEIGHT, dtype=np.int64) + 1)[:, None]
    for index in range(len(mesh.indices) // 3):
        corners = [
            (_doubled(x), _doubled(y)) for x, y in mesh.triangle(index)
        ]
        (ax, ay), (bx, by), (cx, cy) = corners
        area = (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)
        if area == 0:
            continue
        if area < 0:
            corners = [corners[0], corners[2], corners[1]]
        inside = np.ones((HEIGHT, WIDTH), dtype=np.bool_)
        for edge in range(3):
            (vx, vy) = corners[edge]
            (wx, wy) = corners[(edge + 1) % 3]
            dx = wx - vx
            dy = wy - vy
            value = dx * (py - vy) - dy * (px - vx)
            top_left = dy < 0 or (dy == 0 and dx > 0)
            inside &= value >= 0 if top_left else value > 0
        overlap = inside & (result >= 0)
        if np.any(overlap):
            raise ValueError(
                f"transfer triangles overlap at {np.argwhere(overlap)[0].tolist()}"
            )
        result[inside] = index
    return result


class StateConstraints(NamedTuple):
    state: int
    labels: np.ndarray
    triangles: np.ndarray
    half: np.ndarray


def build(state: int, *, base: tuple[int, ...], bitmap: bytes) -> StateConstraints:
    half, covered, _, _ = primary.render_state_half(state, base=base, bitmap=bitmap)
    high_byte = primary.packed_bytes(half, 0x3C00)
    low_byte = primary.packed_bytes(half, 0x3BFF)
    observed = primary.observed_frame(state)
    sensitive = covered & (high_byte != low_byte)
    labels = np.zeros((HEIGHT, WIDTH), dtype=np.uint8)
    labels[sensitive & (observed == high_byte)] = LABEL_HIGH
    labels[sensitive & (observed == low_byte)] = LABEL_LOW
    labels[sensitive & (observed != high_byte) & (observed != low_byte)] = (
        LABEL_EXCLUDED
    )
    mesh = load_mesh(state)
    triangles = triangle_map(mesh)
    scissor_x, scissor_y, scissor_width, scissor_height = mesh.scissor
    outside = np.ones((HEIGHT, WIDTH), dtype=np.bool_)
    outside[
        scissor_y : scissor_y + scissor_height,
        scissor_x : scissor_x + scissor_width,
    ] = False
    if np.any(sensitive & outside):
        raise ValueError(f"state {state} has sensitive pixels outside the scissor")
    return StateConstraints(state, labels, triangles, half)


def summarize(constraints: StateConstraints) -> str:
    lines = []
    for label, name in ((LABEL_LOW, "low(0x3BFF)"), (LABEL_HIGH, "high(0x3C00)"),
                        (LABEL_EXCLUDED, "excluded")):
        selected = constraints.labels == label
        total = int(np.count_nonzero(selected))
        per_triangle = {
            int(triangle): int(count)
            for triangle, count in zip(
                *np.unique(constraints.triangles[selected], return_counts=True)
            )
        }
        lines.append(
            f"  state {constraints.state} {name}: {total} {per_triangle}"
        )
    return "\n".join(lines)


def main() -> int:
    base, bitmap = primary.load_tables()
    states = [int(value) for value in sys.argv[1:]] or [40, 41, 42, 58, 60]
    for state in states:
        constraints = build(state, base=base, bitmap=bitmap)
        print(summarize(constraints))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
