#!/usr/bin/env python3
import time
import json
import urllib.request
from pathlib import Path

repo = "Quince-Pie/lg-test"
url = f"https://api.github.com/repos/{repo}/actions/runs"

print(f"Monitoring GitHub Actions workflow runs for {repo}...")

for attempt in range(12):
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Antigravity-Agent"}
        )
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            runs = data.get("workflow_runs", [])
            print(f"Attempt {attempt+1}: Found {len(runs)} total workflow runs.")
            if runs:
                latest = runs[0]
                print(f" - Latest Run ID: {latest['id']}")
                print(f" - Workflow Name: {latest['name']}")
                print(f" - Status: {latest['status']}")
                print(f" - Conclusion: {latest['conclusion']}")
                print(f" - Commit: {latest['head_commit']['id'][:7]} - {latest['head_commit']['message']}")
                if latest['status'] == 'completed':
                    print("\nWorkflow run completed successfully!")
                    break
    except Exception as e:
        print(f"Error querying GitHub API: {e}")
    time.sleep(5)
