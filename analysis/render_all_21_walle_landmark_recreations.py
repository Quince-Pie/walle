#!/usr/bin/env python3
import sys
from pathlib import Path
import json
from PIL import Image
import numpy as np

sys.path.insert(0, str(Path("analysis").resolve()))
from walle_shader_renderer import WalleShaderRenderer

landmarks_dir = Path("artifacts/liquid_glass_blog/landmarks")
out_dir = Path("artifacts/liquid_glass_blog/walle_landmarks")
out_dir.mkdir(parents=True, exist_ok=True)
spa_dir = Path("artifacts/liquid_glass_blog")

landmark_files = sorted(landmarks_dir.glob("*.jpg"))
print(f"Rendering all {len(landmark_files)} Apple Landmark photo assets using CLEAR material (translucent blurred refraction)...")

records = {}

for bg_file in landmark_files:
    landmark_id = bg_file.name.split("@")[0]
    img = Image.open(bg_file).convert("RGBA")
    img_resized = img.resize((3200, 2000), Image.Resampling.LANCZOS)
    w, h = img_resized.size
    
    with WalleShaderRenderer(
        width=w,
        height=h,
        fragment_shader=Path("shaders/frag.glsl"),
    ) as renderer:
        tex = renderer.upload_wallpaper(img_resized, regular=False)
        rendered_rgba = renderer.render(
            outgoing=tex,
            incoming=tex,
            time=0.62,
            center_top_left=(1600.0, 1000.0),
            maximum_radius=250.0,
            regular=False, # Clear material for translucent photo wallpaper refraction
        )
        
    out_name = f"walle_rendered_{landmark_id}.png"
    out_path = out_dir / out_name
    spa_out_path = spa_dir / out_name
    
    out_img = Image.fromarray(rendered_rgba)
    out_img.save(out_path)
    out_img.save(spa_out_path)
    
    records[landmark_id] = {
        "source_asset": bg_file.name,
        "output_image": out_name,
        "dimensions": f"{w}x{h}",
        "shader": "shaders/frag.glsl",
        "platter_radius_px": 250.0,
    }
    print(f" - {landmark_id}: saved {out_name} ({w}x{h})")

manifest_path = spa_dir / "walle_landmarks_manifest.json"
manifest_path.write_text(json.dumps({"total_landmarks": len(records), "landmarks": records}, indent=2), encoding="utf-8")
print(f"\nManifest saved to {manifest_path}")
