#!/usr/bin/env python3
"""Build one constructor-owned dynamic fixture for the Walle C renderer.

The admitted natural Retina capture supplies the requested public state and
the final comparison image. Profile bytes, geometry, interpolation
coefficients, transparent destination, and the zero backdrop pyramid are
reconstructed. Captured buffers are checked as structural oracles but are
never copied into the renderer's input fixture.
"""

import argparse
import hashlib
import json
import struct
import subprocess
import zlib
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import numpy as np

import analyze_transition_geometry_corpus_local_macos_26_6_1 as geometry_model
import analyze_transition_uniform_profile_calibration as transition_profile
import analyze_walle_dynamic_background_scissor as background_scissor_model
import raster_tile_selector_model as raster_arithmetic
import validate_variable_blur_selected_region_origin as selected_region
from apple_glass_reference_renderer import bgra_raw
from liquid_glass_runtime_raster_coefficients import (
    SelectorTableOverride,
    axis_table,
    coefficient_table,
    determinant_selector_index,
    load_near_square_selector_calibration,
    load_square_selector_calibration,
    runtime_quad_from_vertices,
    selector_table_for_calibrated_quad,
    selector_table_for_square_quad,
    slopes_bits,
    visible_pixel_bounds,
)


type JsonObject = dict[str, Any]
type Vertex = tuple[float, float, float, float, float, float, float, float]

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CAPTURE = (
    ROOT / "artifacts" / "local-natural-walle-current-alpha-interpolant-02"
)
ADMITTED_TIMELINE_SHA256 = frozenset(
    {
        "71eec0992cecac4755ee706be8160c40daac3a00ccf015517ddac42972945e7c",
        "efaa2e4a2b8e9d1f87d429daef27411e76bf43cea8734f14cfaf65fbc3d1ca76",
        "22f13f4baa7984a5921b3bd989955336889eebebf73631c5fc1fed30db50bdca",
        "76310b754cf1eb1a15881e3d64a1aab75048e0fa57ce378cb891a7ec1efe9107",
        "52e4279fd374efc6a349cb3a5e69fcce0b60e538abc387cd6b75bee3866aa2d3",
        "c028e232c0eb06ade31f826578c7209ea2e19f69b65a65cdc723187bc34adc44",
    }
)
MAGIC = b"WALLELG3"
CONFIG_FORMAT = "<8s23I15iI"
WIDTH = 1024
HEIGHT = 1024
PIXEL_BYTES = WIDTH * HEIGHT * 4
HALF_INTRINSIC_TABLE = ROOT / "artifacts" / "gh-run-30721856837" / "half-intrinsics.bin"
HALF_INTRINSIC_TABLE_SHA256 = (
    "ca1e5cbcaa645cad27917b87990edba4e8e06931c8089fbb2fbf1ae96837bfc2"
)
SMALL_SQUARE_WIDTH_FIXED_LOWER = 114_688
SMALL_SQUARE_WIDTH_FIXED_UPPER = 147_456
SMALL_SQUARE_SELECTOR_TABLE = (
    ROOT / "lg-test/Analysis/raster_small_square_selectors_u32le.zlib"
)
SMALL_SQUARE_SELECTOR_COMPRESSED_SHA256 = (
    "4a701a9868484ec6580026b6328ac99ec38d14d1d4747cd2066964e46498989e"
)
SMALL_SQUARE_SELECTOR_RAW_SHA256 = (
    "9cb148ec4996e77243c397c97f01163ea0a08502239adc8aeecd3e8e64fe6d10"
)
SMALL_NEAR_SQUARE_HEIGHT_FIXED_DELTAS = (
    -256,
    -128,
    -64,
    -32,
    -16,
    -8,
    -4,
    -2,
    -1,
    1,
    2,
    4,
    8,
    16,
    32,
    64,
    128,
    256,
)
SMALL_NEAR_SQUARE_SELECTOR_TABLE = (
    ROOT / "lg-test/Analysis/raster_small_near_square_selectors_u32le.zlib"
)
SMALL_NEAR_SQUARE_SELECTOR_COMPRESSED_SHA256 = (
    "7d0f0743a894c47518139456d5e7d9d805526126f760650239babde35388bba6"
)
SMALL_NEAR_SQUARE_SELECTOR_RAW_SHA256 = (
    "424fd9e815520c1f6f77840a6b976bf41d2907aecb1d4c82d1ea43fbc152633f"
)
NATURAL_SHADOW_CASES = (
    ROOT / "lg-test/Analysis/raster_natural_shadow_selector_cases_u32le.bin"
)
NATURAL_SHADOW_CASES_SHA256 = (
    "94a4e83307b5b5ba0020fb7ff6f4838acde2f959a9d3a8a2d6bf250af1a6893d"
)
NATURAL_SHADOW_SELECTORS = (
    ROOT / "lg-test/Analysis/raster_natural_shadow_selectors_u32le.zlib"
)
NATURAL_SHADOW_SELECTORS_COMPRESSED_SHA256 = (
    "b063a9a84afb062a8f54e006dac387f0c65c09cfc003405d7fa69218969e922d"
)
NATURAL_SHADOW_SELECTORS_RAW_SHA256 = (
    "90edc4baf626f8a6b90aa3a874465f3a004d9c8a8cbeac282663d24161aa8ef8"
)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} is not an object")
    return value


def sequence(value: object, name: str) -> Sequence[Any]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        raise ValueError(f"{name} is not an array")
    return value


def float32(value: float) -> float:
    return struct.unpack("<f", struct.pack("<f", value))[0]


