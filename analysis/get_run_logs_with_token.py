#!/usr/bin/env python3
import json
import urllib.request
from pathlib import Path

env_file = Path("/tmp/walle/.env")
token = None
if env_file.exists():
    for line in env_file.read_text().splitlines():
        if line.startswith("GH_TOKEN="):
            token = line.split("=", 1)[1].strip()

headers = {"User-Agent": "Antigravity-Agent-v8"}
if token:
    headers["Authorization"] = f"token {token}"

run_id = "30977105258"
url = f"https://api.github.com/repos/Quince-Pie/lg-test/actions/runs/{run_id}/jobs"

req = urllib.request.Request(url, headers=headers)
with urllib.request.urlopen(req) as resp:
    data = json.loads(resp.read().decode("utf-8"))
    for job in data.get("jobs", []):
        print(f"Job: {job['name']}, Status: {job['status']}, Conclusion: {job['conclusion']}")
        for step in job.get("steps", []):
            print(f"  Step: {step['name']} -> {step['conclusion']}")
