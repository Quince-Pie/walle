#!/usr/bin/env python3
"""Validate Apple's fixed-resource Liquid Glass LOD calibration sweep."""

import argparse
import hashlib
import json
import platform
import resource
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from liquid_glass_lod_cross_match import (
    CandidateBounds,
    exact_catalog_candidates,
    exact_signature_words,
)
from liquid_glass_lod_sweep import (
    AMPLITUDES,
    CHANNELS,
    IDENTITY_VALUES,
    LodSweep,
    PATCH_SIDE,
    SITE_COUNT,
    SOURCE_CODE,
    _expected_blur_radius,
    float32_bits,
)


type JsonObject = dict[str, Any]
type UInt8Array = NDArray[np.uint8]

EXPECTED_RIG = "native-fixed-resource-lod-sweep-1.0.0"
EXPECTED_KIND = "constant-opacity-fixed-resource-lod-curve"
EXPECTED_SCHEMA = 1
RADIUS_ONE_GRID_COUNT = 38
RADIUS_ONE_COUNT = 39
RADIUS_FOUR_START = RADIUS_ONE_COUNT
RADIUS_FOUR_COUNT = 129
STATE_COUNT = RADIUS_ONE_COUNT + RADIUS_FOUR_COUNT
PRODUCTION_RADIUS_ONE_STATE = 38
RADIUS_FOUR_SCALE_ONE_STATE = RADIUS_FOUR_START + 128
SIGNATURE_BYTES = len(AMPLITUDES) * CHANNELS


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _constant_values(
    *,
    resource_radius: float,
    scale: float,
) -> JsonObject:
    result = dict(IDENTITY_VALUES)
    result.update({
        f"inputBlurOpacity{index}": scale
        for index in range(5)
    })
    result.update({
        "inputInnerRefractionAmount": -60,
        "inputOuterRefractionAmount": 160,
        "inputRefractionOpacity": 0,
        "inputBlurRadius": resource_radius,
    })
    return result


def _expected_state_catalog() -> list[JsonObject]:
    states: list[JsonObject] = []

    def append(
        *,
        name: str,
        numerator: int,
        resource_radius: np.float32,
        effective_radius: np.float32,
        production: bool,
    ) -> None:
        scale = np.float32(effective_radius / resource_radius)
        states.append({
            "index": len(states),
            "name": name,
            "resourceBlurRadius": float(resource_radius),
            "resourceBlurRadiusFloat32Bits":
                float32_bits(float(resource_radius)),
            "constantBlurOpacityScale": float(scale),
            "constantBlurOpacityScaleFloat32Bits":
                float32_bits(float(scale)),
            "targetEffectiveBlurRadius": float(effective_radius),
            "targetEffectiveBlurRadiusFloat32Bits":
                float32_bits(float(effective_radius)),
            "targetLodNumerator": numerator,
            "targetLodDenominator": 64,
            "productionEffectiveRadius": production,
        })

    for numerator in range(RADIUS_ONE_GRID_COUNT):
        append(
            name=f"fixed-r1-lod-bin-{numerator:03d}",
            numerator=numerator,
            resource_radius=np.float32(1),
            effective_radius=_expected_blur_radius(numerator),
            production=False,
        )
    append(
        name="fixed-r1-production-blur-1",
        numerator=37,
        resource_radius=np.float32(1),
        effective_radius=np.float32(1),
        production=True,
    )
    for numerator in range(RADIUS_FOUR_COUNT):
        append(
            name=f"fixed-r4-lod-bin-{numerator:03d}",
            numerator=numerator,
            resource_radius=np.float32(4),
            effective_radius=_expected_blur_radius(numerator),
            production=False,
        )
    return states


