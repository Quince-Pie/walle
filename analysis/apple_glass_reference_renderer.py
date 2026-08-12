#!/usr/bin/env python3
"""Replay the captured Apple Liquid Glass pass through experimental GLSL."""

import argparse
import hashlib
import json
import platform
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import moderngl
import numpy as np
from numpy.typing import NDArray
from PIL import Image

from liquid_glass_profile_matrix import decode_profile
from liquid_glass_raster_interpolant import (
    compress_axis_trace,
    load_axis_trace_table,
    load_live_coefficient_table,
    load_live_correction_surface,
    load_live_interpolant_trace,
)


type JsonObject = dict[str, Any]
type CodeImage = NDArray[np.uint8]
type UInt16Image = NDArray[np.uint16]
type UInt32Image = NDArray[np.uint32]

GL_RGBA8 = 0x8058
GL_RGBA16F = 0x881A
GL_RGBA8I = 0x8D8E
GL_RGBA32UI = 0x8D70
GL_R8UI = 0x8232
GL_R32UI = 0x8236
CAPTURE_WIDTH = 1024
CAPTURE_HEIGHT = 1024
ACTIVE_START = 112
ACTIVE_SIZE = 800


@dataclass(frozen=True, slots=True)
class Comparison:
    mismatched_bytes: int
    mismatched_pixels: int
    maximum_channel_delta: int
    mean_absolute_channel_delta: float
    exact: bool

    def as_json(self) -> JsonObject:
        return {
            "exact": self.exact,
            "mismatchedBytes": self.mismatched_bytes,
            "mismatchedPixels": self.mismatched_pixels,
            "maximumChannelDelta": self.maximum_channel_delta,
            "meanAbsoluteChannelDelta": self.mean_absolute_channel_delta,
        }


@dataclass(frozen=True, slots=True)
class DrawGeometry:
    vertices: NDArray[np.float32]
    indices: NDArray[np.uint16] | None


def bgra_raw(path: Path, *, width: int, height: int) -> CodeImage:
    pixels = np.fromfile(path, dtype=np.uint8)
    expected = width * height * 4
    if pixels.size != expected:
        raise ValueError(f"{path} has {pixels.size} bytes; expected {expected}")
    bgra = pixels.reshape(height, width, 4)
    return np.ascontiguousarray(bgra[..., [2, 1, 0, 3]])


def compare_images(reference: CodeImage, candidate: CodeImage) -> Comparison:
    if reference.shape != candidate.shape:
        raise ValueError(
            f"image dimensions differ: {reference.shape} != {candidate.shape}"
        )
    difference = np.abs(reference.astype(np.int16) - candidate.astype(np.int16))
    changed = np.any(difference != 0, axis=2)
    return Comparison(
        mismatched_bytes=int(np.count_nonzero(difference)),
        mismatched_pixels=int(np.count_nonzero(changed)),
        maximum_channel_delta=int(difference.max(initial=0)),
        mean_absolute_channel_delta=float(difference.mean()),
        exact=not bool(np.any(changed)),
    )


