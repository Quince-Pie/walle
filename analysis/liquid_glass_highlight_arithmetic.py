#!/usr/bin/env python3
"""Bit-check arithmetic stages captured by the Apple Metal highlight probe."""

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray


type JsonObject = dict[str, Any]
type Float32Array = NDArray[np.float32]
type HalfArray = NDArray[np.float16]
type UInt16Array = NDArray[np.uint16]
type UInt32Array = NDArray[np.uint32]

WIDTH = 1024
HEIGHT = 1024
EPSILON_BITS = 0x068E


def uint32_image(root: Path, name: str) -> UInt32Array:
    path = root / name
    values = np.fromfile(path, dtype="<u4")
    expected = WIDTH * HEIGHT * 4
    if values.size != expected:
        raise ValueError(f"{path} has {values.size} words; expected {expected}")
    return values.reshape(HEIGHT, WIDTH, 4)


def bgra_image(root: Path, name: str) -> NDArray[np.uint8]:
    path = root / name
    values = np.fromfile(path, dtype=np.uint8)
    expected = WIDTH * HEIGHT * 4
    if values.size != expected:
        raise ValueError(f"{path} has {values.size} bytes; expected {expected}")
    return values.reshape(HEIGHT, WIDTH, 4)


def low_half(words: UInt32Array) -> HalfArray:
    return (words & np.uint32(0xFFFF)).astype("<u2").view("<f2")


def high_half(words: UInt32Array) -> HalfArray:
    return (words >> np.uint32(16)).astype("<u2").view("<f2")


def unpack_half4(words: UInt32Array) -> HalfArray:
    if words.shape[-1] != 2:
        raise ValueError("packed half4 source must have two uint32 components")
    return np.stack(
        (
            low_half(words[..., 0]),
            high_half(words[..., 0]),
            low_half(words[..., 1]),
            high_half(words[..., 1]),
        ),
        axis=-1,
    )


def half_round(value: NDArray[Any]) -> HalfArray:
    return np.asarray(value, dtype=np.float64).astype(np.float16)


def half_fma(
    left: NDArray[Any],
    right: NDArray[Any],
    addend: NDArray[Any],
) -> HalfArray:
    return (
        np.asarray(left, dtype=np.float64)
        * np.asarray(right, dtype=np.float64)
        + np.asarray(addend, dtype=np.float64)
    ).astype(np.float16)


def float32_fma(
    left: NDArray[Any],
    right: NDArray[Any],
    addend: NDArray[Any],
) -> Float32Array:
    return (
        np.asarray(left, dtype=np.float64)
        * np.asarray(right, dtype=np.float64)
        + np.asarray(addend, dtype=np.float64)
    ).astype(np.float32)


def float32_bits(value: NDArray[Any]) -> UInt32Array:
    return np.asarray(value, dtype=np.float32).view("<u4")


def half_bits(value: NDArray[Any]) -> UInt16Array:
    return np.asarray(value, dtype=np.float16).view("<u2")


def comparison(
    candidate: NDArray[Any],
    reference: NDArray[Any],
    mask: NDArray[np.bool_],
    *,
    half: bool = False,
) -> JsonObject:
    candidate_bits = half_bits(candidate) if half else float32_bits(candidate)
    reference_bits = half_bits(reference) if half else float32_bits(reference)
    changed = candidate_bits[mask] != reference_bits[mask]
    return {
        "exact": not bool(np.any(changed)),
        "observedComponents": int(changed.size),
        "mismatchedComponents": int(np.count_nonzero(changed)),
    }


def directional_numerator(
    normal: HalfArray,
    direction_bits: tuple[int, int],
    threshold_bits: int,
) -> Float32Array:
    direction = np.asarray(direction_bits, dtype=np.uint16).view(np.float16)
    first_product = half_round(normal[..., 0] * direction[0])
    dot = half_fma(normal[..., 1], direction[1], first_product)
    threshold = np.uint16(threshold_bits).view(np.float16)
    return (
        dot.astype(np.float32) - np.float32(threshold)
    ).astype(np.float32)


