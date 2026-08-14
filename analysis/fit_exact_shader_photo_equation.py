#!/usr/bin/env python3
import math
from pathlib import Path
from PIL import Image, ImageFilter
import numpy as np

landmarks_dir = Path("artifacts/liquid_glass_blog/landmarks")
hw_shots_dir = Path("artifacts/apple_landmark_hardware_captures_8918669614/shots")

landmark_files = sorted(landmarks_dir.glob("*.jpg"))

print("Analyzing exact mapping from (ref, blur) -> Apple Native HW Capture across all 21 landmark wallpapers...")

y, x = np.ogrid[:2000, :3200]
circle_mask = (x - 1600)**2 + (y - 1000)**2 <= 240**2

all_blur_r, all_blur_g, all_blur_b = [], [], []
all_hw_r, all_hw_g, all_hw_b = [], [], []

for bg_file in landmark_files:
    lid = bg_file.name.split("@")[0]
    hw_reg_path = hw_shots_dir / f"landmark-{lid}__circle-0500-center__regular__light.png"
    if not hw_reg_path.exists():
        continue
        
    img_raw = Image.open(bg_file).convert("RGBA")
    canvas = Image.new("RGBA", (3200, 2000), (0, 0, 0, 255))
    canvas.paste(img_raw, (0, 0))
    
    # Pre-blur for Regular material (sigma = 0.038 * diag)
    sigma_reg = math.hypot(3200, 2000) * 0.038
    blur_reg = np.array(canvas.filter(ImageFilter.GaussianBlur(radius=sigma_reg)).convert("RGB"), dtype=np.float32)
    hw_reg_arr = np.array(Image.open(hw_reg_path).convert("RGB"), dtype=np.float32)
    
    all_blur_r.extend(blur_reg[circle_mask, 0])
    all_blur_g.extend(blur_reg[circle_mask, 1])
    all_blur_b.extend(blur_reg[circle_mask, 2])
    
    all_hw_r.extend(hw_reg_arr[circle_mask, 0])
    all_hw_g.extend(hw_reg_arr[circle_mask, 1])
    all_hw_b.extend(hw_reg_arr[circle_mask, 2])

blur_r = np.array(all_blur_r)
blur_g = np.array(all_blur_g)
blur_b = np.array(all_blur_b)

hw_r = np.array(all_hw_r)
hw_g = np.array(all_hw_g)
hw_b = np.array(all_hw_b)

# Polynomial fit degree 1 and 2
p1_r = np.polyfit(blur_r, hw_r, 1)
p1_g = np.polyfit(blur_g, hw_g, 1)
p1_b = np.polyfit(blur_b, hw_b, 1)

p2_r = np.polyfit(blur_r, hw_r, 2)
p2_g = np.polyfit(blur_g, hw_g, 2)
p2_b = np.polyfit(blur_b, hw_b, 2)

print(f"\n--- Degree 1 Linear Fit across all 21 Landmarks ---")
print(f"Red:   HW_R = {p1_r[0]:.5f} * Blur_R + {p1_r[1]:.2f}")
print(f"Green: HW_G = {p1_g[0]:.5f} * Blur_G + {p1_g[1]:.2f}")
print(f"Blue:  HW_B = {p1_b[0]:.5f} * Blur_B + {p1_b[1]:.2f}")

print(f"\n--- Degree 2 Quadratic Fit across all 21 Landmarks ---")
print(f"Red:   HW_R = {p2_r[0]:.7f} * Blur_R^2 + {p2_r[1]:.5f} * Blur_R + {p2_r[2]:.2f}")
print(f"Green: HW_G = {p2_g[0]:.7f} * Blur_G^2 + {p2_g[1]:.5f} * Blur_G + {p2_g[2]:.2f}")
print(f"Blue:  HW_B = {p2_b[0]:.7f} * Blur_B^2 + {p2_b[1]:.5f} * Blur_B + {p2_b[2]:.2f}")

# Calculate global MAE with Degree 2 Quadratic Fit
fit2_r = np.clip(p2_r[0] * blur_r**2 + p2_r[1] * blur_r + p2_r[2], 0, 255)
fit2_g = np.clip(p2_g[0] * blur_g**2 + p2_g[1] * blur_g + p2_g[2], 0, 255)
fit2_b = np.clip(p2_b[0] * blur_b**2 + p2_b[1] * blur_b + p2_b[2], 0, 255)

mae_quad = (np.abs(fit2_r - hw_r) + np.abs(fit2_g - hw_g) + np.abs(fit2_b - hw_b)).mean() / 3.0
print(f"\nGlobal Quadratic Fit Platter MAE across ALL 21 Landmarks: {mae_quad:.2f} RGB units")
