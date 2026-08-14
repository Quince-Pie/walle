#!/usr/bin/env python3
"""Separate direct AGX clip endpoint mixing from built-in guard setup.

Two output-blind Metal captures use identical endpoint values and distance
indices.  One submits an explicit ``[[clip_distance]]`` whose generated edge
lies on an AGX coefficient-tile origin.  Its exported C coefficient is
therefore the generated endpoint value itself.  The other uses AGX's built-in
viewport guard; its generated value is recoverable only when the observed A/C
coefficient pair has one binary32 value that exactly reforwards through the
already measured right-triangle setup equations.

The analysis never opens a reference image.  It tests the direct endpoint
against the independently captured exhaustive reciprocal and 24/18/17
partial-product law, then compares that result with every uniquely invertible
built-in observation.  A difference in the latter comparison is deliberately
classified as a combined built-in generated-varying/setup boundary: the
coefficient export cannot identify which side of that boundary rounded.
"""

import argparse
import hashlib
import json
import struct
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import numpy as np
from numpy.typing import NDArray


ROOT: Final = Path(__file__).resolve().parent.parent
ANALYSIS: Final = ROOT / "analysis"
sys.path.insert(0, str(ANALYSIS))

import analyze_reveal_agx_clip_weight_tomography as weight  # noqa: E402
import analyze_reveal_agx_direct_clip_reciprocal as reciprocal  # noqa: E402


type JsonValue = (
    None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]
)
type JsonObject = dict[str, JsonValue]
type UInt32Array = NDArray[np.uint32]

CAPTURE_ROOT: Final = ROOT / "build" / "analysis-agx-direct-user-clip"
DIRECT_ROOT: Final = CAPTURE_ROOT / "endpoint-isolation-capture"
BUILTIN_ROOT: Final = CAPTURE_ROOT / "builtin-endpoint-isolation-capture"
RECIPROCAL_TABLE: Final = (
    CAPTURE_ROOT / "exhaustive-reciprocal" / "reveal-agx-direct-clip-reciprocal-u32.bin"
)
DEFAULT_OUTPUT: Final = (
    CAPTURE_ROOT
    / "endpoint-isolation-analysis"
    / "reveal-agx-endpoint-isolation-result.json"
)

RECORD_COUNT: Final = 83_872
DISTANCE_COUNT: Final = 8_193
PATTERN_COUNT: Final = 8
DISCOVERY_RECORD_COUNT: Final = DISTANCE_COUNT * PATTERN_COUNT
VECTOR_COUNT: Final = 101
VECTOR_WORDS: Final = 4
RECORD_WORDS: Final = VECTOR_COUNT * VECTOR_WORDS
COEFFICIENT_STARTS: Final = (5, 21, 37, 53)
COEFFICIENT_REPEAT_COUNT: Final = 16
LANE_COUNT: Final = 4
RECIPROCAL_ENTRY_COUNT: Final = 1 << 23
RECIPROCAL_INDEX_STRIDE: Final = 128
RETAINED_EXPONENT_SHIFT: Final = 16
BUILTIN_INNER_X: Final = 192
BUILTIN_HORIZONTAL_SPAN: Final = 256


@dataclass(frozen=True, slots=True, kw_only=True)
class CaptureSpec:
    name: str
    root: Path
    raw_sha256: str
    manifest_sha256: str
    source_sha256: str
    executable_sha256: str
    stderr_sha256: str
    patch: Path
    patch_sha256: str
    primitive_id: int


@dataclass(frozen=True, slots=True, kw_only=True)
class Capture:
    spec: CaptureSpec
    coefficient_bits: UInt32Array


DIRECT_PATCH: Final = (
    ANALYSIS / "reveal_agx_direct_user_clip_endpoint_isolation_experiment.patch"
)
BUILTIN_PATCH: Final = (
    ANALYSIS / "reveal_agx_builtin_guard_endpoint_isolation_experiment.patch"
)

