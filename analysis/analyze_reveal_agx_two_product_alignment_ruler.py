#!/usr/bin/env python3.14
"""Analyze the exponent-alignment M1 AGX two-product ruler capture."""

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
    ROOT / "build" / "analysis-agx-basis" / "two-product-alignment-ruler-plan-v1"
)
CAPTURE_ROOT: Final = (
    ROOT / "build" / "analysis-agx-basis" / "macos-two-product-alignment-ruler-v1"
)
OUTPUT: Final = (
    ROOT
    / "build"
    / "analysis-agx-basis"
    / "two-product-alignment-ruler-analysis"
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
    ruler.DRAW_COUNT = 8_192
    ruler.EXPECTED_CENSUS = {
        "candidateCount": 8_192,
        "skippedCount": 0,
        "discoveryPatternCount": 6_116,
        "holdoutPatternCount": 2_076,
    }
    ruler.EXPECTED = {
        PLAN_ROOT
        / "manifest.json": "b7616df3cbd4dfa41a1c26811d26653e93bec068d6e7f827fc62de646aaaba8a",
        ruler.PLAN: "dde184d24dac1b7da8ecaad3f051667dfa79971ea33d3e74c6f3d29d67c4b729",
        ruler.VERTICES: "947e20c50d1c8a2bb0ccc0143d47a6a2bb1bfb736ff6bb2b9dacc6e5ad342710",
        ruler.RAW: "5337464c57ec821a4dc9b6a90b15da46d48cf9c76cf386d2a0dc0e3987eb5f01",
        ruler.CAPTURE_MANIFEST: "6f0bd9c209edd74d8eb9be7e9d74989161712553de5ff01a44e84b488537dc32",
        ruler.STDERR: "6c513bf25c946edbff1282828a60113719cf868c604b94709985fb57b8850636",
        ruler.STDOUT: "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        ruler.INTERPOSER: "9ead667be857c2fa3ed8a9b110d6d33edb24cf6d7ddf575427d17740e0ff1e8f",
        ruler.EXECUTABLE: "0eba15db7f872a845398f91cacce7446f9e92cbd22276da3e42e5b9148be4ea9",
    }
    result = ruler.analyze()
    result["schema"] = "walle-reveal-agx-two-product-alignment-ruler-analysis-v1"
    inputs = result.get("inputs")
    if not isinstance(inputs, dict):
        raise ValueError("alignment ruler inputs are absent")
    inputs["entrypointAnalyzer"] = {
        "path": Path(__file__).relative_to(ROOT).as_posix(),
        "bytes": Path(__file__).stat().st_size,
        "sha256": _sha256(Path(__file__)),
    }
    result["classification"] = (
        "output-blind M1 two-product normalization-boundary alignment ruler"
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
