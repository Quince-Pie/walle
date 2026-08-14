#!/usr/bin/env python3
"""Freeze the input-only AGX clip-weight tomography plan.

The plan keeps the post-clip rectangle fixed while moving one rejected edge.
Four legacy endpoint groups join the prior CI discriminator.  Four additional
groups expose algebraic fingerprints that the old mid-range ramps could not:
homogeneous scaling, complements, translation invariance, and cancellation.
No rendered output or captured coefficient is read while constructing it.
"""

import argparse
import hashlib
import json
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Final


ROOT: Final = Path(__file__).resolve().parent.parent
OUTPUT_DEFAULT: Final = ROOT / "build" / "analysis-agx-clip-weight" / "prospective-plan"

MAGIC: Final = b"AGXWGT01"
SCHEMA: Final = "walle-reveal-agx-clip-weight-plan-v1"
UNITS_PER_PIXEL: Final = 256
DISTANCE_FIXED_MAXIMUM: Final = 8_192
PLANE_CODES: Final = {"left": 0, "right": 1, "top": 2, "bottom": 3}
PLAN_HEADER: Final = struct.Struct("<8s4I")
PLAN_RECORD: Final = struct.Struct("<22I")
LEGACY_DELTA_BITS: Final = (
    0x3EE2B84A,
    0x3E88E3E7,
    0x3E89145A,
    0x3E907383,
    0x3E97D2AC,
    0x3EA97516,
    0x3EB0D43F,
    0x3EB83368,
    0x3EC9D5D2,
    0x3ECC2B94,
    0x3ED89424,
    0x3EE52D27,
    0x3EEC8C50,
    0x3EF17493,
    0x3EF791A5,
    0x3EFE2EBA,
)
MANDATORY_HOLDOUT_DISTANCES: Final = frozenset(range(65)) | frozenset(
    1 << exponent for exponent in range(14) if 1 << exponent <= 8_192
)

type JsonValue = (
    None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]
)
type JsonObject = dict[str, JsonValue]


@dataclass(frozen=True, slots=True)
class Pattern:
    name: str
    outer_bits: tuple[int, int, int, int]
    inner_bits: tuple[int, int, int, int]
    purpose: str


@dataclass(frozen=True, slots=True)
class Group:
    name: str
    viewport: int
    plane: str
    cross_span: int
    split: str
    all_distances: bool


def _bits(value: float) -> int:
    return struct.unpack("<I", struct.pack("<f", value))[0]


def _legacy_patterns() -> tuple[Pattern, ...]:
    patterns: list[Pattern] = []
    for pattern_index in range(4):
        deltas = LEGACY_DELTA_BITS[pattern_index * 4 : pattern_index * 4 + 4]
        low: list[int] = []
        high: list[int] = []
        for delta in deltas:
            half = delta - 0x0080_0000
            low.append(half | 0x8000_0000)
            high.append(half)
        patterns.append(
            Pattern(
                name=f"legacy-centered-{pattern_index}",
                outer_bits=tuple(low),  # type: ignore[arg-type]
                inner_bits=tuple(high),  # type: ignore[arg-type]
                purpose="exact join to four prior fixed-post-clip witnesses",
            )
        )
    return tuple(patterns)


def _patterns() -> tuple[Pattern, ...]:
    zero = _bits(0.0)
    one = _bits(1.0)
    negative_one = _bits(-1.0)
    small = _bits(2.0**-20)
    large = _bits(2.0**20)
    negative_large = _bits(-(2.0**20))
    fingerprints = (
        Pattern(
            name="zero-to-scale",
            outer_bits=(zero, zero, zero, zero),
            inner_bits=(one, negative_one, small, negative_large),
            purpose="retained-weight scaling and sign symmetry",
        ),
        Pattern(
            name="scale-to-zero",
            outer_bits=(one, negative_one, small, negative_large),
            inner_bits=(zero, zero, zero, zero),
            purpose="removed-weight scaling, complement, and sign symmetry",
        ),
        Pattern(
            name="translated-unit-delta",
            outer_bits=(zero, one, large, negative_large),
            inner_bits=(
                one,
                _bits(2.0),
                _bits((2.0**20) + 1.0),
                _bits(-(2.0**20) + 1.0),
            ),
            purpose="same unit delta under zero, unit, and large translations",
        ),
        Pattern(
            name="signed-cancellation",
            outer_bits=(
                0x3F80_0001,
                0xBF80_0001,
                0x0080_0000,
                0x8080_0000,
            ),
            inner_bits=(
                0xBF80_0001,
                0x3F80_0001,
                one,
                negative_one,
            ),
            purpose="endpoint order, cancellation, normal-range transition, and sign",
        ),
    )
    patterns = _legacy_patterns() + fingerprints
    if len(patterns) != 8:
        raise AssertionError("the tomography plan must contain eight patterns")
    return patterns


def _groups() -> tuple[Group, ...]:
    return (
        Group("discovery-v256-left-h64", 256, "left", 64, "discovery", True),
        Group("holdout-v512-left-h96", 512, "left", 96, "holdout", False),
        Group("holdout-v512-right-h128", 512, "right", 128, "holdout", False),
        Group("holdout-v512-top-h160", 512, "top", 160, "holdout", False),
        Group("holdout-v512-bottom-h192", 512, "bottom", 192, "holdout", False),
    )