CAPTURE_SPECS: Final = (
    CaptureSpec(
        name="direct-user-clip",
        root=DIRECT_ROOT,
        raw_sha256="0d7283434abdead6f98611f2d7edc11c811770ff0e358cf4017a88c771f8ce61",
        manifest_sha256="12dbe5ef085d5f102c2ffdfef356316e3ef81adbdc21868b3150571da0cd111d",
        source_sha256="650e6fc7b1fc9b324c2899b0dbc250c99a95e2b320c051bcabaeaadbfe0deb3a",
        executable_sha256="d79407d87646d08fb4815d8a52addcbcce95cc31ebb6c7ad470506c1d27cab67",
        stderr_sha256="57403e966bb7ffa3af141fc25184dd1bfaeb73363c9b0382b4f83b39f9b7604d",
        patch=DIRECT_PATCH,
        patch_sha256="91275d572366bb2b8ea6150c68ff27d0965d161498c226a8ef9cb03cb5ae7dcd",
        primitive_id=1,
    ),
    CaptureSpec(
        name="builtin-guard",
        root=BUILTIN_ROOT,
        raw_sha256="64623025a0ec52ac29fba468cdcb5ca988fd385677665af3491f861263d82fd7",
        manifest_sha256="f6082fe0f6e88183e28414bb04c7d858b8c60ad3da897fead1e018f1f695b38d",
        source_sha256="4ec12dd8c9583ea0831fe991c4fb365092e4ccb2a50adcc530fdaa870d33d494",
        executable_sha256="80fdb1dcd1d22c6fa583c400a99d88b5e62dc7a2cd5cb7baa2175f42aab8915d",
        stderr_sha256="f8172f1ae927d523ccf94d74f21eac2711acd557cd0b6e9121275392bfc3e7b6",
        patch=BUILTIN_PATCH,
        patch_sha256="2747a1f6766f0f1bd4b0be354371f246862540a8f44c5b710f865b186db0b8c9",
        primitive_id=0,
    ),
)

EXPECTED_DEPENDENCIES: Final = {
    ANALYSIS
    / "analyze_reveal_agx_clip_weight_tomography.py": "95aec239d9fb11040ff02ffc318a7a823c5bbbc23bf87f7a76bfb9160a531b63",
    ANALYSIS
    / "analyze_reveal_agx_direct_clip_reciprocal.py": "810e94394a6026d9174cda8d9c99594cc0985c2ce0ee77bdd6a5fd86656e399b",
    RECIPROCAL_TABLE: "7381fe62080a7187016d3f32299ea93fbbbe9d974ad8338033c5d161be25720b",
}

