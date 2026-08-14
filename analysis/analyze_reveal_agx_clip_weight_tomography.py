#!/usr/bin/env python3
"""Analyze only the frozen discovery prefix of the AGX clip-weight capture.

The capture contains one discovery group followed by four sealed holdout
groups.  This program deliberately uses bounded reads: it reads the public
plan header plus group 0 records and exactly the corresponding raw prefix.
It never maps, hashes, or otherwise opens a holdout byte.

The discovery patterns expose the two endpoint responses independently and
then exercise translation, scaling, and cancellation.  The resulting report
is a rejection/identification aid for the fixed-function clipper.  It is not a
production renderer law and cannot authorize opening the holdouts.
"""

import argparse
import hashlib
import json
import re
import struct
from collections import Counter
from dataclasses import dataclass
from fractions import Fraction
from functools import lru_cache
from pathlib import Path
from typing import Final

import numpy as np
from numpy.typing import NDArray


type JsonValue = (
    None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]
)
type JsonObject = dict[str, JsonValue]
type UInt32Array = NDArray[np.uint32]

ROOT: Final = Path(__file__).resolve().parent.parent
ANALYSIS: Final = ROOT / "analysis"
CAPTURE_ROOT: Final = ROOT / "build" / "analysis-agx-clip-weight" / "macos-capture"
PLAN: Final = (
    ROOT
    / "build"
    / "analysis-agx-clip-weight"
    / "prospective-plan"
    / "reveal-agx-clip-weight-plan.bin"
)
PLAN_MANIFEST: Final = PLAN.with_name("manifest.json")
PREREGISTRATION: Final = (
    ANALYSIS / "reveal_agx_clip_weight_tomography_preregistration.json"
)
GENERATOR: Final = ANALYSIS / "generate_reveal_agx_clip_weight_tomography.py"
PROBE_SOURCE: Final = ANALYSIS / "reveal_agx_clip_weight_tomography_probe.swift"
CAPTURE_MANIFEST: Final = CAPTURE_ROOT / "capture-export" / "manifest.json"
RAW: Final = CAPTURE_ROOT / "capture-export" / "reveal-agx-clip-weight-tomography.raw"
CAPTURE_STDOUT: Final = CAPTURE_ROOT / "capture.stdout"
CAPTURE_STDERR: Final = CAPTURE_ROOT / "capture.stderr"
CAPTURE_EXECUTABLE: Final = CAPTURE_ROOT / "reveal-agx-clip-weight-tomography-probe"
CAPTURE_INTERPOSER: Final = CAPTURE_ROOT / "libwalle-agx-ldcf-export.dylib"
FAST_RECIPROCAL_DELTAS: Final = (
    ROOT
    / "artifacts"
    / "gh-run-30556057571"
    / "liquid-glass-float-intrinsic-probe-30556057571"
    / "float-fast-reciprocal-deltas-i8.bin"
)
P25_BITMAP: Final = ROOT / "parity" / "raster_p25_selector_ceil_bits.bin"
OUTPUT: Final = (
    ROOT
    / "build"
    / "analysis-agx-clip-weight"
    / "discovery-analysis"
    / "reveal-agx-clip-weight-discovery-result.json"
)

PLAN_HEADER: Final = struct.Struct("<8s4I")
PLAN_RECORD: Final = struct.Struct("<22I")
PLAN_MAGIC: Final = b"AGXWGT01"
PLAN_VERSION: Final = 1
PLAN_RECORD_COUNT: Final = 83_872
PLAN_BYTES: Final = 7_380_760
RAW_BYTES: Final = 135_537_152
PATTERN_COUNT: Final = 8
DISTANCE_COUNT: Final = 8_193
DISCOVERY_RECORD_COUNT: Final = DISTANCE_COUNT * PATTERN_COUNT
RAW_VECTOR_COUNT: Final = 101
RAW_VECTOR_WORDS: Final = 4
RAW_RECORD_WORDS: Final = RAW_VECTOR_COUNT * RAW_VECTOR_WORDS
DISCOVERY_PLAN_BYTES: Final = (
    PLAN_HEADER.size + DISCOVERY_RECORD_COUNT * PLAN_RECORD.size
)
DISCOVERY_RAW_WORDS: Final = DISCOVERY_RECORD_COUNT * RAW_RECORD_WORDS
DISCOVERY_RAW_BYTES: Final = DISCOVERY_RAW_WORDS * 4
COEFFICIENT_STARTS: Final = (5, 21, 37, 53)
COEFFICIENT_REPEAT_COUNT: Final = 16
CLIP_SPAN_FIXED: Final = 65_536

EXPECTED_SHA256: Final = {
    GENERATOR: "780bae4e02711c1da22a2a56b7919455bdf8bda8419726feb002b6ab4c4e8bf1",
    PREREGISTRATION: "020e98dea95357ccfeb3e796b0b1e8d68d1d9de74e32c81ef199f4c74090fb12",
    PROBE_SOURCE: "ed5d348141781ae616cea86cec2156a74294f1e8f6417ceff7325dbaec44eb8b",
    PLAN_MANIFEST: "32019708f66ed0d728de15ce9aa45f5236e5f5d288ad1476a434aed62d4b726a",
    CAPTURE_MANIFEST: "27f041e0a493cd11d22cd092d6e268727d4c8481d6350bcbb4206f7b7c1dc7e6",
    CAPTURE_STDOUT: "051d2bb69288b4eb03416dabb440901ee3f3abd198e4a181b3aead8c6abda2e4",
    CAPTURE_STDERR: "e2be36df70ee59312ed3d1e16e424e2629687d180a2a4de2c1dac301b0f4422e",
    CAPTURE_EXECUTABLE: "3b957137a0295ffd6a50ab3c6f2eff5ba908d4d00946c94909bf35d957814d2e",
    CAPTURE_INTERPOSER: "1b93004ed4e034f3a4cd298b68bfd3cf226af4a9b46c859653247c7f1b05cb5a",
    FAST_RECIPROCAL_DELTAS: "4f3d7ead253db2f8f51b561b94ed858c5b21c1419d6184b8b4f48bd3027d6916",
    P25_BITMAP: "9fbc083dfd9c89fc0bcdc89308acfc4530d408e93789a7dab89ee59ff60a198f",
}
EXPECTED_PLAN_FULL_SHA256: Final = (
    "f90e4e3b5f0d46b0fb8250c97aa40eb4ddb32c67050c9823feaecb0a20baaf8d"
)
EXPECTED_RAW_FULL_SHA256: Final = (
    "c7f273463a80e54c1462a38568fbb0ab86c877ad113b334f668c6dae6595055b"
)
EXPECTED_PLAN_PREFIX_SHA256: Final = (
    "95ad3a742a7f609f673932c30450f5846c1a3f94327c79eb4b2b6741368c80d3"
)
EXPECTED_RAW_PREFIX_SHA256: Final = (
    "8355094aa3c78c98646935f76711c71035f22f0ede2498d5ee2e9059f3382e67"
)

