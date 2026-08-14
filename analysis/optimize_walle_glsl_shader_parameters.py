#!/usr/bin/env python3
"""Numerically optimize shaders/frag.glsl parameters to minimize error against Apple native hardware captures."""

import sys
import json
from pathlib import Path
from PIL import Image
import numpy as np

sys.path.insert(0, str(Path("analysis").resolve()))
from walle_shader_renderer import WalleShaderRenderer

landmarks_dir = Path("artifacts/liquid_glass_blog/landmarks")
hw_shots_dir = Path("artifacts/apple_landmark_hardware_captures_8918669614/shots")

landmark_files = sorted(landmarks_dir.glob("*.jpg"))[:5] # Test on first 5 landmark photo wallpapers

print("Starting numerical optimization of shaders/frag.glsl parameters against Apple hardware captures...")

frag_path = Path("shaders/frag.glsl")
frag_code = frag_path.read_text(encoding="utf-8")

def evaluate_params(wash_ck, platter_s):
    # Temporarily substitute parameters in shaders/frag.glsl
    modified_code = frag_code.replace("const float WASH_CK_LIGHT   = 2.00;", f"const float WASH_CK_LIGHT   = {wash_ck:.4f};")
    modified_code = modified_code.replace("const float WASH_CK_DARK    = 2.00;", f"const float WASH_CK_DARK    = {wash_ck:.4f};")
    modified_code = modified_code.replace("const float PLATTER_LIGHT_S = 0.980;", f"const float PLATTER_LIGHT_S = {platter_s:.4f};")
    
    temp_shader = Path("shaders/frag_temp_opt.glsl")
    temp_shader.write_text(modified_code, encoding="utf-8")
    
    total_mae = 0.0
    count = 0
    
    y, x = np.ogrid[:2000, :3200]
    circle_mask = (x - 1600)**2 + (y - 1000)**2 <= 240**2
    
    for bg_file in landmark_files:
        lid = bg_file.name.split("@")[0]
        hw_reg_path = hw_shots_dir / f"landmark-{lid}__circle-0500-center__regular__light.png"
        if not hw_reg_path.exists():
            continue
            
        img_raw = Image.open(bg_file).convert("RGBA")
        canvas = Image.new("RGBA", (3200, 2000), (0, 0, 0, 255))
        canvas.paste(img_raw, (0, 0))
        
        hw_img = np.array(Image.open(hw_reg_path).convert("RGBA"), dtype=np.float32)
        
        with WalleShaderRenderer(width=3200, height=2000, fragment_shader=temp_shader) as renderer:
            tex = renderer.upload_wallpaper(canvas, regular=True)
            rendered_rgba = renderer.render(
                outgoing=tex,
                incoming=tex,
                time=0.62,
                center_top_left=(1600.0, 1000.0),
                maximum_radius=250.0,
                regular=True,
            ).astype(np.float32)
            
        mae = float(np.abs(rendered_rgba[circle_mask] - hw_img[circle_mask]).mean())
        total_mae += mae
        count += 1
        
    if temp_shader.exists():
        temp_shader.unlink()
        
    avg_mae = total_mae / max(count, 1)
    return avg_mae

best_mae = 999.0
best_wash = 2.00
best_platter = 0.980

# Sweep wash_ck in [0.5, 3.5] and platter_s in [0.70, 0.99]
for wash_ck in np.linspace(0.8, 2.5, 6):
    for platter_s in np.linspace(0.75, 0.95, 5):
        mae = evaluate_params(wash_ck, platter_s)
        print(f"Tested WASH_CK={wash_ck:.2f}, PLATTER_S={platter_s:.2f} -> Average Platter MAE = {mae:.2f}")
        if mae < best_mae:
            best_mae = mae
            best_wash = wash_ck
            best_platter = platter_s

print(f"\n--- OPTIMIZATION RESULTS ---")
print(f"Best WASH_CK: {best_wash:.4f}")
print(f"Best PLATTER_S: {best_platter:.4f}")
print(f"Lowest Platter MAE: {best_mae:.2f}")
