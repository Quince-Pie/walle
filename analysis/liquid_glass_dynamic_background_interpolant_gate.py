#!/usr/bin/env python3
"""Prospectively bit-gate all dynamic background-main interpolants."""

import argparse
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from liquid_glass_dynamic_capture import (
    EXPECTED_SAMPLE_INDICES,
    _background_geometry,
    _background_mvp,
    _report_paths,
)
from liquid_glass_dynamic_render_gate import _draw_scissors
from liquid_glass_post_glass_gate import sha256_file
from liquid_glass_profile_matrix import GLASS_FRAGMENTS
from liquid_glass_runtime_raster_coefficients import (
    RuntimeQuad,
    coordinate_axis_bits,
    primitive_ids,
    runtime_quad_from_vertices,
    slopes_bits,
    visible_pixel_bounds,
)
from liquid_glass_square_selector_calibration import (
    SELECTOR_COUNT,
    WIDTH_FIXED_LOWER,
    SquareSelectorCalibration,
)

import raster_tile_selector_model as arithmetic


type JsonObject = dict[str, Any]
type UIntImage = NDArray[np.uint32]

CAPTURE_SIZE = 1024
CHANNEL_COUNT = 4
EXPECTED_COMPARED_WORDS = (
    len(EXPECTED_SAMPLE_INDICES) * CAPTURE_SIZE * CAPTURE_SIZE * CHANNEL_COUNT
)
FROZEN_FILES = {
    "dynamicRenderGateSha256": Path(__file__).with_name(
        "liquid_glass_dynamic_render_gate.py"
    ),
    "runtimeRasterCoefficientsSha256": Path(__file__).with_name(
        "liquid_glass_runtime_raster_coefficients.py"
    ),
    "squareSelectorCalibrationSha256": Path(__file__).with_name(
        "liquid_glass_square_selector_calibration.py"
    ),
}


def mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} is not an object")
    return value


def _verify_preregistration(
    path: Path,
    *,
    square_selector_archive: Path,
    enforce_frozen_predictor: bool = True,
) -> tuple[JsonObject, JsonObject]:
    registration = mapping(
        json.loads(path.read_text(encoding="utf-8")),
        "background interpolant preregistration",
    )
    capture = mapping(registration.get("capture"), "preregistered capture")
    acceptance = mapping(
        registration.get("acceptance"),
        "preregistered acceptance",
    )
    frozen = mapping(
        registration.get("frozenPredictor"),
        "preregistered frozen predictor",
    )
    if (
        registration.get("backgroundInterpolantTransferPreregistrationSchemaVersion")
        != 1
        or registration.get("classification")
        != "prospective dynamic transfer gate"
        or capture.get("material") != "clear"
        or capture.get("appearance") != "light"
        or capture.get("direction") != "materialize"
        or capture.get("prospectiveReplay") != "main-only"
        or capture.get("diagnosticReplay") != "main-plus-shadow"
        or capture.get("pixelFormat") != "rgba32Uint"
        or capture.get("sampleIndices") != list(EXPECTED_SAMPLE_INDICES)
        or capture.get("traceComponents")
        != ["sdf-x", "sdf-y", "source-x", "source-y"]
        or acceptance.get("comparedWords") != EXPECTED_COMPARED_WORDS
        or acceptance.get("maximumAllowedMismatchedWords") != 0
        or acceptance.get("requireEveryStateExact") is not True
    ):
        raise ValueError("background interpolant preregistration differs")

    verified_files: JsonObject = {}
    for key, frozen_path in FROZEN_FILES.items():
        expected = frozen.get(key)
        observed = sha256_file(frozen_path)
        exact = expected == observed
        if not exact and enforce_frozen_predictor:
            raise ValueError(
                f"frozen predictor hash differs for {frozen_path}: "
                f"{observed} != {expected}"
            )
        verified_files[key] = {
            "path": str(frozen_path),
            "sha256": observed,
            "expectedSha256": expected,
            "observedSha256": observed,
            "exact": exact,
        }
    expected_archive_hash = frozen.get("squareSelectorArchiveSha256")
    observed_archive_hash = sha256_file(square_selector_archive)
    archive_exact = expected_archive_hash == observed_archive_hash
    if not archive_exact and enforce_frozen_predictor:
        raise ValueError(
            "frozen square-selector archive hash differs: "
            f"{observed_archive_hash} != {expected_archive_hash}"
        )
    verified_files["squareSelectorArchiveSha256"] = {
        "path": str(square_selector_archive),
        "sha256": observed_archive_hash,
        "expectedSha256": expected_archive_hash,
        "observedSha256": observed_archive_hash,
        "exact": archive_exact,
    }
    return dict(registration), verified_files