PATCH_TRACE: Final = re.compile(
    r"^AGX_IO coefficient export matches=(\d+) applied=(\d+)$", re.MULTILINE
)


@dataclass(frozen=True, slots=True)
class Discovery:
    plan: UInt32Array
    raw: UInt32Array
    outer_bits: UInt32Array
    inner_bits: UInt32Array
    coefficient_bits: UInt32Array
    generated_bits: UInt32Array


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _sha256_buffer(value: NDArray[np.generic] | bytes) -> str:
    view = memoryview(value).cast("B") if isinstance(value, np.ndarray) else value
    return hashlib.sha256(view).hexdigest()


def _check_small_inputs() -> list[JsonObject]:
    verified: list[JsonObject] = []
    for path, expected in EXPECTED_SHA256.items():
        actual = _sha256(path)
        if actual != expected:
            raise ValueError(f"{path.relative_to(ROOT)} SHA-256 differs")
        verified.append(
            {
                "path": str(path.relative_to(ROOT)),
                "bytes": path.stat().st_size,
                "sha256": actual,
            }
        )
    return verified


def _load_json(path: Path) -> JsonObject:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.relative_to(ROOT)} is not a JSON object")
    return value


def _authenticate_manifests() -> JsonObject:
    plan_manifest = _load_json(PLAN_MANIFEST)
    capture_manifest = _load_json(CAPTURE_MANIFEST)
    plan = plan_manifest.get("plan")
    capture = capture_manifest.get("capture")
    captured_plan = capture_manifest.get("plan")
    preregistration = capture_manifest.get("preregistration")
    executable = capture_manifest.get("executable")
    if not all(
        isinstance(value, dict)
        for value in (plan, capture, captured_plan, preregistration, executable)
    ):
        raise ValueError("capture or plan manifest shape differs")
    assert isinstance(plan, dict)
    assert isinstance(capture, dict)
    assert isinstance(captured_plan, dict)
    assert isinstance(preregistration, dict)
    assert isinstance(executable, dict)
    if (
        plan.get("sha256") != EXPECTED_PLAN_FULL_SHA256
        or plan.get("bytes") != PLAN_BYTES
        or plan.get("recordCount") != PLAN_RECORD_COUNT
        or captured_plan.get("sha256") != EXPECTED_PLAN_FULL_SHA256
        or captured_plan.get("bytes") != PLAN_BYTES
        or capture.get("sha256") != EXPECTED_RAW_FULL_SHA256
        or capture.get("bytes") != RAW_BYTES
        or capture.get("recordCount") != PLAN_RECORD_COUNT
        or capture.get("recordBytes") != RAW_RECORD_WORDS * 4
        or capture.get("recordVectorCount") != RAW_VECTOR_COUNT
        or capture.get("coefficientExportRegions") != list(COEFFICIENT_STARTS)
        or preregistration.get("sha256") != EXPECTED_SHA256[PREREGISTRATION]
        or executable.get("sha256") != EXPECTED_SHA256[CAPTURE_EXECUTABLE]
    ):
        raise ValueError("frozen capture identity differs")
    authority = capture_manifest.get("authority")
    if not isinstance(authority, dict) or authority != {
        "establishesClipSetupLaw": False,
        "mutatesProductionRenderer": False,
        "observedCoefficientsReadBeforePlanFreeze": False,
        "opensReferencePixels": False,
        "usesPublicClipInputsOnly": True,
    }:
        raise ValueError("capture authority differs")
    trace = CAPTURE_STDERR.read_text(encoding="utf-8")
    match = PATCH_TRACE.search(trace)
    if match is None or tuple(map(int, match.groups())) != (1, 1):
        raise ValueError("coefficient-export patch accounting differs")
    progress = CAPTURE_STDOUT.read_text(encoding="utf-8").splitlines()
    if progress != [
        "clip-weight-tomography: group 1/5, records 65544/83872",
        "clip-weight-tomography: group 2/5, records 70088/83872",
        "clip-weight-tomography: group 3/5, records 74696/83872",
        "clip-weight-tomography: group 4/5, records 79248/83872",
        "clip-weight-tomography: group 5/5, records 83872/83872",
    ]:
        raise ValueError("capture progress accounting differs")
    return {
        "planManifestDeclaresFullPlanSha256": EXPECTED_PLAN_FULL_SHA256,
        "captureManifestDeclaresFullRawSha256": EXPECTED_RAW_FULL_SHA256,
        "coefficientExportShaderMatchCount": 1,
        "coefficientExportPatchAppliedCount": 1,
        "completedRecordCount": PLAN_RECORD_COUNT,
    }


def _float_bits(value: float) -> int:
    return struct.unpack("<I", struct.pack("<f", value))[0]


def _float_fraction(bits: int) -> Fraction:
    sign = -1 if bits & 0x8000_0000 else 1
    exponent = (bits >> 23) & 0xFF
    significand = bits & 0x007F_FFFF
    if exponent == 0xFF:
        raise ValueError("non-finite binary32 has no rational value")
    if exponent == 0:
        return sign * Fraction(significand, 1 << 149)
    significand |= 1 << 23
    shift = exponent - 150
    magnitude = (
        Fraction(significand << shift) if shift >= 0 else Fraction(significand, 1 << -shift)
    )
    return sign * magnitude