PLAN_SHA256: Final = "f90e4e3b5f0d46b0fb8250c97aa40eb4ddb32c67050c9823feaecb0a20baaf8d"
PREREGISTRATION_SHA256: Final = (
    "020e98dea95357ccfeb3e796b0b1e8d68d1d9de74e32c81ef199f4c74090fb12"
)
PROGRESS: Final = "clip-weight-tomography: group 1/5, records 65544/83872\n"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(4 * 1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _sha256_array(value: NDArray[np.generic]) -> str:
    contiguous = np.ascontiguousarray(value)
    return hashlib.sha256(memoryview(contiguous).cast("B")).hexdigest()


def _hex32(value: int) -> str:
    return f"0x{value:08x}"


def _float_bits(value: float) -> int:
    return struct.unpack("<I", struct.pack("<f", value))[0]


def _pattern_endpoints() -> tuple[UInt32Array, UInt32Array]:
    a = 0x3F80_0001
    b = 0x3F7F_FFFF
    small = 0x3580_0001
    large = 0x4980_0001
    outer = np.array(
        [
            [a, 0, a ^ 0x8000_0000, 0],
            [b, 0, b ^ 0x8000_0000, 0],
            [small, 0, small ^ 0x8000_0000, 0],
            [large, 0, large ^ 0x8000_0000, 0],
            [a, a ^ 0x8000_0000, b, b ^ 0x8000_0000],
            [small, small ^ 0x8000_0000, large, large ^ 0x8000_0000],
            [a, b, a ^ 0x8000_0000, b ^ 0x8000_0000],
            [0x3E80_0000, 0xBE80_0000, 0x4480_0000, 0xC480_0000],
        ],
        dtype=np.uint32,
    )
    inner = np.array(
        [
            [0, a, 0, a ^ 0x8000_0000],
            [0, b, 0, b ^ 0x8000_0000],
            [0, small, 0, small ^ 0x8000_0000],
            [0, large, 0, large ^ 0x8000_0000],
            [a ^ 0x8000_0000, a, b ^ 0x8000_0000, b],
            [small ^ 0x8000_0000, small, large ^ 0x8000_0000, large],
            [b, a, b ^ 0x8000_0000, a ^ 0x8000_0000],
            [0x3F40_0000, 0xBF40_0000, 0x4480_2000, 0xC480_2000],
        ],
        dtype=np.uint32,
    )
    return outer, inner


def _verify_dependency_hashes() -> list[JsonObject]:
    inputs: list[JsonObject] = []
    expected = dict(EXPECTED_DEPENDENCIES)
    for spec in CAPTURE_SPECS:
        expected[spec.patch] = spec.patch_sha256
    for path, wanted in expected.items():
        actual = _sha256(path)
        if actual != wanted:
            raise ValueError(f"SHA-256 differs for {path.relative_to(ROOT)}")
        inputs.append(
            {
                "path": str(path.relative_to(ROOT)),
                "bytes": path.stat().st_size,
                "sha256": actual,
            }
        )
    return inputs


def _load_capture(spec: CaptureSpec) -> tuple[Capture, JsonObject]:
    raw_path = spec.root / "reveal-agx-clip-weight-tomography.raw"
    manifest_path = spec.root / "manifest.json"
    stdout_path = spec.root / (
        "endpoint-isolation.stdout"
        if spec.name == "direct-user-clip"
        else "builtin-endpoint-isolation.stdout"
    )
    stderr_path = spec.root / (
        "endpoint-isolation.stderr"
        if spec.name == "direct-user-clip"
        else "builtin-endpoint-isolation.stderr"
    )
    source_path = next(spec.root.glob("*.swift"))
    expected_hashes = {
        raw_path: spec.raw_sha256,
        manifest_path: spec.manifest_sha256,
        source_path: spec.source_sha256,
        stderr_path: spec.stderr_sha256,
    }
    verified: list[JsonObject] = []
    for path, wanted in expected_hashes.items():
        actual = _sha256(path)
        if actual != wanted:
            raise ValueError(f"{spec.name} SHA-256 differs for {path.name}")
        verified.append(
            {
                "path": str(path.relative_to(ROOT)),
                "bytes": path.stat().st_size,
                "sha256": actual,
            }
        )
    if stdout_path.read_text(encoding="utf-8") != PROGRESS:
        raise ValueError(f"{spec.name} completion output differs")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError(f"{spec.name} manifest is not an object")
    capture = manifest.get("capture")
    executable = manifest.get("executable")
    plan = manifest.get("plan")
    preregistration = manifest.get("preregistration")
    authority = manifest.get("authority")
    if not all(
        isinstance(value, dict)
        for value in (capture, executable, plan, preregistration, authority)
    ):
        raise ValueError(f"{spec.name} manifest shape differs")
    assert isinstance(capture, dict)
    assert isinstance(executable, dict)
    assert isinstance(plan, dict)
    assert isinstance(preregistration, dict)
    assert isinstance(authority, dict)
    if (
        manifest.get("schema") != "walle-reveal-agx-clip-weight-tomography-capture-v1"
        or capture.get("sha256") != spec.raw_sha256
        or capture.get("bytes") != RECORD_COUNT * RECORD_WORDS * 4
        or capture.get("recordCount") != RECORD_COUNT
        or capture.get("recordBytes") != RECORD_WORDS * 4
        or capture.get("recordVectorCount") != VECTOR_COUNT
        or capture.get("coefficientExportRegions") != list(COEFFICIENT_STARTS)
        or executable.get("sha256") != spec.executable_sha256
        or plan.get("sha256") != PLAN_SHA256
        or preregistration.get("sha256") != PREREGISTRATION_SHA256
        or authority
        != {
            "establishesClipSetupLaw": False,
            "mutatesProductionRenderer": False,
            "observedCoefficientsReadBeforePlanFreeze": False,
            "opensReferencePixels": False,
            "usesPublicClipInputsOnly": True,
        }
    ):
        raise ValueError(f"{spec.name} manifest contents differ")

    raw = np.memmap(
        raw_path,
        dtype="<u4",
        mode="r",
        shape=(RECORD_COUNT, VECTOR_COUNT, VECTOR_WORDS),
    )[:DISCOVERY_RECORD_COUNT].reshape(
        DISTANCE_COUNT, PATTERN_COUNT, VECTOR_COUNT, VECTOR_WORDS
    )
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
        or np.any(raw[:, :, 0, 2] != spec.primitive_id)
        or not np.array_equal(raw[:, :, 0, 3], record_indices)
        or not np.array_equal(raw[:, :, 1, 0], record_indices)
        or not np.array_equal(raw[:, :, 1, 2], patterns)
        or not np.array_equal(raw[:, :, 1, 3], distances)
    ):
        raise ValueError(f"{spec.name} discovery metadata differs")
    coefficient_bits = np.stack(
        [raw[:, :, start, :3] for start in COEFFICIENT_STARTS], axis=2
    ).copy()
    for start in COEFFICIENT_STARTS:
        expected = np.broadcast_to(
            raw[:, :, start : start + 1, :3],
            (DISTANCE_COUNT, PATTERN_COUNT, COEFFICIENT_REPEAT_COUNT, 3),
        )
        if not np.array_equal(
            raw[:, :, start : start + COEFFICIENT_REPEAT_COUNT, :3], expected
        ):
            raise ValueError(f"{spec.name} coefficient repetition differs")
    if np.any(coefficient_bits[:, :, :, 1] != 0):
        raise ValueError(f"{spec.name} B coefficient is not positive zero")
    if np.any((coefficient_bits & np.uint32(0x7F80_0000)) == 0x7F80_0000):
        raise ValueError(f"{spec.name} coefficient is non-finite")
    return (
        Capture(spec=spec, coefficient_bits=coefficient_bits),
        {
            "name": spec.name,
            "verifiedInputs": verified,
            "discoveryRecordCount": DISCOVERY_RECORD_COUNT,
            "coefficientTripleCount": int(
                coefficient_bits.shape[0]
                * coefficient_bits.shape[1]
                * coefficient_bits.shape[2]
            ),
            "coefficientWordCount": int(coefficient_bits.size),
            "referencePixelsRead": False,
        },
    )