def _holdout_selected(group_index: int, distance_fixed: int) -> bool:
    if distance_fixed in MANDATORY_HOLDOUT_DISTANCES:
        return True
    identity = struct.pack("<II", group_index, distance_fixed)
    return hashlib.sha256(identity).digest()[0] < 16


def _float_geometry(
    group: Group, distance_fixed: int
) -> tuple[float, float, float, float]:
    viewport = group.viewport
    center = viewport / 2.0
    cross_lower = center - group.cross_span / 2.0
    cross_upper = center + group.cross_span / 2.0
    distance = distance_fixed / UNITS_PER_PIXEL
    if group.plane == "left":
        guard = -viewport / 4.0
        return (guard - distance, guard + viewport, cross_lower, cross_upper)
    if group.plane == "right":
        guard = 5.0 * viewport / 4.0
        return (guard - viewport, guard + distance, cross_lower, cross_upper)
    if group.plane == "top":
        guard = -viewport / 4.0
        return (cross_lower, cross_upper, guard - distance, guard + viewport)
    if group.plane == "bottom":
        guard = 5.0 * viewport / 4.0
        return (cross_lower, cross_upper, guard - viewport, guard + distance)
    raise AssertionError("unknown plane")


def _sample(group: Group) -> tuple[int, int]:
    center = group.viewport // 2
    if group.plane == "left":
        return (center + 13, center - 11)
    if group.plane == "right":
        return (center - 13, center - 11)
    if group.plane == "top":
        return (center - 11, center + 13)
    if group.plane == "bottom":
        return (center - 11, center - 13)
    raise AssertionError("unknown plane")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def generate(output_directory: Path) -> JsonObject:
    if output_directory.exists():
        raise FileExistsError(output_directory)
    output_directory.mkdir(parents=True)
    patterns = _patterns()
    groups = _groups()
    records = bytearray()
    per_group_counts: list[int] = []
    record_index = 0
    for group_index, group in enumerate(groups):
        group_count = 0
        for distance_fixed in range(DISTANCE_FIXED_MAXIMUM + 1):
            if not group.all_distances and not _holdout_selected(
                group_index, distance_fixed
            ):
                continue
            geometry_bits = tuple(
                _bits(value) for value in _float_geometry(group, distance_fixed)
            )
            sample_x, sample_y = _sample(group)
            for pattern_index, pattern in enumerate(patterns):
                split_code = 0 if group.split == "discovery" else 1
                records.extend(
                    PLAN_RECORD.pack(
                        record_index,
                        group_index,
                        pattern_index,
                        distance_fixed,
                        group.viewport,
                        PLANE_CODES[group.plane],
                        group.cross_span,
                        split_code,
                        *geometry_bits,
                        *pattern.outer_bits,
                        *pattern.inner_bits,
                        sample_x,
                        sample_y,
                    )
                )
                record_index += 1
                group_count += 1
        per_group_counts.append(group_count)

    encoded = bytearray(
        PLAN_HEADER.pack(
            MAGIC,
            1,
            record_index,
            PLAN_RECORD.size // 4,
            len(patterns),
        )
    )
    encoded.extend(records)
    plan_path = output_directory / "reveal-agx-clip-weight-plan.bin"
    plan_path.write_bytes(encoded)
    manifest: JsonObject = {
        "schema": SCHEMA,
        "authority": {
            "referencePixelsRead": False,
            "capturedCoefficientsRead": False,
            "outputBlind": True,
            "productionMutationAuthorized": False,
        },
        "format": {
            "magic": MAGIC.decode(),
            "version": 1,
            "headerBytes": PLAN_HEADER.size,
            "recordBytes": PLAN_RECORD.size,
            "recordWords": PLAN_RECORD.size // 4,
            "recordFields": [
                "recordIndex",
                "groupIndex",
                "patternIndex",
                "distanceFixed",
                "viewport",
                "planeCode",
                "crossSpanPixels",
                "splitCode",
                "geometryFloatBits[4]",
                "outerValueBits[4]",
                "innerValueBits[4]",
                "sampleXY[2]",
            ],
        },
        "plan": {
            "file": plan_path.name,
            "bytes": plan_path.stat().st_size,
            "sha256": _sha256(plan_path),
            "recordCount": record_index,
            "distanceFixedMaximum": DISTANCE_FIXED_MAXIMUM,
            "distanceStepPixels": 1 / UNITS_PER_PIXEL,
            "patternCount": len(patterns),
            "groupCount": len(groups),
            "perGroupRecordCounts": per_group_counts,
        },
        "patterns": [
            {
                "name": pattern.name,
                "outerBits": [f"0x{word:08x}" for word in pattern.outer_bits],
                "innerBits": [f"0x{word:08x}" for word in pattern.inner_bits],
                "purpose": pattern.purpose,
            }
            for pattern in patterns
        ],
        "groups": [
            {
                "name": group.name,
                "viewport": group.viewport,
                "plane": group.plane,
                "planeCode": PLANE_CODES[group.plane],
                "crossSpanPixels": group.cross_span,
                "split": group.split,
                "selection": (
                    "all distances 0..8192"
                    if group.all_distances
                    else "mandatory 0..64 and powers of two, plus sha256(group,distance)[0] < 16"
                ),
                "sample": list(_sample(group)),
            }
            for group in groups
        ],
        "generator": {
            "path": str(Path(__file__).resolve()),
            "sha256": _sha256(Path(__file__).resolve()),
        },
    }
    manifest_path = output_directory / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=OUTPUT_DEFAULT)
    arguments = parser.parse_args()
    print(json.dumps(generate(arguments.output), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
