#!/usr/bin/env python3
"""Score a completed in-process Vulkan reveal-mask capture."""

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CORPUS = (
    ROOT
    / "artifacts/liquid-glass-reveal-coverage-01421a3-v1/capture/sweeps"
    / "sweep__wallpaper-reveal__regular__dark"
)
WIDTH = 2_048
HEIGHT = 2_048
STATE_COUNT = 65
EXPECTED_NAMES = tuple(f"state-{state:04}.r8" for state in range(STATE_COUNT))
COMPOSITION_NAME = "composition-state-0032.bgra"

type JsonObject = dict[str, object]


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def score_capture(capture: Path, corpus: Path) -> JsonObject:
    if not capture.is_dir():
        raise NotADirectoryError(capture)
    if not corpus.is_dir():
        raise NotADirectoryError(corpus)

    actual_names = tuple(sorted(path.name for path in capture.iterdir()))
    expected_with_composition = tuple(sorted((*EXPECTED_NAMES, COMPOSITION_NAME)))
    if actual_names not in (EXPECTED_NAMES, expected_with_composition):
        raise ValueError("capture file inventory differs")

    candidate_hashes: list[str] = []
    for name in EXPECTED_NAMES:
        candidate = (capture / name).read_bytes()
        if len(candidate) != WIDTH * HEIGHT:
            raise ValueError(f"{name} byte count differs")
        candidate_hashes.append(sha256_bytes(candidate))

    frames: list[JsonObject] = []
    reference_hashes: list[str] = []
    mismatch_counts: list[int] = []
    total_absolute_error = 0
    maximum_error = 0
    positive_count = 0
    negative_count = 0

    for state, name in enumerate(EXPECTED_NAMES):
        candidate_bytes = (capture / name).read_bytes()
        candidate = np.frombuffer(candidate_bytes, dtype=np.uint8).reshape(
            HEIGHT, WIDTH
        )

        reference_path = corpus / f"frame-{state:04}.png"
        reference_bytes = reference_path.read_bytes()
        reference_hashes.append(sha256_bytes(reference_bytes))
        with Image.open(reference_path) as image:
            reference = np.asarray(image.convert("RGBA"))
        if reference.shape != (HEIGHT, WIDTH, 4):
            raise ValueError(f"state {state} reference dimensions differ")
        if not (
            np.array_equal(reference[..., 0], reference[..., 1])
            and np.array_equal(reference[..., 0], reference[..., 2])
            and bool(np.all(reference[..., 3] == np.uint8(255)))
        ):
            raise ValueError(f"state {state} reference is not opaque grayscale")

        signed_delta = candidate.astype(np.int16) - reference[..., 0].astype(np.int16)
        absolute_delta = np.abs(signed_delta)
        mismatch_y, mismatch_x = np.nonzero(absolute_delta)
        mismatch_count = int(mismatch_y.size)
        mismatch_counts.append(mismatch_count)
        total_absolute_error += int(absolute_delta.sum())
        maximum_error = max(maximum_error, int(absolute_delta.max(initial=0)))
        positive_count += int(np.count_nonzero(signed_delta > 0))
        negative_count += int(np.count_nonzero(signed_delta < 0))
        frames.append(
            {
                "state": state,
                "candidateSha256": candidate_hashes[state],
                "referenceSha256": reference_hashes[state],
                "mismatchedPixels": mismatch_count,
                "examples": [
                    {
                        "x": int(x),
                        "y": int(y),
                        "candidate": int(candidate[y, x]),
                        "reference": int(reference[y, x, 0]),
                        "signedDelta": int(signed_delta[y, x]),
                    }
                    for y, x in zip(mismatch_y[:16], mismatch_x[:16], strict=True)
                ],
            }
        )

    total_pixels = STATE_COUNT * WIDTH * HEIGHT
    mismatched_pixels = sum(mismatch_counts)
    mismatch_encoding = (
        json.dumps(mismatch_counts, separators=(",", ":")).encode() + b"\n"
    )
    candidate_inventory = b"".join(
        name.encode() + b"\0" + bytes.fromhex(candidate_hash)
        for name, candidate_hash in zip(EXPECTED_NAMES, candidate_hashes, strict=True)
    )
    reference_inventory = b"".join(
        f"frame-{state:04}.png\0".encode() + bytes.fromhex(reference_hash)
        for state, reference_hash in enumerate(reference_hashes)
    )
    return {
        "schemaVersion": 1,
        "classification": "actual Walle Vulkan 1.4 process reveal-mask score",
        "candidateCaptureCompletedBeforeReferenceOpened": True,
        "perStateOrReferencePixelCorrectionLookupUsed": False,
        "target": {
            "width": WIDTH,
            "height": HEIGHT,
            "centerTopLeft": [512.0, 614.4],
            "stateCount": STATE_COUNT,
            "progressLaw": "state / 64",
        },
        "reference": {
            "root": str(corpus),
            "opaqueGrayscaleInvariantVerified": True,
            "frameInventorySha256": sha256_bytes(reference_inventory),
        },
        "capture": {
            "root": str(capture),
            "candidateInventorySha256": sha256_bytes(candidate_inventory),
            "renderer": "Vulkan 1.4 / offline Slang / SPIR-V 1.6",
        },
        "score": {
            "totalPixels": total_pixels,
            "exactPixels": total_pixels - mismatched_pixels,
            "mismatchedPixels": mismatched_pixels,
            "exactPixelPercentage": (total_pixels - mismatched_pixels)
            * 100.0
            / total_pixels,
            "absoluteError": total_absolute_error,
            "maximumError": maximum_error,
            "exactFrameCount": sum(count == 0 for count in mismatch_counts),
            "positiveDeltaCount": positive_count,
            "negativeDeltaCount": negative_count,
            "perStateMismatchCountSha256": sha256_bytes(mismatch_encoding),
        },
        "scope": {
            "actualWalleExecutableRendered": True,
            "actualLayerShellSurfaceRendered": True,
            "compositionRenderedAndPresented": True,
            "maskReadbackScored": True,
            "composedSwapchainPixelsScored": False,
            "physicalPresentationScored": False,
            "formalParityEstablished": False,
        },
        "frames": frames,
    }


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("capture", type=Path)
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--expect-mismatches", type=int)
    parser.add_argument("--expect-candidate-inventory")
    parser.add_argument("--expect-count-hash")
    return parser.parse_args()


def main() -> int:
    arguments = parse_arguments()
    report = score_capture(arguments.capture, arguments.corpus)
    encoded = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if arguments.output is not None:
        arguments.output.write_text(encoded, encoding="utf-8")
    print(encoded, end="")

    score = report["score"]
    capture = report["capture"]
    assert isinstance(score, dict)
    assert isinstance(capture, dict)
    checks = (
        arguments.expect_mismatches is None
        or score["mismatchedPixels"] == arguments.expect_mismatches,
        arguments.expect_candidate_inventory is None
        or capture["candidateInventorySha256"] == arguments.expect_candidate_inventory,
        arguments.expect_count_hash is None
        or score["perStateMismatchCountSha256"] == arguments.expect_count_hash,
    )
    return int(not all(checks))


if __name__ == "__main__":
    raise SystemExit(main())