def _scale_positive_power_of_two(bits: int, exponent_delta: int) -> int:
    exponent = (bits >> 23) & 0xFF
    shifted = exponent + exponent_delta
    if bits & 0x8000_0000 or exponent in {0, 0xFF} or not 1 <= shifted < 0xFF:
        raise ValueError("reciprocal is outside the measured positive normal domain")
    return (bits & 0x807F_FFFF) | (shifted << 23)


def _signed_product_bits(factor_bits: int, value_bits: int) -> int:
    sign = value_bits & 0x8000_0000
    magnitude = value_bits & 0x7FFF_FFFF
    if factor_bits == 0 or magnitude == 0:
        return sign
    return reciprocal.truncated_product_bits(factor_bits, magnitude) ^ sign


def _add_bits(left_bits: int, right_bits: int) -> int:
    return weight._round_fraction(  # noqa: SLF001
        weight._float_fraction(left_bits)  # noqa: SLF001
        + weight._float_fraction(right_bits)  # noqa: SLF001
    )


def _predict_direct_values(table: UInt32Array) -> UInt32Array:
    outer, inner = _pattern_endpoints()
    predicted = np.empty((DISTANCE_COUNT, PATTERN_COUNT, LANE_COUNT), dtype=np.uint32)
    for distance in range(DISTANCE_COUNT):
        reciprocal_bits = int(table[distance * RECIPROCAL_INDEX_STRIDE])
        retained_bits = _scale_positive_power_of_two(
            reciprocal_bits, RETAINED_EXPONENT_SHIFT
        )
        removed_bits = (
            0
            if distance == 0
            else reciprocal.truncated_product_bits(
                reciprocal_bits, _float_bits(float(distance))
            )
        )
        for pattern in range(PATTERN_COUNT):
            for lane in range(LANE_COUNT):
                retained_term = _signed_product_bits(
                    retained_bits, int(outer[pattern, lane])
                )
                removed_term = _signed_product_bits(
                    removed_bits, int(inner[pattern, lane])
                )
                predicted[distance, pattern, lane] = _add_bits(
                    retained_term, removed_term
                )
    return predicted