def _power_of_two(exponent: int) -> Fraction:
    return Fraction(1 << exponent) if exponent >= 0 else Fraction(1, 1 << -exponent)


def _floor_binary_exponent(value: Fraction) -> int:
    if value <= 0:
        raise ValueError("binary exponent requires a positive value")
    exponent = value.numerator.bit_length() - value.denominator.bit_length()
    return exponent - (value < _power_of_two(exponent))


def _round_integer_nearest_even(value: Fraction) -> int:
    if value < 0:
        return -_round_integer_nearest_even(-value)
    quotient, remainder = divmod(value.numerator, value.denominator)
    doubled = 2 * remainder
    return quotient + (
        doubled > value.denominator
        or (doubled == value.denominator and bool(quotient & 1))
    )


def _round_fraction(value: Fraction) -> int:
    if value == 0:
        return 0
    sign = 0x8000_0000 if value < 0 else 0
    magnitude = abs(value)
    if magnitude < _power_of_two(-126):
        significand = _round_integer_nearest_even(magnitude * (1 << 149))
        if not 0 <= significand <= 1 << 23:
            raise ValueError("subnormal rounding escaped binary32")
        return sign | significand
    exponent = _floor_binary_exponent(magnitude)
    if exponent > 127:
        raise OverflowError("value overflows finite binary32")
    significand = _round_integer_nearest_even(magnitude / _power_of_two(exponent - 23))
    if significand == 1 << 24:
        significand >>= 1
        exponent += 1
    if exponent > 127 or not 1 << 23 <= significand < 1 << 24:
        raise AssertionError("normal binary32 rounding invariant failed")
    return sign | ((exponent + 127) << 23) | (significand - (1 << 23))


def _ordered_key(bits: int) -> int:
    return (~bits & 0xFFFF_FFFF) if bits & 0x8000_0000 else bits | 0x8000_0000


def _bits_from_ordered_key(key: int) -> int:
    if not 0 <= key <= 0xFFFF_FFFF:
        raise ValueError("ordered binary32 key is outside uint32")
    return (~key & 0xFFFF_FFFF) if key < 0x8000_0000 else key & 0x7FFF_FFFF


@lru_cache(maxsize=None)
def _rounding_interval(bits: int) -> tuple[Fraction, Fraction]:
    if bits & 0x7F80_0000 == 0x7F80_0000:
        raise ValueError("rounding interval requires a finite binary32")
    key = _ordered_key(bits)
    value = _float_fraction(bits)
    previous_key = key - 1
    while _float_fraction(_bits_from_ordered_key(previous_key)) == value:
        previous_key -= 1
    following_key = key + 1
    while _float_fraction(_bits_from_ordered_key(following_key)) == value:
        following_key += 1
    previous = _float_fraction(_bits_from_ordered_key(previous_key))
    following = _float_fraction(_bits_from_ordered_key(following_key))
    return ((previous + value) / 2, (value + following) / 2)


def _first_float_key_at_or_above(value: Fraction) -> int:
    key = _ordered_key(_round_fraction(value))
    while _float_fraction(_bits_from_ordered_key(key)) < value:
        key += 1
    while key > 0:
        previous = key - 1
        if _float_fraction(_bits_from_ordered_key(previous)) < value:
            break
        key = previous
    return key


def _last_float_key_at_or_below(value: Fraction) -> int:
    key = _ordered_key(_round_fraction(value))
    while _float_fraction(_bits_from_ordered_key(key)) > value:
        key -= 1
    while key < 0xFFFF_FFFF:
        following = key + 1
        if _float_fraction(_bits_from_ordered_key(following)) > value:
            break
        key = following
    return key


def _read_plan_prefix() -> tuple[UInt32Array, str]:
    if PLAN.stat().st_size != PLAN_BYTES:
        raise ValueError("plan byte length differs")
    with PLAN.open("rb") as stream:
        prefix = stream.read(DISCOVERY_PLAN_BYTES)
    if len(prefix) != DISCOVERY_PLAN_BYTES:
        raise ValueError("plan discovery prefix is truncated")
    prefix_sha256 = hashlib.sha256(prefix).hexdigest()
    if prefix_sha256 != EXPECTED_PLAN_PREFIX_SHA256:
        raise ValueError("plan discovery-prefix SHA-256 differs")
    header = PLAN_HEADER.unpack_from(prefix)
    if header != (
        PLAN_MAGIC,
        PLAN_VERSION,
        PLAN_RECORD_COUNT,
        PLAN_RECORD.size // 4,
        PATTERN_COUNT,
    ):
        raise ValueError("plan header differs")
    records = np.frombuffer(prefix, dtype="<u4", offset=PLAN_HEADER.size).reshape(
        DISCOVERY_RECORD_COUNT, PLAN_RECORD.size // 4
    )
    return records.copy(), prefix_sha256


