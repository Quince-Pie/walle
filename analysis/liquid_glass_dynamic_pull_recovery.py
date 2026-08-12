#!/usr/bin/env python3
"""Recover dynamic AGX pull, constant, and center coefficients exactly."""

import argparse
import json
import math
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from liquid_glass_dynamic_capture import (
    EXPECTED_SAMPLE_INDICES,
    _highlight_geometry,
    _report_paths,
)
from liquid_glass_dynamic_interpolant_gate import (
    _trace_reference,
    mapping,
)
from liquid_glass_post_glass_gate import sha256_file
from liquid_glass_raster_interpolant import (
    bits_float32,
    neighboring_float32_bits,
    pull_iterator_bits,
)
from liquid_glass_runtime_raster_coefficients import (
    RuntimeQuad,
    coefficient_bits,
    primitive_ids,
    runtime_quad_from_vertices,
    slopes_bits,
    visible_pixel_bounds,
)

import raster_tile_iterator_model as iterator
import raster_tile_selector_model as arithmetic
import raster_tile_selector_model_v6 as center_lattice


type JsonObject = dict[str, Any]
type UIntImage = NDArray[np.uint32]
type PullSurface = NDArray[np.uint32]

CAPTURE_SIZE = 1024
TILE_COUNT = 32
AXIS_COUNT = 2
PRIMITIVE_COUNT = 2
PULL_COUNT = 16
COMPONENT_COUNT = 4
RECORD_WORDS = 3 + PULL_COUNT * COMPONENT_COUNT
SENTINEL = 0xFFFF_FFFF
LOCKED = 0xFFFF_FFFE
MODELED_CHANNELS = (0, 1)


@dataclass(frozen=True, slots=True)
class PullRecord:
    axis: int
    primitive: int
    tile: int
    x: int
    y: int
    values: UIntImage

    @property
    def coordinate(self) -> int:
        return self.x if self.axis == 0 else self.y

    @property
    def local_pixel(self) -> int:
        return self.coordinate - self.tile * 32


def _sdf_quad(render: JsonObject, *, name: str) -> RuntimeQuad:
    geometry = _highlight_geometry(render)
    if geometry.indices is None:
        raise ValueError(f"{name} has no highlight index buffer")
    vertices = geometry.vertices[geometry.indices].copy()
    # Dynamic source UVs can be genuinely two-dimensional by one binary32
    # word. This recovery is scoped to the separable SDF pair consumed by the
    # final highlight alpha; neutralize the unrelated source pair before the
    # shared quad decoder checks separability.
    vertices[:, 6:8] = np.float32(0.0)
    return runtime_quad_from_vertices(vertices, name=name)


def _pull_reference(
    root: Path,
    render: JsonObject,
    *,
    sample_index: int,
) -> tuple[Path, PullSurface]:
    replay = mapping(render.get("exactPassReplay"), "exact pass replay")
    trace_key = (
        "finalHighlightAlphaTrace"
        if sample_index in {1, 12, 32}
        else "finalHighlightInterpolantTrace"
    )
    trace = mapping(replay.get(trace_key), trace_key)
    interpolant = mapping(trace.get("exactInterpolant"), "exact interpolant")
    pull = mapping(interpolant.get("pullTrace"), "interpolant pull trace")
    expected_words = (
        AXIS_COUNT * PRIMITIVE_COUNT * TILE_COUNT * RECORD_WORDS
    )
    filename = pull.get("rawFile")
    if (
        pull.get("schemaVersion") != 1
        or pull.get("rawCapture") is not True
        or pull.get("rawBytes") != expected_words * 4
        or pull.get("tileCount") != TILE_COUNT
        or pull.get("axisCount") != AXIS_COUNT
        or pull.get("primitiveCount") != PRIMITIVE_COUNT
        or pull.get("pullCount") != PULL_COUNT
        or pull.get("componentCount") != COMPONENT_COUNT
        or pull.get("recordWords") != RECORD_WORDS
        or not isinstance(filename, str)
    ):
        raise ValueError(f"sample {sample_index} pull trace differs")
    path = root / filename
    values = np.fromfile(path, dtype="<u4")
    if values.size != expected_words:
        raise ValueError(
            f"{path} has {values.size} pull words; expected {expected_words}"
        )
    return path, values.reshape(
        AXIS_COUNT,
        PRIMITIVE_COUNT,
        TILE_COUNT,
        RECORD_WORDS,
    )


