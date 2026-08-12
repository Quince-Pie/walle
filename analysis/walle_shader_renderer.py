#!/usr/bin/env python3
"""Render Walle's production Liquid Glass GLSL in a headless EGL context."""

import argparse
import hashlib
import math
import platform
from dataclasses import dataclass
from pathlib import Path

import moderngl
import numpy as np
import pyvips
from numpy.typing import NDArray
from PIL import Image
from PIL import __version__ as pillow_version


type CodeImage = NDArray[np.uint8]

GL_RGBA8 = 0x8058
GL_SRGB8_ALPHA8 = 0x8C43

GLASS_SIGMA_FRAC_CLEAR = 0.013
GLASS_SIGMA_FRAC_REGULAR = 0.038
GLASS_SAT_CLEAR = 1.10
GLASS_SAT_REGULAR = 1.15
GLASS_DOWN_FACTOR = 8


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def rgba8(image: Image.Image | CodeImage) -> CodeImage:
    if isinstance(image, Image.Image):
        result = np.asarray(image.convert("RGBA"), dtype=np.uint8)
    else:
        result = np.asarray(image, dtype=np.uint8)
        if result.ndim != 3 or result.shape[2] not in {3, 4}:
            raise ValueError("wallpaper must have shape (height, width, 3|4)")
        if result.shape[2] == 3:
            alpha = np.full((*result.shape[:2], 1), 255, dtype=np.uint8)
            result = np.concatenate((result, alpha), axis=2)
    return np.ascontiguousarray(result)


