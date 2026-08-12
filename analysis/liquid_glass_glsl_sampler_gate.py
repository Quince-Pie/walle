#!/usr/bin/env python3
"""Bit-gate the recovered Apple sampler through independent desktop GLSL."""

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from apple_glass_reference_renderer import (
    AppleGlassReferenceRenderer,
    bgra_raw,
)


type JsonObject = dict[str, Any]
type HalfTrace = NDArray[np.uint16]

CAPTURE_SIZE = 1024
ACTIVE_START = 112
ACTIVE_SIZE = 800
HELD_OUT_CASES = (
    "constant-opaque",
    "opaque-coordinate-hash",
    "premultiplied-alpha-field",
    "discordant-mips",
    "sampler-basis-level-zero",
    "sampler-basis-level-one",
)


def load_half_trace(path: Path) -> HalfTrace:
    values = np.fromfile(path, dtype="<u2")
    expected = CAPTURE_SIZE * CAPTURE_SIZE * 4
    if values.size != expected:
        raise ValueError(
            f"{path} has {values.size} half values; expected {expected}"
        )
    return values.reshape(CAPTURE_SIZE, CAPTURE_SIZE, 4)


def compare_active_half_trace(
    reference: HalfTrace,
    candidate: HalfTrace,
) -> JsonObject:
    active = np.s_[
        ACTIVE_START : ACTIVE_START + ACTIVE_SIZE,
        ACTIVE_START : ACTIVE_START + ACTIVE_SIZE,
        :,
    ]
    difference = (
        candidate[active].astype(np.int32)
        - reference[active].astype(np.int32)
    )
    changed = difference != 0
    return {
        "exact": not bool(np.any(changed)),
        "observedHalfValues": int(difference.size),
        "mismatchedHalfValues": int(np.count_nonzero(changed)),
        "mismatchedPixels": int(
            np.count_nonzero(np.any(changed, axis=2))
        ),
        "maximumEncodingDistance": int(
            np.abs(difference).max(initial=0)
        ),
    }


def held_out_trace_path(capture: Path, name: str) -> Path:
    return capture / (
        "carenderer-live-tree-glass-source-"
        f"{name}-sample-numeric-trace-rgba16f.raw"
    )


def run_gate(capture: Path) -> JsonObject:
    cases: list[JsonObject] = []
    with AppleGlassReferenceRenderer(capture) as renderer:
        renderer.program["UseAppleInterpolantTrace"].value = 1
        renderer.program["UseAppleRefractionTrace"].value = 1
        renderer.program["UseAppleSdfTrace"].value = 1

        reference = load_half_trace(
            capture
            / "carenderer-live-tree-glass-sample-"
            "numeric-trace-rgba16f.raw"
        )
        comparison = compare_active_half_trace(
            reference,
            renderer.render_numeric_trace(4),
        )
        cases.append({"name": "captured-source", **comparison})

        for name in HELD_OUT_CASES:
            for level, size in ((0, 448), (1, 224)):
                pixels = bgra_raw(
                    capture
                    / f"glass-heldout-{name}-mip{level}-bgra8.raw",
                    width=size,
                    height=size,
                )
                renderer.source_texture.write(
                    pixels.tobytes(),
                    level=level,
                    alignment=1,
                )
            comparison = compare_active_half_trace(
                load_half_trace(held_out_trace_path(capture, name)),
                renderer.render_numeric_trace(4),
            )
            cases.append({"name": name, **comparison})

        implementation = renderer.implementation

    observed = sum(
        int(case["observedHalfValues"]) for case in cases
    )
    mismatched = sum(
        int(case["mismatchedHalfValues"]) for case in cases
    )
    return {
        "schemaVersion": 1,
        "capture": str(capture),
        "implementation": implementation,
        "model": {
            "spatialPhase": "nearest 1/256",
            "mipPhase": "floor 1/64",
            "cornerWeightReduction": "Q0.16 row-directed exact ties",
            "codeDotReduction": "nearest 1/16 code, ties upward",
            "output": "UNORM8 code / 4080, binary16 RNE",
        },
        "cases": cases,
        "gate": {
            "exact": mismatched == 0,
            "observedHalfValues": observed,
            "mismatchedHalfValues": mismatched,
        },
    }


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("capture", type=Path)
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def main() -> int:
    arguments = parse_arguments()
    report = run_gate(arguments.capture)
    encoded = json.dumps(report, indent=2, sort_keys=True) + "\n"
    print(encoded, end="")
    if arguments.output is not None:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(encoded, encoding="utf-8")
    return 0 if report["gate"]["exact"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
