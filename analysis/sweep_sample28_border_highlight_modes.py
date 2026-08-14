#!/usr/bin/env python3
"""Discriminate Walle's sample-28 border alpha residual by arithmetic mode."""

import argparse
import hashlib
import json
import struct
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import numpy as np


type JsonObject = dict[str, Any]

ROOT = Path(__file__).resolve().parent.parent
LG_ANALYSIS = ROOT / "lg-test" / "Analysis"
if str(LG_ANALYSIS) not in sys.path:
    sys.path.insert(0, str(LG_ANALYSIS))

from apple_glass_reference_renderer import (  # noqa: E402
    AppleGlassReferenceRenderer,
    DrawGeometry,
)


WIDTH = 1_024
HEIGHT = 1_024
CONFIG_FORMAT = "<8s23I15iI"
CONFIG_BYTES = struct.calcsize(CONFIG_FORMAT)
EXPECTED_CAPTURE_COMMIT = "47aa8fb27cad780c16869358ec52e61e631cf86d"
EXPECTED_TIMELINE_SHA256 = (
    "609485e86b185358b0b762bd95143d3a29f3d1049b3a843997f0cf7b05fa9b0a"
)
EXPECTED_PREREGISTRATION_SHA256 = (
    "68545eaa26081d2710c386eee3b7554e66f8c17898f1f07a71c6be6e89944103"
)
EXPECTED_CONFIG_SHA256 = (
    "127a2b6297a94285216dfdd8d8815bb7a0d5f82bee9ef9f1cf2291351739b64f"
)
EXPECTED_APPLE_ALPHA_SHA256 = (
    "726e63463c7f1beb6e2d574d1d5f0838545d640f2d65409d81b957e53ba291af"
)
EXPECTED_INTRINSIC_SHA256 = (
    "fff71cc0d4428677ca5bc58b91212a7166b701e4efe504c3d71cab70846d0449"
)
EXPECTED_VERTEX_SHA256 = (
    "99d6942f6b39b52460c23b4e52c498f2d98a03cb6ffc32d87ef6e94c43e7a958"
)

APPLE_ALPHA_FILE = (
    "transition-background-uniform-28-current-Iscd-final-highlight-alpha-"
    "rebuilt-rgba16float-rgba16f.raw"
)
TRANSPORT_RECORD_FILE = "sample28-border-fragment-transport.json"

MODE_FIELDS = (
    "derivative",
    "coordinate",
    "alphaUlpBias",
    "floatDivision",
    "coverage",
    "mix",
    "band",
    "normalize",
    "normalizedCoordinate",
    "sdfArithmetic",
    "sdfSquaredUlpBias",
    "sdfDistanceUlpBias",
    "sourceDivision",
    "sourceConstruction",
    "destinationDivision",
)
SHADER_UNIFORMS = {
    "derivative": "HighlightDerivativeMode",
    "coordinate": "HighlightCoordinateMode",
    "alphaUlpBias": "HighlightAlphaUlpBias",
    "floatDivision": "HighlightFloatDivisionMode",
    "coverage": "HighlightCoverageArithmeticMode",
    "mix": "HighlightMixMode",
    "band": "HighlightBandMode",
    "normalize": "HighlightNormalizeMode",
    "normalizedCoordinate": "HighlightNormalizedCoordinateMode",
    "sdfArithmetic": "HighlightSdfArithmeticMode",
    "sdfSquaredUlpBias": "HighlightSdfSquaredUlpBias",
    "sdfDistanceUlpBias": "HighlightSdfDistanceUlpBias",
    "sourceDivision": "HighlightSourceDivisionMode",
    "sourceConstruction": "HighlightSourceConstructionMode",
    "destinationDivision": "HighlightDestinationDivisionMode",
}
SWEEP_DOMAINS: dict[str, Sequence[int]] = {
    "derivative": tuple(range(9)),
    "alphaUlpBias": tuple(range(-4, 5)),
    "floatDivision": tuple(range(6)),
    "coverage": tuple(range(3)),
    "mix": tuple(range(5)),
    "band": tuple(range(3)),
    "normalize": tuple(range(6)),
    "normalizedCoordinate": tuple(range(5)),
    "sdfArithmetic": tuple(range(4)),
    "sdfSquaredUlpBias": tuple(range(-2, 3)),
    "sdfDistanceUlpBias": tuple(range(-2, 3)),
}


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1 << 20):
            digest.update(block)
    return digest.hexdigest()