def analyze(root: Path) -> JsonObject:
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    schema = manifest.get("schemaVersion")
    if schema not in (1, 2, 3):
        raise ValueError(f"unsupported highlight arithmetic schema: {schema}")
    if (
        manifest.get("probe") != "apple-metal-key-fill-vibrant-arithmetic"
        or manifest.get("width") != WIDTH
        or manifest.get("height") != HEIGHT
        or manifest.get("metalFastMathEnabled") is not True
    ):
        raise ValueError("highlight arithmetic manifest contract differs")

    geometry_words = uint32_image(
        root,
        "highlight-geometry-rgba32ui.raw",
    )
    geometry = geometry_words.view("<f4")
    key_a = uint32_image(root, "highlight-key-a-rgba32ui.raw").view("<f4")
    key_b = uint32_image(root, "highlight-key-b-rgba32ui.raw").view("<f4")
    fill_b = uint32_image(root, "highlight-fill-b-rgba32ui.raw").view("<f4")
    half_stages = uint32_image(
        root,
        "highlight-half-stages-rgba32ui.raw",
    )
    highlight_alpha = low_half(half_stages[..., 1])
    covered = np.any(geometry_words != 0, axis=-1)
    active = (
        highlight_alpha >= np.uint16(EPSILON_BITS).view(np.float16)
        if schema == 3
        else np.any(half_stages != 0, axis=-1)
    )
    scaled_distance = geometry[..., 0]
    normal = np.stack(
        (
            high_half(geometry_words[..., 2]),
            low_half(geometry_words[..., 3]),
        ),
        axis=-1,
    )
    fade_mix = np.uint16(0x399A).view(np.float16).astype(np.float32)
    normalized = np.clip(scaled_distance, np.float32(0), np.float32(1))
    fade = float32_fma(-normalized, fade_mix, np.float32(1))
    denominator = half_round(
        np.float16(1) - np.uint16(0xBB84).view(np.float16)
    ).astype(np.float32)
    reciprocal = (np.float32(1) / denominator).astype(np.float32)

    key_numerator = directional_numerator(
        normal,
        (0xB9A8, 0xB9A8),
        0xBB84,
    )
    fill_numerator = directional_numerator(
        normal,
        (0x39A8, 0x39A8),
        0xBB84,
    )
    key_directional = np.clip(
        (key_b[..., 1] * reciprocal).astype(np.float32),
        np.float32(0),
        np.float32(1),
    )
    fill_directional = np.clip(
        (fill_b[..., 1] * reciprocal).astype(np.float32),
        np.float32(0),
        np.float32(1),
    )
    key_alpha_float = (
        key_b[..., 0] * key_b[..., 2]
    ).astype(np.float32)
    fill_alpha_float = (
        fill_b[..., 0] * fill_b[..., 2]
    ).astype(np.float32)
    key_alpha = low_half(half_stages[..., 0])
    fill_alpha = high_half(half_stages[..., 0])

    stages: dict[str, JsonObject] = {
        "normalizedDistance": comparison(
            normalized,
            key_a[..., 0],
            active,
        ),
        "fadeFma": comparison(fade, key_a[..., 1], active),
        "fadedCoverage": comparison(
            (key_a[..., 2] * key_a[..., 1]).astype(np.float32),
            key_a[..., 3],
            active,
        ),
        "keyDirectionalNumerator": comparison(
            key_numerator,
            key_b[..., 1],
            active,
        ),
        "fillDirectionalNumerator": comparison(
            fill_numerator,
            fill_b[..., 1],
            active,
        ),
        "keyDirectionalReciprocalProduct": comparison(
            key_directional,
            key_b[..., 2],
            active,
        ),
        "fillDirectionalReciprocalProduct": comparison(
            fill_directional,
            fill_b[..., 2],
            active,
        ),
        "keyAlphaFloat": comparison(
            key_alpha_float,
            key_b[..., 3],
            active,
        ),
        "fillAlphaFloat": comparison(
            fill_alpha_float,
            fill_b[..., 3],
            active,
        ),
        "keyAlphaHalf": comparison(
            half_round(key_b[..., 3]),
            key_alpha,
            active,
            half=True,
        ),
        "fillAlphaHalf": comparison(
            half_round(fill_b[..., 3]),
            fill_alpha,
            active,
            half=True,
        ),
        "highlightAlphaHalfAdd": comparison(
            half_round(
                key_alpha.astype(np.float64)
                + fill_alpha.astype(np.float64)
            ),
            highlight_alpha,
            active,
            half=True,
        ),
    }

    if schema >= 2:
        compositor_a = uint32_image(
            root,
            "highlight-compositor-a-rgba32ui.raw",
        )
        compositor_b = uint32_image(
            root,
            "highlight-compositor-b-rgba32ui.raw",
        )
        mapped = unpack_half4(compositor_a[..., :2])
        source_initial = unpack_half4(compositor_a[..., 2:])
        source_straight = np.stack(
            (
                low_half(compositor_b[..., 0]),
                high_half(compositor_b[..., 0]),
                low_half(compositor_b[..., 1]),
            ),
            axis=-1,
        )
        source_final = unpack_half4(compositor_b[..., 2:])
        final = unpack_half4(half_stages[..., 2:])
        destination_bgra = bgra_image(root, "destination-bgra8.raw")
        destination = half_round(
            destination_bgra[..., [2, 1, 0, 3]].astype(np.float64) / 255
        )
        stages["sourceInitialHalfMultiply"] = comparison(
            half_round(
                mapped.astype(np.float64)
                * highlight_alpha[..., None].astype(np.float64)
            ),
            source_initial,
            active,
            half=True,
        )
        stages["sourceRepremultiply"] = comparison(
            half_round(
                source_straight.astype(np.float64)
                * source_initial[..., 3, None].astype(np.float64)
            ),
            source_final[..., :3],
            active,
            half=True,
        )
        factor = half_round(
            1 - source_final[..., 3].astype(np.float64)
        )
        final_candidate = half_fma(
            destination,
            factor[..., None],
            source_final,
        )
        final_candidate[..., 3] = np.clip(
            final_candidate[..., 3],
            np.float16(0),
            np.float16(1),
        )
        stages["finalSourceOverHalfFma"] = comparison(
            final_candidate,
            final,
            active,
            half=True,
        )

        output = bgra_image(root, "highlight-final-bgra8.raw")[
            ..., [2, 1, 0, 3]
        ]
        destination_codes = destination_bgra[..., [2, 1, 0, 3]]
        candidate_codes = destination_codes.copy()
        encoded = np.clip(
            np.rint(final.astype(np.float64) * 255),
            0,
            255,
        ).astype(np.uint8)
        traced = covered if schema == 3 else active
        candidate_codes[traced] = encoded[traced]
        changed = candidate_codes != output
        stages["targetUnorm8Conversion"] = {
            "exact": not bool(np.any(changed)),
            "observedComponents": int(changed.size),
            "mismatchedComponents": int(np.count_nonzero(changed)),
        }

    exact = all(stage["exact"] for stage in stages.values())
    y, x = np.where(active)
    return {
        "liquidGlassHighlightArithmeticSchemaVersion": 1,
        "source": {
            "artifact": str(root),
            "captureSchemaVersion": schema,
            "metalDevice": manifest.get("metalDevice"),
            "metalFastMathEnabled": True,
        },
        "coverage": {
            "activePixels": int(np.count_nonzero(active)),
            "tracedPixels": int(np.count_nonzero(covered)),
            "boundingBox": [
                int(x.min()),
                int(y.min()),
                int(x.max()),
                int(y.max()),
            ],
        },
        "stages": stages,
        "gate": {
            "exact": exact,
            "exactStageCount": sum(stage["exact"] for stage in stages.values()),
            "stageCount": len(stages),
            "productionShaderAuthorized": False,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact", type=Path)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    report = analyze(arguments.artifact)
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
