#!/usr/bin/env python3
"""Gate the recovered sample-28 final-highlight arithmetic at zero tolerance."""

import argparse
import hashlib
import json
import struct
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray


type JsonObject = dict[str, Any]
type UInt16Image = NDArray[np.uint16]
type UInt32Image = NDArray[np.uint32]

ROOT = Path(__file__).resolve().parent.parent
ANALYSIS = ROOT / "analysis"
if str(ANALYSIS) not in sys.path:
    sys.path.insert(0, str(ANALYSIS))

from apple_glass_reference_renderer import AppleGlassReferenceRenderer  # noqa: E402
import sweep_sample28_border_highlight_modes as sweep  # noqa: E402


WIDTH = 1_024
HEIGHT = 1_024
PIXELS = WIDTH * HEIGHT
PREFIX = (
    "transition-background-uniform-28-current-Iscd-final-highlight-alpha-"
)
EXPECTED_PRODUCTION_SHADER_SHA256 = (
    "6489828f12de599da9633d6183266a81b71ed846a1b03c03cb4eb9c23639352d"
)
EXPECTED_SOURCE_SHA256 = {
    "analysis/apple_glass_reference.frag.glsl": (
        "9c89bd0caba93f17957be62a4140150be3619754c107f4c778fb5b2148ca7127"
    ),
    "analysis/apple_glass_reference.vert.glsl": (
        "99d6942f6b39b52460c23b4e52c498f2d98a03cb6ffc32d87ef6e94c43e7a958"
    ),
    "analysis/apple_glass_reference_renderer.py": (
        "a5013b828e67bdab2db7b21efca8070d7df53f1895212b56fded38d1365d688d"
    ),
    "analysis/sweep_sample28_border_highlight_modes.py": (
        "26af05db2854c102240282e05bfda3499c89dd8c6e7a1847903a9505cd3c5329"
    ),
}
EXPECTED_CAPTURE_ARITHMETIC_PREREGISTRATION_SHA256 = (
    "a9c81c5b58e74cae27fff3cd36b9e942f07581c016b53227d851173d849a1466"
)
EXPECTED_INTRINSIC_SHA256 = (
    "fff71cc0d4428677ca5bc58b91212a7166b701e4efe504c3d71cab70846d0449"
)
EXPECTED_FIXTURE_MANIFEST_SHA256 = (
    "7b8e698a5b4dc09ecd4da804f6c7cea8707966ae933cd6f3f196a54ca505187a"
)
EXPECTED_CAPTURE_SHA256 = {
    "capture-session-preflight.json": (
        "a424b3c50899149ba79ef5e70687a01f896e8fbf058da5437a5a564c71a14034"
    ),
    "sample28-border-highlight-arithmetic.json": (
        "108c0126673f1494d6cf0f00dea14c2d6981e1c08afaad3d1597c10c6ccbd7a7"
    ),
    "transition-timeline.json": (
        "3593baa93000e7aee8faacc17819ec8eb64e63323cc55bd7666e92f8606b5f8f"
    ),
    f"{PREFIX}interpolant-rgba32uint-rgba32ui.raw": (
        "7b6b804fadfc99026de6c5106536076c5a14107bd223758981a62e3c3a27c170"
    ),
    f"{PREFIX}custom-sdf-rgba16f.raw": (
        "1c8a18399274d238bdc406518bdf20df2865bb8ce216c1f8d80c4432f4207e83"
    ),
    f"{PREFIX}custom-sdf-float-rgba32ui.raw": (
        "8648efa720abdad9132bb502ae7366a2072f647745059e0c9125416b9e2dbf24"
    ),
    f"{PREFIX}custom-sdf-geometry-rgba32ui.raw": (
        "e9a04156405e1bd0c7d53316573cc36899ed1f9a1bd14ecaeb5ea6e221d7b732"
    ),
    f"{PREFIX}custom-sdf-oval-rgba32ui.raw": (
        "abbdb96682afd9ad16605b68b18d07b7599a094d15b95bd9a0297de7a0429f8a"
    ),
    f"{PREFIX}custom-sdf-normal-rgba32ui.raw": (
        "abbbbbc14db54daf60c24f341f790fed7cc744d3364d88061e64f1c609cfd8c8"
    ),
    f"{PREFIX}custom-sdf-shape-geometry-rgba32ui.raw": (
        "5aa54f5ad241d2b3e868207b60fe5e08c1365280ca187b7a85043fb7ee1a1b3f"
    ),
    f"{PREFIX}custom-sdf-shape-normal-rgba32ui.raw": (
        "543b1a1603859da33ecee6ac50e679bd60b572bb25fd363bcca7f3710e9aaad8"
    ),
    f"{PREFIX}custom-sdf-radial-normal-rgba32ui.raw": (
        "f8ddb35bffd957c72feb7874315de21f5c25b3a76a2d35745fc85f4cf27007c0"
    ),
    f"{PREFIX}custom-sdf-composite-normal-rgba32ui.raw": (
        "d044edbd65692b1525d794c20c43a730357e6ee5003c6374e57fa57fa5810fbe"
    ),
    f"{PREFIX}rebuilt-rgba16float-rgba16f.raw": (
        "726e63463c7f1beb6e2d574d1d5f0838545d640f2d65409d81b957e53ba291af"
    ),
    f"{PREFIX}tomography-positive-normal-x-rgba16float-rgba16f.raw": (
        "de476365aecacbc2d7f33316576670c5d5ee6b85cfc2b6c0f65642ed1d46897f"
    ),
    f"{PREFIX}tomography-negative-normal-x-rgba16float-rgba16f.raw": (
        "37be4eec679a2ad23b654937432e45b798b5e92fbcb2c6df16e455395a4cff83"
    ),
    f"{PREFIX}tomography-positive-normal-y-rgba16float-rgba16f.raw": (
        "da99c76acb9bd2032c26f403f337c718c0213c65ed6b64ab5ee77b0a2fd9575f"
    ),
    f"{PREFIX}tomography-negative-normal-y-rgba16float-rgba16f.raw": (
        "a3bec7e5103c506d4f0bb97ac3f0e6c113d11557fce7398810ac97491af3b4b6"
    ),
    f"{PREFIX}tomography-normalized-normal-x-rgba16float-rgba16f.raw": (
        "9dc26f0fb126ba14a50e5944de2514319c264e6bd0fc1fc40715b1b1c568baf8"
    ),
    f"{PREFIX}tomography-normalized-normal-y-rgba16float-rgba16f.raw": (
        "aec03ecf776226ee04387e6bac993550056adfcf168b4825307a929f3af7b42c"
    ),
    f"{PREFIX}tomography-original-directional-rgba16float-rgba16f.raw": (
        "da79fbc892653ee3741a73164ad5c78d59079f09182ebb57a4c79fa66152c95f"
    ),
    f"{PREFIX}tomography-shifted-scaled-distance-rgba16float-rgba16f.raw": (
        "77b42ffe68f693f2ed923bd2129b69ccbac8aad06a190f4052e6eebb72410ab6"
    ),
    f"{PREFIX}tomography-leading-coverage-rgba16float-rgba16f.raw": (
        "5ca6f0bc291c9d9505581ee2abbb8b6bbf2d41b812e5eae41ca232c3a3d9c7da"
    ),
    f"{PREFIX}tomography-original-coverage-rgba16float-rgba16f.raw": (
        "3dd7f5c09fbd5e5c63bc2042456414c48fcc1a825c7be9dda55b15d7c4936472"
    ),
}


