#!/usr/bin/env python3
"""Gate fractional Liquid Glass raster setup against retained Apple pulls."""

import argparse
import json
import math
import platform
import time
from pathlib import Path
from typing import Any

import numpy as np

from liquid_glass_runtime_raster_coefficients import (
    Endpoint,
    RuntimeQuad,
    _case_from_fixed,
    _determinant_slope,
    coefficient_bits,
)

import raster_tile_selector_model as arithmetic
import validate_raster_clipped_setup_transfer as capture


type JsonObject = dict[str, Any]

UNCLIPPED_VARIANTS = (0, 1)
EXPECTED_VARIANT_NAMES = (
    "unclipped-zero-origin-control",
    "unclipped-centered-control",
)
FIRST_MISMATCH_LIMIT = 32


def _primitive(
    fixed: tuple[int, int, int, int],
    *,
    x: int,
    y: int,
) -> int:
    left, right, top, bottom = fixed
    relative_x = x * 256 + 128 - left
    relative_y = y * 256 + 128 - top
    return int(
        relative_x * (bottom - top) + relative_y * (right - left)
        < (right - left) * (bottom - top)
    )


def _pull_bits(local: int, offset: float, slope: float, constant: float) -> int:
    return arithmetic.float32_bits(
        arithmetic.float32(math.fma(local + offset, slope, constant))
    )


def validate(root: Path) -> JsonObject:
    manifest, raw_path = capture.validate_manifest(root)
    evidence = manifest["rasterClippedSetupTransfer"]
    variants = evidence["variants"]
    names = tuple(variants[index]["name"] for index in UNCLIPPED_VARIANTS)
    if names != EXPECTED_VARIANT_NAMES:
        raise ValueError("fractional raster control variants differ")

    raw = np.memmap(
        raw_path,
        dtype="<u4",
        mode="r",
        shape=(
            capture.COEFFICIENT_COUNT,
            capture.VARIANT_COUNT,
            capture.SAMPLE_POSITION_COUNT,
            2,
        ),
    )
    selectors = arithmetic.load_selector_table()
    compared_words = 0
    mismatched_words = 0
    first_mismatches: list[JsonObject] = []
    variant_mismatches = {name: 0 for name in names}
    started = time.perf_counter()

    for width_index, width in enumerate(capture.WIDTHS):
        for height_index, height in enumerate(capture.HEIGHTS):
            for witness_index, significand in enumerate(
                capture.WITNESS_SIGNIFICANDS
            ):
                coefficient_index = (
                    (width_index * capture.HEIGHT_COUNT + height_index)
                    * capture.WITNESS_COUNT
                    + witness_index
                )
                for variant_index in UNCLIPPED_VARIANTS:
                    fixed = capture.fixed_geometry(
                        width,
                        height,
                        variant_index,
                    )
                    left, right, top, bottom = fixed
                    case = _case_from_fixed(
                        name=(
                            f"width-{width_index}-height-{height_index}"
                            f"-variant-{variant_index}"
                        ),
                        left=left,
                        bottom=top,
                        right=right,
                        top=bottom,
                    )
                    low_bits, high_bits = capture.endpoint_bits(
                        width_index,
                        variant_index,
                        significand,
                    )
                    endpoint = Endpoint(
                        name="fractional-control-x",
                        lowBits=low_bits,
                        highBits=high_bits,
                    )
                    quad = RuntimeQuad(
                        case=case,
                        endpoints=(endpoint, endpoint, endpoint, endpoint),
                    )
                    slope = _determinant_slope(
                        case,
                        endpoint,
                        axis=0,
                        selector_table=selectors,
                    )
                    constants: dict[tuple[int, int], float] = {}
                    variant_name = names[variant_index]
                    for sample_index, sample_x in enumerate(capture.SAMPLE_XS):
                        tile = sample_x // 32
                        primitive = _primitive(
                            fixed,
                            x=sample_x,
                            y=capture.SAMPLE_Y,
                        )
                        key = (primitive, tile)
                        if key not in constants:
                            constants[key] = arithmetic.bits_float32(
                                coefficient_bits(
                                    quad,
                                    channel=0,
                                    primitive=primitive,
                                    tile=tile,
                                    selector_table=selectors,
                                )
                            )
                        constant = constants[key]
                        local = sample_x - tile * 32
                        predicted = (
                            _pull_bits(local, 0.0, slope, constant),
                            _pull_bits(local, 0.9375, slope, constant),
                        )
                        observed = raw[
                            coefficient_index,
                            variant_index,
                            sample_index,
                        ]
                        for component, (candidate, apple) in enumerate(
                            zip(predicted, observed, strict=True)
                        ):
                            compared_words += 1
                            apple_int = int(apple)
                            if candidate == apple_int:
                                continue
                            mismatched_words += 1
                            variant_mismatches[variant_name] += 1
                            if len(first_mismatches) < FIRST_MISMATCH_LIMIT:
                                first_mismatches.append(
                                    {
                                        "widthIndex": width_index,
                                        "heightIndex": height_index,
                                        "witnessIndex": witness_index,
                                        "variant": variant_name,
                                        "sampleIndex": sample_index,
                                        "sampleX": sample_x,
                                        "primitive": primitive,
                                        "component": component,
                                        "predicted": f"0x{candidate:08x}",
                                        "apple": f"0x{apple_int:08x}",
                                    }
                                )

    expected_words = (
        capture.COEFFICIENT_COUNT
        * len(UNCLIPPED_VARIANTS)
        * capture.SAMPLE_POSITION_COUNT
        * 2
    )
    if compared_words != expected_words:
        raise ValueError("fractional raster comparison coverage differs")
    return {
        "liquidGlassFractionalRasterGateSchemaVersion": 1,
        "platform": platform.platform(),
        "artifact": str(root),
        "captureIdentity": {
            "ciCommit": manifest["ciCommit"],
            "rawFile": str(raw_path),
            "rawBytes": evidence["bytes"],
            "rawSHA256": evidence["sha256"],
        },
        "model": {
            "coordinateQuantumPixels": "1/256",
            "coordinateTieRule": "toward-positive-infinity",
            "determinant": "fixedWidth*fixedHeight*2^-16",
            "selector": "exhaustive-normalized-fractional-selector-table",
            "constant": (
                "measured 27-bit first/tile/reciprocal stages, top discarded "
                "column carry, 28-bit composite"
            ),
            "predictionReadsCapturedCoefficients": False,
            "predictionReadsCapturedCoordinates": False,
        },
        "controls": list(names),
        "gate": {
            "exact": mismatched_words == 0,
            "comparedWords": compared_words,
            "mismatchedWords": mismatched_words,
            "variantMismatchedWords": variant_mismatches,
            "firstMismatches": first_mismatches,
        },
        "elapsedSeconds": time.perf_counter() - started,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture", type=Path)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    report = validate(arguments.capture)
    encoded = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if arguments.output is not None:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    return 0 if report["gate"]["exact"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
