#!/usr/bin/env python3
import math
from pathlib import Path
from PIL import Image, ImageFilter
import numpy as np

hw_reg_path = Path("artifacts/apple_landmark_hardware_captures_8918669614/shots/landmark-1001__circle-0500-center__regular__light.png")
ref_path = Path("artifacts/apple_landmark_hardware_captures_8918669614/reference/landmark-1001.png")

hw_reg = np.array(Image.open(hw_reg_path).convert("RGB"), dtype=np.float32)
img_raw = Image.open(ref_path).convert("RGBA")

canvas = Image.new("RGBA", (3200, 2000), (0, 0, 0, 255))
canvas.paste(img_raw, (0, 0))

y, x = np.ogrid[:2000, :3200]
circle_mask = (x - 1600)**2 + (y - 1000)**2 <= 240**2

print("Sweeping blur sigmas and blend parameters for exact pixel matching on Landmark 1001...")

best_mae = 9999.0
best_params = None

# Sweep blur sigmas (from 20px to 160px) and blend multipliers/offsets
for sigma in np.linspace(20, 160, 15):
    blur_img = canvas.filter(ImageFilter.GaussianBlur(radius=sigma))
    blur_arr = np.array(blur_img.convert("RGB"), dtype=np.float32)
    
    # Test linear fit: HW = mult * Blur + offset
    b_c = blur_arr[circle_mask]
    h_c = hw_reg[circle_mask]
    
    # Solve 3x3 matrix for RGB color transform: HW = Blur @ M + B
    M, _, _, _ = np.linalg.lstsq(b_c, h_c, rcond=None)
    pred = b_c @ M
    mae = np.abs(pred - h_c).mean()
    
    if mae < best_mae:
        best_mae = mae
        best_params = (sigma, M)

print(f"\nBest Blur Sigma: {best_params[0]:.2f}px -> Interior MAE: {best_mae:.4f}")
print("Optimal 3x3 Color Matrix M:")
print(best_params[1])