def float32_bits(value: float) -> int:
    return struct.unpack("<I", struct.pack("<f", value))[0]


def vertices_bytes(vertices: Sequence[Vertex]) -> bytes:
    return b"".join(struct.pack("<8f", *vertex) for vertex in vertices)


def active_vertex_bytes(raw: bytes, count: int) -> bytes:
    required = count * geometry_model.VERTEX_STRIDE
    if len(raw) < required:
        raise ValueError("captured vertex stream is truncated")
    active_bytes = 8 * sizeof_float()
    return b"".join(
        raw[
            index * geometry_model.VERTEX_STRIDE : index * geometry_model.VERTEX_STRIDE
            + active_bytes
        ]
        for index in range(count)
    )


def sizeof_float() -> int:
    return struct.calcsize("<f")


def compact_half_intrinsic_table(path: Path) -> bytes:
    raw = path.read_bytes()
    if sha256(raw) != HALF_INTRINSIC_TABLE_SHA256 or len(raw) != (1 << 20):
        raise ValueError("Apple half-intrinsic table identity differs")
    compact = bytearray(256 * 256 * 4)
    for index, values in enumerate(struct.iter_unpack("<8H", raw)):
        struct.pack_into("<I", compact, index * 4, values[6] | (values[7] << 16))
    return bytes(compact)


def write_file(
    directory: Path,
    name: str,
    data: bytes,
    files: dict[str, JsonObject],
    *,
    role: str,
) -> None:
    (directory / name).write_bytes(data)
    files[name] = {
        "byteCount": len(data),
        "role": role,
        "sha256": sha256(data),
    }


def source_coordinates(
    vertices: Sequence[Vertex],
    *,
    backdrop_scale: float,
    crop_origin: tuple[int, int],
    copy_offset: tuple[int, int],
    allocation_extent: tuple[int, int],
) -> list[Vertex]:
    result: list[Vertex] = []
    for vertex in vertices:
        result.append(
            (
                *vertex[:6],
                geometry_model.source_coordinate(
                    vertex[0],
                    backdrop_scale=backdrop_scale,
                    crop_origin=crop_origin[0],
                    copy_offset=copy_offset[0],
                    allocation_extent=allocation_extent[0],
                ),
                geometry_model.source_coordinate(
                    vertex[1],
                    backdrop_scale=backdrop_scale,
                    crop_origin=crop_origin[1],
                    copy_offset=copy_offset[1],
                    allocation_extent=allocation_extent[1],
                ),
            )
        )
    return result


def background_geometry(
    geometry: Mapping[str, Any],
    *,
    material: str,
    remaining: float,
    backdrop_scale: float,
    crop_origin: tuple[int, int],
    copy_offset: tuple[int, int],
    allocation_extent: tuple[int, int],
) -> tuple[list[Vertex], list[Vertex]]:
    state = geometry_model.expected_dynamic_layer_state(geometry, remaining)
    carrier = state["carrierPosition"]
    element_position = state["elementPosition"]
    element_bounds = state["elementBounds"]
    extent = element_bounds[2]
    window_height = float(geometry["windowHeight"])
    left = float32(carrier[0] + element_position[0])
    right = float32((carrier[0] + element_position[0]) + extent)
    top = float32((window_height - carrier[1]) - element_position[1])
    bottom = float32(((window_height - carrier[1]) - element_position[1]) - extent)
    local_minimum = float32(-extent / 2.0)
    local_maximum = float32(extent / 2.0)
    main_without_source: list[Vertex] = [
        (left, top, 0.0, 1.0, local_minimum, local_minimum, 0.0, 0.0),
        (right, top, 0.0, 1.0, local_maximum, local_minimum, 0.0, 0.0),
        (right, bottom, 0.0, 1.0, local_maximum, local_maximum, 0.0, 0.0),
        (right, bottom, 0.0, 1.0, local_maximum, local_maximum, 0.0, 0.0),
        (left, bottom, 0.0, 1.0, local_minimum, local_maximum, 0.0, 0.0),
        (left, top, 0.0, 1.0, local_minimum, local_minimum, 0.0, 0.0),
    ]
    shadow_without_source = geometry_model.shadow_vertices_from_layer_geometry(
        root_bounds=(0.0, 0.0, float(geometry["windowWidth"]), window_height),
        carrier_position=carrier,
        element_position=element_position,
        element_bounds=element_bounds,
        material=material,
        remaining=remaining,
    )
    return (
        source_coordinates(
            main_without_source,
            backdrop_scale=backdrop_scale,
            crop_origin=crop_origin,
            copy_offset=copy_offset,
            allocation_extent=allocation_extent,
        ),
        source_coordinates(
            shadow_without_source,
            backdrop_scale=backdrop_scale,
            crop_origin=crop_origin,
            copy_offset=copy_offset,
            allocation_extent=allocation_extent,
        ),
    )


def load_natural_shadow_selector_calibration() -> dict[tuple[int, int], int]:
    case_payload = NATURAL_SHADOW_CASES.read_bytes()
    compressed = NATURAL_SHADOW_SELECTORS.read_bytes()
    selector_payload = zlib.decompress(compressed)
    if (
        sha256(case_payload) != NATURAL_SHADOW_CASES_SHA256
        or len(case_payload) % 8
        or sha256(compressed) != NATURAL_SHADOW_SELECTORS_COMPRESSED_SHA256
        or sha256(selector_payload) != NATURAL_SHADOW_SELECTORS_RAW_SHA256
        or len(selector_payload) * 2 != len(case_payload)
    ):
        raise ValueError("frozen natural shadow selector calibration differs")
    cases = struct.iter_unpack("<II", case_payload)
    selectors = struct.iter_unpack("<I", selector_payload)
    result = {
        (width_fixed, height_fixed): selector
        for (width_fixed, height_fixed), (selector,) in zip(
            cases,
            selectors,
            strict=True,
        )
    }
    if len(result) * 8 != len(case_payload):
        raise ValueError("natural shadow selector cases are not unique")
    return result


