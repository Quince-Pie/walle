#!/usr/bin/env python3
"""Probe an opened dynamic prefix mismatch without parity claims."""

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np

from apple_glass_reference_renderer import (
    AppleGlassReferenceRenderer,
    CodeImage,
    compare_images,
)
from liquid_glass_dynamic_background_arithmetic import (
    GENERIC_EXACT_CONFIGURATION,
    _apply_scissor,
    _compare_words,
    _load_half,
    _load_uint,
    _unpack_half_pairs,
)
from liquid_glass_dynamic_capture import (
    _background_geometry,
    _background_mvp,
    _report_paths,
    _source_texture,
    _uniform_payloads,
)
from liquid_glass_dynamic_render_gate import (
    DYNAMIC_HIGHLIGHT_CONFIGURATION,
    _draw_scissors,
    _glass_prefix_output,
    _mismatch_details,
    _pre_final_input,
    _raw_mip_levels,
)
from liquid_glass_post_glass_gate import sha256_file
from liquid_glass_profile_matrix import GLASS_FRAGMENTS
from liquid_glass_runtime_raster_coefficients import (
    axis_table,
    runtime_quad_from_vertices,
)
from liquid_glass_square_selector_calibration import SquareSelectorCalibration

import raster_tile_selector_model as arithmetic


type JsonObject = dict[str, Any]

COMPOSITE_MIX_MODE_COUNT = 8
HOLDING_MIX_MODE_COUNT = 18
HOLDING_DIVIDE_MODE_COUNT = 10

MIX_PROBE = r"""
uniform int ProbeCompositeMixMode;
uniform int ProbeHoldingMixMode;

float probe_half_mix(float left, float right, float amount, int mode)
{
    if (mode == 8) {
        float left_term = half_fma_exact(-left, amount, left);
        return half_fma_exact(right, amount, left_term);
    }
    if (mode == 9) {
        float base = half_fma_exact(right, amount, left);
        return half_fma_exact(-left, amount, base);
    }
    if (mode == 10) {
        float left_term = half_fma(-left, amount, left);
        return half_fma(right, amount, left_term);
    }
    if (mode == 11) {
        float left_term = half_subtract(
            left,
            half_multiply(left, amount)
        );
        return half_fma_exact(right, amount, left_term);
    }
    if (mode == 12) {
        float left_term = half_fma_exact(-left, amount, left);
        return half_add(left_term, half_multiply(right, amount));
    }
    if (mode == 13) {
        return half_subtract(
            half_add(left, half_multiply(right, amount)),
            half_multiply(left, amount)
        );
    }
    float inverse = half_subtract(1.0, amount);
    if (mode == 14) {
        return half_fma_exact(
            half_subtract(left, right),
            inverse,
            right
        );
    }
    if (mode == 15) {
        return half_add(
            right,
            half_multiply(half_subtract(left, right), inverse)
        );
    }
    if (mode == 16) {
        return half_value(
            right + (left - right) * float(inverse)
        );
    }
    if (mode == 17) {
        float right_term = half_fma_exact(-right, inverse, right);
        return half_fma_exact(left, inverse, right_term);
    }
    if (mode == 1) {
        return half_value(mix(left, right, amount));
    }
    float delta = half_subtract(right, left);
    if (mode == 2) {
        return half_add(left, half_multiply(delta, amount));
    }
    if (mode == 3) {
        return half_fma_exact(delta, amount, left);
    }
    if (mode == 4) {
        return half_fma(delta, amount, left);
    }
    float right_product = half_multiply(right, amount);
    if (mode == 5) {
        return half_add(half_multiply(left, inverse), right_product);
    }
    if (mode == 6) {
        return half_fma_exact(left, inverse, right_product);
    }
    if (mode == 7) {
        return half_value(
            left * float(inverse) + right * float(amount)
        );
    }
    return half_mix_exact(left, right, amount);
}

vec4 probe_half_mix(vec4 left, vec4 right, float amount, int mode)
{
    return vec4(
        probe_half_mix(left.x, right.x, amount, mode),
        probe_half_mix(left.y, right.y, amount, mode),
        probe_half_mix(left.z, right.z, amount, mode),
        probe_half_mix(left.w, right.w, amount, mode)
    );
}
"""