def require_sha256(path: Path, expected: str, *, name: str) -> None:
    observed = sha256_path(path)
    if observed != expected:
        raise ValueError(f"{name} SHA-256 differs: {observed}")


def object_value(value: object, *, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} is not an object")
    return value


def load_modes(fixture: Path) -> tuple[dict[str, int], JsonObject]:
    config_path = fixture / "config.bin"
    require_sha256(config_path, EXPECTED_CONFIG_SHA256, name="fixture config")
    raw = config_path.read_bytes()
    if len(raw) != CONFIG_BYTES:
        raise ValueError("fixture config byte count differs")
    values = struct.unpack(CONFIG_FORMAT, raw)
    if values[0] != b"WALLELG3":
        raise ValueError("fixture config magic differs")
    unsigned = values[1:24]
    signed = values[24:39]
    if (
        unsigned[0:5] != (WIDTH, HEIGHT, 1, 1, 5)
        or unsigned[15:18] != (48, 16, 24)
        or unsigned[18] != 10
        or unsigned[19:23] != (314, 314, 516, 516)
        or values[39] != 0
    ):
        raise ValueError("fixture config structural fields differ")
    modes = dict(zip(MODE_FIELDS, signed, strict=True))
    return modes, {
        "sha256": EXPECTED_CONFIG_SHA256,
        "byteCount": len(raw),
        "highlightVertexCount": unsigned[16],
        "highlightIndexCount": unsigned[17],
        "backgroundScissor": list(unsigned[19:23]),
        "modes": modes,
    }


def validate_transport(capture: Path) -> JsonObject:
    context = (capture / "capture-context.txt").read_text(encoding="utf-8")
    if f"CAPTURE_COMMIT={EXPECTED_CAPTURE_COMMIT}\n" not in context:
        raise ValueError("capture commit differs")
    if "GITHUB_ACTIONS_USED=0\n" not in context:
        raise ValueError("capture did not exclude GitHub Actions")
    timeline = capture / "transition-timeline.json"
    require_sha256(timeline, EXPECTED_TIMELINE_SHA256, name="capture timeline")
    trace = json.loads((capture / TRANSPORT_RECORD_FILE).read_text(encoding="utf-8"))
    comparison = object_value(
        trace.get("capturedVsRebuiltBGRA8"),
        name="captured/system alpha comparison",
    )
    transport = object_value(
        trace.get("sample28BorderFragmentTransport"),
        name="sample-28 transport",
    )
    inputs = object_value(transport.get("inputs"), name="transport inputs")
    natural = object_value(transport.get("natural"), name="natural transport")
    natural_comparison = object_value(
        natural.get("capturedVsSystemSpecialization"),
        name="natural captured/system comparison",
    )
    if (
        trace.get("executed") is not True
        or trace.get("systemSpecializationExact") is not True
        or comparison.get("exactByteMatch") is not True
        or comparison.get("mismatchedByteCount") != 0
        or transport.get("executed") is not True
        or inputs.get("preregistrationSHA256") != EXPECTED_PREREGISTRATION_SHA256
        or inputs.get("liveAppleFrameMutated") is not False
        or inputs.get("capturedApplePipelineMutated") is not False
        or inputs.get("capturedBuffersMutated") is not False
        or inputs.get("indexCount") != 24
        or natural.get("executed") is not True
        or natural_comparison.get("exactByteMatch") is not True
        or natural_comparison.get("mismatchedByteCount") != 0
    ):
        raise ValueError("sample-28 fragment transport did not pass exactly")
    return {
        "captureCommit": EXPECTED_CAPTURE_COMMIT,
        "timelineSha256": EXPECTED_TIMELINE_SHA256,
        "preregistrationSha256": EXPECTED_PREREGISTRATION_SHA256,
        "capturedVsSystemAlphaOracleMismatchedBytes": 0,
        "capturedVsSystemNaturalMismatchedBytes": 0,
        "indexCount": 24,
    }