@dataclass(frozen=True, slots=True)
class Stage:
    name: str
    trace_mode: int
    file_suffix: str
    channel: int
    shift: int = 0
    mask: int = 0xFFFFFFFF


STAGES = (
    Stage("interpolant-x", 40, "interpolant-rgba32uint-rgba32ui.raw", 0),
    Stage("interpolant-y", 41, "interpolant-rgba32uint-rgba32ui.raw", 1),
    Stage("geometry-numerator-x", 44, "custom-sdf-geometry-rgba32ui.raw", 0),
    Stage("geometry-numerator-y", 45, "custom-sdf-geometry-rgba32ui.raw", 1),
    Stage("geometry-normalized-x", 46, "custom-sdf-geometry-rgba32ui.raw", 2),
    Stage("geometry-normalized-y", 47, "custom-sdf-geometry-rgba32ui.raw", 3),
    Stage("oval-delta-x", 48, "custom-sdf-oval-rgba32ui.raw", 0),
    Stage("oval-delta-y", 49, "custom-sdf-oval-rgba32ui.raw", 1),
    Stage("oval-squared", 50, "custom-sdf-oval-rgba32ui.raw", 2),
    Stage("oval-sqrt", 51, "custom-sdf-oval-rgba32ui.raw", 3),
    Stage("oval-distance", 52, "custom-sdf-float-rgba32ui.raw", 2),
    Stage("curved-distance-half", 53, "custom-sdf-float-rgba32ui.raw", 3, 0, 0xFFFF),
    Stage("distance-half", 54, "custom-sdf-float-rgba32ui.raw", 3, 16, 0xFFFF),
    Stage("point-squared", 55, "custom-sdf-normal-rgba32ui.raw", 0),
    Stage("point-fast-rsqrt", 56, "custom-sdf-normal-rgba32ui.raw", 1),
    Stage("point-normal-x", 57, "custom-sdf-normal-rgba32ui.raw", 2),
    Stage("point-normal-y", 58, "custom-sdf-normal-rgba32ui.raw", 3),
    Stage("shape-adjusted-delta-x", 59, "custom-sdf-shape-geometry-rgba32ui.raw", 0),
    Stage("shape-adjusted-delta-y", 60, "custom-sdf-shape-geometry-rgba32ui.raw", 1),
    Stage("shape-positive-squared", 61, "custom-sdf-shape-geometry-rgba32ui.raw", 2),
    Stage("shape-positive-fast-rsqrt", 62, "custom-sdf-shape-geometry-rgba32ui.raw", 3),
    Stage("shape-curved-normal-float-x", 63, "custom-sdf-shape-normal-rgba32ui.raw", 0),
    Stage("shape-curved-normal-float-y", 64, "custom-sdf-shape-normal-rgba32ui.raw", 1),
    Stage("shape-curved-normal-half-x", 65, "custom-sdf-shape-normal-rgba32ui.raw", 2, 0, 0xFFFF),
    Stage("shape-curved-normal-half-y", 66, "custom-sdf-shape-normal-rgba32ui.raw", 2, 16, 0xFFFF),
    Stage("shape-selected-normal-magnitude-half-x", 72, "custom-sdf-shape-normal-rgba32ui.raw", 3, 0, 0x7FFF),
    Stage("shape-selected-normal-magnitude-half-y", 73, "custom-sdf-shape-normal-rgba32ui.raw", 3, 16, 0x7FFF),
    Stage("radial-input-y", 67, "custom-sdf-radial-normal-rgba32ui.raw", 0),
    Stage("radial-squared", 68, "custom-sdf-radial-normal-rgba32ui.raw", 1),
    Stage("radial-fast-rsqrt", 69, "custom-sdf-radial-normal-rgba32ui.raw", 2),
    Stage("radial-normal-half-x", 70, "custom-sdf-radial-normal-rgba32ui.raw", 3, 0, 0xFFFF),
    Stage("radial-normal-half-y", 71, "custom-sdf-radial-normal-rgba32ui.raw", 3, 16, 0xFFFF),
    Stage("composite-shape-half-x", 72, "custom-sdf-composite-normal-rgba32ui.raw", 0, 0, 0xFFFF),
    Stage("composite-shape-half-y", 73, "custom-sdf-composite-normal-rgba32ui.raw", 0, 16, 0xFFFF),
    Stage("composite-radial-half-x", 70, "custom-sdf-composite-normal-rgba32ui.raw", 1, 0, 0xFFFF),
    Stage("composite-radial-half-y", 71, "custom-sdf-composite-normal-rgba32ui.raw", 1, 16, 0xFFFF),
    Stage("composite-mixed-half-x", 74, "custom-sdf-composite-normal-rgba32ui.raw", 2, 0, 0xFFFF),
    Stage("composite-mixed-half-y", 75, "custom-sdf-composite-normal-rgba32ui.raw", 2, 16, 0xFFFF),
    Stage("composite-final-half-x", 76, "custom-sdf-composite-normal-rgba32ui.raw", 3, 0, 0xFFFF),
    Stage("composite-final-half-y", 77, "custom-sdf-composite-normal-rgba32ui.raw", 3, 16, 0xFFFF),
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1 << 20):
            digest.update(block)
    return digest.hexdigest()


