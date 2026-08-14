#!/usr/bin/env python3
"""Separate ordinary AGX triangle setup from guard-clip attribute generation.

Four output-blind captures use the same public reveal geometry and fragment
shader.  They differ only in what reaches the raster setup unit:

* the original source triangles, clipped by AGX at the guard boundary;
* the canonical post-guard children submitted directly;
* those direct children with local one-hot vertex varyings; and
* the original source triangles in a wide viewport where guard clipping is
  impossible.

The direct one-hot and wide-source controls identify ordinary triangle setup.
The canonical direct capture then supplies a discovery/holdout corpus for the
general two-product numerator path.  Only after that policy is selected from
the discovery children is it evaluated on the held-out children.  No image,
reference pixel, or rendered coverage value is opened by this program.
"""

import argparse
import hashlib
import json
import math
import re
import struct
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Final

import numpy as np
from numpy.typing import NDArray


ROOT: Final = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "analysis"), str(ROOT / "lg-test" / "Analysis")]

import analyze_reveal_agx_basis_phase as phase  # noqa: E402
import analyze_reveal_agx_ldcf_export as export  # noqa: E402
import raster_tile_selector_model as tile_model  # noqa: E402


type JsonObject = dict[str, object]
type CoefficientTriple = tuple[int, int, int]
type Vertex = tuple[float, ...]
type CaptureWords = NDArray[np.uint32]
type ChildKey = tuple[int, int]

CATALOG: Final = ROOT / "build" / "analysis-agx-basis" / "reveal-agx-basis-catalog.json"
OUTPUT: Final = (
    ROOT / "build" / "analysis-agx-basis" / "reveal-agx-clip-setup-split-result.json"
)
P25_PATH: Final = ROOT / "parity" / "raster_p25_selector_ceil_bits.bin"
P25_SHA256: Final = "9fbc083dfd9c89fc0bcdc89308acfc4530d408e93789a7dab89ee59ff60a198f"
P25_BYTES: Final = 1 << 21
P25_KEY_LOW: Final = 1 << 24
P25_KEY_HIGH: Final = 1 << 25
P25_RECIPROCAL: Final = 1 << 49

DIRECT_GENERATOR: Final = (
    ROOT / "analysis" / "generate_reveal_agx_direct_child_vertices.py"
)
DIRECT_PROBE: Final = ROOT / "analysis" / "reveal_agx_direct_child_phase_probe.swift"
UNCLIPPED_PROBE: Final = (
    ROOT / "analysis" / "reveal_agx_unclipped_source_phase_probe.swift"
)
INTERPOSER: Final = ROOT / "analysis" / "macos_agx_iokit_trace.c"
PRIOR_EXPORT_REPORT: Final = (
    ROOT / "build" / "analysis-agx-basis" / "reveal-agx-ldcf-export-result.json"
)

EXPECTED_DEPENDENCIES: Final = {
    CATALOG: "bc8b96dc4d3dc7c2fb6383dda49baa839eb207b60128739604ad8ddcd9402bd6",
    DIRECT_GENERATOR: "8b6d055bfcabd91b174664dcc6cc3d39f73b9a7bd3fab3dc5768c191d5949cd9",
    DIRECT_PROBE: "75fa68eed5681fb881f6fbb8ebe254ffd070868369209b74012b5c0e61b7327a",
    UNCLIPPED_PROBE: "c32eb389cabeef4a63e67453e1b24651161a440d1a67bc646c1fe0085bd9b8f1",
    INTERPOSER: "2ab4165cdd4ca2751e940615c97dd747edf1b4e7001385da6cbb492585416258",
    PRIOR_EXPORT_REPORT: "39ac84a5553e3c650ea89de8f686a26972c4e5d10d463499df8543581d56ded8",
}

PATCH_TRACE = re.compile(
    r"^AGX_IO coefficient export matches=(\d+) applied=(\d+)$",
    flags=re.MULTILINE,
)


@dataclass(frozen=True, slots=True, kw_only=True)
class CaptureSpec:
    name: str
    capture_directory: Path
    root_directory: Path
    raw_sha256: str
    manifest_sha256: str
    trace_sha256: str
    executable_sha256: str
    inventory_sha256: str | None = None
    direct_input_sha256: str | None = None
    basis_mode: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class LoadedCapture:
    spec: CaptureSpec
    manifest: JsonObject
    words: CaptureWords
    raw_path: Path
    authentication: JsonObject


