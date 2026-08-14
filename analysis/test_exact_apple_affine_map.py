#!/usr/bin/env python3
import math
from PIL import Image, ImageFilter
import numpy as np

img = Image.open("artifacts/liquid_glass_blog/landmarks/1001@2x.jpg").convert("RGBA")
canvas = Image.new("RGBA", (3200, 2000), (0, 0, 0, 255))
canvas.paste(img, (0, 0))

width, height = canvas.size
sigma = math.hypot(width, height) * 0.013

pil_blur = canvas.filter(ImageFilter.GaussianBlur(radius=sigma))
pil_arr = np.array(pil_blur.convert("RGB"), dtype=np.float32)

hw_clear_img = Image.open("artifacts/apple_landmark_hardware_captures_8918669614/shots/landmark-1001__circle-0500-center__clear__light.png").convert("RGB")
hw_clear_arr = np.array(hw_clear_img, dtype=np.float32)

y, x = np.ogrid[:2000, :3200]
circle_mask = (x - 1600)**2 + (y - 1000)**2 <= 240**2

# Fit linear affine map: HW_Clear = a * Blur + b
blur_c = pil_arr[circle_mask]
hw_c = hw_clear_arr[circle_mask]

# Solve least-squares linear fit per channel
a_r, b_r = np.polyfit(blur_c[:, 0], hw_c[:, 0], 1)
a_g, b_g = np.polyfit(blur_c[:, 1], hw_c[:, 1], 1)
a_b, b_b = np.polyfit(blur_c[:, 2], hw_c[:, 2], 1)

print(f"Fitted linear map Red:   HW_R = {a_r:.4f} * Blur_R + {b_r:.2f}")
print(f"Fitted linear map Green: HW_G = {a_g:.4f} * Blur_G + {b_g:.2f}")
print(f"Fitted linear map Blue:  HW_B = {a_b:.4f} * Blur_B + {b_b:.2f}")

fit_sim = np.zeros_like(pil_arr)
fit_sim[:, :, 0] = pil_arr[:, :, 0] * a_r + b_r
fit_sim[:, :, 1] = pil_arr[:, :, 1] * a_g + b_g
fit_sim[:, :, 2] = pil_arr[:, :, 2] * a_b + b_b
fit_sim = np.clip(fit_sim, 0, 255)

mae_fitted = np.abs(fit_sim[circle_mask] - hw_c).mean()
print(f"\nFitted Affine Map vs Apple Native HW Clear (Circle Interior MAE): {mae_fitted:.2f}")
print(f"Fitted Mean RGB inside circle: {fit_sim[circle_mask].mean(axis=0)}")
print(f"Apple Native HW Clear Mean RGB inside circle: {hw_c.mean(axis=0)}")