def _validate_plan(records: UInt32Array) -> tuple[UInt32Array, UInt32Array]:
    record_indices = np.arange(DISCOVERY_RECORD_COUNT, dtype=np.uint32)
    distances = np.repeat(np.arange(DISTANCE_COUNT, dtype=np.uint32), PATTERN_COUNT)
    patterns = np.tile(np.arange(PATTERN_COUNT, dtype=np.uint32), DISTANCE_COUNT)
    expected_outer_x = (
        np.float32(-64.0) - distances.astype(np.float32) / np.float32(256.0)
    ).view(np.uint32)
    constants = {
        1: np.zeros(DISCOVERY_RECORD_COUNT, dtype=np.uint32),
        2: patterns,
        3: distances,
        4: np.full(DISCOVERY_RECORD_COUNT, 256, dtype=np.uint32),
        5: np.zeros(DISCOVERY_RECORD_COUNT, dtype=np.uint32),
        6: np.full(DISCOVERY_RECORD_COUNT, 64, dtype=np.uint32),
        7: np.zeros(DISCOVERY_RECORD_COUNT, dtype=np.uint32),
        8: expected_outer_x,
        9: np.full(DISCOVERY_RECORD_COUNT, _float_bits(192.0), dtype=np.uint32),
        10: np.full(DISCOVERY_RECORD_COUNT, _float_bits(96.0), dtype=np.uint32),
        11: np.full(DISCOVERY_RECORD_COUNT, _float_bits(160.0), dtype=np.uint32),
        20: np.full(DISCOVERY_RECORD_COUNT, 141, dtype=np.uint32),
        21: np.full(DISCOVERY_RECORD_COUNT, 117, dtype=np.uint32),
    }
    if not np.array_equal(records[:, 0], record_indices):
        raise ValueError("discovery record indices differ")
    for column, expected in constants.items():
        if not np.array_equal(records[:, column], expected):
            raise ValueError(f"plan discovery column {column} differs")
    first_patterns = records[:PATTERN_COUNT]
    outer_bits = first_patterns[:, 12:16].copy()
    inner_bits = first_patterns[:, 16:20].copy()
    if not np.array_equal(records[:, 12:16], outer_bits[patterns]):
        raise ValueError("outer varying pattern repetition differs")
    if not np.array_equal(records[:, 16:20], inner_bits[patterns]):
        raise ValueError("inner varying pattern repetition differs")
    expected_fingerprints = np.array(
        [
            [0x00000000, 0x00000000, 0x00000000, 0x00000000],
            [0x3F800000, 0xBF800000, 0x35800000, 0xC9800000],
            [0x00000000, 0x3F800000, 0x49800000, 0xC9800000],
            [0x3F800001, 0xBF800001, 0x00800000, 0x80800000],
        ],
        dtype=np.uint32,
    )
    expected_fingerprint_inner = np.array(
        [
            [0x3F800000, 0xBF800000, 0x35800000, 0xC9800000],
            [0x00000000, 0x00000000, 0x00000000, 0x00000000],
            [0x3F800000, 0x40000000, 0x49800008, 0xC97FFFF0],
            [0xBF800001, 0x3F800001, 0x3F800000, 0xBF800000],
        ],
        dtype=np.uint32,
    )
    if not np.array_equal(outer_bits[4:], expected_fingerprints) or not np.array_equal(
        inner_bits[4:], expected_fingerprint_inner
    ):
        raise ValueError("algebraic fingerprint patterns differ")
    return outer_bits, inner_bits


def _read_raw_prefix() -> tuple[UInt32Array, str]:
    if RAW.stat().st_size != RAW_BYTES:
        raise ValueError("raw capture byte length differs")
    flat = np.fromfile(RAW, dtype="<u4", count=DISCOVERY_RAW_WORDS)
    if flat.size != DISCOVERY_RAW_WORDS:
        raise ValueError("raw discovery prefix is truncated")
    prefix_sha256 = _sha256_buffer(flat)
    if prefix_sha256 != EXPECTED_RAW_PREFIX_SHA256:
        raise ValueError("raw discovery-prefix SHA-256 differs")
    return flat.reshape(
        DISTANCE_COUNT, PATTERN_COUNT, RAW_VECTOR_COUNT, 4
    ), prefix_sha256


def _validate_raw(raw: UInt32Array) -> UInt32Array:
    record_indices = np.arange(DISCOVERY_RECORD_COUNT, dtype=np.uint32).reshape(
        DISTANCE_COUNT, PATTERN_COUNT
    )
    patterns = np.tile(np.arange(PATTERN_COUNT, dtype=np.uint32), (DISTANCE_COUNT, 1))
    distances = np.repeat(
        np.arange(DISTANCE_COUNT, dtype=np.uint32)[:, None], PATTERN_COUNT, axis=1
    )
    if (
        np.any(raw[:, :, 0, 0] != 141)
        or np.any(raw[:, :, 0, 1] != 117)
        or np.any(raw[:, :, 0, 2] != 0)
        or not np.array_equal(raw[:, :, 0, 3], record_indices)
        or not np.array_equal(raw[:, :, 1, 0], record_indices)
        or np.any(raw[:, :, 1, 1] != 0)
        or not np.array_equal(raw[:, :, 1, 2], patterns)
        or not np.array_equal(raw[:, :, 1, 3], distances)
        or np.any(raw[:, :, 2, 0] != 256)
        or np.any(raw[:, :, 2, 1] != 0)
        or np.any(raw[:, :, 2, 2] != 0)
        or np.any(raw[:, :, 2, 3] != 64)
    ):
        raise ValueError("raw discovery metadata differs")
    coefficient_bits = np.stack(
        [raw[:, :, start, :3] for start in COEFFICIENT_STARTS], axis=2
    )
    for start in COEFFICIENT_STARTS:
        if not np.array_equal(
            raw[:, :, start : start + COEFFICIENT_REPEAT_COUNT, :3],
            np.broadcast_to(
                raw[:, :, start : start + 1, :3],
                (DISTANCE_COUNT, PATTERN_COUNT, COEFFICIENT_REPEAT_COUNT, 3),
            ),
        ):
            raise ValueError(f"coefficient region {start} does not repeat")
    if np.any(coefficient_bits[:, :, :, 1] != 0):
        raise ValueError("discovery B coefficient is not exact positive zero")
    if np.any((coefficient_bits & 0x7F80_0000) == 0x7F80_0000):
        raise ValueError("discovery coefficient is non-finite")
    return coefficient_bits


def _derive_generated_bits(coefficient_bits: UInt32Array) -> UInt32Array:
    slopes = coefficient_bits[:, :, :, 0].view("<f4")
    constants = coefficient_bits[:, :, :, 2].view("<f4")
    generated = (
        constants.astype(np.float64) - np.float64(192.0) * slopes.astype(np.float64)
    ).astype(np.float32)
    return generated.view(np.uint32)


def _generated_candidate_interval(
    slope_bits: int, constant_bits: int, inner_bits: int
) -> tuple[Fraction, Fraction]:
    slope_lower, slope_upper = _rounding_interval(slope_bits)
    constant_lower, constant_upper = _rounding_interval(constant_bits)
    slope = _float_fraction(slope_bits)
    inner = _float_fraction(inner_bits)
    from_slope = (
        inner - 256 * slope_upper,
        inner - 256 * slope_lower,
    )
    from_constant = (
        constant_lower - 192 * slope,
        constant_upper - 192 * slope,
    )
    return (
        max(from_slope[0], from_constant[0]),
        min(from_slope[1], from_constant[1]),
    )


