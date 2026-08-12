#!/usr/bin/env python3
"""Validate Apple's direct CASDFGenerator output against an AIR replay."""

import argparse
import hashlib
import json
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray


type JsonObject = dict[str, Any]
type FloatArray = NDArray[np.float32]
type UInt8Array = NDArray[np.uint8]
type UInt16Array = NDArray[np.uint16]
type UInt32Array = NDArray[np.uint32]

EXPECTED_INPUT_SIDE = 256
EXPECTED_RECTANGLE = (64, 48, 192, 208)
EXPECTED_ZERO_DISTANCE = -64.0
EXPECTED_ONE_DISTANCE = 16.0
EXPECTED_MAXIMUM_DISTANCE = 64
EXPECTED_PADDING = 64
EXPECTED_FIELD_PIXELS = 384 * 384
EXPECTED_BLUR_ACTIVE_SIDE = 404
EXPECTED_BLUR_FULL_SIDE = 448
EXPECTED_BLUR_CROP_ORIGIN = 11
NATIVE_BLUR_WEIGHT_BITS = (
    0x322D,
    0x31FD,
    0x2D9F,
    0x26D8,
    0x1D67,
)
NATIVE_BLUR_FMA_ORDER = (0, 3, 2, 1, 4)
BLOCK_BYTES = 1024 * 1024


@dataclass(frozen=True)
class GeneratorCapture:
    name: str
    width: int
    height: int
    bits_per_component: int
    bits_per_pixel: int
    bytes_per_row: int
    raw_file: str
    request_values: JsonObject


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(BLOCK_BYTES):
            digest.update(block)
    return digest.hexdigest()


def jump_schedule(maximum_distance: int) -> tuple[int, ...]:
    if maximum_distance < 1:
        raise ValueError("maximum distance must be positive")
    jump = 1 << (maximum_distance.bit_length() - 1)
    schedule: list[int] = []
    while jump:
        schedule.append(jump)
        jump >>= 1
    return tuple(schedule)


def _read_zero(
    source: NDArray[Any],
    delta_y: int,
    delta_x: int,
) -> NDArray[Any]:
    """Return source[y + delta_y, x + delta_x], with zero out of bounds."""
    height, width = source.shape
    result = np.zeros_like(source)
    output_y0 = max(0, -delta_y)
    output_y1 = min(height, height - delta_y)
    input_y0 = output_y0 + delta_y
    input_y1 = output_y1 + delta_y
    output_x0 = max(0, -delta_x)
    output_x1 = min(width, width - delta_x)
    input_x0 = output_x0 + delta_x
    input_x1 = output_x1 + delta_x
    result[output_y0:output_y1, output_x0:output_x1] = source[
        input_y0:input_y1,
        input_x0:input_x1,
    ]
    return result


def padded_alpha(input_rgba: UInt8Array, padding: int) -> FloatArray:
    if input_rgba.shape != (
        EXPECTED_INPUT_SIDE,
        EXPECTED_INPUT_SIDE,
        4,
    ):
        raise ValueError("direct SDF input dimensions differ")
    alpha = input_rgba[..., 3].astype(np.float32) / np.float32(255)
    return np.pad(
        alpha,
        ((padding, padding), (padding, padding)),
        mode="constant",
    )


def brim_seeds(
    alpha: FloatArray,
    threshold: float = 0.5,
) -> tuple[UInt32Array, UInt32Array]:
    padded = np.pad(alpha, 1, mode="constant")
    neighbor_minimum = np.minimum.reduce((
        padded[1:-1, :-2],
        padded[1:-1, 2:],
        padded[:-2, 1:-1],
        padded[2:, 1:-1],
    ))
    boundary = (
        (alpha > np.float32(threshold))
        & (neighbor_minimum <= np.float32(threshold))
    )
    y, x = np.indices(alpha.shape, dtype=np.uint32)
    winner_x = np.where(boundary, x, 0).astype(np.uint32)
    winner_y = np.where(boundary, y, 0).astype(np.uint32)
    return winner_x, winner_y


