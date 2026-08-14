#!/usr/bin/env python3
import os
import json
import urllib.request
from pathlib import Path

# Load .env token if present
env_file = Path("/tmp/walle/.env")
token = None
if env_file.exists():
    for line in env_file.read_text().splitlines():
        if line.startswith("GH_TOKEN="):
            token = line.split("=", 1)[1].strip()

url = "https://api.github.com/repos/Quince-Pie/lg-test/actions/runs?branch=landmark-rig"

headers = {"User-Agent": "Antigravity-Agent-v6"}
if token:
    headers["Authorization"] = f"token {token}"
    print("Using GitHub API Token from /tmp/walle/.env")

try:
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        runs = data.get("workflow_runs", [])
        print(f"Found {len(runs)} workflow runs on branch landmark-rig:")
        for r in runs[:5]:
            print(f"\nRun ID: {r['id']}")
            print(f"Workflow Name: '{r['name']}'")
            print(f"Commit: {r['head_commit']['id'][:7]} - {r['head_commit']['message']}")
            print(f"Status: {r['status']}")
            print(f"Conclusion: {r['conclusion']}")
            
            art_url = r['artifacts_url']
            art_req = urllib.request.Request(art_url, headers=headers)
            with urllib.request.urlopen(art_req) as art_resp:
                art_data = json.loads(art_resp.read().decode("utf-8"))
                artifacts = art_data.get('artifacts', [])
                print(f"Artifacts ({len(artifacts)}):", [{"name": a['name'], "size_bytes": a['size_in_bytes'], "archive_download_url": a['archive_download_url']} for a in artifacts])
except Exception as e:
    print(f"Error querying GitHub API: {e}")
