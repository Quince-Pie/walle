#!/usr/bin/env python3
"""Identify and validate Apple's live Liquid Glass backdrop pyramid."""

import argparse
import hashlib
import json
import platform
import resource
import struct
import time
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray


type JsonObject = dict[str, Any]
type HalfArray = NDArray[np.float16]
type UInt8Array = NDArray[np.uint8]

SOURCE_SIDE = 448
DESTINATION_SIDE = 224
CHANNELS = 4
KERNEL_RADIUS = 6
BASE_SOURCE_SIDE = 1024
BASE_TARGET_SIDE = 448
BASE_SOURCE_X = 114
BASE_SOURCE_Y = 112
BASE_ACTIVE_WIDTH = 400
BASE_ACTIVE_HEIGHT = 401
BASE_COPY_X = -5
BASE_COPY_Y = -4
BASE_COPY_CLAMP = (0, 0, 398, 399)
REGULAR_DOWNSAMPLE_FACTOR = 4
REGULAR_DOWNSAMPLE_WEIGHT = np.float16(0.25)

WEIGHT_BITS = np.asarray(
    (0x2B36, 0x2CEF, 0x2DC6, 0x2EC0),
    dtype=np.uint16,
)
WEIGHTS = WEIGHT_BITS.view(np.float16)

OUTER_TAPS = (
    (0, -4),
    (-4, 0),
    (0, 4),
    (4, 0),
)
DIAGONAL_TAPS = (
    (-2, 2),
    (-2, -2),
    (2, -2),
    (2, 2),
)
INNER_TAPS = (
    (0, -2),
    (-2, 0),
    (0, 2),
    (2, 0),
)
CENTER_TAPS = ((0, 0),)
TAP_GROUPS = (
    OUTER_TAPS,
    DIAGONAL_TAPS,
    INNER_TAPS,
    CENTER_TAPS,
)

# These are the literal load/add orders in the AGX2 AIR. The first two
# operands in each group commute, but retaining the disassembly order makes
# the evidence auditable. Schema 65 proves this schedule, including the three
# final fused multiply-adds, against every native RGBA16Float output word.
AGX2_GROUP_ORDERS = (
    (1, 0, 3, 2),
    (2, 1, 0, 3),
    (1, 0, 3, 2),
)
COPY_BASE_QUARTER = np.float16(0.25)
COPY_BASE_DENORM_LIMIT = np.asarray(
    (0x068E,),
    dtype=np.uint16,
).view(np.float16)[0]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def half_round(value: NDArray[Any]) -> HalfArray:
    return np.asarray(value, dtype=np.float64).astype(np.float16)


def half_add(left: HalfArray, right: HalfArray) -> HalfArray:
    return half_round(
        left.astype(np.float64) + right.astype(np.float64)
    )


def half_multiply(
    left: HalfArray,
    right: np.float16,
) -> HalfArray:
    return half_round(
        left.astype(np.float64) * np.float64(right)
    )


def half_fma(
    left: HalfArray,
    right: np.float16,
    addend: HalfArray,
) -> HalfArray:
    return half_round(
        left.astype(np.float64) * np.float64(right)
        + addend.astype(np.float64)
    )


def unorm8(value: NDArray[Any]) -> UInt8Array:
    return np.clip(
        np.rint(np.asarray(value, dtype=np.float64) * 255),
        0,
        255,
    ).astype(np.uint8)


def replay_base_producer_software(
    source_rgba: UInt8Array,
    *,
    destination_width: int = BASE_TARGET_SIDE,
    destination_height: int = BASE_TARGET_SIDE,
    source_x: int = BASE_SOURCE_X,
    source_y: int = BASE_SOURCE_Y,
    active_width: int = BASE_ACTIVE_WIDTH,
    active_height: int = BASE_ACTIVE_HEIGHT,
) -> UInt8Array:
    if source_rgba.ndim != 3 or source_rgba.shape[2] != CHANNELS:
        raise ValueError("base source must be an RGBA image")
    if source_rgba.dtype != np.uint8:
        raise ValueError("base source must use uint8 codes")
    if (
        destination_width <= 0
        or destination_height <= 0
        or active_width <= 0
        or active_height <= 0
        or active_width > destination_width
        or active_height > destination_height
    ):
        raise ValueError("invalid base producer geometry")
    source_right = source_x + 2 * active_width
    source_bottom = source_y + 2 * active_height
    if (
        source_x < 0
        or source_y < 0
        or source_right > source_rgba.shape[1]
        or source_bottom > source_rgba.shape[0]
    ):
        raise ValueError("base producer footprint exceeds source")

    # CGImage bytes are RGBA with top-left row order. The captured Metal
    # render target is BGRA with bottom-left texture coordinates.
    source_bgra = source_rgba[::-1, :, :][..., (2, 1, 0, 3)]
    top_left = source_bgra[
        source_y:source_bottom:2,
        source_x:source_right:2,
    ]
    top_right = source_bgra[
        source_y:source_bottom:2,
        source_x + 1:source_right + 1:2,
    ]
    bottom_left = source_bgra[
        source_y + 1:source_bottom + 1:2,
        source_x:source_right:2,
    ]
    bottom_right = source_bgra[
        source_y + 1:source_bottom + 1:2,
        source_x + 1:source_right + 1:2,
    ]
    code_sum = (
        top_left.astype(np.uint16)
        + top_right.astype(np.uint16)
        + bottom_left.astype(np.uint16)
        + bottom_right.astype(np.uint16)
    )
    sampled_half = half_round(
        code_sum.astype(np.float64) * (0.25 / 255)
    )
    output = np.zeros(
        (destination_height, destination_width, CHANNELS),
        dtype=np.uint8,
    )
    output[:active_height, :active_width] = unorm8(sampled_half)
    return output