def shadow_interpolant_tables(
    vertices: Sequence[Vertex],
    *,
    natural_selector_calibration: Mapping[tuple[int, int], int],
) -> tuple[np.ndarray, np.ndarray, int]:
    """Build independent AGX coefficient/slopes tables for all eight ring quads."""
    vertex_array = np.asarray(vertices, dtype=np.float32)
    indices = np.asarray(geometry_model.SHADOW_INDICES, dtype=np.uint16).reshape(-1, 3)
    if vertex_array.shape != (16, 8) or indices.shape != (16, 3):
        raise ValueError("shadow interpolation geometry differs")
    selector_table = raster_arithmetic.load_selector_table()
    coefficients = np.empty((16, 32, 4), dtype=np.uint32)
    slopes = np.empty((8, 1, 4), dtype=np.uint32)
    active_quad_count = 0
    for quad_index in range(8):
        triangle_indices = indices[2 * quad_index : 2 * quad_index + 2]
        expanded = vertex_array[triangle_indices.reshape(-1)]
        try:
            quad = runtime_quad_from_vertices(
                expanded,
                name=f"shadow-ring-{quad_index}",
            )
        except ValueError as error:
            if "raster extent is empty" not in str(error):
                raise
            # A dematerializing ring can collapse a strip to zero raster area.
            # Its primitives produce no fragments, but retain deterministic rows
            # so gl_PrimitiveID continues to address every later quad directly.
            coefficients[2 * quad_index : 2 * quad_index + 2] = 0
            slopes[quad_index, 0] = 0
            continue
        active_quad_count += 1
        pair = (quad.case.widthFixed, quad.case.heightFixed)
        try:
            measured_selector = natural_selector_calibration[pair]
        except KeyError as error:
            raise ValueError(
                f"shadow ring {quad_index} is outside the frozen natural "
                f"selector domain: {pair}"
            ) from error
        selector_index, _ = determinant_selector_index(
            quad.case,
            selector_table_length=len(selector_table),
        )
        calibrated_selector_table = SelectorTableOverride(
            selector_table,
            selector_index,
            measured_selector,
        )
        _, table = coefficient_table(
            quad,
            tile_start=0,
            tile_count=32,
            selector_table=calibrated_selector_table,
        )
        coefficients[2 * quad_index : 2 * quad_index + 2] = table
        slopes[quad_index, 0] = np.asarray(
            slopes_bits(quad, calibrated_selector_table),
            dtype=np.uint32,
        )
    return coefficients, slopes, active_quad_count


def highlight_interpolant_axis_table(
    vertices: Sequence[Vertex],
    indices: Sequence[int],
    *,
    base_selector_table: Sequence[int],
    square_selector_calibration: Sequence[int],
    near_square_selector_calibration: Sequence[int],
    border_selector_offset: int = 0,
) -> tuple[np.ndarray, int, int, bool]:
    """Build the exact separable iterator for the retained highlight geometry."""
    vertex_array = np.asarray(vertices, dtype=np.float32)
    index_array = np.asarray(indices, dtype=np.uint16)
    if index_array.size == 0 or index_array.size % 3 != 0:
        raise ValueError("final-highlight indices are incomplete")
    if int(index_array.max()) >= len(vertex_array):
        raise ValueError("final-highlight index lies outside its vertex stream")
    triangles = vertex_array[index_array].reshape(-1, 3, 8)[..., :2]
    left = triangles[:, 1] - triangles[:, 0]
    right = triangles[:, 2] - triangles[:, 0]
    signed_areas = left[:, 0] * right[:, 1] - left[:, 1] * right[:, 0]
    if np.any(signed_areas == 0.0):
        raise ValueError("final-highlight geometry contains a degenerate triangle")
    back_facing = bool(np.all(signed_areas < 0.0))
    if not back_facing and not bool(np.all(signed_areas > 0.0)):
        raise ValueError("final-highlight geometry mixes winding directions")
    if back_facing and index_array.size == 6:
        full_axis = np.zeros((2, WIDTH, 4), dtype=np.uint32)
        return full_axis, 0, 0, True
    if index_array.size == 6:
        full_axis = np.zeros((2, WIDTH, 4), dtype=np.uint32)
        expanded = vertex_array[index_array].copy()
        expanded[:, 6:8] = expanded[:, 4:6]
        quad = runtime_quad_from_vertices(expanded, name="final-highlight")
        selector_table = selector_table_for_calibrated_quad(
            quad,
            base_selector_table,
            square_selector_calibration,
            near_square_selector_calibration,
            width_fixed_lower=SMALL_SQUARE_WIDTH_FIXED_LOWER,
            height_fixed_deltas=SMALL_NEAR_SQUARE_HEIGHT_FIXED_DELTAS,
        )
        axis_start, axis = axis_table(quad, selector_table=selector_table)
        axis_end = axis_start + axis.shape[1]
        if not 0 <= axis_start < axis_end <= WIDTH:
            raise ValueError("final-highlight interpolant axis is outside the target")
        full_axis[:, axis_start:axis_end] = axis
        return full_axis, axis_start, axis_end, False
    if index_array.size != 24:
        raise ValueError(
            "final-highlight geometry is not one quad or four border quads"
        )

    full_axis = np.zeros((8, WIDTH, 4), dtype=np.uint32)
    axis_lower = WIDTH
    axis_upper = 0
    for quad_index in range(4):
        quad_indices = index_array[quad_index * 6 : (quad_index + 1) * 6]
        expanded = vertex_array[quad_indices].copy()
        expanded[:, 6:8] = expanded[:, 4:6]
        quad = runtime_quad_from_vertices(
            expanded,
            name=f"final-highlight-border-{quad_index}",
        )
        selector_index, _ = determinant_selector_index(
            quad.case,
            selector_table_length=len(base_selector_table),
        )
        selector_value = base_selector_table[selector_index] + border_selector_offset
        if not 0 <= selector_value <= 0xFFFF_FFFF:
            raise ValueError("border-highlight selector offset overflows uint32")
        selector_table = SelectorTableOverride(
            base_selector_table,
            selector_index,
            selector_value,
        )
        axis_start, axis = axis_table(
            quad,
            selector_table=selector_table,
            anchor_high_by_primitive_axis=((False, False), (False, False)),
        )
        axis_end = axis_start + axis.shape[1]
        if not 0 <= axis_start < axis_end <= WIDTH:
            raise ValueError("border-highlight interpolant axis is outside the target")
        left, bottom, right, top = visible_pixel_bounds(quad.case)
        for component, lower, upper in ((0, left, right), (1, bottom, top)):
            if not 0 <= lower < upper <= WIDTH:
                raise ValueError("border-highlight visible axis is outside the target")
            source = axis[:, lower - axis_start : upper - axis_start, component]
            row_start = quad_index * 2
            full_axis[row_start : row_start + 2, lower:upper, component] = source
            axis_lower = min(axis_lower, lower)
            axis_upper = max(axis_upper, upper)
    if axis_lower >= axis_upper:
        raise ValueError("border-highlight interpolant axis is empty")
    return full_axis, axis_lower, axis_upper, back_facing