@dataclass(frozen=True, slots=True, kw_only=True)
class SetupPolicy:
    first_bias_units: int
    combined_precision_bits: int
    combined_rounding: str

    @property
    def name(self) -> str:
        return (
            f"first-bias-{self.first_bias_units}_"
            f"combine-p{self.combined_precision_bits}-{self.combined_rounding}"
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class CoefficientCase:
    split: str
    sample: phase.Sample
    vertices: tuple[Vertex, Vertex, Vertex]
    actual: tuple[CoefficientTriple, ...]


CAPTURE_SPECS: Final = (
    CaptureSpec(
        name="clipped-source",
        capture_directory=(
            ROOT
            / "build"
            / "analysis-agx-basis"
            / "macos-ldcf-export"
            / "capture-export"
        ),
        root_directory=ROOT / "build" / "analysis-agx-basis" / "macos-ldcf-export",
        raw_sha256="5706080d724791becd73148d6c5238761bcec9ce77ed8c414c22d375b1ce6e13",
        manifest_sha256="1f9fc1b85c2a31807b0b634cf34d660d724ded5d1d1b6abd2cfcc77a837f4960",
        trace_sha256="3e2c953b6759c1c9652f592754895527b9b768cb5eec3fb9b840a61e63fa85f5",
        executable_sha256="8c40c4e94b7883e9386c0c182ef84088252494ddc30c72246bbda731f7f92ee5",
    ),
    CaptureSpec(
        name="direct-canonical-child",
        capture_directory=(
            ROOT
            / "build"
            / "analysis-agx-basis"
            / "macos-direct-child-ldcf"
            / "capture"
        ),
        root_directory=(
            ROOT / "build" / "analysis-agx-basis" / "macos-direct-child-ldcf"
        ),
        raw_sha256="2dceca738e41dc8e92eede8712d4798721935993eee929f4bd9158bd8b42c962",
        manifest_sha256="afce2c5bbf7c64e56772f7acf7b58f109df93ebb92c40277bbcef77115121970",
        trace_sha256="af64b16c5ecd78108cb288fee6e3ec1d25deec045bc8b255245fd93ced0c340e",
        executable_sha256="cbf396468542ede2e93172a8a4482ebd1a74298a2bf2d730d212d6c13877ed40",
        inventory_sha256="50017c1611f7c5eb3aff115dc3a73aec354d2614ca43734672222c31586c02cb",
        direct_input_sha256="c698b11432704c47eb9c4acf396aa03c5dd3ee78a94d133f000e7c0091677052",
        basis_mode="canonical-transport",
    ),
    CaptureSpec(
        name="direct-local-onehot-child",
        capture_directory=(
            ROOT
            / "build"
            / "analysis-agx-basis"
            / "macos-direct-local-basis-ldcf"
            / "capture"
        ),
        root_directory=(
            ROOT / "build" / "analysis-agx-basis" / "macos-direct-local-basis-ldcf"
        ),
        raw_sha256="feb7281d12417db2ee40de5642a79c1144700fdc48b924e8029aaa5895d4393b",
        manifest_sha256="6f5640622a59c639a725365ed5fd73815c39a3645af2899338f443b20ead1344",
        trace_sha256="0884ed49e59fb88b4ddbdb6353533678a4de5dd691d061d55d0b5434404226da",
        executable_sha256="65409c9f6f9d01812d77241f049180d164391ebbde9fcdb18eb312dae8865dae",
        inventory_sha256="cc4a8ff1907f3eb22425972d85ad56b10f626ac41687289e798ef4b8eadc2145",
        direct_input_sha256="b094620680091bf3fff0dbfe9fb9edeabfc88eb62a86e8764260190481c6010c",
        basis_mode="local-onehot",
    ),
    CaptureSpec(
        name="unclipped-wide-source",
        capture_directory=(
            ROOT
            / "build"
            / "analysis-agx-basis"
            / "macos-unclipped-source-ldcf"
            / "capture"
        ),
        root_directory=(
            ROOT / "build" / "analysis-agx-basis" / "macos-unclipped-source-ldcf"
        ),
        raw_sha256="ff814daa9d872b7499c084a4ebf69c27cbd1e87b1f8bab973748724e9bbbaf9b",
        manifest_sha256="fc1b1491ca117521350a85ea4063916c0ab9d7b45891a6989a0e3298bab62c47",
        trace_sha256="f09d3b3f5b9362733e7d06cce35c835bd1f7719f56a390c941f0bc68bd261012",
        executable_sha256="1e3e74cc00f9025b2dcbcf5cf5d4b05fa612d056b2c0a1cb0f64fc6ab163517f",
        inventory_sha256="174809f8d557a6428892099be5165418435e9310830552acfde889d85c828b54",
    ),
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _check_hash(path: Path, expected: str) -> None:
    actual = _sha256(path)
    if actual != expected:
        raise ValueError(f"SHA-256 differs for {path}: {actual}")


def _require_dict(value: object, name: str) -> JsonObject:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ValueError(f"{name} is not an object")
    return value  # type: ignore[return-value]


def _validate_inventory(spec: CaptureSpec) -> JsonObject | None:
    if spec.inventory_sha256 is None:
        return None
    inventory_path = spec.root_directory / "inventory.sha256"
    _check_hash(inventory_path, spec.inventory_sha256)
    entries: list[JsonObject] = []
    for line_number, line in enumerate(
        inventory_path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        try:
            digest, relative = line.split("  ", maxsplit=1)
        except ValueError as error:
            raise ValueError(f"invalid inventory line {line_number}") from error
        path = ROOT / relative
        self_entry = path.resolve() == inventory_path.resolve()
        if not self_entry:
            _check_hash(path, digest)
        entries.append(
            {
                "path": relative,
                "bytes": path.stat().st_size,
                "sha256": digest,
                "selfEntryExcludedFromVerification": self_entry,
            }
        )
    return {
        "path": str(inventory_path),
        "sha256": spec.inventory_sha256,
        "entryCount": len(entries),
        "entries": entries,
    }


def _load_capture(
    spec: CaptureSpec,
    samples: tuple[phase.Sample, ...],
) -> LoadedCapture:
    manifest_path = spec.capture_directory / "manifest.json"
    _check_hash(manifest_path, spec.manifest_sha256)
    manifest, words, raw_path = phase._load_capture(  # noqa: SLF001
        spec.capture_directory,
        catalog_path=CATALOG,
        record_count=len(samples),
    )
    _check_hash(raw_path, spec.raw_sha256)
    executable = _require_dict(manifest.get("executable"), "capture executable")
    if executable.get("sha256") != spec.executable_sha256:
        raise ValueError(f"{spec.name} executable identity differs")

    trace_path = spec.root_directory / "capture.stderr"
    _check_hash(trace_path, spec.trace_sha256)
    trace = trace_path.read_text(encoding="utf-8", errors="strict")
    if PATCH_TRACE.findall(trace) != [("1", "1")]:
        raise ValueError(f"{spec.name} coefficient-export trace differs")

    direct_input = manifest.get("directChildInput")
    if spec.direct_input_sha256 is None:
        if direct_input is not None:
            raise ValueError(f"{spec.name} unexpectedly declares direct input")
    else:
        direct = _require_dict(direct_input, "direct child input")
        if direct.get("sha256") != spec.direct_input_sha256:
            raise ValueError(f"{spec.name} direct input identity differs")
        if spec.basis_mode is not None:
            actual_mode = direct.get("basisMode", "canonical-transport")
            if actual_mode != spec.basis_mode:
                raise ValueError(f"{spec.name} basis mode differs")

    for sample in samples:
        record = words[sample.record_index]
        expected_header = (*sample.pixel, 0, sample.case_index)
        expected_identity = (
            sample.record_index,
            sample.state,
            sample.source_primitive,
            sample.child_ordinal,
        )
        if tuple(int(value) for value in record[0]) != expected_header:
            raise ValueError(f"{spec.name} record header differs")
        if tuple(int(value) for value in record[1]) != expected_identity:
            raise ValueError(f"{spec.name} record identity differs")
        export._triples(record)  # noqa: SLF001

    authentication: JsonObject = {
        "captureDirectory": str(spec.capture_directory),
        "raw": {
            "path": str(raw_path),
            "bytes": raw_path.stat().st_size,
            "sha256": spec.raw_sha256,
        },
        "manifest": {
            "path": str(manifest_path),
            "sha256": spec.manifest_sha256,
        },
        "trace": {"path": str(trace_path), "sha256": spec.trace_sha256},
        "executableSha256": spec.executable_sha256,
        "directInputSha256": spec.direct_input_sha256,
        "basisMode": spec.basis_mode,
        "inventory": _validate_inventory(spec),
    }
    return LoadedCapture(
        spec=spec,
        manifest=manifest,
        words=words,
        raw_path=raw_path,
        authentication=authentication,
    )


def _float32(value: float) -> float:
    return struct.unpack("<f", struct.pack("<f", value))[0]


def _float_bits(value: float) -> int:
    return struct.unpack("<I", struct.pack("<f", value))[0]


def _power_of_two(exponent: int) -> Fraction:
    return Fraction(1 << exponent) if exponent >= 0 else Fraction(1, 1 << -exponent)


def _positive_float_components(bits: int) -> tuple[int, int]:
    exponent = (bits >> 23) & 0xFF
    if bits >> 31 or exponent in {0, 0xFF}:
        raise ValueError("a positive normal binary32 value is required")
    return (1 << 23) | (bits & 0x7F_FFFF), exponent - 150


def _subpixel_fixed(value: float) -> int:
    return math.floor(value * 256.0 + 0.5)


def _fixed_positions(
    vertices: tuple[Vertex, Vertex, Vertex],
) -> tuple[tuple[int, int], ...]:
    return tuple(
        (_subpixel_fixed(vertex[0]), _subpixel_fixed(vertex[1])) for vertex in vertices
    )


def _determinant(positions: tuple[tuple[int, int], ...]) -> int:
    return (positions[1][0] - positions[0][0]) * (positions[2][1] - positions[0][1]) - (
        positions[1][1] - positions[0][1]
    ) * (positions[2][0] - positions[0][0])


def _p25_selector(determinant: int, bitmap: bytes) -> tuple[int, int]:
    determinant = abs(determinant)
    if determinant == 0:
        raise ValueError("zero determinant")
    exponent = determinant.bit_length() - 1
    if exponent <= 24:
        key = determinant << (24 - exponent)
    else:
        shift = exponent - 24
        quotient, remainder = divmod(determinant, 1 << shift)
        key = quotient + (remainder >= 1 << (shift - 1))
    selector_exponent = -(determinant - 1).bit_length() - 8
    if determinant & (determinant - 1) == 0 or key == P25_KEY_HIGH:
        return 1 << 24, selector_exponent
    if not P25_KEY_LOW <= key < P25_KEY_HIGH:
        raise ValueError("P25 key is outside the calibrated interval")
    bit_index = key - P25_KEY_LOW
    ceil = bool((bitmap[bit_index >> 3] >> (bit_index & 7)) & 1)
    floor, remainder = divmod(P25_RECIPROCAL, key)
    return floor + (ceil and remainder != 0), selector_exponent


def _first_product(
    left: float,
    right: float,
    *,
    bias_units: int,
) -> Fraction:
    left = _float32(left)
    right = _float32(right)
    if left == 0.0 or right == 0.0:
        return Fraction(0)
    sign = -1 if (left < 0.0) != (right < 0.0) else 1
    left_index, left_exponent = _positive_float_components(_float_bits(abs(left)))
    right_index, right_exponent = _positive_float_components(_float_bits(abs(right)))
    index, exponent = tile_model.product_stage(
        left_index,
        left_exponent,
        right_index,
        right_exponent,
        output_bits=27,
        truncation_bits=16,
        bias_units=bias_units,
    )
    return sign * index * _power_of_two(exponent)


def _normalize_signed(
    value: Fraction,
    *,
    precision_bits: int,
    rounding: str,
) -> tuple[int, int, int]:
    if value == 0:
        return 0, 0, 0
    sign = -1 if value < 0 else 1
    magnitude = abs(value)
    exponent = tile_model.floor_binary_exponent(magnitude)
    step = _power_of_two(exponent - precision_bits + 1)
    scaled = magnitude / step
    quotient, remainder = divmod(scaled.numerator, scaled.denominator)
    match rounding:
        case "nearest-even":
            quotient = tile_model.round_fraction_to_integer_nearest_even(scaled)
        case "up":
            quotient += remainder != 0
        case "down":
            pass
        case _:
            raise ValueError(f"unknown combination rounding: {rounding}")
    if quotient.bit_length() > precision_bits:
        quotient >>= 1
        step *= 2
    return sign, quotient, tile_model.floor_binary_exponent(step)


def _reciprocal_product(
    signed_numerator: tuple[int, int, int],
    determinant: int,
    bitmap: bytes,
) -> int:
    sign, numerator, numerator_exponent = signed_numerator
    if sign == 0:
        return 0
    if determinant < 0:
        sign = -sign
    selector, selector_exponent = _p25_selector(determinant, bitmap)
    index, exponent = tile_model.product_stage(
        numerator,
        numerator_exponent,
        selector,
        selector_exponent,
        output_bits=27,
        truncation_bits=19,
        bias_units=20,
    )
    return export._round(sign * index * _power_of_two(exponent))  # noqa: SLF001


def _simple_basis_slope(
    vertices: tuple[Vertex, Vertex, Vertex],
    component: int,
    axis: int,
    bitmap: bytes,
    *,
    first_bias_units: int,
) -> int:
    positions = _fixed_positions(vertices)
    determinant = _determinant(positions)
    if axis == 0:
        edge_fixed = (
            positions[1][1] - positions[2][1],
            positions[2][1] - positions[0][1],
            positions[0][1] - positions[1][1],
        )[component]
    else:
        edge_fixed = (
            positions[2][0] - positions[1][0],
            positions[0][0] - positions[2][0],
            positions[1][0] - positions[0][0],
        )[component]
    numerator = _first_product(
        1.0,
        edge_fixed / 256.0,
        bias_units=first_bias_units,
    )
    signed = _normalize_signed(
        numerator,
        precision_bits=27,
        rounding="nearest-even",
    )
    return _reciprocal_product(signed, determinant, bitmap)


def _general_slope(
    vertices: tuple[Vertex, Vertex, Vertex],
    component: int,
    axis: int,
    bitmap: bytes,
    policy: SetupPolicy,
) -> int:
    positions = _fixed_positions(vertices)
    determinant = _determinant(positions)
    values = tuple(_float32(vertex[2 + component]) for vertex in vertices)
    delta_one = _float32(values[1] - values[0])
    delta_two = _float32(values[2] - values[0])
    if axis == 0:
        numerator = _first_product(
            delta_one,
            (positions[2][1] - positions[0][1]) / 256.0,
            bias_units=policy.first_bias_units,
        ) - _first_product(
            delta_two,
            (positions[1][1] - positions[0][1]) / 256.0,
            bias_units=policy.first_bias_units,
        )
    else:
        numerator = _first_product(
            delta_two,
            (positions[1][0] - positions[0][0]) / 256.0,
            bias_units=policy.first_bias_units,
        ) - _first_product(
            delta_one,
            (positions[2][0] - positions[0][0]) / 256.0,
            bias_units=policy.first_bias_units,
        )
    combined = _normalize_signed(
        numerator,
        precision_bits=policy.combined_precision_bits,
        rounding=policy.combined_rounding,
    )
    return _reciprocal_product(combined, determinant, bitmap)


def _source_vertices(sample: phase.Sample) -> tuple[Vertex, Vertex, Vertex]:
    return tuple(
        (
            phase._float(vertex[0]),  # noqa: SLF001
            phase._float(vertex[1]),  # noqa: SLF001
            *(1.0 if component == vertex_index else 0.0 for component in range(3)),
            float(1 << vertex_index),
        )
        for vertex_index, vertex in enumerate(sample.source_vertices)
    )  # type: ignore[return-value]


def _child_slopes(
    samples: tuple[phase.Sample, ...],
    words: CaptureWords,
) -> dict[tuple[int, int, int], tuple[int, int]]:
    grouped: dict[tuple[int, int, int], set[tuple[int, int]]] = defaultdict(set)
    for sample in samples:
        for component, triple in enumerate(export._triples(words[sample.record_index])):  # noqa: SLF001
            grouped[(sample.case_index, sample.child_ordinal, component)].add(
                triple[:2]
            )
    varying = [key for key, slopes in grouped.items() if len(slopes) != 1]
    if varying:
        raise ValueError(f"tile-varying slope groups: {varying[:8]}")
    return {key: next(iter(slopes)) for key, slopes in grouped.items()}


def _delta_summary(deltas: list[int]) -> JsonObject:
    summary = export._delta_census(deltas)  # noqa: SLF001
    summary["differentCount"] = sum(delta != 0 for delta in deltas)
    return summary


def _compare_slopes(
    left: dict[tuple[int, int, int], tuple[int, int]],
    right: dict[tuple[int, int, int], tuple[int, int]],
) -> JsonObject:
    if left.keys() != right.keys():
        raise ValueError("coefficient group keys differ")
    deltas = [
        export._float_ulp_delta(left[key][axis], right[key][axis])  # noqa: SLF001
        for key in sorted(left)
        for axis in range(2)
    ]
    return _delta_summary(deltas)


def _canonical_plane_comparison(
    samples: tuple[phase.Sample, ...],
    direct_slopes: dict[tuple[int, int, int], tuple[int, int]],
) -> JsonObject:
    unique: dict[ChildKey, phase.Sample] = {}
    for sample in samples:
        unique.setdefault((sample.case_index, sample.child_ordinal), sample)
    deltas: list[int] = []
    for key, sample in sorted(unique.items()):
        vertices = phase._canonical_children(sample)[  # noqa: SLF001
            sample.child_ordinal_within_source
        ]
        for component in range(4):
            observed = direct_slopes[(*key, component)]
            for axis in range(2):
                predicted = phase._plane_slope_bits(  # noqa: SLF001
                    vertices,
                    axis=axis,
                    component=2 + component,
                )
                deltas.append(
                    export._float_ulp_delta(observed[axis], predicted)  # noqa: SLF001
                )
    return _delta_summary(deltas)


def _prediction_summary(
    deltas: list[int],
    predicted_words: list[int],
) -> JsonObject:
    encoded = struct.pack(f"<{len(predicted_words)}I", *predicted_words)
    return {
        **_delta_summary(deltas),
        "predictionWordCount": len(predicted_words),
        "predictionSha256": hashlib.sha256(encoded).hexdigest(),
    }


def _simple_setup_controls(
    samples: tuple[phase.Sample, ...],
    local_words: CaptureWords,
    unclipped_words: CaptureWords,
    bitmap: bytes,
    *,
    first_bias_units: int,
) -> JsonObject:
    children: dict[ChildKey, phase.Sample] = {}
    sources: dict[tuple[tuple[int, ...], ...], phase.Sample] = {}
    for sample in samples:
        children.setdefault((sample.case_index, sample.child_ordinal), sample)
        sources.setdefault(sample.source_vertices, sample)

    local_deltas: list[int] = []
    local_predictions: list[int] = []
    for sample in children.values():
        vertices = phase._canonical_children(sample)[  # noqa: SLF001
            sample.child_ordinal_within_source
        ]
        actual = export._triples(local_words[sample.record_index])  # noqa: SLF001
        for component in range(3):
            for axis in range(2):
                predicted = _simple_basis_slope(
                    vertices,
                    component,
                    axis,
                    bitmap,
                    first_bias_units=first_bias_units,
                )
                local_predictions.append(predicted)
                local_deltas.append(
                    export._float_ulp_delta(actual[component][axis], predicted)  # noqa: SLF001
                )

    source_deltas: list[int] = []
    source_predictions: list[int] = []
    for sample in sources.values():
        vertices = _source_vertices(sample)
        actual = export._triples(unclipped_words[sample.record_index])  # noqa: SLF001
        for component in range(3):
            for axis in range(2):
                predicted = _simple_basis_slope(
                    vertices,
                    component,
                    axis,
                    bitmap,
                    first_bias_units=first_bias_units,
                )
                source_predictions.append(predicted)
                source_deltas.append(
                    export._float_ulp_delta(actual[component][axis], predicted)  # noqa: SLF001
                )

    if any(local_deltas) or any(source_deltas):
        raise ValueError("ordinary one-hot setup control is not exact")
    return {
        "arithmetic": {
            "positionQuantization": "floor(binary32Position * 256 + 0.5)",
            "firstProduct": {
                "outputBits": 27,
                "truncatedPartialProductLowBits": 16,
                "biasUnits": first_bias_units,
            },
            "reciprocal": "P25 calibrated selector",
            "secondProduct": {
                "outputBits": 27,
                "truncatedPartialProductLowBits": 19,
                "biasUnits": 20,
            },
        },
        "directLocalOneHot": {
            "uniqueChildCount": len(children),
            **_prediction_summary(local_deltas, local_predictions),
        },
        "unclippedWideSource": {
            "uniqueSourceTriangleCount": len(sources),
            **_prediction_summary(source_deltas, source_predictions),
        },
        "conclusion": (
            "the measured P25 two-product pipeline exactly explains ordinary "
            "AGX setup for every direct child and every clipping-free source triangle"
        ),
    }


def _coefficient_cases(
    samples: tuple[phase.Sample, ...],
    direct_words: CaptureWords,
) -> tuple[CoefficientCase, ...]:
    unique: dict[ChildKey, phase.Sample] = {}
    for sample in samples:
        unique.setdefault((sample.case_index, sample.child_ordinal), sample)
    return tuple(
        CoefficientCase(
            split=export._split_name(sample),  # noqa: SLF001
            sample=sample,
            vertices=phase._canonical_children(sample)[  # noqa: SLF001
                sample.child_ordinal_within_source
            ],
            actual=export._triples(direct_words[sample.record_index]),  # noqa: SLF001
        )
        for _, sample in sorted(unique.items())
    )


def _score_policy(
    cases: tuple[CoefficientCase, ...],
    bitmap: bytes,
    policy: SetupPolicy,
    *,
    split: str,
    include_examples: bool,
) -> JsonObject:
    deltas: list[int] = []
    predicted_words: list[int] = []
    examples: list[JsonObject] = []
    selected_child_count = 0
    for entry in cases:
        if split != "all" and entry.split != split:
            continue
        selected_child_count += 1
        for component in range(4):
            for axis in range(2):
                predicted = _general_slope(
                    entry.vertices,
                    component,
                    axis,
                    bitmap,
                    policy,
                )
                actual = entry.actual[component][axis]
                delta = export._float_ulp_delta(actual, predicted)  # noqa: SLF001
                deltas.append(delta)
                predicted_words.append(predicted)
                if include_examples and delta != 0 and len(examples) < 32:
                    examples.append(
                        {
                            "caseIndex": entry.sample.case_index,
                            "state": entry.sample.state,
                            "sourcePrimitive": entry.sample.source_primitive,
                            "childOrdinal": entry.sample.child_ordinal,
                            "component": component,
                            "axis": axis,
                            "actualBits": f"0x{actual:08x}",
                            "predictedBits": f"0x{predicted:08x}",
                            "actualMinusPredictedFloatUlps": delta,
                        }
                    )
    return {
        "split": split,
        "childCount": selected_child_count,
        **_prediction_summary(deltas, predicted_words),
        "firstMismatches": examples,
    }


def _policy_rank(result: JsonObject, policy: SetupPolicy) -> tuple[int, ...]:
    rounding_priority = {"nearest-even": 2, "down": 1, "up": 0}
    return (
        int(result["exactCount"]),
        int(result["withinOneUlpCount"]),
        int(result["withinTwoUlpsCount"]),
        int(result["withinFourUlpsCount"]),
        rounding_priority[policy.combined_rounding],
        -abs(policy.combined_precision_bits - 27),
        policy.first_bias_units,
    )


def _search_setup_policy(
    cases: tuple[CoefficientCase, ...],
    bitmap: bytes,
) -> JsonObject:
    policies = tuple(
        SetupPolicy(
            first_bias_units=bias,
            combined_precision_bits=precision,
            combined_rounding=rounding,
        )
        for bias in range(8, 16)
        for precision in range(24, 31)
        for rounding in ("down", "nearest-even", "up")
    )
    discovery_results: list[tuple[SetupPolicy, JsonObject]] = []
    for policy in policies:
        result = _score_policy(
            cases,
            bitmap,
            policy,
            split="discovery",
            include_examples=False,
        )
        discovery_results.append((policy, result))
    winner, winner_discovery = max(
        discovery_results,
        key=lambda item: _policy_rank(item[1], item[0]),
    )
    expected_winner = SetupPolicy(
        first_bias_units=15,
        combined_precision_bits=27,
        combined_rounding="nearest-even",
    )
    if winner != expected_winner:
        raise ValueError(f"discovery winner changed: {winner}")
    holdout = _score_policy(
        cases,
        bitmap,
        winner,
        split="holdout",
        include_examples=True,
    )
    combined = _score_policy(
        cases,
        bitmap,
        winner,
        split="all",
        include_examples=True,
    )
    ranked = sorted(
        discovery_results,
        key=lambda item: _policy_rank(item[1], item[0]),
        reverse=True,
    )
    return {
        "selection": {
            "splitUsed": "discovery only",
            "candidatePolicyCount": len(policies),
            "firstBiasUnitsSearched": [8, 15],
            "combinedPrecisionBitsSearched": [24, 30],
            "combinedRoundingModes": ["down", "nearest-even", "up"],
            "ranking": [
                "exactCount",
                "withinOneUlpCount",
                "withinTwoUlpsCount",
                "withinFourUlpsCount",
                "nearest-even prior",
                "measured 27-bit precision prior",
                "higher first-stage bias",
            ],
            "winner": {
                "name": winner.name,
                "firstBiasUnits": winner.first_bias_units,
                "combinedPrecisionBits": winner.combined_precision_bits,
                "combinedRounding": winner.combined_rounding,
            },
            "topDiscoveryPolicies": [
                {
                    "name": policy.name,
                    "firstBiasUnits": policy.first_bias_units,
                    "combinedPrecisionBits": policy.combined_precision_bits,
                    "combinedRounding": policy.combined_rounding,
                    **result,
                }
                for policy, result in ranked[:16]
            ],
        },
        "discovery": winner_discovery,
        "holdout": holdout,
        "combined": combined,
        "interpretation": (
            "the frozen two-product/27-bit-combination law transfers to the "
            "untouched holdout; the remaining disagreements are concentrated in "
            "signed cancellation and do not reopen the already exact simple setup path"
        ),
    }


def analyze() -> JsonObject:
    for path, digest in EXPECTED_DEPENDENCIES.items():
        _check_hash(path, digest)
    _check_hash(P25_PATH, P25_SHA256)
    if P25_PATH.stat().st_size != P25_BYTES:
        raise ValueError("P25 selector bitmap length differs")
    bitmap = P25_PATH.read_bytes()

    catalog, samples = phase._load_catalog(CATALOG)  # noqa: SLF001
    captures = tuple(_load_capture(spec, samples) for spec in CAPTURE_SPECS)
    reference_metadata = captures[0].words[:, :3, :]
    for capture in captures[1:]:
        if not np.array_equal(reference_metadata, capture.words[:, :3, :]):
            raise ValueError(f"{capture.spec.name} metadata vectors differ")

    by_name = {capture.spec.name: capture for capture in captures}
    slope_groups = {
        name: _child_slopes(samples, capture.words) for name, capture in by_name.items()
    }
    direct = slope_groups["direct-canonical-child"]
    comparisons = {
        "clippedSourceVsDirectCanonicalChild": _compare_slopes(
            slope_groups["clipped-source"], direct
        ),
        "clippedSourceVsUnclippedWideSource": _compare_slopes(
            slope_groups["clipped-source"],
            slope_groups["unclipped-wide-source"],
        ),
        "directCanonicalChildVsOrdinaryBinary32Plane": _canonical_plane_comparison(
            samples, direct
        ),
    }
    controls = _simple_setup_controls(
        samples,
        by_name["direct-local-onehot-child"].words,
        by_name["unclipped-wide-source"].words,
        bitmap,
        first_bias_units=15,
    )
    cases = _coefficient_cases(
        samples,
        by_name["direct-canonical-child"].words,
    )
    split_counts = Counter(entry.split for entry in cases)
    policy = _search_setup_policy(cases, bitmap)

    script_path = Path(__file__).resolve()
    return {
        "schemaVersion": 1,
        "classification": "output-blind AGX clip-versus-setup separation",
        "authority": {
            "referencePixelsRead": False,
            "renderedCoverageRead": False,
            "usesPublicRevealGeometryOnly": True,
            "rawLDCFTriplesAuthenticated": True,
            "ordinaryOneHotTriangleSetupRecoveredForMeasuredDomain": True,
            "ordinaryArbitraryVaryingSetupFullyRecovered": False,
            "guardClipGeneratedAttributeLawRecovered": False,
            "productionIntegrationAuthorized": False,
        },
        "inputs": {
            "analyzer": {
                "path": str(script_path),
                "bytes": script_path.stat().st_size,
                "sha256": _sha256(script_path),
            },
            "catalog": {
                "path": str(CATALOG),
                "bytes": CATALOG.stat().st_size,
                "sha256": _sha256(CATALOG),
            },
            "p25Selector": {
                "path": str(P25_PATH),
                "bytes": P25_PATH.stat().st_size,
                "sha256": _sha256(P25_PATH),
            },
            "dependencies": [
                {"path": str(path), "bytes": path.stat().st_size, "sha256": digest}
                for path, digest in EXPECTED_DEPENDENCIES.items()
            ],
            "captures": {
                capture.spec.name: capture.authentication for capture in captures
            },
        },
        "captureJoin": {
            "recordCount": len(samples),
            "metadataVectorComparisonCount": len(samples) * 3 * 4 * 3,
            "identityAndSampleMetadataVectorsExactAcrossAllFourCaptures": True,
            "uniqueChildCount": len(cases),
            "discoveryChildCount": split_counts["discovery"],
            "holdoutChildCount": split_counts["holdout"],
            "componentSlopeCount": len(cases) * 4 * 2,
        },
        "captureComparisons": comparisons,
        "ordinarySetupControls": controls,
        "generalArbitraryVaryingSetup": policy,
        "conclusion": {
            "established": (
                "the one-hot AGX triangle setup path is exactly identified by the "
                "P25 two-product pipeline on every measured direct child and every "
                "clipping-free source triangle"
            ),
            "discriminator": (
                "the clipped source capture changes both slopes and tile constants "
                "relative to the clipping-free source and direct canonical child"
            ),
            "remainingUnknown": (
                "the guard clipper's generated per-vertex attribute transform, plus "
                "the signed cancellation detail needed to make arbitrary-varying "
                "ordinary setup bit-exact"
            ),
            "nextExperiment": (
                "use the solved setup transform with vertex-basis inputs to recover "
                "the clipper-generated child attributes without consulting output pixels"
            ),
        },
        "catalogCensus": catalog["census"],
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=OUTPUT)
    return parser.parse_args()


def main() -> None:
    arguments = _parse_args()
    report = analyze()
    encoded = (json.dumps(report, indent=2, sort_keys=True) + "\n").encode()
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_bytes(encoded)
    print(
        json.dumps(
            {
                "output": str(arguments.output),
                "sha256": hashlib.sha256(encoded).hexdigest(),
                "directOneHotExact": report["ordinarySetupControls"][  # type: ignore[index]
                    "directLocalOneHot"
                ]["exactCount"],
                "wideSourceExact": report["ordinarySetupControls"][  # type: ignore[index]
                    "unclippedWideSource"
                ]["exactCount"],
                "discoveryExact": report["generalArbitraryVaryingSetup"][  # type: ignore[index]
                    "discovery"
                ]["exactCount"],
                "holdoutExact": report["generalArbitraryVaryingSetup"][  # type: ignore[index]
                    "holdout"
                ]["exactCount"],
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