def jump_flood(
    alpha: FloatArray,
    maximum_distance: int,
    *,
    cost_dtype: type[np.float32] | type[np.float16] = np.float32,
    threshold: float = 0.5,
) -> tuple[UInt32Array, UInt32Array]:
    """Replay brim_jump's ordered 3x3 passes and x==0 sentinel."""
    height, width = alpha.shape
    y, x = np.indices((height, width), dtype=np.uint32)
    y_float = y.astype(np.float32)
    x_float = x.astype(np.float32)
    inside = alpha > np.float32(threshold)
    winner_x, winner_y = brim_seeds(alpha, threshold)

    for jump in jump_schedule(maximum_distance):
        best_cost = np.full(
            (height, width),
            np.inf,
            dtype=cost_dtype,
        )
        next_x = x.copy()
        next_y = y.copy()
        for offset_x in (-1, 0, 1):
            for offset_y in (-1, 0, 1):
                candidate_x = _read_zero(
                    winner_x,
                    offset_y * jump,
                    offset_x * jump,
                )
                candidate_y = _read_zero(
                    winner_y,
                    offset_y * jump,
                    offset_x * jump,
                )
                # The AIR tests only candidate.x. Coordinate x==0 is reserved
                # as the invalid sentinel even when candidate.y is nonzero.
                valid = candidate_x != 0
                delta_x = x_float - candidate_x.astype(np.float32)
                delta_y = y_float - candidate_y.astype(np.float32)
                distance = np.sqrt(
                    delta_x * delta_x + delta_y * delta_y,
                    dtype=np.float32,
                )
                boundary_alpha = alpha[candidate_y, candidate_x]
                side_cost = np.where(
                    inside,
                    boundary_alpha,
                    np.float32(1) - boundary_alpha,
                )
                candidate_cost = distance + side_cost
                take = valid & (
                    candidate_cost < best_cost.astype(np.float32)
                )
                next_x = np.where(take, candidate_x, next_x)
                next_y = np.where(take, candidate_y, next_y)
                best_cost = np.where(
                    take,
                    candidate_cost.astype(cost_dtype),
                    best_cost,
                )

        invalid = np.isinf(best_cost)
        winner_x = np.where(invalid, 0, next_x).astype(np.uint32)
        winner_y = np.where(invalid, 0, next_y).astype(np.uint32)

    return winner_x, winner_y


def generate_field(
    alpha: FloatArray,
    winner_x: UInt32Array,
    winner_y: UInt32Array,
    *,
    zero_distance: float,
    one_distance: float,
    threshold: float = 0.5,
) -> FloatArray:
    """Replay sdf_gen_field through its final float result."""
    y, x = np.indices(alpha.shape, dtype=np.uint32)
    delta_x = x.astype(np.float32) - winner_x.astype(np.float32)
    delta_y = y.astype(np.float32) - winner_y.astype(np.float32)
    distance = np.sqrt(
        delta_x * delta_x + delta_y * delta_y,
        dtype=np.float32,
    )
    signed_distance = np.where(
        alpha > np.float32(threshold),
        -distance,
        distance,
    )
    boundary_alpha = alpha[winner_y, winner_x]
    scale = np.float32(1 / (one_distance - zero_distance))
    bias = np.float32(-zero_distance) * scale
    return (
        (
            signed_distance
            + np.float32(0.5)
            - boundary_alpha
        )
        * scale
        + bias
    ).astype(np.float32)


def encode_unorm8_via_half(field: FloatArray) -> UInt8Array:
    half_field = field.astype(np.float16).astype(np.float32)
    return np.clip(
        np.rint(half_field * np.float32(255)),
        0,
        255,
    ).astype(np.uint8)


def native_half_fma_blur(
    sample_bits: UInt16Array,
    *,
    weight_bits: tuple[int, ...] = NATIVE_BLUR_WEIGHT_BITS,
    order: tuple[int, ...] = NATIVE_BLUR_FMA_ORDER,
) -> UInt16Array:
    """Replay Apple's rounded-pair, binary16-FMA blur arithmetic."""
    if sample_bits.shape[-1] != 10:
        raise ValueError("native blur trace must contain ten samples")
    if sorted(order) != list(range(5)):
        raise ValueError("native blur FMA order must cover five pairs")
    if len(weight_bits) != 5:
        raise ValueError("native blur must contain five weights")

    samples = sample_bits.view(np.float16).astype(np.float32)
    pairs = (
        samples[..., 0::2] + samples[..., 1::2]
    ).astype(np.float16).astype(np.float32)
    weights = np.asarray(
        weight_bits,
        dtype=np.uint16,
    ).view(np.float16).astype(np.float32)

    first = order[0]
    accumulation = (
        weights[first] * pairs[..., first]
    ).astype(np.float16).astype(np.float32)
    for pair_index in order[1:]:
        # A binary16 product has enough precision for float32 to perform the
        # exact pre-round FMA sum used by these captured operands.
        accumulation = (
            weights[pair_index] * pairs[..., pair_index]
            + accumulation
        ).astype(np.float16).astype(np.float32)
    return accumulation.astype(np.float16).view(np.uint16)


