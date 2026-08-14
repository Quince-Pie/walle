#!/usr/bin/env python3
import time
import json
import urllib.request

url = "https://api.github.com/repos/Quince-Pie/lg-test/actions/runs?branch=landmark-rig"

print("Sleeping 45 seconds for GitHub REST API rate limit window reset...")
time.sleep(45)

try:
    req = urllib.request.Request(url, headers={"User-Agent": "Antigravity-Agent-v5"})
    with urllib.request.urlopen(req) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        runs = data.get("workflow_runs", [])
        print(f"Found {len(runs)} workflow runs on branch landmark-rig:")
        for r in runs[:3]:
            print(f" - Run ID: {r['id']}, Workflow: '{r['name']}', Status: {r['status']}, Conclusion: {r['conclusion']}")
            if r['status'] == 'completed':
                art_url = r['artifacts_url']
                art_req = urllib.request.Request(art_url, headers={"User-Agent": "Antigravity-Agent-v5"})
                with urllib.request.urlopen(art_req) as art_resp:
                    art_data = json.loads(art_resp.read().decode("utf-8"))
                    print("   Artifacts:", [a['name'] for a in art_data.get('artifacts', [])])
except Exception as e:
    print(f"Error querying GitHub API: {e}")
