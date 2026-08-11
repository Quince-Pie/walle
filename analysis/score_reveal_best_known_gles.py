#!/usr/bin/env python3
"""Score Walle's production GLES reveal mask against the retained corpus."""

import argparse
import hashlib
import json
import subprocess
import tempfile
import time
from pathlib import Path

import numpy as np
from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RENDERER = ROOT / "build/bin/quality/verify_reveal_best_known_gles"
DEFAULT_CORPUS = (
    ROOT
    / "artifacts/liquid-glass-reveal-coverage-01421a3-v1/capture/sweeps"
    / "sweep__wallpaper-reveal__regular__dark"
)
WIDTH = 2_048
HEIGHT = 2_048
STATE_COUNT = 65
CENTER_X = 512.0
CENTER_Y = 614.4

type JsonObject = dict[str, object]


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def parse_renderer_metadata(stderr: str) -> dict[str, str]:
    metadata: dict[str, str] = {}
    for line in stderr.splitlines():
        key, separator, value = line.partition("=")
        if separator and key in {"GL_VENDOR", "GL_RENDERER", "GL_VERSION"}:
            metadata[key] = value
    if set(metadata) != {"GL_VENDOR", "GL_RENDERER", "GL_VERSION"}:
        raise ValueError("renderer metadata is incomplete")
    return metadata


def score_state(
    renderer: Path,
    corpus: Path,
    state: int,
    candidate_path: Path,
) -> tuple[JsonObject, dict[str, str]]:
    command = (
        str(renderer),
        "--dump-public-mask",
        str(WIDTH),
        str(HEIGHT),
        repr(CENTER_X),
        repr(CENTER_Y),
        str(state),
        str(STATE_COUNT),
        str(candidate_path),
    )
    started = time.perf_counter()
    completed = subprocess.run(command, check=False, capture_output=True, text=True)
    render_seconds = time.perf_counter() - started
    if completed.returncode != 0 or completed.stdout:
        error = RuntimeError(f"GLES state {state} render failed")
        error.add_note(f"status={completed.returncode}")
        error.add_note(f"stdout={completed.stdout!r}")
        error.add_note(f"stderr={completed.stderr!r}")
        raise error

    candidate_bytes = candidate_path.read_bytes()
    if len(candidate_bytes) != WIDTH * HEIGHT:
        raise ValueError(f"state {state} candidate byte count differs")
    candidate = np.frombuffer(candidate_bytes, dtype=np.uint8).reshape(HEIGHT, WIDTH)

    reference_path = corpus / f"frame-{state:04}.png"
    reference_bytes = reference_path.read_bytes()
    reference = np.asarray(Image.open(reference_path).convert("RGBA"))
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
    examples = [
        {
            "x": int(x),
            "y": int(y),
            "candidate": int(candidate[y, x]),
            "reference": int(reference[y, x, 0]),
            "signedDelta": int(signed_delta[y, x]),
        }
        for y, x in zip(mismatch_y[:16], mismatch_x[:16], strict=True)
    ]
    return (
        {
            "state": state,
            "candidateSha256": sha256_bytes(candidate_bytes),
            "referenceSha256": sha256_bytes(reference_bytes),
            "mismatchedPixels": int(mismatch_y.size),
            "absoluteError": int(absolute_delta.sum()),
            "maximumError": int(absolute_delta.max(initial=0)),
            "positiveDeltaCount": int(np.count_nonzero(signed_delta > 0)),
            "negativeDeltaCount": int(np.count_nonzero(signed_delta < 0)),
            "renderSeconds": render_seconds,
            "examples": examples,
        },
        parse_renderer_metadata(completed.stderr),
    )


