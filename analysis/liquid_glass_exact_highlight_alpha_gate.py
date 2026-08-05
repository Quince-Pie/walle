#!/usr/bin/env python3
"""Gate the portable compositor with Apple's directly captured half alpha."""

import argparse
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from apple_glass_reference_renderer import (
    CAPTURE_HEIGHT,
    CAPTURE_WIDTH,
    AppleGlassReferenceRenderer,
    bgra_raw,
    compare_images,
)
from liquid_glass_post_glass_gate import load_sweep, raw_path, sha256_file


type JsonObject = dict[str, Any]
type UInt32Image = NDArray[np.uint32]

EPSILON_HALF_BITS = 0x068E


@dataclass(frozen=True, slots=True)
class AlphaOracle:
    packed_trace: UInt32Image
    path: Path
    sha256: str
    active_pixels: int
    unique_words: int
    minimum_word: int
    maximum_word: int

    def as_json(self) -> JsonObject:
        return {
            "path": str(self.path),
            "sha256": self.sha256,
            "activePixels": self.active_pixels,
            "uniqueHalfWords": self.unique_words,
            "minimumHalfWord": f"0x{self.minimum_word:04x}",
            "maximumHalfWord": f"0x{self.maximum_word:04x}",
        }


def mapping(value: object, context: str) -> JsonObject:
    if not isinstance(value, dict):
        raise ValueError(f"{context} is not an object")
    return value


def runtime_json(capture: Path) -> JsonObject:
    return mapping(
        json.loads((capture / "runtime.json").read_text(encoding="utf-8")),
        "runtime root",
    )


def exact_pass_replay(runtime: JsonObject) -> JsonObject:
    local = mapping(
        runtime.get("carendererLocalBackdropEvidence"),
        "local-backdrop evidence",
    )
    render = mapping(local.get("render"), "local-backdrop render")
    return mapping(render.get("exactPassReplay"), "exact pass replay")


def load_alpha_oracle(capture: Path) -> AlphaOracle:
    trace = mapping(
        exact_pass_replay(runtime_json(capture)).get("finalHighlightAlphaTrace"),
        "final highlight alpha trace",
    )
    if (
        trace.get("schemaVersion") != 1
        or trace.get("executed") is not True
        or trace.get("capturedAppleFunctionUnmodified") is not True
        or trace.get("selectedLastA2XghfcDraw") is not True
    ):
        raise ValueError("final highlight alpha trace contract differs")
    comparison = mapping(
        trace.get("capturedVsRebuiltBGRA8"),
        "same-format rebuild comparison",
    )
    if (
        comparison.get("compared") is not True
        or comparison.get("mismatchedByteCount") != 0
        or comparison.get("maximumChannelDelta") != 0
    ):
        raise ValueError("same-format Apple pipeline rebuild is not exact")
    exact_half = mapping(trace.get("exactHalfAlpha"), "exact half replay")
    if exact_half.get("executed") is not True:
        raise ValueError("exact half replay failed")
    output = mapping(exact_half.get("output"), "exact half output")
    if output.get("pixelFormat") != 115:
        raise ValueError("exact half output is not RGBA16Float")
    filename = output.get("rawFile")
    if not isinstance(filename, str):
        raise ValueError("exact half output has no raw file")
    path = capture / filename
    words = np.fromfile(path, dtype="<u2")
    expected_words = CAPTURE_WIDTH * CAPTURE_HEIGHT * 4
    if words.size != expected_words:
        raise ValueError(
            f"{path} has {words.size} half words; expected {expected_words}"
        )
    rgba = words.reshape(CAPTURE_HEIGHT, CAPTURE_WIDTH, 4)
    alpha_words = rgba[..., 0]
    if not (
        np.array_equal(alpha_words, rgba[..., 1])
        and np.array_equal(alpha_words, rgba[..., 2])
    ):
        raise ValueError("exact half output RGB channels differ")
    active = alpha_words != 0
    if np.any(alpha_words[active] < EPSILON_HALF_BITS):
        raise ValueError("exact half output contains sub-threshold pixels")
    if np.any(alpha_words[active] > 0x3C00):
        raise ValueError("exact half output exceeds one")
    active_words = alpha_words[active]
    if active_words.size == 0:
        raise ValueError("exact half output is empty")

    packed = np.zeros(
        (CAPTURE_HEIGHT, CAPTURE_WIDTH, 4),
        dtype=np.uint32,
    )
    packed[..., 1] = alpha_words.astype(np.uint32)
    return AlphaOracle(
        packed_trace=packed,
        path=path,
        sha256=sha256_file(path),
        active_pixels=int(active_words.size),
        unique_words=int(np.unique(active_words).size),
        minimum_word=int(active_words.min()),
        maximum_word=int(active_words.max()),
    )


def latest_highlight_payload(
    runtime: JsonObject,
    *,
    stage: str,
    index: int,
    byte_count: int,
) -> bytes:
    local = mapping(
        runtime.get("carendererLocalBackdropEvidence"),
        "local-backdrop evidence",
    )
    render = mapping(local.get("render"), "local-backdrop render")
    snapshots = mapping(
        render.get("metalBufferSnapshots"),
        "metal buffer snapshots",
    ).get("snapshots")
    if not isinstance(snapshots, list):
        raise ValueError("metal buffer snapshot list is absent")
    candidates = [
        snapshot
        for snapshot in snapshots
        if isinstance(snapshot, dict)
        and snapshot.get("stage") == stage
        and snapshot.get("index") == index
        and "_A2Xghfc" in str(snapshot.get("pipeline", {}))
    ]
    if not candidates:
        raise ValueError(f"final highlight {stage} buffer {index} is absent")
    latest = max(candidates, key=lambda item: int(item["sequence"]))
    payload = mapping(latest.get("payload"), "buffer payload").get("hex")
    if not isinstance(payload, str):
        raise ValueError("final highlight buffer payload is absent")
    decoded = bytes.fromhex(payload)
    if len(decoded) < byte_count:
        raise ValueError("final highlight buffer payload is truncated")
    return decoded[:byte_count]


