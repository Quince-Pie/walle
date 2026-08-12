#!/usr/bin/env python3
"""Compare portable highlight arithmetic with captured Apple post-glass draws."""

import argparse
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from apple_glass_reference_renderer import (
    CAPTURE_HEIGHT,
    CAPTURE_WIDTH,
    AppleGlassReferenceRenderer,
    bgra_raw,
    compare_images,
)


type JsonObject = dict[str, Any]


@dataclass(frozen=True, slots=True)
class ArithmeticConfiguration:
    name: str
    derivative_mode: int
    vibrant_mode: int
    source_division_mode: int = 0


CONFIGURATIONS = (
    ArithmeticConfiguration("fine-separate", 1, 0),
    ArithmeticConfiguration("fine-final-fma", 1, 2),
    ArithmeticConfiguration("fine-matrix-and-final-fma", 1, 3),
    ArithmeticConfiguration("fine-native-final-fma", 1, 6),
    ArithmeticConfiguration("fine-final-fma-reciprocal-source", 1, 2, 1),
    ArithmeticConfiguration("fine-final-fma-rtz-source", 1, 2, 2),
    ArithmeticConfiguration("coarse-separate", 0, 0),
    ArithmeticConfiguration("coarse-final-fma", 0, 2),
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def load_sweep(capture: Path) -> JsonObject:
    runtime_path = capture / "runtime.json"
    runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
    local = runtime.get("carendererLocalBackdropEvidence")
    if not isinstance(local, dict):
        raise ValueError("local-backdrop CARenderer evidence is absent")
    render = local.get("render")
    if not isinstance(render, dict):
        raise ValueError("local-backdrop render evidence is absent")
    replay = render.get("exactPassReplay")
    if not isinstance(replay, dict):
        raise ValueError("exact pass replay evidence is absent")
    sweep = replay.get("postGlassDestinationSweep")
    if not isinstance(sweep, dict):
        raise ValueError("post-glass destination sweep is absent")
    if (
        sweep.get("schemaVersion") != 1
        or sweep.get("capturedApplePipelinesUnmodified") is not True
        or sweep.get("glassPrefixSkipped") is not True
        or sweep.get("destinationAlpha") != 255
    ):
        raise ValueError("post-glass destination contract differs")
    cases = sweep.get("cases")
    if not isinstance(cases, list) or len(cases) != 8:
        raise ValueError("post-glass destination cases are incomplete")
    return sweep


def raw_path(record: JsonObject, capture: Path, key: str) -> Path:
    value = record.get(key)
    if not isinstance(value, str):
        raise ValueError(f"post-glass record has no {key}")
    path = capture / value
    expected = CAPTURE_WIDTH * CAPTURE_HEIGHT * 4
    if path.stat().st_size != expected:
        raise ValueError(
            f"{path} has {path.stat().st_size} bytes; expected {expected}"
        )
    return path


def evaluate_capture(capture: Path) -> JsonObject:
    sweep = load_sweep(capture)
    configurations: dict[str, JsonObject] = {
        configuration.name: {
            "configuration": {
                "derivativeMode": configuration.derivative_mode,
                "vibrantMode": configuration.vibrant_mode,
                "sourceDivisionMode": configuration.source_division_mode,
            },
            "cases": [],
        }
        for configuration in CONFIGURATIONS
    }
    case_sources: list[JsonObject] = []
    for untyped_case in sweep["cases"]:
        if not isinstance(untyped_case, dict):
            raise ValueError("post-glass case record is malformed")
        case: JsonObject = untyped_case
        if case.get("executed") is not True:
            raise ValueError(f"post-glass case failed: {case}")
        name = case.get("name")
        output = case.get("output")
        if not isinstance(name, str) or not isinstance(output, dict):
            raise ValueError("post-glass case identity is malformed")
        input_path = raw_path(case, capture, "inputFile")
        output_path = raw_path(output, capture, "rawFile")
        reference = bgra_raw(
            output_path,
            width=CAPTURE_WIDTH,
            height=CAPTURE_HEIGHT,
        )
        with AppleGlassReferenceRenderer(
            capture,
            destination_bgra_path=input_path,
            half_intrinsic_table=capture / "half-intrinsics.bin",
            load_interpolant_trace=False,
            load_interpolant_axis_trace=False,
            load_diagnostic_traces=False,
        ) as renderer:
            for configuration in CONFIGURATIONS:
                renderer.program["HighlightDerivativeMode"].value = (
                    configuration.derivative_mode
                )
                renderer.program["HighlightVibrantArithmeticMode"].value = (
                    configuration.vibrant_mode
                )
                renderer.program["HighlightSourceDivisionMode"].value = (
                    configuration.source_division_mode
                )
                comparison = compare_images(
                    reference,
                    renderer.render_final_highlight(),
                ).as_json()
                configurations[configuration.name]["cases"].append(
                    {
                        "name": name,
                        "comparison": comparison,
                    }
                )
        case_sources.append(
            {
                "name": name,
                "input": {
                    "path": str(input_path),
                    "sha256": sha256_file(input_path),
                },
                "appleOutput": {
                    "path": str(output_path),
                    "sha256": sha256_file(output_path),
                },
            }
        )

    for record in configurations.values():
        comparisons = [case["comparison"] for case in record["cases"]]
        record["gate"] = {
            "exact": all(item["exact"] for item in comparisons),
            "mismatchedBytes": sum(
                int(item["mismatchedBytes"]) for item in comparisons
            ),
            "mismatchedPixels": sum(
                int(item["mismatchedPixels"]) for item in comparisons
            ),
            "maximumChannelDelta": max(
                int(item["maximumChannelDelta"])
                for item in comparisons
            ),
        }
    return {
        "capture": str(capture),
        "runtimeJsonSha256": sha256_file(capture / "runtime.json"),
        "cases": case_sources,
        "configurations": configurations,
    }


def run_gate(captures: list[Path]) -> JsonObject:
    measurements = [evaluate_capture(capture) for capture in captures]
    configuration_totals: dict[str, JsonObject] = {}
    for configuration in CONFIGURATIONS:
        gates = [
            measurement["configurations"][configuration.name]["gate"]
            for measurement in measurements
        ]
        configuration_totals[configuration.name] = {
            "exact": all(gate["exact"] for gate in gates),
            "mismatchedBytes": sum(
                int(gate["mismatchedBytes"]) for gate in gates
            ),
            "mismatchedPixels": sum(
                int(gate["mismatchedPixels"]) for gate in gates
            ),
            "maximumChannelDelta": max(
                int(gate["maximumChannelDelta"]) for gate in gates
            ),
        }
    exact_configurations = [
        name
        for name, total in configuration_totals.items()
        if total["exact"]
    ]
    return {
        "liquidGlassPostGlassGateSchemaVersion": 1,
        "captures": measurements,
        "configurationTotals": configuration_totals,
        "gate": {
            "exact": bool(exact_configurations),
            "exactConfigurations": exact_configurations,
            "capturedApplePipelinesUnmodified": True,
            "productionShaderAuthorized": False,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("captures", nargs="+", type=Path)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    report = run_gate(arguments.captures)
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
