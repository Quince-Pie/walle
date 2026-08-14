#!/usr/bin/env python3.14
"""Analyze the dense mantissa sweep on a correction-bearing public AGX child."""

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
    ROOT / "build" / "analysis-agx-basis" / "public-child-mantissa-ruler-plan-v1"
)
CAPTURE_ROOT: Final = (
    ROOT / "build" / "analysis-agx-basis" / "macos-public-child-mantissa-ruler-v1"
)
OUTPUT: Final = (
    ROOT
    / "build"
    / "analysis-agx-basis"
    / "public-child-mantissa-ruler-analysis"
    / "result.json"
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(4 * 1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def configure_ruler() -> None:
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
    ruler.DRAW_COUNT = 5_252
    ruler.VARIABLE_FIELD = "variableUlpOffset"
    ruler.EXPECTED_CENSUS = {
        "candidateCount": 8_192,
        "skippedCount": 2_940,
        "discoveryPatternCount": 3_938,
        "holdoutPatternCount": 1_314,
    }
    ruler.EXPECTED = {
        PLAN_ROOT
        / "manifest.json": "e1eb9a50f39fcf1f8381a281f26fc7262307d7b3dfd2d7a37272c41ef8c4a96d",
        ruler.PLAN: "f145155a8b2c50366e954a7c0d85b72ce9bd79c97975ca272731a6cc0154625d",
        ruler.VERTICES: "048c83418a3a348069bf4845c7e7322c3c8b092b779cd135e658ad79ef8ac047",
        ruler.RAW: "37022663f7de88f83e94f2401d207822270547844d921849894d52ebb01a46f9",
        ruler.CAPTURE_MANIFEST: "3fb7898f794501240b8170f72566411ec89cd0a39bd7cfa0d85b9f29e681747c",
        ruler.STDERR: "ab32e8ff34bd1461418b6b64978c854e4d3f750c8077ba957a7f2f0256f70360",
        ruler.STDOUT: "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        ruler.INTERPOSER: "9ead667be857c2fa3ed8a9b110d6d33edb24cf6d7ddf575427d17740e0ff1e8f",
        ruler.EXECUTABLE: "0eba15db7f872a845398f91cacce7446f9e92cbd22276da3e42e5b9148be4ea9",
    }


def analyze() -> JsonObject:
    configure_ruler()
    result = ruler.analyze()
    result["schema"] = "walle-reveal-agx-public-child-mantissa-ruler-analysis-v1"
    inputs = result.get("inputs")
    if not isinstance(inputs, dict):
        raise ValueError("public-child ruler inputs are absent")
    inputs["entrypointAnalyzer"] = {
        "path": Path(__file__).relative_to(ROOT).as_posix(),
        "bytes": Path(__file__).stat().st_size,
        "sha256": _sha256(Path(__file__)),
    }
    result["classification"] = (
        "output-blind dense varying sweep on correction-bearing public AGX geometry"
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