def _candidate_reforwards(
    candidate_bits: int, slope_bits: int, constant_bits: int, inner_bits: int
) -> bool:
    candidate = _float_fraction(candidate_bits)
    slope = _round_fraction((_float_fraction(inner_bits) - candidate) / 256)
    if slope != slope_bits:
        return False
    constant = _round_fraction(candidate + 192 * _float_fraction(slope_bits))
    return constant == constant_bits


def _second_reforwarding_candidate(
    first_key: int,
    last_key: int,
    compatible_bits: int,
    slope_bits: int,
    constant_bits: int,
    inner_bits: int,
) -> int | None:
    compatible_key = _ordered_key(compatible_bits)
    probes = [first_key, last_key]
    for offset in range(1, 17):
        probes.extend((compatible_key - offset, compatible_key + offset))
    seen = {compatible_bits}
    for key in probes:
        if not first_key <= key <= last_key:
            continue
        bits = _bits_from_ordered_key(key)
        if bits in seen:
            continue
        seen.add(bits)
        if _candidate_reforwards(bits, slope_bits, constant_bits, inner_bits):
            return bits
    if last_key - first_key <= 4_096:
        for key in range(first_key, last_key + 1):
            bits = _bits_from_ordered_key(key)
            if bits in seen:
                continue
            if _candidate_reforwards(bits, slope_bits, constant_bits, inner_bits):
                return bits
    return None


def _generated_value_uniqueness_gate(
    coefficient_bits: UInt32Array,
    compatible_generated_bits: UInt32Array,
    inner_bits: UInt32Array,
) -> tuple[JsonObject, NDArray[np.bool_]]:
    unique = np.zeros((DISTANCE_COUNT, PATTERN_COUNT, 4), dtype=np.bool_)
    records: list[JsonObject] = []
    expected_unique_counts = {
        4: [0, 0, 0, 0],
        5: [DISTANCE_COUNT] * 4,
        6: [0, DISTANCE_COUNT - 1, DISTANCE_COUNT, DISTANCE_COUNT],
        7: [DISTANCE_COUNT, DISTANCE_COUNT, 0, 0],
    }
    for pattern in range(4, 8):
        lane_records: list[JsonObject] = []
        for lane in range(4):
            ambiguous = 0
            unique_count = 0
            maximum_closed_span = 0
            first_ambiguous: list[int] = []
            for distance in range(DISTANCE_COUNT):
                slope_bits = int(coefficient_bits[distance, pattern, lane, 0])
                constant_bits = int(coefficient_bits[distance, pattern, lane, 2])
                lane_inner_bits = int(inner_bits[pattern, lane])
                compatible_bits = int(
                    compatible_generated_bits[distance, pattern, lane]
                )
                if not _candidate_reforwards(
                    compatible_bits,
                    slope_bits,
                    constant_bits,
                    lane_inner_bits,
                ):
                    raise ValueError(
                        "compatible generated candidate does not reforward"
                    )
                lower, upper = _generated_candidate_interval(
                    slope_bits, constant_bits, lane_inner_bits
                )
                if lower > upper:
                    raise ValueError("generated candidate interval is empty")
                first_key = _first_float_key_at_or_above(lower)
                last_key = _last_float_key_at_or_below(upper)
                if first_key > last_key:
                    raise ValueError(
                        "generated candidate interval contains no binary32"
                    )
                maximum_closed_span = max(maximum_closed_span, last_key - first_key + 1)
                if first_key == last_key:
                    if _bits_from_ordered_key(first_key) != compatible_bits:
                        raise ValueError("unique generated candidate differs")
                    unique[distance, pattern, lane] = True
                    unique_count += 1
                    continue
                second = _second_reforwarding_candidate(
                    first_key,
                    last_key,
                    compatible_bits,
                    slope_bits,
                    constant_bits,
                    lane_inner_bits,
                )
                if second is None:
                    if last_key - first_key <= 4_096:
                        unique[distance, pattern, lane] = True
                        unique_count += 1
                        continue
                    raise ValueError(
                        "closed interval is non-unique but a second exact candidate "
                        f"was not authenticated at pattern {pattern}, lane {lane}, "
                        f"distance {distance}, key span {last_key - first_key + 1}"
                    )
                ambiguous += 1
                if len(first_ambiguous) < 16:
                    first_ambiguous.append(distance)
            if unique_count != expected_unique_counts[pattern][lane]:
                raise ValueError(
                    f"pattern {pattern} lane {lane} uniqueness census differs"
                )
            lane_records.append(
                {
                    "lane": lane,
                    "uniqueGeneratedValueCount": unique_count,
                    "ambiguousGeneratedValueCount": ambiguous,
                    "maximumClosedIntervalFloatCount": maximum_closed_span,
                    "firstAmbiguousDistancesFixed": first_ambiguous,
                }
            )
        records.append({"patternIndex": pattern, "lanes": lane_records})
    return (
        {
            "method": (
                "Intersect the exact binary32 preimage intervals of observed A and C "
                "under A=round((inner-g)/256), C=round(g+192*A), then reforward "
                "boundary candidates exactly. A value is unique only when one "
                "binary32 reforwards; every reported ambiguity includes two values "
                "that exactly reforward."
            ),
            "patterns": records,
            "uniqueLanePolicy": (
                "Downstream factor tests use only observations with one authenticated "
                "generated binary32 value."
            ),
        },
        unique,
    )