def emitted_profile(
    emitter: Path,
    *,
    material: str,
    appearance: str,
    diameter: int,
    remaining: float,
    half_extent: float,
    source_step_x: float,
    source_step_y: float,
) -> bytes:
    command = (
        str(emitter),
        "0" if material == "clear" else "1",
        "0" if appearance == "light" else "1",
        str(diameter),
        f"{float32_bits(remaining):08x}",
        f"{float32_bits(half_extent):08x}",
        f"{float32_bits(half_extent):08x}",
        f"{float32_bits(source_step_x):08x}",
        f"{float32_bits(source_step_y):08x}",
    )
    completed = subprocess.run(command, check=True, capture_output=True, text=True)
    result = bytes.fromhex(completed.stdout.strip())
    if len(result) != 258:
        raise ValueError("transition profile emitter returned an invalid payload")
    return result


def captured_background_profile(record: Mapping[str, Any]) -> bytes:
    render = mapping(record.get("render"), "dynamic render")
    snapshots = mapping(render.get("metalBufferSnapshots"), "Metal buffer snapshots")
    candidates = [
        mapping(value, "Metal buffer snapshot")
        for value in sequence(snapshots.get("snapshots"), "Metal snapshots")
        if isinstance(value, Mapping)
        and value.get("stage") == "fragment"
        and value.get("index") == 1
        and str(
            mapping(value.get("pipeline"), "pipeline")
            .get("creationDescriptor", {})
            .get("fragmentFunction", "")
        ).startswith("glass_background")
    ]
    if len(candidates) != 2:
        raise ValueError("expected main and shadow background profiles")
    raw = geometry_model.payload(candidates[0])
    if len(raw) < 258:
        raise ValueError("captured main profile is truncated")
    return raw[:258]


def captured_background_scissor(
    record: Mapping[str, Any],
) -> tuple[int, int, int, int]:
    render = mapping(record.get("render"), "dynamic render")
    probe = mapping(render.get("metalUniformProbe"), "Metal uniform probe")
    candidates = [
        mapping(value, "Metal command record")
        for value in sequence(probe.get("records"), "Metal command records")
        if isinstance(value, Mapping)
        and value.get("kind") == "scissorRect"
        and str(
            mapping(value.get("pipeline"), "scissor pipeline")
            .get("creationDescriptor", {})
            .get("fragmentFunction", "")
        ).startswith("glass_background")
    ]
    if len(candidates) != 1:
        raise ValueError("expected exactly one dynamic background scissor")
    candidate = candidates[0]
    x = int(candidate["x"])
    metal_y = int(candidate["y"])
    width = int(candidate["width"])
    height = int(candidate["height"])
    y = HEIGHT - metal_y - height
    if (
        x < 0
        or y < 0
        or width <= 0
        or height <= 0
        or x + width > WIDTH
        or y + height > HEIGHT
    ):
        raise ValueError("dynamic background scissor is out of bounds")
    return x, y, width, height


