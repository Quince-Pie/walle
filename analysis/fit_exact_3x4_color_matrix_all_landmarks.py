#!/usr/bin/env python3
import math
from pathlib import Path
from PIL import Image, ImageFilter
import numpy as np

landmarks_dir = Path("artifacts/liquid_glass_blog/landmarks")
hw_shots_dir = Path("artifacts/apple_landmark_hardware_captures_8918669614/shots")

landmark_files = sorted(landmarks_dir.glob("*.jpg"))

print("Fitting 3x4 Color Transformation Matrix across ALL 21 Landmark Photo Wallpapers...")

y, x = np.ogrid[:2000, :3200]
circle_mask = (x - 1600)**2 + (y - 1000)**2 <= 240**2

all_blur = []
all_hw = []

sigma_reg = math.hypot(3200, 2000) * 0.038

for bg_file in landmark_files:
    lid = bg_file.name.split("@")[0]
    hw_reg_path = hw_shots_dir / f"landmark-{lid}__circle-0500-center__regular__light.png"
    if not hw_reg_path.exists():
        continue
        
    img_raw = Image.open(bg_file).convert("RGBA")
    canvas = Image.new("RGBA", (3200, 2000), (0, 0, 0, 255))
    canvas.paste(img_raw, (0, 0))
    
    blur_reg = np.array(canvas.filter(ImageFilter.GaussianBlur(radius=sigma_reg)).convert("RGB"), dtype=np.float32) / 255.0
    hw_reg_arr = np.array(Image.open(hw_reg_path).convert("RGB"), dtype=np.float32) / 255.0
    
    all_blur.append(blur_reg[circle_mask])
    all_hw.append(hw_reg_arr[circle_mask])

blur_concat = np.vstack(all_blur) # Shape: (N, 3)
hw_concat = np.vstack(all_hw)     # Shape: (N, 3)

# Add constant 1.0 column for 3x4 affine matrix fit: [Blur_R, Blur_G, Blur_B, 1.0]
X = np.hstack([blur_concat, np.ones((blur_concat.shape[0], 1), dtype=np.float32)]) # Shape: (N, 4)

# Solve least-square: X @ M = HW -> M shape: (4, 3)
M, _, _, _ = np.linalg.lstsq(X, hw_concat, rcond=None)

print("\n--- Derived 3x4 Color Matrix M (4x3) ---")
print(f"mat3 ColorMatrix = mat3(")
print(f"    vec3({M[0,0]:.5f}, {M[0,1]:.5f}, {M[0,2]:.5f}),")
print(f"    vec3({M[1,0]:.5f}, {M[1,1]:.5f}, {M[1,2]:.5f}),")
print(f"    vec3({M[2,0]:.5f}, {M[2,1]:.5f}, {M[2,2]:.5f})")
print(f");")
print(f"vec3 ColorOffset = vec3({M[3,0]:.5f}, {M[3,1]:.5f}, {M[3,2]:.5f});")

# Predict and compute MAE across all 21 landmarks
pred = np.clip(X @ M, 0.0, 1.0)
global_mae = np.abs((pred - hw_concat) * 255.0).mean()

print(f"\nGlobal 3x4 Color Matrix MAE across ALL 21 Landmark Wallpapers: {global_mae:.2f} RGB units")
