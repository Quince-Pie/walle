#!/usr/bin/env python3
"""Validate and measure the native Liquid Glass spatial sweep."""

import argparse
import hashlib
import json
import platform
import resource
import time
import zipfile
from collections import Counter, defaultdict
from dataclasses import dataclass
from importlib.metadata import version as package_version
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from liquid_glass_native_capture import (
    RECOVERED_MATRIX_BITS,
    recovered_half_face,
)
from liquid_glass_sampler_probe import rgba8_unorm_linear_ties_up


type IntArray = NDArray[np.int64]
type UInt8Array = NDArray[np.uint8]
type JsonObject = dict[str, Any]

EXPECTED_RIGS = {
    "native-spatial-sweep-1.0.0": 1,
    "native-spatial-sweep-1.1.0": 2,
}
EXPECTED_AMPLITUDES = tuple(range(128))
EXPECTED_INTERVENTIONS = (
    "identity-blur-0",
    "identity-blur-1",
    "identity-blur-2",
    "identity-blur-4",
)
CHANNELS = 3
BLUR_ZERO_WEIGHT_NUMERATORS = np.asarray(
    (
        (16, 48, 48, 16),
        (48, 144, 144, 48),
        (48, 144, 144, 48),
        (16, 48, 48, 16),
    ),
    dtype=np.int64,
)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def bytes_sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