def _direct_gate(capture: Capture, predicted: UInt32Array) -> JsonObject:
    observed = capture.coefficient_bits[:, :, :, 2]
    if observed.shape != predicted.shape:
        raise AssertionError("direct observed/predicted shape differs")
    pattern_records: list[JsonObject] = []
    for pattern in range(PATTERN_COUNT):
        differences = observed[:, pattern] != predicted[:, pattern]
        pattern_records.append(
            {
                "pattern": pattern,
                "comparisonCount": int(differences.size),
                "mismatchCount": int(np.count_nonzero(differences)),
            }
        )
    mismatch_count = int(np.count_nonzero(observed != predicted))
    if mismatch_count != 0:
        raise ValueError("direct endpoint model differs")
    return {
        "coefficientInterpretation": (
            "The explicit clip plane is screen x=128, the origin of its 32-pixel "
            "coefficient tile, so exported C is the generated endpoint value."
        ),
        "model": (
            "round(add(product24_18_17(retained, outer), "
            "product24_18_17(removed, inner))); retained=directReciprocal*2^16; "
            "removed=product24_18_17(directReciprocal, distance)"
        ),
        "comparisonCount": int(observed.size),
        "mismatchCount": mismatch_count,
        "predictedBitsSha256": _sha256_array(predicted),
        "observedBitsSha256": _sha256_array(observed),
        "patterns": pattern_records,
        "allDirectEndpointValuesExact": True,
    }


def _compatible_builtin_value(
    slope_bits: int, constant_bits: int, inner_bits: int
) -> tuple[int | None, int]:
    lower, upper = weight._generated_candidate_interval(  # noqa: SLF001
        slope_bits, constant_bits, inner_bits
    )
    if lower > upper:
        raise ValueError("built-in coefficient interval is empty")
    first = weight._first_float_key_at_or_above(lower)  # noqa: SLF001
    last = weight._last_float_key_at_or_below(upper)  # noqa: SLF001
    if first > last:
        raise ValueError("built-in coefficient interval contains no binary32")
    candidates: list[int] = []
    for key in range(first, last + 1):
        bits = weight._bits_from_ordered_key(key)  # noqa: SLF001
        if weight._candidate_reforwards(  # noqa: SLF001
            bits, slope_bits, constant_bits, inner_bits
        ):
            candidates.append(bits)
            if len(candidates) > 1:
                return None, last - first + 1
    if len(candidates) != 1:
        return None, last - first + 1
    return candidates[0], last - first + 1