def load_render_inputs(
    capture: Path,
    record: Mapping[str, Any],
    *,
    require_zero_source: bool,
) -> tuple[int, int, list[bytes]]:
    render = mapping(record.get("render"), "dynamic render")
    pre_final = mapping(
        mapping(render.get("exactPassReplay"), "exact replay").get("preFinalPass"),
        "pre-final pass",
    )
    pre_final_name = pre_final.get("rawFile")
    if not isinstance(pre_final_name, str):
        raise ValueError("pre-final pass raw file is absent")
    destination = (capture / pre_final_name).read_bytes()
    if len(destination) != PIXEL_BYTES or any(destination):
        raise ValueError("captured dynamic destination is not transparent zero")

    textures = mapping(render.get("metalTextureSnapshots"), "Metal texture snapshots")
    sources = [
        mapping(value, "source texture")
        for value in sequence(textures.get("snapshots"), "texture snapshots")
        if isinstance(value, Mapping)
        and value.get("index") == 3
        and str(
            mapping(value.get("pipeline"), "source pipeline")
            .get("creationDescriptor", {})
            .get("fragmentFunction", "")
        ).startswith("glass_background")
    ]
    if len(sources) != 1:
        raise ValueError("expected exactly one dynamic backdrop pyramid")
    source = sources[0]
    width = int(source["width"])
    height = int(source["height"])
    levels = sequence(source.get("mipSnapshots"), "source mip snapshots")
    if [int(mapping(level, "source mip")["level"]) for level in levels] != list(
        range(len(levels))
    ):
        raise ValueError("dynamic backdrop mip sequence is incomplete")
    payloads: list[bytes] = []
    for level_value in levels:
        level = mapping(level_value, "source mip")
        name = level.get("rawFile")
        if not isinstance(name, str):
            raise ValueError("dynamic backdrop mip raw file is absent")
        raw = (capture / name).read_bytes()
        expected = int(level["width"]) * int(level["height"]) * 4
        if len(raw) != expected:
            raise ValueError("captured dynamic backdrop mip byte count differs")
        if require_zero_source and any(raw):
            raise ValueError("captured dynamic backdrop mip is not zero")
        payloads.append(raw)
    return width, height, payloads