def _validate_manifest(manifest: JsonObject) -> list[JsonObject]:
    if (
        manifest.get("schemaVersion") != EXPECTED_SCHEMA
        or manifest.get("rigVersion") != EXPECTED_RIG
        or manifest.get("sweepKind") != EXPECTED_KIND
    ):
        raise ValueError("fixed-resource LOD rig differs")
    if manifest.get("flatBlurProfileInputs") is not None:
        raise ValueError("fixed-resource flat-profile marker differs")
    if manifest.get("fixedResourceInputs") != {
        "inputBlurRadius": "held at the resource-group radius",
        "inputBlurOpacity0Through4":
            "all held at constantBlurOpacityScale",
        "inputInnerRefractionAmount": -60,
        "inputOuterRefractionAmount": 160,
        "inputRefractionOpacity": 0,
    }:
        raise ValueError("fixed-resource input design differs")
    design = manifest.get("lodDesign")
    if (
        not isinstance(design, dict)
        or design.get("quantizedFractionDenominator") != 64
        or design.get("resourceGroups") != [
            {
                "resourceBlurRadius": 1,
                "stateIndexRangeInclusive": [0, 38],
                "targetLodNumeratorRangeInclusive": [0, 37],
                "productionEffectiveRadiusStateIndex": 38,
            },
            {
                "resourceBlurRadius": 4,
                "stateIndexRangeInclusive": [39, 167],
                "targetLodNumeratorRangeInclusive": [0, 128],
                "productionEffectiveRadiusStateIndex": None,
            },
        ]
    ):
        raise ValueError("fixed-resource LOD group design differs")
    expected_states = _expected_state_catalog()
    if design.get("states") != expected_states:
        raise ValueError("fixed-resource LOD state catalog differs")

    captures = manifest.get("captures")
    if (
        not isinstance(captures, list)
        or len(captures) != len(AMPLITUDES)
    ):
        raise ValueError("fixed-resource capture catalog differs")
    for amplitude, capture in zip(
        AMPLITUDES,
        captures,
        strict=True,
    ):
        if (
            capture.get("amplitudeCodes") != amplitude
            or int(capture.get("controlStabilitySamples", 0)) < 2
            or int(capture.get("materializedStabilitySamples", 0)) < 2
            or capture.get("captureBackend")
            != "CGWindowListCreateImage"
        ):
            raise ValueError(
                f"fixed-resource capture differs at {amplitude}"
            )
        records = capture.get("states")
        if (
            not isinstance(records, list)
            or len(records) != STATE_COUNT
        ):
            raise ValueError(
                f"fixed-resource states differ at {amplitude}"
            )
        for expected, record in zip(
            expected_states,
            records,
            strict=True,
        ):
            radius = float(expected["resourceBlurRadius"])
            scale = float(expected["constantBlurOpacityScale"])
            values = _constant_values(
                resource_radius=radius,
                scale=scale,
            )
            bits = {
                key: float32_bits(float(value))
                for key, value in values.items()
                if not isinstance(value, bool)
            }
            if (
                any(
                    record.get(key) != value
                    for key, value in expected.items()
                )
                or record.get("inputReadbacks") != values
                or record.get("inputReadbackFloat32Bits") != bits
                or record.get("readbackBlurRadius") != radius
                or record.get("readbackBlurRadiusFloat32Bits")
                != expected["resourceBlurRadiusFloat32Bits"]
                or int(record.get("stabilitySamples", 0)) < 2
                or record.get("captureBackend")
                != "CGWindowListCreateImage"
            ):
                raise ValueError(
                    "fixed-resource readback differs at "
                    f"{amplitude}/{expected['name']}"
                )
    return expected_states


