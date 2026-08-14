#!/usr/bin/env python3
"""Compare current production GLES residual coordinates with canonical CPU91."""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "lg-test/Analysis")]

import score_reveal_v74_public_geometry as public_geometry  # noqa: E402
import score_reveal_v74_public_raster as cpu_raster  # noqa: E402


WIDTH = 2_048
HEIGHT = 2_048
STATE_COUNT = 65
RENDERER = ROOT / "build/bin/quality/verify_reveal_best_known_gles"
BASELINE = ROOT / "analysis/reveal_best_known_gles_corpus_gate_result.json"
CORPUS = (
    ROOT
    / "artifacts/liquid-glass-reveal-coverage-01421a3-v1/capture/sweeps"
    / "sweep__wallpaper-reveal__regular__dark"
)
COMPACT_STATES = (5, 11, 16, 21, 22, 27, 32, 38, 43, 48, 54, 59)
SETUP_UNAFFECTED_STATES = tuple(sorted(set(range(1, 31)) | set(COMPACT_STATES)))


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def compare() -> dict[str, object]:
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    baseline_frames = {int(frame["state"]): frame for frame in baseline["frames"]}
    base = cpu_raster.reveal.raster_arithmetic.load_selector_table()
    bitmap = cpu_raster.reveal.P25_BITMAP.read_bytes()
    frames: list[dict[str, object]] = []
    production_inventory: list[bytes] = []
    cpu_inventory: list[bytes] = []
    environment = os.environ.copy()
    environment.pop("WALLE_REVEAL_AXIS_ABLATION", None)

    with tempfile.TemporaryDirectory(prefix="walle-gles-cpu91-") as temporary:
        candidate_path = Path(temporary) / "production.r8"
        for state in range(STATE_COUNT):
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
            completed = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )
            if completed.returncode != 0 or completed.stdout:
                raise RuntimeError(
                    f"production render {state} failed: {completed.stderr!r}"
                )
            production_bytes = candidate_path.read_bytes()
            production_hash = sha256_bytes(production_bytes)
            if production_hash != baseline_frames[state]["candidateSha256"]:
                raise ValueError(f"production state {state} differs from baseline report")
            production = np.frombuffer(production_bytes, np.uint8).reshape(
                HEIGHT, WIDTH
            )
            cpu, unsupported = cpu_raster.render_public_state(
                state,
                base=base,
                bitmap=bitmap,
            )
            reference = np.asarray(
                Image.open(CORPUS / f"frame-{state:04}.png").convert("RGBA")
            )[..., 0]
            production_residual = production != reference
            cpu_residual = cpu != reference
            intersection = production_residual & cpu_residual
            production_only = production_residual & ~cpu_residual
            cpu_only = cpu_residual & ~production_residual
            production_coordinates = np.argwhere(production_residual).astype(
                "<u2", copy=False
            )
            cpu_coordinates = np.argwhere(cpu_residual).astype("<u2", copy=False)
            production_inventory.append(
                state.to_bytes(2, "little") + production_coordinates.tobytes()
            )
            cpu_inventory.append(state.to_bytes(2, "little") + cpu_coordinates.tobytes())
            frames.append(
                {
                    "state": state,
                    "productionResiduals": int(np.count_nonzero(production_residual)),
                    "cpu91Residuals": int(np.count_nonzero(cpu_residual)),
                    "intersection": int(np.count_nonzero(intersection)),
                    "productionOnly": int(np.count_nonzero(production_only)),
                    "cpu91Only": int(np.count_nonzero(cpu_only)),
                    "union": int(np.count_nonzero(production_residual | cpu_residual)),
                    "unsupportedPostGuardSetupCount": unsupported,
                }
            )

    def total(field: str) -> int:
        return sum(int(frame[field]) for frame in frames)

    production_count = total("productionResiduals")
    cpu_count = total("cpu91Residuals")
    strict_lower_bound = sum(
        int(frames[state]["productionResiduals"])
        for state in SETUP_UNAFFECTED_STATES
    )
    cpu_counts = [int(frame["cpu91Residuals"]) for frame in frames]
    return {
        "schemaVersion": 1,
        "classification": "coordinate inventory comparison; no tolerance",
        "productionReport": str(BASELINE),
        "cpu91Model": str(ROOT / "lg-test/Analysis/score_reveal_v74_public_raster.py"),
        "productionResiduals": production_count,
        "cpu91Residuals": cpu_count,
        "intersection": total("intersection"),
        "productionOnly": total("productionOnly"),
        "cpu91Only": total("cpu91Only"),
        "union": total("union"),
        "strictSetupUnaffectedLowerBound": {
            "states": list(SETUP_UNAFFECTED_STATES),
            "productionResiduals": strict_lower_bound,
            "fractionOfProductionResiduals": strict_lower_bound / production_count,
            "basis": (
                "states 1-30 precede the post-guard setup frontier; all compact "
                "visible-arc states use completed axis-separable right triangles"
            ),
        },
        "verification": {
            "allProductionFrameHashesMatchReport": True,
            "cpu91CountMatchesCanonical": cpu_count == 91,
            "cpu91PerStateMismatchCountSha256": sha256_bytes(
                (json.dumps(cpu_counts, separators=(",", ":")) + "\n").encode()
            ),
            "productionResidualCoordinateInventorySha256": sha256_bytes(
                b"".join(production_inventory)
            ),
            "cpu91ResidualCoordinateInventorySha256": sha256_bytes(
                b"".join(cpu_inventory)
            ),
        },
        "frames": frames,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    arguments = parse_args()
    result = compare()
    arguments.output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {key: result[key] for key in (
                "productionResiduals",
                "cpu91Residuals",
                "intersection",
                "productionOnly",
                "cpu91Only",
                "union",
                "strictSetupUnaffectedLowerBound",
            )},
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