def _pull_records(surface: PullSurface) -> list[PullRecord]:
    result: list[PullRecord] = []
    for axis in range(AXIS_COUNT):
        for primitive in range(PRIMITIVE_COUNT):
            for tile in range(TILE_COUNT):
                words = surface[axis, primitive, tile]
                state = int(words[0])
                if state == SENTINEL:
                    continue
                if state == LOCKED:
                    raise ValueError("pull trace contains an unfinished record")
                x = int(words[1])
                y = int(words[2])
                coordinate = x if axis == 0 else y
                if (
                    not 0 <= x < CAPTURE_SIZE
                    or not 0 <= y < CAPTURE_SIZE
                    or state != y * CAPTURE_SIZE + x
                    or coordinate // 32 != tile
                ):
                    raise ValueError("pull trace record identity differs")
                values = words[3:].reshape(PULL_COUNT, COMPONENT_COUNT)
                if np.any(values & np.uint32(0x7F80_0000) == 0x7F80_0000):
                    raise ValueError("pull trace contains a non-finite word")
                result.append(
                    PullRecord(
                        axis=axis,
                        primitive=primitive,
                        tile=tile,
                        x=x,
                        y=y,
                        values=values,
                    )
                )
    return result


def _rounding_bounds(bits: int) -> tuple[float, float]:
    value = np.asarray([bits], dtype="<u4").view("<f4")[0]
    previous = np.nextafter(value, np.float32(-np.inf))
    following = np.nextafter(value, np.float32(np.inf))
    return (
        (float(previous) + float(value)) / 2.0,
        (float(value) + float(following)) / 2.0,
    )


def _pull_constant_candidates(
    record: PullRecord,
    *,
    channel: int,
    slope_bits: int,
) -> tuple[int, ...]:
    slope = bits_float32(slope_bits)
    positions = [
        record.local_pixel + pull / PULL_COUNT
        for pull in range(PULL_COUNT)
    ]
    targets = [int(value) for value in record.values[:, channel]]
    lower = -math.inf
    upper = math.inf
    for position, target in zip(positions, targets, strict=True):
        value_lower, value_upper = _rounding_bounds(target)
        lower = max(lower, value_lower - position * slope)
        upper = min(upper, value_upper - position * slope)
    if lower > upper:
        return ()

    candidate_bits: set[int] = set()
    for position, target in zip(positions, targets, strict=True):
        residual = bits_float32(target) - position * slope
        candidate_bits.update(neighboring_float32_bits(residual, 8))
    candidate = np.float32(lower)
    if float(candidate) < lower:
        candidate = np.nextafter(candidate, np.float32(np.inf))
    for _ in range(9):
        if float(candidate) > upper:
            break
        candidate_bits.add(arithmetic.float32_bits(float(candidate)))
        candidate = np.nextafter(candidate, np.float32(np.inf))
    candidates: list[int] = []
    for bits in candidate_bits:
        value = bits_float32(bits)
        if not lower <= value <= upper:
            continue
        if all(
            pull_iterator_bits(position, slope, value) == target
            for position, target in zip(positions, targets, strict=True)
        ):
            candidates.append(bits)
    return tuple(sorted(candidates))


def _direct_slope_bits(quad: RuntimeQuad, channel: int) -> int:
    endpoint = quad.endpoints[channel]
    axis = quad.channelAxes[channel]
    extent_fixed = quad.case.widthFixed if axis == 0 else quad.case.heightFixed
    delta = (
        arithmetic.float32_bits_fraction(endpoint.highBits)
        - arithmetic.float32_bits_fraction(endpoint.lowBits)
    )
    return arithmetic.round_fraction_to_float32_bits(
        delta / Fraction(extent_fixed, 256)
    )