@dataclass(frozen=True, slots=True)
class FixedResourceSweep:
    manifest: JsonObject
    control: UInt8Array
    identity: UInt8Array
    member_hashes: dict[str, str]

    @classmethod
    def open(cls, path: Path) -> "FixedResourceSweep":
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
                        "fixed-resource archive has duplicate members"
                    )
                try:
                    manifest = json.loads(
                        archive.read("manifest.json")
                    )
                except KeyError as error:
                    raise ValueError(
                        "fixed-resource manifest is missing"
                    ) from error
                members = {
                    name: archive.read(name)
                    for name in names
                    if name.startswith(
                        "native-fixed-resource-lod-"
                    )
                }

            def read(name: str) -> bytes:
                try:
                    return members[name]
                except KeyError as error:
                    raise ValueError(
                        "fixed-resource member is missing: "
                        f"{name}"
                    ) from error

        _validate_manifest(manifest)
        evidence = manifest.get("nativeCaptureEvidence")
        if not isinstance(evidence, dict):
            raise ValueError("fixed-resource evidence is missing")
        control_records = (
            len(AMPLITUDES) * SITE_COUNT * PATCH_SIDE**2
        )
        identity_records = control_records * STATE_COUNT
        if (
            evidence.get("schemaVersion") != EXPECTED_SCHEMA
            or evidence.get("recordFormat") != "RGB8"
            or evidence.get("recordStrideBytes") != CHANNELS
            or evidence.get("controlRecordCount") != control_records
            or evidence.get("recordCount") != identity_records
        ):
            raise ValueError("fixed-resource stream layout differs")
        member_hashes: dict[str, str] = {}

        def checked(
            *,
            file_key: str,
            bytes_key: str,
            hash_key: str,
            expected_bytes: int,
        ) -> bytes:
            name = str(evidence[file_key])
            value = read(name)
            digest = sha256_bytes(value)
            if (
                len(value) != expected_bytes
                or evidence.get(bytes_key) != expected_bytes
                or evidence.get(hash_key) != digest
            ):
                raise ValueError(
                    f"fixed-resource stream differs: {name}"
                )
            member_hashes[name] = digest
            return value

        control_bytes = checked(
            file_key="controlFile",
            bytes_key="controlFileBytes",
            hash_key="controlFileSha256",
            expected_bytes=control_records * CHANNELS,
        )
        identity_bytes = checked(
            file_key="file",
            bytes_key="fileBytes",
            hash_key="fileSha256",
            expected_bytes=identity_records * CHANNELS,
        )
        if icc_name := evidence.get("iccFile"):
            icc = read(str(icc_name))
            digest = sha256_bytes(icc)
            if (
                len(icc) != evidence.get("iccFileBytes")
                or digest != evidence.get("iccFileSha256")
            ):
                raise ValueError(
                    "fixed-resource ICC evidence differs"
                )
            member_hashes[str(icc_name)] = digest
        control = np.frombuffer(
            control_bytes,
            dtype=np.uint8,
        ).reshape(
            len(AMPLITUDES),
            SITE_COUNT,
            PATCH_SIDE,
            PATCH_SIDE,
            CHANNELS,
        )
        identity = np.frombuffer(
            identity_bytes,
            dtype=np.uint8,
        ).reshape(
            len(AMPLITUDES),
            STATE_COUNT,
            SITE_COUNT,
            PATCH_SIDE,
            PATCH_SIDE,
            CHANNELS,
        )
        return cls(
            manifest=manifest,
            control=control,
            identity=identity,
            member_hashes=member_hashes,
        )


def difference_metrics(
    left: UInt8Array,
    right: UInt8Array,
) -> JsonObject:
    if left.shape != right.shape:
        raise ValueError("fixed-resource comparison shapes differ")
    changed = left != right
    pixels = np.any(changed, axis=-1)
    distance = np.abs(
        left.astype(np.int16) - right.astype(np.int16)
    )
    return {
        "values": int(changed.size),
        "changedValues": int(np.count_nonzero(changed)),
        "exactValueFraction": float(np.mean(~changed)),
        "pixels": int(pixels.size),
        "changedPixels": int(np.count_nonzero(pixels)),
        "exactPixelFraction": float(np.mean(~pixels)),
        "maximumAbsoluteCodes": int(distance.max(initial=0)),
        "exact": not bool(np.any(changed)),
    }


def response_signatures(stream: UInt8Array) -> UInt8Array:
    if (
        stream.ndim != 6
        or stream.shape[0] != len(AMPLITUDES)
        or stream.shape[2:] != (
            SITE_COUNT,
            PATCH_SIDE,
            PATCH_SIDE,
            CHANNELS,
        )
    ):
        raise ValueError(
            "fixed-resource response signature shape differs"
        )
    states = stream.shape[1]
    return np.ascontiguousarray(
        np.transpose(stream, (1, 2, 3, 4, 0, 5))
    ).reshape(
        states,
        SITE_COUNT * PATCH_SIDE**2,
        SIGNATURE_BYTES,
    )


def _match_summary(
    bounds: CandidateBounds,
    names: list[str],
) -> JsonObject:
    if bounds.count.shape[0] != 1:
        raise ValueError("fixed-resource match has multiple probes")
    count = bounds.count[0]
    lower = bounds.lower[0]
    upper = bounds.upper[0]
    matched = count != 0
    unique = count == 1
    hist = np.bincount(
        lower[unique],
        minlength=len(names),
    )
    noncontiguous = matched & ~bounds.contiguous[0]
    return {
        "spatialSignatures": int(count.size),
        "unmatchedSignatures": int(np.count_nonzero(~matched)),
        "uniqueStateSignatures": int(np.count_nonzero(unique)),
        "ambiguousStateSignatures":
            int(np.count_nonzero(count > 1)),
        "noncontiguousCandidateSignatures":
            int(np.count_nonzero(noncontiguous)),
        "allSignaturesMatched": bool(np.all(matched)),
        "uniqueStateHistogram": {
            names[index]: int(value)
            for index, value in enumerate(hist)
            if value
        },
        "candidateCountMaximum": int(count.max()),
        "candidateLowerMinimum": (
            int(lower[matched].min())
            if np.any(matched)
            else None
        ),
        "candidateUpperMaximum": (
            int(upper[matched].max())
            if np.any(matched)
            else None
        ),
    }