def replay_regular_base_producer_software(
    source_rgba: UInt8Array,
) -> UInt8Array:
    if (
        source_rgba.ndim != 3
        or source_rgba.shape[2] != CHANNELS
        or source_rgba.dtype != np.uint8
        or source_rgba.shape[0] % REGULAR_DOWNSAMPLE_FACTOR
        != 0
        or source_rgba.shape[1] % REGULAR_DOWNSAMPLE_FACTOR
        != 0
    ):
        raise ValueError(
            "regular base source must be a uint8 RGBA image "
            "with dimensions divisible by four"
        )

    # The source snapshot is the CGImage's top-left RGBA byte order. The
    # downsample render pass consumes a bottom-left BGRA texture.
    source = source_rgba[::-1, :, :][..., (2, 1, 0, 3)]

    def quadrant(offset_y: int, offset_x: int) -> HalfArray:
        code_sum = sum(
            (
                source[
                    offset_y + delta_y
                    ::REGULAR_DOWNSAMPLE_FACTOR,
                    offset_x + delta_x
                    ::REGULAR_DOWNSAMPLE_FACTOR,
                ].astype(np.uint16)
                for delta_y in (0, 1)
                for delta_x in (0, 1)
            ),
            start=np.zeros(
                (
                    source.shape[0]
                    // REGULAR_DOWNSAMPLE_FACTOR,
                    source.shape[1]
                    // REGULAR_DOWNSAMPLE_FACTOR,
                    CHANNELS,
                ),
                dtype=np.uint16,
            ),
        )
        # Apple's measured RGBA8 sampler first rounds its code-domain
        # bilinear result to 1/16 code and then converts to binary16. At the
        # exact half phase used here the four-code mean is already on that
        # grid.
        return half_round(
            code_sum.astype(np.float64) * (0.25 / 255)
        )

    # AIR order is (+y,-x), (+y,+x), (-y,-x), (-y,+x). Raw texture row zero
    # is the negative-y pair, so preserving the order below is essential:
    # changing it produces thousands of one-code errors after half FMAs.
    samples = (
        quadrant(2, 0),
        quadrant(2, 2),
        quadrant(0, 0),
        quadrant(0, 2),
    )
    result = np.zeros_like(samples[0])
    for sample in samples:
        result = half_fma(
            sample,
            REGULAR_DOWNSAMPLE_WEIGHT,
            result,
        )
    return unorm8(result)


def replay_live_copy_base_software(
    source: UInt8Array,
    *,
    destination_width: int = BASE_TARGET_SIDE,
    destination_height: int = BASE_TARGET_SIDE,
    base_x: int = BASE_COPY_X,
    base_y: int = BASE_COPY_Y,
    clamp: tuple[int, int, int, int] = BASE_COPY_CLAMP,
) -> UInt8Array:
    if source.ndim != 3 or source.shape[2] != CHANNELS:
        raise ValueError("copy-base source must be an RGBA image")
    clamp_x_min, clamp_y_min, clamp_x_max, clamp_y_max = clamp
    if (
        destination_width <= 0
        or destination_height <= 0
        or clamp_x_min < 0
        or clamp_y_min < 0
        or clamp_x_max < clamp_x_min
        or clamp_y_max < clamp_y_min
        or clamp_x_max >= source.shape[1]
        or clamp_y_max >= source.shape[0]
    ):
        raise ValueError("invalid copy-base geometry")
    source_x = np.clip(
        np.arange(destination_width) + base_x,
        clamp_x_min,
        clamp_x_max,
    )
    source_y = np.clip(
        np.arange(destination_height) + base_y,
        clamp_y_min,
        clamp_y_max,
    )
    return source[source_y[:, None], source_x[None, :]].copy()


def read_texture(
    path: Path,
    *,
    width: int,
    height: int,
) -> UInt8Array:
    values = np.fromfile(path, dtype=np.uint8)
    expected = width * height * CHANNELS
    if values.size != expected:
        raise ValueError(
            f"{path} has {values.size} bytes; expected {expected}"
        )
    return values.reshape(height, width, CHANNELS)


def comparison(
    predicted: UInt8Array,
    measured: UInt8Array,
) -> JsonObject:
    if predicted.shape != measured.shape:
        raise ValueError(
            f"comparison shapes differ: "
            f"{predicted.shape} != {measured.shape}"
        )
    delta = (
        predicted.astype(np.int16)
        - measured.astype(np.int16)
    )
    changed = delta != 0
    return {
        "exact": not bool(np.any(changed)),
        "observedBytes": int(delta.size),
        "mismatchedBytes": int(np.count_nonzero(changed)),
        "mismatchedPixels": int(
            np.count_nonzero(np.any(changed, axis=2))
        ),
        "maximumCodeDelta": int(
            np.abs(delta).max(initial=0)
        ),
        "meanAbsoluteCodeDelta": float(
            np.mean(np.abs(delta))
        ),
        "meanSignedCodeDelta": float(np.mean(delta)),
        "mismatchedBytesByChannel": [
            int(np.count_nonzero(changed[..., channel]))
            for channel in range(CHANNELS)
        ],
    }


