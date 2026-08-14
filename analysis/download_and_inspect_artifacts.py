#!/usr/bin/env python3
import os
import json
import zipfile
import urllib.request
from pathlib import Path

# Load .env token
env_file = Path("/tmp/walle/.env")
token = None
if env_file.exists():
    for line in env_file.read_text().splitlines():
        if line.startswith("GH_TOKEN="):
            token = line.split("=", 1)[1].strip()

headers = {"User-Agent": "Antigravity-Agent-v6"}
if token:
    headers["Authorization"] = f"token {token}"

art_url = "https://api.github.com/repos/Quince-Pie/lg-test/actions/artifacts/8918323974/zip"
dest_zip = Path("artifacts/landmark_hardware_captures_30975551580.zip")
extract_dir = Path("artifacts/landmark_hardware_captures_30975551580")
extract_dir.mkdir(parents=True, exist_ok=True)

print(f"Downloading native macOS 26 hardware capture artifact (577 MB) from {art_url}...")

req = urllib.request.Request(art_url, headers=headers)
with urllib.request.urlopen(req) as resp, open(dest_zip, "wb") as f:
    chunk_count = 0
    while True:
        chunk = resp.read(1024 * 1024)
        if not chunk:
            break
        f.write(chunk)
        chunk_count += 1
        if chunk_count % 50 == 0:
            print(f" - Downloaded {chunk_count} MB...")

print(f"Download complete! Extracting {dest_zip.name}...")

with zipfile.ZipFile(dest_zip, "r") as zip_ref:
    zip_ref.extractall(extract_dir)

captured_files = list(extract_dir.rglob("*.png"))
print(f"Extracted {len(captured_files)} native PNG captures into {extract_dir}/")
for p in captured_files[:10]:
    print(f" - {p.name}")
