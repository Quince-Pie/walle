#!/usr/bin/env python3
"""Compare the strongest exact-axis GLES candidate with canonical CPU91."""

import argparse
import hashlib
import json
import os
import subprocess
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image

import compare_reveal_gles_cpu91_inventory as common


ROOT = common.ROOT
MODE = "owner-xor-apple-sqrt"
AXIS_REPORT = ROOT / "build/analysis-gles-axis/owner-xor-apple-sqrt-full-result.json"
INTERPOSER = ROOT / "build/analysis-gles-axis/libreveal_gles_axis_interposer.so"


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def compare() -> dict[str, object]:
    axis_report = json.loads(AXIS_REPORT.read_text(encoding="utf-8"))
    axis_frames = {int(frame["state"]): frame for frame in axis_report["frames"]}
    base = common.cpu_raster.reveal.raster_arithmetic.load_selector_table()
    bitmap = common.cpu_raster.reveal.P25_BITMAP.read_bytes()
    environment = os.environ.copy()
    environment["WALLE_REVEAL_AXIS_ABLATION"] = MODE
    preload = str(INTERPOSER)
    if inherited := environment.get("LD_PRELOAD"):
        preload = f"{preload}:{inherited}"
    environment["LD_PRELOAD"] = preload
    frames: list[dict[str, int]] = []
    axis_inventory: list[bytes] = []
    cpu_inventory: list[bytes] = []
    with tempfile.TemporaryDirectory(prefix="walle-axis-cpu91-") as temporary:
        candidate_path = Path(temporary) / "axis.r8"
        for state in range(common.STATE_COUNT):
            command = (
                str(common.RENDERER),
                "--dump-public-mask",
                str(common.WIDTH),
                str(common.HEIGHT),
                "512.0",
                "614.4",
                str(state),
                str(common.STATE_COUNT),
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
                raise RuntimeError(f"axis state {state} failed: {completed.stderr!r}")
            candidate_bytes = candidate_path.read_bytes()
            if sha256_bytes(candidate_bytes) != axis_frames[state]["candidateSha256"]:
                raise ValueError(f"axis state {state} differs from its score report")
            axis = np.frombuffer(candidate_bytes, np.uint8).reshape(
                common.HEIGHT, common.WIDTH
            )
            cpu, _ = common.cpu_raster.render_public_state(
                state,
                base=base,
                bitmap=bitmap,
            )
            reference = np.asarray(
                Image.open(common.CORPUS / f"frame-{state:04}.png").convert("RGBA")
            )[..., 0]
            axis_residual = axis != reference
            cpu_residual = cpu != reference
            intersection = axis_residual & cpu_residual
            axis_coordinates = np.argwhere(axis_residual).astype("<u2", copy=False)
            cpu_coordinates = np.argwhere(cpu_residual).astype("<u2", copy=False)
            axis_inventory.append(state.to_bytes(2, "little") + axis_coordinates.tobytes())
            cpu_inventory.append(state.to_bytes(2, "little") + cpu_coordinates.tobytes())
            frames.append(
                {
                    "state": state,
                    "axisResiduals": int(np.count_nonzero(axis_residual)),
                    "cpu91Residuals": int(np.count_nonzero(cpu_residual)),
                    "intersection": int(np.count_nonzero(intersection)),
                    "axisOnly": int(np.count_nonzero(axis_residual & ~cpu_residual)),
                    "cpu91Only": int(np.count_nonzero(cpu_residual & ~axis_residual)),
                    "union": int(np.count_nonzero(axis_residual | cpu_residual)),
                }
            )

    def total(field: str) -> int:
        return sum(frame[field] for frame in frames)

    return {
        "schemaVersion": 1,
        "classification": "exact-axis Apple-sqrt GLES vs canonical CPU91 coordinate inventory",
        "mode": MODE,
        "axisReport": str(AXIS_REPORT),
        "axisResiduals": total("axisResiduals"),
        "cpu91Residuals": total("cpu91Residuals"),
        "intersection": total("intersection"),
        "axisOnly": total("axisOnly"),
        "cpu91Only": total("cpu91Only"),
        "union": total("union"),
        "verification": {
            "allAxisFrameHashesMatchReport": True,
            "axisResidualCoordinateInventorySha256": sha256_bytes(
                b"".join(axis_inventory)
            ),
            "cpu91ResidualCoordinateInventorySha256": sha256_bytes(
                b"".join(cpu_inventory)
            ),
        },
        "frames": frames,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    result = compare()
    arguments.output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                key: result[key]
                for key in (
                    "axisResiduals",
                    "cpu91Residuals",
                    "intersection",
                    "axisOnly",
                    "cpu91Only",
                    "union",
                )
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
