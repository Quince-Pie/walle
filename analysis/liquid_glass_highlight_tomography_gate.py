#!/usr/bin/env python3
"""Compare controlled Apple highlight stages with the portable GLSL model."""

import argparse
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
)
from liquid_glass_exact_highlight_alpha_gate import (
    highlight_fixture_fingerprint,
    mapping,
    runtime_json,
)
from liquid_glass_post_glass_gate import sha256_file


type JsonObject = dict[str, Any]
type HalfWordImage = NDArray[np.uint16]

EXPECTED_CASES = {
    "positive-normal-x",
    "negative-normal-x",
    "positive-normal-y",
    "negative-normal-y",
    "normalized-normal-x",
    "normalized-normal-y",
    "original-directional",
    "shifted-scaled-distance",
    "leading-coverage",
    "original-coverage",
}


@dataclass(frozen=True, slots=True)
class TomographyCase:
    name: str
    edits: dict[int, bytes]
    reference_path: Path


def final_highlight_trace(runtime: JsonObject) -> JsonObject:
    local = mapping(
        runtime.get("carendererLocalBackdropEvidence"),
        "local-backdrop evidence",
    )
    render = mapping(local.get("render"), "local-backdrop render")
    replay = mapping(render.get("exactPassReplay"), "exact pass replay")
    return mapping(
        replay.get("finalHighlightAlphaTrace"),
        "final highlight alpha trace",
    )


def tomography_cases(
    capture: Path,
    runtime: JsonObject,
) -> list[TomographyCase]:
    trace = final_highlight_trace(runtime)
    if (
        trace.get("executed") is not True
        or trace.get("capturedAppleFunctionUnmodified") is not True
        or trace.get("selectedLastA2XghfcDraw") is not True
    ):
        raise ValueError("final highlight trace contract differs")
    tomography = mapping(trace.get("stageTomography"), "stage tomography")
    records = tomography.get("cases")
    if (
        tomography.get("schemaVersion") != 1
        or tomography.get("executed") is not True
        or tomography.get("capturedAppleFunctionUnmodified") is not True
        or not isinstance(records, list)
        or tomography.get("caseCount") != len(records)
    ):
        raise ValueError("stage tomography contract differs")

    result: list[TomographyCase] = []
    names: set[str] = set()
    for untyped_record in records:
        record = mapping(untyped_record, "tomography case")
        name = record.get("name")
        edits = record.get("edits")
        replay = mapping(record.get("replay"), "tomography replay")
        output = mapping(replay.get("output"), "tomography output")
        filename = output.get("rawFile")
        if (
            not isinstance(name, str)
            or name in names
            or not isinstance(edits, list)
            or record.get("executed") is not True
            or replay.get("executed") is not True
            or output.get("pixelFormat") != 115
            or not isinstance(filename, str)
        ):
            raise ValueError(f"malformed tomography case: {record}")

        patches: dict[int, bytes] = {}
        occupied: set[int] = set()
        for untyped_edit in edits:
            edit = mapping(untyped_edit, f"{name} edit")
            offset = edit.get("recordOffset")
            hexadecimal = edit.get("hex")
            if not isinstance(offset, int) or not isinstance(hexadecimal, str):
                raise ValueError(f"malformed {name} edit: {edit}")
            payload = bytes.fromhex(hexadecimal)
            byte_range = set(range(offset, offset + len(payload)))
            if not payload or occupied & byte_range:
                raise ValueError(f"overlapping or empty {name} edit at {offset}")
            occupied |= byte_range
            patches[offset] = payload

        names.add(name)
        result.append(
            TomographyCase(
                name=name,
                edits=patches,
                reference_path=capture / filename,
            )
        )
    if names != EXPECTED_CASES:
        raise ValueError(
            f"tomography cases differ: {sorted(names)} != {sorted(EXPECTED_CASES)}"
        )
    return result


def load_half_rgb(path: Path) -> HalfWordImage:
    words = np.fromfile(path, dtype="<u2")
    expected = CAPTURE_WIDTH * CAPTURE_HEIGHT * 4
    if words.size != expected:
        raise ValueError(f"{path} has {words.size} words; expected {expected}")
    rgba = words.reshape(CAPTURE_HEIGHT, CAPTURE_WIDTH, 4)
    red = rgba[..., 0]
    if not (np.array_equal(red, rgba[..., 1]) and np.array_equal(red, rgba[..., 2])):
        raise ValueError(f"{path} does not contain equal RGB half words")
    return np.ascontiguousarray(red)