def _scalar(request: JsonObject, key: str) -> float:
    value = request.get(key)
    if not isinstance(value, dict):
        raise ValueError(f"request scalar {key} is missing")
    spelling = value.get("float64")
    if spelling == "+infinity":
        return float("inf")
    if spelling == "-infinity":
        return float("-inf")
    if spelling == "nan":
        return float("nan")
    if not isinstance(spelling, str):
        raise ValueError(f"request scalar {key} differs")
    return float(spelling)


def _capture_from_json(value: Any) -> GeneratorCapture:
    if not isinstance(value, dict):
        raise ValueError("generator capture is not an object")
    request = value.get("requestValues")
    if not isinstance(request, dict):
        raise ValueError("generator request values are missing")
    return GeneratorCapture(
        name=str(value["name"]),
        width=int(value["width"]),
        height=int(value["height"]),
        bits_per_component=int(value["bitsPerComponent"]),
        bits_per_pixel=int(value["bitsPerPixel"]),
        bytes_per_row=int(value["bytesPerRow"]),
        raw_file=str(value["rawFile"]),
        request_values=request,
    )


def _member_ending(archive: zipfile.ZipFile, suffix: str) -> str:
    matches = [
        name
        for name in archive.namelist()
        if name == suffix or name.endswith(f"/{suffix}")
    ]
    if len(matches) != 1:
        raise ValueError(f"archive member {suffix!r} is not unique")
    return matches[0]


def _read_member(archive: zipfile.ZipFile, name: str) -> bytes:
    direct = [member for member in archive.namelist() if member == name]
    if len(direct) == 1:
        return archive.read(direct[0])
    return archive.read(_member_ending(archive, name))


def _metrics(actual: UInt8Array, predicted: UInt8Array) -> JsonObject:
    delta = actual.astype(np.int16) - predicted.astype(np.int16)
    absolute = np.abs(delta)
    exact = int(np.count_nonzero(delta == 0))
    return {
        "pixels": int(delta.size),
        "exactPixels": exact,
        "mismatchedPixels": int(delta.size - exact),
        "exactFraction": float(exact / delta.size),
        "maximumAbsoluteCodeError": int(absolute.max(initial=0)),
        "meanAbsoluteCodeError": float(absolute.mean()),
    }


def _bit_metrics(
    actual: UInt16Array,
    predicted: UInt16Array,
) -> JsonObject:
    if actual.shape != predicted.shape:
        raise ValueError("bit-gate array shapes differ")
    equal = actual == predicted
    exact = int(np.count_nonzero(equal))
    values = int(equal.size)
    return {
        "values": values,
        "exactValues": exact,
        "mismatchedValues": values - exact,
        "exactFraction": float(exact / values),
    }


def _object(value: Any, description: str) -> JsonObject:
    if not isinstance(value, dict):
        raise ValueError(description)
    return value


