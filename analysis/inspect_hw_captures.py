#!/usr/bin/env python3
import json
from pathlib import Path
from PIL import Image
import numpy as np

hw_shots = Path("artifacts/apple_landmark_hardware_captures_8918669614")
print("Listing files in hardware captures artifact:")
for p in hw_shots.glob("**/*"):
    if p.is_file() and p.suffix in [".json", ".txt", ".png", ".raw"]:
        print(f"  {p.relative_to(hw_shots)} ({p.stat().st_size} bytes)")
