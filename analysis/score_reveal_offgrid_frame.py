#!/usr/bin/env python3
"""Score a single off-ladder Walle reveal capture against Apple hardware.

The 65-state corpus samples progress on the k/64 ladder.  The dynamic
sequence of the same capture session additionally recorded frames at the
presentation clock's own instants, one of which (frame-0001) falls between
ladder states.  That frame is the only hardware evidence for the continuous
progress path, so it is scored exactly the way the ladder is: the reveal
mask byte against the reference channel, and the composed BGRA against all
four reference channels.
"""

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CAPTURE_ROOT = (
    ROOT / "artifacts/liquid-glass-reveal-coverage-01421a3-v1/capture"
)
WIDTH = 2_048
HEIGHT = 2_048
# manifest dynamicSequences[0].analysisExclusionPixels
EXCLUDED_ROWS = 8

type JsonObject = dict[str, object]


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def score(capture: Path, reference_path: Path, progress: float) -> JsonObject:
    rows = slice(EXCLUDED_ROWS, HEIGHT)
    mask_bytes = (capture / "state-0000.r8").read_bytes()
    composed_bytes = (capture / "composition-state-0000.bgra").read_bytes()
    if len(mask_bytes) != WIDTH * HEIGHT:
        raise ValueError("mask byte count differs")
    if len(composed_bytes) != WIDTH * HEIGHT * 4:
        raise ValueError("composition byte count differs")

    mask = np.frombuffer(mask_bytes, dtype=np.uint8).reshape(HEIGHT, WIDTH)
    composed = np.frombuffer(composed_bytes, dtype=np.uint8).reshape(
        HEIGHT, WIDTH, 4
    )

    reference_bytes = reference_path.read_bytes()
    with Image.open(reference_path) as image:
        reference = np.asarray(image.convert("RGBA"))
    if reference.shape != (HEIGHT, WIDTH, 4):
        raise ValueError("reference dimensions differ")
    scored = reference[rows]
    if not (
        np.array_equal(scored[..., 0], scored[..., 1])
        and np.array_equal(scored[..., 0], scored[..., 2])
        and bool(np.all(scored[..., 3] == np.uint8(255)))
    ):
        raise ValueError("reference is not opaque grayscale below the probe band")

    mask_delta = np.abs(
        mask[rows].astype(np.int16) - scored[..., 0].astype(np.int16)
    )
    expected_composed = np.empty(scored.shape, dtype=np.uint8)
    expected_composed[..., 0] = scored[..., 2]
    expected_composed[..., 1] = scored[..., 1]
    expected_composed[..., 2] = scored[..., 0]
    expected_composed[..., 3] = 255
    composed_delta = np.abs(
        composed[rows].astype(np.int16) - expected_composed.astype(np.int16)
    )
    composed_pixel_mismatch = np.any(composed_delta != 0, axis=2)

    mismatch_y, mismatch_x = np.nonzero(mask_delta)
    return {
        "schemaVersion": 1,
        "classification": (
            "actual Walle Vulkan 1.4 process off-ladder reveal score"
        ),
        "progress": progress,
        "reference": {
            "path": str(reference_path),
            "fileSha256": sha256_bytes(reference_bytes),
        },
        "capture": {
            "root": str(capture),
            "maskSha256": sha256_bytes(mask_bytes),
            "compositionSha256": sha256_bytes(composed_bytes),
        },
        "excludedProbeRows": EXCLUDED_ROWS,
        "score": {
            "totalPixels": WIDTH * (HEIGHT - EXCLUDED_ROWS),
            "mismatchedPixels": int(mismatch_y.size),
            "absoluteError": int(mask_delta.sum()),
            "maximumError": int(mask_delta.max(initial=0)),
            "composedMismatchedPixels": int(
                np.count_nonzero(composed_pixel_mismatch)
            ),
            "composedAbsoluteError": int(composed_delta.sum()),
            "composedMaximumError": int(composed_delta.max(initial=0)),
        },
        "examples": [
            {
                "x": int(x),
                "y": int(y) + EXCLUDED_ROWS,
                "candidate": int(mask[int(y) + EXCLUDED_ROWS, x]),
                "reference": int(scored[y, x, 0]),
            }
            for y, x in zip(mismatch_y[:16], mismatch_x[:16], strict=True)
        ],
    }


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("capture", type=Path)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--progress", type=float, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--expect-mismatches", type=int)
    parser.add_argument("--expect-composed-mismatches", type=int)
    return parser.parse_args()


def main() -> int:
    arguments = parse_arguments()
    report = score(arguments.capture, arguments.reference, arguments.progress)
    encoded = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if arguments.output is not None:
        arguments.output.write_text(encoded, encoding="utf-8")
    print(encoded, end="")

    result = report["score"]
    assert isinstance(result, dict)
    checks = (
        arguments.expect_mismatches is None
        or result["mismatchedPixels"] == arguments.expect_mismatches,
        arguments.expect_composed_mismatches is None
        or result["composedMismatchedPixels"]
        == arguments.expect_composed_mismatches,
    )
    return int(not all(checks))


if __name__ == "__main__":
    raise SystemExit(main())
