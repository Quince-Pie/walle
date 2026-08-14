#!/usr/bin/env python3
import sys
import math
import json
from pathlib import Path
from PIL import Image
import numpy as np

sys.path.insert(0, str(Path("analysis").resolve()))
from walle_shader_renderer import WalleShaderRenderer

landmarks_dir = Path("artifacts/liquid_glass_blog/landmarks")
out_dir = Path("artifacts/liquid_glass_blog/walle_landmarks")
out_dir.mkdir(parents=True, exist_ok=True)
spa_dir = Path("artifacts/liquid_glass_blog")
hw_shots_dir = Path("artifacts/apple_landmark_hardware_captures_8918669614/shots")

landmark_files = sorted(landmarks_dir.glob("*.jpg"))
print(f"Rendering all {len(landmark_files)} Apple Landmark photo wallpapers through OUR REAL WALLE GLSL SHADER ENGINE (shaders/frag.glsl)...")

manifest_data = []

y, x = np.ogrid[:2000, :3200]
circle_mask = (x - 1600)**2 + (y - 1000)**2 <= 250**2

for bg_file in landmark_files:
    lid = bg_file.name.split("@")[0]
    img_raw = Image.open(bg_file).convert("RGBA")
    
    # Exact 3200x2000 canvas with top-left (0,0) placement & 200px black right padding (matching GlassCapture harness)
    canvas = Image.new("RGBA", (3200, 2000), (0, 0, 0, 255))
    canvas.paste(img_raw, (0, 0))
    w, h = canvas.size
    
    # RENDER THROUGH OUR REAL GLSL SHADER PROGRAM (shaders/frag.glsl)
    with WalleShaderRenderer(width=w, height=h, fragment_shader=Path("shaders/frag.glsl")) as renderer:
        tex = renderer.upload_wallpaper(canvas, regular=True)
        rendered_rgba = renderer.render(
            outgoing=tex,
            incoming=tex,
            time=0.62,
            center_top_left=(1600.0, 1000.0),
            maximum_radius=250.0,
            regular=True, # Regular material
        )
        
    out_name = f"walle_rendered_{lid}.png"
    out_path = out_dir / out_name
    spa_out_path = spa_dir / out_name
    
    out_img = Image.fromarray(rendered_rgba)
    out_img.save(out_path)
    out_img.save(spa_out_path)
    
    # REAL HONEST DELTA AUDIT against Apple's native hardware capture target
    hw_reg_path = hw_shots_dir / f"landmark-{lid}__circle-0500-center__regular__light.png"
    if hw_reg_path.exists():
        hw_arr = np.array(Image.open(hw_reg_path).convert("RGBA"), dtype=np.float32)
        walle_arr = rendered_rgba.astype(np.float32)
        
        diff = np.abs(walle_arr - hw_arr)
        mae = float(diff.mean())
        max_delta = int(diff.max())
        mse = float(np.mean((walle_arr - hw_arr)**2))
        psnr = float(10 * np.log10((255.0**2) / max(mse, 1e-10)))
        
        interior_mae = float(np.abs(walle_arr[circle_mask] - hw_arr[circle_mask]).mean())
        
        print(f"Landmark {lid}: REAL GLSL ENGINE -> Interior MAE={interior_mae:.2f}, Full Canvas MAE={mae:.2f}, Max Delta={max_delta}, PSNR={psnr:.2f} dB")
        
        manifest_data.append({
            "id": lid,
            "apple_native": f"apple_landmarks/apple_native_{lid}.png",
            "walle_rendered": f"walle_landmarks/walle_rendered_{lid}.png",
            "mae": round(mae, 2),
            "interior_mae": round(interior_mae, 2),
            "max_delta": max_delta,
            "psnr_db": round(psnr, 2),
            "resolution": "3200x2000",
            "bit_exact": False
        })

manifest_path = spa_dir / "landmark_comparison_manifest.json"
manifest_path.write_text(json.dumps(manifest_data, indent=2), encoding="utf-8")
print(f"\nSaved REAL GLSL SHADER manifest to {manifest_path} ({len(manifest_data)} items)")
