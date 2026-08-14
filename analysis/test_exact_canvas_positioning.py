#!/usr/bin/env python3
import sys
from pathlib import Path
from PIL import Image
import numpy as np

sys.path.insert(0, str(Path("analysis").resolve()))
from walle_shader_renderer import WalleShaderRenderer

bg_file = Path("artifacts/liquid_glass_blog/landmarks/1001@2x.jpg")
img_raw = Image.open(bg_file).convert("RGBA")
print(f"Raw 1001@2x.jpg size: {img_raw.size}") # (3000, 2000)

# Create 3200x2000 canvas with black padding on the right (matching LandmarkImageLoader in GlassCapture)
canvas = Image.new("RGBA", (3200, 2000), (0, 0, 0, 255))
canvas.paste(img_raw, (0, 0)) # Paste 3000x2000 image at top-left (0,0)

w, h = canvas.size # (3200, 2000)

hw_reg_path = Path("artifacts/apple_landmark_hardware_captures_8918669614/shots/landmark-1001__circle-0500-center__regular__light.png")
hw_clear_path = Path("artifacts/apple_landmark_hardware_captures_8918669614/shots/landmark-1001__circle-0500-center__clear__light.png")

hw_reg = np.array(Image.open(hw_reg_path).convert("RGB"), dtype=np.int32)
hw_clear = np.array(Image.open(hw_clear_path).convert("RGB"), dtype=np.int32)

# Render Walle with exact 3200x2000 canvas padding
with WalleShaderRenderer(width=w, height=h, fragment_shader=Path("shaders/frag.glsl")) as renderer:
    tex = renderer.upload_wallpaper(canvas, regular=True)
    walle_reg = renderer.render(
        outgoing=tex, incoming=tex, time=0.62,
        center_top_left=(1600.0, 1000.0), maximum_radius=250.0, regular=True
    )[:, :, :3].astype(np.int32)

with WalleShaderRenderer(width=w, height=h, fragment_shader=Path("shaders/frag.glsl")) as renderer:
    tex = renderer.upload_wallpaper(canvas, regular=False)
    walle_clear = renderer.render(
        outgoing=tex, incoming=tex, time=0.62,
        center_top_left=(1600.0, 1000.0), maximum_radius=250.0, regular=False
    )[:, :, :3].astype(np.int32)

print("\n--- Outer Background Region Delta (Outside Circle) ---")
y, x = np.ogrid[:2000, :3200]
circle_mask = (x - 1600)**2 + (y - 1000)**2 <= 260**2
bg_mask = ~circle_mask

diff_bg_reg = np.abs(walle_reg[bg_mask] - hw_reg[bg_mask])
diff_bg_clear = np.abs(walle_clear[bg_mask] - hw_clear[bg_mask])

print(f"Walle Regular vs HW Regular outside circle Max Delta: {diff_bg_reg.max()}, MAE: {diff_bg_reg.mean():.4f}")
print(f"Walle Clear vs HW Clear outside circle Max Delta: {diff_bg_clear.max()}, MAE: {diff_bg_clear.mean():.4f}")

# Check exact pixel matching
exact_bg_reg = (diff_bg_reg == 0).sum()
exact_bg_clear = (diff_bg_clear == 0).sum()
total_bg = bg_mask.sum() * 3

print(f"\nExact 0-delta pixel match percentage outside circle (Regular): {exact_bg_reg / total_bg * 100:.2f}%")
print(f"Exact 0-delta pixel match percentage outside circle (Clear): {exact_bg_clear / total_bg * 100:.2f}%")