def _verify_followup_preregistration(
    path: Path,
    *,
    prior_preregistration: Path,
    verified_files: Mapping[str, Any],
    run_id: int,
) -> JsonObject:
    registration = mapping(
        json.loads(path.read_text(encoding="utf-8")),
        "background interpolant follow-up preregistration",
    )
    capture = mapping(registration.get("capture"), "follow-up capture")
    acceptance = mapping(
        registration.get("acceptance"),
        "follow-up acceptance",
    )
    frozen = mapping(
        registration.get("frozenPredictor"),
        "follow-up frozen predictor",
    )
    if (
        registration.get(
            "backgroundInterpolantTransferFollowupPreregistrationSchemaVersion"
        )
        != 1
        or registration.get("classification")
        != "prospective follow-up after a retained harness failure"
        or capture.get("material") != "clear"
        or capture.get("appearance") != "light"
        or capture.get("direction") != "materialize"
        or capture.get("prospectiveReplay") != "main-only"
        or capture.get("diagnosticReplay") != "main-plus-shadow"
        or capture.get("pixelFormat") != "rgba32Uint"
        or capture.get("sampleIndices") != list(EXPECTED_SAMPLE_INDICES)
        or capture.get("traceComponents")
        != ["sdf-x", "sdf-y", "source-x", "source-y"]
        or acceptance.get("comparedWords") != EXPECTED_COMPARED_WORDS
        or acceptance.get("maximumAllowedMismatchedWords") != 0
        or acceptance.get("requireEveryStateExact") is not True
        or not isinstance(acceptance.get("requireRunIdDifferentFrom"), int)
    ):
        raise ValueError("background interpolant follow-up preregistration differs")
    forbidden_run_id = int(acceptance["requireRunIdDifferentFrom"])
    if run_id == forbidden_run_id:
        raise ValueError(
            f"run {run_id} is the opened calibration; a new holdout is required"
        )

    observed_wrapper_hash = sha256_file(Path(__file__))
    expected_wrapper_hash = frozen.get("prospectiveGateWrapperSha256")
    if observed_wrapper_hash != expected_wrapper_hash:
        raise ValueError(
            "prospective gate-wrapper hash differs: "
            f"{observed_wrapper_hash} != {expected_wrapper_hash}"
        )
    observed_prior_hash = sha256_file(prior_preregistration)
    if frozen.get("priorPreregistrationSha256") != observed_prior_hash:
        raise ValueError("prior preregistration hash differs")
    for key in FROZEN_FILES:
        observed = mapping(verified_files.get(key), key).get("sha256")
        if frozen.get(key) != observed:
            raise ValueError(f"follow-up frozen predictor hash differs for {key}")
    selector = mapping(
        verified_files.get("squareSelectorArchiveSha256"),
        "square selector archive",
    ).get("sha256")
    if frozen.get("squareSelectorArchiveSha256") != selector:
        raise ValueError("follow-up square selector archive hash differs")
    return dict(registration)


