#!/usr/bin/env python3
"""Check the reveal composition law against Apple's COLORED reveal corpus.

The 65-state coverage corpus reveals opaque white over opaque black, so its
frames are numerically the reveal mask itself and every composed byte is a
saturated copy.  That corpus cannot exercise the blend at interior code
values.  The earlier d67fb35 capture ran the same reveal geometry over two
procedurally generated colour fields, so it does.

Two caveats keep this an evidence script rather than a gate:

  * that capture saved through a Color LCD -> sRGB conversion (see its
    manifest sourceImage/savedImage colour spaces), so its bytes are NOT the
    renderer's output: regenerating the two source fields from the capture
    tool's closed-form generators reproduces the endpoint frames only to
    within 2-4 code values;
  * a per-channel colour transform does not commute with blending, so error
    is expected exactly where the mask is partial.

What the corpus can still establish is reported below, split by region:
saturated pixels (mask 0 or 255, a pure copy) versus the antialiased ring.
The mask for ladder step k is taken from the coverage corpus at state 4k -
the two captures share geometry and 17 = 65 // 4 steps align.
"""

import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
COVERAGE = (
    ROOT
    / "artifacts/liquid-glass-reveal-coverage-01421a3-v1/capture/sweeps"
    / "sweep__wallpaper-reveal__regular__dark"
)
COLOURED = (
    ROOT
    / "artifacts/liquid-glass-reveal-d67fb35-v1/capture/sweeps"
    / "sweep__wallpaper-reveal__regular__dark"
)
WIDTH = HEIGHT = 2_048
LADDER = 17

type JsonObject = dict[str, object]


def generated_fields() -> tuple[np.ndarray, np.ndarray]:
    """The capture tool's two background generators, evaluated at device pixels."""
    x = np.arange(WIDTH, dtype=np.float64)[None, :].repeat(HEIGHT, 0)
    y = np.arange(HEIGHT, dtype=np.float64)[:, None].repeat(WIDTH, 1)

    def s(coordinate: np.ndarray, period: float) -> np.ndarray:
        return np.sin(2 * np.pi * coordinate / period)

    def c(coordinate: np.ndarray, period: float) -> np.ndarray:
        return np.cos(2 * np.pi * coordinate / period)

    def channel(value: np.ndarray) -> np.ndarray:
        return np.clip(np.rint(value), 0, 255).astype(np.uint8)

    outgoing = np.dstack(
        [
            channel(128 + 42 * s(x, 257) + 31 * s(y, 613) + 20 * s(x + y, 887)),
            channel(128 + 39 * s(y, 293) + 33 * s(x, 557) + 19 * s(x - y, 941)),
            channel(
                128 + 37 * s(x + 2 * y, 347) + 29 * s(2 * x - y, 719) + 21 * s(x, 1091)
            ),
        ]
    )
    incoming = np.dstack(
        [
            channel(
                116 + 47 * c(2 * x + y, 431) + 28 * c(y, 769) + 23 * c(x - y, 1151)
            ),
            channel(139 + 41 * c(x - 2 * y, 389) + 35 * c(x, 683) + 17 * c(x + y, 997)),
            channel(
                124 + 44 * c(y, 337) + 32 * c(2 * x - y, 821) + 22 * c(x, 1237)
            ),
        ]
    )
    return outgoing, incoming


def verify() -> JsonObject:
    outgoing, incoming = generated_fields()
    first = np.asarray(Image.open(COLOURED / "frame-0000.png").convert("RGB"))
    last = np.asarray(Image.open(COLOURED / "frame-0016.png").convert("RGB"))
    capture_transform_error = {
        "outgoingMaximumCodeDelta": int(
            np.abs(first.astype(np.int32) - outgoing.astype(np.int32)).max()
        ),
        "incomingMaximumCodeDelta": int(
            np.abs(last.astype(np.int32) - incoming.astype(np.int32)).max()
        ),
    }

    # Predict from the CAPTURED endpoints so the colour transform is common to
    # both sides everywhere the blend is a pure copy.
    base = first.astype(np.int32)
    target = last.astype(np.int32)
    saturated_total = saturated_bad = ring_total = ring_bad = 0
    maximum_error = 0
    frames: list[JsonObject] = []
    for step in range(LADDER):
        mask = np.asarray(
            Image.open(COVERAGE / f"frame-{4 * step:04}.png").convert("RGBA")
        )[..., 0].astype(np.int32)
        ring = (mask > 0) & (mask < 255)
        lane = mask[..., None]
        # Round-half-up matches round-half-even here: 255 * odd / 2 is never
        # an integer, so the blend numerator never lands on a tie.
        predicted = (target * lane + base * (255 - lane) + 127) // 255
        observed = np.asarray(
            Image.open(COLOURED / f"frame-{step:04}.png").convert("RGB")
        ).astype(np.int32)
        delta = np.abs(observed - predicted)
        wrong = np.any(delta != 0, axis=2)
        ring_total += int(ring.sum())
        ring_bad += int((wrong & ring).sum())
        saturated_total += int((~ring).sum())
        saturated_bad += int((wrong & ~ring).sum())
        maximum_error = max(maximum_error, int(delta.max()))
        frames.append(
            {
                "step": step,
                "progress": step / (LADDER - 1),
                "maskState": 4 * step,
                "ringMismatched": int((wrong & ring).sum()),
                "saturatedMismatched": int((wrong & ~ring).sum()),
                "maximumCodeDelta": int(delta.max()),
            }
        )

    return {
        "schemaVersion": 1,
        "classification": (
            "colour-field reveal corpus evidence for the code-value blend law"
        ),
        "formalParityEstablished": False,
        "captureColourTransformIsLossy": True,
        "captureTransformError": capture_transform_error,
        "saturated": {
            "pixels": saturated_total,
            "mismatched": saturated_bad,
            "exactPercentage": (saturated_total - saturated_bad)
            * 100.0
            / saturated_total,
        },
        "antialiasedRing": {
            "pixels": ring_total,
            "mismatched": ring_bad,
        },
        "maximumCodeDelta": maximum_error,
        "frames": frames,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    report = verify()
    encoded = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if arguments.output is not None:
        arguments.output.write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
