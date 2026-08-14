#!/usr/bin/env python3
import shutil
from pathlib import Path

src_dir = Path("artifacts/liquid_glass_blog/landmarks")
dest_dir = Path("lg-landmark-rig/Resources/Landmarks")
dest_dir.mkdir(parents=True, exist_ok=True)

for f in src_dir.glob("*.jpg"):
    shutil.copy(f, dest_dir / f.name)

print(f"Copied {len(list(dest_dir.glob('*.jpg')))} landmark photo assets to lg-landmark-rig/Resources/Landmarks/")
