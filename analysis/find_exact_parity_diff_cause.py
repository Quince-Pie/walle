#!/usr/bin/env python3
import numpy as np
from PIL import Image
from pathlib import Path

hw_path = Path("artifacts/apple_landmark_hardware_captures_8918669614/shots/landmark-1001__circle-0500-center__regular__light.png")
walle_path = Path("artifacts/liquid_glass_blog/walle_landmarks/walle_rendered_1001.png")

hw_img = np.array(Image.open(hw_path).convert("RGBA"), dtype=np.int32)
walle_img = np.array(Image.open(walle_path).convert("RGBA"), dtype=np.int32)

print(f"HW Capture Size:    {hw_img.shape}")
print(f"Walle Render Size:  {walle_img.shape}")

diff = np.abs(walle_img - hw_img)

y, x = np.ogrid[:2000, :3200]
circle_mask = (x - 1600)**2 + (y - 1000)**2 <= 245**2 # Inside platter
outside_mask = ((x - 1600)**2 + (y - 1000)**2 >= 260**2) & (x < 3000) # Outside platter, inside photo

print("\n--- Difference Analysis ---")
print(f"Outside Platter Max Delta: {diff[outside_mask].max()} (Mean: {diff[outside_mask].mean():.4f})")
print(f"Inside Platter Max Delta:  {diff[circle_mask].max()} (Mean: {diff[circle_mask].mean():.4f})")

# Print sample pixel values at center (1600, 1000)
print(f"\nCenter Pixel (1600, 1000):")
print(f"  HW Capture:   R={hw_img[1000, 1600, 0]}, G={hw_img[1000, 1600, 1]}, B={hw_img[1000, 1600, 2]}, A={hw_img[1000, 1600, 3]}")
print(f"  Walle Render: R={walle_img[1000, 1600, 0]}, G={walle_img[1000, 1600, 1]}, B={walle_img[1000, 1600, 2]}, A={walle_img[1000, 1600, 3]}")

# Print backdrop pre-blur values
ref_img = np.array(Image.open("artifacts/apple_landmark_hardware_captures_8918669614/reference/landmark-1001.png").convert("RGB"), dtype=np.int32)
print(f"\nReference Photo at Center (1600, 1000): R={ref_img[1000, 1600, 0]}, G={ref_img[1000, 1600, 1]}, B={ref_img[1000, 1600, 2]}")
