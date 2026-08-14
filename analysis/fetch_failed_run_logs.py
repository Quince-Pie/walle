#!/usr/bin/env python3
import json
import urllib.request

run_id = "30975092190"
url = f"https://api.github.com/repos/Quince-Pie/lg-test/actions/runs/{run_id}/jobs"

req = urllib.request.Request(url, headers={"User-Agent": "Antigravity-Agent"})
with urllib.request.urlopen(req) as resp:
    data = json.loads(resp.read().decode("utf-8"))
    for job in data.get("jobs", []):
        print(f"Job: {job['name']}, Status: {job['status']}, Conclusion: {job['conclusion']}")
        for step in job.get("steps", []):
            print(f"  Step: {step['name']} -> {step['conclusion']}")