def require_hash(path: Path, expected: str, *, label: str) -> str:
    actual = sha256_file(path)
    if actual != expected:
        raise ValueError(f"{label} SHA-256 differs: {actual} != {expected}")
    return actual


def object_value(value: object, *, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} is not an object")
    return value


def exact_comparison(value: object, *, label: str) -> None:
    comparison = object_value(value, label=label)
    if (
        comparison.get("exactByteMatch") is not True
        or comparison.get("mismatchedByteCount") != 0
        or comparison.get("mismatchedPixelCount") != 0
        or comparison.get("maximumChannelDelta") != 0
    ):
        raise ValueError(f"{label} is not exact")


def load_record(path: Path) -> JsonObject:
    text = path.read_text(encoding="utf-8")
    # The selected local archive retained the packager's literal line suffix.
    value = json.loads(text.removesuffix("\\n"))
    if not isinstance(value, dict):
        raise ValueError("sample-28 arithmetic record is not an object")
    return value


def validate_capture(capture: Path) -> tuple[JsonObject, JsonObject]:
    hashes = {
        name: require_hash(capture / name, expected, label=f"capture file {name}")
        for name, expected in EXPECTED_CAPTURE_SHA256.items()
    }
    preflight = json.loads(
        (capture / "capture-session-preflight.json").read_text(encoding="utf-8")
    )
    if (
        preflight.get("passed") is not True
        or preflight.get("displayActive") is not True
        or preflight.get("displayAsleep") is not False
        or preflight.get("sessionLocked") is not False
        or preflight.get("sessionLoginDone") is not True
        or preflight.get("sessionOnConsole") is not True
        or preflight.get("backingScaleFactor") != 2
        or preflight.get("physicalPixels") != [3456, 2234]
    ):
        raise ValueError("physical-Retina capture-session preflight failed")

    timeline = json.loads(
        (capture / "transition-timeline.json").read_text(encoding="utf-8")
    )
    if (
        timeline.get("failedSamples") != 0
        or timeline.get("appearance") != "dark"
        or timeline.get("material") != "regular"
        or timeline.get("direction") != "dematerialize"
        or timeline.get("windowBackingScaleFactor") != 2
    ):
        raise ValueError("capture timeline scope differs")

    record = load_record(capture / "sample28-border-highlight-arithmetic.json")
    if (
        record.get("executed") is not True
        or record.get("systemSpecializationExact") is not True
        or record.get("capturedAppleFunctionUnmodified") is not True
        or record.get("uniformOffset") != 3024
        or record.get("uniformBufferLength") != 262_144
    ):
        raise ValueError("sample-28 Apple alpha oracle did not execute exactly")
    exact_comparison(
        record.get("capturedVsRebuiltBGRA8"), label="captured/system alpha oracle"
    )

    transport = object_value(
        record.get("sample28BorderFragmentTransport"), label="fragment transport"
    )
    inputs = object_value(transport.get("inputs"), label="fragment transport inputs")
    natural = object_value(transport.get("natural"), label="natural transport")
    alpha_oracle = object_value(
        transport.get("alphaOracle"), label="alpha-oracle transport"
    )
    if (
        transport.get("executed") is not True
        or inputs.get("arithmeticPreregistrationSHA256")
        != EXPECTED_CAPTURE_ARITHMETIC_PREREGISTRATION_SHA256
        or inputs.get("liveAppleFrameMutated") is not False
        or inputs.get("capturedApplePipelineMutated") is not False
        or inputs.get("capturedBuffersMutated") is not False
        or inputs.get("indexCount") != 24
        or inputs.get("uniformPrefixSHA256")
        != "d9d07c4c5e6030f86b8a9e070b01691074152c5b7b8c4a5d775932b33a5a8936"
        or natural.get("capturedApplePipelineUnmodified") is not True
        or natural.get("systemSpecializationUnmodified") is not True
    ):
        raise ValueError("sample-28 fragment transport provenance differs")
    exact_comparison(
        natural.get("capturedVsSystemSpecialization"), label="natural transport"
    )
    exact_comparison(
        alpha_oracle.get("capturedVsSystemSpecialization"),
        label="alpha-oracle transport",
    )

    diagnostics = object_value(
        record.get("customHighlightSDFDiagnostics"), label="custom SDF diagnostics"
    )
    replays = diagnostics.get("replays")
    expected_names = {
        "sdf",
        "sdf-float",
        "sdf-geometry",
        "sdf-oval",
        "sdf-normal",
        "sdf-shape-geometry",
        "sdf-shape-normal",
        "sdf-radial-normal",
        "sdf-composite-normal",
    }
    if (
        diagnostics.get("executed") is not True
        or diagnostics.get("pipelineCount") != 9
        or diagnostics.get("replayCount") != 9
        or diagnostics.get("customStageInVertex") is not True
        or not isinstance(replays, list)
        or {replay.get("name") for replay in replays if isinstance(replay, Mapping)}
        != expected_names
        or not all(
            isinstance(replay, Mapping)
            and replay.get("executed") is True
            and object_value(replay.get("replay"), label="SDF replay").get("executed")
            is True
            for replay in replays
        )
    ):
        raise ValueError("custom SDF diagnostic set is incomplete")

    tomography = object_value(record.get("stageTomography"), label="tomography")
    cases = tomography.get("cases")
    if (
        tomography.get("executed") is not True
        or tomography.get("capturedAppleFunctionUnmodified") is not True
        or tomography.get("caseCount") != 10
        or not isinstance(cases, list)
        or len(cases) != 10
        or not all(
            isinstance(case, Mapping)
            and case.get("executed") is True
            and object_value(case.get("replay"), label="tomography replay").get(
                "executed"
            )
            is True
            for case in cases
        )
    ):
        raise ValueError("sample-28 tomography is incomplete")
    return record, {
        "files": hashes,
        "preflight": {
            "passed": True,
            "physicalPixels": [3456, 2234],
            "logicalPoints": [1728, 1117],
            "backingScaleFactor": 2,
            "displayActive": True,
            "sessionUnlockedOnConsole": True,
        },
        "timeline": {
            "sha256": hashes["transition-timeline.json"],
            "failedSamples": 0,
            "sampleCount": timeline.get("sampleCount"),
        },
        "capturedPrivateVsSystemSpecializationMismatchedBytes": 0,
        "arithmeticPreregistrationSha256": (
            EXPECTED_CAPTURE_ARITHMETIC_PREREGISTRATION_SHA256
        ),
        "diagnosticPipelineCount": 9,
        "tomographyCaseCount": 10,
    }


