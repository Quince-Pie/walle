#!/usr/bin/env python3
"""Validate and measure radial-state-interior Liquid Glass stripes."""

import argparse
import hashlib
import json
import math
import platform
import resource
import time
import zipfile
from dataclasses import dataclass
from importlib.metadata import version as package_version
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from liquid_glass_kernel_sweep import _half_unorm_envelopes
from liquid_glass_sampler_probe import half_linear_ties_up
from liquid_glass_stripe_sweep import (
    baseline_histogram,
    bytes_sha256,
    difference_metrics,
    file_sha256,
    signed_channel_diagnostics,
)


type FloatArray = NDArray[np.float64]
type IntArray = NDArray[np.int64]
type UInt8Array = NDArray[np.uint8]
type JsonObject = dict[str, Any]

EXPECTED_RIG = "native-stripe-sweep-1.2.0"
FLAT_RIG = "native-flat-stripe-sweep-1.0.0"
EXPECTED_SCHEMA = 1
EXPECTED_SWEEP_KIND = "geometry-state-interior-phase-stripes"
FLAT_SWEEP_KIND = "flat-blur-profile-phase-stripes"
EXPECTED_AMPLITUDES = tuple(range(128))
EXPECTED_ORIENTATIONS = ("vertical", "horizontal")
EXPECTED_INTERVENTIONS = (
    "identity-blur-0",
    "identity-blur-1",
    "identity-blur-2",
)
FLAT_INTERVENTIONS = (
    "flat-blur-0",
    "flat-blur-1",
    "flat-blur-2",
    "flat-blur-4",
)
EXPECTED_POSITIONS = (24, 50, 76, 102, 400, 426, 452, 478)
EXPECTED_INTERVALS = ((24, 50), (76, 102), (400, 426), (452, 478))
EXPECTED_BOUNDARIES = (
    0.0,
    0.08,
    0.1577545,
    0.2289485,
    0.3037185,
    0.3753005,
)
EXPECTED_GROUPS = (
    (0, (400, 426, 452, 478), 470),
    (1, (400, 426, 452, 478), 290),
    (2, (400, 426, 452, 478), 135),
    (3, (24, 50, 76, 102), 228),
    (4, (24, 50, 76, 102), 25),
)
EXPECTED_PATCH_RADIUS = 12
EXPECTED_PATCH_SIDE = 25
SOURCE_CODE = 128
CHANNELS = 3
CHANNEL_SIGNS = np.asarray((1, -1, 1), dtype=np.int16)
IMAGE_CENTER = 512
GLASS_RADIUS = 2000
MIP_NUMERATOR = 37
MIP_DENOMINATOR = 64