def _algebraic_reforward_gate(
    coefficient_bits: UInt32Array,
    generated_bits: UInt32Array,
    inner_bits: UInt32Array,
) -> JsonObject:
    generated = generated_bits.view("<f4")
    inner = inner_bits.view("<f4")
    predicted_slope = (
        (inner[None, :, :].astype(np.float64) - generated.astype(np.float64))
        / np.float64(256.0)
    ).astype(np.float32)
    predicted_constant = (
        generated.astype(np.float64)
        + np.float64(192.0) * predicted_slope.astype(np.float64)
    ).astype(np.float32)
    per_pattern: list[JsonObject] = []
    for pattern in range(PATTERN_COUNT):
        slope_mismatches = int(
            np.count_nonzero(
                predicted_slope[:, pattern].view(np.uint32)
                != coefficient_bits[:, pattern, :, 0]
            )
        )
        constant_mismatches = int(
            np.count_nonzero(
                predicted_constant[:, pattern].view(np.uint32)
                != coefficient_bits[:, pattern, :, 2]
            )
        )
        per_pattern.append(
            {
                "patternIndex": pattern,
                "coefficientLaneCount": DISTANCE_COUNT * 4,
                "slopeMismatchCount": slope_mismatches,
                "constantMismatchCount": constant_mismatches,
            }
        )
    if any(
        per_pattern[index][field] != 0
        for index in range(4, 8)
        for field in ("slopeMismatchCount", "constantMismatchCount")
    ):
        raise ValueError("fingerprint generated-value inversion does not reforward")
    return {
        "compatibleCandidate": (
            "g = binary32(C - 192*A), evaluated in binary64 before binary32 "
            "materialization; this is one compatible candidate, not generally a "
            "unique inversion"
        ),
        "reforward": "A = binary32((inner-generated)/256); C = binary32(generated+192*A)",
        "perPattern": per_pattern,
        "fingerprintPatternCandidatesReforwardExactly": True,
        "legacyPattern3CandidateSlopeMismatchCount": int(
            per_pattern[3]["slopeMismatchCount"]  # type: ignore[arg-type]
        ),
    }


def _signed_power_of_two_homogeneity(generated_bits: UInt32Array) -> JsonObject:
    generated = generated_bits.view("<f4")
    pattern = 5
    base = generated[:, pattern, 0]
    base_bits = base.view(np.uint32)
    expected = (
        base_bits ^ np.uint32(0x8000_0000),
        np.ldexp(base, -20).astype(np.float32).view(np.uint32),
        np.ldexp(-base.astype(np.float64), 20).astype(np.float32).view(np.uint32),
    )
    mismatches = [
        int(np.count_nonzero(generated_bits[:, pattern, lane] != expected[lane - 1]))
        for lane in range(1, 4)
    ]
    record: JsonObject = {
        "pattern": "scale-to-zero",
        "comparisonCount": DISTANCE_COUNT * 3,
        "negativeMismatchCount": mismatches[0],
        "smallPowerOfTwoMismatchCount": mismatches[1],
        "negativeLargePowerOfTwoMismatchCount": mismatches[2],
    }
    return {
        "record": record,
        "allPowerOfTwoAndSignComparisonsExact": all(value == 0 for value in mismatches),
        "scope": (
            "Pattern 5 is used because every lane has a uniquely authenticated "
            "generated value; pattern 4 is intentionally excluded as ambiguous."
        ),
    }


def _ulp_distribution(predicted: UInt32Array, observed: UInt32Array) -> JsonObject:
    differences = predicted.astype(np.int64) - observed.astype(np.int64)
    counts = Counter(int(value) for value in differences)
    return {
        "matchCount": int(np.count_nonzero(differences == 0)),
        "mismatchCount": int(np.count_nonzero(differences != 0)),
        "predictedMinusObservedFloatUlpDistribution": {
            str(offset): count for offset, count in sorted(counts.items())
        },
    }


def _fast_reciprocal(bits: int, deltas: bytes) -> int:
    value = _float_fraction(bits)
    if value <= 0:
        raise ValueError("fast reciprocal requires a positive value")
    reciprocal = _round_fraction(1 / value)
    correction = struct.unpack_from("<b", deltas, bits & 0x007F_FFFF)[0]
    result = reciprocal + correction
    if result & 0x7F80_0000 == 0x7F80_0000:
        raise ValueError("fast reciprocal escaped the finite range")
    return result


def _normalized_p25_key(value: int) -> int:
    exponent = value.bit_length() - 1
    if exponent <= 24:
        return value << (24 - exponent)
    shift = exponent - 24
    quotient, remainder = divmod(value, 1 << shift)
    return quotient + (remainder >= 1 << (shift - 1))


def _p25_factor_bits(denominator: int, bitmap: bytes) -> int:
    key = _normalized_p25_key(denominator)
    exponent = -(denominator - 1).bit_length() - 8
    if denominator & (denominator - 1) == 0 or key == 1 << 25:
        selector = 1 << 24
    else:
        if not 1 << 24 <= key < 1 << 25:
            raise ValueError("P25 key escaped the calibrated interval")
        floor, remainder = divmod(1 << 49, key)
        bit_index = key - (1 << 24)
        ceil = bool((bitmap[bit_index >> 3] >> (bit_index & 7)) & 1)
        selector = floor + (ceil and remainder != 0)
    return _round_fraction(Fraction(selector) * _power_of_two(exponent))


def _endpoint_weight_models(generated_bits: UInt32Array) -> JsonObject:
    retained = generated_bits[:, 5, 0]
    exact_retained = np.array(
        [
            _round_fraction(Fraction(CLIP_SPAN_FIXED, CLIP_SPAN_FIXED + distance))
            for distance in range(DISTANCE_COUNT)
        ],
        dtype=np.uint32,
    )
    reciprocal_deltas = FAST_RECIPROCAL_DELTAS.read_bytes()
    fast_retained: list[int] = []
    for distance in range(DISTANCE_COUNT):
        denominator = _round_fraction(Fraction(2) + Fraction(distance, 32_768))
        reciprocal = _fast_reciprocal(denominator, reciprocal_deltas)
        fast_retained.append(_round_fraction(2 * _float_fraction(reciprocal)))
    p25_bitmap = P25_BITMAP.read_bytes()
    p25_retained = np.array(
        [
            _p25_factor_bits(CLIP_SPAN_FIXED + distance, p25_bitmap)
            for distance in range(DISTANCE_COUNT)
        ],
        dtype=np.uint32,
    )
    return {
        "retainedWeight": {
            "exactRationalRne": _ulp_distribution(exact_retained, retained),
            "appleFastReciprocal": _ulp_distribution(
                np.array(fast_retained, dtype=np.uint32), retained
            ),
            "p25FixedGridSelector": _ulp_distribution(p25_retained, retained),
        },
        "classification": (
            "The uniquely invertible retained response remains within a narrow "
            "arithmetic neighborhood, but none of exact division, Apple shader fast "
            "reciprocal, or P25 fixed-grid selection is the clipper law. The ambiguous "
            "zero-to-scale response is not interpreted as a removed weight."
        ),
    }


