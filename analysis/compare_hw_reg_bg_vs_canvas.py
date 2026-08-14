#!/usr/bin/env python3
from PIL import Image
import numpy as np

hw_reg = np.array(Image.open("artifacts/apple_landmark_hardware_captures_8918669614/shots/landmark-1001__circle-0500-center__regular__light.png").convert("RGB"), dtype=np.int32)
img_raw = Image.open("artifacts/liquid_glass_blog/landmarks/1001@2x.jpg").convert("RGB")

canvas = Image.new("RGB", (3200, 2000), (0, 0, 0))
canvas.paste(img_raw, (0, 0))
canvas_arr = np.array(canvas, dtype=np.int32)

print(f"HW Regular top-left pixel (0, 0): {hw_reg[0, 0]}")
print(f"Canvas top-left pixel (0, 0):     {canvas_arr[0, 0]}")

print(f"HW Regular pixel (100, 100):     {hw_reg[100, 100]}")
print(f"Canvas pixel (100, 100):         {canvas_arr[100, 100]}")

print(f"HW Regular right-edge pixel (3100, 1000): {hw_reg[1000, 3100]}")
print(f"Canvas right-edge pixel (3100, 1000):     {canvas_arr[1000, 3100]}")

# Print mean RGB outside circle for HW Regular vs Canvas
y, x = np.ogrid[:2000, :3200]
bg_mask = (x - 1600)**2 + (y - 1000)**2 > 260**2

print(f"\nMean RGB outside circle HW Regular: {hw_reg[bg_mask].mean(axis=0)}")
print(f"Mean RGB outside circle Canvas:     {canvas_arr[bg_mask].mean(axis=0)}")
