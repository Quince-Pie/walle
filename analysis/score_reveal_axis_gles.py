#!/usr/bin/env python3
"""Score the analysis-only exact-axis GLES interposer on a bounded state set."""

import argparse
import hashlib
import json
import os
import subprocess
import tempfile
import time
from pathlib import Path

import numpy as np
from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
RENDERER = ROOT / "build/bin/quality/verify_reveal_best_known_gles"
INTERPOSER = (
    ROOT / "build/analysis-gles-axis/libreveal_gles_axis_interposer.so"
)
BASELINE = ROOT / "analysis/reveal_best_known_gles_corpus_gate_result.json"
CORPUS = (
    ROOT
    / "artifacts/liquid-glass-reveal-coverage-01421a3-v1/capture/sweeps"
    / "sweep__wallpaper-reveal__regular__dark"
)
WIDTH = 2_048
HEIGHT = 2_048
STATE_COUNT = 65
COMPACT_STATES = (5, 11, 16, 21, 22, 27, 32, 38, 43, 48, 54, 59)
SUBSET_STATES = tuple(sorted(set(range(1, 31)) | set(COMPACT_STATES)))

type JsonObject = dict[str, object]


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def parse_stderr(stderr: str) -> tuple[dict[str, str], dict[str, int]]:
    renderer: dict[str, str] = {}
    statistics: dict[str, int] = {}
    for line in stderr.splitlines():
        key, separator, value = line.partition("=")
        if not separator:
            continue
        if key in {"GL_VENDOR", "GL_RENDERER", "GL_VERSION"}:
            renderer[key] = value
        elif key.startswith("REVEAL_AXIS_"):
            statistics[key] = int(value)
    if len(renderer) != 3 or len(statistics) != 6:
        raise ValueError(f"incomplete renderer diagnostics: {stderr!r}")
    return renderer, statistics


def score_state(
    state: int,
    candidate_path: Path,
    mode: str,
) -> tuple[JsonObject, dict[str, str]]:
    environment = os.environ.copy()
    environment["WALLE_REVEAL_AXIS_ABLATION"] = mode
    preload = str(INTERPOSER)
    if inherited := environment.get("LD_PRELOAD"):
        preload = f"{preload}:{inherited}"
    environment["LD_PRELOAD"] = preload
    command = (
        str(RENDERER),
        "--dump-public-mask",
        str(WIDTH),
        str(HEIGHT),
        "512.0",
        "614.4",
        str(state),
        str(STATE_COUNT),
        str(candidate_path),
    )
    started = time.perf_counter()
    completed = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )
    elapsed = time.perf_counter() - started
    if completed.returncode != 0 or completed.stdout:
        error = RuntimeError(f"state {state} failed")
        error.add_note(f"status={completed.returncode}")
        error.add_note(f"stdout={completed.stdout!r}")
        error.add_note(f"stderr={completed.stderr!r}")
        raise error
    renderer, statistics = parse_stderr(completed.stderr)
    candidate_bytes = candidate_path.read_bytes()
    if len(candidate_bytes) != WIDTH * HEIGHT:
        raise ValueError(f"state {state} candidate size differs")
    candidate = np.frombuffer(candidate_bytes, dtype=np.uint8).reshape(HEIGHT, WIDTH)
    reference_path = CORPUS / f"frame-{state:04}.png"
    reference = np.asarray(Image.open(reference_path).convert("RGBA"))
    if not (
        reference.shape == (HEIGHT, WIDTH, 4)
        and np.array_equal(reference[..., 0], reference[..., 1])
        and np.array_equal(reference[..., 0], reference[..., 2])
        and bool(np.all(reference[..., 3] == np.uint8(255)))
    ):
        raise ValueError(f"state {state} reference is not opaque grayscale")
    signed = candidate.astype(np.int16) - reference[..., 0].astype(np.int16)
    absolute = np.abs(signed)
    mismatch_y, mismatch_x = np.nonzero(absolute)
    frame: JsonObject = {
        "state": state,
        "candidateSha256": sha256_bytes(candidate_bytes),
        "referenceSha256": sha256_file(reference_path),
        "mismatchedPixels": int(mismatch_y.size),
        "absoluteError": int(absolute.sum()),
        "maximumError": int(absolute.max(initial=0)),
        "positiveDeltaCount": int(np.count_nonzero(signed > 0)),
        "negativeDeltaCount": int(np.count_nonzero(signed < 0)),
        "renderSeconds": elapsed,
        "axisStatistics": statistics,
        "examples": [
            {
                "x": int(x),
                "y": int(y),
                "candidate": int(candidate[y, x]),
                "reference": int(reference[y, x, 0]),
                "signedDelta": int(signed[y, x]),
            }
            for y, x in zip(mismatch_y[:16], mismatch_x[:16], strict=True)
        ],
    }
    return frame, renderer


