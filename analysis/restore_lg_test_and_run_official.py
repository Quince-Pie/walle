#!/usr/bin/env python3
import subprocess
from pathlib import Path

lg_test_dir = Path("/tmp/walle/lg-test")

# 1. Remove experimental landmark-capture.yml workflow
landmark_yml = lg_test_dir / ".github/workflows/landmark-capture.yml"
if landmark_yml.exists():
    landmark_yml.unlink()

# 2. Reset git and commit clean official lg-test
subprocess.run(["git", "add", "-A"], cwd=lg_test_dir, check=True)
subprocess.run(["git", "commit", "-m", "Restore official lg-test calibration capture workflow"], cwd=lg_test_dir, check=True)
subprocess.run(["git", "push", "origin", "main"], cwd=lg_test_dir, check=True)

print("Pushed restored official lg-test repository to remote!")