def mapping(value: object, name: str) -> JsonObject:
    if not isinstance(value, dict):
        raise ValueError(f"{name} is not an object")
    return value


def comparison(reference: CodeImage, candidate: CodeImage) -> JsonObject:
    return {
        **compare_images(reference, candidate).as_json(),
        **_mismatch_details(reference, candidate),
    }


def _arithmetic_trace_paths(
    root: Path,
    render: JsonObject,
    names: frozenset[str],
) -> dict[str, Path]:
    exact = mapping(render.get("exactPassReplay"), "exact pass replay")
    trace = mapping(
        exact.get("backgroundArithmeticTrace"),
        "background arithmetic trace",
    )
    replays = trace.get("replays")
    if (
        trace.get("schemaVersion") != 1
        or trace.get("executed") is not True
        or trace.get("scope")
        not in {
            "sample-16-custom-metal-main-only",
            "selected-dynamic-states-custom-metal-main-only",
        }
        or trace.get("capturedAppleFunctionUnmodified") is not False
        or trace.get("customStageInVertex") is not True
        or trace.get("classification")
        != "diagnostic custom-Metal arithmetic replay"
        or not isinstance(replays, list)
    ):
        raise ValueError("background arithmetic trace metadata differs")
    observed = {
        str(item.get("name")): mapping(item, "arithmetic trace wrapper")
        for item in replays
        if isinstance(item, dict) and item.get("name") in names
    }
    paths: dict[str, Path] = {}
    layouts = {
        "sdf": (115, 8),
        "color-stages-a": (123, 16),
        "color-stages-b": (123, 16),
        "holding-operands": (123, 16),
    }
    for name, wrapper in observed.items():
        replay = mapping(wrapper.get("replay"), f"{name} replay")
        output = mapping(replay.get("output"), f"{name} output")
        filename = output.get("rawFile")
        pixel_format, bytes_per_pixel = layouts[name]
        expected_bytes = 1024 * 1024 * bytes_per_pixel
        if (
            wrapper.get("pixelFormat") != pixel_format
            or replay.get("executed") is not True
            or replay.get("glassDrawCount") != 1
            or output.get("pixelFormat") != pixel_format
            or output.get("width") != 1024
            or output.get("height") != 1024
            or output.get("rawBytes") != expected_bytes
            or not isinstance(filename, str)
        ):
            raise ValueError(f"background arithmetic trace layout differs: {name}")
        path = root / filename
        if not path.is_file() or path.stat().st_size != expected_bytes:
            raise ValueError(f"background arithmetic trace file differs: {path}")
        paths[name] = path
    return paths


def _half_samples(
    words: np.ndarray,
    coordinates: list[tuple[int, int]],
) -> list[JsonObject]:
    values = words.view(np.float16)
    return [
        {
            "x": x,
            "y": y,
            "halfCodes": [int(value) for value in words[y, x]],
            "values": [float(value) for value in values[y, x]],
        }
        for y, x in coordinates
    ]


