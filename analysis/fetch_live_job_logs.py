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

headers = {"User-Agent": "Antigravity-Agent-v9"}
if token:
    headers["Authorization"] = f"token {token}"

run_id = "30977053348"
jobs_url = f"https://api.github.com/repos/Quince-Pie/lg-test/actions/runs/{run_id}/jobs"

class NoAuthRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new_req = super().redirect_request(req, fp, code, msg, headers, newurl)
        if "Authorization" in new_req.headers:
            del new_req.headers["Authorization"]
        return new_req

opener = urllib.request.build_opener(NoAuthRedirectHandler)
urllib.request.install_opener(opener)

try:
    req = urllib.request.Request(jobs_url, headers=headers)
    with urllib.request.urlopen(req) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        jobs = data.get("jobs", [])
        if jobs:
            job_id = jobs[0]["id"]
            print(f"Fetching live logs for Job ID {job_id} ({jobs[0]['name']})...")
            log_url = f"https://api.github.com/repos/Quince-Pie/lg-test/actions/jobs/{job_id}/logs"
            log_req = urllib.request.Request(log_url, headers=headers)
            with urllib.request.urlopen(log_req) as log_resp:
                log_text = log_resp.read().decode("utf-8", errors="replace")
                lines = log_text.splitlines()
                print(f"Total Log Lines: {len(lines)}")
                print("\n--- Last 30 Log Lines ---")
                for line in lines[-30:]:
                    print(line)
except Exception as e:
    print(f"Error fetching live logs: {e}")
