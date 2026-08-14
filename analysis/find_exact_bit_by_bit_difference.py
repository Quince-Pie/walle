#!/usr/bin/env python3
import numpy as np
from PIL import Image
from pathlib import Path

hw_reg_path = Path("artifacts/apple_landmark_hardware_captures_8918669614/shots/landmark-1001__circle-0500-center__regular__light.png")
hw_clear_path = Path("artifacts/apple_landmark_hardware_captures_8918669614/shots/landmark-1001__circle-0500-center__clear__light.png")
ref_path = Path("artifacts/apple_landmark_hardware_captures_8918669614/reference/landmark-1001.png")

hw_reg = np.array(Image.open(hw_reg_path).convert("RGB"), dtype=np.int32)
hw_clear = np.array(Image.open(hw_clear_path).convert("RGB"), dtype=np.int32)
ref = Image.open(ref_path).convert("RGB")

print(f"HW Regular shape: {hw_reg.shape}")
print(f"HW Clear shape: {hw_clear.shape}")
print(f"Reference shape: {ref.size}")

# Check outer background region (outside glass circle)
y, x = np.ogrid[:2000, :3200]
circle_mask = (x - 1600)**2 + (y - 1000)**2 <= 260**2
bg_mask = ~circle_mask

print("\n--- Outer Background Region (Outside Glass Circle) ---")
hw_reg_bg = hw_reg[bg_mask]
hw_clear_bg = hw_clear[bg_mask]

bg_diff = np.abs(hw_reg_bg - hw_clear_bg)
print(f"HW Regular vs HW Clear outside circle Max Delta: {bg_diff.max()}, MAE: {bg_diff.mean():.4f}")

# Check reference wallpaper background vs HW capture background
ref_resized = np.array(ref.resize((3200, 2000), Image.Resampling.LANCZOS), dtype=np.int32)
ref_bg = ref_resized[bg_mask]
diff_ref_hw = np.abs(hw_reg_bg - ref_bg)
print(f"HW Regular vs Reference Wallpaper outside circle Max Delta: {diff_ref_hw.max()}, MAE: {diff_ref_hw.mean():.4f}")

# Analyze non-zero deltas outside circle between HW capture and Raw Photo
diff_mask = diff_ref_hw > 0
print(f"Number of pixels outside circle where HW capture != Raw photo: {diff_mask.sum()} / {bg_mask.sum()} ({diff_mask.sum() / bg_mask.sum() * 100:.2f}%)")
if diff_mask.sum() > 0:
    print(f"Sample pixel deltas: HW capture={hw_reg_bg[diff_mask][:5]}, Raw photo={ref_bg[diff_mask][:5]}")
