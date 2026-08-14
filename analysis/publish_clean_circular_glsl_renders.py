#!/usr/bin/env python3
import sys
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

landmark_files = sorted(landmarks_dir.glob("*.jpg"))
print(f"Rendering clean circular glass platters for all {len(landmark_files)} Apple Landmark wallpapers using shaders/frag.glsl...")

manifest_data = []

for bg_file in landmark_files:
    lid = bg_file.name.split("@")[0]
    img_raw = Image.open(bg_file).convert("RGBA")
    
    # 3200x2000 canvas
    canvas = Image.new("RGBA", (3200, 2000), (0, 0, 0, 255))
    canvas.paste(img_raw, (0, 0))
    w, h = canvas.size
    
    with WalleShaderRenderer(width=w, height=h, fragment_shader=Path("shaders/frag.glsl")) as renderer:
        tex = renderer.upload_wallpaper(canvas, regular=True)
        rendered_rgba = renderer.render(
            outgoing=tex,
            incoming=tex,
            time=0.62,
            center_top_left=(1600.0, 1000.0),
            maximum_radius=250.0,
            regular=True,
        )
        
    out_name = f"walle_rendered_{lid}.png"
    out_path = out_dir / out_name
    spa_out_path = spa_dir / out_name
    
    out_img = Image.fromarray(rendered_rgba)
    out_img.save(out_path)
    out_img.save(spa_out_path)
    
    print(f"Saved clean circular platter render for Landmark {lid} ({out_img.size[0]}x{out_img.size[1]})")
    
    manifest_data.append({
        "id": lid,
        "apple_native": f"apple_landmarks/apple_native_{lid}.png",
        "walle_rendered": f"walle_landmarks/walle_rendered_{lid}.png",
        "resolution": "3200x2000"
    })

manifest_path = spa_dir / "landmark_comparison_manifest.json"
manifest_path.write_text(json.dumps(manifest_data, indent=2), encoding="utf-8")
print(f"\nSaved clean manifest to {manifest_path}")