def _half_phase_sample(
    source: UInt8Array,
    *,
    offset_x: int,
    offset_y: int,
) -> HalfArray:
    output_height = source.shape[0] // 2
    output_width = source.shape[1] // 2
    output_y, output_x = np.mgrid[
        :output_height,
        :output_width,
    ]
    base_x = 2 * output_x + offset_x
    base_y = 2 * output_y + offset_y
    x_zero = np.clip(
        base_x,
        0,
        source.shape[1] - 1,
    )
    y_zero = np.clip(
        base_y,
        0,
        source.shape[0] - 1,
    )
    # Clamp both unbounded bilinear endpoints independently. Advancing an
    # already-clamped lower endpoint would incorrectly pull texel one into
    # negative-coordinate samples instead of extending texel zero.
    x_one = np.clip(base_x + 1, 0, source.shape[1] - 1)
    y_one = np.clip(base_y + 1, 0, source.shape[0] - 1)
    exact_codes = (
        source[y_zero, x_zero].astype(np.float64)
        + source[y_zero, x_one].astype(np.float64)
        + source[y_one, x_zero].astype(np.float64)
        + source[y_one, x_one].astype(np.float64)
    ) / 4
    # A half-phase average has quarter-code resolution, which is already
    # representable by Apple's measured 1/16-code sampler accumulator.
    fixed_codes = np.floor(exact_codes * 16 + 0.5) / 16
    return half_round(fixed_codes / 255)


def _ordered_half_sum(
    values: tuple[HalfArray, ...],
    order: tuple[int, ...],
) -> HalfArray:
    result = values[order[0]]
    for index in order[1:]:
        result = half_add(result, values[index])
    return result


def _validate_downsample_source(source: UInt8Array) -> None:
    if (
        source.ndim != 3
        or source.shape[2] != CHANNELS
        or source.dtype != np.uint8
        or source.shape[0] < 2
        or source.shape[1] < 2
        or source.shape[0] % 2 != 0
        or source.shape[1] % 2 != 0
    ):
        raise ValueError(
            "downsample source must be an even-sized uint8 RGBA image"
        )


def replay_agx2_software(source: UInt8Array) -> HalfArray:
    _validate_downsample_source(source)
    samples = {
        tap: _half_phase_sample(
            source,
            offset_x=tap[0],
            offset_y=tap[1],
        )
        for group in TAP_GROUPS
        for tap in group
    }
    groups: list[HalfArray] = []
    for taps, order in zip(
        TAP_GROUPS[:3],
        AGX2_GROUP_ORDERS,
        strict=True,
    ):
        groups.append(
            _ordered_half_sum(
                tuple(samples[tap] for tap in taps),
                order,
            )
        )
    center = samples[(0, 0)]
    result = half_multiply(center, WEIGHTS[3])
    result = half_fma(groups[1], WEIGHTS[1], result)
    result = half_fma(groups[2], WEIGHTS[2], result)
    result = half_fma(groups[0], WEIGHTS[0], result)
    return result


def _copy_base_half_phase_sample(
    source_half: HalfArray,
    *,
    offset_x: int,
    offset_y: int,
) -> HalfArray:
    output_height = source_half.shape[0] // 2
    output_width = source_half.shape[1] // 2
    output_y, output_x = np.mgrid[
        :output_height,
        :output_width,
    ]
    base_x = 2 * output_x + offset_x
    base_y = 2 * output_y + offset_y
    x_zero = np.clip(
        base_x,
        0,
        source_half.shape[1] - 1,
    )
    y_zero = np.clip(
        base_y,
        0,
        source_half.shape[0] - 1,
    )
    x_one = np.clip(
        base_x + 1,
        0,
        source_half.shape[1] - 1,
    )
    y_one = np.clip(
        base_y + 1,
        0,
        source_half.shape[0] - 1,
    )
    top_left = source_half[y_zero, x_zero]
    top_right = source_half[y_zero, x_one]
    bottom_left = source_half[y_one, x_zero]
    bottom_right = source_half[y_one, x_one]
    summed = half_add(top_right, top_left)
    summed = half_add(summed, bottom_left)
    summed = half_add(summed, bottom_right)
    return half_multiply(summed, COPY_BASE_QUARTER)


def replay_copy_base_mip_software(
    source: UInt8Array,
) -> HalfArray:
    _validate_downsample_source(source)
    source_half = half_round(
        source.astype(np.float64) / 255
    )
    samples = {
        tap: _copy_base_half_phase_sample(
            source_half,
            offset_x=tap[0],
            offset_y=tap[1],
        )
        for group in TAP_GROUPS
        for tap in group
    }
    groups: list[HalfArray] = []
    for taps, order in zip(
        TAP_GROUPS[:3],
        AGX2_GROUP_ORDERS,
        strict=True,
    ):
        groups.append(
            _ordered_half_sum(
                tuple(samples[tap] for tap in taps),
                order,
            )
        )
    result = half_multiply(
        samples[(0, 0)],
        WEIGHTS[3],
    )
    result = half_fma(groups[1], WEIGHTS[1], result)
    result = half_fma(groups[2], WEIGHTS[2], result)
    result = half_fma(groups[0], WEIGHTS[0], result)
    result = result.copy()
    rgb = result[..., :3]
    result[..., :3] = np.where(
        np.abs(rgb) < COPY_BASE_DENORM_LIMIT,
        np.float16(0),
        rgb,
    )
    return result


