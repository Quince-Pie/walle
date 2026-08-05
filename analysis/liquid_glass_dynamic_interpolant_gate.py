#!/usr/bin/env python3
"""Bit-gate modeled raster interpolants at dynamic highlight checkpoints."""

import argparse
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from liquid_glass_dynamic_capture import (
    EXPECTED_SAMPLE_INDICES,
    _background_mvp,
    _highlight_geometry,
    _report_paths,
)
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
    NEAR_SQUARE_HEIGHT_DELTAS,
    NEAR_SQUARE_SELECTOR_COUNT,
    SELECTOR_COUNT,
    WIDTH_FIXED_LOWER,
    SquareSelectorCalibration,
    base_selector_use,
)

import raster_tile_selector_model as arithmetic


type JsonObject = dict[str, Any]
type UIntImage = NDArray[np.uint32]

CAPTURE_SIZE = 1024
FULL_TRACE_SAMPLE_INDICES = frozenset({1, 12, 32})


def mapping(value: object, name: str) -> JsonObject:
    if not isinstance(value, dict):
        raise ValueError(f"{name} is not an object")
    return value


def _trace_reference(
    root: Path,
    render: JsonObject,
    *,
    sample_index: int,
) -> tuple[Path, UIntImage, str]:
    replay = mapping(render.get("exactPassReplay"), "exact pass replay")
    trace_key = (
        "finalHighlightAlphaTrace"
        if sample_index in FULL_TRACE_SAMPLE_INDICES
        else "finalHighlightInterpolantTrace"
    )
    trace = mapping(replay.get(trace_key), trace_key)
    interpolant = mapping(trace.get("exactInterpolant"), "exact interpolant")
    output = mapping(interpolant.get("output"), "exact interpolant output")
    filename = output.get("rawFile")
    expected_bytes = CAPTURE_SIZE * CAPTURE_SIZE * 4 * 4
    if (
        trace.get("executed") is not True
        or interpolant.get("executed") is not True
        or output.get("rawCapture") is not True
        or output.get("pixelFormat") != 123
        or output.get("width") != CAPTURE_SIZE
        or output.get("height") != CAPTURE_SIZE
        or output.get("rawBytes") != expected_bytes
        or not isinstance(filename, str)
    ):
        raise ValueError(f"sample {sample_index} interpolant trace differs")
    path = root / filename
    values = np.fromfile(path, dtype="<u4")
    expected_words = CAPTURE_SIZE * CAPTURE_SIZE * 4
    if values.size != expected_words:
        raise ValueError(
            f"{path} has {values.size} words; expected {expected_words}"
        )
    return path, values.reshape(CAPTURE_SIZE, CAPTURE_SIZE, 4), trace_key


def _quad(
    render: JsonObject,
    *,
    name: str,
    mvp_payload: bytes,
) -> RuntimeQuad:
    geometry = _highlight_geometry(render)
    if geometry.indices is None:
        raise ValueError(f"{name} has no highlight index buffer")
    vertices = geometry.vertices[geometry.indices].copy()
    # The final-highlight fragment consumes only SDF.xy. Source UV differs by
    # one binary32 word at one corner in the captured draw, so it is not an
    # axis-separable field and must not constrain the two-channel SDF gate.
    vertices[:, 6:8] = vertices[:, 4:6]
    return runtime_quad_from_vertices(
        vertices,
        name=name,
        mvp_payload=mvp_payload,
    )


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
        for channel in range(4)
        for primitive in (0, 1)
    }
    yy, xx = np.indices((top - bottom, right - left), dtype=np.uint32)
    xx += np.uint32(left)
    yy += np.uint32(bottom)
    primitives = primitive_ids(quad, xx, yy)
    candidate = np.empty((top - bottom, right - left, 4), dtype=np.uint32)
    for channel, axis in enumerate(quad.channelAxes):
        indices = xx - np.uint32(left) if axis == 0 else yy - np.uint32(bottom)
        for primitive in (0, 1):
            selected = primitives == primitive
            candidate[..., channel][selected] = axis_predictions[
                channel,
                primitive,
            ][indices[selected]]
    return (left, bottom, right, top), candidate, primitives


def _comparison(
    reference: UIntImage,
    candidate: UIntImage,
    *,
    left: int,
    bottom: int,
) -> JsonObject:
    if reference.shape != candidate.shape:
        raise ValueError(f"interpolant shapes differ: {reference.shape}")
    changed = candidate != reference
    changed_pixels = np.any(changed, axis=2)
    coordinates = np.argwhere(changed_pixels)
    examples = [
        {
            "x": int(x + left),
            "y": int(y + bottom),
            "predictedBits": [
                f"0x{int(value):08x}" for value in candidate[y, x]
            ],
            "appleBits": [f"0x{int(value):08x}" for value in reference[y, x]],
            "predictedValues": [float(value) for value in candidate[y, x].view("<f4")],
            "appleValues": [float(value) for value in reference[y, x].view("<f4")],
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
            for channel in range(reference.shape[2])
        ],
        "examples": examples,
    }


def _coverage(reference: UIntImage) -> JsonObject:
    active = np.any(reference != 0, axis=2)
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


