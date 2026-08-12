#!/usr/bin/env python3
"""Validate and measure the same-tile Liquid Glass stripe sweep."""

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


type FloatArray = NDArray[np.float64]
type IntArray = NDArray[np.int64]
type UInt8Array = NDArray[np.uint8]
type JsonObject = dict[str, Any]

EXPECTED_SCHEMA = 1
EXPECTED_SWEEP_KIND = "same-tile-phase-controlled-production-stripes"
EXPECTED_AMPLITUDES = tuple(range(128))
EXPECTED_ORIENTATIONS = ("vertical", "horizontal")
EXPECTED_POSITIONS = (304, 362, 420, 478)
EXPECTED_TRANSITION_SIGNS = (1, -1, 1, -1)
EXPECTED_PHASES = (0, 1, 2, 3)
EXPECTED_PATCH_RADIUS = 24
EXPECTED_PATCH_SIDE = 49
EXPECTED_CROSS_AXIS_CENTER = 384
SOURCE_CODE = 128
CHANNELS = 3
CHANNEL_SIGNS = np.asarray((1, -1, 1), dtype=np.int16)
MEASURED_SUPPORT_RADIUS = 12
RIG_CONFIGS: dict[str, JsonObject] = {
    "native-stripe-sweep-1.0.0": {
        "positions": EXPECTED_POSITIONS,
        "patchRadius": EXPECTED_PATCH_RADIUS,
        "intervals": [[304, 362], [420, 478]],
        "minimumSpacing": 58,
        "minimumTileDistance": 34,
        "phaseDirectionPairs": None,
    },
    "native-stripe-sweep-1.1.0": {
        "positions": (280, 312, 338, 370, 396, 428, 454, 486),
        "patchRadius": 13,
        "intervals": [
            [280, 312],
            [338, 370],
            [396, 428],
            [454, 486],
        ],
        "minimumSpacing": 26,
        "minimumTileDistance": 24,
        "phaseDirectionPairs": True,
    },
}


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


