#!/usr/bin/env python3
import sys
import json
from pathlib import Path
from PIL import Image
import numpy as np

sys.path.insert(0, str(Path("analysis").resolve()))
from apple_glass_reference_renderer import AppleGlassReferenceRenderer, compare_images
from liquid_glass_shader_specialization import load_specialized_exact_final_shader

landmarks_dir = Path("artifacts/liquid_glass_blog/landmarks")
out_dir = Path("artifacts/liquid_glass_blog/walle_landmarks")
out_dir.mkdir(parents=True, exist_ok=True)
spa_dir = Path("artifacts/liquid_glass_blog")
hw_shots_dir = Path("artifacts/apple_landmark_hardware_captures_8918669614/shots")

landmark_files = sorted(landmarks_dir.glob("*.jpg"))

float_intrinsic_table = Path("artifacts/apple-float-intrinsics-r8-30556057571.bin")
cap_path = Path("artifacts/liquid-glass-introspection-30581698599/liquid-glass-introspection-regular-light-30581698599")
half_intrinsic_table = cap_path / "half-intrinsics.bin"

print(f"Rendering all {len(landmark_files)} Apple Landmark photo wallpapers using EXACT AppleGlassReferenceRenderer with specialized GLSL shader + intrinsic hardware tables...")

y, x = np.ogrid[:2000, :3200]
circle_mask = (x - 1600)**2 + (y - 1000)**2 <= 250**2

manifest_data = []

for bg_file in landmark_files:
    lid = bg_file.name.split("@")[0]
    
    img_raw = Image.open(bg_file).convert("RGBA").resize((1024, 1024), Image.Resampling.LANCZOS)
    
    with AppleGlassReferenceRenderer(
        cap_path,
        fragment_shader_source=load_specialized_exact_final_shader(),
        intrinsic_table=float_intrinsic_table,
        half_intrinsic_table=half_intrinsic_table,
        load_interpolant_trace=False,
        load_interpolant_axis_trace=False,
        load_diagnostic_traces=False,
    ) as renderer:
        renderer.program["HighlightCoordinateMode"].value = 0
        renderer.program["HighlightCoverageArithmeticMode"].value = 1
        renderer.program["HighlightDerivativeMode"].value = 1
        renderer.program["HighlightFloatDivisionMode"].value = 3
        renderer.program["HighlightNormalizeMode"].value = 1
        renderer.program["HighlightSourceConstructionMode"].value = 1
        renderer.program["HighlightSourceDivisionMode"].value = 0
        renderer.program["HighlightVibrantArithmeticMode"].value = 9
        
        rendered_bgra = renderer.render_complete()
        
    # Convert BGRA -> RGBA
    rendered_rgba = rendered_bgra[:, :, [2, 1, 0, 3]]
    
    out_name = f"walle_rendered_{lid}.png"
    out_path = out_dir / out_name
    spa_out_path = spa_dir / out_name
    
    out_img = Image.fromarray(rendered_rgba)
    out_img.save(out_path)
    out_img.save(spa_out_path)
    
    # Audit against hardware capture target
    hw_reg_path = hw_shots_dir / f"landmark-{lid}__circle-0500-center__regular__light.png"
    if hw_reg_path.exists():
        hw_img = Image.open(hw_reg_path).convert("RGBA").resize((1024, 1024), Image.Resampling.LANCZOS)
        hw_arr = np.array(hw_img, dtype=np.float32)
        walle_arr = rendered_rgba.astype(np.float32)
        
        diff = np.abs(walle_arr - hw_arr)
        mae = float(diff.mean())
        max_delta = int(diff.max())
        mse = float(np.mean((walle_arr - hw_arr)**2))
        psnr = float(10 * np.log10((255.0**2) / max(mse, 1e-10)))
        
        y_1024, x_1024 = np.ogrid[:1024, :1024]
        circle_mask_1024 = (x_1024 - 512)**2 + (y_1024 - 512)**2 <= 80**2
        
        interior_mae = float(np.abs(walle_arr[circle_mask_1024] - hw_arr[circle_mask_1024]).mean())
        
        print(f"Landmark {lid}: AppleGlassReferenceRenderer -> Interior MAE={interior_mae:.2f}, Full Canvas MAE={mae:.2f}, Max Delta={max_delta}, PSNR={psnr:.2f} dB")
        
        manifest_data.append({
            "id": lid,
            "apple_native": f"apple_landmarks/apple_native_{lid}.png",
            "walle_rendered": f"walle_landmarks/walle_rendered_{lid}.png",
            "mae": round(mae, 2),
            "interior_mae": round(interior_mae, 2),
            "max_delta": max_delta,
            "psnr_db": round(psnr, 2),
            "resolution": f"{rendered_rgba.shape[1]}x{rendered_rgba.shape[0]}",
            "bit_exact": max_delta == 0
        })

manifest_path = spa_dir / "landmark_comparison_manifest.json"
manifest_path.write_text(json.dumps(manifest_data, indent=2), encoding="utf-8")
print(f"\nSaved AppleGlassReferenceRenderer manifest to {manifest_path} ({len(manifest_data)} items)")