def _catalog_match(
    probe: UInt8Array,
    catalog: UInt8Array,
    names: list[str],
) -> tuple[CandidateBounds, JsonObject]:
    probe_words = exact_signature_words(
        response_signatures(probe)
    )
    catalog_words = exact_signature_words(
        response_signatures(catalog)
    )
    bounds = exact_catalog_candidates(
        probe_words,
        catalog_words,
    )
    return bounds, _match_summary(bounds, names)


def _write_maps(
    path: Path,
    radius_one: CandidateBounds,
    radius_four: CandidateBounds,
) -> JsonObject:
    shape = (SITE_COUNT, PATCH_SIDE, PATCH_SIDE)
    with path.open("wb") as stream:
        np.savez_compressed(
            stream,
            radius_one_candidate_count=
                radius_one.count[0].reshape(shape),
            radius_one_candidate_lower=
                radius_one.lower[0].reshape(shape),
            radius_one_candidate_upper=
                radius_one.upper[0].reshape(shape),
            radius_four_candidate_count=
                radius_four.count[0].reshape(shape),
            radius_four_candidate_lower=
                radius_four.lower[0].reshape(shape),
            radius_four_candidate_upper=
                radius_four.upper[0].reshape(shape),
        )
    return {
        "path": str(path),
        "sha256": sha256_file(path),
        "spatialShape": list(shape),
        "dtype": "uint16",
    }