@dataclass(frozen=True, slots=True)
class SpatialSweep:
    manifest: JsonObject
    control: UInt8Array
    clear: UInt8Array
    interventions: dict[str, UInt8Array]

    @property
    def sites(self) -> list[JsonObject]:
        return self.manifest["sourceDesign"]["sites"]

    @property
    def patch_side(self) -> int:
        return int(self.manifest["sourceDesign"]["patchSidePixels"])

    @property
    def patch_radius(self) -> int:
        return int(self.manifest["sourceDesign"]["patchRadiusPixels"])

    @classmethod
    def open(cls, path: Path) -> "SpatialSweep":
        if path.is_dir():
            manifest = json.loads(
                (path / "manifest.json").read_text(encoding="utf-8")
            )

            def read(name: str) -> bytes:
                return (path / name).read_bytes()

        else:
            archive = zipfile.ZipFile(path)
            try:
                manifest = json.loads(archive.read("manifest.json"))
                members = {
                    name: archive.read(name)
                    for name in archive.namelist()
                }
            finally:
                archive.close()

            def read(name: str) -> bytes:
                try:
                    return members[name]
                except KeyError as error:
                    raise ValueError(
                        f"spatial sweep member is missing: {name}"
                    ) from error

        rig = manifest.get("rigVersion")
        if rig not in EXPECTED_RIGS:
            raise ValueError(f"unexpected spatial sweep rig: {rig!r}")
        if manifest.get("schemaVersion") != EXPECTED_RIGS[rig]:
            raise ValueError("spatial sweep schema differs")
        source = manifest.get("sourceDesign")
        if not isinstance(source, dict):
            raise ValueError("spatial sweep source design is missing")
        amplitudes = tuple(source.get("amplitudesCodes", ()))
        if amplitudes != EXPECTED_AMPLITUDES:
            raise ValueError("spatial sweep amplitudes differ")
        sites = source.get("sites")
        if not isinstance(sites, list) or len(sites) != 36:
            raise ValueError("spatial sweep site catalog differs")
        patch_side = source.get("patchSidePixels")
        patch_radius = source.get("patchRadiusPixels")
        if patch_side != 33 or patch_radius != 16:
            raise ValueError("spatial sweep patch geometry differs")

        evidence = manifest.get("nativeCaptureEvidence")
        if not isinstance(evidence, dict):
            raise ValueError("native spatial evidence is missing")
        if evidence.get("schemaVersion") != EXPECTED_RIGS[rig]:
            raise ValueError("native spatial evidence schema differs")
        if (
            evidence.get("recordFormat") != "RGB8"
            or evidence.get("recordStrideBytes") != CHANNELS
        ):
            raise ValueError("native spatial record format differs")
        record_count = len(amplitudes) * len(sites) * patch_side**2
        if evidence.get("recordCount") != record_count:
            raise ValueError("native spatial record count differs")
        shape = (
            len(amplitudes),
            len(sites),
            patch_side,
            patch_side,
            CHANNELS,
        )

        def decode(record: JsonObject) -> UInt8Array:
            data = read(str(record["file"]))
            expected_bytes = record_count * CHANNELS
            if (
                len(data) != expected_bytes
                or record.get("fileBytes") != expected_bytes
            ):
                raise ValueError(
                    f"native spatial stream length differs: {record['file']}"
                )
            if bytes_sha256(data) != record.get("fileSha256"):
                raise ValueError(
                    f"native spatial stream hash differs: {record['file']}"
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
        intervention_records = evidence.get("interventions", [])
        interventions = {
            str(record["name"]): decode(record)
            for record in intervention_records
        }
        expected_interventions = (
            set(EXPECTED_INTERVENTIONS) if EXPECTED_RIGS[rig] >= 2 else set()
        )
        if set(interventions) != expected_interventions:
            raise ValueError("native spatial intervention catalog differs")
        return cls(
            manifest=manifest,
            control=control,
            clear=clear,
            interventions=interventions,
        )


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


def source_fidelity(sweep: SpatialSweep) -> JsonObject:
    expected = np.broadcast_to(
        sweep.control[0:1],
        sweep.control.shape,
    ).copy()
    center = sweep.patch_radius
    block_size = int(sweep.manifest["sourceDesign"]["blockWidth"])
    for site_index, site in enumerate(sweep.sites):
        channel = int(site["sourceChannelIndex"])
        sign = int(site["sourceSign"])
        for amplitude in EXPECTED_AMPLITUDES:
            expected[
                amplitude,
                site_index,
                center : center + block_size,
                center : center + block_size,
                channel,
            ] = 128 + sign * amplitude
    baseline_values, baseline_counts = np.unique(
        sweep.control[0],
        return_counts=True,
    )
    return {
        "baselineHistogram": {
            str(int(value)): int(count)
            for value, count in zip(
                baseline_values,
                baseline_counts,
                strict=True,
            )
        },
        "expectedSourceComparison": difference_metrics(
            expected,
            sweep.control,
        ),
    }


def rgba8_blur_zero_patch(
    source_code: int,
    *,
    baseline_code: int = 128,
) -> UInt8Array:
    if (
        not 0 <= source_code <= 255
        or not 0 <= baseline_code <= 255
    ):
        raise ValueError("blur-zero source code is outside [0, 255]")
    result = np.empty((4, 4), dtype=np.uint8)
    for y in range(4):
        for x in range(4):
            value = rgba8_unorm_linear_ties_up(
                np.asarray(baseline_code),
                np.asarray(source_code),
                int(BLUR_ZERO_WEIGHT_NUMERATORS[y, x]),
            )
            result[y, x] = np.uint8(
                np.rint(np.float32(value) * np.float32(255))
            )
    return result


def rgba8_blur_zero_replay(
    sweep: SpatialSweep,
) -> JsonObject | None:
    actual = sweep.interventions.get("identity-blur-0")
    if actual is None:
        return None
    predicted = np.full(actual.shape, 128, dtype=np.uint8)
    center = sweep.patch_radius
    for amplitude in EXPECTED_AMPLITUDES:
        for site_index, site in enumerate(sweep.sites):
            channel = int(site["sourceChannelIndex"])
            source_code = 128 + int(site["sourceSign"]) * amplitude
            predicted[
                amplitude,
                site_index,
                center - 1 : center + 3,
                center - 1 : center + 3,
                channel,
            ] = rgba8_blur_zero_patch(source_code)
    result = difference_metrics(predicted, actual)
    result["model"] = (
        "RGBA8 UNORM bilinear reconstruction with effective source "
        "weights 1/16, 3/16, and 9/16; interpolate in code units, "
        "round to 1/16 code with midpoint ties upward, divide by 255, "
        "convert to binary16, then convert to native RGB8"
    )
    return result


def site_groups(
    sweep: SpatialSweep,
) -> dict[tuple[int, int, int, int], list[int]]:
    groups: dict[tuple[int, int, int, int], list[int]] = defaultdict(list)
    for index, site in enumerate(sweep.sites):
        key = (
            int(site["halfGridPhaseY"]),
            int(site["halfGridPhaseX"]),
            int(site["sourceChannelIndex"]),
            int(site["sourceSign"]),
        )
        groups[key].append(index)
    return groups


def replicate_determinism(
    sweep: SpatialSweep,
    stream: UInt8Array,
) -> JsonObject:
    comparisons = 0
    changed_values = 0
    changed_captures = 0
    maximum = 0
    for indices in site_groups(sweep).values():
        reference = stream[:, indices[0]].astype(np.int16)
        for index in indices[1:]:
            delta = reference - stream[:, index].astype(np.int16)
            comparisons += stream.shape[0]
            changed_values += int(np.count_nonzero(delta))
            changed_captures += int(
                np.count_nonzero(np.any(delta != 0, axis=(1, 2, 3)))
            )
            maximum = max(maximum, int(np.abs(delta).max(initial=0)))
    return {
        "captureComparisons": comparisons,
        "changedCaptures": changed_captures,
        "changedValues": changed_values,
        "maximumAbsoluteCodes": maximum,
        "exact": changed_values == 0,
    }


def canonicalize_phase(
    values: UInt8Array,
    *,
    phase_y: int,
    phase_x: int,
) -> UInt8Array:
    result = values
    if phase_y:
        result = np.roll(result[:, ::-1], 1, axis=1)
    if phase_x:
        result = np.roll(result[:, :, ::-1], 1, axis=2)
    return result


def phase_symmetry(
    sweep: SpatialSweep,
    stream: UInt8Array,
) -> JsonObject:
    groups = site_groups(sweep)
    comparisons = 0
    changed_values = 0
    maximum = 0
    records = []
    for channel in range(CHANNELS):
        for sign in (-1, 1):
            reference = stream[:, groups[(0, 0, channel, sign)][0]]
            for phase_y in range(2):
                for phase_x in range(2):
                    candidate = canonicalize_phase(
                        stream[
                            :,
                            groups[
                                (phase_y, phase_x, channel, sign)
                            ][0],
                        ],
                        phase_y=phase_y,
                        phase_x=phase_x,
                    )
                    delta = (
                        reference.astype(np.int16)
                        - candidate.astype(np.int16)
                    )
                    changed = int(np.count_nonzero(delta))
                    comparisons += delta.size
                    changed_values += changed
                    maximum = max(
                        maximum,
                        int(np.abs(delta).max(initial=0)),
                    )
                    records.append({
                        "channel": channel,
                        "sign": sign,
                        "phaseY": phase_y,
                        "phaseX": phase_x,
                        "changedValues": changed,
                    })
    return {
        "values": comparisons,
        "changedValues": changed_values,
        "maximumAbsoluteCodes": maximum,
        "exact": changed_values == 0,
        "records": records,
    }


def support_metrics(
    sweep: SpatialSweep,
    stream: UInt8Array,
) -> JsonObject:
    radius = sweep.patch_radius
    y, x = np.mgrid[-radius : radius + 1, -radius : radius + 1]
    chebyshev = np.maximum(np.abs(y), np.abs(x))
    delta = stream.astype(np.int16) - stream[0:1].astype(np.int16)
    changed_union = np.any(delta != 0, axis=(0, 1, 4))
    changed_radii = chebyshev[changed_union]
    return {
        "unionChangedCoordinates": int(np.count_nonzero(changed_union)),
        "maximumChangedChebyshevRadius": (
            int(changed_radii.max()) if changed_radii.size else None
        ),
        "byRadius": [
            {
                "radius": current,
                "changedCoordinates": int(
                    np.count_nonzero(
                        changed_union & (chebyshev == current)
                    )
                ),
                "changedValues": int(
                    np.count_nonzero(
                        delta[:, :, chebyshev == current]
                    )
                ),
                "maximumAbsoluteCodes": int(
                    np.abs(
                        delta[:, :, chebyshev == current]
                    ).max(initial=0)
                ),
            }
            for current in range(radius + 1)
        ],
    }


def face_replay(
    sweep: SpatialSweep,
) -> JsonObject | None:
    identity = sweep.interventions.get("identity-blur-1")
    if identity is None:
        return None
    predicted = recovered_half_face(
        identity.reshape(-1, CHANNELS).astype(np.int64)
    ).reshape(identity.shape)
    return difference_metrics(predicted, sweep.clear)


def mip_code_blend(sweep: SpatialSweep) -> JsonObject | None:
    level_zero = sweep.interventions.get("identity-blur-0")
    blended = sweep.interventions.get("identity-blur-1")
    level_one = sweep.interventions.get("identity-blur-2")
    if level_zero is None or blended is None or level_one is None:
        return None
    weight = np.log2(1.5)
    continuous = (
        (1.0 - weight) * level_zero.astype(np.float64)
        + weight * level_one.astype(np.float64)
    )
    candidates = {
        "nearestEven": np.rint(continuous),
        "floor": np.floor(continuous),
        "ceil": np.ceil(continuous),
    }
    return {
        "levelOneWeight": weight,
        "codeDomainDiagnostics": {
            name: difference_metrics(
                np.clip(value, 0, 255).astype(np.uint8),
                blended,
            )
            for name, value in candidates.items()
        },
    }


def analyze(path: Path) -> JsonObject:
    started = time.perf_counter()
    sweep = SpatialSweep.open(path)
    streams = {
        "baselineClear": sweep.clear,
        **sweep.interventions,
    }
    baseline_values, baseline_counts = np.unique(
        sweep.clear[0],
        return_counts=True,
    )
    return {
        "liquidGlassSpatialSweepAnalysisSchemaVersion": 1,
        "analysisImplementation": {
            "file": "analysis/liquid_glass_spatial_sweep.py",
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
        "controls": source_fidelity(sweep),
        "clearBaselineHistogram": {
            str(int(value)): int(count)
            for value, count in zip(
                baseline_values,
                baseline_counts,
                strict=True,
            )
        },
        "streams": {
            name: {
                "replicateDeterminism":
                    replicate_determinism(sweep, stream),
                "phaseSymmetry": phase_symmetry(sweep, stream),
                "support": support_metrics(sweep, stream),
            }
            for name, stream in streams.items()
        },
        "recoveredPointStageReplay": face_replay(sweep),
        "rgba8IdentityBlurZeroReplay":
            rgba8_blur_zero_replay(sweep),
        "explicitMipBlendDiagnostic": mip_code_blend(sweep),
        "resourceMeasurements": {
            "analysisSeconds": time.perf_counter() - started,
            "maximumResidentSetKiB":
                resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        },
        "conclusion": {
            "productionShaderAuthorized": False,
            "requiredGate":
                "zero unequal channels on protected Apple captures",
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Analyze the native Liquid Glass spatial sweep."
    )
    parser.add_argument("spatial_sweep", type=Path)
    parser.add_argument("-o", "--output", type=Path)
    arguments = parser.parse_args()
    result = analyze(arguments.spatial_sweep)
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
