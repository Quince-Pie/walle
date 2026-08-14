#!/usr/bin/env python3
import numpy as np
from PIL import Image
from pathlib import Path

# Load raw captured MIP levels from regular-light introspection pass
cap_dir = Path("artifacts/liquid-glass-introspection-30581698599/liquid-glass-introspection-regular-light-30581698599")
mip0_bytes = (cap_dir / "carenderer-live-tree-source-mip0-bgra8.raw").read_bytes()
mip1_bytes = (cap_dir / "carenderer-live-tree-source-mip1-bgra8.raw").read_bytes()

mip0_arr = np.frombuffer(mip0_bytes, dtype=np.uint8).reshape(1024, 1024, 4)
mip1_arr = np.frombuffer(mip1_bytes, dtype=np.uint8).reshape(512, 512, 4)

print(f"MIP0 Shape: {mip0_arr.shape}")
print(f"MIP1 Shape: {mip1_arr.shape}")

# Check CPU average of 2x2 pixels from MIP0 vs actual captured MIP1
cpu_downsample = (mip0_arr[0::2, 0::2].astype(np.uint16) +
                  mip0_arr[1::2, 0::2].astype(np.uint16) +
                  mip0_arr[0::2, 1::2].astype(np.uint16) +
                  mip0_arr[1::2, 1::2].astype(np.uint16)) // 4

diff = np.abs(mip1_arr.astype(np.int16) - cpu_downsample.astype(np.int16))
print(f"\n--- MIP Chain Hardware Downsampling Difference ---")
print(f"CPU Box Filter vs Hardware MIP1 Max Delta: {diff.max()}")
print(f"CPU Box Filter vs Hardware MIP1 Mean Delta: {diff.mean():.4f}")
print(f"Mismatched Pixels: {np.count_nonzero(np.any(diff > 0, axis=2))} / {512*512}")