def instrumented_shader(path: Path) -> str:
    source = path.read_text(encoding="utf-8")
    main_anchor = "\nvoid main()\n{"
    composite_anchor = ": half_mix_exact(shadow_layer, face, coverage);"
    holding_anchor = ": replay_holding_mix(composite, holding, amount);"
    trace_declaration_anchor = "    vec4 composite_trace = composite;\n"
    amount_anchor = """        float amount = half_multiply(
            holding_distance,
            HoldingToneOpacity
        );
"""
    trace_dispatch_anchor = """    if (NumericTrace == 10) {
"""
    if (
        source.count(main_anchor) != 1
        or source.count(composite_anchor) != 1
        or source.count(holding_anchor) != 1
        or source.count(trace_declaration_anchor) != 1
        or source.count(amount_anchor) != 1
        or source.count(trace_dispatch_anchor) != 1
    ):
        raise ValueError("prefix mix instrumentation anchors differ")
    source = source.replace(main_anchor, f"\n{MIX_PROBE}{main_anchor}")
    source = source.replace(
        composite_anchor,
        ": probe_half_mix("
        "shadow_layer, face, coverage, ProbeCompositeMixMode);",
    )
    source = source.replace(
        trace_declaration_anchor,
        trace_declaration_anchor
        + "    vec4 probe_holding_operand_trace = vec4(0.0);\n"
        + "    float probe_holding_amount_trace = 0.0;\n"
        + "    float probe_holding_distance_trace = 0.0;\n",
    )
    source = source.replace(
        amount_anchor,
        amount_anchor
        + "        probe_holding_operand_trace = holding;\n"
        + "        probe_holding_amount_trace = amount;\n"
        + "        probe_holding_distance_trace = holding_distance;\n",
    )
    source = source.replace(
        trace_dispatch_anchor,
        """    if (NumericTrace == 26) {
        fragment_color = probe_holding_operand_trace;
        return;
    }
    if (NumericTrace == 27) {
        fragment_color = vec4(probe_holding_amount_trace);
        return;
    }
    if (NumericTrace == 28) {
        fragment_color = vec4(probe_holding_distance_trace);
        return;
    }
"""
        + trace_dispatch_anchor,
    )
    return source.replace(
        holding_anchor,
        ": probe_half_mix("
        "composite, holding, amount, ProbeHoldingMixMode);",
    )