def _split_mask(*, holdout: bool) -> NDArray[np.bool_]:
    y, x = np.mgrid[:DESTINATION_SIDE, :DESTINATION_SIDE]
    interior = (
        (x >= 4)
        & (x < DESTINATION_SIDE - 4)
        & (y >= 4)
        & (y < DESTINATION_SIDE - 4)
    )
    parity = ((x + y) & 1).astype(np.bool_)
    return interior & (parity if holdout else ~parity)


def _continuous_metrics(
    predicted: NDArray[np.float64],
    measured: NDArray[np.float64],
) -> JsonObject:
    delta = predicted - measured
    return {
        "values": int(delta.size),
        "meanAbsoluteCodes": float(np.mean(np.abs(delta))),
        "maximumAbsoluteCodes": float(
            np.abs(delta).max(initial=0)
        ),
        "rootMeanSquareCodes": float(
            np.sqrt(np.mean(np.square(delta)))
        ),
    }


def _affine_least_squares(
    design: NDArray[np.float64],
    target: NDArray[np.float64],
) -> NDArray[np.float64]:
    """Solve a small affine fit without an SVD-sized workspace."""
    feature_count = design.shape[1]
    normal = np.empty(
        (feature_count + 1, feature_count + 1),
        dtype=np.float64,
    )
    normal[:-1, :-1] = design.T @ design
    feature_sums = np.sum(design, axis=0)
    normal[:-1, -1] = feature_sums
    normal[-1, :-1] = feature_sums
    normal[-1, -1] = design.shape[0]
    right = np.empty(feature_count + 1, dtype=np.float64)
    right[:-1] = design.T @ target
    right[-1] = np.sum(target)
    return np.linalg.solve(normal, right)


def _unconstrained_kernel_fit(
    source: UInt8Array,
    target: UInt8Array,
) -> JsonObject:
    offsets = np.arange(
        -KERNEL_RADIUS,
        KERNEL_RADIUS + 1,
    )
    output_y, output_x = np.mgrid[
        :DESTINATION_SIDE,
        :DESTINATION_SIDE,
    ]
    train = _split_mask(holdout=False)
    holdout = _split_mask(holdout=True)

    def design(mask: NDArray[np.bool_]) -> NDArray[np.float64]:
        y = output_y[mask]
        x = output_x[mask]
        patches = source[
            (2 * y)[:, None, None] + offsets[None, :, None],
            (2 * x)[:, None, None] + offsets[None, None, :],
            :3,
        ]
        return np.moveaxis(
            patches,
            -1,
            1,
        ).reshape(
            -1,
            offsets.size**2,
        ).astype(np.float64)

    train_design = design(train)
    train_target = target[train, :3].reshape(-1).astype(
        np.float64
    )
    solution = _affine_least_squares(
        train_design,
        train_target,
    )
    coefficients = solution[:-1]
    bias = float(solution[-1])
    train_prediction = train_design @ coefficients + bias

    holdout_design = design(holdout)
    holdout_target = target[holdout, :3].reshape(-1).astype(
        np.float64
    )
    holdout_prediction = holdout_design @ coefficients + bias
    kernel = coefficients.reshape(
        offsets.size,
        offsets.size,
    )
    significant = []
    for row, column in np.argwhere(np.abs(kernel) >= 0.005):
        significant.append({
            "offsetX": int(offsets[column]),
            "offsetY": int(offsets[row]),
            "coefficient": float(kernel[row, column]),
        })
    return {
        "radius": KERNEL_RADIUS,
        "coordinateBase": "source texel (2*x, 2*y)",
        "preregisteredSplit":
            "interior checkerboard; even parity train, odd parity holdout",
        "coefficientSum": float(np.sum(coefficients)),
        "biasCodes": bias,
        "coefficientMatrix": kernel.tolist(),
        "significantCoefficients": significant,
        "train": _continuous_metrics(
            train_prediction,
            train_target,
        ),
        "holdout": _continuous_metrics(
            holdout_prediction,
            holdout_target,
        ),
    }


def _structured_kernel_fit(
    source: UInt8Array,
    target: UInt8Array,
) -> JsonObject:
    samples = {
        tap: (
            _half_phase_sample(
                source,
                offset_x=tap[0],
                offset_y=tap[1],
            ).astype(np.float64)
            * 255
        )
        for group in TAP_GROUPS
        for tap in group
    }
    group_codes = np.stack(
        [
            sum(
                (
                    samples[tap]
                    for tap in group
                ),
                start=np.zeros_like(samples[(0, 0)]),
            )
            for group in TAP_GROUPS
        ],
        axis=-1,
    )
    train = _split_mask(holdout=False)
    holdout = _split_mask(holdout=True)

    train_design = group_codes[train, :3].reshape(-1, 4)
    train_target = target[train, :3].reshape(-1).astype(
        np.float64
    )
    solution = _affine_least_squares(
        train_design,
        train_target,
    )
    fitted_weights = solution[:-1]
    bias = float(solution[-1])

    def evaluate(
        mask: NDArray[np.bool_],
        weights: NDArray[np.float64],
        current_bias: float,
    ) -> JsonObject:
        design = group_codes[mask, :3].reshape(-1, 4)
        measured = target[mask, :3].reshape(-1).astype(
            np.float64
        )
        predicted = design @ weights + current_bias
        rounded = np.clip(
            np.rint(predicted),
            0,
            255,
        ).astype(np.uint8)
        return {
            "continuous": _continuous_metrics(
                predicted,
                measured,
            ),
            "roundedMismatchedValues": int(
                np.count_nonzero(
                    rounded != measured.astype(np.uint8)
                )
            ),
            "roundedValues": int(measured.size),
        }

    fixed_weights = WEIGHTS.astype(np.float64)
    return {
        "groupOrder": [
            "outer axial",
            "diagonal",
            "inner axial",
            "center",
        ],
        "weightBinary16Bits": [
            f"{int(value):04x}"
            for value in WEIGHT_BITS
        ],
        "weightValues": fixed_weights.tolist(),
        "normalization": float(
            fixed_weights[3]
            + 4 * np.sum(fixed_weights[:3])
        ),
        "fittedWeights": fitted_weights.tolist(),
        "fittedBiasCodes": bias,
        "train": {
            "fitted": evaluate(
                train,
                fitted_weights,
                bias,
            ),
            "embeddedBinary16": evaluate(
                train,
                fixed_weights,
                0,
            ),
        },
        "holdout": {
            "fitted": evaluate(
                holdout,
                fitted_weights,
                bias,
            ),
            "embeddedBinary16": evaluate(
                holdout,
                fixed_weights,
                0,
            ),
        },
    }


