#!/usr/bin/env python3
from pathlib import Path
from PIL import Image
import numpy as np

ref_path = Path("artifacts/apple_landmark_hardware_captures_8918669614/reference/landmark-1001.png")
shot_path = Path("artifacts/apple_landmark_hardware_captures_8918669614/shots/landmark-1001__circle-0500-center__regular__light.png")
walle_path = Path("artifacts/liquid_glass_blog/walle_landmarks/walle_rendered_1001.png")

shot_img = Image.open(shot_path).convert("RGB")
ref_img = Image.open(ref_path).convert("RGB").resize(shot_img.size, Image.Resampling.LANCZOS)
walle_img = Image.open(walle_path).convert("RGB")

ref = np.array(ref_img, dtype=np.float32)
shot = np.array(shot_img, dtype=np.float32)
walle = np.array(walle_img, dtype=np.float32)

diff_hw = np.abs(shot - ref)
diff_walle = np.abs(walle - ref)

print("--- Inside Circle Analysis (Center (1600, 1000), Radius 250px) ---")
y, x = np.ogrid[:2000, :3200]
dist_from_center = np.sqrt((x - 1600)**2 + (y - 1000)**2)
circle_mask = dist_from_center <= 250

ref_circle = ref[circle_mask]
shot_circle = shot[circle_mask]
walle_circle = walle[circle_mask]

print(f"Reference Mean RGB inside circle: {ref_circle.mean(axis=0)}")
print(f"Apple Native Hardware Mean RGB inside circle: {shot_circle.mean(axis=0)}")
print(f"Walle Rendered Mean RGB inside circle: {walle_circle.mean(axis=0)}")

hw_ref_diff = np.abs(shot_circle - ref_circle).mean()
walle_ref_diff = np.abs(walle_circle - ref_circle).mean()

print(f"Apple Native Hardware vs Raw Photo Un-refracted Diff: {hw_ref_diff:.2f}")
print(f"Walle Rendered vs Raw Photo Un-refracted Diff: {walle_ref_diff:.2f}")

# Save cropped circle crops for direct visual inspection
crop_box = (1600 - 300, 1000 - 300, 1600 + 300, 1000 + 300)
ref_crop = ref_img.crop(crop_box)
shot_crop = shot_img.crop(crop_box)
walle_crop = walle_img.crop(crop_box)

out_dir = Path("artifacts/liquid_glass_blog/crop_analysis")
out_dir.mkdir(parents=True, exist_ok=True)

ref_crop.save(out_dir / "1001_raw_photo_crop.png")
shot_crop.save(out_dir / "1001_apple_native_crop.png")
walle_crop.save(out_dir / "1001_walle_rendered_crop.png")

print(f"Saved crops to {out_dir}: 1001_raw_photo_crop.png, 1001_apple_native_crop.png, 1001_walle_rendered_crop.png")
