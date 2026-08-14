#!/usr/bin/env python3
import json
from pathlib import Path
from PIL import Image
import numpy as np

hw_shots_dir = Path("artifacts/apple_landmark_hardware_captures_8918669614/shots")
walle_dir = Path("artifacts/liquid_glass_blog/walle_landmarks")

landmark_ids = ["1001", "1002", "1003", "1004", "1005", "1006", "1007", "1008", "1009", "1010", "1011", "1012", "1014", "1015", "1016", "1017", "1018", "1019", "1020", "1021", "1022"]

print("--- Detailed Interior Circle Comparison (Center (1600, 1000), Radius 250px) ---")
print(f"{'ID':<6} | {'HW Clear Interior RGB':<28} | {'HW Regular Interior RGB':<28} | {'Walle Interior RGB':<28}")
print("-" * 95)

y, x = np.ogrid[:2000, :3200]
circle_mask = (x - 1600)**2 + (y - 1000)**2 <= 250**2

for lid in landmark_ids:
    hw_clear_path = hw_shots_dir / f"landmark-{lid}__circle-0500-center__clear__light.png"
    hw_reg_path = hw_shots_dir / f"landmark-{lid}__circle-0500-center__regular__light.png"
    walle_path = walle_dir / f"walle_rendered_{lid}.png"
    
    if not (hw_clear_path.exists() and hw_reg_path.exists() and walle_path.exists()):
        continue
        
    hw_clear_img = Image.open(hw_clear_path).convert("RGB")
    hw_reg_img = Image.open(hw_reg_path).convert("RGB")
    walle_img = Image.open(walle_path).convert("RGB")
    
    hw_clear_rgb = np.array(hw_clear_img, dtype=np.float32)[circle_mask].mean(axis=0)
    hw_reg_rgb = np.array(hw_reg_img, dtype=np.float32)[circle_mask].mean(axis=0)
    walle_rgb = np.array(walle_img, dtype=np.float32)[circle_mask].mean(axis=0)
    
    str_hw_clear = f"[{hw_clear_rgb[0]:.1f}, {hw_clear_rgb[1]:.1f}, {hw_clear_rgb[2]:.1f}]"
    str_hw_reg = f"[{hw_reg_rgb[0]:.1f}, {hw_reg_rgb[1]:.1f}, {hw_reg_rgb[2]:.1f}]"
    str_walle = f"[{walle_rgb[0]:.1f}, {walle_rgb[1]:.1f}, {walle_rgb[2]:.1f}]"
    
    print(f"{lid:<6} | {str_hw_clear:<28} | {str_hw_reg:<28} | {str_walle:<28}")