def _recover_pull_setup(
    quad: RuntimeQuad,
    records: list[PullRecord],
    *,
    channel: int,
    selector_table: tuple[int, ...],
) -> tuple[
    dict[int, dict[tuple[int, int], tuple[int, ...]]],
    JsonObject,
]:
    axis = quad.channelAxes[channel]
    selected_records = [record for record in records if record.axis == axis]
    model_bits = slopes_bits(quad, selector_table)[channel]
    direct_bits = _direct_slope_bits(quad, channel)
    attempts: list[JsonObject] = []
    accepted: dict[int, dict[tuple[int, int], tuple[int, ...]]] = {}
    for radius in (32, 256, 2048):
        candidates = neighboring_float32_bits(bits_float32(model_bits), radius)
        candidates.update(
            neighboring_float32_bits(bits_float32(direct_bits), radius)
        )
        for slope_bits in candidates:
            if slope_bits in accepted:
                continue
            constants: dict[tuple[int, int], tuple[int, ...]] = {}
            for record in selected_records:
                options = _pull_constant_candidates(
                    record,
                    channel=channel,
                    slope_bits=slope_bits,
                )
                if not options:
                    break
                constants[(record.primitive, record.tile)] = options
            else:
                accepted[slope_bits] = constants
        attempts.append(
            {
                "radius": radius,
                "candidateCount": len(candidates),
                "acceptedSlopeCount": len(accepted),
            }
        )
        if accepted:
            break
    if not accepted:
        raise ValueError(
            f"channel {channel} has no pull slope"
        )
    return accepted, {
        "modelSlopeBits": f"0x{model_bits:08x}",
        "directSlopeBits": f"0x{direct_bits:08x}",
        "recordCount": len(selected_records),
        "acceptedSlopeBits": [
            f"0x{slope_bits:08x}" for slope_bits in sorted(accepted)
        ],
        "attempts": attempts,
    }


def _center_groups(
    reference: UIntImage,
    quad: RuntimeQuad,
    *,
    channel: int,
) -> dict[tuple[int, int], list[tuple[int, int]]]:
    raster_left, raster_bottom, raster_right, raster_top = visible_pixel_bounds(
        quad.case
    )
    left = max(0, raster_left)
    bottom = max(0, raster_bottom)
    right = min(CAPTURE_SIZE, raster_right)
    top = min(CAPTURE_SIZE, raster_top)
    yy, xx = np.indices((top - bottom, right - left), dtype=np.uint32)
    xx += np.uint32(left)
    yy += np.uint32(bottom)
    primitives = primitive_ids(quad, xx, yy)
    values = reference[bottom:top, left:right, channel]
    axis = quad.channelAxes[channel]
    coordinates = xx if axis == 0 else yy
    start = left if axis == 0 else bottom
    end = right if axis == 0 else top
    groups: dict[tuple[int, int], list[tuple[int, int]]] = {}
    for primitive in range(PRIMITIVE_COUNT):
        for coordinate in range(start, end):
            selected = values[
                (primitives == primitive) & (coordinates == coordinate)
            ]
            if not selected.size:
                continue
            if np.any(selected != selected[0]):
                raise ValueError("center trace is not axis-separable")
            tile = coordinate // 32
            groups.setdefault((primitive, tile), []).append(
                (coordinate - tile * 32, int(selected[0]))
            )
    return groups


def _center_constant_selection(
    endpoint: object,
    groups: dict[tuple[int, int], list[tuple[int, int]]],
    constant_options: dict[tuple[int, int], tuple[int, ...]],
    slope: Fraction,
) -> dict[tuple[int, int], tuple[int, ...]] | None:
    selected_constants: dict[tuple[int, int], tuple[int, ...]] = {}
    for key, observations in groups.items():
        options = constant_options.get(key)
        if options is None:
            return None
        matches: list[int] = []
        for constant_bits in options:
            constant = arithmetic.float32_bits_fraction(constant_bits)
            step = iterator.significand_step(
                constant,
                iterator.endpoint_step(endpoint),
            )
            for local_pixel, expected in observations:
                left, right = iterator.quad_center_pair(
                    local_pixel,
                    slope,
                    constant,
                    step,
                )
                if (right if local_pixel & 1 else left) != expected:
                    break
            else:
                matches.append(constant_bits)
        if not matches:
            return None
        selected_constants[key] = tuple(matches)
    return selected_constants