def _builtin_gate(capture: Capture, direct: UInt32Array) -> JsonObject:
    _outer, inner = _pattern_endpoints()
    actual = capture.coefficient_bits
    inferred = np.zeros_like(direct)
    unique = np.zeros_like(direct, dtype=np.bool_)
    maximum_interval = 0
    for distance in range(DISTANCE_COUNT):
        for pattern in range(PATTERN_COUNT):
            for lane in range(LANE_COUNT):
                candidate, interval = _compatible_builtin_value(
                    int(actual[distance, pattern, lane, 0]),
                    int(actual[distance, pattern, lane, 2]),
                    int(inner[pattern, lane]),
                )
                maximum_interval = max(maximum_interval, interval)
                if candidate is not None:
                    inferred[distance, pattern, lane] = candidate
                    unique[distance, pattern, lane] = True

    records: list[JsonObject] = []
    total_unique = 0
    total_mismatch = 0
    total_delta = Counter[int]()
    for pattern in range(PATTERN_COUNT):
        selected = unique[:, pattern]
        observed = inferred[:, pattern][selected]
        expected = direct[:, pattern][selected]
        differences = np.array(
            [
                weight._ordered_key(int(left))  # noqa: SLF001
                - weight._ordered_key(int(right))  # noqa: SLF001
                for left, right in zip(observed, expected, strict=True)
            ],
            dtype=np.int64,
        )
        distribution = Counter(int(value) for value in differences)
        unique_count = int(selected.sum())
        mismatch_count = int(np.count_nonzero(differences))
        total_unique += unique_count
        total_mismatch += mismatch_count
        total_delta.update(distribution)
        records.append(
            {
                "pattern": pattern,
                "uniqueValueCount": unique_count,
                "ambiguousValueCount": int(selected.size - unique_count),
                "directValueMismatchCount": mismatch_count,
                "builtinCandidateMinusDirectFloatUlpDistribution": {
                    str(delta): count for delta, count in sorted(distribution.items())
                },
            }
        )
    expected = [
        (16_386, 16_386, 0),
        (16_386, 16_386, 0),
        (16_386, 16_386, 0),
        (16_386, 16_386, 0),
        (32_772, 0, 8_384),
        (32_772, 0, 6_928),
        (32_772, 0, 0),
        (32_770, 2, 0),
    ]
    actual_census = [
        (
            int(record["uniqueValueCount"]),
            int(record["ambiguousValueCount"]),
            int(record["directValueMismatchCount"]),
        )
        for record in records
    ]
    if actual_census != expected:
        raise ValueError("built-in endpoint-isolation census differs")
    if total_delta != Counter({0: 181_318, -1: 7_656, 1: 7_656}):
        raise ValueError("built-in endpoint-isolation delta distribution differs")
    return {
        "inversion": (
            "Intersect exact RN-even preimages of A and C under "
            "A=round((inner-g)/256), C=round(g+192*A); accept only one "
            "binary32 g that exactly reforwards."
        ),
        "uniqueValueCount": total_unique,
        "ambiguousValueCount": int(unique.size - total_unique),
        "directValueMatchCount": total_unique - total_mismatch,
        "directValueMismatchCount": total_mismatch,
        "builtinCandidateMinusDirectFloatUlpDistribution": {
            str(delta): count for delta, count in sorted(total_delta.items())
        },
        "maximumClosedIntervalFloatCount": maximum_interval,
        "patterns": records,
        "classification": (
            "One-hot, same-sign, translation, and non-cancelling arbitrary values "
            "join the direct endpoint law. Only opposite-sign patterns 4 and 5 "
            "differ, symmetrically by one ULP. Because g is inferred through the "
            "ordinary setup equations, this capture alone cannot assign that ULP "
            "to built-in generated-varying arithmetic versus coefficient setup."
        ),
    }


def analyze() -> JsonObject:
    dependencies = _verify_dependency_hashes()
    captures: dict[str, Capture] = {}
    capture_inputs: list[JsonObject] = []
    for spec in CAPTURE_SPECS:
        capture, authentication = _load_capture(spec)
        captures[spec.name] = capture
        capture_inputs.append(authentication)
    table = np.fromfile(RECIPROCAL_TABLE, dtype="<u4")
    if table.size != RECIPROCAL_ENTRY_COUNT:
        raise ValueError("reciprocal table entry count differs")
    predicted = _predict_direct_values(table)
    direct = _direct_gate(captures["direct-user-clip"], predicted)
    builtin = _builtin_gate(captures["builtin-guard"], predicted)
    return {
        "schema": "walle-reveal-agx-endpoint-isolation-analysis-v1",
        "passed": True,
        "classification": "output-blind AGX endpoint and setup-boundary isolation",
        "authority": {
            "referencePixelsRead": False,
            "usesOnlyRasterizerGeneratedCoefficientTriples": True,
            "directUserClipEndpointLawRecoveredForMeasuredDomain": True,
            "builtinGuardOneHotJoinEstablishedForMeasuredDomain": True,
            "builtinOppositeSignGeneratedVaryingLawRecovered": False,
            "arbitraryGeneratedChildSetupLawRecovered": False,
            "productionParityAuthorized": False,
        },
        "inputs": {
            "dependencies": dependencies,
            "captures": capture_inputs,
        },
        "directEndpoint": direct,
        "builtinGuardBoundary": builtin,
        "conclusion": (
            "The exhaustive reciprocal, measured 24/18/17 product, and one final "
            "binary32 add reproduce every one of 262,176 direct-user-clip endpoint "
            "values, including all opposite-sign cancellation probes. The remaining "
            "unknown is not direct endpoint mixing. It begins on the built-in guard "
            "path where arbitrary opposite-sign generated varyings are transformed "
            "into raster coefficient triples. A one-hot basis capture or direct TVB "
            "observation is required to separate that transform from setup rounding."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    arguments = parser.parse_args()
    result = analyze()
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