def expected_sites() -> list[JsonObject]:
    sites: list[JsonObject] = []
    for state, positions, cross_axis_center in EXPECTED_GROUPS:
        for position in positions:
            edge_index = EXPECTED_POSITIONS.index(position)
            minimum = math.inf
            maximum = 0.0
            for delta_y in range(
                -EXPECTED_PATCH_RADIUS,
                EXPECTED_PATCH_RADIUS + 1,
            ):
                for delta_x in range(
                    -EXPECTED_PATCH_RADIUS,
                    EXPECTED_PATCH_RADIUS + 1,
                ):
                    radius = math.hypot(
                        position + delta_x - IMAGE_CENTER,
                        cross_axis_center + delta_y - IMAGE_CENTER,
                    ) / GLASS_RADIUS
                    minimum = min(minimum, radius)
                    maximum = max(maximum, radius)
            sites.append({
                "index": len(sites),
                "geometryState": state,
                "edgePosition": position,
                "crossAxisCenter": cross_axis_center,
                "reducedGridPhase": (position // 2) & 3,
                "transitionSign": 1 if edge_index % 2 == 0 else -1,
                "normalizedRadiusMinimum": minimum,
                "normalizedRadiusMaximum": maximum,
                "geometryStateLowerBoundary": EXPECTED_BOUNDARIES[state],
                "geometryStateUpperBoundary":
                    EXPECTED_BOUNDARIES[state + 1],
            })
    return sites


def _values_for_radius(
    radius: int,
    *,
    flat_profile: bool = False,
) -> JsonObject:
    values: JsonObject = {
        "inputBlurRadius": radius,
        "inputFaceColorMatrixBlack": 0,
        "inputFaceColorMatrixSaturation": 1,
        "inputFaceColorMatrixWhite": 1,
        "inputSDRHoldingToneEnabled": False,
    }
    if flat_profile:
        values.update({
            "inputBlurOpacity0": 1,
            "inputBlurOpacity1": 1,
            "inputBlurOpacity2": 1,
            "inputBlurOpacity3": 1,
            "inputBlurOpacity4": 1,
            "inputInnerRefractionAmount": 0,
            "inputOuterRefractionAmount": 0,
            "inputRefractionOpacity": 0,
        })
    return values


def _rig_configuration(
    manifest: JsonObject,
) -> tuple[bool, tuple[str, ...], tuple[int, ...]]:
    rig = manifest.get("rigVersion")
    kind = manifest.get("sweepKind")
    if rig == EXPECTED_RIG and kind == EXPECTED_SWEEP_KIND:
        return False, EXPECTED_INTERVENTIONS, (0, 1, 2)
    if rig == FLAT_RIG and kind == FLAT_SWEEP_KIND:
        return True, FLAT_INTERVENTIONS, (0, 1, 2, 4)
    raise ValueError(
        f"unexpected state stripe rig/kind: {rig!r}/{kind!r}"
    )


def _checked_catalog(manifest: JsonObject) -> list[JsonObject]:
    flat_profile, intervention_names, radii = (
        _rig_configuration(manifest)
    )
    if manifest.get("schemaVersion") != EXPECTED_SCHEMA:
        raise ValueError("state stripe manifest schema differs")
    if manifest.get("fixedFaceState") != {
        "inputFaceColorMatrixBlack": 0,
        "inputFaceColorMatrixSaturation": 1,
        "inputFaceColorMatrixWhite": 1,
        "inputSDRHoldingToneEnabled": False,
    }:
        raise ValueError("state stripe fixed face differs")
    interventions = manifest.get("interventions")
    if not isinstance(interventions, list) or tuple(
        record.get("name") for record in interventions
    ) != intervention_names:
        raise ValueError("state stripe intervention catalog differs")
    for radius, record in zip(radii, interventions, strict=True):
        if record.get("values") != _values_for_radius(
            radius,
            flat_profile=flat_profile,
        ):
            raise ValueError("state stripe intervention values differ")

    source = manifest.get("sourceDesign")
    if not isinstance(source, dict):
        raise ValueError("state stripe source design is missing")
    expected_fields: JsonObject = {
        "baseCode": SOURCE_CODE,
        "amplitudesCodes": list(EXPECTED_AMPLITUDES),
        "channelSigns": {"red": 1, "green": -1, "blue": 1},
        "orientationOrder": list(EXPECTED_ORIENTATIONS),
        "alternatingInsideIntervals": [
            list(interval) for interval in EXPECTED_INTERVALS
        ],
        "edgeMinimumSpacingPixels": 26,
        "patchRadiusPixels": EXPECTED_PATCH_RADIUS,
        "patchSidePixels": EXPECTED_PATCH_SIDE,
        "priorMeasuredSupportRadiusUpperBoundPixels": 12,
        "minimumGapBeyondAdjacentMeasuredSupportsPixels": 2,
        "geometryStateCoordinate":
            "hypot(pixel-center)/(glassDiameter/2)",
        "geometryStateBoundaries": list(EXPECTED_BOUNDARIES),
    }
    for key, expected in expected_fields.items():
        if source.get(key) != expected:
            raise ValueError(f"state stripe source field differs: {key}")
    expected_edges = [
        {
            "index": index,
            "position": position,
            "reducedGridPhase": (position // 2) & 3,
            "transitionSign": 1 if index % 2 == 0 else -1,
        }
        for index, position in enumerate(EXPECTED_POSITIONS)
    ]
    if source.get("edges") != expected_edges:
        raise ValueError("state stripe edge catalog differs")
    sites = source.get("sampleSites")
    expected = expected_sites()
    if not isinstance(sites, list) or len(sites) != len(expected):
        raise ValueError("state stripe sample catalog differs")
    for actual, reference in zip(sites, expected, strict=True):
        for key, value in reference.items():
            if key.startswith("normalizedRadius"):
                if not math.isclose(
                    float(actual.get(key)),
                    float(value),
                    rel_tol=0,
                    abs_tol=1e-12,
                ):
                    raise ValueError(
                        f"state stripe sample radius differs: {key}"
                    )
            elif actual.get(key) != value:
                raise ValueError(
                    f"state stripe sample field differs: {key}"
                )
        state = int(actual["geometryState"])
        if not (
            float(actual["normalizedRadiusMinimum"])
            > EXPECTED_BOUNDARIES[state]
            and float(actual["normalizedRadiusMaximum"])
            < EXPECTED_BOUNDARIES[state + 1]
        ):
            raise ValueError("state stripe sample crosses a state boundary")

    captures = manifest.get("captures")
    if not isinstance(captures, list) or len(captures) != 128:
        raise ValueError("state stripe capture catalog differs")
    for amplitude, record in enumerate(captures):
        if record.get("amplitudeCodes") != amplitude:
            raise ValueError(
                f"state stripe amplitude differs at {amplitude}"
            )
        orientations = record.get("orientations")
        if not isinstance(orientations, list) or tuple(
            candidate.get("orientation") for candidate in orientations
        ) != EXPECTED_ORIENTATIONS:
            raise ValueError(
                f"state stripe orientations differ at {amplitude}"
            )
        for orientation in orientations:
            if (
                orientation.get("captureBackend")
                != "CGWindowListCreateImage"
                or int(orientation.get("controlStabilitySamples", 0)) < 2
                or int(orientation.get("materializedStabilitySamples", 0)) < 2
            ):
                raise ValueError(
                    f"state stripe stability differs at {amplitude}"
                )
            states = orientation.get("interventions")
            if not isinstance(states, list) or tuple(
                candidate.get("name") for candidate in states
            ) != intervention_names:
                raise ValueError(
                    f"state stripe captures differ at {amplitude}"
                )
            for radius, candidate in zip(
                radii,
                states,
                strict=True,
            ):
                expected_values = _values_for_radius(
                    radius,
                    flat_profile=flat_profile,
                )
                if (
                    candidate.get("captureBackend")
                    != "CGWindowListCreateImage"
                    or int(candidate.get("stabilitySamples", 0)) < 2
                    or candidate.get("values") != expected_values
                    or candidate.get("inputBlurRadiusReadback") != radius
                    or candidate.get(
                        "inputBlurRadiusReadbackFloat32Bits"
                    ) != f"{np.float32(radius).view(np.uint32):08x}"
                ):
                    raise ValueError(
                        "state stripe intervention metadata differs at "
                        f"{amplitude}/{orientation.get('orientation')}/"
                        f"{radius}"
                    )
                if flat_profile:
                    float_bits = {
                        key: f"{np.float32(value).view(np.uint32):08x}"
                        for key, value in expected_values.items()
                        if not isinstance(value, bool)
                    }
                    if (
                        candidate.get("inputReadbacks")
                        != expected_values
                        or candidate.get(
                            "inputReadbackFloat32Bits"
                        ) != float_bits
                    ):
                        raise ValueError(
                            "flat stripe full readback differs at "
                            f"{amplitude}/"
                            f"{orientation.get('orientation')}/"
                            f"{radius}"
                        )
    return sites


@dataclass(frozen=True, slots=True)
class StateStripeSweep:
    manifest: JsonObject
    control: UInt8Array
    interventions: dict[str, UInt8Array]

    @property
    def sites(self) -> list[JsonObject]:
        return self.manifest["sourceDesign"]["sampleSites"]

    @classmethod
    def open(cls, path: Path) -> "StateStripeSweep":
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
                    raise ValueError(
                        "state stripe archive has duplicate members"
                    )
                try:
                    manifest = json.loads(archive.read("manifest.json"))
                except KeyError as error:
                    raise ValueError(
                        "state stripe manifest is missing"
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
                        f"state stripe member is missing: {name}"
                    ) from error

        sites = _checked_catalog(manifest)
        _, intervention_names, _ = _rig_configuration(manifest)
        evidence = manifest.get("nativeCaptureEvidence")
        if not isinstance(evidence, dict):
            raise ValueError("state stripe evidence is missing")
        expected_order = (
            "amplitude ascending, orientation vertical then horizontal, "
            "sample-site order, patch y-major then x-major"
        )
        if (
            evidence.get("schemaVersion") != EXPECTED_SCHEMA
            or evidence.get("recordOrder") != expected_order
            or evidence.get("recordFormat") != "RGB8"
            or evidence.get("recordStrideBytes") != CHANNELS
        ):
            raise ValueError("state stripe record format differs")
        record_count = (
            len(EXPECTED_AMPLITUDES)
            * len(EXPECTED_ORIENTATIONS)
            * len(sites)
            * EXPECTED_PATCH_SIDE**2
        )
        if evidence.get("recordCount") != record_count:
            raise ValueError("state stripe record count differs")
        shape = (
            len(EXPECTED_AMPLITUDES),
            len(EXPECTED_ORIENTATIONS),
            len(sites),
            EXPECTED_PATCH_SIDE,
            EXPECTED_PATCH_SIDE,
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
                    f"state stripe stream length differs: {name}"
                )
            if bytes_sha256(data) != record.get("fileSha256"):
                raise ValueError(
                    f"state stripe stream hash differs: {name}"
                )
            return np.frombuffer(data, dtype=np.uint8).reshape(shape)

        control = decode({
            "file": evidence["controlFile"],
            "fileBytes": evidence["controlFileBytes"],
            "fileSha256": evidence["controlFileSha256"],
        })
        records = evidence.get("interventions")
        if not isinstance(records, list) or tuple(
            record.get("name") for record in records
        ) != intervention_names:
            raise ValueError("state stripe evidence interventions differ")
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
                raise ValueError("state stripe ICC evidence differs")
        for record in manifest["captures"]:
            for orientation in record["orientations"]:
                for kind in ("source", "control"):
                    name = orientation.get(f"{kind}File")
                    if name is not None:
                        data = read(str(name))
                        if bytes_sha256(data) != orientation.get(
                            f"{kind}FileSha256"
                        ):
                            raise ValueError(
                                f"state stripe audit hash differs: {name}"
                            )
                for state in orientation["interventions"]:
                    if name := state.get("file"):
                        data = read(str(name))
                        if bytes_sha256(data) != state.get("fileSha256"):
                            raise ValueError(
                                f"state stripe audit hash differs: {name}"
                            )
        return cls(
            manifest=manifest,
            control=control,
            interventions=interventions,
        )


def expected_control(sweep: StateStripeSweep) -> UInt8Array:
    result = np.full(sweep.control.shape, SOURCE_CODE, dtype=np.uint8)
    offsets = (
        np.arange(EXPECTED_PATCH_SIDE, dtype=np.int16)
        - EXPECTED_PATCH_RADIUS
    )
    for amplitude in EXPECTED_AMPLITUDES:
        codes = (
            SOURCE_CODE
            + amplitude * CHANNEL_SIGNS.astype(np.int16)
        ).astype(np.uint8)
        for site_index, site in enumerate(sweep.sites):
            inside = (
                offsets >= 0
                if int(site["transitionSign"]) > 0
                else offsets < 0
            )
            result[amplitude, 0, site_index, :, inside, :] = codes
            result[amplitude, 1, site_index, inside, :, :] = codes
    return result


def orthogonal_invariance(stream: UInt8Array) -> JsonObject:
    center = EXPECTED_PATCH_RADIUS
    vertical = np.broadcast_to(
        stream[:, 0, :, center:center + 1, :, :],
        stream[:, 0].shape,
    )
    horizontal = np.broadcast_to(
        stream[:, 1, :, :, center:center + 1, :],
        stream[:, 1].shape,
    )
    return {
        "verticalRows": difference_metrics(vertical, stream[:, 0]),
        "horizontalColumns": difference_metrics(
            horizontal,
            stream[:, 1],
        ),
    }


def orientation_isotropy(stream: UInt8Array) -> JsonObject:
    return difference_metrics(
        stream[:, 0],
        np.swapaxes(stream[:, 1], 2, 3),
    )


def channel_step_slopes(stream: UInt8Array) -> FloatArray:
    baseline = stream[0].astype(np.int16)
    denominator = (
        EXPECTED_PATCH_SIDE
        * sum(amplitude * amplitude for amplitude in EXPECTED_AMPLITUDES)
    )
    result = np.zeros(
        (
            len(EXPECTED_ORIENTATIONS),
            len(expected_sites()),
            EXPECTED_PATCH_SIDE,
            CHANNELS,
        ),
        dtype=np.float64,
    )
    for amplitude in EXPECTED_AMPLITUDES[1:]:
        delta = stream[amplitude].astype(np.int16) - baseline
        signed = delta * CHANNEL_SIGNS
        result[0] += amplitude * signed[0].sum(axis=1)
        result[1] += amplitude * signed[1].sum(axis=2)
    return result / denominator


def response_measurements(
    sweep: StateStripeSweep,
    stream: UInt8Array,
) -> JsonObject:
    channel_slopes = channel_step_slopes(stream)
    slopes = channel_slopes.mean(axis=-1)
    normalized = np.empty_like(slopes)
    gains = np.empty(slopes.shape[:2], dtype=np.float64)
    records: list[JsonObject] = []
    offsets = np.arange(
        -EXPECTED_PATCH_RADIUS + 0.5,
        EXPECTED_PATCH_RADIUS,
        dtype=np.float64,
    )
    for orientation, orientation_name in enumerate(EXPECTED_ORIENTATIONS):
        for site_index, site in enumerate(sweep.sites):
            sign = int(site["transitionSign"])
            if sign > 0:
                outside = slopes[orientation, site_index, 0]
                inside = slopes[orientation, site_index, -1]
            else:
                inside = slopes[orientation, site_index, 0]
                outside = slopes[orientation, site_index, -1]
            gain = inside - outside
            if gain <= 0:
                raise ValueError("state stripe response gain is nonpositive")
            gains[orientation, site_index] = gain
            step = (
                slopes[orientation, site_index] - outside
            ) / gain
            normalized[orientation, site_index] = step
            kernel = np.diff(step) * sign
            total = float(kernel.sum())
            records.append({
                "orientation": orientation_name,
                "sampleSiteIndex": site_index,
                "geometryState": int(site["geometryState"]),
                "phase": int(site["reducedGridPhase"]),
                "transitionSign": sign,
                "outsideSlopeCodesPerAmplitude": float(outside),
                "insideSlopeCodesPerAmplitude": float(inside),
                "gainCodesPerAmplitude": float(gain),
                "normalizedStep": step.tolist(),
                "kernel": kernel.tolist(),
                "kernelSum": total,
                "kernelPositiveMass":
                    float(kernel[kernel > 0].sum()),
                "kernelNegativeMass":
                    float(-kernel[kernel < 0].sum()),
                "kernelCenterOfMassPixels": (
                    float(np.sum(kernel * offsets) / total)
                    if total else None
                ),
            })

    red_green = channel_slopes[..., 0] - channel_slopes[..., 1]
    red_blue = channel_slopes[..., 0] - channel_slopes[..., 2]
    state_distances = []
    for orientation, orientation_name in enumerate(EXPECTED_ORIENTATIONS):
        for phase in range(4):
            indices = [
                index
                for index, site in enumerate(sweep.sites)
                if int(site["reducedGridPhase"]) == phase
            ]
            for left in range(5):
                for right in range(left + 1, 5):
                    delta = (
                        normalized[orientation, indices[left]]
                        - normalized[orientation, indices[right]]
                    )
                    state_distances.append({
                        "orientation": orientation_name,
                        "phase": phase,
                        "leftState": left,
                        "rightState": right,
                        "maximumAbsoluteDifference":
                            float(np.abs(delta).max(initial=0)),
                        "rootMeanSquareDifference":
                            float(np.sqrt(np.mean(delta**2))),
                    })
    return {
        "slopeFit": (
            "least squares through amplitude zero, averaging only the "
            "orthogonal axis; channel fits remain separate until reporting"
        ),
        "gainRangeCodesPerAmplitude": [
            float(gains.min()),
            float(gains.max()),
        ],
        "redBlueSlopeMaximumAbsoluteDifference":
            float(np.abs(red_blue).max(initial=0)),
        "redGreenSignedSlopeMaximumAbsoluteDifference":
            float(np.abs(red_green).max(initial=0)),
        "redGreenSignedSlopeRootMeanSquareDifference":
            float(np.sqrt(np.mean(red_green**2))),
        "phaseRecords": records,
        "crossStateDistances": state_distances,
    }


def mip_endpoint_envelope(
    level_zero: UInt8Array,
    production: UInt8Array,
    level_one: UInt8Array,
    sites: list[JsonObject],
) -> JsonObject:
    if not (
        level_zero.shape == production.shape == level_one.shape
    ):
        raise ValueError("state stripe mip stream shapes differ")
    minimum, maximum = _half_unorm_envelopes()
    low = half_linear_ties_up(
        minimum[level_zero],
        minimum[level_one],
        MIP_NUMERATOR,
        denominator=MIP_DENOMINATOR,
    )
    high = half_linear_ties_up(
        maximum[level_zero],
        maximum[level_one],
        MIP_NUMERATOR,
        denominator=MIP_DENOMINATOR,
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
    lower = np.minimum(low_code, high_code)
    upper = np.maximum(low_code, high_code)
    actual = production.astype(np.int16)
    outside = (actual < lower) | (actual > upper)
    distance = np.maximum(lower - actual, actual - upper)

    per_state = []
    for state in range(5):
        indices = [
            index
            for index, site in enumerate(sites)
            if int(site["geometryState"]) == state
        ]
        current = outside[:, :, indices]
        current_distance = distance[:, :, indices]
        per_state.append({
            "geometryState": state,
            "values": int(current.size),
            "incompatibleValues": int(np.count_nonzero(current)),
            "compatibleFraction": float(np.mean(~current)),
            "maximumOutsideDistanceCodes":
                int(np.maximum(current_distance, 0).max(initial=0)),
        })

    continuous = (
        (MIP_DENOMINATOR - MIP_NUMERATOR)
        * level_zero.astype(np.float64)
        + MIP_NUMERATOR * level_one.astype(np.float64)
    ) / MIP_DENOMINATOR
    diagnostics = {}
    for name, prediction in (
        ("floor", np.floor(continuous)),
        ("nearestEven", np.rint(continuous)),
        ("ceil", np.ceil(continuous)),
    ):
        diagnostics[name] = difference_metrics(
            np.clip(prediction, 0, 255).astype(np.uint8),
            production,
        )
    return {
        "quantizedLodFraction": {
            "numerator": MIP_NUMERATOR,
            "denominator": MIP_DENOMINATOR,
            "exact": "37/64",
        },
        "binary16RoundedEndpointEnvelope": {
            "values": int(outside.size),
            "incompatibleValues": int(np.count_nonzero(outside)),
            "compatibleFraction": float(np.mean(~outside)),
            "maximumOutsideDistanceCodes":
                int(np.maximum(distance, 0).max(initial=0)),
            "allCompatible": not bool(np.any(outside)),
            "perGeometryState": per_state,
            "limitation": (
                "endpoint captures use distinct filter states; compatibility "
                "is necessary but does not prove one shared upstream resource"
            ),
        },
        "roundedCodeDomainDiagnostics": diagnostics,
    }


def analyze(path: Path) -> JsonObject:
    started = time.perf_counter()
    sweep = StateStripeSweep.open(path)
    flat_profile, intervention_names, radii = (
        _rig_configuration(sweep.manifest)
    )
    expected = expected_control(sweep)
    controls = difference_metrics(expected, sweep.control)
    controls["exact"] = controls["changedValues"] == 0
    stream_reports = {}
    for name, stream in sweep.interventions.items():
        stream_reports[name] = {
            "baselineHistogram": baseline_histogram(stream),
            "signedChannelDiagnostics":
                signed_channel_diagnostics(stream),
            "orthogonalInvariance": orthogonal_invariance(stream),
            "orientationIsotropy": orientation_isotropy(stream),
            "response": response_measurements(sweep, stream),
        }
    if flat_profile:
        mip: JsonObject = mip_endpoint_envelope(
            sweep.interventions["flat-blur-0"],
            sweep.interventions["flat-blur-1"],
            sweep.interventions["flat-blur-2"],
            sweep.sites,
        )
        mip["validity"] = (
            "all blur opacities are one and refraction is zero, so "
            "requested radii 0, 1, and 2 reach stationary LOD 0, "
            "log2(1.5), and 1 in the disassembled Apple shader"
        )
    else:
        mip = {
            "evaluated": False,
            "reason": (
                "the live default blur-opacity profile multiplies each "
                "requested radius by an SDF-conditioned scale; requested "
                "radii 0 and 2 are therefore not fixed mip endpoints"
            ),
        }
    return {
        "liquidGlassStateStripeAnalysisSchemaVersion": 1,
        "analysisImplementation": {
            "file": "analysis/liquid_glass_state_stripe_sweep.py",
            "sha256": file_sha256(Path(__file__).resolve()),
            "python": platform.python_version(),
            "numpy": package_version("numpy"),
        },
        "source": {
            "path": str(path),
            "sha256": file_sha256(path) if path.is_file() else None,
            "rigVersion": sweep.manifest["rigVersion"],
            "flatBlurProfile": flat_profile,
            "interventionOrder": list(intervention_names),
            "requestedBlurRadii": list(radii),
            "ciCommit": sweep.manifest["ciCommit"],
            "osVersion": sweep.manifest["osVersion"],
            "architecture": sweep.manifest["architecture"],
            "sampleSites": sweep.sites,
            "nativeCaptureEvidence":
                sweep.manifest["nativeCaptureEvidence"],
        },
        "controls": {
            "sourceFidelity": controls,
        },
        "interventions": stream_reports,
        "mipEndpointTest": mip,
        "resourceMeasurements": {
            "analysisSeconds": time.perf_counter() - started,
            "maximumResidentSetKiB":
                resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        },
        "conclusion": {
            "captureControlsExact": controls["exact"],
            "allSamplePatchesInsideDeclaredGeometryState": True,
            "stationaryMipPathIsolated": flat_profile,
            "productionShaderAuthorized": False,
            "requiredGate":
                "zero unequal channels on protected Apple captures",
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Analyze radial-state-interior Liquid Glass stripes."
    )
    parser.add_argument("state_stripe_sweep", type=Path)
    parser.add_argument("-o", "--output", type=Path)
    arguments = parser.parse_args()
    result = analyze(arguments.state_stripe_sweep)
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