def validate_fixture(fixture: Path) -> tuple[JsonObject, JsonObject]:
    manifest_path = fixture / "manifest.json"
    manifest_sha256 = require_hash(
        manifest_path,
        EXPECTED_FIXTURE_MANIFEST_SHA256,
        label="sample-28 fixture manifest",
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (
        manifest.get("sampleIndex") != 28
        or manifest.get("material") != "regular"
        or manifest.get("appearance") != "dark"
        or manifest.get("direction") != "dematerialize"
        or manifest.get("highlightOnlyFixture") is not False
    ):
        raise ValueError("sample-28 fixture scope differs")
    files = object_value(manifest.get("files"), label="fixture files")
    validated: JsonObject = {}
    for name, raw_metadata in files.items():
        if not isinstance(name, str):
            raise ValueError("fixture filename is not a string")
        metadata = object_value(raw_metadata, label=f"fixture file {name}")
        expected_hash = metadata.get("sha256")
        expected_bytes = metadata.get("byteCount")
        if not isinstance(expected_hash, str) or not isinstance(expected_bytes, int):
            raise ValueError(f"fixture metadata is incomplete: {name}")
        path = fixture / name
        if path.stat().st_size != expected_bytes:
            raise ValueError(f"fixture byte count differs: {name}")
        validated[name] = {
            "sha256": require_hash(path, expected_hash, label=f"fixture file {name}"),
            "byteCount": expected_bytes,
            "role": metadata.get("role"),
        }
    return manifest, {"manifestSha256": manifest_sha256, "files": validated}


def load_uint32(path: Path) -> UInt32Image:
    words = np.fromfile(path, dtype="<u4")
    if words.size != PIXELS * 4:
        raise ValueError(f"{path} has {words.size} uint32 words; expected {PIXELS * 4}")
    return words.reshape(HEIGHT, WIDTH, 4)


def load_half(path: Path) -> UInt16Image:
    words = np.fromfile(path, dtype="<u2")
    if words.size != PIXELS * 4:
        raise ValueError(f"{path} has {words.size} half words; expected {PIXELS * 4}")
    return words.reshape(HEIGHT, WIDTH, 4)


def decode_trace_words(pixels: UInt16Image) -> UInt32Image:
    values = pixels.view("<f2").astype(np.float32)
    scaled = values * 255.0
    encoded = np.rint(scaled).astype(np.uint32)
    if np.any(np.abs(scaled - encoded.astype(np.float32)) >= 0.5):
        raise ValueError("binary16 byte trace does not round-trip")
    return (
        encoded[..., 0]
        | (encoded[..., 1] << np.uint32(8))
        | (encoded[..., 2] << np.uint32(16))
        | (encoded[..., 3] << np.uint32(24))
    )


def compare_words(reference: NDArray[Any], candidate: NDArray[Any]) -> JsonObject:
    if reference.shape != candidate.shape:
        raise ValueError(f"comparison shapes differ: {reference.shape} != {candidate.shape}")
    mismatch = reference != candidate
    delta = np.abs(reference.astype(np.int64) - candidate.astype(np.int64))
    return {
        "exact": not bool(np.any(mismatch)),
        "checkedWords": int(reference.size),
        "mismatchedWords": int(np.count_nonzero(mismatch)),
        "maximumBitDistance": int(delta.max(initial=0)),
        "referenceNonzeroWords": int(np.count_nonzero(reference)),
        "candidateNonzeroWords": int(np.count_nonzero(candidate)),
    }


def edited_uniform(original: bytes, edits: object) -> bytes:
    if not isinstance(edits, list):
        raise ValueError("tomography edits are not a list")
    result = bytearray(original)
    for raw_edit in edits:
        edit = object_value(raw_edit, label="tomography edit")
        offset = edit.get("recordOffset")
        encoded = edit.get("hex")
        if not isinstance(offset, int) or not isinstance(encoded, str):
            raise ValueError("tomography edit metadata differs")
        payload = bytes.fromhex(encoded)
        end = offset + len(payload)
        if offset < 0 or not payload or end > len(result):
            raise ValueError("tomography edit is outside the uniform prefix")
        result[offset:end] = payload
    return bytes(result)


def renderer_options(
    fixture: Path,
    manifest: Mapping[str, Any],
    intrinsic_table: Path,
    device_index: int,
) -> JsonObject:
    construction = object_value(manifest.get("construction"), label="construction")
    axis = np.fromfile(
        fixture / "highlight-interpolant-axis.rgba32ui", dtype="<u4"
    )
    if axis.size != 8 * WIDTH * 4:
        raise ValueError("highlight interpolant axis size differs")
    return {
        "intrinsic_table": intrinsic_table,
        "interpolant_axis_data": axis.reshape(8, WIDTH, 4),
        "interpolant_axis_start": 0,
        "source_mip_bgra_levels": sweep.source_levels(fixture, construction),
        "destination_bgra_data": (fixture / "destination.rgba8").read_bytes(),
        "main_geometry": sweep.geometry(
            fixture, vertex_name="main-vertices.f32", index_name=None
        ),
        "shadow_geometry": sweep.geometry(
            fixture,
            vertex_name="shadow-vertices.f32",
            index_name="shadow-indices.u16",
        ),
        "final_highlight_geometry": sweep.geometry(
            fixture,
            vertex_name="highlight-vertices.f32",
            index_name="highlight-indices.u16",
        ),
        "profile_payload": (fixture / "profile.bin").read_bytes(),
        "runtime_data": {},
        "load_interpolant_trace": False,
        "load_interpolant_axis_trace": True,
        "load_diagnostic_traces": False,
        "context_arguments": {"device_index": device_index},
    }


def reference_stage(capture: Path, stage: Stage) -> UInt32Image:
    words = load_uint32(capture / f"{PREFIX}{stage.file_suffix}")[..., stage.channel]
    return ((words >> np.uint32(stage.shift)) & np.uint32(stage.mask)).copy()


def render_stage(
    renderer: AppleGlassReferenceRenderer,
    payload: bytes,
    stage: Stage,
) -> UInt32Image:
    pixels = renderer.render_final_highlight_half(
        uniform_payload=payload,
        trace_mode=stage.trace_mode,
    )
    return decode_trace_words(pixels) & np.uint32(stage.mask)


def alpha_reference(path: Path) -> UInt16Image:
    pixels = load_half(path)
    if (
        not np.all(pixels[..., :3] == pixels[..., :1])
        or not np.all(pixels[..., 3] == 0x3C00)
    ):
        raise ValueError(f"alpha-oracle channel semantics differ: {path}")
    return pixels[..., 0].copy()


def render_alpha(
    renderer: AppleGlassReferenceRenderer,
    payload: bytes,
) -> UInt16Image:
    pixels = renderer.render_final_highlight_half(
        uniform_payload=payload,
        trace_mode=2,
    )
    if not np.all(pixels == pixels[..., :1]):
        raise ValueError("candidate alpha trace channels differ")
    return pixels[..., 0]


def summarize(comparisons: Mapping[str, Mapping[str, Any]]) -> JsonObject:
    return {
        "caseCount": len(comparisons),
        "checkedWords": sum(int(value["checkedWords"]) for value in comparisons.values()),
        "mismatchedWords": sum(
            int(value["mismatchedWords"]) for value in comparisons.values()
        ),
        "exactCaseCount": sum(bool(value["exact"]) for value in comparisons.values()),
        "maximumBitDistance": max(
            (int(value["maximumBitDistance"]) for value in comparisons.values()),
            default=0,
        ),
    }


def run(arguments: argparse.Namespace) -> JsonObject:
    source_hashes = {
        relative: require_hash(ROOT / relative, expected, label=relative)
        for relative, expected in EXPECTED_SOURCE_SHA256.items()
    }
    production_hash = require_hash(
        ROOT / "shaders/frag.glsl",
        EXPECTED_PRODUCTION_SHADER_SHA256,
        label="protected production shader",
    )
    intrinsic_hash = require_hash(
        arguments.intrinsic_table,
        EXPECTED_INTRINSIC_SHA256,
        label="Apple float-intrinsic table",
    )
    record, capture_provenance = validate_capture(arguments.capture)
    manifest, fixture_provenance = validate_fixture(arguments.fixture)
    modes, config_provenance = sweep.load_modes(arguments.fixture)
    payload = (arguments.fixture / "highlight-uniform.bin").read_bytes()
    profile = (arguments.fixture / "profile.bin").read_bytes()
    if len(payload) != 248 or len(profile) < 48:
        raise ValueError("SDF uniform records are truncated")
    background_sdf_bits = struct.unpack_from("<4I", profile, 0)
    final_sdf_bits = struct.unpack_from("<4I", payload, 0)
    if (
        background_sdf_bits[:2] != (0x43770167, 0x43770167)
        or final_sdf_bits[:2] != (0x43770168, 0x43770168)
        or background_sdf_bits[2:] != final_sdf_bits[2:]
    ):
        raise ValueError("sample-28 pass-owned SDF discriminator differs")
    wrong_pass_payload = profile[:48] + payload[48:]

    raw_stage_references = {
        stage.name: reference_stage(arguments.capture, stage) for stage in STAGES
    }
    interpolant_reference = load_uint32(
        arguments.capture / f"{PREFIX}interpolant-rgba32uint-rgba32ui.raw"
    )
    sdf_active = np.any(interpolant_reference[..., :2] != 0, axis=2)
    if np.count_nonzero(sdf_active) != 262_144:
        raise ValueError("sample-28 SDF diagnostic active mask differs")
    natural_reference = alpha_reference(
        arguments.capture / f"{PREFIX}rebuilt-rgba16float-rgba16f.raw"
    )
    tomography = object_value(record.get("stageTomography"), label="tomography")
    raw_cases = tomography.get("cases")
    if not isinstance(raw_cases, list):
        raise ValueError("tomography cases are absent")

    baseline_stages: JsonObject = {}
    candidate_stages: JsonObject = {}
    baseline_tomography: JsonObject = {}
    candidate_tomography: JsonObject = {}
    with AppleGlassReferenceRenderer(
        arguments.fixture,
        **renderer_options(
            arguments.fixture,
            manifest,
            arguments.intrinsic_table,
            arguments.device_index,
        ),
    ) as renderer:
        renderer.program["CoordinateMode"].value = 7
        renderer.program["AppleInterpolantAxisStart"].value = 0
        renderer.program["UseAppleIntrinsicTable"].value = 1
        renderer.program["UseAppleHalfIntrinsicTable"].value = 0
        sweep.set_modes(renderer, modes)

        for normal_mode, target in ((0, baseline_stages), (5, candidate_stages)):
            renderer.program["HighlightSdfNormalMode"].value = normal_mode
            for stage in STAGES:
                target[stage.name] = compare_words(
                    raw_stage_references[stage.name],
                    render_stage(renderer, payload, stage),
                )

        sdf_reference = load_half(arguments.capture / f"{PREFIX}custom-sdf-rgba16f.raw")
        renderer.program["HighlightSdfNormalMode"].value = 0
        baseline_sdf = compare_words(
            sdf_reference[sdf_active, :3],
            renderer.render_final_highlight_half(
                uniform_payload=payload,
                trace_mode=1,
            )[sdf_active, :3],
        )
        baseline_natural = compare_words(
            natural_reference,
            render_alpha(renderer, payload),
        )
        wrong_pass_control = compare_words(
            natural_reference,
            render_alpha(renderer, wrong_pass_payload),
        )

        renderer.program["HighlightSdfNormalMode"].value = 5
        candidate_sdf = compare_words(
            sdf_reference[sdf_active, :3],
            renderer.render_final_highlight_half(
                uniform_payload=payload,
                trace_mode=1,
            )[sdf_active, :3],
        )
        candidate_natural = compare_words(
            natural_reference,
            render_alpha(renderer, payload),
        )

        for raw_case in raw_cases:
            case = object_value(raw_case, label="tomography case")
            name = case.get("name")
            replay = object_value(case.get("replay"), label="tomography replay")
            output = object_value(replay.get("output"), label="tomography output")
            raw_name = output.get("rawFile")
            if not isinstance(name, str) or not isinstance(raw_name, str):
                raise ValueError("tomography case identity differs")
            reference = alpha_reference(arguments.capture / raw_name)
            edited = edited_uniform(payload, case.get("edits"))
            renderer.program["HighlightSdfNormalMode"].value = 0
            baseline_tomography[name] = compare_words(
                reference,
                render_alpha(renderer, edited),
            )
            renderer.program["HighlightSdfNormalMode"].value = 5
            candidate_tomography[name] = compare_words(
                reference,
                render_alpha(renderer, edited),
            )
        implementation = renderer.implementation

    first_baseline_divergence = next(
        (
            stage.name
            for stage in STAGES
            if not bool(baseline_stages[stage.name]["exact"])
        ),
        None,
    )
    baseline_stage_total = summarize(baseline_stages)
    candidate_stage_total = summarize(candidate_stages)
    baseline_tomography_total = summarize(baseline_tomography)
    candidate_tomography_total = summarize(candidate_tomography)
    positive_controls_pass = (
        wrong_pass_control["mismatchedWords"] == 148
        and wrong_pass_control["maximumBitDistance"] == 24
        and baseline_tomography_total["mismatchedWords"] == 47
        and first_baseline_divergence == "radial-input-y"
        and natural_reference.nonzero()[0].size == 2_520
    )
    calibration_exact = (
        candidate_stage_total["mismatchedWords"] == 0
        and candidate_sdf["mismatchedWords"] == 0
        and candidate_natural["mismatchedWords"] == 0
        and candidate_tomography_total["mismatchedWords"] == 0
        and positive_controls_pass
    )
    return {
        "schemaVersion": 1,
        "scope": "sample-28 eight-primitive final-highlight arithmetic calibration",
        "provenance": {
            "capture": capture_provenance,
            "fixture": fixture_provenance,
            "config": config_provenance,
            "sources": source_hashes,
            "floatIntrinsicTableSha256": intrinsic_hash,
            "productionShader": {
                "sha256": production_hash,
                "modified": False,
                "renderedByThisGate": False,
            },
            "implementation": implementation,
        },
        "passUniformOwnership": {
            "backgroundSdfArgBits": [f"0x{value:08x}" for value in background_sdf_bits],
            "finalHighlightSdfArgBits": [f"0x{value:08x}" for value in final_sdf_bits],
            "halfSizeUlpDelta": [
                final_sdf_bits[index] - background_sdf_bits[index]
                for index in range(2)
            ],
            "wrongPassUniformPositiveControl": wrong_pass_control,
        },
        "recoveredRule": {
            "radialY": "point.y * (arg.x * apple_fast_reciprocal(arg.y))",
            "radialSquared": "radial.y * radial.y + (radial.x * radial.x)",
            "radialInverseLength": "apple_fast_rsqrt(radialSquared)",
            "perPixelCorrectionTableUsed": False,
            "capturedPixelSurfaceUsedAsRendererInput": False,
        },
        "baseline": {
            "description": "correct final-pass uniform with prior AMD division/order",
            "firstDivergentStage": first_baseline_divergence,
            "stageTotals": baseline_stage_total,
            "stages": baseline_stages,
            "sdf": baseline_sdf,
            "naturalAlpha": baseline_natural,
            "tomographyTotals": baseline_tomography_total,
            "tomography": baseline_tomography,
        },
        "candidate": {
            "stageTotals": candidate_stage_total,
            "stages": candidate_stages,
            "sdf": candidate_sdf,
            "naturalAlpha": candidate_natural,
            "tomographyTotals": candidate_tomography_total,
            "tomography": candidate_tomography,
        },
        "gate": {
            "positiveControlsPassed": positive_controls_pass,
            "calibrationExact": calibration_exact,
            "comparisonTolerance": 0,
            "prospectiveUnseenRetinaHoldoutRequired": True,
            "eightStateAmdFrameGateRequired": True,
            "productionWalleParityEstablished": False,
            "shaderQualityReductionAllowed": False,
        },
    }


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--capture",
        type=Path,
        default=ROOT / "artifacts/local-normal-arithmetic-9f68f71-01",
    )
    parser.add_argument(
        "--fixture",
        type=Path,
        default=Path("/tmp/walle-border-fixture-base"),
    )
    parser.add_argument(
        "--intrinsic-table",
        type=Path,
        default=ROOT / "artifacts/apple-float-intrinsics-r8-30556057571.bin",
    )
    parser.add_argument("--device-index", type=int, default=1)
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def main() -> int:
    arguments = parse_arguments()
    report = run(arguments)
    encoded = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if arguments.output is None:
        print(encoded, end="")
    else:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(encoded, encoding="utf-8")
        print(arguments.output)
    return 0 if report["gate"]["calibrationExact"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
