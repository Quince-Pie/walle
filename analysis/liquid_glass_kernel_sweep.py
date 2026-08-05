#!/usr/bin/env python3
"""Validate and measure the native Liquid Glass cumulative-kernel sweep."""

import argparse
import hashlib
import json
import platform
import resource
import time
import zipfile
from collections import Counter
from dataclasses import dataclass
from importlib.metadata import version as package_version
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from liquid_glass_face_stage import half_fused_multiply_add
from liquid_glass_native_capture import (
    EXPECTED_BIAS_BITS,
    RECOVERED_MATRIX_BITS,
    recovered_half_face,
)
from liquid_glass_sampler_probe import half_linear_ties_up


type FloatArray = NDArray[np.float64]
type IntArray = NDArray[np.int64]
type UInt8Array = NDArray[np.uint8]
type JsonObject = dict[str, Any]

EXPECTED_RIG = "native-kernel-sweep-1.0.0"
EXPECTED_SCHEMA = 1
EXPECTED_AMPLITUDES = tuple(range(128))
EXPECTED_INTERVENTIONS = (
    "identity-blur-0",
    "identity-blur-1",
    "identity-blur-2",
    "identity-blur-4",
)
EXPECTED_COORDINATES = (112, 338, 564, 790)
EXPECTED_PATCH_RADIUS = 40
EXPECTED_PATCH_SIDE = 81
EXPECTED_SQUARE_SIDE = 96
CHANNEL_SIGNS = np.asarray((1, -1, 1), dtype=np.int16)
CHANNELS = 3
SOURCE_CODE = 128
MIP_BLEND_NUMERATOR = 148
MIP_BLEND_DENOMINATOR = 256


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def bytes_sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def difference_metrics(
    predicted: UInt8Array | IntArray,
    actual: UInt8Array | IntArray,
) -> JsonObject:
    if predicted.shape != actual.shape:
        raise ValueError("comparison shapes differ")
    delta = predicted.astype(np.int16) - actual.astype(np.int16)
    changed = delta != 0
    changed_pixels = np.any(changed, axis=-1)
    return {
        "values": int(delta.size),
        "changedValues": int(np.count_nonzero(changed)),
        "exactValueFraction": float(np.mean(~changed)),
        "pixels": int(changed_pixels.size),
        "changedPixels": int(np.count_nonzero(changed_pixels)),
        "exactPixelFraction": float(np.mean(~changed_pixels)),
        "meanAbsoluteCodes": float(np.mean(np.abs(delta))),
        "maximumAbsoluteCodes": int(np.abs(delta).max(initial=0)),
    }


