#!/usr/bin/env python3
"""
verify_registry.py - sanity-check every DB id the site-diary pipeline writes to.

Run this after ANY change to outputs/internal/site-diary-db-registry.json, and as
a periodic health check. It probes each main diary DB and each Voice Memo Log DB
against the live Notion API and reports OK / FAIL with the DB title, so a stale or
unshared id can never silently break capture again.

Exit code is non-zero if any id fails, so it can gate a deploy or a cron alert.

    python scripts/agent/verify_registry.py

HARD RULE: no long dashes anywhere in this file.
"""

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

WS = Path(__file__).resolve().parent.parent.parent
REGISTRY = WS / "outputs" / "internal" / "site-diary-db-registry.json"
NV = "2022-06-28"


def _load_dotenv():
    env = WS / ".env"
    if not env.exists():
        return
    for raw in env.read_text(encoding="utf-8").splitlines():
        l = raw.strip()
        if l and not l.startswith("#") and "=" in l:
            k, _, v = l.partition("=")
            k = k.strip(); v = v.strip().strip('"').strip("'")
            if k and k not in os.environ:
                os.environ[k] = v


def _probe(dbid, headers):
    try:
        req = urllib.request.Request(
            f"https://api.notion.com/v1/databases/{dbid}", headers=headers)
        with urllib.request.urlopen(req, timeout=20) as resp:
            d = json.loads(resp.read())
        title = "".join(t.get("plain_text", "") for t in d.get("title", []))
        return True, title or "(untitled)"
    except urllib.error.HTTPError as e:
        return False, f"{e.code} {e.read().decode()[:80]}"
    except Exception as e:  # noqa: BLE001
        return False, str(e)


def main():
    _load_dotenv()
    key = os.environ.get("NOTION_API_KEY")
    if not key:
        print("no NOTION_API_KEY in .env")
        return 2
    headers = {"Authorization": f"Bearer {key}", "Notion-Version": NV}
    reg = json.loads(REGISTRY.read_text(encoding="utf-8"))

    # Derive every DB from the registry so this gate is correct for any PM
    # instance, not just Nathan's project set.
    rows = []
    if reg.get("general_notes"):
        rows.append(("general-notes", reg["general_notes"]))
    for pid, dbid in reg.items():
        if pid in ("_note", "general_notes", "voice_memo_logs"):
            continue
        if isinstance(dbid, str):
            rows.append((f"diary {pid}", dbid))
    for pid, dbid in (reg.get("voice_memo_logs") or {}).items():
        rows.append((f"voice-memo {pid}", dbid))

    print(f"Checking {len(rows)} databases against live Notion...\n")
    failures = 0
    for label, dbid in rows:
        ok, detail = _probe(dbid, headers)
        mark = "OK  " if ok else "FAIL"
        if not ok:
            failures += 1
        print(f"  {mark} {label:<16} {dbid}  {detail}")

    print()
    if failures:
        print(f"{failures} database(s) FAILED. Capture will silently drop writes "
              "for these. Fix the registry and re-run.")
        return 1
    print("All databases reachable. Registry is healthy.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