def _recover_center_slope(
    quad: RuntimeQuad,
    groups: dict[tuple[int, int], list[tuple[int, int]]],
    constant_options: dict[tuple[int, int], tuple[int, ...]],
    *,
    channel: int,
    pull_slope_bits: int,
) -> tuple[
    Fraction,
    dict[tuple[int, int], tuple[int, ...]],
    JsonObject,
]:
    endpoint = quad.endpoints[channel]
    axis = quad.channelAxes[channel]
    extent_fixed = quad.case.widthFixed if axis == 0 else quad.case.heightFixed
    delta = (
        arithmetic.float32_bits_fraction(endpoint.highBits)
        - arithmetic.float32_bits_fraction(endpoint.lowBits)
    )
    quotient = delta / Fraction(extent_fixed, 256)
    floor, step, phase = center_lattice.signed_p27_lattice(quotient)
    candidates: dict[Fraction, list[str]] = {}
    for offset in range(-256, 257):
        candidates.setdefault(floor + offset * step, []).append(
            f"signed-p27-floor{offset:+d}"
        )
    for bits in neighboring_float32_bits(bits_float32(pull_slope_bits), 32):
        candidates.setdefault(
            arithmetic.float32_bits_fraction(bits),
            [],
        ).append(f"pull-f32-neighbor-0x{bits:08x}")
    accepted: list[
        tuple[
            Fraction,
            list[str],
            dict[tuple[int, int], tuple[int, ...]],
        ]
    ] = []
    for slope, names in candidates.items():
        selected_constants = _center_constant_selection(
            endpoint,
            groups,
            constant_options,
            slope,
        )
        if selected_constants is not None:
            accepted.append((slope, names, selected_constants))
    if len(accepted) != 1:
        raise ValueError(
            f"channel {channel} has {len(accepted)} center slopes"
        )
    slope, names, constants = accepted[0]
    return slope, constants, {
        "exactQuotient": str(quotient),
        "p27Phase": str(phase),
        "recoveredSlope": str(slope),
        "recoveredSlopeHex": float(slope).hex(),
        "candidateNames": names,
        "candidateCount": len(candidates),
        "groupCount": len(groups),
    }


def _constant_comparison(
    quad: RuntimeQuad,
    constants: dict[tuple[int, int], tuple[int, ...]],
    *,
    channel: int,
    selector_table: tuple[int, ...],
) -> JsonObject:
    mismatches: list[JsonObject] = []
    for (primitive, tile), recovered in sorted(constants.items()):
        predicted = coefficient_bits(
            quad,
            channel=channel,
            primitive=primitive,
            tile=tile,
            selector_table=selector_table,
        )
        if predicted not in recovered:
            mismatches.append(
                {
                    "primitive": primitive,
                    "tile": tile,
                    "predicted": f"0x{predicted:08x}",
                    "recoveredOptions": [
                        f"0x{bits:08x}" for bits in recovered
                    ],
                }
            )
    return {
        "constantCount": len(constants),
        "ambiguousConstantCount": sum(
            len(options) != 1 for options in constants.values()
        ),
        "mismatchedConstants": len(mismatches),
        "exact": not mismatches,
        "examples": mismatches[:32],
    }


def _serialize_constants(
    constants: dict[tuple[int, int], tuple[int, ...]],
) -> list[JsonObject]:
    return [
        {
            "primitive": primitive,
            "tile": tile,
            "options": [f"0x{bits:08x}" for bits in options],
        }
        for (primitive, tile), options in sorted(constants.items())
    ]


