#!/usr/bin/env python3
import sys
from pathlib import Path
from PIL import Image
import numpy as np

sys.path.insert(0, str(Path("analysis").resolve()))
from walle_shader_renderer import WalleShaderRenderer

bg_file = Path("artifacts/liquid_glass_blog/landmarks/1001@2x.jpg")
img_raw = Image.open(bg_file).convert("RGBA")

canvas = Image.new("RGBA", (3200, 2000), (0, 0, 0, 255))
canvas.paste(img_raw, (0, 0))

hw_reg = np.array(Image.open("artifacts/apple_landmark_hardware_captures_8918669614/shots/landmark-1001__circle-0500-center__regular__light.png").convert("RGB"), dtype=np.float32)
hw_clear = np.array(Image.open("artifacts/apple_landmark_hardware_captures_8918669614/shots/landmark-1001__circle-0500-center__clear__light.png").convert("RGB"), dtype=np.float32)

with WalleShaderRenderer(width=3200, height=2000, fragment_shader=Path("shaders/frag.glsl")) as renderer:
    tex = renderer.upload_wallpaper(canvas, regular=True)
    walle_reg = renderer.render(
        outgoing=tex, incoming=tex, time=0.62,
        center_top_left=(1600.0, 1000.0), maximum_radius=250.0, regular=True
    )[:, :, :3].astype(np.float32)

with WalleShaderRenderer(width=3200, height=2000, fragment_shader=Path("shaders/frag.glsl")) as renderer:
    tex = renderer.upload_wallpaper(canvas, regular=False)
    walle_clear = renderer.render(
        outgoing=tex, incoming=tex, time=0.62,
        center_top_left=(1600.0, 1000.0), maximum_radius=250.0, regular=False
    )[:, :, :3].astype(np.float32)

y, x = np.ogrid[:2000, :3200]
circle_mask = (x - 1600)**2 + (y - 1000)**2 <= 240**2 # 240px radius interior

print("--- Inside Glass Platter (Circle Interior r <= 240px) ---")
print(f"HW Regular Mean RGB:   {hw_reg[circle_mask].mean(axis=0)}")
print(f"Walle Regular Mean RGB: {walle_reg[circle_mask].mean(axis=0)}")
print(f"Regular Interior MAE:   {np.abs(walle_reg[circle_mask] - hw_reg[circle_mask]).mean():.2f}")

print(f"\nHW Clear Mean RGB:     {hw_clear[circle_mask].mean(axis=0)}")
print(f"Walle Clear Mean RGB:   {walle_clear[circle_mask].mean(axis=0)}")
print(f"Clear Interior MAE:     {np.abs(walle_clear[circle_mask] - hw_clear[circle_mask]).mean():.2f}")
