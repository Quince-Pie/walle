#!/usr/bin/env python3
"""Generate exact EGL MIP pyramids for Apple Landmark wallpapers and execute AppleGlassReferenceRenderer."""

import sys
import json
from pathlib import Path
from PIL import Image
import numpy as np
import moderngl

sys.path.insert(0, str(Path("analysis").resolve()))
from apple_glass_reference_renderer import AppleGlassReferenceRenderer
from liquid_glass_shader_specialization import load_specialized_exact_final_shader

landmarks_dir = Path("artifacts/liquid_glass_blog/landmarks")
out_dir = Path("artifacts/liquid_glass_blog/walle_landmarks")
out_dir.mkdir(parents=True, exist_ok=True)
spa_dir = Path("artifacts/liquid_glass_blog")
hw_shots_dir = Path("artifacts/apple_landmark_hardware_captures_8918669614/shots")

float_intrinsic_table = Path("artifacts/apple-float-intrinsics-r8-30556057571.bin")
cap_path = Path("artifacts/liquid-glass-introspection-30581698599/liquid-glass-introspection-regular-light-30581698599")
half_intrinsic_table = cap_path / "half-intrinsics.bin"

landmark_files = sorted(landmarks_dir.glob("*.jpg"))
print(f"Generating exact EGL MIP pyramids & rendering all {len(landmark_files)} Apple Landmark photo wallpapers using AppleGlassReferenceRenderer...")

# Create EGL standalone context for exact hardware MIP generation
ctx = moderngl.create_standalone_context(backend="egl")

manifest_data = []

for bg_file in landmark_files:
    lid = bg_file.name.split("@")[0]
    
    # Load wallpaper at 384x384 base size matching AppleGlassReferenceRenderer contract
    img_raw = Image.open(bg_file).convert("RGBA").resize((384, 384), Image.Resampling.LANCZOS)
    bgra_raw = np.array(img_raw)[:, :, [2, 1, 0, 3]].tobytes()
    
    # Create ModernGL texture with 6 MIP levels (384 -> 192 -> 96 -> 48 -> 24 -> 12)
    tex = ctx.texture((384, 384), 4, bgra_raw)
    tex.build_mipmaps(0, 5)
    
    # Extract exact MIP level bytes
    mip_overrides = {}
    for level in range(6):
        mip_bytes = tex.read(level=level)
        mip_overrides[level] = mip_bytes
        
    tex.release()
    
    # Execute AppleGlassReferenceRenderer with exact MIP overrides
    with AppleGlassReferenceRenderer(
        cap_path,
        fragment_shader_source=load_specialized_exact_final_shader(),
        intrinsic_table=float_intrinsic_table,
        half_intrinsic_table=half_intrinsic_table,
        source_mip_bgra_overrides=mip_overrides,
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
        
    rendered_rgba = rendered_bgra[:, :, [2, 1, 0, 3]]
    
    out_name = f"walle_rendered_{lid}.png"
    out_path = out_dir / out_name
    spa_out_path = spa_dir / out_name
    
    out_img = Image.fromarray(rendered_rgba)
    out_img.save(out_path)
    out_img.save(spa_out_path)
    
    # Compare against native hardware capture
    hw_reg_path = hw_shots_dir / f"landmark-{lid}__circle-0500-center__regular__light.png"
    if hw_reg_path.exists():
        hw_img = Image.open(hw_reg_path).convert("RGBA").resize((1024, 1024), Image.Resampling.LANCZOS)
        hw_arr = np.array(hw_img, dtype=np.float32)
        walle_arr = rendered_rgba.astype(np.float32)
        
        y_1024, x_1024 = np.ogrid[:1024, :1024]
        circle_mask_1024 = (x_1024 - 512)**2 + (y_1024 - 512)**2 <= 80**2
        
        diff = np.abs(walle_arr - hw_arr)
        interior_diff = np.abs(walle_arr[circle_mask_1024] - hw_arr[circle_mask_1024])
        
        interior_mae = float(interior_diff.mean())
        max_delta = int(diff.max())
        mae = float(diff.mean())
        mse = float(np.mean((walle_arr - hw_arr)**2))
        psnr = float(10 * np.log10((255.0**2) / max(mse, 1e-10)))
        
        bit_exact = (max_delta == 0)
        print(f"Landmark {lid}: AppleGlassReferenceRenderer (with MIP Overrides) -> Platter Interior MAE={interior_mae:.2f}, Max Delta={max_delta}, Bit-Exact={bit_exact}")
        
        manifest_data.append({
            "id": lid,
            "apple_native": f"apple_landmarks/apple_native_{lid}.png",
            "walle_rendered": f"walle_landmarks/walle_rendered_{lid}.png",
            "mae": round(mae, 2),
            "interior_mae": round(interior_mae, 2),
            "max_delta": max_delta,
            "psnr_db": round(psnr, 2),
            "resolution": f"{rendered_rgba.shape[1]}x{rendered_rgba.shape[0]}",
            "bit_exact": bit_exact
        })

ctx.release()

manifest_path = spa_dir / "landmark_comparison_manifest.json"
manifest_path.write_text(json.dumps(manifest_data, indent=2), encoding="utf-8")
print(f"\nSaved MIP-overridden manifest to {manifest_path}")