def _hash_mapping(source: UInt8Array) -> JsonObject:
    coordinate_y, coordinate_x = np.mgrid[:512, :512]
    hashes = (
        coordinate_x.astype(np.uint32) * np.uint32(0x045D9F3B)
        ^ coordinate_y.astype(np.uint32) * np.uint32(0x119DE1F3)
    )
    codes = (hashes & np.uint32(0x00FF_FFFF)).reshape(-1)
    order = np.argsort(codes)
    sorted_codes = codes[order]

    raw_codes = (
        source[..., 2].astype(np.uint32)
        | source[..., 1].astype(np.uint32) << np.uint32(8)
        | source[..., 0].astype(np.uint32) << np.uint32(16)
    )
    flattened = raw_codes.reshape(-1)
    left = np.searchsorted(sorted_codes, flattened, side="left")
    right = np.searchsorted(sorted_codes, flattened, side="right")
    unique = right == left + 1
    matched_indices = order[
        np.minimum(left, order.size - 1)
    ]
    matched_x = matched_indices % 512
    matched_y = matched_indices // 512
    raw_y, raw_x = np.mgrid[:SOURCE_SIDE, :SOURCE_SIDE]
    delta_x = (
        matched_x.astype(np.int32) - raw_x.reshape(-1)
    )[unique]
    reflected_y = (
        matched_y.astype(np.int32) + raw_y.reshape(-1)
    )[unique]

    def mode(values: NDArray[np.int32]) -> tuple[int, int]:
        unique_values, counts = np.unique(
            values,
            return_counts=True,
        )
        index = int(np.argmax(counts))
        return int(unique_values[index]), int(counts[index])

    offset_x, offset_votes = mode(delta_x)
    reflected_origin_y, origin_votes = mode(reflected_y)
    expected_x = raw_x + offset_x
    expected_y = reflected_origin_y - raw_y
    expected_hash = (
        expected_x.astype(np.uint32) * np.uint32(0x045D9F3B)
        ^ expected_y.astype(np.uint32) * np.uint32(0x119DE1F3)
    )
    expected = np.stack(
        (
            (expected_hash >> np.uint32(16)) & np.uint32(255),
            (expected_hash >> np.uint32(8)) & np.uint32(255),
            expected_hash & np.uint32(255),
        ),
        axis=-1,
    ).astype(np.uint8)
    exact = np.all(expected == source[..., :3], axis=2)
    exact_y, exact_x = np.where(exact)
    bounding_box: JsonObject | None = None
    if exact_x.size != 0:
        bounding_box = {
            "minimumX": int(exact_x.min()),
            "maximumX": int(exact_x.max()),
            "minimumY": int(exact_y.min()),
            "maximumY": int(exact_y.max()),
        }
    return {
        "rawChannelOrder": "BGRA",
        "sourceCoordinateX": f"rawX + {offset_x}",
        "sourceCoordinateY":
            f"{reflected_origin_y} - rawY",
        "uniqueCodeObservations": int(np.count_nonzero(unique)),
        "offsetXVotes": offset_votes,
        "reflectedOriginYVotes": origin_votes,
        "exactPixels": int(np.count_nonzero(exact)),
        "observedPixels": int(exact.size),
        "exactFraction": float(np.mean(exact)),
        "exactBoundingBox": bounding_box,
    }


def _native_half_comparison(
    artifact: Path,
    candidate: JsonObject,
    software: HalfArray,
) -> JsonObject | None:
    trace = candidate.get("halfTrace")
    if not isinstance(trace, dict) or trace.get("executed") is not True:
        return None
    path = artifact / str(trace["outputFile"])
    native = np.fromfile(path, dtype=np.uint16)
    expected = DESTINATION_SIDE * DESTINATION_SIDE * CHANNELS
    if native.size != expected:
        raise ValueError(
            f"{path} has {native.size} words; expected {expected}"
        )
    native = native.reshape(
        DESTINATION_SIDE,
        DESTINATION_SIDE,
        CHANNELS,
    )
    # BGRA8 source samples are logically returned as RGBA by Metal, while
    # the raw normalized source/replay arrays retain memory channel order.
    software_rgba = software[..., (2, 1, 0, 3)].view(np.uint16)
    changed = native != software_rgba
    distance = np.abs(
        native.astype(np.int32)
        - software_rgba.astype(np.int32)
    )
    return {
        "nativeFile": str(path),
        "nativeSha256": sha256_file(path),
        "observedWords": int(native.size),
        "mismatchedWords": int(np.count_nonzero(changed)),
        "mismatchedPixels": int(
            np.count_nonzero(np.any(changed, axis=2))
        ),
        "maximumBinary16BitDistance": int(
            distance.max(initial=0)
        ),
        "exact": not bool(np.any(changed)),
    }


