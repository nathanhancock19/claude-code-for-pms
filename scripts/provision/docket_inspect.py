#!/usr/bin/env python3
"""
docket_inspect.py - read-only snapshot of the Day Docket App Airtable base.

Used by the PM fleet rollout to learn how jobs map to PMs, so a PM's projects
can be derived automatically when generating their workspace. Lists the target
table's fields (name/type) and a sample of rows. Does NOT write anything.
Self-loads the plain AIRTABLE_API_KEY / AIRTABLE_BASE_ID from .env (the docket
base, YOUR_AIRTABLE_BASE_ID).

Usage:
    python scripts/provision/docket_inspect.py [tableId]

HARD RULE: no long dashes anywhere in this file or its output.
"""

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parent.parent.parent
META = "https://api.airtable.com/v0/meta/bases"
DEFAULT_TABLE = "YOUR_DOCKET_PROJECTS_TABLE_ID"


def load_env():
    env = {}
    for line in (WORKSPACE / ".env").read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if s.startswith("AIRTABLE_") and "=" in s:
            k, _, v = s.partition("=")
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def api(url, key):
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {key}", "Content-Type": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        print(f"[HTTP {e.code}] {e.read().decode()[:500]}")
        return None


def main():
    env = load_env()
    key = env.get("AIRTABLE_API_KEY")
    base = env.get("AIRTABLE_BASE_ID")
    if not key or not base:
        sys.exit("Missing AIRTABLE_API_KEY / AIRTABLE_BASE_ID in .env")
    target = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_TABLE
    print(f"Base: {base}\nTarget table: {target}\n" + "=" * 60)

    schema = api(f"{META}/{base}/tables", key)
    if not schema:
        return
    for t in schema["tables"]:
        marker = "  <-- TARGET" if t["id"] == target else ""
        print(f"\n## {t['name']}  ({t['id']}){marker}")
        if t["id"] != target:
            continue
        for f in t["fields"]:
            print(f"   - {f['name']}  [{f['type']}]")
        rows = api(f"https://api.airtable.com/v0/{base}/{target}?pageSize=5", key)
        if rows:
            print(f"\n   sample rows ({len(rows.get('records', []))}):")
            for r in rows.get("records", []):
                print(f"      {json.dumps(r.get('fields', {}), default=str)[:600]}")


if __name__ == "__main__":
    main()