def load_apple_alpha(capture: Path) -> tuple[np.ndarray, JsonObject]:
    path = capture / APPLE_ALPHA_FILE
    require_sha256(path, EXPECTED_APPLE_ALPHA_SHA256, name="Apple half alpha")
    words = np.fromfile(path, dtype="<u2")
    if words.size != WIDTH * HEIGHT * 4:
        raise ValueError("Apple half-alpha output byte count differs")
    pixels = words.reshape(HEIGHT, WIDTH, 4)
    alpha = pixels[..., 0]
    active = alpha != 0
    if (
        not np.all(pixels[..., :3] == pixels[..., :1])
        or not np.all(pixels[..., 3] == 0x3C00)
        or not np.all(pixels[..., :3][~active] == 0)
        or np.count_nonzero(active) != 2_520
    ):
        raise ValueError("Apple alpha-oracle surface semantics differ")
    return alpha.copy(), {
        "file": APPLE_ALPHA_FILE,
        "sha256": EXPECTED_APPLE_ALPHA_SHA256,
        "checkedHalfWords": WIDTH * HEIGHT,
        "activePixels": int(np.count_nonzero(active)),
        "rgbChannelsEqual": True,
        "outputAlphaIsOne": True,
        "inactiveRgbIsZero": True,
    }


def geometry(
    directory: Path,
    *,
    vertex_name: str,
    index_name: str | None,
) -> DrawGeometry:
    vertices = np.fromfile(directory / vertex_name, dtype="<f4")
    if vertices.size % 8:
        raise ValueError(f"{vertex_name} has a partial vertex")
    indices = (
        np.fromfile(directory / index_name, dtype="<u2")
        if index_name is not None
        else None
    )
    return DrawGeometry(vertices=vertices.reshape(-1, 8), indices=indices)


def source_levels(
    directory: Path,
    construction: Mapping[str, Any],
) -> dict[int, tuple[int, int, bytes]]:
    extent = construction.get("sourceExtent")
    count = construction.get("sourceMipCount")
    if (
        not isinstance(extent, list)
        or len(extent) != 2
        or not all(isinstance(value, int) for value in extent)
        or not isinstance(count, int)
    ):
        raise ValueError("fixture source pyramid metadata differs")
    width, height = extent
    result: dict[int, tuple[int, int, bytes]] = {}
    for level in range(count):
        payload = (directory / f"source-mip-{level}.rgba8").read_bytes()
        if len(payload) != width * height * 4:
            raise ValueError(f"source mip {level} byte count differs")
        result[level] = (width, height, payload)
        width //= 2
        height //= 2
    return result


def compare_alpha(apple: np.ndarray, candidate: np.ndarray) -> JsonObject:
    if candidate.shape != (HEIGHT, WIDTH):
        raise ValueError("candidate alpha dimensions differ")
    mismatch = apple != candidate
    mask_mismatch = (apple != 0) != (candidate != 0)
    coordinates = np.argwhere(mismatch)
    pairs = Counter((int(apple[y, x]), int(candidate[y, x])) for y, x in coordinates)
    bit_distance = np.abs(apple.astype(np.int32) - candidate.astype(np.int32))
    return {
        "checkedHalfWords": WIDTH * HEIGHT,
        "mismatchedHalfWords": int(np.count_nonzero(mismatch)),
        "coverageMaskMismatchedPixels": int(np.count_nonzero(mask_mismatch)),
        "appleActivePixels": int(np.count_nonzero(apple)),
        "candidateActivePixels": int(np.count_nonzero(candidate)),
        "maximumHalfBitDistance": int(bit_distance.max(initial=0)),
        "firstMismatches": [
            {
                "x": int(x),
                "yTopLeft": int(y),
                "appleBits": f"0x{int(apple[y, x]):04x}",
                "candidateBits": f"0x{int(candidate[y, x]):04x}",
                "signedBitDelta": int(candidate[y, x]) - int(apple[y, x]),
            }
            for y, x in coordinates[:32]
        ],
        "mismatchPairs": [
            {
                "appleBits": f"0x{left:04x}",
                "candidateBits": f"0x{right:04x}",
                "count": count,
            }
            for (left, right), count in pairs.most_common()
        ],
    }


