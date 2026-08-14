#!/usr/bin/env python3
"""Gate the recovered current Apple compositor through Walle's GLSL path.

This is a captured-input port gate.  It proves that the recovered binary16
compositor arithmetic produces the same BGRA8 bytes as Apple's live Metal
function for both observed final-highlight roles.  It does not prove that
Walle independently constructs the dynamic alpha field or presents the same
bytes through a Retina display pipeline.
"""

import argparse
import hashlib
import json
import struct
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from apple_glass_reference_renderer import (
    AppleGlassReferenceRenderer,
    DrawGeometry,
    bgra_raw,
    compare_images,
)
from liquid_glass_shader_specialization import load_amd_exact_circle_shader
from liquid_glass_static_profile import (
    build_static_profile,
    canonical_static_profile_request,
)


type JsonObject = dict[str, Any]
type UInt32Image = NDArray[np.uint32]

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CAPTURE = (
    ROOT
    / "artifacts"
    / "local-current-final-compositor-transfer-walle-gl-port-01-v7"
)
TIMELINE_SHA256 = (
    "cf0497adfa050395c9fb2b74cb2fb66b61aee6d2ca6e04801e752591c52d29b5"
)
VALIDATION_SHA256 = (
    "3719d14e7c6fffe96f796928bde68861a1b8eea8b216d6dde53106c1aa2ac6ca"
)
WIDTH = 1024
HEIGHT = 1024
BYTES_PER_FRAME = WIDTH * HEIGHT * 4
ROLES = ("Iscd", "Irsd")
CASES = (
    "zero-rgb-unit-alpha",
    "unit-rgb-unit-alpha",
    "identity-rgb-unit-alpha",
    "permuted-rgb-unit-alpha",
    "identity-rgb-destination-alpha",
    "asymmetric-constant-unit-alpha",
    "natural-rgb-unit-alpha",
)

