#!/usr/bin/env python3
"""
supabase_events.py - the project event store, on Supabase (Postgres + PostgREST).

Replaces the Notion Project Events DB as the home for the email-router event
stream. Uses the Supabase REST API with the sb_secret server key (zero extra
dependencies - just urllib). Dedup is automatic: project_events.msg_id is unique,
and inserts use on_conflict=msg_id with resolution=ignore-duplicates, so a
re-seen email is skipped rather than duplicated.

Public API:
    insert_events(rows) -> inserted_count
    query(select, filters, order, limit) -> list[dict]
    recent_for_project(project_id, days) -> list[dict]
    count() -> int

Columns (create-table SQL lives in scripts/supabase_schema.sql; run it once in
the Supabase SQL editor before first use):
    msg_id, project_id, event_type, source, event_date, sender, subject,
    summary, key_facts, action_owner, due_date, confidence, status, weblink.

HARD RULE: no long dashes anywhere in this file.
"""

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parent.parent
TABLE = "project_events"


def load_env():
    env = WORKSPACE / ".env"
    if not env.exists():
        return
    for raw in env.read_text(encoding="utf-8").splitlines():
        l = raw.strip()
        if l and not l.startswith("#") and "=" in l:
            k, _, v = l.partition("="); k = k.strip(); v = v.strip().strip('"').strip("'")
            if k and k not in os.environ:
                os.environ[k] = v


def _base():
    load_env()
    url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    key = os.environ.get("SUPABASE_API_KEY", "")
    if not url or not key:
        raise RuntimeError("SUPABASE_URL / SUPABASE_API_KEY not set in .env")
    return url, key


def _headers(key, prefer=None):
    h = {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    if prefer:
        h["Prefer"] = prefer
    return h


def insert_events(rows):
    """Insert a list of column dicts. Returns the number actually inserted
    (duplicates on msg_id are ignored, not counted)."""
    if not rows:
        return 0
    url, key = _base()
    endpoint = f"{url}/rest/v1/{TABLE}?on_conflict=msg_id"
    req = urllib.request.Request(
        endpoint, data=json.dumps(rows).encode(), method="POST",
        headers=_headers(key, prefer="resolution=ignore-duplicates,return=representation"))
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            returned = json.loads(resp.read() or "[]")
        return len(returned)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"supabase insert {e.code}: {e.read().decode()[:300]}")


def query(select="*", filters=None, order=None, limit=None):
    url, key = _base()
    params = {"select": select}
    if filters:
        params.update(filters)
    if order:
        params["order"] = order
    if limit:
        params["limit"] = str(limit)
    qs = urllib.parse.urlencode(params, safe="*().,")
    req = urllib.request.Request(f"{url}/rest/v1/{TABLE}?{qs}", headers=_headers(key))
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read() or "[]")
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"supabase query {e.code}: {e.read().decode()[:300]}")


def recent_for_project(project_id, days=7):
    import datetime
    since = (datetime.date.today() - datetime.timedelta(days=days)).isoformat()
    return query(filters={"project_id": f"eq.{project_id}", "event_date": f"gte.{since}"},
                 order="event_date.desc")


def count():
    url, key = _base()
    req = urllib.request.Request(
        f"{url}/rest/v1/{TABLE}?select=id", headers=_headers(key, prefer="count=exact"))
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            cr = resp.headers.get("Content-Range", "")
        return int(cr.split("/")[-1]) if "/" in cr else 0
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"supabase count {e.code}: {e.read().decode()[:200]}")


if __name__ == "__main__":
    try:
        print("project_events count:", count())
    except Exception as e:  # noqa: BLE001
        print("not ready:", e)