def highlight_fixture_fingerprint(runtime: JsonObject) -> JsonObject:
    payloads = {
        "uniform": latest_highlight_payload(
            runtime,
            stage="fragment",
            index=1,
            byte_count=0xF8,
        ),
        "vertices": latest_highlight_payload(
            runtime,
            stage="vertex",
            index=1,
            byte_count=4 * 48,
        ),
        "indices": latest_highlight_payload(
            runtime,
            stage="index",
            index=-1,
            byte_count=6 * 2,
        ),
    }
    return {
        name: hashlib.sha256(payload).hexdigest() for name, payload in payloads.items()
    }


def aggregate(comparisons: list[JsonObject]) -> JsonObject:
    return {
        "exact": all(bool(item["exact"]) for item in comparisons),
        "mismatchedBytes": sum(int(item["mismatchedBytes"]) for item in comparisons),
        "mismatchedPixels": sum(int(item["mismatchedPixels"]) for item in comparisons),
        "maximumChannelDelta": max(
            int(item["maximumChannelDelta"]) for item in comparisons
        ),
    }


def run_gate(reference_capture: Path, alpha_capture: Path) -> JsonObject:
    reference_runtime = runtime_json(reference_capture)
    alpha_runtime = runtime_json(alpha_capture)
    reference_fingerprint = highlight_fixture_fingerprint(reference_runtime)
    alpha_fingerprint = highlight_fixture_fingerprint(alpha_runtime)
    if reference_fingerprint != alpha_fingerprint:
        raise ValueError(
            "reference and alpha captures use different highlight fixtures"
        )

    oracle = load_alpha_oracle(alpha_capture)
    sweep = load_sweep(reference_capture)
    modes: dict[str, list[JsonObject]] = {
        "half-division": [],
        "reciprocal-multiply": [],
        "round-toward-zero-division": [],
    }
    source_division_modes = {
        "half-division": 0,
        "reciprocal-multiply": 1,
        "round-toward-zero-division": 2,
    }
    case_sources: list[JsonObject] = []
    for untyped_case in sweep["cases"]:
        case = mapping(untyped_case, "post-glass case")
        name = case.get("name")
        output = mapping(case.get("output"), "post-glass output")
        if not isinstance(name, str):
            raise ValueError("post-glass case has no name")
        input_path = raw_path(case, reference_capture, "inputFile")
        output_path = raw_path(output, reference_capture, "rawFile")
        reference = bgra_raw(
            output_path,
            width=CAPTURE_WIDTH,
            height=CAPTURE_HEIGHT,
        )
        with AppleGlassReferenceRenderer(
            reference_capture,
            destination_bgra_path=input_path,
            load_interpolant_trace=False,
            load_interpolant_axis_trace=False,
            load_diagnostic_traces=False,
            highlight_half_stage_data=oracle.packed_trace,
        ) as renderer:
            renderer.program["UseAppleHighlightAlphaTrace"].value = 1
            renderer.program["HighlightVibrantArithmeticMode"].value = 9
            for mode_name, mode in source_division_modes.items():
                renderer.program["HighlightSourceDivisionMode"].value = mode
                comparison = compare_images(
                    reference,
                    renderer.render_final_highlight(),
                ).as_json()
                modes[mode_name].append(
                    {
                        "name": name,
                        "comparison": comparison,
                    }
                )
        case_sources.append(
            {
                "name": name,
                "inputSHA256": sha256_file(input_path),
                "appleOutputSHA256": sha256_file(output_path),
            }
        )

    mode_reports = {
        name: {
            "sourceDivisionMode": source_division_modes[name],
            "vibrantArithmeticMode": 9,
            "cases": cases,
            "gate": aggregate(
                [mapping(case["comparison"], "comparison") for case in cases]
            ),
        }
        for name, cases in modes.items()
    }
    exact_modes = [
        name
        for name, report in mode_reports.items()
        if mapping(report["gate"], "gate")["exact"]
    ]
    return {
        "liquidGlassExactHighlightAlphaGateSchemaVersion": 1,
        "referenceCapture": str(reference_capture),
        "alphaCapture": str(alpha_capture),
        "referenceRuntimeSHA256": sha256_file(reference_capture / "runtime.json"),
        "alphaRuntimeSHA256": sha256_file(alpha_capture / "runtime.json"),
        "highlightFixtureFingerprint": reference_fingerprint,
        "alphaOracle": oracle.as_json(),
        "cases": case_sources,
        "modes": mode_reports,
        "gate": {
            "exact": bool(exact_modes),
            "exactModes": exact_modes,
            "productionShaderAuthorized": False,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reference_capture", type=Path)
    parser.add_argument("alpha_capture", type=Path)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    report = run_gate(
        arguments.reference_capture,
        arguments.alpha_capture,
    )
    encoded = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if arguments.output is None:
        print(encoded, end="")
    else:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(encoded, encoding="utf-8")
        print(arguments.output)
    return 0 if report["gate"]["exact"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