def _trace_outputs(
    root: Path,
    render: Mapping[str, Any],
    *,
    sample_index: int,
) -> tuple[Path, Path]:
    exact_replay = mapping(render.get("exactPassReplay"), "exact pass replay")
    trace = mapping(
        exact_replay.get("backgroundInterpolantTrace"),
        "background interpolant trace",
    )
    main = mapping(trace.get("mainReplay"), "background main replay")
    combined = mapping(
        trace.get("combinedReplay"),
        "background combined replay",
    )
    main_output = mapping(main.get("output"), "background main output")
    combined_output = mapping(
        combined.get("output"),
        "background combined output",
    )
    if (
        trace.get("schemaVersion") != 1
        or trace.get("executed") is not True
        or trace.get("scope") != "all-dynamic-background-states"
        or trace.get("capturedAppleFunctionUnmodified") is not False
        or trace.get("customStageInVertex") is not True
        or trace.get("prospectiveReplay") != "main-only"
        or trace.get("diagnosticReplay") != "main-plus-shadow"
        or main.get("executed") is not True
        or main.get("glassDrawCount") != 1
        or combined.get("executed") is not True
        or combined.get("glassDrawCount") != 2
    ):
        raise ValueError(f"sample {sample_index} background trace differs")

    paths: list[Path] = []
    expected_bytes = CAPTURE_SIZE * CAPTURE_SIZE * CHANNEL_COUNT * 4
    for name, output in (("main", main_output), ("combined", combined_output)):
        filename = output.get("rawFile")
        if (
            output.get("rawCapture") is not True
            or output.get("pixelFormat") != 123
            or output.get("width") != CAPTURE_SIZE
            or output.get("height") != CAPTURE_SIZE
            or output.get("rawBytes") != expected_bytes
            or not isinstance(filename, str)
        ):
            raise ValueError(
                f"sample {sample_index} background {name} output differs"
            )
        output_path = root / filename
        if not output_path.is_file() or output_path.stat().st_size != expected_bytes:
            raise ValueError(
                f"sample {sample_index} background {name} raw file differs: "
                f"{output_path}"
            )
        paths.append(output_path)
    return paths[0], paths[1]


def _load_uint_image(path: Path) -> UIntImage:
    values = np.fromfile(path, dtype="<u4")
    expected_words = CAPTURE_SIZE * CAPTURE_SIZE * CHANNEL_COUNT
    if values.size != expected_words:
        raise ValueError(f"{path} has {values.size} words; expected {expected_words}")
    return values.reshape(CAPTURE_SIZE, CAPTURE_SIZE, CHANNEL_COUNT)


def _predicted_region(
    quad: RuntimeQuad,
    *,
    selector_table: Sequence[int],
) -> tuple[tuple[int, int, int, int], UIntImage, UIntImage]:
    raster_left, raster_bottom, raster_right, raster_top = visible_pixel_bounds(
        quad.case
    )
    left = max(0, raster_left)
    bottom = max(0, raster_bottom)
    right = min(CAPTURE_SIZE, raster_right)
    top = min(CAPTURE_SIZE, raster_top)
    if left >= right or bottom >= top:
        raise ValueError(f"{quad.case.name} does not intersect the target")
    axis_predictions = {
        (channel, primitive): coordinate_axis_bits(
            quad,
            channel=channel,
            primitive=primitive,
            coordinates=(
                range(left, right)
                if quad.channelAxes[channel] == 0
                else range(bottom, top)
            ),
            selector_table=selector_table,
        )
        for channel in range(CHANNEL_COUNT)
        for primitive in (0, 1)
    }
    yy, xx = np.indices((top - bottom, right - left), dtype=np.uint32)
    xx += np.uint32(left)
    yy += np.uint32(bottom)
    primitives = primitive_ids(quad, xx, yy)
    candidate = np.empty(
        (top - bottom, right - left, CHANNEL_COUNT),
        dtype=np.uint32,
    )
    for channel, axis in enumerate(quad.channelAxes):
        indices = xx - np.uint32(left) if axis == 0 else yy - np.uint32(bottom)
        for primitive in (0, 1):
            selected = primitives == primitive
            candidate[..., channel][selected] = axis_predictions[
                channel,
                primitive,
            ][indices[selected]]
    return (left, bottom, right, top), candidate, primitives