def _raw_candidate_comparison(
    artifact: Path,
    candidate: JsonObject,
    target: UInt8Array,
) -> JsonObject | None:
    if candidate.get("executed") is not True:
        return None
    filename = candidate.get("outputFile")
    if not isinstance(filename, str) or not filename:
        return None
    path = artifact / filename
    output = read_texture(
        path,
        width=DESTINATION_SIDE,
        height=DESTINATION_SIDE,
    )
    return {
        "file": str(path),
        "sha256": sha256_file(path),
        **comparison(output, target),
    }


def _live_copy_base_provenance(
    runtime: JsonObject,
) -> JsonObject:
    evidence = runtime.get("carendererEvidence", {})
    provenance = evidence.get("metalCommandProvenance", {})
    records = provenance.get("records", [])
    if not isinstance(records, list):
        return {
            "observed": False,
            "reason": "Metal command provenance is unavailable",
        }
    pipeline_record = next(
        (
            record
            for record in records
            if isinstance(record, dict)
            and record.get("kind") == "computePipeline"
            and record.get("pipeline", {}).get("label")
            == (
                "com.apple.coreanimation."
                "variable_blur_copy_base_mip_compute"
            )
        ),
        None,
    )
    if not isinstance(pipeline_record, dict):
        return {
            "observed": False,
            "reason": "copy-base compute pipeline is unavailable",
        }
    encoder = pipeline_record.get("encoder")
    sequence = int(pipeline_record.get("sequence", -1))
    next_pipeline_sequence = min(
        (
            int(record["sequence"])
            for record in records
            if isinstance(record, dict)
            and record.get("kind") == "computePipeline"
            and record.get("encoder") == encoder
            and int(record.get("sequence", -1)) > sequence
        ),
        default=2**63 - 1,
    )
    commands = [
        record
        for record in records
        if isinstance(record, dict)
        and record.get("encoder") == encoder
        and sequence <= int(record.get("sequence", -1))
        < next_pipeline_sequence
    ]
    textures = {
        int(record["index"]): record.get("texture", {})
        for record in commands
        if record.get("kind") == "texture"
        and isinstance(record.get("index"), int)
    }
    buffer_record = next(
        (
            record
            for record in commands
            if record.get("kind") == "buffer"
            and record.get("stage") == "compute"
            and record.get("index") == 0
        ),
        None,
    )
    dispatch = next(
        (
            record
            for record in commands
            if record.get("kind") == "dispatchThreadgroups"
        ),
        None,
    )
    imageblock = next(
        (
            record
            for record in commands
            if record.get("kind") == "imageblockSize"
        ),
        None,
    )
    if not isinstance(buffer_record, dict):
        return {
            "observed": False,
            "reason": "copy-base uniform buffer is unavailable",
        }
    payload = buffer_record.get("payload", {})
    payload_hex = payload.get("hex")
    if not isinstance(payload_hex, str):
        return {
            "observed": False,
            "reason": "copy-base uniform bytes are unavailable",
        }
    payload_bytes = bytes.fromhex(payload_hex)
    if len(payload_bytes) < 32:
        return {
            "observed": False,
            "reason": "copy-base uniform payload is truncated",
        }
    uniforms = {
        "texCoordBase":
            list(struct.unpack_from("<2h", payload_bytes, 0)),
        "texCoordClamp":
            list(struct.unpack_from("<4h", payload_bytes, 8)),
        "destinationMipZeroSize":
            list(struct.unpack_from("<2H", payload_bytes, 16)),
        "destinationMipOneSize":
            list(struct.unpack_from("<2H", payload_bytes, 20)),
        "destinationMipOneLevel":
            struct.unpack_from("<H", payload_bytes, 24)[0],
        "noBaseMip": bool(payload_bytes[26]),
        "rawHex": payload_bytes[:32].hex(),
    }
    return {
        "observed": True,
        "pipeline":
            pipeline_record.get("pipeline", {}),
        "encoder": encoder,
        "sequence": sequence,
        "sourceTexture": textures.get(0),
        "destinationTexture": textures.get(1),
        "uniformBuffer": {
            "address": buffer_record.get("bufferAddress"),
            "offset": buffer_record.get("offset"),
            "length": buffer_record.get("bufferLength"),
            "storageMode": buffer_record.get("storageMode"),
        },
        "uniforms": uniforms,
        "imageblock": {
            "width":
                imageblock.get("width")
                if isinstance(imageblock, dict)
                else None,
            "height":
                imageblock.get("height")
                if isinstance(imageblock, dict)
                else None,
        },
        "dispatch": {
            "threadgroups":
                dispatch.get("grid")
                if isinstance(dispatch, dict)
                else None,
            "threadsPerThreadgroup":
                dispatch.get("threadsPerThreadgroup")
                if isinstance(dispatch, dict)
                else None,
        },
        "writesMipZeroAndMipOneInOneDispatch": (
            uniforms["destinationMipOneLevel"] == 1
            and uniforms["noBaseMip"] is False
        ),
    }


