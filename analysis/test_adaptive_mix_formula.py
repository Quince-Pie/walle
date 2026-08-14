#!/usr/bin/env python3
import math
from pathlib import Path
from PIL import Image, ImageFilter
import numpy as np

landmarks_dir = Path("artifacts/liquid_glass_blog/landmarks")
hw_shots_dir = Path("artifacts/apple_landmark_hardware_captures_8918669614/shots")

landmark_files = sorted(landmarks_dir.glob("*.jpg"))

y, x = np.ogrid[:2000, :3200]
circle_mask = (x - 1600)**2 + (y - 1000)**2 <= 240**2

print("Testing adaptive blending formula: plat_s = wash_s * a + (1-a) * Y_wash + offset...")

for mix_fac in [0.20, 0.35, 0.50, 0.65]:
    maes = []
    for bg_file in landmark_files:
        lid = bg_file.name.split("@")[0]
        hw_reg_path = hw_shots_dir / f"landmark-{lid}__circle-0500-center__regular__light.png"
        if not hw_reg_path.exists():
            continue
            
        img_raw = Image.open(bg_file).convert("RGBA")
        canvas = Image.new("RGBA", (3200, 2000), (0, 0, 0, 255))
        canvas.paste(img_raw, (0, 0))
        
        sigma_reg = math.hypot(3200, 2000) * 0.038
        blur_reg = np.array(canvas.filter(ImageFilter.GaussianBlur(radius=sigma_reg)).convert("RGB"), dtype=np.float32) / 255.0
        hw_reg_arr = np.array(Image.open(hw_reg_path).convert("RGB"), dtype=np.float32) / 255.0
        
        # Adaptive formula: plat_s = mix(wash_s, vec3(0.98), mix_fac * (1.0 - wash_s))
        plat_s = blur_reg * (1.0 - mix_fac) + 0.98 * mix_fac
        
        mae = np.abs((plat_s[circle_mask] - hw_reg_arr[circle_mask]) * 255.0).mean()
        maes.append(mae)
        
    print(f"Mix Factor {mix_fac:.2f} -> Mean Interior MAE across all 21 landmarks: {np.mean(maes):.2f} RGB units")