def analyze(
    fixed_path: Path,
    default_path: Path,
    flat_path: Path,
    *,
    map_output: Path | None = None,
) -> JsonObject:
    started = time.perf_counter()
    fixed = FixedResourceSweep.open(fixed_path)
    default = LodSweep.open(default_path)
    flat = LodSweep.open(flat_path)
    for candidate, label in (
        (default, "default"),
        (flat, "flat"),
    ):
        for key in ("osVersion", "architecture", "sourceDesign"):
            if (
                fixed.manifest.get(key)
                != candidate.manifest.get(key)
            ):
                raise ValueError(
                    f"fixed and {label} metadata differ: {key}"
                )

    controls_default = difference_metrics(
        fixed.control,
        default.control,
    )
    controls_flat = difference_metrics(
        fixed.control,
        flat.control,
    )
    amplitude_zero = fixed.identity[0]
    baseline = difference_metrics(
        amplitude_zero,
        np.full_like(amplitude_zero, SOURCE_CODE),
    )
    fixed_radius_one_bucket = difference_metrics(
        fixed.identity[:, 37],
        fixed.identity[:, PRODUCTION_RADIUS_ONE_STATE],
    )
    radius_one_complex_vs_flat = difference_metrics(
        fixed.identity[:, PRODUCTION_RADIUS_ONE_STATE],
        flat.identity[:, 129],
    )
    radius_four_complex_vs_flat = difference_metrics(
        fixed.identity[:, RADIUS_FOUR_SCALE_ONE_STATE],
        flat.identity[:, 128],
    )
    radius_one_grid_vs_flat = difference_metrics(
        fixed.identity[:, :RADIUS_ONE_GRID_COUNT],
        flat.identity[:, :RADIUS_ONE_GRID_COUNT],
    )
    radius_four_grid_vs_flat = difference_metrics(
        fixed.identity[:, RADIUS_FOUR_START:],
        flat.identity[:, :RADIUS_FOUR_COUNT],
    )
    radius_four_unequal_states = np.flatnonzero(
        np.any(
            fixed.identity[:, RADIUS_FOUR_START:]
            != flat.identity[:, :RADIUS_FOUR_COUNT],
            axis=(0, 2, 3, 4, 5),
        )
    )

    radius_one_catalog = fixed.identity[:, :RADIUS_ONE_COUNT]
    radius_one_names = [
        state["name"]
        for state in fixed.manifest["lodDesign"]["states"][
            :RADIUS_ONE_COUNT
        ]
    ]
    radius_one_bounds, radius_one_match = _catalog_match(
        default.identity[:, 129:130],
        radius_one_catalog,
        radius_one_names,
    )
    radius_four_catalog = fixed.identity[
        :,
        RADIUS_FOUR_START:,
    ]
    radius_four_names = [
        state["name"]
        for state in fixed.manifest["lodDesign"]["states"][
            RADIUS_FOUR_START:
        ]
    ]
    radius_four_bounds, radius_four_match = _catalog_match(
        default.identity[:, 128:129],
        radius_four_catalog,
        radius_four_names,
    )
    map_record = (
        _write_maps(
            map_output,
            radius_one_bounds,
            radius_four_bounds,
        )
        if map_output is not None
        else None
    )
    return {
        "liquidGlassFixedResourceLodAnalysisSchemaVersion": 1,
        "analysisImplementation": {
            "file":
                "analysis/liquid_glass_fixed_resource_lod.py",
            "sha256": sha256_file(Path(__file__).resolve()),
            "python": platform.python_version(),
            "numpy": np.__version__,
        },
        "sources": {
            "fixedResource": {
                "path": str(fixed_path),
                "sha256": (
                    sha256_file(fixed_path)
                    if fixed_path.is_file()
                    else None
                ),
                "ciCommit": fixed.manifest["ciCommit"],
                "memberSha256": fixed.member_hashes,
            },
            "default": {
                "path": str(default_path),
                "sha256": (
                    sha256_file(default_path)
                    if default_path.is_file()
                    else None
                ),
                "ciCommit": default.manifest["ciCommit"],
            },
            "flat": {
                "path": str(flat_path),
                "sha256": (
                    sha256_file(flat_path)
                    if flat_path.is_file()
                    else None
                ),
                "ciCommit": flat.manifest["ciCommit"],
            },
            "osVersion": fixed.manifest["osVersion"],
            "architecture": fixed.manifest["architecture"],
            "sourceDesignExact": True,
        },
        "controls": {
            "fixedVsDefaultSource": controls_default,
            "fixedVsFlatSource": controls_flat,
            "identityAmplitudeZero": baseline,
            "radiusOneGrid37VsExactScaleOne":
                fixed_radius_one_bucket,
            "radiusOneComplexVsFlatScaleOne":
                radius_one_complex_vs_flat,
            "radiusFourComplexVsFlatScaleOne":
                radius_four_complex_vs_flat,
            "radiusOneConstantScaleGridVsFlatRequestedRadiusGrid":
                radius_one_grid_vs_flat,
            "radiusFourConstantScaleGridVsFlatRequestedRadiusGrid": {
                **radius_four_grid_vs_flat,
                "unequalStateIndices":
                    radius_four_unequal_states.tolist(),
                "firstUnequalStateIndex": (
                    int(radius_four_unequal_states[0])
                    if radius_four_unequal_states.size
                    else None
                ),
            },
        },
        "defaultProfileExactCatalogMatching": {
            "equality": (
                "all five native RGB amplitude responses are packed "
                "losslessly into two uint64 words; no hash, fit, or "
                "tolerance is used"
            ),
            "requestedRadiusOne": radius_one_match,
            "requestedRadiusFour": radius_four_match,
            "candidateMaps": map_record,
        },
        "resourceMeasurements": {
            "analysisSeconds": time.perf_counter() - started,
            "maximumResidentSetKiB":
                resource.getrusage(
                    resource.RUSAGE_SELF
                ).ru_maxrss,
        },
        "conclusion": {
            "captureControlsExact": (
                controls_default["exact"]
                and controls_flat["exact"]
                and baseline["exact"]
            ),
            "fixedRadiusOneSamplerBucketExact":
                fixed_radius_one_bucket["exact"],
            "radiusOneConstantProductCrossRequestExact":
                radius_one_grid_vs_flat["exact"],
            "radiusFourConstantProductCrossRequestExact":
                radius_four_grid_vs_flat["exact"],
            "complexAndSimpleScaleOnePathsExact": (
                radius_one_complex_vs_flat["exact"]
                and radius_four_complex_vs_flat["exact"]
            ),
            "defaultRadiusOneExplainedByFixedResourceCatalog":
                radius_one_match["allSignaturesMatched"],
            "defaultRadiusFourExplainedByFixedResourceCatalog":
                radius_four_match["allSignaturesMatched"],
            "productionShaderAuthorized": False,
            "requiredGate":
                "zero unequal channels on protected Apple captures",
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Validate the fixed-resource Liquid Glass LOD sweep "
            "and bit-match the default SDF-conditioned profile."
        )
    )
    parser.add_argument("fixed_resource_lod_sweep", type=Path)
    parser.add_argument("default_lod_sweep", type=Path)
    parser.add_argument("flat_lod_sweep", type=Path)
    parser.add_argument("--map-output", type=Path)
    parser.add_argument("-o", "--output", type=Path)
    arguments = parser.parse_args()
    result = analyze(
        arguments.fixed_resource_lod_sweep,
        arguments.default_lod_sweep,
        arguments.flat_lod_sweep,
        map_output=arguments.map_output,
    )
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
