#!/usr/bin/env python3
import time
import json
import urllib.request

repo = "Quince-Pie/lg-test"
url = f"https://api.github.com/repos/{repo}/actions/runs"

print("Polling GitHub Actions workflow runs for commit 70789bb...")

for attempt in range(15):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Antigravity-Agent"})
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            runs = data.get("workflow_runs", [])
            if runs:
                latest = runs[0]
                commit_sha = latest['head_commit']['id'][:7]
                print(f"Attempt {attempt+1}: Run ID {latest['id']}, Commit {commit_sha}, Status: {latest['status']}, Conclusion: {latest['conclusion']}")
                if commit_sha == "96acef5" and latest['status'] == 'completed':
                    print(f"\nTarget Run {latest['id']} Completed with Conclusion: {latest['conclusion']}!")
                    break
    except Exception as e:
        print(f"Error: {e}")
    time.sleep(4)