def set_modes(
    renderer: AppleGlassReferenceRenderer,
    modes: Mapping[str, int],
) -> None:
    for name, uniform in SHADER_UNIFORMS.items():
        renderer.program[uniform].value = modes[name]


def render_alpha(
    renderer: AppleGlassReferenceRenderer,
    *,
    uniform_payload: bytes,
    modes: Mapping[str, int],
) -> np.ndarray:
    set_modes(renderer, modes)
    pixels = renderer.render_final_highlight_half(
        uniform_payload=uniform_payload,
        trace_mode=2,
    )
    if not np.all(pixels == pixels[..., :1]):
        raise ValueError("candidate alpha trace channels differ")
    return pixels[..., 0]


def run(arguments: argparse.Namespace) -> JsonObject:
    capture_record = validate_transport(arguments.capture)
    apple, apple_record = load_apple_alpha(arguments.capture)
    baseline_modes, fixture_record = load_modes(arguments.fixture)
    require_sha256(
        arguments.vertex_shader,
        EXPECTED_VERTEX_SHA256,
        name="diagnostic vertex shader",
    )
    require_sha256(
        arguments.intrinsic_table,
        EXPECTED_INTRINSIC_SHA256,
        name="Apple float-intrinsic table",
    )

    manifest = json.loads(
        (arguments.fixture / "manifest.json").read_text(encoding="utf-8")
    )
    construction = object_value(
        manifest.get("construction"), name="fixture construction"
    )
    axis = np.fromfile(
        arguments.fixture / "highlight-interpolant-axis.rgba32ui",
        dtype="<u4",
    )
    if axis.size != 8 * WIDTH * 4:
        raise ValueError("eight-primitive highlight axis byte count differs")
    axis = axis.reshape(8, WIDTH, 4)
    uniform_payload = (arguments.fixture / "highlight-uniform.bin").read_bytes()
    if len(uniform_payload) != 248:
        raise ValueError("highlight uniform prefix byte count differs")

    shader = arguments.fragment_shader.read_text(encoding="utf-8")
    with AppleGlassReferenceRenderer(
        arguments.fixture,
        vertex_shader=arguments.vertex_shader,
        fragment_shader_source=shader,
        intrinsic_table=arguments.intrinsic_table,
        interpolant_axis_data=axis,
        interpolant_axis_start=0,
        source_mip_bgra_levels=source_levels(arguments.fixture, construction),
        destination_bgra_data=(arguments.fixture / "destination.rgba8").read_bytes(),
        main_geometry=geometry(
            arguments.fixture,
            vertex_name="main-vertices.f32",
            index_name=None,
        ),
        shadow_geometry=geometry(
            arguments.fixture,
            vertex_name="shadow-vertices.f32",
            index_name="shadow-indices.u16",
        ),
        final_highlight_geometry=geometry(
            arguments.fixture,
            vertex_name="highlight-vertices.f32",
            index_name="highlight-indices.u16",
        ),
        profile_payload=(arguments.fixture / "profile.bin").read_bytes(),
        runtime_data={},
        load_interpolant_trace=False,
        load_interpolant_axis_trace=True,
        load_diagnostic_traces=False,
        context_arguments={"device_index": arguments.device_index},
    ) as renderer:
        renderer.program["CoordinateMode"].value = 7
        renderer.program["AppleInterpolantAxisStart"].value = 0
        renderer.program["UseAppleIntrinsicTable"].value = 1
        renderer.program["UseAppleHalfIntrinsicTable"].value = 0
        baseline_alpha = render_alpha(
            renderer,
            uniform_payload=uniform_payload,
            modes=baseline_modes,
        )
        baseline = compare_alpha(apple, baseline_alpha)
        if (
            baseline["mismatchedHalfWords"] != 148
            or baseline["coverageMaskMismatchedPixels"] != 0
            or baseline["appleActivePixels"] != 2_520
            or baseline["candidateActivePixels"] != 2_520
        ):
            raise ValueError(
                "baseline does not reproduce the independently measured residual: "
                + json.dumps(
                    {
                        name: baseline[name]
                        for name in (
                            "mismatchedHalfWords",
                            "coverageMaskMismatchedPixels",
                            "appleActivePixels",
                            "candidateActivePixels",
                            "maximumHalfBitDistance",
                        )
                    },
                    sort_keys=True,
                )
            )

        cases: list[JsonObject] = []
        for field, domain in SWEEP_DOMAINS.items():
            for value in domain:
                if value == baseline_modes[field]:
                    continue
                modes = {**baseline_modes, field: value}
                candidate = render_alpha(
                    renderer,
                    uniform_payload=uniform_payload,
                    modes=modes,
                )
                comparison = compare_alpha(apple, candidate)
                cases.append(
                    {
                        "changedField": field,
                        "changedValue": value,
                        "modes": modes,
                        **comparison,
                    }
                )
        implementation = renderer.implementation

    cases.sort(
        key=lambda case: (
            case["mismatchedHalfWords"],
            case["coverageMaskMismatchedPixels"],
            case["maximumHalfBitDistance"],
            case["changedField"],
            case["changedValue"],
        )
    )
    exact_cases = [case for case in cases if case["mismatchedHalfWords"] == 0]
    return {
        "schemaVersion": 1,
        "scope": "sample-28 eight-primitive final-highlight binary16 alpha",
        "capture": capture_record,
        "appleAlpha": apple_record,
        "fixture": fixture_record,
        "implementation": implementation,
        "baseline": {"modes": baseline_modes, **baseline},
        "sweep": {
            "method": "one arithmetic field changed at a time",
            "domains": {name: list(values) for name, values in SWEEP_DOMAINS.items()},
            "caseCount": len(cases),
            "exactCaseCount": len(exact_cases),
            "exactCases": exact_cases,
            "bestCases": cases[:16],
            "cases": cases,
        },
        "promotion": {
            "traceMayBeWalleInput": False,
            "frameTolerance": 0,
            "requiresIndependentRule": True,
            "requiresUnseenRetinaCapture": True,
            "requiresEightStateAmdZeroByteGate": True,
        },
    }


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--capture",
        type=Path,
        default=ROOT / "artifacts/local-fragment-transport-47aa8fb-01",
    )
    parser.add_argument(
        "--fixture", type=Path, default=Path("/tmp/walle-border-fixture-base")
    )
    parser.add_argument(
        "--vertex-shader",
        type=Path,
        default=ROOT / "analysis/apple_glass_reference.vert.glsl",
    )
    parser.add_argument(
        "--fragment-shader",
        type=Path,
        default=ROOT / "analysis/apple_glass_reference.frag.glsl",
    )
    parser.add_argument(
        "--intrinsic-table",
        type=Path,
        default=ROOT / "artifacts/apple-float-intrinsics-r8-30556057571.bin",
    )
    parser.add_argument("--device-index", type=int, default=1)
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def main() -> None:
    arguments = parse_arguments()
    result = run(arguments)
    serialized = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if arguments.output is None:
        print(serialized, end="")
    else:
        arguments.output.write_text(serialized, encoding="utf-8")


if __name__ == "__main__":
    main()