def _live_base_stage_snapshots(
    runtime: JsonObject,
) -> JsonObject:
    carenderer = runtime.get("carendererEvidence", {})
    texture_evidence = (
        carenderer.get("metalTextureSnapshots", {})
        if isinstance(carenderer, dict)
        else {}
    )
    snapshots = (
        texture_evidence.get("snapshots", [])
        if isinstance(texture_evidence, dict)
        else []
    )
    diagnostic_sources: set[str] = set()
    producer_sources: set[str] = set()
    for snapshot in snapshots:
        if not isinstance(snapshot, dict):
            continue
        raw_file = snapshot.get("rawFile")
        pipeline = snapshot.get("pipeline", {})
        label = (
            str(pipeline.get("label", ""))
            if isinstance(pipeline, dict)
            else ""
        )
        if not isinstance(raw_file, str):
            continue
        if (
            snapshot.get("pixelFormat") == 70
            and snapshot.get("width") == BASE_SOURCE_SIDE
            and snapshot.get("height") == BASE_SOURCE_SIDE
            and "PBGRAXm_TimgA2Xhfc_Ixrg" in label
        ):
            diagnostic_sources.add(raw_file)
        if (
            snapshot.get("pixelFormat") == 80
            and snapshot.get("width") == BASE_TARGET_SIDE
            and snapshot.get("height") == BASE_TARGET_SIDE
            and snapshot.get("mipmapLevelCount") == 1
            and snapshot.get("index") == 0
            and label
            == (
                "com.apple.coreanimation."
                "variable_blur_copy_base_mip_compute"
            )
        ):
            producer_sources.add(raw_file)
    return {
        "diagnosticSourceCandidates":
            sorted(diagnostic_sources),
        "producerSourceCandidates":
            sorted(producer_sources),
    }