def prepare_glass_texture(image: CodeImage, *, regular: bool) -> CodeImage:
    """Reproduce Walle's libvips downsample/blur/vibrancy pipeline."""
    source = rgba8(image)
    height, width, bands = source.shape
    glass_width = max(1, width // GLASS_DOWN_FACTOR)
    glass_height = max(1, height // GLASS_DOWN_FACTOR)
    scale_x = glass_width / width
    scale_y = glass_height / height
    sigma = math.hypot(width, height) * (
        GLASS_SIGMA_FRAC_REGULAR if regular else GLASS_SIGMA_FRAC_CLEAR
    )
    saturation = GLASS_SAT_REGULAR if regular else GLASS_SAT_CLEAR

    vips_source = pyvips.Image.new_from_memory(
        source.data,
        width,
        height,
        bands,
        "uchar",
    )
    glass = vips_source.resize(scale_x, vscale=scale_y)
    glass = glass.gaussblur(sigma / GLASS_DOWN_FACTOR)
    glass = glass.colourspace("hsv")
    glass = glass.linear(
        [1.0, saturation, 1.0, 1.0],
        [0.0, 0.0, 0.0, 0.0],
    )
    glass = glass.colourspace("srgb").cast("uchar")
    pixels = np.frombuffer(glass.write_to_memory(), dtype=np.uint8)
    return pixels.reshape(glass.height, glass.width, glass.bands).copy()


@dataclass(slots=True, kw_only=True)
class WallpaperTextures:
    standard: moderngl.Texture
    glass: moderngl.Texture
    width: int
    height: int

    def release(self) -> None:
        self.standard.release()
        self.glass.release()


class WalleShaderRenderer:
    """Own the exact production program, textures, and RGBA8 render target."""

    def __init__(
        self,
        *,
        width: int,
        height: int,
        vertex_shader: Path = Path("shaders/vert.glsl"),
        fragment_shader: Path = Path("shaders/frag.glsl"),
    ) -> None:
        if width <= 0 or height <= 0:
            raise ValueError("render dimensions must be positive")
        self.width = width
        self.height = height
        self.vertex_shader = vertex_shader
        self.fragment_shader = fragment_shader
        self.context = moderngl.create_standalone_context(backend="egl")
        self.program = self.context.program(
            vertex_shader=vertex_shader.read_text(encoding="utf-8"),
            fragment_shader=fragment_shader.read_text(encoding="utf-8"),
        )
        vertices = np.array(
            [
                -1.0,
                -1.0,
                0.0,
                0.0,
                1.0,
                -1.0,
                1.0,
                0.0,
                -1.0,
                1.0,
                0.0,
                1.0,
                1.0,
                1.0,
                1.0,
                1.0,
            ],
            dtype=np.float32,
        )
        self.vertex_buffer = self.context.buffer(vertices.tobytes())
        self.vertex_array = self.context.vertex_array(
            self.program,
            [(self.vertex_buffer, "2f 2f", "in_vert", "in_texcoord")],
        )
        self.color = self.context.texture(
            (width, height),
            4,
            dtype="f1",
            internal_format=GL_RGBA8,
        )
        self.framebuffer = self.context.framebuffer([self.color])
        for unit, name in enumerate(("TexA", "TexGlassA", "TexB", "TexGlassB", "SourceTexture")):
            if name in self.program:
                self.program[name].value = unit

    def __enter__(self) -> "WalleShaderRenderer":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    @property
    def implementation(self) -> dict[str, str | int]:
        return {
            "vertexShader": str(self.vertex_shader),
            "vertexShaderSha256": file_sha256(self.vertex_shader),
            "fragmentShader": str(self.fragment_shader),
            "fragmentShaderSha256": file_sha256(self.fragment_shader),
            "glVersion": self.context.info["GL_VERSION"],
            "glVendor": self.context.info["GL_VENDOR"],
            "glRenderer": self.context.info["GL_RENDERER"],
            "moderngl": moderngl.__version__,
            "numpy": np.__version__,
            "Pillow": pillow_version,
            "pyvips": pyvips.__version__,
            "libvips": ".".join(str(pyvips.version(index)) for index in range(3)),
            "python": platform.python_version(),
        }

    def upload_wallpaper(
        self,
        image: Image.Image | CodeImage,
        *,
        regular: bool,
    ) -> WallpaperTextures:
        standard_pixels = rgba8(image)
        if standard_pixels.shape[:2] != (self.height, self.width):
            raise ValueError(
                "wallpaper dimensions differ from the render target: "
                f"{standard_pixels.shape[1]}x{standard_pixels.shape[0]} != "
                f"{self.width}x{self.height}"
            )
        glass_pixels = prepare_glass_texture(
            standard_pixels,
            regular=regular,
        )
        standard = self.context.texture(
            (self.width, self.height),
            4,
            standard_pixels.tobytes(),
            alignment=1,
            dtype="f1",
            internal_format=GL_SRGB8_ALPHA8,
        )
        glass = self.context.texture(
            (glass_pixels.shape[1], glass_pixels.shape[0]),
            4,
            glass_pixels.tobytes(),
            alignment=1,
            dtype="f1",
            internal_format=GL_SRGB8_ALPHA8,
        )
        for texture in (standard, glass):
            texture.filter = (moderngl.LINEAR, moderngl.LINEAR)
            texture.repeat_x = False
            texture.repeat_y = False
        return WallpaperTextures(
            standard=standard,
            glass=glass,
            width=self.width,
            height=self.height,
        )

    def render(
        self,
        *,
        outgoing: WallpaperTextures,
        incoming: WallpaperTextures,
        time: float,
        center_top_left: tuple[float, float],
        maximum_radius: float,
        regular: bool,
    ) -> CodeImage:
        if outgoing.width != self.width or outgoing.height != self.height:
            raise ValueError("outgoing texture dimensions do not match")
        if incoming.width != self.width or incoming.height != self.height:
            raise ValueError("incoming texture dimensions do not match")
        if not 0.0 <= time <= 1.0:
            raise ValueError("time must be in [0, 1]")
        center_x, center_y = center_top_left
        self.framebuffer.use()
        self.context.viewport = (0, 0, self.width, self.height)
        outgoing.standard.use(location=0)
        outgoing.glass.use(location=1)
        incoming.standard.use(location=2)
        incoming.glass.use(location=3)
        if "Time" in self.program:
            self.program["Time"].value = time
        if "Resolution" in self.program:
            self.program["Resolution"].value = (self.width, self.height)
        if "CenterPointPixels" in self.program:
            self.program["CenterPointPixels"].value = (
                center_x,
                self.height - center_y,
            )
        if "MaxRadiusPixels" in self.program:
            self.program["MaxRadiusPixels"].value = maximum_radius
        if "Variant" in self.program:
            self.program["Variant"].value = 1.0 if regular else 0.0
        self.vertex_array.render(mode=moderngl.TRIANGLE_STRIP)
        pixels = np.frombuffer(
            self.framebuffer.read(components=4, alignment=1),
            dtype=np.uint8,
        ).reshape(self.height, self.width, 4)
        return np.flipud(pixels).copy()

    def close(self) -> None:
        self.framebuffer.release()
        self.color.release()
        self.vertex_array.release()
        self.vertex_buffer.release()
        self.program.release()
        self.context.release()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Render one frame with Walle's exact production GLSL.",
    )
    parser.add_argument("outgoing", type=Path)
    parser.add_argument("incoming", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--time", type=float, required=True)
    parser.add_argument("--center", default="0.25,0.30")
    parser.add_argument(
        "--variant",
        choices=("clear", "regular"),
        required=True,
    )
    parser.add_argument(
        "--vertex-shader",
        type=Path,
        default=Path("shaders/vert.glsl"),
    )
    parser.add_argument(
        "--fragment-shader",
        type=Path,
        default=Path("shaders/frag.glsl"),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    with Image.open(args.outgoing) as outgoing_image:
        outgoing_pixels = rgba8(outgoing_image)
    with Image.open(args.incoming) as incoming_image:
        incoming_pixels = rgba8(incoming_image)
    if outgoing_pixels.shape != incoming_pixels.shape:
        raise ValueError("incoming and outgoing dimensions differ")
    height, width = outgoing_pixels.shape[:2]
    center_x, center_y = (float(value) for value in args.center.split(",", 1))
    center = (width * center_x, height * center_y)
    farthest = max(
        math.hypot(x - center[0], y - center[1])
        for x in (0.0, float(width))
        for y in (0.0, float(height))
    )
    regular = args.variant == "regular"
    with WalleShaderRenderer(
        width=width,
        height=height,
        vertex_shader=args.vertex_shader,
        fragment_shader=args.fragment_shader,
    ) as renderer:
        outgoing = renderer.upload_wallpaper(
            outgoing_pixels,
            regular=regular,
        )
        incoming = renderer.upload_wallpaper(
            incoming_pixels,
            regular=regular,
        )
        try:
            result = renderer.render(
                outgoing=outgoing,
                incoming=incoming,
                time=args.time,
                center_top_left=center,
                maximum_radius=farthest * 1.03,
                regular=regular,
            )
        finally:
            outgoing.release()
            incoming.release()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(result, mode="RGBA").save(args.output)
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
