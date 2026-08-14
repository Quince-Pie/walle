#!/usr/bin/env python3
import os
import sys
import time
import json
import urllib.request
from pathlib import Path

env_file = Path("/tmp/walle/.env")
token = None
if env_file.exists():
    for line in env_file.read_text().splitlines():
        if line.startswith("GH_TOKEN="):
            token = line.split("=", 1)[1].strip()

headers = {"User-Agent": "Antigravity-Agent-v9"}
if token:
    headers["Authorization"] = f"token {token}"

url = "https://api.github.com/repos/Quince-Pie/lg-test/actions/runs?branch=landmark-rig"

print("Polling GitHub Actions workflow runs for branch landmark-rig...")

for i in range(1, 13):
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            runs = data.get('workflow_runs', [])
            print(f"Attempt {i}: Found {len(runs)} workflow runs on branch landmark-rig.")
            if runs:
                latest = runs[0]
                print(f" - Run ID: {latest['id']}")
                print(f" - Workflow Name: {latest['name']}")
                print(f" - Status: {latest['status']}")
                print(f" - Conclusion: {latest['conclusion']}")
                if latest['status'] == 'completed':
                    print("\nLandmark capture workflow run completed!")
                    break
    except Exception as e:
        print(f"Attempt {i} error: {e}")
    time.sleep(5)