def score(states: tuple[int, ...], mode: str) -> JsonObject:
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    baseline_frames = {int(frame["state"]): frame for frame in baseline["frames"]}
    frames: list[JsonObject] = []
    renderer_metadata: dict[str, str] | None = None
    with tempfile.TemporaryDirectory(prefix="walle-axis-gles-") as temporary:
        candidate_path = Path(temporary) / "candidate.r8"
        for state in states:
            frame, observed_renderer = score_state(state, candidate_path, mode)
            if renderer_metadata is None:
                renderer_metadata = observed_renderer
            elif observed_renderer != renderer_metadata:
                raise ValueError("renderer metadata changed during score")
            frames.append(frame)

    counts = [int(frame["mismatchedPixels"]) for frame in frames]
    count_encoding = (json.dumps(counts, separators=(",", ":")) + "\n").encode()
    baseline_selected = [baseline_frames[state] for state in states]
    total_pixels = len(states) * WIDTH * HEIGHT
    mismatches = sum(counts)
    statistic_keys = tuple(frames[0]["axisStatistics"])
    aggregate_statistics = {
        key: sum(int(frame["axisStatistics"][key]) for frame in frames)
        for key in statistic_keys
        if key != "REVEAL_AXIS_MAX_TEXTURE_BYTES"
    }
    aggregate_statistics["REVEAL_AXIS_MAX_TEXTURE_BYTES"] = max(
        int(frame["axisStatistics"]["REVEAL_AXIS_MAX_TEXTURE_BYTES"])
        for frame in frames
    )
    return {
        "schemaVersion": 1,
        "classification": f"analysis-only exact public-input AGX axis GLES {mode} ablation",
        "candidateCompletedBeforeReferenceOpened": True,
        "perStateOrReferencePixelCorrectionLookupUsed": False,
        "states": list(states),
        "compactStates": list(COMPACT_STATES),
        "implementation": {
            "renderer": renderer_metadata,
            "rendererSha256": sha256_file(RENDERER),
            "interposerSha256": sha256_file(INTERPOSER),
            "interposerSourceSha256": sha256_file(
                ROOT / "analysis/reveal_gles_axis_interposer.c"
            ),
            "scorerSourceSha256": sha256_file(Path(__file__)),
            "rasterSourceSha256": sha256_file(
                ROOT / "parity/liquid_glass_raster.c"
            ),
            "p25BitmapSha256": sha256_file(
                ROOT / "lg-test/Analysis/raster_p25_selector_ceil_bits.bin"
            ),
            "appleFastSqrtTableSha256": (
                sha256_file(
                    ROOT / "artifacts/apple-float-intrinsics-r8-30556057571.bin"
                )
                if mode.startswith("owner-xor-apple-sqrt")
                else None
            ),
            "coordinateLaw": (
                "public uploaded geometry -> exact P25 selector -> admitted AGX "
                "axis center iterator; native GLES length; "
                + (
                    "native GLES derivatives"
                    if mode == "native"
                    else (
                        "exact top-left primitive owner and XOR helper partners; "
                        "exact IEEE seed plus admitted Apple fast-sqrt correction"
                        if mode.startswith("owner-xor-apple-sqrt")
                        else "exact top-left primitive owner and XOR helper partners"
                    )
                )
            ),
            "mode": mode,
        },
        "reference": {
            "candidateInventorySha256": sha256_bytes(
                b"".join(
                    f"state-{int(frame['state']):04}.r8\0".encode()
                    + bytes.fromhex(str(frame["candidateSha256"]))
                    for frame in frames
                )
            ),
            "perStateMismatchCountSha256": sha256_bytes(count_encoding),
        },
        "score": {
            "totalPixels": total_pixels,
            "exactPixels": total_pixels - mismatches,
            "exactPixelPercentage": (total_pixels - mismatches) * 100.0 / total_pixels,
            "mismatchedPixels": mismatches,
            "absoluteError": sum(int(frame["absoluteError"]) for frame in frames),
            "maximumError": max(int(frame["maximumError"]) for frame in frames),
            "positiveDeltaCount": sum(
                int(frame["positiveDeltaCount"]) for frame in frames
            ),
            "negativeDeltaCount": sum(
                int(frame["negativeDeltaCount"]) for frame in frames
            ),
            "exactFrameCount": sum(count == 0 for count in counts),
            "renderSeconds": sum(float(frame["renderSeconds"]) for frame in frames),
        },
        "baseline": {
            "report": str(BASELINE),
            "mismatchedPixels": sum(
                int(frame["mismatchedPixels"]) for frame in baseline_selected
            ),
            "absoluteError": sum(int(frame["absoluteError"]) for frame in baseline_selected),
            "maximumError": max(int(frame["maximumError"]) for frame in baseline_selected),
            "candidateFrameHashMatchCount": sum(
                str(frame["candidateSha256"])
                == str(baseline_frames[int(frame["state"])]["candidateSha256"])
                for frame in frames
            ),
        },
        "axisStatistics": aggregate_statistics,
        "frames": frames,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--matrix", choices=("subset", "full"), default="subset")
    parser.add_argument(
        "--mode",
        choices=(
            "native",
            "owner-xor",
            "owner-xor-apple-sqrt",
            "owner-xor-apple-sqrt-exact-div",
        ),
        default="native",
    )
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    arguments = parse_args()
    states = SUBSET_STATES if arguments.matrix == "subset" else tuple(range(STATE_COUNT))
    report = score(states, arguments.mode)
    arguments.output.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report["score"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