def run_recovery(dynamic_root: Path) -> JsonObject:
    reports = _report_paths(dynamic_root)
    if len(reports) != 1:
        raise ValueError(f"expected one dynamic report under {dynamic_root}")
    report_path = reports[0]
    root = report_path.parent
    report = json.loads(report_path.read_text(encoding="utf-8"))
    uniforms = mapping(report.get("dynamicBackgroundUniforms"), "dynamic uniforms")
    untyped_records = uniforms.get("records")
    if not isinstance(untyped_records, list):
        raise ValueError("dynamic records are absent")
    records = [mapping(record, "dynamic record") for record in untyped_records]
    selected = {int(record["sampleIndex"]): record for record in records}
    if tuple(sorted(selected)) != EXPECTED_SAMPLE_INDICES:
        raise ValueError(f"dynamic samples differ: {sorted(selected)}")

    selector_table = arithmetic.load_selector_table()
    samples: JsonObject = {}
    for sample_index in EXPECTED_SAMPLE_INDICES:
        record = selected[sample_index]
        render = mapping(record.get("render"), f"sample {sample_index} render")
        center_path, center_reference, _ = _trace_reference(
            root,
            render,
            sample_index=sample_index,
        )
        pull_path, pull_surface = _pull_reference(
            root,
            render,
            sample_index=sample_index,
        )
        pull_records = _pull_records(pull_surface)
        quad = _sdf_quad(
            render,
            name=f"dynamic-highlight-sample-{sample_index}",
        )
        channels: JsonObject = {}
        for channel in MODELED_CHANNELS:
            pull_setups, pull_report = _recover_pull_setup(
                quad,
                pull_records,
                channel=channel,
                selector_table=selector_table,
            )
            center_groups = _center_groups(
                center_reference,
                quad,
                channel=channel,
            )
            solutions: list[
                tuple[
                    int,
                    dict[tuple[int, int], tuple[int, ...]],
                    JsonObject,
                    dict[tuple[int, int], tuple[int, ...]],
                ]
            ] = []
            for pull_slope_bits, constant_options in pull_setups.items():
                try:
                    _, constants, center_report = _recover_center_slope(
                        quad,
                        center_groups,
                        constant_options,
                        channel=channel,
                        pull_slope_bits=pull_slope_bits,
                    )
                except ValueError:
                    continue
                solutions.append(
                    (
                        pull_slope_bits,
                        constants,
                        center_report,
                        constant_options,
                    )
                )
            if len(solutions) != 1:
                raise ValueError(
                    f"sample {sample_index} channel {channel} has "
                    f"{len(solutions)} joint pull/center setups"
                )
            (
                pull_slope_bits,
                constants,
                center_report,
                constant_options,
            ) = solutions[0]
            model_slope_bits = slopes_bits(quad, selector_table)[channel]
            pull_report["recoveredSlopeBits"] = (
                f"0x{pull_slope_bits:08x}"
            )
            pull_report["modelSlopeExact"] = (
                pull_slope_bits == model_slope_bits
            )
            pull_report["ambiguousConstantRecordCount"] = sum(
                len(options) != 1 for options in constant_options.values()
            )
            channels[str(channel)] = {
                "axis": quad.channelAxes[channel],
                "endpoint": {
                    "lowBits": f"0x{quad.endpoints[channel].lowBits:08x}",
                    "highBits": f"0x{quad.endpoints[channel].highBits:08x}",
                },
                "pull": pull_report,
                "center": center_report,
                "recoveredConstants": _serialize_constants(constants),
                "constantComparison": _constant_comparison(
                    quad,
                    constants,
                    channel=channel,
                    selector_table=selector_table,
                ),
            }
        samples[str(sample_index)] = {
            "remaining": record.get("remaining"),
            "centerTrace": {
                "path": str(center_path),
                "sha256": sha256_file(center_path),
            },
            "pullTrace": {
                "path": str(pull_path),
                "sha256": sha256_file(pull_path),
                "recordCount": len(pull_records),
            },
            "fixedBounds": [
                quad.case.originXFixed,
                quad.case.originYFixed,
                quad.case.originXFixed + quad.case.widthFixed,
                quad.case.originYFixed + quad.case.heightFixed,
            ],
            "channels": channels,
        }

    model_pull_exact = all(
        channel["pull"]["modelSlopeExact"]
        for sample in samples.values()
        for channel in sample["channels"].values()
    )
    model_constants_exact = all(
        channel["constantComparison"]["exact"]
        for sample in samples.values()
        for channel in sample["channels"].values()
    )
    return {
        "liquidGlassDynamicPullRecoverySchemaVersion": 1,
        "dynamicArtifact": str(dynamic_root),
        "scope": {
            "sampleIndices": list(EXPECTED_SAMPLE_INDICES),
            "channels": list(MODELED_CHANNELS),
            "capturedPullsReadByPortablePredictor": False,
        },
        "samples": samples,
        "measurement": {
            "sampleCount": len(samples),
            "channelSetupCount": len(samples) * len(MODELED_CHANNELS),
            "recoveryExact": True,
            "modelPullSlopesExact": model_pull_exact,
            "modelConstantsExact": model_constants_exact,
        },
        "gate": {
            "portableCenterLawEstablished": False,
            "productionShaderAuthorized": False,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dynamic_root", type=Path)
    parser.add_argument("-o", "--output", type=Path)
    arguments = parser.parse_args()
    report = run_recovery(arguments.dynamic_root)
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