def generate_fixture(
    capture: Path,
    output: Path,
    *,
    profile_emitter: Path,
    sample_index: int = 24,
    highlight_only: bool = False,
    captured_render_inputs: bool = False,
    border_selector_offset: int = 0,
    expected_timeline_sha256: str | None = None,
) -> JsonObject:
    timeline_path = capture / "transition-timeline.json"
    timeline_sha256 = sha256_file(timeline_path)
    prospectively_admitted = (
        expected_timeline_sha256 is not None
        and timeline_sha256 == expected_timeline_sha256
        and timeline_sha256 not in ADMITTED_TIMELINE_SHA256
    )
    if timeline_sha256 not in ADMITTED_TIMELINE_SHA256 and not prospectively_admitted:
        raise ValueError("dynamic timeline is not the admitted natural capture")
    timeline = mapping(
        json.loads(timeline_path.read_text(encoding="utf-8")),
        "transition timeline",
    )
    if not captured_render_inputs and (
        timeline.get("material") != "regular"
        or timeline.get("appearance") != "dark"
        or timeline.get("direction") != "dematerialize"
    ):
        raise ValueError("dynamic fixture profile differs")
    geometry = mapping(timeline.get("geometry"), "timeline geometry")
    records = sequence(
        mapping(timeline.get("dynamicBackgroundUniforms"), "dynamic uniforms").get(
            "records"
        ),
        "dynamic records",
    )
    sample_indices = [
        int(mapping(value, "dynamic record")["sampleIndex"]) for value in records
    ]
    if not captured_render_inputs and sample_indices != [1, 4, 8, 12, 16, 20, 24, 28]:
        raise ValueError("natural dynamic sample inventory differs")
    matching_records = [
        mapping(value, "dynamic record")
        for value in records
        if mapping(value, "dynamic record").get("sampleIndex") == sample_index
    ]
    if len(matching_records) != 1:
        raise ValueError(f"natural dynamic sample {sample_index} is not unique")
    record = matching_records[0]
    if "finalHighlightVertexTailGeometryTransport" in record:
        raise ValueError("dynamic fixture contains transported geometry")
    remaining = float(record["remaining"])
    if remaining != float32(remaining):
        raise ValueError("dynamic remaining value is not binary32")

    material = str(timeline["material"])
    appearance = str(timeline["appearance"])
    diameter = int(geometry["width"])
    layer = geometry_model.expected_dynamic_layer_state(geometry, remaining)
    element_extent = float(layer["elementBounds"][2])
    backdrop_scale = geometry_model.expected_backdrop_scale(material, remaining)
    producer = geometry_model.expected_producer_crop(
        geometry,
        material=material,
        carrier_position=layer["carrierPosition"],
        backdrop_scale=backdrop_scale,
    )
    numeric = transition_profile.predict_numeric_fields(
        material=material,
        appearance=appearance,
        diameter=diameter,
        fraction=remaining,
    )
    radius1 = selected_region.predict_radius1(
        blur_radius=numeric["inputBlurRadius"],
        bleed_blur_radius=numeric["inputBleedBlurRadius"],
        backdrop_scale=backdrop_scale,
    )
    mip_policy = selected_region.predict_mip_policy(
        radius1=radius1,
        source_extent=producer["activeExtent"],
    )
    helper_bounds = selected_region.predict_integer_bounds(
        bounds=[*producer["cropOrigin"], *producer["activeExtent"]],
        radius1=radius1,
        alignment_scale=int(mip_policy["alignmentScale"]),
    )
    allocation_extent = tuple(
        selected_region.align_up(value) for value in helper_bounds[2:]
    )
    copy_offset = tuple(
        helper_bounds[axis] - producer["cropOrigin"][axis] for axis in range(2)
    )
    crop_origin = tuple(int(value) for value in producer["cropOrigin"])
    allocation = (int(allocation_extent[0]), int(allocation_extent[1]))
    copy = (int(copy_offset[0]), int(copy_offset[1]))

    main, shadow = background_geometry(
        geometry,
        material=material,
        remaining=remaining,
        backdrop_scale=backdrop_scale,
        crop_origin=crop_origin,
        copy_offset=copy,
        allocation_extent=allocation,
    )
    highlight = geometry_model.expected_final_highlight(
        geometry,
        material=material,
        appearance=appearance,
        remaining=remaining,
    )
    highlight_vertices = [tuple(vertex) for vertex in highlight["vertices"]]
    highlight_indices = tuple(int(index) for index in highlight["indices"])

    observed_main, observed_shadow = geometry_model.background_geometry(record)
    if vertices_bytes(observed_main) != vertices_bytes(main):
        raise ValueError("independent dynamic main geometry differs")
    if vertices_bytes(observed_shadow) != vertices_bytes(shadow):
        raise ValueError("independent dynamic shadow geometry differs")
    if not captured_render_inputs:
        observed_final = geometry_model.final_highlight_inventory(record)
        if (
            active_vertex_bytes(
                bytes(observed_final["vertices"]), int(observed_final["vertexCount"])
            )
            != vertices_bytes(highlight_vertices)
            or bytes(observed_final["indices"])
            != struct.pack(f"<{len(highlight_indices)}H", *highlight_indices)
            or bytes(observed_final["fragmentPrefix"])
            != bytes(highlight["fragmentPrefix"])
        ):
            raise ValueError("independent dynamic final-highlight construction differs")

    if highlight_only:
        source_width, source_height = allocation
        source_level_count = int(mip_policy["levelCount"])
        source_payloads = [
            bytes(max(1, source_width >> level) * max(1, source_height >> level) * 4)
            for level in range(source_level_count)
        ]
    else:
        source_width, source_height, source_payloads = load_render_inputs(
            capture,
            record,
            require_zero_source=not captured_render_inputs,
        )
        source_level_count = len(source_payloads)
    observed_background_scissor = captured_background_scissor(record)
    if captured_render_inputs:
        background_scissor = observed_background_scissor
    else:
        predicted_scissor_state = background_scissor_model.predict_scissor_state(
            geometry, remaining
        )
        background_scissor = tuple(
            int(value) for value in predicted_scissor_state["glBottomLeftScissor"]
        )
    if len(background_scissor) != 4:
        raise ValueError("constructed background scissor is malformed")
    if observed_background_scissor != background_scissor:
        raise ValueError("independent background scissor differs")
    if (source_width, source_height) != allocation or source_level_count != int(
        mip_policy["levelCount"]
    ):
        raise ValueError("constructed source layout differs from the retained layout")
    half_extent = float32(element_extent / 2.0)
    source_step_x = float32(backdrop_scale / source_width)
    source_step_y = float32(backdrop_scale / source_height)
    constructed_profile = emitted_profile(
        profile_emitter,
        material=material,
        appearance=appearance,
        diameter=diameter,
        remaining=remaining,
        half_extent=half_extent,
        source_step_x=source_step_x,
        source_step_y=source_step_y,
    )
    captured_profile = captured_background_profile(record)
    if not captured_render_inputs and constructed_profile != captured_profile:
        raise ValueError("C-constructed dynamic background profile differs")
    profile = captured_profile if captured_render_inputs else constructed_profile

    if captured_render_inputs:
        tile_start = 0
        coefficients = np.zeros((2, 1, 4), dtype=np.uint32)
        slopes = (0, 0, 0, 0)
        full_interpolant_axis = np.zeros((2, WIDTH, 4), dtype=np.uint32)
        interpolant_axis_start = 0
        interpolant_axis_end = WIDTH
        shadow_coefficients = np.zeros((16, 32, 4), dtype=np.uint32)
        shadow_slopes = np.zeros((8, 1, 4), dtype=np.uint32)
        active_shadow_quad_count = 0
        highlight_rows = 8 if len(highlight_indices) == 24 else 2
        highlight_interpolant_axis = np.zeros(
            (highlight_rows, WIDTH, 4), dtype=np.uint32
        )
        highlight_interpolant_axis_start = 0
        highlight_interpolant_axis_end = WIDTH
        highlight_back_facing = False
    else:
        main_array = np.asarray(main, dtype=np.float32)
        base_selector_table = raster_arithmetic.load_selector_table()
        natural_shadow_selector_calibration = load_natural_shadow_selector_calibration()
        square_selector_calibration = load_square_selector_calibration(
            SMALL_SQUARE_SELECTOR_TABLE,
            width_fixed_lower=SMALL_SQUARE_WIDTH_FIXED_LOWER,
            width_fixed_upper=SMALL_SQUARE_WIDTH_FIXED_UPPER,
            expected_compressed_sha256=SMALL_SQUARE_SELECTOR_COMPRESSED_SHA256,
            expected_raw_sha256=SMALL_SQUARE_SELECTOR_RAW_SHA256,
        )
        near_square_selector_calibration = load_near_square_selector_calibration(
            SMALL_NEAR_SQUARE_SELECTOR_TABLE,
            width_fixed_lower=SMALL_SQUARE_WIDTH_FIXED_LOWER,
            width_fixed_upper=SMALL_SQUARE_WIDTH_FIXED_UPPER,
            height_fixed_deltas=SMALL_NEAR_SQUARE_HEIGHT_FIXED_DELTAS,
            expected_compressed_sha256=(SMALL_NEAR_SQUARE_SELECTOR_COMPRESSED_SHA256),
            expected_raw_sha256=SMALL_NEAR_SQUARE_SELECTOR_RAW_SHA256,
        )
        quad = runtime_quad_from_vertices(
            main_array, name=f"{material}-{appearance}-dynamic-{sample_index:02d}"
        )
        selector_table = selector_table_for_square_quad(
            quad,
            base_selector_table,
            square_selector_calibration,
            width_fixed_lower=SMALL_SQUARE_WIDTH_FIXED_LOWER,
        )
        tile_start, coefficients = coefficient_table(
            quad,
            selector_table=selector_table,
        )
        slopes = slopes_bits(quad, selector_table)
        interpolant_axis_start, interpolant_axis = axis_table(
            quad,
            selector_table=selector_table,
            helper_lane_halo=1,
        )
        full_interpolant_axis = np.zeros((2, WIDTH, 4), dtype=np.uint32)
        interpolant_axis_end = interpolant_axis_start + interpolant_axis.shape[1]
        if not 0 <= interpolant_axis_start < interpolant_axis_end <= WIDTH:
            raise ValueError("main interpolant axis lies outside the render target")
        full_interpolant_axis[:, interpolant_axis_start:interpolant_axis_end] = (
            interpolant_axis
        )
        shadow_coefficients, shadow_slopes, active_shadow_quad_count = (
            shadow_interpolant_tables(
                shadow,
                natural_selector_calibration=natural_shadow_selector_calibration,
            )
        )
        (
            highlight_interpolant_axis,
            highlight_interpolant_axis_start,
            highlight_interpolant_axis_end,
            highlight_back_facing,
        ) = highlight_interpolant_axis_table(
            highlight_vertices,
            highlight_indices,
            base_selector_table=base_selector_table,
            square_selector_calibration=square_selector_calibration,
            near_square_selector_calibration=near_square_selector_calibration,
            border_selector_offset=border_selector_offset,
        )

    output.mkdir(parents=True, exist_ok=True)
    files: dict[str, JsonObject] = {}
    config = struct.pack(
        CONFIG_FORMAT,
        MAGIC,
        WIDTH,
        HEIGHT,
        0 if material == "clear" else 1,
        0 if appearance == "light" else 1,
        source_level_count,
        tile_start,
        coefficients.shape[1],
        *slopes,
        source_width,
        source_height,
        len(main),
        len(shadow),
        len(geometry_model.SHADOW_INDICES),
        len(highlight_vertices),
        len(highlight_indices),
        10,
        *background_scissor,
        1,
        1,
        0,
        1,
        1,
        0,
        0,
        1,
        0,
        0,
        0,
        0,
        0,
        1,
        0,
        0,
    )
    write_file(output, "config.bin", config, files, role="independent-input")
    write_file(
        output,
        "main-vertices.f32",
        vertices_bytes(main),
        files,
        role="independent-input",
    )
    write_file(
        output,
        "shadow-vertices.f32",
        vertices_bytes(shadow),
        files,
        role="independent-input",
    )
    write_file(
        output,
        "shadow-indices.u16",
        struct.pack(
            f"<{len(geometry_model.SHADOW_INDICES)}H",
            *geometry_model.SHADOW_INDICES,
        ),
        files,
        role="independent-input",
    )
    write_file(
        output,
        "highlight-vertices.f32",
        vertices_bytes(highlight_vertices),
        files,
        role="independent-input",
    )
    write_file(
        output,
        "highlight-indices.u16",
        struct.pack(f"<{len(highlight_indices)}H", *highlight_indices),
        files,
        role="independent-input",
    )
    write_file(
        output,
        "profile.bin",
        profile,
        files,
        role=(
            "captured-apple-hardware-input"
            if captured_render_inputs
            else "independent-input"
        ),
    )
    write_file(
        output,
        "highlight-uniform.bin",
        bytes(highlight["fragmentPrefix"]),
        files,
        role="independent-input",
    )
    write_file(
        output,
        "interpolant-coefficients.rgba32ui",
        coefficients.astype("<u4", copy=False).tobytes(),
        files,
        role="independent-input",
    )
    if captured_render_inputs:
        interpolant_trace_name = (
            f"transition-background-uniform-{sample_index:02d}-glass-dynamic-main-"
            "interpolant-numeric-trace-rgba32ui.raw"
        )
        write_file(
            output,
            "interpolant-trace.rgba32ui",
            (capture / interpolant_trace_name).read_bytes(),
            files,
            role="captured-apple-hardware-diagnostic-input",
        )
    write_file(
        output,
        "interpolant-axis.rgba32ui",
        full_interpolant_axis.astype("<u4", copy=False).tobytes(),
        files,
        role="independent-input",
    )
    write_file(
        output,
        "highlight-interpolant-axis.rgba32ui",
        highlight_interpolant_axis.astype("<u4", copy=False).tobytes(),
        files,
        role="independent-input",
    )
    write_file(
        output,
        "shadow-interpolant-coefficients.rgba32ui",
        shadow_coefficients.astype("<u4", copy=False).tobytes(),
        files,
        role="independent-input",
    )
    write_file(
        output,
        "shadow-interpolant-slopes.rgba32ui",
        shadow_slopes.astype("<u4", copy=False).tobytes(),
        files,
        role="independent-input",
    )
    write_file(
        output,
        "half-intrinsics.r32ui",
        compact_half_intrinsic_table(HALF_INTRINSIC_TABLE),
        files,
        role="measured-apple-hardware-intrinsic-lookup",
    )
    write_file(
        output,
        "destination.rgba8",
        bytes(PIXEL_BYTES),
        files,
        role="independent-input",
    )
    for level, source_payload in enumerate(source_payloads):
        write_file(
            output,
            f"source-mip-{level}.rgba8",
            source_payload,
            files,
            role=(
                "captured-apple-hardware-input"
                if captured_render_inputs
                else "independent-input"
            ),
        )

    if highlight_only:
        reference_bottom_left = np.zeros((HEIGHT, WIDTH, 4), dtype=np.uint8)
        reference_role = "unused-highlight-only-placeholder"
    else:
        render = mapping(record.get("render"), "dynamic render")
        reference_metadata = mapping(render.get("output"), "dynamic output")
        reference_name = reference_metadata.get("rawFile")
        if not isinstance(reference_name, str):
            raise ValueError("dynamic final comparison image is absent")
        reference_top_left = bgra_raw(
            capture / reference_name,
            width=WIDTH,
            height=HEIGHT,
        )
        reference_bottom_left = np.ascontiguousarray(np.flipud(reference_top_left))
        reference_role = "captured-comparison-oracle-only"
    write_file(
        output,
        "reference-bottom-left.rgba8",
        reference_bottom_left.tobytes(),
        files,
        role=reference_role,
    )

    manifest: JsonObject = {
        "schemaVersion": 2,
        "name": (
            f"{material}-{appearance}-{timeline['direction']}-sample-{sample_index:02d}"
        ),
        "captureTimelineSha256": timeline_sha256,
        "material": material,
        "appearance": appearance,
        "direction": timeline["direction"],
        "sampleIndex": record["sampleIndex"],
        "remainingFloat32Bits": f"0x{float32_bits(remaining):08x}",
        "captureAdmission": (
            "prospective-explicit-timeline-sha256"
            if prospectively_admitted
            else "opened-calibration-corpus"
        ),
        "renderInputsCaptured": not highlight_only,
        "capturedRenderInputFields": [],
        "capturedFinalOutputUsedForComparisonOnly": not highlight_only,
        "highlightOnlyFixture": highlight_only,
        "construction": {
            "dynamicLayerStateExact": True,
            "backgroundGeometryExact": True,
            "backgroundProfileExact": True,
            "finalHighlightExact": True,
            "sourcePyramid": (
                "captured-controlled-apple-hardware-input"
                if captured_render_inputs
                else "independently-generated-transparent-zero"
            ),
            "destination": "independently-generated-transparent-zero",
            "sourceExtent": [source_width, source_height],
            "sourceMipCount": source_level_count,
            "vibrantArithmeticMode": 10,
            "highlightArithmeticModes": {
                "derivative": 1,
                "coordinate": 1,
                "alphaUlpBias": 0,
                "floatDivision": 1,
                "coverage": 1,
                "mix": 0,
                "band": 0,
                "normalize": 1,
                "normalizedCoordinate": 0,
                "sdfArithmetic": 0,
                "sdfSquaredUlpBias": 0,
                "sdfDistanceUlpBias": 0,
                "sourceDivision": 0,
                "sourceConstruction": 1,
                "destinationDivision": 0,
                "useAppleHalfIntrinsicTable": False,
            },
            "backgroundScissor": {
                "source": "independent-public-state-constructor",
                "glBottomLeft": list(background_scissor),
                "capturedStructuralOracleExact": True,
            },
            "shadowInterpolantModel": {
                "source": "independent-AGX-raster-coefficient-constructor",
                "quadCount": 8,
                "activeQuadCount": active_shadow_quad_count,
                "primitiveCount": 16,
                "tileCount": 32,
                "capturedCoordinateOrCoefficientTableUsed": False,
            },
            "mainInterpolantModel": {
                "source": "independent-AGX-raster-axis-constructor",
                "selector": "authenticated-small-square-finite-calibration",
                "axisStart": interpolant_axis_start,
                "axisEnd": interpolant_axis_end,
                "capturedCoordinateOrCoefficientTableUsed": False,
            },
            "highlightInterpolantModel": {
                "source": "independent-AGX-raster-axis-constructor",
                "selector": (
                    "authenticated-small-square-and-near-square-finite-calibration"
                    if len(highlight_indices) == 6
                    else "authenticated-fractional-selector-table"
                ),
                "borderSelectorOffset": border_selector_offset,
                "axisStart": highlight_interpolant_axis_start,
                "axisEnd": highlight_interpolant_axis_end,
                "backFacing": highlight_back_facing,
                "capturedCoordinateOrCoefficientTableUsed": False,
            },
        },
        "files": files,
    }
    (output / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--capture", type=Path, default=DEFAULT_CAPTURE)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--profile-emitter", type=Path, required=True)
    parser.add_argument("--sample-index", type=int, default=24)
    parser.add_argument("--highlight-only", action="store_true")
    parser.add_argument("--captured-render-inputs", action="store_true")
    parser.add_argument("--border-selector-offset", type=int, default=0)
    parser.add_argument("--expected-timeline-sha256")
    return parser.parse_args()


def main() -> int:
    arguments = parse_arguments()
    manifest = generate_fixture(
        arguments.capture,
        arguments.output,
        profile_emitter=arguments.profile_emitter,
        sample_index=arguments.sample_index,
        highlight_only=arguments.highlight_only,
        captured_render_inputs=arguments.captured_render_inputs,
        border_selector_offset=arguments.border_selector_offset,
        expected_timeline_sha256=arguments.expected_timeline_sha256,
    )
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
