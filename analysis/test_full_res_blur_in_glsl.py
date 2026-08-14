#!/usr/bin/env python3
import sys
import math
from pathlib import Path
import moderngl
from PIL import Image, ImageFilter
import numpy as np

sys.path.insert(0, str(Path("analysis").resolve()))
from walle_shader_renderer import WalleShaderRenderer, WallpaperTextures, prepare_glass_texture

img_raw = Image.open("artifacts/liquid_glass_blog/landmarks/1001@2x.jpg").convert("RGBA")
canvas = Image.new("RGBA", (3200, 2000), (0, 0, 0, 255))
canvas.paste(img_raw, (0, 0))

hw_reg = np.array(Image.open("artifacts/apple_landmark_hardware_captures_8918669614/shots/landmark-1001__circle-0500-center__regular__light.png").convert("RGB"), dtype=np.float32)

y, x = np.ogrid[:2000, :3200]
circle_mask = (x - 1600)**2 + (y - 1000)**2 <= 240**2

# Test rendering Walle with full-resolution 143.4px Gaussian blur
sigma = math.hypot(3200, 2000) * 0.038 # 143.45px
full_blur = canvas.filter(ImageFilter.GaussianBlur(radius=sigma))

with WalleShaderRenderer(width=3200, height=2000, fragment_shader=Path("shaders/frag.glsl")) as renderer:
    # Upload canvas as TexA and full_blur as TexGlassB
    tex_std = renderer.context.texture(canvas.size, 4, np.asarray(canvas, dtype=np.uint8).tobytes())
    tex_glass = renderer.context.texture(full_blur.size, 4, np.asarray(full_blur, dtype=np.uint8).tobytes())
    
    textures = WallpaperTextures(standard=tex_std, glass=tex_glass, width=3200, height=2000)
    
    # Custom render passing textures
    renderer.program["TexA"].value = 0
    renderer.program["TexGlassA"].value = 1
    renderer.program["TexB"].value = 2
    renderer.program["TexGlassB"].value = 3

    textures.standard.use(location=0)
    textures.standard.use(location=1)
    textures.standard.use(location=2)
    textures.glass.use(location=3)

    renderer.program["Time"].value = 0.62
    renderer.program["Resolution"].value = (3200.0, 2000.0)
    renderer.program["CenterPointPixels"].value = (1600.0, 1000.0)
    renderer.program["MaxRadiusPixels"].value = 250.0
    renderer.program["Variant"].value = 1.0

    renderer.framebuffer.use()
    renderer.context.clear(0, 0, 0, 0)
    renderer.vertex_array.render(moderngl.TRIANGLE_STRIP)

    rendered_rgba = np.frombuffer(renderer.framebuffer.read(components=4), dtype=np.uint8).reshape((2000, 3200, 4))
    textures.release()

walle_rendered = rendered_rgba[:, :, :3].astype(np.float32)

diff = np.abs(walle_rendered - hw_reg)
print(f"Full-Res Blur GLSL Render vs Apple Native HW Capture (Circle Interior MAE): {diff[circle_mask].mean():.2f}")
print(f"Full-Res Blur GLSL Render vs Apple Native HW Capture (Full Canvas MAE):     {diff.mean():.2f}")