class AppleGlassReferenceRenderer:
    """Own the captured pass fixtures and the independent OpenGL replay."""

    def __init__(
        self,
        capture: Path,
        *,
        vertex_shader: Path = Path("analysis/apple_glass_reference.vert.glsl"),
        fragment_shader: Path = Path("analysis/apple_glass_reference.frag.glsl"),
        fragment_shader_source: str | None = None,
        intrinsic_table: Path | None = None,
        half_intrinsic_table: Path | None = None,
        sqrt_intrinsic_table: Path | None = None,
        rsqrt_intrinsic_table: Path | None = None,
        circle_scale_reciprocal_bits: int | None = None,
        interpolant_axis_table: Path | None = None,
        interpolant_axis_data: UInt32Image | None = None,
        interpolant_axis_start: int = ACTIVE_START,
        interpolant_trace_data: UInt32Image | None = None,
        interpolant_coefficient_table: Path | None = None,
        interpolant_coefficient_data: UInt32Image | None = None,
        interpolant_tile_start: int = 3,
        interpolant_source_slope_bits: int | None = None,
        interpolant_slope_bits: tuple[int, int, int, int] | None = None,
        interpolant_correction_surface: Path | None = None,
        interpolant_source_low_bits: int | None = None,
        load_interpolant_trace: bool = True,
        load_interpolant_axis_trace: bool = True,
        load_diagnostic_traces: bool = True,
        emulate_apple_blend: bool = True,
        source_mip_bgra_overrides: dict[int, bytes] | None = None,
        destination_bgra_path: Path | None = None,
        highlight_half_stage_data: UInt32Image | None = None,
        highlight_compositor_b_data: UInt32Image | None = None,
        highlight_geometry_data: UInt32Image | None = None,
        context_arguments: dict[str, object] | None = None,
    ) -> None:
        self.capture = capture
        self.emulate_apple_blend = emulate_apple_blend
        self.destination_bgra_path = (
            destination_bgra_path
            if destination_bgra_path is not None
            else capture / "carenderer-live-tree-pre-final-pass-bgra8.raw"
        )
        self.source_mip_bgra_overrides = (
            dict(source_mip_bgra_overrides)
            if source_mip_bgra_overrides is not None
            else None
        )
        self.interpolant_trace_data = (
            np.asarray(interpolant_trace_data, dtype=np.uint32)
            if interpolant_trace_data is not None
            else None
        )
        self.highlight_half_stage_data = highlight_half_stage_data
        self.highlight_compositor_b_data = highlight_compositor_b_data
        self.highlight_geometry_data = highlight_geometry_data
        self.runtime = json.loads(
            (capture / "runtime.json").read_text(encoding="utf-8")
        )
        self.vertex_shader_source = vertex_shader.read_text(encoding="utf-8")
        self.fragment_shader_source = (
            fragment_shader_source
            if fragment_shader_source is not None
            else fragment_shader.read_text(encoding="utf-8")
        )
        context_options = {
            "backend": "egl",
            **(context_arguments or {}),
        }
        self.context = moderngl.create_standalone_context(**context_options)
        self.program = self.context.program(
            vertex_shader=self.vertex_shader_source,
            fragment_shader=self.fragment_shader_source,
        )
        vertex_snapshots = self._glass_buffer_snapshots(
            stage="vertex",
            index=1,
        )
        if len(vertex_snapshots) != 2:
            raise ValueError(
                "captured glass pass must have exactly two vertex-buffer "
                f"records at index 1; found {len(vertex_snapshots)}"
            )
        index_snapshots = self._glass_buffer_snapshots(
            stage="index",
            index=-1,
        )
        if len(index_snapshots) != 1:
            raise ValueError(
                "captured glass pass must have exactly one index-buffer "
                f"record; found {len(index_snapshots)}"
            )
        self.main_geometry = self._geometry(
            vertex_sequence=vertex_snapshots[0]["sequence"],
            vertex_count=6,
        )
        self.shadow_geometry = self._geometry(
            vertex_sequence=vertex_snapshots[1]["sequence"],
            vertex_count=16,
            index_sequence=index_snapshots[0]["sequence"],
            index_count=48,
        )
        self.main_vertex_buffer, self.main_index_buffer, self.main_array = (
            self._vertex_array(self.main_geometry)
        )
        (
            self.shadow_vertex_buffer,
            self.shadow_index_buffer,
            self.shadow_array,
        ) = self._vertex_array(self.shadow_geometry)
        self.final_highlight_vertex_buffer: moderngl.Buffer | None = None
        self.final_highlight_index_buffer: moderngl.Buffer | None = None
        self.final_highlight_array: moderngl.VertexArray | None = None
        self.color = self.context.texture(
            (CAPTURE_WIDTH, CAPTURE_HEIGHT),
            4,
            dtype="f1",
            internal_format=GL_RGBA8,
        )
        self.framebuffer = self.context.framebuffer([self.color])
        self.background_scissor = (0, 0, CAPTURE_WIDTH, CAPTURE_HEIGHT)
        self.final_highlight_scissor = (0, 0, CAPTURE_WIDTH, CAPTURE_HEIGHT)
        self.source_texture = self._source_texture()
        self.destination_texture = self._destination_texture()
        self.refraction_trace_texture = (
            self._refraction_trace_texture() if load_diagnostic_traces else None
        )
        self.interpolant_trace_texture = (
            self._interpolant_trace_texture() if load_interpolant_trace else None
        )
        self.interpolant_axis_trace_texture = (
            self._interpolant_axis_trace_texture(
                interpolant_axis_table,
                interpolant_axis_data,
            )
            if load_interpolant_axis_trace
            else None
        )
        self.interpolant_coefficient_texture = self._interpolant_coefficient_texture(
            interpolant_coefficient_table,
            interpolant_coefficient_data,
        )
        self.interpolant_correction_texture = self._interpolant_correction_texture(
            interpolant_correction_surface
        )
        self.sdf_trace_texture = (
            self._sdf_trace_texture() if load_diagnostic_traces else None
        )
        self.sdf_float_trace_texture = (
            self._sdf_float_trace_texture() if load_diagnostic_traces else None
        )
        self.sdf_normal_trace_texture = (
            self._sdf_normal_trace_texture() if load_diagnostic_traces else None
        )
        self.intrinsic_table_texture = self._intrinsic_table_texture(intrinsic_table)
        self.half_intrinsic_table_texture = self._half_intrinsic_table_texture(
            half_intrinsic_table
        )
        self.highlight_half_stage_texture = self._uint32_trace_texture(
            highlight_half_stage_data,
            name="highlight half-stage trace",
        )
        self.highlight_compositor_b_texture = self._uint32_trace_texture(
            highlight_compositor_b_data,
            name="highlight compositor-B trace",
        )
        self.highlight_geometry_texture = self._uint32_trace_texture(
            highlight_geometry_data,
            name="highlight geometry trace",
        )
        self.sqrt_intrinsic_table_texture = self._packed_intrinsic_table_texture(
            sqrt_intrinsic_table,
            dimensions=(2048, 512),
        )
        self.rsqrt_intrinsic_table_texture = self._packed_intrinsic_table_texture(
            rsqrt_intrinsic_table,
            dimensions=(2048, 256),
        )
        for uniform, value in (
            ("SourceTexture", 0),
            ("AppleRefractionTrace", 1),
            ("AppleInterpolantTrace", 2),
            ("AppleSdfTrace", 3),
            ("AppleSdfFloatTrace", 4),
            ("AppleSdfNormalTrace", 5),
            ("AppleFloatIntrinsicTable", 6),
            ("DestinationTexture", 7),
            ("AppleInterpolantAxisTrace", 8),
            ("AppleInterpolantCoefficientTrace", 9),
            ("AppleInterpolantCorrectionSurface", 10),
            ("AppleSqrtIntrinsicTable", 11),
            ("AppleRsqrtIntrinsicTable", 12),
            ("AppleHalfIntrinsicTable", 13),
            ("AppleHighlightHalfStages", 14),
            ("AppleHighlightCompositorB", 15),
            ("AppleHighlightGeometryTrace", 16),
        ):
            self._set_optional_uniform(uniform, value)
        if circle_scale_reciprocal_bits is not None:
            self.set_circle_scale_reciprocal_bits(
                circle_scale_reciprocal_bits
            )
        if (
            interpolant_slope_bits is not None
            and interpolant_source_slope_bits is not None
        ):
            raise ValueError(
                "complete and scalar interpolant slopes are mutually exclusive"
            )
        source_slope_bits = interpolant_source_slope_bits or 0
        slope_bits = interpolant_slope_bits or (
            0x3F800000,
            0xBF800000,
            source_slope_bits,
            source_slope_bits,
        )
        if len(slope_bits) != 4 or any(
            not 0 <= value <= 0xFFFFFFFF for value in slope_bits
        ):
            raise ValueError("interpolant slopes must be four uint32 values")
        self._set_optional_uniform(
            "AppleInterpolantSlopeBits",
            slope_bits,
        )
        self._set_optional_uniform(
            "AppleInterpolantSourceLowBits",
            interpolant_source_low_bits or 0,
        )
        self._set_optional_uniform(
            "AppleInterpolantAxisStart",
            interpolant_axis_start,
        )
        self._set_optional_uniform(
            "AppleInterpolantTileStart",
            interpolant_tile_start,
        )
        for uniform, value in (
            ("SamplerSpatialQuantization", 0),
            ("SamplerModel", 0),
            ("InnerSamplerCoordinateModel", 0),
            ("OuterSamplerCoordinateModel", 0),
            ("EdgeSamplerCoordinateModel", 0),
            ("ShadowSamplerCoordinateModel", 0),
            ("RefractionMixModel", 0),
            ("HoldingMixMode", 0),
            ("HoldingDivideMode", 0),
            ("UseAppleRefractionTrace", 0),
            ("UseAppleInterpolantTrace", 0),
            ("UseAppleSdfTrace", 0),
            ("UseAppleSqrtTrace", 0),
            ("UseAppleRsqrtTrace", 0),
            ("UseAppleIntrinsicTable", 0),
            ("UseAppleHalfIntrinsicTable", 0),
            ("RecordAppleIntrinsicUsage", 0),
            ("NumericTrace", 0),
            ("CoordinateMode", 0),
            ("AnalyticCoordinateUlpBias", 0),
            ("AppleFastSqrtBias", 0),
            ("AppleFastReciprocalBias", 1),
            ("ArithmeticBarrier", 0),
            ("ProfileMode4Path", 0),
            ("EmulateAppleBlend", int(emulate_apple_blend)),
            ("FinalHighlightPass", 0),
            ("FinalHighlightTrace", 0),
            ("HighlightDerivativeMode", 0),
            ("HighlightCoordinateMode", 0),
            ("HighlightAlphaUlpBias", 0),
            ("HighlightFloatDivisionMode", 0),
            ("HighlightCoverageArithmeticMode", 0),
            ("HighlightMixMode", 0),
            ("HighlightBandMode", 0),
            ("HighlightNormalizeMode", 0),
            ("HighlightSdfArithmeticMode", 0),
            ("HighlightSdfSquaredUlpBias", 0),
            ("HighlightSdfDistanceUlpBias", 0),
            ("HighlightVibrantArithmeticMode", 0),
            ("HighlightSourceDivisionMode", 0),
            ("HighlightSourceConstructionMode", 0),
            ("HighlightDestinationDivisionMode", 0),
            ("UseAppleHighlightAlphaTrace", 0),
            ("UseAppleHighlightSourceTrace", 0),
            ("UseAppleHighlightGeometryTrace", 0),
        ):
            self._set_optional_uniform(uniform, value)
        self.program["MVP"].write(
            struct.pack(
                "<16f",
                0.001953125,
                0.0,
                0.0,
                0.0,
                0.0,
                -0.001953125,
                0.0,
                0.0,
                0.0,
                0.0,
                0.0,
                0.0,
                -1.0,
                1.0,
                0.0,
                1.0,
            )
        )
        self._set_profile_uniforms()

    def __enter__(self) -> "AppleGlassReferenceRenderer":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    @property
    def implementation(self) -> JsonObject:
        return {
            "fragmentShaderBytes": len(self.fragment_shader_source.encode("utf-8")),
            "fragmentShaderSha256": hashlib.sha256(
                self.fragment_shader_source.encode("utf-8")
            ).hexdigest(),
            "glVersion": self.context.info["GL_VERSION"],
            "glVendor": self.context.info["GL_VENDOR"],
            "glRenderer": self.context.info["GL_RENDERER"],
            "moderngl": moderngl.__version__,
            "numpy": np.__version__,
            "python": platform.python_version(),
        }

    def _set_optional_uniform(self, name: str, value: object) -> None:
        try:
            self.program[name].value = value
        except KeyError:
            pass

    def _buffer_snapshot(self, sequence: int) -> bytes:
        snapshots = self.runtime["carendererEvidence"]["metalBufferSnapshots"][
            "snapshots"
        ]
        snapshot = next(value for value in snapshots if value["sequence"] == sequence)
        return bytes.fromhex(snapshot["payload"]["hex"])

    @staticmethod
    def _pipeline_fragment(snapshot: JsonObject) -> str:
        pipeline = snapshot.get("pipeline", {})
        descriptor = (
            pipeline.get("creationDescriptor", {}) if isinstance(pipeline, dict) else {}
        )
        return (
            str(descriptor.get("fragmentFunction", ""))
            if isinstance(descriptor, dict)
            else ""
        )

    def _glass_buffer_snapshots(
        self,
        *,
        stage: str,
        index: int,
    ) -> list[JsonObject]:
        snapshots = self.runtime["carendererEvidence"]["metalBufferSnapshots"][
            "snapshots"
        ]
        matches = [
            snapshot
            for snapshot in snapshots
            if snapshot.get("stage") == stage
            and snapshot.get("index") == index
            and self._pipeline_fragment(snapshot).startswith("glass_background_sdf")
        ]
        return sorted(matches, key=lambda snapshot: snapshot["sequence"])

    def _geometry(
        self,
        *,
        vertex_sequence: int,
        vertex_count: int,
        index_sequence: int | None = None,
        index_count: int | None = None,
    ) -> DrawGeometry:
        source = self._buffer_snapshot(vertex_sequence)
        vertices = np.empty((vertex_count, 8), dtype=np.float32)
        for index in range(vertex_count):
            offset = index * 48
            vertices[index] = struct.unpack_from("<8f", source, offset)
        indices = None
        if index_sequence is not None:
            if index_count is None:
                raise ValueError("indexed geometry requires an index count")
            index_source = self._buffer_snapshot(index_sequence)
            indices = np.frombuffer(
                index_source,
                dtype="<u2",
                count=index_count,
            ).copy()
        return DrawGeometry(vertices=vertices, indices=indices)

    def _vertex_array(
        self,
        geometry: DrawGeometry,
    ) -> tuple[moderngl.Buffer, moderngl.Buffer | None, moderngl.VertexArray]:
        vertex_buffer = self.context.buffer(geometry.vertices.tobytes())
        index_buffer = (
            self.context.buffer(geometry.indices.tobytes())
            if geometry.indices is not None
            else None
        )
        vertex_array = self.context.vertex_array(
            self.program,
            [
                (
                    vertex_buffer,
                    "4f 2f 2f",
                    "in_position",
                    "in_sdf_uv",
                    "in_source_uv",
                )
            ],
            index_buffer=index_buffer,
            index_element_size=2 if index_buffer is not None else 4,
        )
        return vertex_buffer, index_buffer, vertex_array

    def set_draw_geometries(
        self,
        *,
        main: DrawGeometry,
        shadow: DrawGeometry,
    ) -> None:
        """Replace the main and shadow geometry used by subsequent draws."""
        self.main_array.release()
        self.shadow_array.release()
        self.main_vertex_buffer.release()
        self.shadow_vertex_buffer.release()
        if self.main_index_buffer is not None:
            self.main_index_buffer.release()
        if self.shadow_index_buffer is not None:
            self.shadow_index_buffer.release()
        self.main_geometry = main
        self.shadow_geometry = shadow
        self.main_vertex_buffer, self.main_index_buffer, self.main_array = (
            self._vertex_array(main)
        )
        (
            self.shadow_vertex_buffer,
            self.shadow_index_buffer,
            self.shadow_array,
        ) = self._vertex_array(shadow)

    def set_mvp_payload(self, payload: bytes) -> None:
        """Apply the captured 4x4 float32 transform for subsequent draws."""
        if len(payload) < 64:
            raise ValueError("MVP payload is shorter than sixteen float32 values")
        self.program["MVP"].write(payload[:64])

    def set_source_mip_bgra(
        self,
        levels: dict[int, tuple[int, int, bytes]],
    ) -> None:
        """Replace the captured BGRA8 source pyramid used by later draws."""
        ordered = sorted(levels.items())
        if not ordered or [level for level, _ in ordered] != list(range(len(ordered))):
            raise ValueError("source mip levels must be consecutive from zero")

        rgba_levels: list[tuple[int, int, bytes]] = []
        base_width = 0
        base_height = 0
        for level, (width, height, raw) in ordered:
            if width <= 0 or height <= 0 or len(raw) != width * height * 4:
                raise ValueError(f"source mip {level} has an invalid BGRA8 layout")
            if level == 0:
                base_width = width
                base_height = height
            elif width != max(1, base_width >> level) or height != max(
                1, base_height >> level
            ):
                raise ValueError(f"source mip {level} dimensions do not halve")
            bgra = np.frombuffer(raw, dtype=np.uint8).reshape(height, width, 4)
            rgba_levels.append(
                (
                    width,
                    height,
                    np.ascontiguousarray(bgra[..., [2, 1, 0, 3]]).tobytes(),
                )
            )

        replacement = self.context.texture(
            (base_width, base_height),
            4,
            rgba_levels[0][2],
            alignment=1,
            dtype="f1",
            internal_format=GL_RGBA8,
        )
        replacement.build_mipmaps()
        for level, (width, height, rgba) in zip(
            range(1, len(rgba_levels)),
            rgba_levels[1:],
            strict=True,
        ):
            replacement.write(
                rgba,
                viewport=(0, 0, width, height),
                level=level,
                alignment=1,
            )
        replacement.filter = (moderngl.LINEAR_MIPMAP_LINEAR, moderngl.LINEAR)
        replacement.repeat_x = False
        replacement.repeat_y = False
        previous = self.source_texture
        self.source_texture = replacement
        previous.release()

    def set_destination_bgra_path(self, path: Path) -> None:
        """Replace the captured pre-pass color attachment used by later draws."""
        pixels = bgra_raw(
            path,
            width=CAPTURE_WIDTH,
            height=CAPTURE_HEIGHT,
        )
        self.destination_bgra_path = path
        self.destination_texture.write(
            np.ascontiguousarray(np.flipud(pixels)).tobytes(),
            alignment=1,
        )

    def set_draw_scissors(
        self,
        *,
        background: tuple[int, int, int, int],
        final_highlight: tuple[int, int, int, int],
    ) -> None:
        """Apply captured top-left Metal scissors to subsequent OpenGL draws."""

        def metal_to_gl(
            value: tuple[int, int, int, int],
        ) -> tuple[int, int, int, int]:
            x, y, width, height = value
            if (
                x < 0
                or y < 0
                or width <= 0
                or height <= 0
                or x + width > CAPTURE_WIDTH
                or y + height > CAPTURE_HEIGHT
            ):
                raise ValueError(
                    f"captured Metal scissor is outside the target: {value}"
                )
            return x, CAPTURE_HEIGHT - y - height, width, height

        self.background_scissor = metal_to_gl(background)
        self.final_highlight_scissor = metal_to_gl(final_highlight)

    def set_interpolant_coefficients(
        self,
        coefficients: UInt32Image,
        *,
        tile_start: int,
        slope_bits: tuple[int, int, int, int],
    ) -> None:
        """Replace generated AGX raster coefficients for a new main quad."""
        if len(slope_bits) != 4 or any(
            not 0 <= value <= 0xFFFFFFFF for value in slope_bits
        ):
            raise ValueError("interpolant slopes must be four uint32 values")
        replacement = self._interpolant_coefficient_texture(
            None,
            coefficients,
        )
        if replacement is None:
            raise ValueError("interpolant coefficient data is absent")
        previous = self.interpolant_coefficient_texture
        self.interpolant_coefficient_texture = replacement
        if previous is not None:
            previous.release()
        self._set_optional_uniform("AppleInterpolantTileStart", tile_start)
        self._set_optional_uniform("AppleInterpolantSlopeBits", slope_bits)

    def set_interpolant_axis_table(
        self,
        data: UInt32Image,
        *,
        start: int,
    ) -> None:
        """Replace exact separable AGX center values for a runtime quad."""
        replacement = self._interpolant_axis_trace_texture(None, data)
        if replacement is None:
            raise ValueError("interpolant axis data is absent")
        previous = self.interpolant_axis_trace_texture
        self.interpolant_axis_trace_texture = replacement
        if previous is not None:
            previous.release()
        self.program["AppleInterpolantAxisStart"].value = start

    def set_interpolant_trace_data(self, data: UInt32Image) -> None:
        """Replace a complete observed RGBA32Uint interpolant trace."""
        self.interpolant_trace_data = np.asarray(data, dtype=np.uint32)
        replacement = self._interpolant_trace_texture()
        if replacement is None:
            raise ValueError("interpolant trace data is absent")
        previous = self.interpolant_trace_texture
        self.interpolant_trace_texture = replacement
        if previous is not None:
            previous.release()

    def set_sdf_trace_data(self, data: UInt16Image) -> None:
        """Replace the complete top-left-origin RGBA16Float SDF trace."""
        replacement = self._sdf_trace_texture(data)
        if replacement is None:
            raise ValueError("SDF trace data is absent")
        previous = self.sdf_trace_texture
        self.sdf_trace_texture = replacement
        if previous is not None:
            previous.release()

    def set_final_highlight_geometry(self, geometry: DrawGeometry) -> None:
        """Replace the final foreground/highlight pass geometry."""
        if (
            geometry.vertices.shape != (4, 8)
            or geometry.indices is None
            or geometry.indices.shape != (6,)
        ):
            raise ValueError(
                "final-highlight geometry must contain four vertices and six indices"
            )
        replacement = self._vertex_array(geometry)
        if self.final_highlight_array is not None:
            self.final_highlight_array.release()
        if self.final_highlight_vertex_buffer is not None:
            self.final_highlight_vertex_buffer.release()
        if self.final_highlight_index_buffer is not None:
            self.final_highlight_index_buffer.release()
        (
            self.final_highlight_vertex_buffer,
            self.final_highlight_index_buffer,
            self.final_highlight_array,
        ) = replacement

    def _source_snapshot(self) -> JsonObject:
        snapshots = self.runtime["carendererEvidence"]["metalTextureSnapshots"][
            "snapshots"
        ]
        matches = [
            snapshot
            for snapshot in snapshots
            if snapshot.get("index") == 3
            and self._pipeline_fragment(snapshot).startswith("glass_background_sdf")
        ]
        if len(matches) != 1:
            raise ValueError(
                "captured glass pass must have exactly one source texture "
                f"at index 3; found {len(matches)}"
            )
        return matches[0]

    def _source_texture(self) -> moderngl.Texture:
        snapshot = self._source_snapshot()
        levels = snapshot["mipSnapshots"]
        expected_levels = {int(level["level"]) for level in levels}
        if (
            self.source_mip_bgra_overrides is not None
            and set(self.source_mip_bgra_overrides) != expected_levels
        ):
            raise ValueError(
                "source-mip override levels differ: "
                f"{sorted(self.source_mip_bgra_overrides)} != "
                f"{sorted(expected_levels)}"
            )

        def load_level(level: JsonObject) -> CodeImage:
            width = int(level["width"])
            height = int(level["height"])
            if self.source_mip_bgra_overrides is None:
                return bgra_raw(
                    self.capture / str(level["rawFile"]),
                    width=width,
                    height=height,
                )
            index = int(level["level"])
            raw = self.source_mip_bgra_overrides[index]
            expected_bytes = width * height * 4
            if len(raw) != expected_bytes:
                raise ValueError(
                    f"source-mip override {index} has {len(raw)} bytes; "
                    f"expected {expected_bytes}"
                )
            bgra = np.frombuffer(raw, dtype=np.uint8).reshape(
                height,
                width,
                4,
            )
            return np.ascontiguousarray(bgra[..., [2, 1, 0, 3]])

        level_0 = load_level(levels[0])
        texture = self.context.texture(
            (levels[0]["width"], levels[0]["height"]),
            4,
            level_0.tobytes(),
            alignment=1,
            dtype="f1",
            internal_format=GL_RGBA8,
        )
        texture.build_mipmaps()
        for level in levels[1:]:
            pixels = load_level(level)
            texture.write(pixels.tobytes(), level=level["level"], alignment=1)
        texture.filter = (moderngl.LINEAR_MIPMAP_LINEAR, moderngl.LINEAR)
        texture.repeat_x = False
        texture.repeat_y = False
        return texture

    def _destination_texture(self) -> moderngl.Texture:
        pixels = bgra_raw(
            self.destination_bgra_path,
            width=CAPTURE_WIDTH,
            height=CAPTURE_HEIGHT,
        )
        texture = self.context.texture(
            (CAPTURE_WIDTH, CAPTURE_HEIGHT),
            4,
            np.ascontiguousarray(np.flipud(pixels)).tobytes(),
            alignment=1,
            dtype="f1",
            internal_format=GL_RGBA8,
        )
        texture.filter = (moderngl.NEAREST, moderngl.NEAREST)
        texture.repeat_x = False
        texture.repeat_y = False
        return texture

    def _refraction_trace_texture(self) -> moderngl.Texture | None:
        path = self.capture / (
            "carenderer-live-tree-glass-refraction-numeric-trace-rgba16f.raw"
        )
        if not path.exists():
            return None
        values = np.fromfile(path, dtype="<u2")
        expected = CAPTURE_WIDTH * CAPTURE_HEIGHT * 4
        if values.size != expected:
            raise ValueError(
                f"{path} has {values.size} components; expected {expected}"
            )
        pixels = values.reshape(CAPTURE_HEIGHT, CAPTURE_WIDTH, 4)
        texture = self.context.texture(
            (CAPTURE_WIDTH, CAPTURE_HEIGHT),
            4,
            np.ascontiguousarray(np.flipud(pixels)).tobytes(),
            alignment=1,
            dtype="f2",
            internal_format=GL_RGBA16F,
        )
        texture.filter = (moderngl.NEAREST, moderngl.NEAREST)
        return texture

    def _interpolant_trace_texture(self) -> moderngl.Texture | None:
        if self.interpolant_trace_data is None:
            path = self._interpolant_trace_path()
            if not path.exists():
                return None
            values = np.fromfile(path, dtype="<u4")
            expected = CAPTURE_WIDTH * CAPTURE_HEIGHT * 4
            if values.size != expected:
                raise ValueError(
                    f"{path} has {values.size} components; expected {expected}"
                )
            pixels = values.reshape(CAPTURE_HEIGHT, CAPTURE_WIDTH, 4)
        else:
            pixels = self.interpolant_trace_data
            expected_shape = (CAPTURE_HEIGHT, CAPTURE_WIDTH, 4)
            if pixels.shape != expected_shape:
                raise ValueError(
                    "interpolant trace data has shape "
                    f"{pixels.shape}; expected {expected_shape}"
                )
        texture = self.context.texture(
            (CAPTURE_WIDTH, CAPTURE_HEIGHT),
            4,
            np.ascontiguousarray(np.flipud(pixels)).tobytes(),
            alignment=1,
            dtype="u4",
            internal_format=GL_RGBA32UI,
        )
        texture.filter = (moderngl.NEAREST, moderngl.NEAREST)
        return texture

    def _interpolant_trace_path(self) -> Path:
        return self.capture / (
            "carenderer-live-tree-glass-interpolant-numeric-trace-rgba32ui.raw"
        )

    def _interpolant_axis_trace_texture(
        self,
        table_path: Path | None,
        data: UInt32Image | None = None,
    ) -> moderngl.Texture | None:
        if table_path is not None and data is not None:
            raise ValueError(
                "axis table path and in-memory data are mutually exclusive"
            )
        if data is not None:
            table = np.asarray(data, dtype=np.uint32)
        elif table_path is not None:
            table = load_axis_trace_table(table_path)
        else:
            path = self._interpolant_trace_path()
            if not path.exists():
                return None
            table = compress_axis_trace(load_live_interpolant_trace(path))
        if table.ndim != 3 or table.shape[0] != 2 or table.shape[2] != 4:
            raise ValueError("axis data must have shape (2, sample-count, 4)")
        table = np.ascontiguousarray(table, dtype="<u4")
        texture = self.context.texture(
            (table.shape[1], table.shape[0]),
            4,
            table.tobytes(),
            alignment=1,
            dtype="u4",
            internal_format=GL_RGBA32UI,
        )
        texture.filter = (moderngl.NEAREST, moderngl.NEAREST)
        return texture

    def _interpolant_coefficient_texture(
        self,
        path: Path | None,
        data: UInt32Image | None,
    ) -> moderngl.Texture | None:
        if path is not None and data is not None:
            raise ValueError(
                "coefficient table path and in-memory data are mutually exclusive"
            )
        if path is None and data is None:
            return None
        table = (
            load_live_coefficient_table(path)
            if path is not None
            else np.asarray(data, dtype=np.uint32)
        )
        if table.ndim != 3 or table.shape[0] != 2 or table.shape[2] != 4:
            raise ValueError("coefficient data must have shape (2, tile-count, 4)")
        table = np.ascontiguousarray(table, dtype="<u4")
        texture = self.context.texture(
            (table.shape[1], table.shape[0]),
            4,
            table.tobytes(),
            alignment=1,
            dtype="u4",
            internal_format=GL_RGBA32UI,
        )
        texture.filter = (moderngl.NEAREST, moderngl.NEAREST)
        return texture

    def _interpolant_correction_texture(
        self,
        path: Path | None,
    ) -> moderngl.Texture | None:
        if path is None:
            return None
        surface = load_live_correction_surface(path)
        texture = self.context.texture(
            (surface.shape[1], surface.shape[0]),
            4,
            surface.tobytes(),
            alignment=1,
            dtype="i1",
            internal_format=GL_RGBA8I,
        )
        texture.filter = (moderngl.NEAREST, moderngl.NEAREST)
        return texture

    def _sdf_trace_texture(
        self,
        data: UInt16Image | None = None,
    ) -> moderngl.Texture | None:
        if data is None:
            path = self.capture / (
                "carenderer-live-tree-glass-sdf-numeric-trace-rgba16f.raw"
            )
            if not path.exists():
                return None
            values = np.fromfile(path, dtype="<u2")
            expected = CAPTURE_WIDTH * CAPTURE_HEIGHT * 4
            if values.size != expected:
                raise ValueError(
                    f"{path} has {values.size} components; expected {expected}"
                )
            pixels = values.reshape(CAPTURE_HEIGHT, CAPTURE_WIDTH, 4)
        else:
            pixels = np.asarray(data, dtype=np.uint16)
            expected_shape = (CAPTURE_HEIGHT, CAPTURE_WIDTH, 4)
            if pixels.shape != expected_shape:
                raise ValueError(
                    f"SDF trace shape is {pixels.shape}; expected {expected_shape}"
                )
        texture = self.context.texture(
            (CAPTURE_WIDTH, CAPTURE_HEIGHT),
            4,
            np.ascontiguousarray(np.flipud(pixels)).tobytes(),
            alignment=1,
            dtype="f2",
            internal_format=GL_RGBA16F,
        )
        texture.filter = (moderngl.NEAREST, moderngl.NEAREST)
        return texture

    def _sdf_float_trace_texture(self) -> moderngl.Texture | None:
        path = self.capture / (
            "carenderer-live-tree-glass-sdf-float-numeric-trace-rgba32ui.raw"
        )
        if not path.exists():
            return None
        values = np.fromfile(path, dtype="<u4")
        expected = CAPTURE_WIDTH * CAPTURE_HEIGHT * 4
        if values.size != expected:
            raise ValueError(
                f"{path} has {values.size} components; expected {expected}"
            )
        pixels = values.reshape(CAPTURE_HEIGHT, CAPTURE_WIDTH, 4)
        texture = self.context.texture(
            (CAPTURE_WIDTH, CAPTURE_HEIGHT),
            4,
            np.ascontiguousarray(np.flipud(pixels)).tobytes(),
            alignment=1,
            dtype="u4",
            internal_format=GL_RGBA32UI,
        )
        texture.filter = (moderngl.NEAREST, moderngl.NEAREST)
        return texture

    def _sdf_normal_trace_texture(self) -> moderngl.Texture | None:
        path = self.capture / (
            "carenderer-live-tree-glass-sdf-normal-numeric-trace-rgba32ui.raw"
        )
        if not path.exists():
            return None
        values = np.fromfile(path, dtype="<u4")
        expected = CAPTURE_WIDTH * CAPTURE_HEIGHT * 4
        if values.size != expected:
            raise ValueError(
                f"{path} has {values.size} components; expected {expected}"
            )
        pixels = values.reshape(CAPTURE_HEIGHT, CAPTURE_WIDTH, 4)
        texture = self.context.texture(
            (CAPTURE_WIDTH, CAPTURE_HEIGHT),
            4,
            np.ascontiguousarray(np.flipud(pixels)).tobytes(),
            alignment=1,
            dtype="u4",
            internal_format=GL_RGBA32UI,
        )
        texture.filter = (moderngl.NEAREST, moderngl.NEAREST)
        return texture

    def _intrinsic_table_texture(
        self,
        path: Path | None,
    ) -> moderngl.Texture | None:
        if path is None:
            return None
        values = np.fromfile(path, dtype=np.uint8)
        expected = 4096 * 2048
        if values.size != expected:
            raise ValueError(f"{path} has {values.size} bytes; expected {expected}")
        texture = self.context.texture(
            (4096, 2048),
            1,
            values.tobytes(),
            alignment=1,
            dtype="u1",
            internal_format=GL_R8UI,
        )
        texture.filter = (moderngl.NEAREST, moderngl.NEAREST)
        return texture

    def _packed_intrinsic_table_texture(
        self,
        path: Path | None,
        *,
        dimensions: tuple[int, int],
    ) -> moderngl.Texture | None:
        if path is None:
            return None
        values = np.fromfile(path, dtype="<u4")
        expected = dimensions[0] * dimensions[1]
        if values.size != expected:
            raise ValueError(f"{path} has {values.size} words; expected {expected}")
        texture = self.context.texture(
            dimensions,
            1,
            values.tobytes(),
            alignment=1,
            dtype="u4",
            internal_format=GL_R32UI,
        )
        texture.filter = (moderngl.NEAREST, moderngl.NEAREST)
        return texture

    def _half_intrinsic_table_texture(
        self,
        path: Path | None,
    ) -> moderngl.Texture | None:
        if path is None:
            return None
        records = np.fromfile(path, dtype="<u2")
        expected = (1 << 16) * 8
        if records.size != expected:
            raise ValueError(
                f"{path} has {records.size} half words; expected {expected}"
            )
        records = records.reshape(1 << 16, 8)
        packed = records[:, 6].astype(np.uint32)
        packed |= records[:, 7].astype(np.uint32) << 16
        texture = self.context.texture(
            (256, 256),
            1,
            packed.tobytes(),
            alignment=1,
            dtype="u4",
            internal_format=GL_R32UI,
        )
        texture.filter = (moderngl.NEAREST, moderngl.NEAREST)
        return texture

    def _uint32_trace_texture(
        self,
        data: UInt32Image | None,
        *,
        name: str,
    ) -> moderngl.Texture | None:
        if data is None:
            return None
        pixels = np.asarray(data, dtype=np.uint32)
        expected_shape = (CAPTURE_HEIGHT, CAPTURE_WIDTH, 4)
        if pixels.shape != expected_shape:
            raise ValueError(
                f"{name} shape is {pixels.shape}; expected {expected_shape}"
            )
        texture = self.context.texture(
            (CAPTURE_WIDTH, CAPTURE_HEIGHT),
            4,
            np.ascontiguousarray(np.flipud(pixels)).tobytes(),
            alignment=1,
            dtype="u4",
            internal_format=GL_RGBA32UI,
        )
        texture.filter = (moderngl.NEAREST, moderngl.NEAREST)
        return texture

    def _set_profile_uniforms(self) -> None:
        snapshots = self._glass_buffer_snapshots(
            stage="fragment",
            index=1,
        )
        if len(snapshots) != 2:
            raise ValueError(
                "captured glass pass must have exactly two profile-uniform "
                f"records; found {len(snapshots)}"
            )
        self.set_profile_payload(self._buffer_snapshot(snapshots[0]["sequence"]))

    def set_profile_payload(self, payload: bytes) -> None:
        """Apply one captured or independently generated profile buffer."""
        profile = decode_profile(payload)
        fields = profile["fields"]

        def values(name: str) -> tuple[float, ...]:
            return tuple(float(value) for value in fields[name]["values"])

        def scalar(name: str) -> float:
            components = values(name)
            if len(components) != 1:
                raise ValueError(f"{name} is not a scalar profile field")
            return components[0]

        vector_uniforms = {
            "SdfArg": "sdf_arg",
            "SdfTransform": "sdf_transform",
            "SdfArg2": "sdf_arg2",
            "DisplacementMatrix": "displacement_matrix",
            "ShadowOffset": "shadow_offset",
            "FaceMatrix0": "face_matrix_0",
            "FaceMatrix1": "face_matrix_1",
            "FaceMatrix2": "face_matrix_2",
            "BleedMatrix0": "bleed_matrix_0",
            "BleedMatrix1": "bleed_matrix_1",
            "BleedMatrix2": "bleed_matrix_2",
            "ShadowMatrix0": "shadow_matrix_0",
            "ShadowMatrix1": "shadow_matrix_1",
            "ShadowMatrix2": "shadow_matrix_2",
            "BlurAlpha": "blur_alpha",
            "BlurDistance": "blur_distance",
            "EdgeBleedDistance": "edge_bleed_distance",
            "BleedDarken": "bleed_darken",
            "SdrShadowDistance": "sdr_shadow_distance",
        }
        scalar_uniforms = {
            "InnerRefractionAmount": "inner_refraction_amount",
            "InnerRefractionInverseHeight": "inner_refraction_inverse_height",
            "OuterRefractionAmount": "outer_refraction_amount",
            "OuterRefractionInverseHeight": "outer_refraction_inverse_height",
            "RefractionThreshold0": "refraction_threshold_0",
            "RefractionThreshold1": "refraction_threshold_1",
            "BlurRadius": "blur_radius",
            "EdgeBleedBlurRadius": "edge_bleed_blur_radius",
            "EdgeBleedAmount": "edge_bleed_amount",
            "EdgeBleedInverseHeight": "edge_bleed_inverse_height",
            "ShadowAmount": "shadow_amount",
            "ShadowInverseHeight": "shadow_inverse_height",
            "ShadowBlurRadius": "shadow_blur_radius",
            "ShadowInverseRadius": "shadow_inverse_radius",
            "ShadowFaceOpacity": "shadow_face_opacity",
            "ShadowContribution": "shadow_contribution",
            "EdgeBleedOpacity": "edge_bleed_opacity",
            "FaceOpacity": "face_opacity",
            "ShadowDistanceOffset": "shadow_distance_offset",
            "ShadowOpacity": "shadow_opacity",
            "RefractionOpacity": "refraction_opacity",
            "HoldingToneOpacity": "holding_tone_opacity",
            "ClampLimit": "clamp_limit",
            "PreserveHue": "preserve_hue",
            "SdrWhiteValue": "sdr_white_value",
            "FloatMixWorkaround": "float_mix_workaround",
            "ComplexRefraction": "complex_refraction",
        }
        for uniform, field in vector_uniforms.items():
            self._set_optional_uniform(uniform, values(field))
        for uniform, field in scalar_uniforms.items():
            self._set_optional_uniform(uniform, scalar(field))
        self._set_optional_uniform("EdrScale", 1.0)

    def set_circle_scale_reciprocal_bits(self, bits: int) -> None:
        """Update the exact uniform reciprocal for one profile radius."""
        if not 0 <= bits <= 0xFFFF_FFFF:
            raise ValueError("circle-scale reciprocal bits must fit uint32")
        self._set_optional_uniform("AppleCircleScaleReciprocalBits", bits)

    def prepare_render(self) -> None:
        prefill = bgra_raw(
            self.destination_bgra_path,
            width=CAPTURE_WIDTH,
            height=CAPTURE_HEIGHT,
        )
        self.color.write(
            np.ascontiguousarray(np.flipud(prefill)).tobytes(),
            alignment=1,
        )
        self.framebuffer.use()
        self.context.viewport = (0, 0, CAPTURE_WIDTH, CAPTURE_HEIGHT)
        self.context.scissor = (0, 0, CAPTURE_WIDTH, CAPTURE_HEIGHT)
        if self.emulate_apple_blend:
            self.context.disable(moderngl.BLEND)
        else:
            self.context.enable(moderngl.BLEND)
            self.context.blend_func = (
                moderngl.ONE,
                moderngl.ONE_MINUS_SRC_ALPHA,
            )
        self.source_texture.use(location=0)
        if self.refraction_trace_texture is not None:
            self.refraction_trace_texture.use(location=1)
        if self.interpolant_trace_texture is not None:
            self.interpolant_trace_texture.use(location=2)
        if self.interpolant_axis_trace_texture is not None:
            self.interpolant_axis_trace_texture.use(location=8)
        if self.interpolant_coefficient_texture is not None:
            self.interpolant_coefficient_texture.use(location=9)
        if self.interpolant_correction_texture is not None:
            self.interpolant_correction_texture.use(location=10)
        if self.sdf_trace_texture is not None:
            self.sdf_trace_texture.use(location=3)
        if self.sdf_float_trace_texture is not None:
            self.sdf_float_trace_texture.use(location=4)
        if self.sdf_normal_trace_texture is not None:
            self.sdf_normal_trace_texture.use(location=5)
        if self.intrinsic_table_texture is not None:
            self.intrinsic_table_texture.use(location=6)
        if self.sqrt_intrinsic_table_texture is not None:
            self.sqrt_intrinsic_table_texture.use(location=11)
        if self.rsqrt_intrinsic_table_texture is not None:
            self.rsqrt_intrinsic_table_texture.use(location=12)
        if self.half_intrinsic_table_texture is not None:
            self.half_intrinsic_table_texture.use(location=13)
        if self.highlight_half_stage_texture is not None:
            self.highlight_half_stage_texture.use(location=14)
        if self.highlight_compositor_b_texture is not None:
            self.highlight_compositor_b_texture.use(location=15)
        if self.highlight_geometry_texture is not None:
            self.highlight_geometry_texture.use(location=16)
        self.destination_texture.use(location=7)

    def draw_main_layer(self) -> None:
        self.program["SdfMode"].value = 4
        self.main_array.render(
            mode=moderngl.TRIANGLES,
            vertices=6,
        )

    def draw_shadow_layer(self) -> None:
        self.program["SdfMode"].value = -4
        self.shadow_array.render(
            mode=moderngl.TRIANGLES,
            vertices=48,
        )

    def draw_layers(self) -> None:
        self.context.scissor = self.background_scissor
        self.draw_main_layer()
        self.draw_shadow_layer()

    def _final_highlight_snapshots(self) -> list[JsonObject]:
        evidence = self.runtime.get("carendererLocalBackdropEvidence")
        if not isinstance(evidence, dict):
            raise ValueError("capture has no local-backdrop render evidence")
        render = evidence.get("render")
        if not isinstance(render, dict):
            raise ValueError("local-backdrop evidence has no render record")
        buffers = render.get("metalBufferSnapshots")
        if not isinstance(buffers, dict):
            raise ValueError("local-backdrop render has no buffer snapshots")
        snapshots = buffers.get("snapshots")
        if not isinstance(snapshots, list):
            raise ValueError("local-backdrop buffer snapshots are malformed")
        return [
            snapshot
            for snapshot in snapshots
            if isinstance(snapshot, dict)
            and self._pipeline_fragment(snapshot) == "A2Xghfc"
        ]

    @staticmethod
    def _snapshot_bytes(snapshot: JsonObject) -> bytes:
        payload = snapshot.get("payload")
        if not isinstance(payload, dict):
            raise ValueError("buffer snapshot has no payload")
        hexadecimal = payload.get("hex")
        if not isinstance(hexadecimal, str):
            raise ValueError("buffer snapshot payload has no hex bytes")
        return bytes.fromhex(hexadecimal)

    def _prepare_final_highlight(
        self,
        *,
        uniform_edits: dict[int, bytes] | None = None,
        uniform_payload: bytes | None = None,
    ) -> None:
        snapshots = self._final_highlight_snapshots()

        def latest(stage: str, index: int) -> JsonObject:
            matches = [
                snapshot
                for snapshot in snapshots
                if snapshot.get("stage") == stage and snapshot.get("index") == index
            ]
            if not matches:
                raise ValueError(
                    f"captured final highlight has no {stage} buffer at index {index}"
                )
            return max(matches, key=lambda snapshot: int(snapshot["sequence"]))

        if self.final_highlight_array is None:
            vertex_source = self._snapshot_bytes(latest("vertex", 1))
            vertices = np.empty((4, 8), dtype=np.float32)
            for index in range(4):
                vertices[index] = struct.unpack_from(
                    "<8f",
                    vertex_source,
                    index * 48,
                )
            index_source = self._snapshot_bytes(latest("index", -1))
            geometry = DrawGeometry(
                vertices=vertices,
                indices=np.frombuffer(
                    index_source,
                    dtype="<u2",
                    count=6,
                ).copy(),
            )
            (
                self.final_highlight_vertex_buffer,
                self.final_highlight_index_buffer,
                self.final_highlight_array,
            ) = self._vertex_array(geometry)

        uniform_source = bytearray(
            self._snapshot_bytes(latest("fragment", 1))
            if uniform_payload is None
            else uniform_payload
        )
        if len(uniform_source) < 0xF8:
            raise ValueError(
                "final-highlight uniform payload is shorter than 248 bytes"
            )
        for offset, payload in sorted((uniform_edits or {}).items()):
            end = offset + len(payload)
            if offset < 0 or not payload or end > len(uniform_source):
                raise ValueError(
                    "final-highlight uniform edit exceeds the captured "
                    f"record: offset={offset} bytes={len(payload)} "
                    f"record={len(uniform_source)}"
                )
            uniform_source[offset:end] = payload

        def half_vector(offset: int) -> tuple[float, ...]:
            return tuple(
                float(value)
                for value in struct.unpack_from("<4e", uniform_source, offset)
            )

        for name, offset in (
            ("VibrantMatrix0", 0x60),
            ("VibrantMatrix1", 0x68),
            ("VibrantMatrix2", 0x70),
            ("VibrantMatrix3", 0x78),
            ("VibrantMatrix4", 0x80),
            ("VibrantControls", 0x88),
            ("KeyFillParams0", 0xD0),
            ("KeyFillParams1", 0xD8),
            ("KeyFillParams2", 0xE0),
            ("KeyFillColor0", 0xE8),
            ("KeyFillColor1", 0xF0),
        ):
            self.program[name].value = half_vector(offset)

    def draw_final_highlight_layer(
        self,
        *,
        uniform_edits: dict[int, bytes] | None = None,
        uniform_payload: bytes | None = None,
    ) -> None:
        self.prepare_final_highlight_layer(
            uniform_edits=uniform_edits,
            uniform_payload=uniform_payload,
        )
        self.draw_prepared_final_highlight_layer()

    def prepare_final_highlight_layer(
        self,
        *,
        uniform_edits: dict[int, bytes] | None = None,
        uniform_payload: bytes | None = None,
    ) -> None:
        """Bind final-highlight geometry and uniforms without issuing a draw."""
        self._prepare_final_highlight(
            uniform_edits=uniform_edits,
            uniform_payload=uniform_payload,
        )
        if self.final_highlight_array is None:
            raise RuntimeError("final-highlight geometry was not created")
        self.context.scissor = self.final_highlight_scissor
        self.program["SdfMode"].value = 4

    def draw_prepared_final_highlight_layer(self) -> None:
        """Issue one draw after :meth:`prepare_final_highlight_layer`."""
        if self.final_highlight_array is None:
            raise RuntimeError("final-highlight geometry was not created")
        self.program["FinalHighlightPass"].value = 1
        try:
            self.final_highlight_array.render(
                mode=moderngl.TRIANGLES,
                vertices=6,
            )
        finally:
            self.program["FinalHighlightPass"].value = 0

    def render_final_highlight(
        self,
        *,
        uniform_payload: bytes | None = None,
    ) -> CodeImage:
        self.prepare_render()
        self.draw_final_highlight_layer(
            uniform_payload=uniform_payload,
        )
        self.context.finish()
        pixels = np.frombuffer(
            self.framebuffer.read(components=4, alignment=1),
            dtype=np.uint8,
        ).reshape(CAPTURE_HEIGHT, CAPTURE_WIDTH, 4)
        return np.flipud(pixels).copy()

    def render_final_highlight_half(
        self,
        *,
        uniform_edits: dict[int, bytes] | None = None,
        uniform_payload: bytes | None = None,
        trace_mode: int = 2,
    ) -> NDArray[np.uint16]:
        """Render one final-highlight trace as raw binary16 words."""
        if trace_mode not in {*range(1, 19), 40, 41}:
            raise ValueError(
                "half trace mode must be 1 through 18, "
                "40 (SDF X bits), or 41 (SDF Y bits)"
            )
        target = self.context.texture(
            (CAPTURE_WIDTH, CAPTURE_HEIGHT),
            4,
            dtype="f2",
            internal_format=GL_RGBA16F,
        )
        framebuffer = self.context.framebuffer([target])
        previous_trace = self.program["FinalHighlightTrace"].value
        try:
            self.prepare_render()
            framebuffer.use()
            framebuffer.clear()
            self.context.viewport = (0, 0, CAPTURE_WIDTH, CAPTURE_HEIGHT)
            self.context.scissor = (0, 0, CAPTURE_WIDTH, CAPTURE_HEIGHT)
            self.context.disable(moderngl.BLEND)
            self.program["FinalHighlightTrace"].value = trace_mode
            self.draw_final_highlight_layer(
                uniform_edits=uniform_edits,
                uniform_payload=uniform_payload,
            )
            self.context.finish()
            pixels = np.frombuffer(
                framebuffer.read(
                    components=4,
                    alignment=1,
                    dtype="f2",
                ),
                dtype="<u2",
            ).reshape(CAPTURE_HEIGHT, CAPTURE_WIDTH, 4)
            return np.flipud(pixels).copy()
        finally:
            self.program["FinalHighlightTrace"].value = previous_trace
            framebuffer.release()
            target.release()

    def render(self) -> CodeImage:
        self.prepare_render()
        self.draw_layers()
        self.context.finish()
        pixels = np.frombuffer(
            self.framebuffer.read(components=4, alignment=1),
            dtype=np.uint8,
        ).reshape(CAPTURE_HEIGHT, CAPTURE_WIDTH, 4)
        return np.flipud(pixels).copy()

    def render_complete(
        self,
        *,
        final_highlight_payload: bytes | None = None,
    ) -> CodeImage:
        """Render the recovered background and final-highlight passes."""
        return self.render_final_highlight_over(
            self.render(),
            final_highlight_payload=final_highlight_payload,
        )

    def render_final_highlight_over(
        self,
        background: CodeImage,
        *,
        final_highlight_payload: bytes | None = None,
    ) -> CodeImage:
        """Render only the final highlight over an existing BGRA8 result."""
        if background.shape != (CAPTURE_HEIGHT, CAPTURE_WIDTH, 4):
            raise ValueError(
                "final-highlight background must be a complete BGRA8 frame"
            )
        if background.dtype != np.uint8:
            raise ValueError("final-highlight background must contain uint8 pixels")
        original_destination = self.destination_texture.read(alignment=1)
        try:
            background_bottom_left = np.ascontiguousarray(
                np.flipud(background)
            ).tobytes()
            self.destination_texture.write(background_bottom_left, alignment=1)
            self.prepare_render()
            self.color.write(background_bottom_left, alignment=1)
            self.framebuffer.use()
            self.draw_final_highlight_layer(
                uniform_payload=final_highlight_payload,
            )
            self.context.finish()
            pixels = np.frombuffer(
                self.framebuffer.read(components=4, alignment=1),
                dtype=np.uint8,
            ).reshape(CAPTURE_HEIGHT, CAPTURE_WIDTH, 4)
            return np.flipud(pixels).copy()
        finally:
            self.destination_texture.write(
                original_destination,
                alignment=1,
            )

    def render_final_highlight_composite_half_over(
        self,
        background: CodeImage,
        *,
        final_highlight_payload: bytes | None = None,
    ) -> NDArray[np.uint16]:
        """Render the complete final-highlight composite as binary16 words."""
        if background.shape != (CAPTURE_HEIGHT, CAPTURE_WIDTH, 4):
            raise ValueError(
                "final-highlight background must be a complete BGRA8 frame"
            )
        if background.dtype != np.uint8:
            raise ValueError("final-highlight background must contain uint8 pixels")
        target = self.context.texture(
            (CAPTURE_WIDTH, CAPTURE_HEIGHT),
            4,
            dtype="f2",
            internal_format=GL_RGBA16F,
        )
        framebuffer = self.context.framebuffer([target])
        original_destination = self.destination_texture.read(alignment=1)
        try:
            background_bottom_left = np.ascontiguousarray(np.flipud(background))
            self.destination_texture.write(
                background_bottom_left.tobytes(),
                alignment=1,
            )
            background_half = np.ascontiguousarray(
                background_bottom_left.astype(np.float32) / 255.0,
                dtype="<f2",
            )
            target.write(background_half.tobytes(), alignment=1)
            self.prepare_render()
            framebuffer.use()
            self.context.viewport = (0, 0, CAPTURE_WIDTH, CAPTURE_HEIGHT)
            self.context.disable(moderngl.BLEND)
            self.draw_final_highlight_layer(
                uniform_payload=final_highlight_payload,
            )
            self.context.finish()
            pixels = np.frombuffer(
                framebuffer.read(
                    components=4,
                    alignment=1,
                    dtype="f2",
                ),
                dtype="<u2",
            ).reshape(CAPTURE_HEIGHT, CAPTURE_WIDTH, 4)
            return np.flipud(pixels).copy()
        finally:
            self.framebuffer.use()
            self.destination_texture.write(
                original_destination,
                alignment=1,
            )
            framebuffer.release()
            target.release()

    def record_intrinsic_usage(self) -> NDArray[np.uint32]:
        if "RecordAppleIntrinsicUsage" not in self.program:
            raise RuntimeError("the specialized shader omits intrinsic-usage recording")
        word_count = (1 << 23) // 32
        operation_count = 3
        usage = self.context.buffer(reserve=operation_count * word_count * 4)
        usage.clear()
        usage.bind_to_storage_buffer(0)
        try:
            self.program["RecordAppleIntrinsicUsage"].value = 1
            self.prepare_render()
            self.draw_layers()
            self.context.finish()
            return (
                np.frombuffer(
                    usage.read(),
                    dtype="<u4",
                )
                .reshape(operation_count, word_count)
                .copy()
            )
        finally:
            self.program["RecordAppleIntrinsicUsage"].value = 0
            usage.release()

    def render_numeric_trace(
        self,
        trace: int,
        *,
        include_shadow_draw: bool = False,
    ) -> NDArray[np.uint16]:
        if "NumericTrace" not in self.program:
            raise RuntimeError("the specialized shader omits numeric trace rendering")
        if trace not in {
            1,
            2,
            3,
            4,
            5,
            6,
            7,
            8,
            9,
            10,
            11,
            12,
            13,
            14,
            15,
            16,
            17,
            18,
            19,
            20,
            21,
            22,
            23,
            24,
            25,
            26,
            27,
            28,
        }:
            raise ValueError(
                "numeric trace must be one (SDF), two (refraction), "
                "three (GLSL arithmetic), four (source sample), "
                "five (sampler weights), six (weight residuals), "
                "seven (displacement), eight (coverage arithmetic), "
                "nine (final color), ten (source color), eleven "
                "(face), twelve (pre-holding composite), or thirteen "
                "(post-holding composite), fourteen (edge-bleed face), "
                "fifteen (outer refraction), sixteen (outer sample), "
                "seventeen (outer-refraction mix), eighteen "
                "(edge refraction), nineteen (edge sample), twenty "
                "(edge amount), twenty-one (inner sample), or twenty-two "
                "(outer sample in the mixed path), twenty-three "
                "(shadow layer), or twenty-four (shadow arithmetic)"
                ", twenty-five (shadow source sample), or a probe-defined "
                "trace from twenty-six onward"
            )
        target = self.context.texture(
            (CAPTURE_WIDTH, CAPTURE_HEIGHT),
            4,
            dtype="f2",
            internal_format=GL_RGBA16F,
        )
        framebuffer = self.context.framebuffer([target])
        emulate_blend = self.program["EmulateAppleBlend"].value
        try:
            framebuffer.use()
            framebuffer.clear()
            self.context.viewport = (0, 0, CAPTURE_WIDTH, CAPTURE_HEIGHT)
            self.context.scissor = (0, 0, CAPTURE_WIDTH, CAPTURE_HEIGHT)
            self.context.disable(moderngl.BLEND)
            self.source_texture.use(location=0)
            if self.refraction_trace_texture is not None:
                self.refraction_trace_texture.use(location=1)
            if self.interpolant_trace_texture is not None:
                self.interpolant_trace_texture.use(location=2)
            if self.interpolant_axis_trace_texture is not None:
                self.interpolant_axis_trace_texture.use(location=8)
            if self.interpolant_coefficient_texture is not None:
                self.interpolant_coefficient_texture.use(location=9)
            if self.interpolant_correction_texture is not None:
                self.interpolant_correction_texture.use(location=10)
            if self.sdf_trace_texture is not None:
                self.sdf_trace_texture.use(location=3)
            if self.sdf_float_trace_texture is not None:
                self.sdf_float_trace_texture.use(location=4)
            if self.sdf_normal_trace_texture is not None:
                self.sdf_normal_trace_texture.use(location=5)
            if self.intrinsic_table_texture is not None:
                self.intrinsic_table_texture.use(location=6)
            if self.sqrt_intrinsic_table_texture is not None:
                self.sqrt_intrinsic_table_texture.use(location=11)
            if self.rsqrt_intrinsic_table_texture is not None:
                self.rsqrt_intrinsic_table_texture.use(location=12)
            if self.half_intrinsic_table_texture is not None:
                self.half_intrinsic_table_texture.use(location=13)
            self.destination_texture.use(location=7)
            if trace == 9:
                # Apple's trace 9 is the final main-layer value before the
                # fixed-function destination blend.
                self.program["EmulateAppleBlend"].value = 0
            self.program["NumericTrace"].value = trace
            if include_shadow_draw:
                self.draw_layers()
            else:
                self.program["SdfMode"].value = 4
                self.main_array.render(
                    mode=moderngl.TRIANGLES,
                    vertices=6,
                )
            self.context.finish()
            values = np.frombuffer(
                framebuffer.read(
                    components=4,
                    alignment=1,
                    dtype="f2",
                ),
                dtype="<u2",
            ).reshape(CAPTURE_HEIGHT, CAPTURE_WIDTH, 4)
            return np.flipud(values).copy()
        finally:
            self.program["NumericTrace"].value = 0
            self.program["EmulateAppleBlend"].value = emulate_blend
            framebuffer.release()
            target.release()

    def close(self) -> None:
        if self.final_highlight_array is not None:
            self.final_highlight_array.release()
        if self.final_highlight_vertex_buffer is not None:
            self.final_highlight_vertex_buffer.release()
        if self.final_highlight_index_buffer is not None:
            self.final_highlight_index_buffer.release()
        self.main_array.release()
        self.shadow_array.release()
        self.main_vertex_buffer.release()
        self.shadow_vertex_buffer.release()
        if self.main_index_buffer is not None:
            self.main_index_buffer.release()
        if self.shadow_index_buffer is not None:
            self.shadow_index_buffer.release()
        self.source_texture.release()
        self.destination_texture.release()
        if self.refraction_trace_texture is not None:
            self.refraction_trace_texture.release()
        if self.interpolant_trace_texture is not None:
            self.interpolant_trace_texture.release()
        if self.interpolant_axis_trace_texture is not None:
            self.interpolant_axis_trace_texture.release()
        if self.interpolant_coefficient_texture is not None:
            self.interpolant_coefficient_texture.release()
        if self.interpolant_correction_texture is not None:
            self.interpolant_correction_texture.release()
        if self.sdf_trace_texture is not None:
            self.sdf_trace_texture.release()
        if self.sdf_float_trace_texture is not None:
            self.sdf_float_trace_texture.release()
        if self.sdf_normal_trace_texture is not None:
            self.sdf_normal_trace_texture.release()
        if self.intrinsic_table_texture is not None:
            self.intrinsic_table_texture.release()
        if self.sqrt_intrinsic_table_texture is not None:
            self.sqrt_intrinsic_table_texture.release()
        if self.rsqrt_intrinsic_table_texture is not None:
            self.rsqrt_intrinsic_table_texture.release()
        if self.half_intrinsic_table_texture is not None:
            self.half_intrinsic_table_texture.release()
        if self.highlight_half_stage_texture is not None:
            self.highlight_half_stage_texture.release()
        if self.highlight_compositor_b_texture is not None:
            self.highlight_compositor_b_texture.release()
        if self.highlight_geometry_texture is not None:
            self.highlight_geometry_texture.release()
        self.framebuffer.release()
        self.color.release()
        self.program.release()
        self.context.release()


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "capture",
        type=Path,
        help="extracted schema-53 artifact directory",
    )
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def main() -> int:
    arguments = parse_arguments()
    reference = bgra_raw(
        arguments.capture / "carenderer-live-tree-glass-prefix-reference-bgra8.raw",
        width=CAPTURE_WIDTH,
        height=CAPTURE_HEIGHT,
    )
    with AppleGlassReferenceRenderer(arguments.capture) as renderer:
        candidate = renderer.render()
        implementation = renderer.implementation
    comparison = compare_images(reference, candidate)
    report = {
        "capture": str(arguments.capture),
        "implementation": implementation,
        "comparison": comparison.as_json(),
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    if arguments.output is not None:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        Image.fromarray(candidate, mode="RGBA").save(arguments.output)
    return 0 if comparison.exact else 1


if __name__ == "__main__":
    raise SystemExit(main())