def _checked_catalog(manifest: JsonObject) -> tuple[list[JsonObject], int]:
    if manifest.get("rigVersion") != EXPECTED_RIG:
        raise ValueError(
            f"unexpected native kernel rig: {manifest.get('rigVersion')!r}"
        )
    if manifest.get("schemaVersion") != EXPECTED_SCHEMA:
        raise ValueError("native kernel manifest schema differs")
    if manifest.get("sweepKind") != (
        "deep-interior-phase-controlled-square-steps"
    ):
        raise ValueError("native kernel sweep kind differs")
    source = manifest.get("sourceDesign")
    if not isinstance(source, dict):
        raise ValueError("native kernel source design is missing")
    if (
        tuple(source.get("amplitudesCodes", ()))
        != EXPECTED_AMPLITUDES
        or source.get("baseCode") != SOURCE_CODE
        or source.get("squareWidth") != EXPECTED_SQUARE_SIDE
        or source.get("squareHeight") != EXPECTED_SQUARE_SIDE
        or source.get("patchRadiusPixels") != EXPECTED_PATCH_RADIUS
        or source.get("patchSidePixels") != EXPECTED_PATCH_SIDE
        or source.get("reducedGridPixelSizeSourcePixels") != 2
        or source.get("phasePeriodReducedGridPixels") != 4
        or source.get("channelSigns")
        != {"red": 1, "green": -1, "blue": 1}
    ):
        raise ValueError("native kernel source geometry differs")

    sites = source.get("sites")
    if not isinstance(sites, list) or len(sites) != 16:
        raise ValueError("native kernel phase catalog differs")
    expected_sites = []
    for phase_y, y in enumerate(EXPECTED_COORDINATES):
        for phase_x, x in enumerate(EXPECTED_COORDINATES):
            expected_sites.append({
                "index": phase_y * 4 + phase_x,
                "x": x,
                "y": y,
                "reducedGridPhaseX": phase_x,
                "reducedGridPhaseY": phase_y,
                "observedReducedGridPhaseX": (x // 2) & 3,
                "observedReducedGridPhaseY": (y // 2) & 3,
            })
    if sites != expected_sites:
        raise ValueError("native kernel phase coordinates differ")

    captures = manifest.get("captures")
    if not isinstance(captures, list) or len(captures) != 128:
        raise ValueError("native kernel capture catalog differs")
    for amplitude, record in enumerate(captures):
        if (
            record.get("amplitudeCodes") != amplitude
            or record.get("captureBackend") != "CGWindowListCreateImage"
            or int(record.get("controlStabilitySamples", 0)) < 2
            or int(record.get("clearStabilitySamples", 0)) < 2
        ):
            raise ValueError(
                f"native kernel capture metadata differs at {amplitude}"
            )
        interventions = record.get("interventions")
        if not isinstance(interventions, list) or tuple(
            candidate.get("name") for candidate in interventions
        ) != EXPECTED_INTERVENTIONS:
            raise ValueError(
                f"native kernel interventions differ at {amplitude}"
            )
        if any(
            candidate.get("captureBackend")
            != "CGWindowListCreateImage"
            or int(candidate.get("stabilitySamples", 0)) < 2
            for candidate in interventions
        ):
            raise ValueError(
                f"native kernel intervention stability differs at {amplitude}"
            )
    return sites, EXPECTED_PATCH_SIDE


@dataclass(frozen=True, slots=True)
class KernelSweep:
    manifest: JsonObject
    control: UInt8Array
    clear: UInt8Array
    interventions: dict[str, UInt8Array]

    @property
    def sites(self) -> list[JsonObject]:
        return self.manifest["sourceDesign"]["sites"]

    @classmethod
    def open(cls, path: Path) -> "KernelSweep":
        if path.is_dir():
            manifest = json.loads(
                (path / "manifest.json").read_text(encoding="utf-8")
            )

            def read(name: str) -> bytes:
                return (path / name).read_bytes()

        else:
            with zipfile.ZipFile(path) as archive:
                names = archive.namelist()
                if len(names) != len(set(names)):
                    raise ValueError("native kernel archive has duplicate members")
                try:
                    manifest = json.loads(archive.read("manifest.json"))
                except KeyError as error:
                    raise ValueError(
                        "native kernel manifest is missing"
                    ) from error
                members = {
                    name: archive.read(name)
                    for name in names
                    if name.startswith("native-kernel-")
                    or name.endswith(".icc")
                }

            def read(name: str) -> bytes:
                try:
                    return members[name]
                except KeyError as error:
                    raise ValueError(
                        f"native kernel member is missing: {name}"
                    ) from error

        sites, patch_side = _checked_catalog(manifest)
        evidence = manifest.get("nativeCaptureEvidence")
        if not isinstance(evidence, dict):
            raise ValueError("native kernel evidence is missing")
        if (
            evidence.get("schemaVersion") != EXPECTED_SCHEMA
            or evidence.get("recordFormat") != "RGB8"
            or evidence.get("recordStrideBytes") != CHANNELS
        ):
            raise ValueError("native kernel record format differs")
        record_count = (
            len(EXPECTED_AMPLITUDES) * len(sites) * patch_side**2
        )
        if evidence.get("recordCount") != record_count:
            raise ValueError("native kernel record count differs")
        shape = (
            len(EXPECTED_AMPLITUDES),
            len(sites),
            patch_side,
            patch_side,
            CHANNELS,
        )

        def decode(record: JsonObject) -> UInt8Array:
            name = str(record["file"])
            data = read(name)
            expected_bytes = record_count * CHANNELS
            if (
                len(data) != expected_bytes
                or record.get("fileBytes") != expected_bytes
            ):
                raise ValueError(
                    f"native kernel stream length differs: {name}"
                )
            if bytes_sha256(data) != record.get("fileSha256"):
                raise ValueError(
                    f"native kernel stream hash differs: {name}"
                )
            return np.frombuffer(data, dtype=np.uint8).reshape(shape)

        control = decode({
            "file": evidence["controlFile"],
            "fileBytes": evidence["controlFileBytes"],
            "fileSha256": evidence["controlFileSha256"],
        })
        clear = decode({
            "file": evidence["clearFile"],
            "fileBytes": evidence["clearFileBytes"],
            "fileSha256": evidence["clearFileSha256"],
        })
        records = evidence.get("interventions")
        if not isinstance(records, list) or tuple(
            record.get("name") for record in records
        ) != EXPECTED_INTERVENTIONS:
            raise ValueError("native kernel evidence interventions differ")
        interventions = {
            str(record["name"]): decode(record)
            for record in records
        }
        if icc_name := evidence.get("iccFile"):
            icc = read(str(icc_name))
            if (
                len(icc) != evidence.get("iccFileBytes")
                or bytes_sha256(icc) != evidence.get("iccFileSha256")
            ):
                raise ValueError("native kernel ICC evidence differs")
        return cls(
            manifest=manifest,
            control=control,
            clear=clear,
            interventions=interventions,
        )


def source_fidelity(sweep: KernelSweep) -> JsonObject:
    changed_values = 0
    changed_pixels = 0
    maximum = 0
    absolute_sum = 0
    value_count = 0
    center = EXPECTED_PATCH_RADIUS
    for amplitude in EXPECTED_AMPLITUDES:
        expected = np.full(
            sweep.control.shape[1:],
            SOURCE_CODE,
            dtype=np.uint8,
        )
        expected[
            :,
            center:,
            center:,
            0,
        ] = SOURCE_CODE + amplitude
        expected[
            :,
            center:,
            center:,
            1,
        ] = SOURCE_CODE - amplitude
        expected[
            :,
            center:,
            center:,
            2,
        ] = SOURCE_CODE + amplitude
        delta = (
            expected.astype(np.int16)
            - sweep.control[amplitude].astype(np.int16)
        )
        changed = delta != 0
        changed_values += int(np.count_nonzero(changed))
        changed_pixels += int(
            np.count_nonzero(np.any(changed, axis=-1))
        )
        maximum = max(maximum, int(np.abs(delta).max(initial=0)))
        absolute_sum += int(np.abs(delta).sum())
        value_count += delta.size
    return {
        "values": value_count,
        "changedValues": changed_values,
        "exactValueFraction": 1.0 - changed_values / value_count,
        "pixels": value_count // CHANNELS,
        "changedPixels": changed_pixels,
        "exactPixelFraction": (
            1.0 - changed_pixels / (value_count // CHANNELS)
        ),
        "meanAbsoluteCodes": absolute_sum / value_count,
        "maximumAbsoluteCodes": maximum,
        "exact": changed_values == 0,
    }


def baseline_histogram(stream: UInt8Array) -> dict[str, int]:
    values, counts = np.unique(stream[0], return_counts=True)
    return {
        str(int(value)): int(count)
        for value, count in zip(values, counts, strict=True)
    }


def signed_channel_diagnostics(stream: UInt8Array) -> JsonObject:
    red_blue_changed = 0
    symmetry_histogram: Counter[int] = Counter()
    maximum_symmetry_error = 0
    for amplitude in EXPECTED_AMPLITUDES:
        current = stream[amplitude].astype(np.int16)
        red_blue_changed += int(
            np.count_nonzero(current[..., 0] != current[..., 2])
        )
        residual = current[..., 0] + current[..., 1] - 2 * SOURCE_CODE
        values, counts = np.unique(residual, return_counts=True)
        symmetry_histogram.update({
            int(value): int(count)
            for value, count in zip(values, counts, strict=True)
        })
        maximum_symmetry_error = max(
            maximum_symmetry_error,
            int(np.abs(residual).max(initial=0)),
        )
    return {
        "redBlueValues": int(stream[..., 0].size),
        "redBlueChangedValues": red_blue_changed,
        "redBlueExact": red_blue_changed == 0,
        "positiveNegativeSymmetryResidualHistogram": {
            str(key): symmetry_histogram[key]
            for key in sorted(symmetry_histogram)
        },
        "maximumPositiveNegativeSymmetryErrorCodes":
            maximum_symmetry_error,
    }


def signed_step_slopes(stream: UInt8Array) -> FloatArray:
    numerator = np.zeros(stream.shape[1:-1], dtype=np.float64)
    denominator = (
        CHANNELS
        * sum(amplitude * amplitude for amplitude in EXPECTED_AMPLITUDES)
    )
    baseline = stream[0].astype(np.int16)
    for amplitude in EXPECTED_AMPLITUDES[1:]:
        delta = stream[amplitude].astype(np.int16) - baseline
        signed = delta * CHANNEL_SIGNS
        numerator += amplitude * signed.sum(axis=-1)
    return numerator / denominator


def mixed_derivative(step: FloatArray) -> FloatArray:
    if step.ndim != 3:
        raise ValueError("step response must be site/y/x")
    return np.diff(np.diff(step, axis=1), axis=2)


def _rank_one_residual(values: FloatArray) -> float:
    singular = np.linalg.svd(values, compute_uv=False)
    energy = float(np.dot(singular, singular))
    if energy == 0:
        return 0.0
    return float(
        np.sqrt(np.dot(singular[1:], singular[1:]) / energy)
    )


def kernel_measurements(
    sweep: KernelSweep,
    stream: UInt8Array,
) -> JsonObject:
    slopes = signed_step_slopes(stream)
    interior = np.median(
        slopes[:, -9:, -9:],
        axis=(1, 2),
    )
    if np.any(interior <= 0):
        raise ValueError("native kernel interior response is nonpositive")
    step = slopes / interior[:, None, None]
    kernel = mixed_derivative(step)
    offsets = np.arange(
        -EXPECTED_PATCH_RADIUS + 0.5,
        EXPECTED_PATCH_RADIUS,
        dtype=np.float64,
    )
    y, x = np.meshgrid(offsets, offsets, indexing="ij")
    records = []
    for site_index, site in enumerate(sweep.sites):
        current = kernel[site_index]
        total = float(current.sum())
        absolute = np.abs(current)
        positive_total = float(current[current > 0].sum())
        negative_total = float(-current[current < 0].sum())
        normalized = current / total if total != 0 else current
        records.append({
            "siteIndex": site_index,
            "phaseX": int(site["reducedGridPhaseX"]),
            "phaseY": int(site["reducedGridPhaseY"]),
            "interiorSlopeCodesPerAmplitude":
                float(interior[site_index]),
            "stepMinimum": float(step[site_index].min()),
            "stepMaximum": float(step[site_index].max()),
            "stepRankOneRelativeFrobeniusResidual":
                _rank_one_residual(step[site_index]),
            "kernelSum": total,
            "kernelPositiveMass": positive_total,
            "kernelNegativeMass": negative_total,
            "kernelRankOneRelativeFrobeniusResidual":
                _rank_one_residual(current),
            "kernelCenterOfMassX": (
                float(np.sum(normalized * x))
                if total != 0 else None
            ),
            "kernelCenterOfMassY": (
                float(np.sum(normalized * y))
                if total != 0 else None
            ),
            "kernelAbsoluteMassOutsideRadii": {
                str(radius): float(
                    absolute[
                        np.maximum(np.abs(x), np.abs(y)) > radius
                    ].sum()
                )
                for radius in (8, 12, 16, 24, 32)
            },
        })
    return {
        "slopeFit": (
            "least squares through the amplitude-zero baseline, "
            "jointly over R/B positive and G negative steps"
        ),
        "phaseRecords": records,
        "interiorSlopeRangeCodesPerAmplitude": [
            float(interior.min()),
            float(interior.max()),
        ],
    }


def _half_unorm_envelopes() -> tuple[
    NDArray[np.float16],
    NDArray[np.float16],
]:
    bits = np.arange(0x3C01, dtype=np.uint16)
    values = bits.view(np.float16)
    codes = np.clip(
        np.rint(values.astype(np.float32) * np.float32(255)),
        0,
        255,
    ).astype(np.uint8)
    minimum = np.empty(256, dtype=np.float16)
    maximum = np.empty(256, dtype=np.float16)
    for code in range(256):
        selected = values[codes == code]
        if selected.size == 0:
            raise AssertionError(f"UNORM code {code} has no binary16 image")
        minimum[code] = selected[0]
        maximum[code] = selected[-1]
    return minimum, maximum


def mip_blend_envelope(
    level_zero: UInt8Array,
    blended: UInt8Array,
    level_one: UInt8Array,
) -> JsonObject:
    if not (
        level_zero.shape == blended.shape == level_one.shape
    ):
        raise ValueError("mip blend stream shapes differ")
    minimum, maximum = _half_unorm_envelopes()
    incompatible = 0
    values = 0
    maximum_distance = 0
    code_predictions = {
        "floor": 0,
        "nearestEven": 0,
        "ceil": 0,
    }
    for amplitude in EXPECTED_AMPLITUDES:
        low = half_linear_ties_up(
            minimum[level_zero[amplitude]],
            minimum[level_one[amplitude]],
            MIP_BLEND_NUMERATOR,
            denominator=MIP_BLEND_DENOMINATOR,
        )
        high = half_linear_ties_up(
            maximum[level_zero[amplitude]],
            maximum[level_one[amplitude]],
            MIP_BLEND_NUMERATOR,
            denominator=MIP_BLEND_DENOMINATOR,
        )
        low_code = np.clip(
            np.rint(low.astype(np.float32) * np.float32(255)),
            0,
            255,
        ).astype(np.int16)
        high_code = np.clip(
            np.rint(high.astype(np.float32) * np.float32(255)),
            0,
            255,
        ).astype(np.int16)
        actual = blended[amplitude].astype(np.int16)
        outside = (actual < low_code) | (actual > high_code)
        incompatible += int(np.count_nonzero(outside))
        values += actual.size
        distance = np.maximum(low_code - actual, actual - high_code)
        maximum_distance = max(
            maximum_distance,
            int(np.maximum(distance, 0).max(initial=0)),
        )

        continuous = (
            (MIP_BLEND_DENOMINATOR - MIP_BLEND_NUMERATOR)
            * level_zero[amplitude].astype(np.float64)
            + MIP_BLEND_NUMERATOR
            * level_one[amplitude].astype(np.float64)
        ) / MIP_BLEND_DENOMINATOR
        for name, prediction in (
            ("floor", np.floor(continuous)),
            ("nearestEven", np.rint(continuous)),
            ("ceil", np.ceil(continuous)),
        ):
            code_predictions[name] += int(
                np.count_nonzero(prediction != actual)
            )
    return {
        "fraction": {
            "numerator": MIP_BLEND_NUMERATOR,
            "denominator": MIP_BLEND_DENOMINATOR,
            "exact": "37/64",
        },
        "binary16EndpointEnvelope": {
            "values": values,
            "incompatibleValues": incompatible,
            "compatibleFraction": 1.0 - incompatible / values,
            "maximumOutsideDistanceCodes": maximum_distance,
            "allCompatible": incompatible == 0,
        },
        "roundedCodeDomainDiagnostics": {
            name: {
                "changedValues": changed,
                "exactValueFraction": 1.0 - changed / values,
            }
            for name, changed in code_predictions.items()
        },
    }


def _recovered_face_from_half(values: NDArray[np.float16]) -> UInt8Array:
    matrix = RECOVERED_MATRIX_BITS.view(np.float16)
    bias = np.asarray(
        (EXPECTED_BIAS_BITS,),
        dtype=np.uint16,
    ).view(np.float16)[0]
    channels = []
    for row in matrix:
        accumulator = np.zeros(values.shape[:-1], dtype=np.float16)
        for channel in range(CHANNELS):
            accumulator = half_fused_multiply_add(
                values[..., channel],
                row[channel],
                accumulator,
            )
        accumulator = half_fused_multiply_add(
            np.float16(1),
            bias,
            accumulator,
        )
        channels.append(accumulator)
    result = np.stack(channels, axis=-1)
    result = half_fused_multiply_add(
        result,
        np.float16(0.97),
        np.zeros_like(result),
    )
    return np.clip(
        np.rint(result.astype(np.float32) * np.float32(255)),
        0,
        255,
    ).astype(np.uint8)


def face_stage_envelope(
    identity: UInt8Array,
    clear: UInt8Array,
) -> JsonObject:
    if identity.shape != clear.shape:
        raise ValueError("face-stage stream shapes differ")
    minimum, maximum = _half_unorm_envelopes()
    replay_changed = 0
    incompatible = 0
    maximum_distance = 0
    values = 0
    for amplitude in EXPECTED_AMPLITUDES:
        actual = clear[amplitude]
        replay = recovered_half_face(
            identity[amplitude].reshape(-1, CHANNELS).astype(np.int64)
        ).reshape(actual.shape)
        replay_changed += int(np.count_nonzero(replay != actual))

        low = _recovered_face_from_half(
            minimum[identity[amplitude]]
        ).astype(np.int16)
        high = _recovered_face_from_half(
            maximum[identity[amplitude]]
        ).astype(np.int16)
        lower = np.minimum(low, high)
        upper = np.maximum(low, high)
        actual_int = actual.astype(np.int16)
        outside = (actual_int < lower) | (actual_int > upper)
        incompatible += int(np.count_nonzero(outside))
        distance = np.maximum(
            lower - actual_int,
            actual_int - upper,
        )
        maximum_distance = max(
            maximum_distance,
            int(np.maximum(distance, 0).max(initial=0)),
        )
        values += actual.size
    return {
        "roundedIdentityReplay": {
            "values": values,
            "changedValues": replay_changed,
            "exactValueFraction": 1.0 - replay_changed / values,
        },
        "binary16InputEnvelope": {
            "values": values,
            "incompatibleValues": incompatible,
            "compatibleFraction": 1.0 - incompatible / values,
            "maximumOutsideDistanceCodes": maximum_distance,
            "allCompatible": incompatible == 0,
        },
    }


def analyze(path: Path) -> JsonObject:
    started = time.perf_counter()
    sweep = KernelSweep.open(path)
    identity = sweep.interventions
    controls = source_fidelity(sweep)
    stream_records = {
        name: {
            "baselineHistogram": baseline_histogram(stream),
            "signedChannelDiagnostics":
                signed_channel_diagnostics(stream),
            "cumulativeKernel": kernel_measurements(
                sweep,
                stream,
            ),
        }
        for name, stream in identity.items()
    }
    mip = mip_blend_envelope(
        identity["identity-blur-0"],
        identity["identity-blur-1"],
        identity["identity-blur-2"],
    )
    face = face_stage_envelope(
        identity["identity-blur-1"],
        sweep.clear,
    )
    return {
        "liquidGlassKernelSweepAnalysisSchemaVersion": 1,
        "analysisImplementation": {
            "file": "analysis/liquid_glass_kernel_sweep.py",
            "sha256": file_sha256(Path(__file__).resolve()),
            "python": platform.python_version(),
            "numpy": package_version("numpy"),
        },
        "source": {
            "path": str(path),
            "sha256": file_sha256(path) if path.is_file() else None,
            "rigVersion": sweep.manifest["rigVersion"],
            "ciCommit": sweep.manifest["ciCommit"],
            "osVersion": sweep.manifest["osVersion"],
            "architecture": sweep.manifest["architecture"],
            "nativeCaptureEvidence":
                sweep.manifest["nativeCaptureEvidence"],
        },
        "controls": {
            "sourceFidelity": controls,
            "clearBaselineHistogram":
                baseline_histogram(sweep.clear),
        },
        "identityStreams": stream_records,
        "mipBlend": mip,
        "faceStage": face,
        "resourceMeasurements": {
            "analysisSeconds": time.perf_counter() - started,
            "maximumResidentSetKiB":
                resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        },
        "conclusion": {
            "captureControlsExact": controls["exact"],
            "productionShaderAuthorized": False,
            "requiredGate":
                "zero unequal channels on protected Apple captures",
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Analyze the native Liquid Glass cumulative-kernel sweep."
    )
    parser.add_argument("kernel_sweep", type=Path)
    parser.add_argument("-o", "--output", type=Path)
    arguments = parser.parse_args()
    result = analyze(arguments.kernel_sweep)
    encoded = json.dumps(
        result,
        indent=2,
        sort_keys=True,
        allow_nan=False,
    ) + "\n"
    if arguments.output:
        arguments.output.write_text(encoded, encoding="utf-8")
        print(arguments.output)
    else:
        print(encoded, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