def run_gate(
    dynamic_root: Path,
    *,
    square_selector_archive: Path | None = None,
    near_square_selector_archive: Path | None = None,
) -> JsonObject:
    reports = _report_paths(dynamic_root)
    if len(reports) != 1:
        raise ValueError(f"expected one dynamic report under {dynamic_root}")
    report_path = reports[0]
    root = report_path.parent
    report = json.loads(report_path.read_text(encoding="utf-8"))
    try:
        fragment = GLASS_FRAGMENTS[str(report.get("material"))]
    except KeyError as error:
        raise ValueError("dynamic material is unsupported") from error
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
        raise ValueError(f"interpolant samples differ: {sorted(selected)}")
    selector_table = list(arithmetic.load_selector_table())
    square_calibration = (
        SquareSelectorCalibration.load(
            square_selector_archive,
            near_square_path=near_square_selector_archive,
        )
        if square_selector_archive is not None
        else None
    )
    results: JsonObject = {}
    for sample_index in EXPECTED_SAMPLE_INDICES:
        record = selected[sample_index]
        render = mapping(record.get("render"), f"sample {sample_index} render")
        path, reference, trace_key = _trace_reference(
            root,
            render,
            sample_index=sample_index,
        )
        quad = _quad(
            render,
            name=f"dynamic-highlight-sample-{sample_index}",
            mvp_payload=_background_mvp(render, fragment),
        )
        selector_use = (
            square_calibration.use_for(quad.case, selector_table)
            if square_calibration is not None
            else base_selector_use(quad.case, selector_table)
        )
        selector_table[selector_use.table_index] = selector_use.selected
        try:
            bounds, candidate, primitives = _predicted_region(
                quad,
                selector_table=selector_table,
            )
            selected_slope_bits = slopes_bits(quad, selector_table)
        finally:
            selector_table[selector_use.table_index] = selector_use.base
        left, bottom, right, top = bounds
        outside = np.any(reference != 0, axis=2)
        outside[bottom:top, left:right] = False
        comparison = _comparison(
            reference[bottom:top, left:right, :2],
            candidate[..., :2],
            left=left,
            bottom=bottom,
        )
        results[str(sample_index)] = {
            "remaining": record.get("remaining"),
            "trace": {
                "record": trace_key,
                "path": str(path),
                "sha256": sha256_file(path),
                "coverage": _coverage(reference),
                "activePixelsOutsidePredictedBounds": int(np.count_nonzero(outside)),
            },
            "quad": {
                "fixedBounds": [
                    quad.case.originXFixed,
                    quad.case.originYFixed,
                    quad.case.originXFixed + quad.case.widthFixed,
                    quad.case.originYFixed + quad.case.heightFixed,
                ],
                "visibleBounds": list(bounds),
                "diagonal": (
                    "ascending" if quad.ascendingDiagonal else "descending"
                ),
                "channelAxes": list(quad.channelAxes),
                "slopeBits": [
                    f"0x{bits:08x}"
                    for bits in selected_slope_bits
                ],
                "reciprocalSelector": {
                    "fractionalTableIndex": selector_use.table_index,
                    "base": selector_use.base,
                    "selected": selector_use.selected,
                    "offset": selector_use.offset,
                    "squareCalibrationUsed": square_calibration is not None,
                },
                "primitivePixelCounts": [
                    int(np.count_nonzero(primitives == primitive))
                    for primitive in (0, 1)
                ],
            },
            "comparison": comparison,
            "comparedChannels": ["sdf-x", "sdf-y"],
        }
    exact = all(
        mapping(result, sample)["comparison"]["exact"]
        and result["trace"]["activePixelsOutsidePredictedBounds"] == 0
        for sample, result in results.items()
    )
    return {
        "liquidGlassDynamicInterpolantGateSchemaVersion": 4,
        "dynamicArtifact": str(dynamic_root),
        "predictor": {
            "file": "analysis/liquid_glass_runtime_raster_coefficients.py",
            "capturedInterpolantReadByPredictor": False,
            "squareSelectorCalibration": (
                {
                    "path": str(square_selector_archive),
                    "sha256": sha256_file(square_selector_archive),
                    "widthFixedLower": WIDTH_FIXED_LOWER,
                    "selectorCount": SELECTOR_COUNT,
                    "classification": "retrospective finite-domain calibration",
                }
                if square_selector_archive is not None
                and square_calibration is not None
                else None
            ),
            "nearSquareSelectorCalibration": (
                {
                    "path": str(near_square_selector_archive),
                    "sha256": sha256_file(near_square_selector_archive),
                    "heightFixedDeltas": list(NEAR_SQUARE_HEIGHT_DELTAS),
                    "selectorCount": NEAR_SQUARE_SELECTOR_COUNT,
                    "classification": (
                        "preregistered finite-domain calibration; "
                        "not a universal closed form"
                    ),
                }
                if near_square_selector_archive is not None
                and square_calibration is not None
                else None
            ),
        },
        "samples": results,
        "gate": {
            "sampleCount": len(results),
            "mismatchedWords": sum(
                int(result["comparison"]["mismatchedWords"])
                for result in results.values()
            ),
            "exact": exact,
            "calibrationBacked": square_calibration is not None,
            "productionShaderAuthorized": False,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dynamic_root", type=Path)
    parser.add_argument("--square-selector-archive", type=Path)
    parser.add_argument("--near-square-selector-archive", type=Path)
    parser.add_argument("-o", "--output", type=Path)
    arguments = parser.parse_args()
    report = run_gate(
        arguments.dynamic_root,
        square_selector_archive=arguments.square_selector_archive,
        near_square_selector_archive=arguments.near_square_selector_archive,
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