def _comparison(reference: UIntImage, candidate: UIntImage) -> JsonObject:
    if reference.shape != candidate.shape:
        raise ValueError(f"interpolant shapes differ: {reference.shape}")
    changed = candidate != reference
    changed_pixels = np.any(changed, axis=2)
    coordinates = np.argwhere(changed_pixels)
    examples = [
        {
            "x": int(x),
            "y": int(y),
            "predictedBits": [
                f"0x{int(value):08x}" for value in candidate[y, x]
            ],
            "appleBits": [
                f"0x{int(value):08x}" for value in reference[y, x]
            ],
            "predictedValues": [
                float(value) for value in candidate[y, x].view("<f4")
            ],
            "appleValues": [
                float(value) for value in reference[y, x].view("<f4")
            ],
        }
        for y, x in coordinates[:32]
    ]
    return {
        "exact": not bool(np.any(changed)),
        "comparedWords": int(changed.size),
        "mismatchedWords": int(np.count_nonzero(changed)),
        "comparedPixels": int(changed_pixels.size),
        "mismatchedPixels": int(np.count_nonzero(changed_pixels)),
        "mismatchedWordsByChannel": [
            int(np.count_nonzero(changed[..., channel]))
            for channel in range(CHANNEL_COUNT)
        ],
        "examples": examples,
    }


def _coverage(values: UIntImage) -> JsonObject:
    active = np.any(values != 0, axis=2)
    coordinates = np.argwhere(active)
    if not coordinates.size:
        return {"activePixels": 0, "bounds": None}
    return {
        "activePixels": int(np.count_nonzero(active)),
        "bounds": {
            "minimumX": int(coordinates[:, 1].min()),
            "minimumY": int(coordinates[:, 0].min()),
            "maximumX": int(coordinates[:, 1].max()),
            "maximumY": int(coordinates[:, 0].max()),
        },
    }


def _combined_diagnostic(main: UIntImage, combined: UIntImage) -> JsonObject:
    changed = main != combined
    changed_pixels = np.any(changed, axis=2)
    coordinates = np.argwhere(changed_pixels)
    bounds = None
    if coordinates.size:
        bounds = {
            "minimumX": int(coordinates[:, 1].min()),
            "minimumY": int(coordinates[:, 0].min()),
            "maximumX": int(coordinates[:, 1].max()),
            "maximumY": int(coordinates[:, 0].max()),
        }
    return {
        "classification": "diagnostic-only; not prospective acceptance",
        "differingWordsFromMainReplay": int(np.count_nonzero(changed)),
        "differingPixelsFromMainReplay": int(np.count_nonzero(changed_pixels)),
        "differingPixelBounds": bounds,
        "coverage": _coverage(combined),
    }