def run_probe(
    dynamic_root: Path,
    *,
    sample_index: int,
    static_capture: Path,
    float_intrinsic_table: Path,
    square_selector_archive: Path,
) -> JsonObject:
    report_paths = _report_paths(dynamic_root)
    if len(report_paths) != 1:
        raise ValueError(f"expected one dynamic report, found {len(report_paths)}")
    report_path = report_paths[0]
    report = mapping(
        json.loads(report_path.read_text(encoding="utf-8")),
        "dynamic report",
    )
    material = str(report.get("material"))
    appearance = str(report.get("appearance"))
    fragment = GLASS_FRAGMENTS[material]
    uniforms = mapping(report.get("dynamicBackgroundUniforms"), "dynamic uniforms")
    records = uniforms.get("records")
    if not isinstance(records, list):
        raise ValueError("dynamic records are absent")
    matches = [
        mapping(record, "dynamic record")
        for record in records
        if isinstance(record, dict) and record.get("sampleIndex") == sample_index
    ]
    if len(matches) != 1:
        raise ValueError(f"sample {sample_index} is absent or duplicated")
    record = matches[0]
    render = mapping(record.get("render"), "dynamic render")
    if render.get("executed") is not True:
        raise ValueError("dynamic render did not execute")

    main, shadow = _background_geometry(render, fragment)
    mvp = _background_mvp(render, fragment)
    profile_payload, _ = _uniform_payloads(render, fragment)
    source = _source_texture(render, fragment)
    background_scissor, highlight_scissor = _draw_scissors(render, fragment)
    prefix_path, prefix_reference = _glass_prefix_output(
        report_path.parent,
        render,
    )
    pre_final_path = _pre_final_input(report_path.parent, render)
    selector_table = list(arithmetic.load_selector_table())
    square_calibration = SquareSelectorCalibration.load(square_selector_archive)
    quad = runtime_quad_from_vertices(
        main.vertices,
        name=f"{material}-{appearance}-sample-{sample_index}-prefix-probe",
    )
    selector_use = square_calibration.use_for(quad.case, selector_table)
    selector_table[selector_use.table_index] = selector_use.selected
    axis_start, axes = axis_table(
        quad,
        selector_table=selector_table,
        helper_lane_halo=2,
    )

    with AppleGlassReferenceRenderer(
        static_capture,
        fragment_shader_source=instrumented_shader(
            Path("analysis/apple_glass_reference.frag.glsl")
        ),
        intrinsic_table=float_intrinsic_table,
        half_intrinsic_table=static_capture / "half-intrinsics.bin",
        load_interpolant_trace=False,
        load_interpolant_axis_trace=False,
        load_diagnostic_traces=False,
    ) as renderer:
        for name, value in GENERIC_EXACT_CONFIGURATION.items():
            if name in renderer.program:
                renderer.program[name].value = value
        for name, value in DYNAMIC_HIGHLIGHT_CONFIGURATION.items():
            if name in renderer.program:
                renderer.program[name].value = value
        renderer.program["ProbeCompositeMixMode"].value = 0
        renderer.program["ProbeHoldingMixMode"].value = 0
        renderer.set_draw_geometries(main=main, shadow=shadow)
        renderer.set_mvp_payload(mvp)
        renderer.set_profile_payload(profile_payload)
        renderer.set_source_mip_bgra(
            _raw_mip_levels(report_path.parent, source)
        )
        renderer.set_destination_bgra_path(pre_final_path)
        renderer.set_draw_scissors(
            background=background_scissor,
            final_highlight=highlight_scissor,
        )
        renderer.set_interpolant_axis_table(axes, start=axis_start)
        renderer.program["CoordinateMode"].value = 4

        baseline = renderer.render()
        baseline_comparison = comparison(prefix_reference, baseline)
        renderer.program["ProbeHoldingMixMode"].value = 1
        holding_mode_one = renderer.render()
        renderer.program["ProbeHoldingMixMode"].value = 0
        changed = np.any(prefix_reference != baseline, axis=2) | np.any(
            prefix_reference != holding_mode_one,
            axis=2,
        )
        exact_replay = mapping(render.get("exactPassReplay"), "exact pass replay")
        trace_paths: dict[str, Path] = {}
        reference_holding: np.ndarray | None = None
        if "backgroundArithmeticTrace" in exact_replay:
            trace_paths = _arithmetic_trace_paths(
                report_path.parent,
                render,
                frozenset(
                    {
                        "sdf",
                        "color-stages-a",
                        "color-stages-b",
                        "holding-operands",
                    }
                ),
            )
            if "color-stages-b" in trace_paths:
                packed_stages = _unpack_half_pairs(
                    _load_uint(trace_paths["color-stages-b"])
                )
                reference_holding = packed_stages[..., 4:8]
                portable_holding = _apply_scissor(
                    renderer.render_numeric_trace(13),
                    background_scissor,
                )
                changed |= np.any(reference_holding != portable_holding, axis=2)
        coordinates = [
            (int(y), int(x)) for y, x in np.argwhere(changed)
        ]
        traces: JsonObject = {}
        for trace_mode in (
            1,
            2,
            4,
            5,
            6,
            7,
            8,
            9,
            10,
            11,
            12,
            13,
            14,
            21,
            26,
            27,
            28,
        ):
            words = renderer.render_numeric_trace(trace_mode)
            traces[str(trace_mode)] = _half_samples(words, coordinates)
        holding_mode_traces: JsonObject = {}
        for mode in range(HOLDING_MIX_MODE_COUNT):
            renderer.program["ProbeHoldingMixMode"].value = mode
            words = renderer.render_numeric_trace(13)
            holding_mode_traces[str(mode)] = _half_samples(words, coordinates)
        renderer.program["ProbeHoldingMixMode"].value = 0

        sweeps: JsonObject = {}
        sweep_values = {
            "InnerSamplerCoordinateModel": range(6),
            "SamplerSpatialQuantization": range(2),
            "SamplerModel": range(2),
            "AppleFastSqrtBias": range(3),
            "AppleFastReciprocalBias": range(3),
        }
        baseline_values = {
            name: renderer.program[name].value for name in sweep_values
        }
        for name, values in sweep_values.items():
            cases: JsonObject = {}
            for value in values:
                renderer.program[name].value = value
                cases[str(value)] = comparison(prefix_reference, renderer.render())
            renderer.program[name].value = baseline_values[name]
            sweeps[name] = cases

        combined_coordinate_quantization: JsonObject = {}
        for coordinate_mode in range(6):
            renderer.program["InnerSamplerCoordinateModel"].value = coordinate_mode
            for quantization_mode in range(2):
                renderer.program["SamplerSpatialQuantization"].value = (
                    quantization_mode
                )
                combined_coordinate_quantization[
                    f"coordinate-{coordinate_mode}-quantization-{quantization_mode}"
                ] = comparison(prefix_reference, renderer.render())
        renderer.program["InnerSamplerCoordinateModel"].value = baseline_values[
            "InnerSamplerCoordinateModel"
        ]
        renderer.program["SamplerSpatialQuantization"].value = baseline_values[
            "SamplerSpatialQuantization"
        ]
        mix_sweeps: JsonObject = {}
        for uniform in ("ProbeCompositeMixMode", "ProbeHoldingMixMode"):
            cases: JsonObject = {}
            mode_count = (
                COMPOSITE_MIX_MODE_COUNT
                if uniform == "ProbeCompositeMixMode"
                else HOLDING_MIX_MODE_COUNT
            )
            for mode in range(mode_count):
                renderer.program[uniform].value = mode
                cases[str(mode)] = comparison(prefix_reference, renderer.render())
            renderer.program[uniform].value = 0
            mix_sweeps[uniform] = cases
        combined_mix_sweep: JsonObject = {}
        for composite_mode in range(COMPOSITE_MIX_MODE_COUNT):
            renderer.program["ProbeCompositeMixMode"].value = composite_mode
            for holding_mode in range(HOLDING_MIX_MODE_COUNT):
                renderer.program["ProbeHoldingMixMode"].value = holding_mode
                combined_mix_sweep[
                    f"composite-{composite_mode}-holding-{holding_mode}"
                ] = comparison(prefix_reference, renderer.render())
        renderer.program["ProbeCompositeMixMode"].value = 0
        renderer.program["ProbeHoldingMixMode"].value = 0
        custom_holding_stage_sweep: JsonObject | None = None
        custom_holding_operand_division_sweep: JsonObject | None = None
        custom_color_stage_comparisons: JsonObject | None = None
        custom_color_stage_samples: JsonObject | None = None
        if trace_paths:
            custom_color_stage_comparisons = {}
            custom_color_stage_samples = {}
            if "sdf" in trace_paths:
                reference_sdf = _load_half(trace_paths["sdf"])
                candidate_sdf = _apply_scissor(
                    renderer.render_numeric_trace(1),
                    background_scissor,
                )
                custom_color_stage_comparisons["sdf"] = _compare_words(
                    reference_sdf,
                    candidate_sdf,
                )
                custom_color_stage_samples["sdf"] = {
                    "customMetal": _half_samples(reference_sdf, coordinates),
                    "portable": _half_samples(candidate_sdf, coordinates),
                }
            stage_specs = {
                "color-stages-a": (("source", 10), ("face", 11)),
                "color-stages-b": (("composite", 12),),
            }
            for trace_name, specifications in stage_specs.items():
                if trace_name not in trace_paths:
                    continue
                packed_stages = _unpack_half_pairs(
                    _load_uint(trace_paths[trace_name])
                )
                for offset, (stage_name, trace_mode) in enumerate(specifications):
                    reference_stage = packed_stages[
                        ...,
                        offset * 4 : (offset + 1) * 4,
                    ]
                    candidate_stage = _apply_scissor(
                        renderer.render_numeric_trace(trace_mode),
                        background_scissor,
                    )
                    custom_color_stage_comparisons[stage_name] = _compare_words(
                        reference_stage,
                        candidate_stage,
                    )
                    custom_color_stage_samples[stage_name] = {
                        "customMetal": _half_samples(
                            reference_stage,
                            coordinates,
                        ),
                        "portable": _half_samples(candidate_stage, coordinates),
                    }
            if "holding-operands" in trace_paths:
                packed_operands = _unpack_half_pairs(
                    _load_uint(trace_paths["holding-operands"])
                )
                operand_specs = {
                    "holding-operand": (packed_operands[..., 0:4], 26, slice(None)),
                    "holding-amount": (packed_operands[..., 4:5], 27, slice(0, 1)),
                    "holding-distance": (
                        packed_operands[..., 5:6],
                        28,
                        slice(0, 1),
                    ),
                }
                for stage_name, (
                    reference_stage,
                    trace_mode,
                    candidate_slice,
                ) in operand_specs.items():
                    candidate_stage = _apply_scissor(
                        renderer.render_numeric_trace(trace_mode),
                        background_scissor,
                    )[..., candidate_slice]
                    custom_color_stage_comparisons[stage_name] = _compare_words(
                        reference_stage,
                        candidate_stage,
                    )
                    custom_color_stage_samples[stage_name] = {
                        "customMetal": _half_samples(
                            reference_stage,
                            coordinates,
                        ),
                        "portable": _half_samples(candidate_stage, coordinates),
                    }
                reference_holding_operand = packed_operands[..., 0:4]
                custom_holding_operand_division_sweep = {}
                for mode in range(HOLDING_DIVIDE_MODE_COUNT):
                    renderer.program["HoldingDivideMode"].value = mode
                    candidate_holding_operand = _apply_scissor(
                        renderer.render_numeric_trace(26),
                        background_scissor,
                    )
                    custom_holding_operand_division_sweep[str(mode)] = (
                        _compare_words(
                            reference_holding_operand,
                            candidate_holding_operand,
                        )
                    )
                renderer.program["HoldingDivideMode"].value = 0
            if "color-stages-b" in trace_paths:
                if reference_holding is None:
                    raise RuntimeError("holding reference was not preloaded")
                custom_color_stage_samples["holding"] = {
                    "customMetal": _half_samples(
                        reference_holding,
                        coordinates,
                    ),
                }
                custom_holding_stage_sweep = {}
                for mode in range(HOLDING_MIX_MODE_COUNT):
                    renderer.program["ProbeHoldingMixMode"].value = mode
                    candidate_holding = _apply_scissor(
                        renderer.render_numeric_trace(13),
                        background_scissor,
                    )
                    custom_holding_stage_sweep[str(mode)] = _compare_words(
                        reference_holding,
                        candidate_holding,
                    )
            renderer.program["ProbeHoldingMixMode"].value = 0
        implementation = renderer.implementation

    return {
        "liquidGlassDynamicPrefixMismatchProbeSchemaVersion": 3,
        "classification": "retrospective opened diagnostic",
        "productionShaderAuthorized": False,
        "dynamicArtifact": str(dynamic_root),
        "sampleIndex": sample_index,
        "profile": {"material": material, "appearance": appearance},
        "prefixReference": {
            "path": str(prefix_path),
            "sha256": sha256_file(prefix_path),
        },
        "runtimeRaster": {
            "axisStart": axis_start,
            "selector": {
                "base": selector_use.base,
                "selected": selector_use.selected,
                "offset": selector_use.offset,
            },
        },
        "implementation": implementation,
        "baseline": baseline_comparison,
        "probeCoordinates": [
            {"x": x, "y": y} for y, x in coordinates
        ],
        "numericTracesAtProbeCoordinates": traces,
        "holdingModeTraces": holding_mode_traces,
        "singleUniformSweeps": sweeps,
        "combinedCoordinateQuantizationSweep": combined_coordinate_quantization,
        "mixSweeps": mix_sweeps,
        "combinedMixSweep": combined_mix_sweep,
        "customMetalColorStageComparisons": custom_color_stage_comparisons,
        "customMetalColorStageSamples": custom_color_stage_samples,
        "customMetalHoldingStageSweep": custom_holding_stage_sweep,
        "customMetalHoldingOperandDivisionSweep": (
            custom_holding_operand_division_sweep
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dynamic_root", type=Path)
    parser.add_argument("--sample-index", type=int, required=True)
    parser.add_argument("--static-capture", type=Path, required=True)
    parser.add_argument("--float-intrinsic-table", type=Path, required=True)
    parser.add_argument("--square-selector-archive", type=Path, required=True)
    parser.add_argument("-o", "--output", type=Path)
    arguments = parser.parse_args()
    report = run_probe(
        arguments.dynamic_root,
        sample_index=arguments.sample_index,
        static_capture=arguments.static_capture,
        float_intrinsic_table=arguments.float_intrinsic_table,
        square_selector_archive=arguments.square_selector_archive,
    )
    encoded = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if arguments.output is None:
        print(encoded, end="")
    else:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(encoded, encoding="utf-8")
        print(arguments.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