def _list(value: Any, description: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(description)
    return value


def _stage_file(
    archive: zipfile.ZipFile,
    section: JsonObject,
    *,
    shape: tuple[int, ...],
) -> UInt16Array:
    filename = section.get("outputFile")
    if not isinstance(filename, str):
        raise ValueError("SDF stage output file is missing")
    raw = _read_member(archive, filename)
    expected_bytes = int(np.prod(shape)) * np.dtype("<u2").itemsize
    if len(raw) != expected_bytes:
        raise ValueError(f"SDF stage output {filename} has wrong size")
    return np.frombuffer(raw, dtype="<u2").copy().reshape(shape)


def _snapshot_file(
    archive: zipfile.ZipFile,
    snapshot: JsonObject,
    *,
    shape: tuple[int, ...],
) -> UInt16Array:
    filename = snapshot.get("rawFile")
    if not isinstance(filename, str):
        raise ValueError("SDF texture snapshot is missing")
    raw = _read_member(archive, filename)
    expected_bytes = int(np.prod(shape)) * np.dtype("<u2").itemsize
    if len(raw) != expected_bytes:
        raise ValueError(f"SDF texture {filename} has wrong size")
    return np.frombuffer(raw, dtype="<u2").copy().reshape(shape)


def _one_snapshot(
    snapshots: list[Any],
    *,
    width: int,
    height: int,
    index: int,
    pipeline_token: str,
) -> JsonObject:
    matches = [
        value
        for value in snapshots
        if isinstance(value, dict)
        and value.get("width") == width
        and value.get("height") == height
        and value.get("index") == index
        and pipeline_token
        in str(_object(
            value.get("pipeline"),
            "SDF snapshot pipeline is missing",
        ).get("label", ""))
    ]
    if len(matches) != 1:
        raise ValueError(
            f"SDF snapshot {pipeline_token} {width}x{height} "
            "is not unique",
        )
    return matches[0]


def _latest_snapshot(
    snapshots: list[Any],
    *,
    width: int,
    height: int,
    index: int,
    pixel_format: int,
    expected_count: int,
) -> JsonObject:
    matches = [
        value
        for value in snapshots
        if isinstance(value, dict)
        and value.get("width") == width
        and value.get("height") == height
        and value.get("index") == index
        and value.get("pixelFormat") == pixel_format
    ]
    if len(matches) != expected_count:
        raise ValueError(
            f"SDF pixel-format {pixel_format} snapshot count differs",
        )
    if not all(isinstance(value.get("sequence"), int) for value in matches):
        raise ValueError("SDF snapshot sequence is missing")
    return max(matches, key=lambda value: int(value["sequence"]))


def analyze(artifact: Path) -> JsonObject:
    native_winner_bits: UInt16Array | None = None
    native_base_field_bits: UInt16Array | None = None
    field_half_trace_bits: UInt16Array | None = None
    winner_snapshot_info: JsonObject | None = None

    with zipfile.ZipFile(artifact) as archive:
        bad_member = archive.testzip()
        if bad_member is not None:
            raise ValueError(f"artifact CRC failed for {bad_member}")
        runtime = json.loads(
            archive.read(_member_ending(archive, "runtime.json")),
        )
        if not isinstance(runtime, dict):
            raise ValueError("runtime report is not an object")
        schema = runtime.get("schemaVersion")
        if not isinstance(schema, int) or schema < 17:
            raise ValueError("direct SDF schema is unavailable")
        evidence = runtime.get("sdfGeneratorEvidence")
        if not isinstance(evidence, dict):
            raise ValueError("direct SDF evidence is missing")
        input_record = evidence.get("input")
        if not isinstance(input_record, dict):
            raise ValueError("direct SDF input record is missing")
        input_file = input_record.get("rawFile")
        if not isinstance(input_file, str):
            raise ValueError("direct SDF raw input is missing")
        input_bytes = _read_member(archive, input_file)
        input_rgba = np.frombuffer(
            input_bytes,
            dtype=np.uint8,
        ).copy().reshape(
            EXPECTED_INPUT_SIDE,
            EXPECTED_INPUT_SIDE,
            4,
        )
        expected_input = np.zeros_like(input_rgba)
        x0, y0, x1, y1 = EXPECTED_RECTANGLE
        expected_input[y0:y1, x0:x1] = 255
        if not np.array_equal(input_rgba, expected_input):
            raise ValueError("direct SDF input mask differs")

        capture_values = evidence.get("captures")
        if not isinstance(capture_values, list):
            raise ValueError("direct SDF captures are missing")
        capture_records = {
            str(value.get("name")): value
            for value in capture_values
            if isinstance(value, dict)
        }
        captures = {
            capture.name: capture
            for capture in map(_capture_from_json, capture_values)
        }
        preferred_names = (
            "bounded-depth0-gradient-smoothing3",
            "bounded-minus64-plus16-gradient",
        )
        capture = next(
            (
                captures[name]
                for name in preferred_names
                if name in captures
            ),
            None,
        )
        if capture is None:
            raise ValueError("bounded depth-zero gradient capture is missing")
        if (
            capture.bits_per_component != 8
            or capture.bits_per_pixel != 32
            or capture.width != 384
            or capture.height != 384
            or capture.bytes_per_row != 1536
        ):
            raise ValueError("bounded depth-zero output format differs")
        output = np.frombuffer(
            _read_member(archive, capture.raw_file),
            dtype=np.uint8,
        ).copy().reshape(capture.height, capture.width, 4)

        native_stages: JsonObject = {
            "available": False,
        }
        depth2_name = "bounded-depth2-gradient-smoothing3"
        if schema >= 30 and depth2_name in capture_records:
            depth2_record = _object(
                capture_records[depth2_name],
                "depth-two SDF capture is missing",
            )
            depth2_capture = _capture_from_json(depth2_record)
            if (
                depth2_capture.width != 384
                or depth2_capture.height != 384
                or depth2_capture.bits_per_component != 16
                or depth2_capture.bits_per_pixel != 64
                or depth2_capture.bytes_per_row != 3072
            ):
                raise ValueError("depth-two SDF output format differs")
            depth2_output = np.frombuffer(
                _read_member(archive, depth2_capture.raw_file),
                dtype="<u2",
            ).copy().reshape(384, 384, 4)

            texture_report = _object(
                depth2_record.get("metalTextureSnapshots"),
                "SDF texture snapshots are missing",
            )
            snapshots = _list(
                texture_report.get("snapshots"),
                "SDF texture snapshot list is missing",
            )
            stage = _object(
                texture_report.get("stageTrace"),
                "SDF stage trace is missing",
            )
            stage_schema = stage.get("schemaVersion")
            if stage_schema not in (7, 8):
                raise ValueError("SDF stage-trace schema differs")

            if stage_schema >= 8:
                winner_snapshot = _latest_snapshot(
                    snapshots,
                    width=384,
                    height=384,
                    index=3,
                    pixel_format=63,
                    expected_count=2,
                )
                native_winner_bits = _snapshot_file(
                    archive,
                    winner_snapshot,
                    shape=(384, 384, 2),
                )
                winner_snapshot_info = {
                    "sequence": winner_snapshot["sequence"],
                    "rawFile": winner_snapshot["rawFile"],
                }
                native_base_field_bits = _snapshot_file(
                    archive,
                    _one_snapshot(
                        snapshots,
                        width=384,
                        height=384,
                        index=3,
                        pipeline_token="_Tn19",
                    ),
                    shape=(384, 384, 4),
                )[..., 0]
                field_half_trace_bits = _stage_file(
                    archive,
                    _object(
                        stage.get("fieldHalfTrace"),
                        "SDF field-half trace is missing",
                    ),
                    shape=(384, 384),
                )

            native_horizontal = _snapshot_file(
                archive,
                _one_snapshot(
                    snapshots,
                    width=EXPECTED_BLUR_FULL_SIDE,
                    height=EXPECTED_BLUR_FULL_SIDE,
                    index=3,
                    pipeline_token="_Tn19",
                ),
                shape=(
                    EXPECTED_BLUR_FULL_SIDE,
                    EXPECTED_BLUR_FULL_SIDE,
                    4,
                ),
            )
            native_vertical = _snapshot_file(
                archive,
                _one_snapshot(
                    snapshots,
                    width=EXPECTED_BLUR_FULL_SIDE,
                    height=EXPECTED_BLUR_FULL_SIDE,
                    index=3,
                    pipeline_token="_A2Xghfc",
                ),
                shape=(
                    EXPECTED_BLUR_FULL_SIDE,
                    EXPECTED_BLUR_FULL_SIDE,
                    4,
                ),
            )
            native_final = _snapshot_file(
                archive,
                _one_snapshot(
                    snapshots,
                    width=384,
                    height=384,
                    index=4,
                    pipeline_token="_Tdgg",
                ),
                shape=(384, 384, 4),
            )

            blur_trace = _stage_file(
                archive,
                _object(
                    stage.get("blurTrace"),
                    "SDF blur trace is missing",
                ),
                shape=(
                    EXPECTED_BLUR_ACTIVE_SIDE,
                    EXPECTED_BLUR_ACTIVE_SIDE,
                    24,
                ),
            )
            sample_sections = _object(
                stage.get("blurFragmentSampleTraces"),
                "SDF fragment sample traces are missing",
            )
            position_filename = sample_sections.get(
                "positionCoordinateFile",
            )
            if not isinstance(position_filename, str):
                raise ValueError("position-coordinate trace is missing")
            position_samples = _stage_file(
                archive,
                {"outputFile": position_filename},
                shape=(
                    EXPECTED_BLUR_ACTIVE_SIDE,
                    EXPECTED_BLUR_ACTIVE_SIDE,
                    10,
                ),
            )
            sampled_bits = _bit_metrics(
                blur_trace[..., :10],
                position_samples,
            )
            modeled_horizontal_r = native_half_fma_blur(
                position_samples,
            )
            active = slice(
                1,
                1 + EXPECTED_BLUR_ACTIVE_SIDE,
            )
            modeled_horizontal = _bit_metrics(
                native_horizontal[active, active, 0],
                modeled_horizontal_r,
            )

            horizontal_replay = _stage_file(
                archive,
                _object(
                    stage.get("nativeFMABlurFragmentReplay"),
                    "native-FMA horizontal replay is missing",
                ),
                shape=(
                    EXPECTED_BLUR_ACTIVE_SIDE,
                    EXPECTED_BLUR_ACTIVE_SIDE,
                    4,
                ),
            )
            vertical_replay = _stage_file(
                archive,
                _object(
                    stage.get(
                        "nativeVerticalFMABlurFragmentReplay",
                    ),
                    "native-FMA vertical replay is missing",
                ),
                shape=(
                    EXPECTED_BLUR_ACTIVE_SIDE,
                    EXPECTED_BLUR_ACTIVE_SIDE,
                    4,
                ),
            )
            end_to_end = _object(
                stage.get("endToEndBlurReplay"),
                "end-to-end SDF blur replay is missing",
            )
            if end_to_end.get("nativeIntermediateInputUsed") is not False:
                raise ValueError(
                    "end-to-end blur used a native intermediate",
                )
            end_horizontal = _stage_file(
                archive,
                _object(
                    end_to_end.get("horizontal"),
                    "end-to-end horizontal replay is missing",
                ),
                shape=(
                    EXPECTED_BLUR_FULL_SIDE,
                    EXPECTED_BLUR_FULL_SIDE,
                    4,
                ),
            )
            end_vertical = _stage_file(
                archive,
                _object(
                    end_to_end.get("vertical"),
                    "end-to-end vertical replay is missing",
                ),
                shape=(
                    EXPECTED_BLUR_ACTIVE_SIDE,
                    EXPECTED_BLUR_ACTIVE_SIDE,
                    4,
                ),
            )
            end_final = _stage_file(
                archive,
                _object(
                    end_to_end.get("finalCrop"),
                    "end-to-end final crop is missing",
                ),
                shape=(384, 384, 4),
            )
            gradient_trace = _stage_file(
                archive,
                _object(
                    stage.get("gradientHalfTrace"),
                    "SDF gradient trace is missing",
                ),
                shape=(384, 384, 2),
            )

            vertical_active = native_vertical[active, active]
            stage_metrics = {
                "fragmentSamplesVsCompute":
                    sampled_bits,
                "modeledHorizontalR":
                    modeled_horizontal,
                "independentHorizontalRGBA":
                    _bit_metrics(
                        native_horizontal[active, active],
                        horizontal_replay,
                    ),
                "independentVerticalRGBA":
                    _bit_metrics(
                        vertical_active,
                        vertical_replay,
                    ),
                "endToEndHorizontalRGBA":
                    _bit_metrics(
                        native_horizontal,
                        end_horizontal,
                    ),
                "endToEndVerticalRGBA":
                    _bit_metrics(
                        vertical_active,
                        end_vertical,
                    ),
                "endToEndFinalCropRGBA":
                    _bit_metrics(
                        native_final,
                        end_final,
                    ),
                "gradientGB":
                    _bit_metrics(
                        depth2_output[..., 1:3],
                        gradient_trace,
                    ),
            }
            native_stages = {
                "available": True,
                "capture": depth2_name,
                "blurWeightsHalfBits": [
                    f"{value:04x}"
                    for value in NATIVE_BLUR_WEIGHT_BITS
                ],
                "blurFMAOrder": list(NATIVE_BLUR_FMA_ORDER),
                "metrics": stage_metrics,
                "protectedStageGatePassed": all(
                    metric["mismatchedValues"] == 0
                    for metric in stage_metrics.values()
                ),
            }

    maximum_distance = int(
        _scalar(capture.request_values, "maximumDistance"),
    )
    padding = int(_scalar(capture.request_values, "padding"))
    zero_distance = _scalar(
        capture.request_values,
        "zeroValueDistance",
    )
    one_distance = _scalar(
        capture.request_values,
        "oneValueDistance",
    )
    if (
        maximum_distance != EXPECTED_MAXIMUM_DISTANCE
        or padding != EXPECTED_PADDING
        or zero_distance != EXPECTED_ZERO_DISTANCE
        or one_distance != EXPECTED_ONE_DISTANCE
    ):
        raise ValueError("bounded direct SDF request differs")

    alpha = padded_alpha(input_rgba, padding)
    winner_x, winner_y = jump_flood(
        alpha,
        maximum_distance,
        cost_dtype=np.float32,
    )
    field = generate_field(
        alpha,
        winner_x,
        winner_y,
        zero_distance=zero_distance,
        one_distance=one_distance,
    )
    predicted = encode_unorm8_via_half(field)
    field_metrics = _metrics(output[..., 0], predicted)
    alpha_metrics = _metrics(
        output[..., 3],
        np.full(output.shape[:2], 255, dtype=np.uint8),
    )
    protected_field_gate = (
        field_metrics["mismatchedPixels"] == 0
        and alpha_metrics["mismatchedPixels"] == 0
    )

    if (
        native_winner_bits is not None
        and native_base_field_bits is not None
        and field_half_trace_bits is not None
        and winner_snapshot_info is not None
    ):
        stage_winner_x, stage_winner_y = jump_flood(
            alpha,
            maximum_distance,
            cost_dtype=np.float16,
        )
        modeled_winner_bits = np.stack(
            (stage_winner_x, stage_winner_y),
            axis=-1,
        ).astype(np.uint16)
        modeled_base_field_bits = generate_field(
            alpha,
            stage_winner_x,
            stage_winner_y,
            zero_distance=0,
            one_distance=1,
        ).astype(np.float16).view(np.uint16)
        stage_metrics = _object(
            native_stages.get("metrics"),
            "native SDF stage metrics are missing",
        )
        stage_metrics.update({
            "jumpFloodWinnerRG16Uint": _bit_metrics(
                native_winner_bits,
                modeled_winner_bits,
            ),
            "modeledBaseFieldR": _bit_metrics(
                native_base_field_bits,
                modeled_base_field_bits,
            ),
            "metalFastSqrtFieldTraceR": _bit_metrics(
                native_base_field_bits,
                field_half_trace_bits,
            ),
        })
        native_stages["winnerSnapshot"] = winner_snapshot_info
        native_stages["jumpFloodCostStorage"] = "binary16"
        native_stages["protectedStageGatePassed"] = all(
            metric["mismatchedValues"] == 0
            for metric in stage_metrics.values()
        )

    protected_sdf_generator_gate = (
        protected_field_gate
        and native_stages.get("protectedStageGatePassed") is True
    )

    seed_x, _ = brim_seeds(alpha)
    return {
        "schemaVersion": 3,
        "artifact": str(artifact),
        "artifactSha256": sha256_file(artifact),
        "runtimeSchemaVersion": schema,
        "osVersion": runtime.get("osVersion"),
        "capture": capture.name,
        "request": {
            "maximumDistance": maximum_distance,
            "padding": padding,
            "zeroValueDistance": zero_distance,
            "oneValueDistance": one_distance,
            "jumpSchedule": list(jump_schedule(maximum_distance)),
        },
        "airReplay": {
            "jumpFloodCostStorage": "float32",
            "brimSeedPixels": int(np.count_nonzero(seed_x)),
            "validWinnerPixels": int(np.count_nonzero(winner_x)),
            "zeroSentinelPixels": int(np.count_nonzero(winner_x == 0)),
            "fieldR8": field_metrics,
            "alphaR8": alpha_metrics,
            "protectedFieldGatePassed": protected_field_gate,
            "protectedSDFGeneratorGatePassed":
                protected_sdf_generator_gate,
        },
        "nativeStageReplay": native_stages,
        "remaining": {
            "glassCompositor": (
                "source pyramid and final color/compositing path remain "
                "outside this SDF generator gate"
            ),
        },
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__,
    )
    parser.add_argument("artifact", type=Path)
    parser.add_argument("--report", type=Path)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    report = analyze(args.artifact)
    serialized = json.dumps(
        report,
        indent=2,
        sort_keys=True,
    ) + "\n"
    if args.report is not None:
        args.report.write_text(serialized, encoding="utf-8")
    print(serialized, end="")
    return (
        0
        if report["airReplay"]["protectedSDFGeneratorGatePassed"]
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