def run_gate(
    dynamic_root: Path,
    *,
    square_selector_archive: Path,
    preregistration: Path,
    evidence_classification: str,
    followup_preregistration: Path | None,
    run_id: int | None,
) -> JsonObject:
    prospective_evidence = evidence_classification == "prospective-followup"
    registration, verified_files = _verify_preregistration(
        preregistration,
        square_selector_archive=square_selector_archive,
        enforce_frozen_predictor=prospective_evidence,
    )
    followup_registration = None
    if evidence_classification == "prospective-followup":
        if followup_preregistration is None or run_id is None:
            raise ValueError(
                "prospective follow-up requires its preregistration and run ID"
            )
        followup_registration = _verify_followup_preregistration(
            followup_preregistration,
            prior_preregistration=preregistration,
            verified_files=verified_files,
            run_id=run_id,
        )
    reports = _report_paths(dynamic_root)
    if len(reports) != 1:
        raise ValueError(f"expected one dynamic report under {dynamic_root}")
    report_path = reports[0]
    root = report_path.parent
    report = mapping(
        json.loads(report_path.read_text(encoding="utf-8")),
        "transition report",
    )
    if (
        report.get("material") != "clear"
        or report.get("appearance") != "light"
        or report.get("direction") != "materialize"
    ):
        raise ValueError("dynamic capture profile differs from preregistration")
    fragment = GLASS_FRAGMENTS["clear"]
    uniforms = mapping(report.get("dynamicBackgroundUniforms"), "dynamic uniforms")
    untyped_records = uniforms.get("records")
    if not isinstance(untyped_records, list):
        raise ValueError("dynamic records are absent")
    records = [mapping(record, "dynamic record") for record in untyped_records]
    selected = {
        int(record["sampleIndex"]): record
        for record in records
        if record.get("sampleIndex") in EXPECTED_SAMPLE_INDICES
    }
    if tuple(sorted(selected)) != EXPECTED_SAMPLE_INDICES:
        raise ValueError(f"background interpolant samples differ: {sorted(selected)}")

    selector_table = arithmetic.load_selector_table()
    calibration = SquareSelectorCalibration.load(square_selector_archive)
    results: JsonObject = {}
    for sample_index in EXPECTED_SAMPLE_INDICES:
        record = selected[sample_index]
        render = mapping(record.get("render"), f"sample {sample_index} render")
        main_geometry, _ = _background_geometry(dict(render), fragment)
        mvp = _background_mvp(dict(render), fragment)
        background_scissor, _ = _draw_scissors(dict(render), fragment)
        quad = runtime_quad_from_vertices(
            main_geometry.vertices,
            name=f"dynamic-background-main-sample-{sample_index}",
            mvp_payload=mvp,
        )
        selector_use = calibration.use_for(quad.case, selector_table)
        state_selectors = list(selector_table)
        state_selectors[selector_use.table_index] = selector_use.selected
        bounds, region, primitives = _predicted_region(
            quad,
            selector_table=state_selectors,
        )
        predicted = np.zeros(
            (CAPTURE_SIZE, CAPTURE_SIZE, CHANNEL_COUNT),
            dtype=np.uint32,
        )
        left, bottom, right, top = bounds
        scissor_x, scissor_y, scissor_width, scissor_height = background_scissor
        clipped_left = max(left, scissor_x)
        clipped_bottom = max(bottom, scissor_y)
        clipped_right = min(right, scissor_x + scissor_width)
        clipped_top = min(top, scissor_y + scissor_height)
        if clipped_left >= clipped_right or clipped_bottom >= clipped_top:
            raise ValueError(
                f"sample {sample_index} background scissor excludes the main quad"
            )
        predicted[
            clipped_bottom:clipped_top,
            clipped_left:clipped_right,
        ] = region[
            clipped_bottom - bottom : clipped_top - bottom,
            clipped_left - left : clipped_right - left,
        ]
        selected_slope_bits = slopes_bits(quad, state_selectors)

        main_path, combined_path = _trace_outputs(
            root,
            render,
            sample_index=sample_index,
        )
        # Prediction is complete before either raw trace is opened.
        main_reference = _load_uint_image(main_path)
        combined_reference = _load_uint_image(combined_path)
        comparison = _comparison(main_reference, predicted)
        results[str(sample_index)] = {
            "remaining": record.get("remaining"),
            "requestedProgress": record.get("requestedProgress"),
            "mainTrace": {
                "path": str(main_path),
                "sha256": sha256_file(main_path),
                "coverage": _coverage(main_reference),
            },
            "combinedTrace": {
                "path": str(combined_path),
                "sha256": sha256_file(combined_path),
                **_combined_diagnostic(main_reference, combined_reference),
            },
            "quad": {
                "fixedBounds": [
                    quad.case.originXFixed,
                    quad.case.originYFixed,
                    quad.case.originXFixed + quad.case.widthFixed,
                    quad.case.originYFixed + quad.case.heightFixed,
                ],
                "visibleBounds": list(bounds),
                "capturedScissor": list(background_scissor),
                "clippedBounds": [
                    clipped_left,
                    clipped_bottom,
                    clipped_right,
                    clipped_top,
                ],
                "diagonal": (
                    "ascending" if quad.ascendingDiagonal else "descending"
                ),
                "channelAxes": list(quad.channelAxes),
                "slopeBits": [
                    f"0x{value:08x}" for value in selected_slope_bits
                ],
                "reciprocalSelector": {
                    "fractionalTableIndex": selector_use.table_index,
                    "base": selector_use.base,
                    "selected": selector_use.selected,
                    "offset": selector_use.offset,
                    "squareCalibrationUsed": True,
                },
                "primitivePixelCounts": [
                    int(np.count_nonzero(primitives == primitive))
                    for primitive in (0, 1)
                ],
            },
            "comparison": comparison,
        }

    compared_words = sum(
        int(mapping(result, "sample result")["comparison"]["comparedWords"])
        for result in results.values()
    )
    mismatched_words = sum(
        int(mapping(result, "sample result")["comparison"]["mismatchedWords"])
        for result in results.values()
    )
    every_state_exact = all(
        mapping(result, "sample result")["comparison"]["exact"] is True
        for result in results.values()
    )
    exact = (
        compared_words == EXPECTED_COMPARED_WORDS
        and mismatched_words == 0
        and every_state_exact
    )
    return {
        "liquidGlassDynamicBackgroundInterpolantGateSchemaVersion": 1,
        "classification": evidence_classification,
        "dynamicArtifact": str(dynamic_root),
        "transitionReport": str(report_path),
        "preregistration": {
            "path": str(preregistration),
            "sha256": sha256_file(preregistration),
            "record": registration,
        },
        "followupPreregistration": (
            {
                "path": str(followup_preregistration),
                "sha256": sha256_file(followup_preregistration),
                "record": followup_registration,
                "runId": run_id,
            }
            if followup_registration is not None
            and followup_preregistration is not None
            else None
        ),
        "predictor": {
            "gateFile": str(Path(__file__)),
            "gateFileSha256": sha256_file(Path(__file__)),
            "verifiedFrozenFiles": verified_files,
            "capturedInterpolantReadByPredictor": False,
            "predictionCompletedBeforeRawTraceOpen": prospective_evidence,
            "squareSelectorCalibration": {
                "classification": "retrospective finite-domain calibration",
                "widthFixedLower": WIDTH_FIXED_LOWER,
                "selectorCount": SELECTOR_COUNT,
            },
        },
        "samples": results,
        "gate": {
            "sampleCount": len(results),
            "comparedWords": compared_words,
            "expectedComparedWords": EXPECTED_COMPARED_WORDS,
            "mismatchedWords": mismatched_words,
            "everyStateExact": every_state_exact,
            "exact": exact,
            "calibrationBacked": True,
            "combinedReplayUsedForAcceptance": False,
            "prospectiveEvidence": prospective_evidence,
            "productionShaderAuthorized": False,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dynamic_root", type=Path)
    parser.add_argument("--square-selector-archive", type=Path, required=True)
    parser.add_argument(
        "--preregistration",
        type=Path,
        default=Path(
            "lg-test/Analysis/"
            "background_interpolant_transfer_preregistration.json"
        ),
    )
    parser.add_argument(
        "--evidence-classification",
        choices=(
            "post-opening-harness-correction",
            "prospective-followup",
            "retrospective-opened-diagnostic",
        ),
        required=True,
    )
    parser.add_argument("--followup-preregistration", type=Path)
    parser.add_argument("--run-id", type=int)
    parser.add_argument("-o", "--output", type=Path)
    arguments = parser.parse_args()
    report = run_gate(
        arguments.dynamic_root,
        square_selector_archive=arguments.square_selector_archive,
        preregistration=arguments.preregistration,
        evidence_classification=arguments.evidence_classification,
        followup_preregistration=arguments.followup_preregistration,
        run_id=arguments.run_id,
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