def _factor_interval_gate(generated_bits: UInt32Array) -> JsonObject:
    retained = generated_bits[:, 5, 0]
    translated = generated_bits[:, 6, 1]
    cancellation_positive = generated_bits[:, 7, 0]
    cancellation_negative = generated_bits[:, 7, 1]
    cancellation_scale = _float_fraction(0x3F80_0001)
    two_response_empty = 0
    all_response_empty = 0
    exact_inside_two_response = 0
    exact_inside_all_response = 0
    first_two_response_empty: list[int] = []
    first_all_response_empty: list[int] = []
    for distance in range(1, DISTANCE_COUNT):
        retained_interval = _rounding_interval(int(retained[distance]))
        translated_interval = _rounding_interval(int(translated[distance]))
        lower = max(1 - retained_interval[1], translated_interval[0] - 1)
        upper = min(1 - retained_interval[0], translated_interval[1] - 1)
        exact = Fraction(distance, CLIP_SPAN_FIXED + distance)
        if lower > upper:
            two_response_empty += 1
            if len(first_two_response_empty) < 16:
                first_two_response_empty.append(distance)
        else:
            exact_inside_two_response += lower <= exact <= upper

        positive_interval = _rounding_interval(int(cancellation_positive[distance]))
        negative_interval = _rounding_interval(int(cancellation_negative[distance]))
        positive_lower = (1 - positive_interval[1] / cancellation_scale) / 2
        positive_upper = (1 - positive_interval[0] / cancellation_scale) / 2
        negative_lower = (negative_interval[0] / cancellation_scale + 1) / 2
        negative_upper = (negative_interval[1] / cancellation_scale + 1) / 2
        all_lower = max(lower, positive_lower, negative_lower)
        all_upper = min(upper, positive_upper, negative_upper)
        if all_lower > all_upper:
            all_response_empty += 1
            if len(first_all_response_empty) < 16:
                first_all_response_empty.append(distance)
        else:
            exact_inside_all_response += all_lower <= exact <= all_upper

    if (two_response_empty, exact_inside_two_response) != (1_021, 6_153):
        raise ValueError("two-response factor census differs")
    if (all_response_empty, exact_inside_all_response) != (1_870, 3_548):
        raise ValueError("four-response factor census differs")
    nonzero_count = DISTANCE_COUNT - 1
    return {
        "nonzeroDistanceCount": nonzero_count,
        "twoResponseConstraints": {
            "constraints": [
                "pattern5 lane0 = round(1-t)",
                "pattern6 lane1 = round(1+t)",
            ],
            "emptyRealFactorIntersectionCount": two_response_empty,
            "nonemptyRealFactorIntersectionCount": nonzero_count - two_response_empty,
            "exactRationalFactorInsideNonemptyIntersectionCount": (
                exact_inside_two_response
            ),
            "firstEmptyDistancesFixed": first_two_response_empty,
        },
        "fourResponseConstraints": {
            "constraints": [
                "pattern5 lane0 = round(1-t)",
                "pattern6 lane1 = round(1+t)",
                "pattern7 lane0 = round(a*(1-2*t))",
                "pattern7 lane1 = round(a*(2*t-1)); a=nextafter(1,+inf)",
            ],
            "emptyRealFactorIntersectionCount": all_response_empty,
            "nonemptyRealFactorIntersectionCount": nonzero_count - all_response_empty,
            "exactRationalFactorInsideNonemptyIntersectionCount": (
                exact_inside_all_response
            ),
            "firstEmptyDistancesFixed": first_all_response_empty,
        },
        "commonRealFactorRejected": all_response_empty > 0,
        "conclusion": (
            "Even the uniquely invertible responses cannot all be one shared real clip "
            "factor followed only by final binary32 rounding. The fixed-function path "
            "contains endpoint/product-dependent staged arithmetic, or an exactly "
            "equivalent multi-response transform."
        ),
    }


def _exact_affine_model_gate(
    generated_bits: UInt32Array,
    unique: NDArray[np.bool_],
    outer_bits: UInt32Array,
    inner_bits: UInt32Array,
) -> JsonObject:
    lane_ranges = {
        5: {
            0: range(DISTANCE_COUNT),
            1: range(DISTANCE_COUNT),
            2: range(DISTANCE_COUNT),
            3: range(DISTANCE_COUNT),
        },
        6: {
            1: range(1, DISTANCE_COUNT),
            2: range(DISTANCE_COUNT),
            3: range(DISTANCE_COUNT),
        },
        7: {0: range(DISTANCE_COUNT), 1: range(DISTANCE_COUNT)},
    }
    pattern_records: list[JsonObject] = []
    total_values = 0
    total_mismatches = 0
    for pattern, lanes in lane_ranges.items():
        lane_records: list[JsonObject] = []
        for lane, distances in lanes.items():
            mismatch_count = 0
            first_mismatches: list[int] = []
            value_count = 0
            outer = _float_fraction(int(outer_bits[pattern, lane]))
            inner = _float_fraction(int(inner_bits[pattern, lane]))
            for distance in distances:
                if not unique[distance, pattern, lane]:
                    raise ValueError(
                        "exact affine gate received an ambiguous observation"
                    )
                factor = Fraction(distance, CLIP_SPAN_FIXED + distance)
                predicted = _round_fraction(outer * (1 - factor) + inner * factor)
                observed = int(generated_bits[distance, pattern, lane])
                value_count += 1
                if predicted != observed:
                    mismatch_count += 1
                    if len(first_mismatches) < 16:
                        first_mismatches.append(distance)
            total_values += value_count
            total_mismatches += mismatch_count
            lane_records.append(
                {
                    "lane": lane,
                    "valueCount": value_count,
                    "mismatchCount": mismatch_count,
                    "firstMismatchDistancesFixed": first_mismatches,
                }
            )
        pattern_records.append({"patternIndex": pattern, "lanes": lane_records})
    return {
        "model": "round(outer*(1-t)+inner*t), t=d/(65536+d), one exact final RNE",
        "valueCount": total_values,
        "mismatchCount": total_mismatches,
        "patterns": pattern_records,
        "singleExactAffineEvaluationRejected": total_mismatches > 0,
    }