def _expected_edges(positions: tuple[int, ...]) -> list[JsonObject]:
    return [
        {
            "index": index,
            "position": position,
            "reducedGridPhase": (position // 2) & 3,
            "transitionSign": 1 if index % 2 == 0 else -1,
            "distanceFromCandidateTileStart": position - 256,
            "distanceFromCandidateTileEnd": 512 - position,
        }
        for index, position in enumerate(positions)
    ]


def _checked_catalog(manifest: JsonObject) -> JsonObject:
    rig = manifest.get("rigVersion")
    if rig not in RIG_CONFIGS:
        raise ValueError(
            f"unexpected native stripe rig: {manifest.get('rigVersion')!r}"
        )
    config = RIG_CONFIGS[str(rig)]
    if manifest.get("schemaVersion") != EXPECTED_SCHEMA:
        raise ValueError("native stripe manifest schema differs")
    if manifest.get("sweepKind") != EXPECTED_SWEEP_KIND:
        raise ValueError("native stripe sweep kind differs")
    source = manifest.get("sourceDesign")
    if not isinstance(source, dict):
        raise ValueError("native stripe source design is missing")
    positions = tuple(config["positions"])
    patch_radius = int(config["patchRadius"])
    expected_fields: JsonObject = {
        "baseCode": SOURCE_CODE,
        "amplitudesCodes": list(EXPECTED_AMPLITUDES),
        "channelSigns": {"red": 1, "green": -1, "blue": 1},
        "orientationOrder": list(EXPECTED_ORIENTATIONS),
        "alternatingInsideIntervals": config["intervals"],
        "edges": _expected_edges(positions),
        "edgeMinimumSpacingPixels": config["minimumSpacing"],
        "patchRadiusPixels": patch_radius,
        "patchSidePixels": 2 * patch_radius + 1,
        "crossAxisCenter": EXPECTED_CROSS_AXIS_CENTER,
        "candidateTileInterval": [256, 512],
        "minimumEdgeDistanceFromCandidateTileBoundary":
            config["minimumTileDistance"],
    }
    if config["phaseDirectionPairs"] is not None:
        expected_fields |= {
            "phaseDirectionPairs": True,
            "priorMeasuredSupportRadiusUpperBoundPixels":
                MEASURED_SUPPORT_RADIUS,
            "minimumGapBeyondPairedMeasuredSupportsPixels": 2,
        }
    for key, expected in expected_fields.items():
        if source.get(key) != expected:
            raise ValueError(f"native stripe source field differs: {key}")

    fixed = manifest.get("fixedFilterState")
    if not isinstance(fixed, dict) or fixed != {
        "inputBlurRadius": 1,
        "inputBlurRadiusFloat32Bits": "3f800000",
        "inputFaceColorMatrixBlack": 0,
        "inputFaceColorMatrixSaturation": 1,
        "inputFaceColorMatrixWhite": 1,
        "inputSDRHoldingToneEnabled": False,
    }:
        raise ValueError("native stripe fixed filter state differs")

    captures = manifest.get("captures")
    if not isinstance(captures, list) or len(captures) != 128:
        raise ValueError("native stripe capture catalog differs")
    for amplitude, record in enumerate(captures):
        if record.get("amplitudeCodes") != amplitude:
            raise ValueError(
                f"native stripe amplitude differs at {amplitude}"
            )
        orientations = record.get("orientations")
        if not isinstance(orientations, list) or tuple(
            candidate.get("orientation") for candidate in orientations
        ) != EXPECTED_ORIENTATIONS:
            raise ValueError(
                f"native stripe orientations differ at {amplitude}"
            )
        for candidate in orientations:
            if (
                candidate.get("captureBackend")
                != "CGWindowListCreateImage"
                or int(candidate.get("controlStabilitySamples", 0)) < 2
                or int(candidate.get("materializedStabilitySamples", 0)) < 2
                or int(candidate.get("identityStabilitySamples", 0)) < 2
                or candidate.get("inputBlurRadiusReadback") != 1
                or candidate.get("inputBlurRadiusReadbackFloat32Bits")
                != "3f800000"
            ):
                raise ValueError(
                    "native stripe capture state differs at "
                    f"{amplitude}/{candidate.get('orientation')}"
                )
    return config


@dataclass(frozen=True, slots=True)
class StripeSweep:
    manifest: JsonObject
    control: UInt8Array
    identity: UInt8Array

    @classmethod
    def open(cls, path: Path) -> "StripeSweep":
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
                    raise ValueError("native stripe archive has duplicate members")
                try:
                    manifest = json.loads(archive.read("manifest.json"))
                except KeyError as error:
                    raise ValueError(
                        "native stripe manifest is missing"
                    ) from error
                members = {
                    name: archive.read(name)
                    for name in names
                    if name.startswith("native-stripe-")
                    or name.endswith(".png")
                }

            def read(name: str) -> bytes:
                try:
                    return members[name]
                except KeyError as error:
                    raise ValueError(
                        f"native stripe member is missing: {name}"
                    ) from error

        config = _checked_catalog(manifest)
        evidence = manifest.get("nativeCaptureEvidence")
        if not isinstance(evidence, dict):
            raise ValueError("native stripe evidence is missing")
        expected_order = (
            "amplitude ascending, orientation vertical then horizontal, "
            "edge order, patch y-major then x-major"
        )
        if (
            evidence.get("schemaVersion") != EXPECTED_SCHEMA
            or evidence.get("recordOrder") != expected_order
            or evidence.get("recordFormat") != "RGB8"
            or evidence.get("recordStrideBytes") != CHANNELS
        ):
            raise ValueError("native stripe record format differs")
        record_count = (
            len(EXPECTED_AMPLITUDES)
            * len(EXPECTED_ORIENTATIONS)
            * len(config["positions"])
            * (2 * int(config["patchRadius"]) + 1) ** 2
        )
        if evidence.get("recordCount") != record_count:
            raise ValueError("native stripe record count differs")
        patch_side = 2 * int(config["patchRadius"]) + 1
        shape = (
            len(EXPECTED_AMPLITUDES),
            len(EXPECTED_ORIENTATIONS),
            len(config["positions"]),
            patch_side,
            patch_side,
            CHANNELS,
        )

        def decode(prefix: str) -> UInt8Array:
            name = str(evidence[f"{prefix}File"])
            data = read(name)
            expected_bytes = record_count * CHANNELS
            if (
                len(data) != expected_bytes
                or evidence.get(f"{prefix}FileBytes") != expected_bytes
            ):
                raise ValueError(
                    f"native stripe stream length differs: {name}"
                )
            if bytes_sha256(data) != evidence.get(f"{prefix}FileSha256"):
                raise ValueError(
                    f"native stripe stream hash differs: {name}"
                )
            return np.frombuffer(data, dtype=np.uint8).reshape(shape)

        control = decode("control")
        identity = decode("identity")
        if icc_name := evidence.get("iccFile"):
            icc = read(str(icc_name))
            if (
                len(icc) != evidence.get("iccFileBytes")
                or bytes_sha256(icc) != evidence.get("iccFileSha256")
            ):
                raise ValueError("native stripe ICC evidence differs")
        for record in manifest["captures"]:
            for candidate in record["orientations"]:
                for kind in ("source", "control", "identity"):
                    name = candidate.get(f"{kind}File")
                    if name is None:
                        continue
                    data = read(str(name))
                    if bytes_sha256(data) != candidate.get(
                        f"{kind}FileSha256"
                    ):
                        raise ValueError(
                            f"native stripe audit hash differs: {name}"
                        )
        return cls(
            manifest=manifest,
            control=control,
            identity=identity,
        )


def expected_control(shape: tuple[int, ...]) -> UInt8Array:
    edge_count = shape[2]
    patch_side = shape[3]
    if patch_side != shape[4] or patch_side % 2 != 1:
        raise ValueError("native stripe patch shape differs")
    patch_radius = patch_side // 2
    expected_shape = (
        len(EXPECTED_AMPLITUDES),
        len(EXPECTED_ORIENTATIONS),
        edge_count,
        patch_side,
        patch_side,
        CHANNELS,
    )
    if shape != expected_shape:
        raise ValueError("native stripe control shape differs")
    result = np.full(shape, SOURCE_CODE, dtype=np.uint8)
    offsets = (
        np.arange(patch_side, dtype=np.int16)
        - patch_radius
    )
    for amplitude in EXPECTED_AMPLITUDES:
        for edge in range(edge_count):
            sign = 1 if edge % 2 == 0 else -1
            inside = offsets >= 0 if sign > 0 else offsets < 0
            codes = (
                SOURCE_CODE
                + amplitude * CHANNEL_SIGNS.astype(np.int16)
            ).astype(np.uint8)
            result[amplitude, 0, edge, :, inside, :] = codes
            result[amplitude, 1, edge, inside, :, :] = codes
    return result


def source_fidelity(sweep: StripeSweep) -> JsonObject:
    expected = expected_control(sweep.control.shape)
    result = difference_metrics(expected, sweep.control)
    result["exact"] = result["changedValues"] == 0
    return result


def baseline_histogram(stream: UInt8Array) -> dict[str, int]:
    values, counts = np.unique(stream[0], return_counts=True)
    return {
        str(int(value)): int(count)
        for value, count in zip(values, counts, strict=True)
    }


def signed_channel_diagnostics(stream: UInt8Array) -> JsonObject:
    red_blue_changed = int(
        np.count_nonzero(stream[..., 0] != stream[..., 2])
    )
    current = stream.astype(np.int16)
    residual = current[..., 0] + current[..., 1] - 2 * SOURCE_CODE
    values, counts = np.unique(residual, return_counts=True)
    return {
        "redBlueValues": int(stream[..., 0].size),
        "redBlueChangedValues": red_blue_changed,
        "redBlueExact": red_blue_changed == 0,
        "positiveNegativeSymmetryResidualHistogram": {
            str(int(value)): int(count)
            for value, count in zip(values, counts, strict=True)
        },
        "maximumPositiveNegativeSymmetryErrorCodes":
            int(np.abs(residual).max(initial=0)),
    }


def orthogonal_invariance(stream: UInt8Array) -> JsonObject:
    center = stream.shape[3] // 2
    vertical_reference = np.broadcast_to(
        stream[:, 0, :, center:center + 1, :, :],
        stream[:, 0].shape,
    )
    horizontal_reference = np.broadcast_to(
        stream[:, 1, :, :, center:center + 1, :],
        stream[:, 1].shape,
    )
    return {
        "verticalRows": difference_metrics(
            vertical_reference,
            stream[:, 0],
        ),
        "horizontalColumns": difference_metrics(
            horizontal_reference,
            stream[:, 1],
        ),
    }


def orientation_isotropy(stream: UInt8Array) -> JsonObject:
    horizontal_transposed = np.swapaxes(stream[:, 1], 2, 3)
    return difference_metrics(stream[:, 0], horizontal_transposed)


def orthogonal_state_runs(
    stream: UInt8Array,
    positions: tuple[int, ...],
    cross_axis_center: int,
) -> list[JsonObject]:
    if len(positions) != stream.shape[2]:
        raise ValueError("native stripe position catalog differs")
    patch_side = stream.shape[3]
    patch_radius = patch_side // 2
    records: list[JsonObject] = []
    for orientation, orientation_name in enumerate(EXPECTED_ORIENTATIONS):
        for edge, position in enumerate(positions):
            current = stream[:, orientation, edge]
            if orientation == 1:
                current = np.swapaxes(current, 1, 2)
            profiles = np.ascontiguousarray(
                current.transpose(1, 0, 2, 3)
            ).reshape(patch_side, -1)
            _, inverse = np.unique(
                profiles,
                axis=0,
                return_inverse=True,
            )
            ordered_ids: dict[int, int] = {}
            states = np.empty_like(inverse)
            for index, old_id in enumerate(inverse):
                state = ordered_ids.setdefault(
                    int(old_id),
                    len(ordered_ids),
                )
                states[index] = state
            runs = []
            start = 0
            for stop in range(1, patch_side + 1):
                if (
                    stop != patch_side
                    and states[stop] == states[start]
                ):
                    continue
                row = start
                runs.append({
                    "state": int(states[start]),
                    "startCoordinate":
                        cross_axis_center - patch_radius + start,
                    "endCoordinateInclusive":
                        cross_axis_center - patch_radius + stop - 1,
                    "rows": stop - start,
                    "profileSha256": bytes_sha256(
                        profiles[row].tobytes()
                    ),
                })
                start = stop
            records.append({
                "orientation": orientation_name,
                "edgeIndex": edge,
                "position": position,
                "phase": (position // 2) & 3,
                "crossAxisCenter": cross_axis_center,
                "stateCount": len(ordered_ids),
                "runs": runs,
            })
    return records


def signed_step_slopes(stream: UInt8Array) -> FloatArray:
    if (
        stream.ndim != 6
        or stream.shape[0] != len(EXPECTED_AMPLITUDES)
        or stream.shape[1] != len(EXPECTED_ORIENTATIONS)
        or stream.shape[3] != stream.shape[4]
        or stream.shape[3] % 2 != 1
        or stream.shape[5] != CHANNELS
    ):
        raise ValueError("native stripe stream shape differs")
    baseline = stream[0].astype(np.int16)
    sum_amplitude_squared = sum(
        amplitude * amplitude
        for amplitude in EXPECTED_AMPLITUDES
    )
    denominator = (
        CHANNELS * stream.shape[3] * sum_amplitude_squared
    )
    result = np.zeros(
        (
            len(EXPECTED_ORIENTATIONS),
            stream.shape[2],
            stream.shape[3],
        ),
        dtype=np.float64,
    )
    for amplitude in EXPECTED_AMPLITUDES[1:]:
        delta = stream[amplitude].astype(np.int16) - baseline
        signed = delta * CHANNEL_SIGNS
        result[0] += amplitude * signed[0].sum(axis=(1, 3))
        result[1] += amplitude * signed[1].sum(axis=(2, 3))
    return result / denominator


def response_measurements(
    stream: UInt8Array,
    positions: tuple[int, ...] = EXPECTED_POSITIONS,
) -> JsonObject:
    slopes = signed_step_slopes(stream)
    if len(positions) != stream.shape[2]:
        raise ValueError("native stripe position catalog differs")
    transition_signs_int = tuple(
        1 if index % 2 == 0 else -1
        for index in range(len(positions))
    )
    phases = tuple((position // 2) & 3 for position in positions)
    patch_radius = stream.shape[3] // 2
    plateau_samples = max(
        1,
        min(9, patch_radius - MEASURED_SUPPORT_RADIUS),
    )
    inside = np.empty(
        (len(EXPECTED_ORIENTATIONS), len(positions)),
        dtype=np.float64,
    )
    outside = np.empty_like(inside)
    for edge, sign in enumerate(transition_signs_int):
        if sign > 0:
            outside[:, edge] = np.median(
                slopes[:, edge, :plateau_samples],
                axis=-1,
            )
            inside[:, edge] = np.median(
                slopes[:, edge, -plateau_samples:],
                axis=-1,
            )
        else:
            inside[:, edge] = np.median(
                slopes[:, edge, :plateau_samples],
                axis=-1,
            )
            outside[:, edge] = np.median(
                slopes[:, edge, -plateau_samples:],
                axis=-1,
            )
    gain = inside - outside
    if np.any(gain <= 0):
        raise ValueError("native stripe interior gain is nonpositive")
    normalized = (
        slopes - outside[..., None]
    ) / gain[..., None]
    transition_signs = np.asarray(
        transition_signs_int,
        dtype=np.float64,
    )
    kernels = (
        np.diff(normalized, axis=-1)
        * transition_signs[None, :, None]
    )
    offsets = np.arange(
        -patch_radius + 0.5,
        patch_radius,
        dtype=np.float64,
    )
    records: list[JsonObject] = []
    for orientation, orientation_name in enumerate(EXPECTED_ORIENTATIONS):
        for edge, (phase, sign) in enumerate(
            zip(
                phases,
                transition_signs_int,
                strict=True,
            )
        ):
            kernel = kernels[orientation, edge]
            total = float(kernel.sum())
            absolute = np.abs(kernel)
            normalized_kernel = kernel / total if total else kernel
            records.append({
                "orientation": orientation_name,
                "edgeIndex": edge,
                "position": positions[edge],
                "phase": phase,
                "transitionSign": sign,
                "outsideSlopeCodesPerAmplitude":
                    float(outside[orientation, edge]),
                "insideSlopeCodesPerAmplitude":
                    float(inside[orientation, edge]),
                "gainCodesPerAmplitude":
                    float(gain[orientation, edge]),
                "normalizedStep": normalized[orientation, edge].tolist(),
                "kernel": kernel.tolist(),
                "kernelSum": total,
                "kernelPositiveMass":
                    float(kernel[kernel > 0].sum()),
                "kernelNegativeMass":
                    float(-kernel[kernel < 0].sum()),
                "kernelCenterOfMassPixels": (
                    float(np.sum(normalized_kernel * offsets))
                    if total else None
                ),
                "kernelAbsoluteMassOutsideRadii": {
                    str(radius): float(
                        absolute[np.abs(offsets) > radius].sum()
                    )
                    for radius in (4, 8, 12, 16, 20)
                },
            })
    vertical_horizontal = np.abs(
        normalized[0] - normalized[1]
    )
    return {
        "slopeFit": (
            "least squares through the amplitude-zero baseline, jointly "
            f"over all {stream.shape[3]} orthogonal samples, R/B positive, "
            "and G negative"
        ),
        "plateauSamplesPerSide": plateau_samples,
        "phaseRecords": records,
        "gainRangeCodesPerAmplitude": [
            float(gain.min()),
            float(gain.max()),
        ],
        "verticalHorizontalNormalizedStepMaximumAbsoluteDifference":
            float(vertical_horizontal.max(initial=0)),
        "verticalHorizontalNormalizedStepRootMeanSquareDifference":
            float(np.sqrt(np.mean(vertical_horizontal**2))),
    }


def analyze(path: Path) -> JsonObject:
    started = time.perf_counter()
    sweep = StripeSweep.open(path)
    controls = source_fidelity(sweep)
    orthogonal = orthogonal_invariance(sweep.identity)
    isotropy = orientation_isotropy(sweep.identity)
    positions = tuple(
        int(record["position"])
        for record in sweep.manifest["sourceDesign"]["edges"]
    )
    response = response_measurements(sweep.identity, positions)
    orthogonal_states = orthogonal_state_runs(
        sweep.identity,
        positions,
        int(sweep.manifest["sourceDesign"]["crossAxisCenter"]),
    )
    return {
        "liquidGlassStripeSweepAnalysisSchemaVersion": 1,
        "analysisImplementation": {
            "file": "analysis/liquid_glass_stripe_sweep.py",
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
            "identityBaselineHistogram":
                baseline_histogram(sweep.identity),
        },
        "identityProductionBlurOne": {
            "signedChannelDiagnostics":
                signed_channel_diagnostics(sweep.identity),
            "orthogonalInvariance": orthogonal,
            "orthogonalStateRuns": orthogonal_states,
            "orientationIsotropy": isotropy,
            "response": response,
        },
        "resourceMeasurements": {
            "analysisSeconds": time.perf_counter() - started,
            "maximumResidentSetKiB":
                resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        },
        "conclusion": {
            "captureControlsExact": controls["exact"],
            "sameTileOrthogonalInvariant": all(
                record["changedValues"] == 0
                for record in orthogonal.values()
            ),
            "verticalHorizontalBitExact":
                isotropy["changedValues"] == 0,
            "productionShaderAuthorized": False,
            "requiredGate":
                "zero unequal channels on protected Apple captures",
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Analyze the same-tile Liquid Glass stripe sweep."
    )
    parser.add_argument("stripe_sweep", type=Path)
    parser.add_argument("-o", "--output", type=Path)
    arguments = parser.parse_args()
    result = analyze(arguments.stripe_sweep)
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