def analyze(artifact: Path) -> JsonObject:
    started = time.perf_counter()
    runtime_path = artifact / "runtime.json"
    runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
    if int(runtime.get("schemaVersion", 0)) < 63:
        raise ValueError("expected introspection schema 63 or newer")

    downsample = runtime.get(
        "variableBlurDownsampleEvidence",
        {},
    )
    source_filename = str(
        downsample.get(
            "sourceFile",
            "sdf-generator-carenderer-live-tree-texture-005"
            "-pf80-448x448.raw",
        )
    )
    target_filename = str(
        downsample.get(
            "referenceFile",
            "sdf-generator-carenderer-live-tree-texture-005"
            "-pf80-448x448-mip-01.raw",
        )
    )
    source_path = artifact / source_filename
    target_path = artifact / target_filename
    source = read_texture(
        source_path,
        width=SOURCE_SIDE,
        height=SOURCE_SIDE,
    )
    target = read_texture(
        target_path,
        width=DESTINATION_SIDE,
        height=DESTINATION_SIDE,
    )
    software_half = replay_agx2_software(source)
    software_codes = unorm8(software_half)
    copy_base_half = replay_copy_base_mip_software(source)
    copy_base_codes = unorm8(copy_base_half)
    copy_base_comparison = comparison(
        copy_base_codes,
        target,
    )
    copy_base_provenance = _live_copy_base_provenance(
        runtime
    )
    base_snapshots = _live_base_stage_snapshots(runtime)
    diagnostic_candidates = base_snapshots[
        "diagnosticSourceCandidates"
    ]
    producer_candidates = base_snapshots[
        "producerSourceCandidates"
    ]
    base_stage: JsonObject = {
        **base_snapshots,
        "geometry": {
            "sourceSize": [
                BASE_SOURCE_SIDE,
                BASE_SOURCE_SIDE,
            ],
            "producerSize": [
                BASE_TARGET_SIDE,
                BASE_TARGET_SIDE,
            ],
            "sourceOrigin": [
                BASE_SOURCE_X,
                BASE_SOURCE_Y,
            ],
            "activeSize": [
                BASE_ACTIVE_WIDTH,
                BASE_ACTIVE_HEIGHT,
            ],
            "copyBase": [
                BASE_COPY_X,
                BASE_COPY_Y,
            ],
            "copyClamp": list(BASE_COPY_CLAMP),
        },
        "executed": False,
    }
    if len(diagnostic_candidates) == 1:
        diagnostic_source_path = (
            artifact / diagnostic_candidates[0]
        )
        diagnostic_source = read_texture(
            diagnostic_source_path,
            width=BASE_SOURCE_SIDE,
            height=BASE_SOURCE_SIDE,
        )
        predicted_producer = replay_base_producer_software(
            diagnostic_source
        )
        predicted_mip_zero = replay_live_copy_base_software(
            predicted_producer
        )
        base_stage.update({
            "executed": True,
            "diagnosticSourceFile":
                str(diagnostic_source_path),
            "diagnosticSourceSha256":
                sha256_file(diagnostic_source_path),
            "sourceToMipZeroComparison":
                comparison(predicted_mip_zero, source),
        })
        if len(producer_candidates) == 1:
            producer_source_path = (
                artifact / producer_candidates[0]
            )
            producer_source = read_texture(
                producer_source_path,
                width=BASE_TARGET_SIDE,
                height=BASE_TARGET_SIDE,
            )
            base_stage.update({
                "producerSourceFile":
                    str(producer_source_path),
                "producerSourceSha256":
                    sha256_file(producer_source_path),
                "sourceToProducerComparison":
                    comparison(
                        predicted_producer,
                        producer_source,
                    ),
                "producerToMipZeroComparison":
                    comparison(
                        replay_live_copy_base_software(
                            producer_source
                        ),
                        source,
                    ),
            })
    elif not diagnostic_candidates:
        base_stage["reason"] = (
            "native diagnostic source snapshot is absent"
        )
    else:
        base_stage["reason"] = (
            "native diagnostic source snapshot is ambiguous"
        )

    native_candidates = []
    candidates = (
        downsample.get("candidates", [])
        if isinstance(downsample, dict)
        else []
    )
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        record: JsonObject = {
            "function": candidate.get("function"),
            "reported": candidate,
        }
        half_metrics = _native_half_comparison(
            artifact,
            candidate,
            software_half,
        )
        if half_metrics is not None:
            record["softwareHalfComparison"] = half_metrics
        raw_metrics = _raw_candidate_comparison(
            artifact,
            candidate,
            target,
        )
        if raw_metrics is not None:
            record["liveMipComparison"] = raw_metrics
        native_candidates.append(record)

    in_place_candidates = []
    for candidate in (
        downsample.get("inPlaceCandidates", [])
        if isinstance(downsample, dict)
        else []
    ):
        if not isinstance(candidate, dict):
            continue
        record: JsonObject = {
            "function": candidate.get("function"),
            "mode": candidate.get("mode"),
            "reported": candidate,
        }
        raw_metrics = _raw_candidate_comparison(
            artifact,
            candidate,
            target,
        )
        if raw_metrics is not None:
            record["liveMipComparison"] = raw_metrics
        in_place_candidates.append(record)

    distinct_rgb = np.unique(
        source[..., :3].reshape(-1, 3),
        axis=0,
    ).shape[0]
    diagnostic_mapping = _hash_mapping(source)
    unconstrained_kernel = _unconstrained_kernel_fit(
        source,
        target,
    )
    structured_kernel = _structured_kernel_fit(source, target)
    elapsed = time.perf_counter() - started
    return {
        "liquidGlassBackdropPyramidAnalysisSchemaVersion": 4,
        "analysisImplementation": {
            "file":
                "analysis/liquid_glass_backdrop_pyramid.py",
            "sha256": sha256_file(Path(__file__).resolve()),
            "python": platform.python_version(),
            "numpy": np.__version__,
        },
        "source": {
            "artifact": str(artifact),
            "runtimeSchemaVersion": runtime["schemaVersion"],
            "runtimeJsonSha256": sha256_file(runtime_path),
            "mipZeroFile": str(source_path),
            "mipZeroSha256": sha256_file(source_path),
            "mipOneFile": str(target_path),
            "mipOneSha256": sha256_file(target_path),
            "mipZeroDistinctRGB": int(distinct_rgb),
        },
        "diagnosticBackdropMapping": diagnostic_mapping,
        "unconstrainedEffectiveKernel":
            unconstrained_kernel,
        "airConstrainedKernel":
            structured_kernel,
        "softwareAGX2Replay": comparison(
            software_codes,
            target,
        ),
        "nativeBaseProducer": base_stage,
        "liveCopyBaseMipProducer": copy_base_provenance,
        "softwareCopyBaseMipReplay":
            copy_base_comparison,
        "nativeCandidates": native_candidates,
        "inPlaceCandidates": in_place_candidates,
        "resourceMeasurements": {
            "analysisSeconds": elapsed,
            "maximumResidentSetKiB":
                resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        },
        "conclusion": {
            "fullRankIdentificationInput":
                distinct_rgb >= 50_000,
            "nativeAGX2HalfTraceExact": any(
                candidate.get("function")
                == "variable_blur_downsample_compute_agx2"
                and candidate.get(
                    "softwareHalfComparison",
                    {},
                ).get("exact") is True
                for candidate in native_candidates
            ),
            "liveMipExact":
                copy_base_provenance.get("observed") is True
                and copy_base_comparison.get("exact") is True,
            "nativeBaseProducerExact":
                base_stage.get(
                    "sourceToProducerComparison",
                    {},
                ).get("exact") is True,
            "nativeBaseToMipZeroExact":
                base_stage.get(
                    "sourceToMipZeroComparison",
                    {},
                ).get("exact") is True,
            "liveMipProducerIdentified":
                copy_base_provenance.get("observed") is True,
            "siblingMipArithmeticExact":
                copy_base_comparison.get("exact") is True,
            "portableBackdropAlgorithmRecovered":
                copy_base_provenance.get("observed") is True
                and copy_base_comparison.get("exact") is True
                and base_stage.get(
                    "sourceToMipZeroComparison",
                    {},
                ).get("exact") is True,
            "productionBackdropAuthorized": False,
            "productionShaderAuthorized": False,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Identify Apple's live Liquid Glass backdrop pyramid."
        )
    )
    parser.add_argument("artifact", type=Path)
    parser.add_argument("-o", "--output", type=Path)
    arguments = parser.parse_args()
    result = analyze(arguments.artifact)
    encoded = json.dumps(
        result,
        indent=2,
        sort_keys=True,
        allow_nan=False,
    ) + "\n"
    if arguments.output is None:
        print(encoded, end="")
    else:
        arguments.output.write_text(
            encoded,
            encoding="utf-8",
        )
        print(arguments.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
