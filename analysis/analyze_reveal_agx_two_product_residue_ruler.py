#!/usr/bin/env python3.14
"""Analyze the controlled-residue M1 AGX two-product ruler capture."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Final

import analyze_reveal_agx_two_product_ruler as ruler


type JsonObject = dict[str, object]

ROOT: Final = Path(__file__).resolve().parent.parent
PLAN_ROOT: Final = (
    ROOT / "build" / "analysis-agx-basis" / "two-product-residue-ruler-plan-v1"
)
CAPTURE_ROOT: Final = (
    ROOT / "build" / "analysis-agx-basis" / "macos-two-product-residue-ruler-v1"
)
OUTPUT: Final = (
    ROOT
    / "build"
    / "analysis-agx-basis"
    / "two-product-residue-ruler-analysis"
    / "result.json"
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(4 * 1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def analyze() -> JsonObject:
    ruler.PLAN_ROOT = PLAN_ROOT
    ruler.CAPTURE_ROOT = CAPTURE_ROOT
    ruler.PLAN = CAPTURE_ROOT / "reveal-agx-setup-accumulator-plan.json"
    ruler.VERTICES = CAPTURE_ROOT / "reveal-agx-setup-accumulator-vertices.bin"
    ruler.RAW = CAPTURE_ROOT / "capture" / "reveal-agx-setup-accumulator.raw"
    ruler.CAPTURE_MANIFEST = CAPTURE_ROOT / "capture" / "manifest.json"
    ruler.STDERR = CAPTURE_ROOT / "capture.stderr"
    ruler.STDOUT = CAPTURE_ROOT / "capture.stdout"
    ruler.INTERPOSER = CAPTURE_ROOT / "libwalle-agx-ldcf-export.dylib"
    ruler.EXECUTABLE = CAPTURE_ROOT / "reveal-agx-setup-accumulator-probe"
    ruler.DRAW_COUNT = 8_191
    ruler.EXPECTED_CENSUS = {
        "candidateCount": 8_192,
        "skippedCount": 1,
        "discoveryPatternCount": 6_140,
        "holdoutPatternCount": 2_051,
    }
    ruler.EXPECTED = {
        PLAN_ROOT
        / "manifest.json": "15e83c474dbccc73acfa0c293ad206c79dba8627e8b67ab72ff3e8032a46ef87",
        ruler.PLAN: "2c44d58b5360a3c09108b1684d2a81e1392ccaf4bb024621bec622f645d6bd3e",
        ruler.VERTICES: "fcff4177591c60a62bb8d2686fea3c4a1df773bc458d7f029a0200ea4fc1c0dd",
        ruler.RAW: "9d08989711c19b94b7e45340857686a97439a67fe6a24461ccd256fda61ffbb9",
        ruler.CAPTURE_MANIFEST: "760d079317a432f2d71f917b0369aa820e0192c886f80b67cb22da31a1674d12",
        ruler.STDERR: "9306db645593b2df7249794016822fec67735cc05e6468c722553c0419f890c2",
        ruler.STDOUT: "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        ruler.INTERPOSER: "9ead667be857c2fa3ed8a9b110d6d33edb24cf6d7ddf575427d17740e0ff1e8f",
        ruler.EXECUTABLE: "0eba15db7f872a845398f91cacce7446f9e92cbd22276da3e42e5b9148be4ea9",
    }
    result = ruler.analyze()
    result["schema"] = "walle-reveal-agx-two-product-residue-ruler-analysis-v1"
    inputs = result.get("inputs")
    if not isinstance(inputs, dict):
        raise ValueError("residue ruler inputs are absent")
    inputs["entrypointAnalyzer"] = {
        "path": Path(__file__).relative_to(ROOT).as_posix(),
        "bytes": Path(__file__).stat().st_size,
        "sha256": _sha256(Path(__file__)),
    }
    result["classification"] = (
        "output-blind M1 two-product reduction with one controlled discarded residue"
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    arguments = parser.parse_args()
    result = analyze()
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(result["census"], indent=2, sort_keys=True))
    print(json.dumps(result["rulerAnalysis"], indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
