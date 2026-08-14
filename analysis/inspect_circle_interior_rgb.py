#!/usr/bin/env python3
from pathlib import Path
from PIL import Image
import numpy as np

hw_reg_img = Image.open("artifacts/apple_landmark_hardware_captures_8918669614/shots/landmark-1001__circle-0500-center__regular__light.png").convert("RGB")
hw_clear_img = Image.open("artifacts/apple_landmark_hardware_captures_8918669614/shots/landmark-1001__circle-0500-center__clear__light.png").convert("RGB")
ref_img = Image.open("artifacts/apple_landmark_hardware_captures_8918669614/reference/landmark-1001.png").convert("RGB").resize((3200, 2000), Image.Resampling.LANCZOS)
walle_img = Image.open("artifacts/liquid_glass_blog/walle_landmarks/walle_rendered_1001.png").convert("RGB")

hw_reg = np.array(hw_reg_img, dtype=np.float32)
hw_clear = np.array(hw_clear_img, dtype=np.float32)
ref = np.array(ref_img, dtype=np.float32)
walle = np.array(walle_img, dtype=np.float32)

y, x = np.ogrid[:2000, :3200]
circle_mask = (x - 1600)**2 + (y - 1000)**2 <= 250**2

print(f"Reference Photo Mean RGB inside circle:        {ref[circle_mask].mean(axis=0)}")
print(f"Apple Native HW Regular Mean RGB inside circle: {hw_reg[circle_mask].mean(axis=0)}")
print(f"Apple Native HW Clear Mean RGB inside circle:   {hw_clear[circle_mask].mean(axis=0)}")
print(f"Walle Clear Mean RGB inside circle:             {walle[circle_mask].mean(axis=0)}")