SCOPE: JsonObject = {
    "freshDirectRetinaMacCapture": True,
    "githubActionsCapture": False,
    "capturedDestinationSeeds": True,
    "capturedFinalHighlightAlphaFields": True,
    "capturedFinalOutputsUsedForComparisonOnly": True,
    "independentlyRecoveredCurrentCompositorArithmetic": True,
    "walleGlslPathRendered": True,
    "independentlyConstructedDynamicAlphaFields": False,
    "productionWalleProcessRendered": False,
    "physicalRetinaOutput": False,
    "formalLiquidGlassParity": False,
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _object(value: object, *, name: str) -> JsonObject:
    if not isinstance(value, dict):
        raise ValueError(f"{name} is not an object")
    return value


def _array(value: object, *, name: str) -> list[object]:
    if not isinstance(value, list):
        raise ValueError(f"{name} is not an array")
    return value


def _raw_file(capture: Path, output: object, *, byte_count: int) -> Path:
    metadata = _object(output, name="raw output")
    if metadata.get("rawCapture") is not True:
        raise ValueError("current-compositor fixture is not a raw capture")
    if metadata.get("width") != WIDTH or metadata.get("height") != HEIGHT:
        raise ValueError("current-compositor fixture has unexpected dimensions")
    if metadata.get("rawBytes") != byte_count:
        raise ValueError("current-compositor fixture has an unexpected byte count")
    name = metadata.get("rawFile")
    if not isinstance(name, str) or Path(name).name != name:
        raise ValueError("current-compositor raw filename is unsafe")
    path = capture / name
    if not path.is_file() or path.stat().st_size != byte_count:
        raise ValueError(f"current-compositor raw file is incomplete: {path}")
    return path


def load_records(capture: Path) -> list[JsonObject]:
    """Load and fail closed on the exact accepted v7 transfer corpus."""
    timeline_path = capture / "transition-timeline.json"
    validation_path = capture / "validation.json"
    if sha256_file(timeline_path) != TIMELINE_SHA256:
        raise ValueError("current-compositor timeline is not the admitted corpus")
    if sha256_file(validation_path) != VALIDATION_SHA256:
        raise ValueError("current-compositor validation is not the admitted corpus")

    validation = _object(
        json.loads(validation_path.read_text(encoding="utf-8")),
        name="validation",
    )
    if validation.get("accepted") is not True:
        raise ValueError("the direct Mac capture was not accepted")
    if validation.get("remainingAppleConstructionQuestions") != 0:
        raise ValueError("the capture still reports an Apple construction gap")

    timeline = _object(
        json.loads(timeline_path.read_text(encoding="utf-8")),
        name="timeline",
    )
    dynamic = _object(
        timeline.get("dynamicBackgroundUniforms"),
        name="dynamic background uniforms",
    )
    dynamic_records = _array(dynamic.get("records"), name="dynamic records")
    if len(dynamic_records) != 1:
        raise ValueError("expected exactly one dynamic transition record")
    render = _object(
        _object(dynamic_records[0], name="dynamic record").get("render"),
        name="dynamic render",
    )
    replay = _object(render.get("exactPassReplay"), name="exact pass replay")
    transfer = _object(
        replay.get("currentFinalCompositorTransfer"),
        name="current final compositor transfer",
    )
    if transfer.get("executed") is not True or transfer.get("recordCount") != 2:
        raise ValueError("current final compositor transfer is incomplete")

    records = [
        _object(value, name="current compositor role")
        for value in _array(transfer.get("records"), name="compositor records")
    ]
    if tuple(record.get("role") for record in records) != ROLES:
        raise ValueError("current compositor roles are incomplete or reordered")
    for record in records:
        role = record["role"]
        candidate = _object(record.get("candidate"), name=f"{role} candidate")
        expected_candidate = {
            "vibrantArithmeticMode": 10,
            "vibrantFMAAccumulationOrder": "g-r-b",
            "sourceConstructionMode": 1,
            "sourceDivisionMode": 0,
            "destinationDivisionMode": 0,
            "fastMathEnabled": False,
        }
        for key, expected in expected_candidate.items():
            if candidate.get(key) != expected:
                raise ValueError(f"{role} candidate has unexpected {key}")
        if record.get("candidatesExact") is not True:
            raise ValueError(f"{role} Apple-side candidate was not exact")

        alpha_trace = _object(record.get("alphaTrace"), name=f"{role} alpha")
        _raw_file(
            capture,
            alpha_trace.get("output"),
            byte_count=WIDTH * HEIGHT * 8,
        )
        seed = _object(record.get("seed"), name=f"{role} seed")
        _raw_file(capture, seed.get("output"), byte_count=BYTES_PER_FRAME)

        cases = [
            _object(value, name=f"{role} case")
            for value in _array(record.get("cases"), name=f"{role} cases")
        ]
        if tuple(case.get("name") for case in cases) != CASES:
            raise ValueError(f"{role} case matrix is incomplete or reordered")
        for case in cases:
            comparison = _object(
                case.get("candidateComparison"),
                name=f"{role}/{case['name']} comparison",
            )
            if (
                comparison.get("exactByteMatch") is not True
                or comparison.get("mismatchedByteCount") != 0
                or comparison.get("byteCount") != BYTES_PER_FRAME
            ):
                raise ValueError(
                    f"{role}/{case['name']} Apple-side candidate is not exact"
                )
            words = _array(
                case.get("matrixHalfWordsLittleEndian"),
                name=f"{role}/{case['name']} matrix words",
            )
            if len(words) != 24 or any(
                not isinstance(word, str) or not word.startswith("0x")
                for word in words
            ):
                raise ValueError(f"{role}/{case['name']} matrix is malformed")
            apple = _object(case.get("apple"), name=f"{role} Apple output")
            _raw_file(
                capture,
                apple.get("output"),
                byte_count=BYTES_PER_FRAME,
            )
    return records


def current_uniform_payload(words: list[object]) -> bytes:
    """Build the 248-byte A2Xghfc fragment record for one matrix case."""
    if len(words) != 24:
        raise ValueError("current compositor matrix must contain 24 half words")
    values: list[int] = []
    for word in words:
        if not isinstance(word, str):
            raise ValueError("current compositor matrix word is not hexadecimal")
        value = int(word, 16)
        if not 0 <= value <= 0xFFFF:
            raise ValueError("current compositor matrix word exceeds binary16")
        values.append(value)
    payload = bytearray(248)
    struct.pack_into("<24H", payload, 0x60, *values)
    return bytes(payload)


def alpha_trace_words(path: Path) -> UInt32Image:
    """Put the captured RGBA16F alpha into the GLSL trace texture contract."""
    source = np.fromfile(path, dtype="<u2")
    expected = WIDTH * HEIGHT * 4
    if source.size != expected:
        raise ValueError(f"alpha trace has {source.size} words; expected {expected}")
    rgba = source.reshape(HEIGHT, WIDTH, 4)
    if not np.array_equal(rgba[..., 0], rgba[..., 1]):
        raise ValueError("alpha trace red and green channels differ")
    if not np.array_equal(rgba[..., 0], rgba[..., 2]):
        raise ValueError("alpha trace red and blue channels differ")
    if not np.all(rgba[..., 3] == 0x3C00):
        raise ValueError("alpha trace output-alpha channel is not binary16 one")
    packed = np.zeros((HEIGHT, WIDTH, 4), dtype=np.uint32)
    packed[..., 1] = rgba[..., 0].astype(np.uint32)
    return packed


def _dummy_geometry() -> tuple[DrawGeometry, DrawGeometry]:
    main = DrawGeometry(vertices=np.zeros((6, 8), dtype=np.float32), indices=None)
    shadow = DrawGeometry(
        vertices=np.zeros((16, 8), dtype=np.float32),
        indices=np.zeros(48, dtype=np.uint16),
    )
    return main, shadow


def _fullscreen_geometry() -> DrawGeometry:
    vertices = np.zeros((4, 8), dtype=np.float32)
    vertices[:, :4] = np.asarray(
        (
            (0.0, 0.0, 0.0, 1.0),
            (float(WIDTH), 0.0, 0.0, 1.0),
            (float(WIDTH), float(HEIGHT), 0.0, 1.0),
            (0.0, float(HEIGHT), 0.0, 1.0),
        ),
        dtype=np.float32,
    )
    return DrawGeometry(
        vertices=vertices,
        indices=np.asarray((0, 1, 2, 2, 3, 0), dtype=np.uint16),
    )


def _configure_current_compositor(renderer: AppleGlassReferenceRenderer) -> None:
    for name, value in {
        "HighlightVibrantArithmeticMode": 10,
        "HighlightSourceConstructionMode": 1,
        "HighlightSourceDivisionMode": 0,
        "HighlightDestinationDivisionMode": 0,
        "UseAppleHighlightAlphaTrace": 1,
        "UseAppleHighlightSourceTrace": 0,
        "UseAppleHighlightGeometryTrace": 0,
    }.items():
        renderer.program[name].value = value


def run_gate(capture: Path, *, device_indices: list[int]) -> JsonObject:
    records = load_records(capture)
    fragment_source = load_amd_exact_circle_shader(
        "regular",
        ROOT / "analysis/apple_glass_reference.frag.glsl",
    )
    main, shadow = _dummy_geometry()
    final = _fullscreen_geometry()
    profile = build_static_profile(
        canonical_static_profile_request("regular", "dark")
    )

    runs: list[JsonObject] = []
    devices: list[JsonObject] = []
    for device_index in device_indices:
        device: JsonObject | None = None
        for record in records:
            role = str(record["role"])
            alpha = _object(record["alphaTrace"], name=f"{role} alpha")
            alpha_path = _raw_file(
                capture,
                alpha.get("output"),
                byte_count=WIDTH * HEIGHT * 8,
            )
            seed = _object(record["seed"], name=f"{role} seed")
            seed_path = _raw_file(
                capture,
                seed.get("output"),
                byte_count=BYTES_PER_FRAME,
            )
            seed_rgba = bgra_raw(seed_path, width=WIDTH, height=HEIGHT)
            with AppleGlassReferenceRenderer(
                capture,
                fragment_shader_source=fragment_source,
                source_mip_bgra_levels={0: (1, 1, bytes((0, 0, 0, 255)))},
                destination_bgra_data=seed_path.read_bytes(),
                highlight_half_stage_data=alpha_trace_words(alpha_path),
                main_geometry=main,
                shadow_geometry=shadow,
                final_highlight_geometry=final,
                profile_payload=profile,
                runtime_data={},
                load_interpolant_trace=False,
                load_interpolant_axis_trace=False,
                load_diagnostic_traces=False,
                context_arguments={"device_index": device_index},
            ) as renderer:
                _configure_current_compositor(renderer)
                if device is None:
                    implementation = renderer.implementation
                    device = {
                        "deviceIndex": device_index,
                        "vendor": implementation["glVendor"],
                        "renderer": implementation["glRenderer"],
                        "version": implementation["glVersion"],
                    }
                    devices.append(device)
                cases = _array(record["cases"], name=f"{role} cases")
                for case_value in cases:
                    case = _object(case_value, name=f"{role} case")
                    apple = _object(case["apple"], name=f"{role} Apple output")
                    reference_path = _raw_file(
                        capture,
                        apple.get("output"),
                        byte_count=BYTES_PER_FRAME,
                    )
                    reference = bgra_raw(
                        reference_path,
                        width=WIDTH,
                        height=HEIGHT,
                    )
                    candidate = renderer.render_final_highlight_over(
                        seed_rgba,
                        final_highlight_payload=current_uniform_payload(
                            _array(
                                case["matrixHalfWordsLittleEndian"],
                                name=f"{role}/{case['name']} matrix",
                            )
                        ),
                    )
                    comparison = compare_images(reference, candidate)
                    runs.append(
                        {
                            "deviceIndex": device_index,
                            "role": role,
                            "case": case["name"],
                            "checkedBytes": BYTES_PER_FRAME,
                            "comparison": comparison.as_json(),
                        }
                    )

    expected_runs = len(device_indices) * len(ROLES) * len(CASES)
    exact = len(runs) == expected_runs and all(
        run["comparison"]["exact"] is True for run in runs
    )
    return {
        "schemaVersion": 1,
        "scope": SCOPE,
        "capture": {
            "path": str(capture),
            "timelineSha256": TIMELINE_SHA256,
            "validationSha256": VALIDATION_SHA256,
            "appleConstructionUnknowns": 0,
        },
        "implementation": {
            "fragmentShaderPath": "analysis/apple_glass_reference.frag.glsl",
            "specializedFragmentShaderSha256": hashlib.sha256(
                fragment_source.encode()
            ).hexdigest(),
            "vibrantArithmeticMode": 10,
            "vibrantFMAAccumulationOrder": "g-r-b",
            "sourceConstructionMode": 1,
            "sourceDivisionMode": 0,
            "destinationDivisionMode": 0,
        },
        "devices": devices,
        "runs": runs,
        "totals": {
            "checkedBytes": sum(int(run["checkedBytes"]) for run in runs),
            "mismatchedBytes": sum(
                int(run["comparison"]["mismatchedBytes"]) for run in runs
            ),
            "mismatchedPixels": sum(
                int(run["comparison"]["mismatchedPixels"]) for run in runs
            ),
        },
        "gate": {
            "currentCompositorGlslPortExact": exact,
            "productionWalleParityEstablished": False,
        },
    }


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--capture", type=Path, default=DEFAULT_CAPTURE)
    parser.add_argument("--device-index", type=int, action="append")
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    if arguments.device_index is None:
        arguments.device_index = [0, 1]
    if len(set(arguments.device_index)) != len(arguments.device_index):
        parser.error("device indices must be unique")
    return arguments


def main() -> int:
    arguments = parse_arguments()
    report = run_gate(arguments.capture, device_indices=arguments.device_index)
    encoded = json.dumps(report, indent=2, sort_keys=True) + "\n"
    print(encoded, end="")
    if arguments.output is not None:
        arguments.output.write_text(encoded, encoding="utf-8")
    return 0 if report["gate"]["currentCompositorGlslPortExact"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