def score_corpus(renderer: Path, corpus: Path) -> JsonObject:
    if not renderer.is_file():
        raise FileNotFoundError(f"renderer is absent: {renderer}")
    if not corpus.is_dir():
        raise FileNotFoundError(f"corpus is absent: {corpus}")

    frames: list[JsonObject] = []
    renderer_metadata: dict[str, str] | None = None
    with tempfile.TemporaryDirectory(prefix="walle-reveal-gles-") as temporary:
        candidate_path = Path(temporary) / "candidate.r8"
        for state in range(STATE_COUNT):
            frame, observed_metadata = score_state(
                renderer,
                corpus,
                state,
                candidate_path,
            )
            if renderer_metadata is None:
                renderer_metadata = observed_metadata
            elif observed_metadata != renderer_metadata:
                raise ValueError("renderer metadata changed within the matrix")
            frames.append(frame)

    total_pixels = STATE_COUNT * WIDTH * HEIGHT
    mismatched_pixels = sum(int(frame["mismatchedPixels"]) for frame in frames)
    absolute_error = sum(int(frame["absoluteError"]) for frame in frames)
    maximum_error = max(int(frame["maximumError"]) for frame in frames)
    mismatch_counts = [int(frame["mismatchedPixels"]) for frame in frames]
    mismatch_encoding = (
        json.dumps(mismatch_counts, separators=(",", ":")).encode() + b"\n"
    )
    return {
        "schemaVersion": 1,
        "classification": "actual production GLES reveal-mask shader retrospective score",
        "candidateCompletedBeforeReferenceOpened": True,
        "perStateOrReferencePixelCorrectionLookupUsed": False,
        "target": {
            "width": WIDTH,
            "height": HEIGHT,
            "center": [CENTER_X, CENTER_Y],
            "stateCount": STATE_COUNT,
            "progressLaw": "state / (stateCount - 1)",
            "radiusLaw": "max corner hypot * binary64 1.03",
        },
        "implementation": {
            "rendererBinary": str(renderer),
            "rendererBinarySha256": sha256_file(renderer),
            "scorerSourceSha256": sha256_file(Path(__file__)),
            "gateSourceSha256": sha256_file(
                ROOT / "analysis/verify_reveal_best_known_gles.c"
            ),
            "modelSourceSha256": sha256_file(
                ROOT / "parity/liquid_glass_reveal_mask_model.c"
            ),
            "modelHeaderSha256": sha256_file(
                ROOT / "parity/liquid_glass_reveal_mask_model.h"
            ),
            "postguardSourceSha256": sha256_file(
                ROOT / "parity/liquid_glass_postguard.c"
            ),
            "postguardHeaderSha256": sha256_file(
                ROOT / "parity/liquid_glass_postguard.h"
            ),
            "rasterSourceSha256": sha256_file(
                ROOT / "parity/liquid_glass_raster.c"
            ),
            "rasterHeaderSha256": sha256_file(
                ROOT / "parity/liquid_glass_raster.h"
            ),
            "p25CalibrationSha256": sha256_file(
                ROOT / "parity/raster_p25_selector_ceil_bits.bin"
            ),
            "appleFastSqrtTableSha256": sha256_file(
                ROOT / "parity/apple_fast_sqrt_correction_nibbles.bin"
            ),
            "vertexShaderSha256": sha256_file(ROOT / "shaders/reveal_mask.vert.glsl"),
            "fragmentShaderSha256": sha256_file(ROOT / "shaders/reveal_mask.frag.glsl"),
            "renderer": renderer_metadata,
        },
        "reference": {
            "root": str(corpus),
            "opaqueGrayscaleInvariantVerified": True,
            "frameInventorySha256": sha256_bytes(
                b"".join(
                    f"frame-{state:04}.png\0".encode()
                    + bytes.fromhex(str(frame["referenceSha256"]))
                    for state, frame in enumerate(frames)
                )
            ),
            "candidateInventorySha256": sha256_bytes(
                b"".join(
                    f"state-{state:04}.r8\0".encode()
                    + bytes.fromhex(str(frame["candidateSha256"]))
                    for state, frame in enumerate(frames)
                )
            ),
        },
        "score": {
            "totalPixels": total_pixels,
            "exactPixels": total_pixels - mismatched_pixels,
            "mismatchedPixels": mismatched_pixels,
            "exactPixelPercentage": (total_pixels - mismatched_pixels)
            * 100.0
            / total_pixels,
            "absoluteError": absolute_error,
            "maximumError": maximum_error,
            "exactFrameCount": sum(count == 0 for count in mismatch_counts),
            "positiveDeltaCount": sum(
                int(frame["positiveDeltaCount"]) for frame in frames
            ),
            "negativeDeltaCount": sum(
                int(frame["negativeDeltaCount"]) for frame in frames
            ),
            "perStateMismatchCountSha256": sha256_bytes(mismatch_encoding),
            "renderSeconds": sum(float(frame["renderSeconds"]) for frame in frames),
        },
        "scope": {
            "sameModelAndMaskShadersAsOrdinaryWalle": True,
            "surfacelessGlesInsteadOfLayerShellSurface": True,
            "compositionShaderRendered": False,
            "ordinaryWalleProcessRendered": False,
            "physicalPresentationRendered": False,
            "formalParityEstablished": False,
        },
        "frames": frames,
    }


def require_baseline(report: JsonObject, baseline_path: Path) -> None:
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    if not isinstance(baseline, dict):
        raise TypeError("baseline root is not an object")
    paths = (
        ("implementation", "rendererBinarySha256"),
        ("implementation", "scorerSourceSha256"),
        ("implementation", "gateSourceSha256"),
        ("implementation", "modelSourceSha256"),
        ("implementation", "modelHeaderSha256"),
        ("implementation", "postguardSourceSha256"),
        ("implementation", "postguardHeaderSha256"),
        ("implementation", "rasterSourceSha256"),
        ("implementation", "rasterHeaderSha256"),
        ("implementation", "p25CalibrationSha256"),
        ("implementation", "appleFastSqrtTableSha256"),
        ("implementation", "vertexShaderSha256"),
        ("implementation", "fragmentShaderSha256"),
        ("implementation", "renderer"),
        ("reference", "frameInventorySha256"),
        ("reference", "candidateInventorySha256"),
        ("score", "exactPixels"),
        ("score", "mismatchedPixels"),
        ("score", "absoluteError"),
        ("score", "maximumError"),
        ("score", "exactFrameCount"),
        ("score", "positiveDeltaCount"),
        ("score", "negativeDeltaCount"),
        ("score", "perStateMismatchCountSha256"),
    )
    for section, field in paths:
        actual_section = report.get(section)
        baseline_section = baseline.get(section)
        if not isinstance(actual_section, dict) or not isinstance(
            baseline_section, dict
        ):
            raise TypeError(f"baseline section differs: {section}")
        if actual_section.get(field) != baseline_section.get(field):
            raise ValueError(f"baseline field differs: {section}.{field}")


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--renderer", type=Path, default=DEFAULT_RENDERER)
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--expect-mismatches", type=int)
    parser.add_argument("--baseline", type=Path)
    return parser.parse_args()


def main() -> int:
    arguments = parse_arguments()
    report = score_corpus(arguments.renderer, arguments.corpus)
    encoded = json.dumps(report, indent=2, sort_keys=True) + "\n"
    print(encoded, end="")
    if arguments.output is not None:
        arguments.output.write_text(encoded, encoding="utf-8")
    if arguments.baseline is not None:
        require_baseline(report, arguments.baseline)
    expected = arguments.expect_mismatches
    if expected is None:
        return 0
    return int(report["score"]["mismatchedPixels"] != expected)


if __name__ == "__main__":
    raise SystemExit(main())
