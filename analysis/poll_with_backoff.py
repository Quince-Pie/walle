#!/usr/bin/env python3
import time
import json
import urllib.request

url = "https://api.github.com/repos/Quince-Pie/lg-test/actions/runs"

print("Waiting 30s for GitHub API rate limit window to reset...")
time.sleep(30)

for attempt in range(10):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Antigravity-Agent-v2"})
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            runs = data.get("workflow_runs", [])
            if runs:
                latest = runs[0]
                print(f"Run ID: {latest['id']}")
                print(f"Workflow: {latest['name']}")
                print(f"Status: {latest['status']}")
                print(f"Conclusion: {latest['conclusion']}")
                
                # If completed, check artifacts
                if latest['status'] == 'completed':
                    art_url = latest['artifacts_url']
                    art_req = urllib.request.Request(art_url, headers={"User-Agent": "Antigravity-Agent-v2"})
                    with urllib.request.urlopen(art_req) as art_resp:
                        art_data = json.loads(art_resp.read().decode("utf-8"))
                        print("Artifacts:", [a['name'] for a in art_data.get('artifacts', [])])
                    break
    except Exception as e:
        print(f"Attempt {attempt+1} error: {e}")
    time.sleep(15)