def _load_discovery() -> tuple[Discovery, JsonObject]:
    verified = _check_small_inputs()
    manifests = _authenticate_manifests()
    plan, plan_prefix_sha256 = _read_plan_prefix()
    outer_bits, inner_bits = _validate_plan(plan)
    raw, raw_prefix_sha256 = _read_raw_prefix()
    coefficient_bits = _validate_raw(raw)
    generated_bits = _derive_generated_bits(coefficient_bits)
    return (
        Discovery(
            plan=plan,
            raw=raw,
            outer_bits=outer_bits,
            inner_bits=inner_bits,
            coefficient_bits=coefficient_bits,
            generated_bits=generated_bits,
        ),
        {
            "verifiedSmallInputs": verified,
            "manifests": manifests,
            "boundedRead": {
                "planPrefixBytesRead": DISCOVERY_PLAN_BYTES,
                "planPrefixSha256": plan_prefix_sha256,
                "planHoldoutBytesRead": 0,
                "rawPrefixBytesRead": DISCOVERY_RAW_BYTES,
                "rawPrefixSha256": raw_prefix_sha256,
                "rawHoldoutBytesRead": 0,
                "fullPlanBytes": PLAN_BYTES,
                "fullRawBytes": RAW_BYTES,
                "fullPlanSha256NotRecomputedBecauseHoldoutIsSealed": (
                    EXPECTED_PLAN_FULL_SHA256
                ),
                "fullRawSha256NotRecomputedBecauseHoldoutIsSealed": (
                    EXPECTED_RAW_FULL_SHA256
                ),
            },
        },
    )


def analyze() -> JsonObject:
    discovery, authentication = _load_discovery()
    algebraic = _algebraic_reforward_gate(
        discovery.coefficient_bits,
        discovery.generated_bits,
        discovery.inner_bits,
    )
    uniqueness, unique = _generated_value_uniqueness_gate(
        discovery.coefficient_bits,
        discovery.generated_bits,
        discovery.inner_bits,
    )
    return {
        "schemaVersion": 1,
        "classification": "bounded output-blind AGX clip-weight discovery analysis",
        "authority": {
            "referencePixelsRead": False,
            "holdoutPlanBytesRead": 0,
            "holdoutRawBytesRead": 0,
            "completeInputOnlyClipSetupLawRecovered": False,
            "holdoutOpeningAuthorized": False,
            "productionWalleMutationAuthorized": False,
        },
        "authentication": authentication,
        "discovery": {
            "groupIndex": 0,
            "distanceFixedInclusive": [0, DISTANCE_COUNT - 1],
            "distanceStepPixels": 1 / 256,
            "patternCount": PATTERN_COUNT,
            "recordCount": DISCOVERY_RECORD_COUNT,
            "coefficientTripleCount": (
                DISCOVERY_RECORD_COUNT * len(COEFFICIENT_STARTS)
            ),
            "coefficientWordCount": (
                DISCOVERY_RECORD_COUNT * len(COEFFICIENT_STARTS) * 3
            ),
            "repeatedCoefficientWordComparisonCount": (
                DISCOVERY_RECORD_COUNT
                * len(COEFFICIENT_STARTS)
                * (COEFFICIENT_REPEAT_COUNT - 1)
                * 3
            ),
            "allCoefficientTriplesFinite": True,
            "allCoefficientBWordsPositiveZero": True,
            "allCoefficientRegionsRepeatExactly": True,
        },
        "compatibleGeneratedValue": algebraic,
        "generatedValueUniqueness": uniqueness,
        "homogeneity": _signed_power_of_two_homogeneity(discovery.generated_bits),
        "endpointWeightModels": _endpoint_weight_models(discovery.generated_bits),
        "singleFactorGate": _factor_interval_gate(discovery.generated_bits),
        "exactAffineModelGate": _exact_affine_model_gate(
            discovery.generated_bits,
            unique,
            discovery.outer_bits,
            discovery.inner_bits,
        ),
        "currentConclusion": {
            "established": [
                "The discovery prefix is authenticated and bounded; no holdout byte was opened.",
                "The four exported (A,B,C) triples repeat exactly and B is exact positive zero.",
                "For every algebraic fingerprint lane, a compatible binary32 generated-value candidate reforwards to the observed A and C words under the authenticated ordinary setup seam.",
                "Exact rounding-preimage intersection identifies which fingerprint lanes have one possible generated binary32 and proves the others ambiguous with two exact reforwarding candidates.",
                "The uniquely invertible scale-to-zero response is exactly homogeneous under sign and powers of two.",
                "No single real factor followed only by final binary32 rounding explains all uniquely invertible responses at every distance.",
            ],
            "notEstablished": [
                "A unique generated value for the deliberately ambiguous fingerprint lanes.",
                "The internal clip reciprocal, product precision, addition order, and rounding points.",
                "Any complete input-only coefficient law.",
                "Any holdout or production authority.",
            ],
            "nextDiscriminator": (
                "Fit staged two-endpoint numerator and reciprocal-product families directly "
                "to all four coefficient lanes, using the basis patterns to constrain each "
                "rounding stage. Freeze one all-discovery exact law before opening holdout."
            ),
        },
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=OUTPUT)
    return parser.parse_args()


def main() -> None:
    arguments = _parse_args()
    report = analyze()
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(report, indent=2, sort_keys=True) + "\n"
    arguments.output.write_text(encoded, encoding="utf-8")
    print(encoded, end="")


if __name__ == "__main__":
    main()
