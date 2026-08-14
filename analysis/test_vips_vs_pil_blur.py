#!/usr/bin/env python3
import math
from PIL import Image, ImageFilter
import numpy as np

img = Image.open("artifacts/liquid_glass_blog/landmarks/1001@2x.jpg").convert("RGBA")
canvas = Image.new("RGBA", (3200, 2000), (0, 0, 0, 255))
canvas.paste(img, (0, 0))

width, height = canvas.size
sigma = math.hypot(width, height) * 0.013 # GLASS_SIGMA_FRAC_CLEAR = 0.013 -> sigma = 49.0px

print(f"Blur sigma: {sigma:.2f}px")

# PIL GaussianBlur
pil_blur = canvas.filter(ImageFilter.GaussianBlur(radius=sigma))
pil_arr = np.array(pil_blur.convert("RGB"), dtype=np.float32)

y, x = np.ogrid[:2000, :3200]
circle_mask = (x - 1600)**2 + (y - 1000)**2 <= 240**2

print(f"PIL Blur Mean RGB inside circle: {pil_arr[circle_mask].mean(axis=0)}")

hw_clear_img = Image.open("artifacts/apple_landmark_hardware_captures_8918669614/shots/landmark-1001__circle-0500-center__clear__light.png").convert("RGB")
hw_clear_arr = np.array(hw_clear_img, dtype=np.float32)

print(f"Apple Native HW Clear Mean RGB inside circle: {hw_clear_arr[circle_mask].mean(axis=0)}")

# Apply affine veil: out = 0.494 * blur + 0.267 * 255 (68.085)
veil_sim = np.clip(pil_arr * 0.494 + 68.085, 0, 255)
print(f"PIL Blur + Affine Veil Mean RGB inside circle: {veil_sim[circle_mask].mean(axis=0)}")

mae_veil_vs_hw = np.abs(veil_sim[circle_mask] - hw_clear_arr[circle_mask]).mean()
print(f"PIL Blur + Affine Veil vs Apple Native HW Clear (Circle Interior MAE): {mae_veil_vs_hw:.2f}")