def compare_half_words(
    reference: HalfWordImage,
    candidate: HalfWordImage,
) -> JsonObject:
    if reference.shape != candidate.shape:
        raise ValueError(
            f"half-word dimensions differ: {reference.shape} != {candidate.shape}"
        )
    mismatch = reference != candidate
    active_reference = reference != 0
    active_candidate = candidate != 0
    signed_delta = candidate.astype(np.int32) - reference.astype(np.int32)
    nonzero_deltas = signed_delta[mismatch]
    histogram: list[JsonObject] = []
    if nonzero_deltas.size:
        values, counts = np.unique(nonzero_deltas, return_counts=True)
        order = np.argsort(counts, stable=True)[::-1][:12]
        histogram = [
            {
                "candidateMinusAppleHalfCode": int(values[index]),
                "count": int(counts[index]),
            }
            for index in order
        ]
    coordinates = np.argwhere(mismatch)
    mismatch_bounds = (
        None
        if coordinates.size == 0
        else {
            "minimumX": int(coordinates[:, 1].min()),
            "minimumY": int(coordinates[:, 0].min()),
            "maximumX": int(coordinates[:, 1].max()),
            "maximumY": int(coordinates[:, 0].max()),
        }
    )
    reference_float = reference.view("<f2").astype(np.float32)
    candidate_float = candidate.view("<f2").astype(np.float32)
    absolute_float_delta = np.abs(candidate_float - reference_float)
    return {
        "exact": not bool(np.any(mismatch)),
        "wordCount": int(reference.size),
        "mismatchedWords": int(np.count_nonzero(mismatch)),
        "matchingWordFraction": float(1.0 - np.mean(mismatch)),
        "activeApplePixels": int(np.count_nonzero(active_reference)),
        "activeCandidatePixels": int(np.count_nonzero(active_candidate)),
        "activeMaskMismatchedPixels": int(
            np.count_nonzero(active_reference != active_candidate)
        ),
        "maximumHalfCodeDelta": int(np.abs(signed_delta).max(initial=0)),
        "maximumAbsoluteValueDelta": float(absolute_float_delta.max(initial=0.0)),
        "mismatchBounds": mismatch_bounds,
        "mostCommonNonzeroDeltas": histogram,
    }


def run_gate(
    reference_capture: Path,
    tomography_capture: Path,
    intrinsic_table: Path,
) -> JsonObject:
    reference_runtime = runtime_json(reference_capture)
    tomography_runtime = runtime_json(tomography_capture)
    reference_fingerprint = highlight_fixture_fingerprint(reference_runtime)
    tomography_fingerprint = highlight_fixture_fingerprint(tomography_runtime)
    if reference_fingerprint != tomography_fingerprint:
        raise ValueError(
            "reference and tomography captures use different highlight fixtures"
        )
    cases = tomography_cases(tomography_capture, tomography_runtime)
    half_intrinsic_table = reference_capture / "half-intrinsics.bin"
    if not half_intrinsic_table.is_file():
        raise ValueError(f"half-intrinsic evidence is missing: {half_intrinsic_table}")

    comparisons: JsonObject = {}
    with AppleGlassReferenceRenderer(
        reference_capture,
        intrinsic_table=intrinsic_table,
        half_intrinsic_table=half_intrinsic_table,
        load_interpolant_trace=False,
        load_interpolant_axis_trace=False,
        load_diagnostic_traces=False,
    ) as renderer:
        renderer.program["UseAppleIntrinsicTable"].value = 1
        renderer.program["UseAppleHalfIntrinsicTable"].value = 1
        renderer.program["HighlightCoordinateMode"].value = 0
        renderer.program["HighlightDerivativeMode"].value = 1
        renderer.program["HighlightCoverageArithmeticMode"].value = 1
        renderer.program["HighlightNormalizeMode"].value = 1
        for case in cases:
            reference = load_half_rgb(case.reference_path)
            candidate_rgba = renderer.render_final_highlight_half(
                uniform_edits=case.edits
            )
            candidate = np.ascontiguousarray(candidate_rgba[..., 0])
            if not (
                np.array_equal(candidate, candidate_rgba[..., 1])
                and np.array_equal(candidate, candidate_rgba[..., 2])
            ):
                raise ValueError(f"candidate {case.name} RGB half words differ")
            comparisons[case.name] = {
                "applePath": str(case.reference_path),
                "appleSHA256": sha256_file(case.reference_path),
                "uniformEdits": {
                    f"0x{offset:02x}": payload.hex()
                    for offset, payload in sorted(case.edits.items())
                },
                "comparison": compare_half_words(reference, candidate),
            }

        implementation = renderer.implementation

    stage_exact = {
        name: bool(mapping(record, name)["comparison"]["exact"])
        for name, record in comparisons.items()
    }
    return {
        "liquidGlassHighlightTomographyGateSchemaVersion": 1,
        "referenceCapture": str(reference_capture),
        "tomographyCapture": str(tomography_capture),
        "intrinsicTable": {
            "path": str(intrinsic_table),
            "sha256": sha256_file(intrinsic_table),
        },
        "halfIntrinsicTable": {
            "path": str(half_intrinsic_table),
            "sha256": sha256_file(half_intrinsic_table),
        },
        "highlightFixtureFingerprint": reference_fingerprint,
        "candidateConfiguration": {
            "useAppleFloatIntrinsicTable": True,
            "useAppleHalfIntrinsicTable": True,
            "highlightCoordinateMode": 0,
            "highlightDerivativeMode": 1,
            "highlightCoverageArithmeticMode": 1,
            "highlightNormalizeMode": 1,
        },
        "implementation": implementation,
        "cases": comparisons,
        "stageExact": stage_exact,
        "gate": {
            "allTomographyStagesExact": all(stage_exact.values()),
            "productionShaderAuthorized": False,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reference_capture", type=Path)
    parser.add_argument("tomography_capture", type=Path)
    parser.add_argument("--intrinsic-table", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    report = run_gate(
        arguments.reference_capture,
        arguments.tomography_capture,
        arguments.intrinsic_table,
    )
    encoded = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if arguments.output is None:
        print(encoded, end="")
    else:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(encoded, encoding="utf-8")
        print(arguments.output)
    return 0 if report["gate"]["allTomographyStagesExact"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
