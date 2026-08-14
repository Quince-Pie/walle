#!/usr/bin/env python3
import sys
from pathlib import Path
from PIL import Image
import numpy as np

sys.path.insert(0, str(Path("analysis").resolve()))
from walle_shader_renderer import WalleShaderRenderer

bg_file = Path("artifacts/liquid_glass_blog/landmarks/1001@2x.jpg")
img = Image.open(bg_file).convert("RGBA").resize((3200, 2000), Image.Resampling.LANCZOS)
w, h = img.size

hw_regular_path = Path("artifacts/apple_landmark_hardware_captures_8918669614/shots/landmark-1001__circle-0500-center__regular__light.png")
hw_clear_path = Path("artifacts/apple_landmark_hardware_captures_8918669614/shots/landmark-1001__circle-0500-center__clear__light.png")

hw_regular = np.array(Image.open(hw_regular_path).convert("RGB"), dtype=np.float32)
hw_clear = np.array(Image.open(hw_clear_path).convert("RGB"), dtype=np.float32)

# 1. Render Walle with regular=True (Variant 1.0)
with WalleShaderRenderer(width=w, height=h, fragment_shader=Path("shaders/frag.glsl")) as renderer:
    tex = renderer.upload_wallpaper(img, regular=True)
    walle_regular = renderer.render(
        outgoing=tex, incoming=tex, time=0.62,
        center_top_left=(1600.0, 1000.0), maximum_radius=250.0, regular=True
    )[:, :, :3].astype(np.float32)

# 2. Render Walle with regular=False (Variant 0.0 - Clear)
with WalleShaderRenderer(width=w, height=h, fragment_shader=Path("shaders/frag.glsl")) as renderer:
    tex = renderer.upload_wallpaper(img, regular=False)
    walle_clear = renderer.render(
        outgoing=tex, incoming=tex, time=0.62,
        center_top_left=(1600.0, 1000.0), maximum_radius=250.0, regular=False
    )[:, :, :3].astype(np.float32)

diff_reg_vs_hw_reg = np.abs(walle_regular - hw_regular).mean()
diff_clear_vs_hw_clear = np.abs(walle_clear - hw_clear).mean()
diff_clear_vs_hw_reg = np.abs(walle_clear - hw_regular).mean()

print(f"Walle Regular vs Hardware Regular MAE: {diff_reg_vs_hw_reg:.2f}")
print(f"Walle Clear vs Hardware Clear MAE: {diff_clear_vs_hw_clear:.2f}")
print(f"Walle Clear vs Hardware Regular MAE: {diff_clear_vs_hw_reg:.2f}")

# Circle interior MAE
y, x = np.ogrid[:2000, :3200]
circle_mask = (x - 1600)**2 + (y - 1000)**2 <= 250**2

print("\n--- Circle Interior Only MAE ---")
print(f"Walle Regular vs Hardware Regular (Circle Interior MAE): {np.abs(walle_regular[circle_mask] - hw_regular[circle_mask]).mean():.2f}")
print(f"Walle Clear vs Hardware Clear (Circle Interior MAE): {np.abs(walle_clear[circle_mask] - hw_clear[circle_mask]).mean():.2f}")
