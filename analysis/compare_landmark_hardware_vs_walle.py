#!/usr/bin/env python3
import json
import shutil
from pathlib import Path
import numpy as np
from PIL import Image

hardware_dir = Path("artifacts/apple_landmark_hardware_captures_8918669614/shots")
walle_landmarks_dir = Path("artifacts/liquid_glass_blog/walle_landmarks")
output_blog_dir = Path("artifacts/liquid_glass_blog/apple_landmarks")
output_blog_dir.mkdir(parents=True, exist_ok=True)

manifest_data = []

landmark_ids = ["1001", "1002", "1003", "1004", "1005", "1006", "1007", "1008", "1009", "1010", "1011", "1012", "1014", "1015", "1016", "1017", "1018", "1019", "1020", "1021", "1022"]

print(f"Auditing native Apple macOS 26 hardware captures vs Walle engine renders for {len(landmark_ids)} landmark wallpapers...")

for lid in landmark_ids:
    hw_filename = f"landmark-{lid}__circle-0500-center__regular__light.png"
    hw_path = hardware_dir / hw_filename
    walle_filename = f"walle_rendered_{lid}.png"
    walle_path = walle_landmarks_dir / walle_filename
    
    if not hw_path.exists():
        print(f"Warning: Hardware capture missing for landmark {lid} ({hw_path})")
        continue
    if not walle_path.exists():
        print(f"Warning: Walle render missing for landmark {lid} ({walle_path})")
        continue
        
    # Copy hardware capture into blog directory
    dst_hw_path = output_blog_dir / f"apple_native_{lid}.png"
    shutil.copy(hw_path, dst_hw_path)
    
    # Load images for delta calculation
    hw_img = Image.open(hw_path).convert("RGB")
    walle_img = Image.open(walle_path).convert("RGB")
    
    # Resize Walle to match Hardware capture dimensions if needed
    if hw_img.size != walle_img.size:
        walle_img = walle_img.resize(hw_img.size, Image.Resampling.LANCZOS)
        
    hw_arr = np.array(hw_img, dtype=np.float32)
    walle_arr = np.array(walle_img, dtype=np.float32)
    
    # Calculate MAE and Peak Delta
    diff = np.abs(hw_arr - walle_arr)
    mae = float(np.mean(diff))
    max_delta = float(np.max(diff))
    
    # Calculate MSE and PSNR
    mse = float(np.mean((hw_arr - walle_arr) ** 2))
    psnr = float(10 * np.log10((255.0 ** 2) / max(mse, 1e-10)))
    
    print(f"Landmark {lid}: MAE={mae:.2f}, Max Delta={max_delta:.0f}, PSNR={psnr:.2f} dB, Size={hw_img.size}")
    
    manifest_data.append({
        "id": lid,
        "apple_native": f"apple_landmarks/apple_native_{lid}.png",
        "walle_rendered": f"walle_landmarks/walle_rendered_{lid}.png",
        "mae": round(mae, 2),
        "max_delta": int(max_delta),
        "psnr_db": round(psnr, 2),
        "resolution": f"{hw_img.size[0]}x{hw_img.size[1]}"
    })

manifest_path = Path("artifacts/liquid_glass_blog/landmark_comparison_manifest.json")
manifest_path.write_text(json.dumps(manifest_data, indent=2), encoding="utf-8")
print(f"\nSaved comparison manifest to {manifest_path.name} ({len(manifest_data)} items)")
