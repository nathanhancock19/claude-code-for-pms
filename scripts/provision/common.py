#!/usr/bin/env python3
"""
common.py - shared helpers for the PM fleet provisioning scripts.

Zero-dependency (stdlib only), so it runs the same on a PM's PC and on the VPS.
Covers: dotenv loading, Notion + Airtable API calls, and the PM's projects from
the Day Docket Airtable. The provisioning scripts (generate_pm_workspace,
build_pm_env, provision_pm_notion, deploy_pm_bot, pm_daily_brief) build on this.

HARD RULE: no long dashes anywhere in this file.
Plan: plans/2026-06-21-telegram-aios-pm-fleet-rollout.md
"""

import json
import urllib.error
import urllib.request
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parent.parent.parent
DOCKET_PROJECTS_TABLE = "YOUR_DOCKET_PROJECTS_TABLE_ID"   # Projects table in base YOUR_AIRTABLE_BASE_ID
NOTION_VERSION = "2022-06-28"


def load_env(path=None) -> dict:
    """Parse a .env into a dict. Does not touch os.environ. Quotes stripped."""
    path = Path(path) if path else (WORKSPACE / ".env")
    out = {}
    if not path.exists():
        return out
    for raw in path.read_text(encoding="utf-8").splitlines():
        s = raw.strip()
        if s and not s.startswith("#") and "=" in s:
            k, _, v = s.partition("=")
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


# --------------------------------------------------------------------------- #
# Notion
# --------------------------------------------------------------------------- #
def notion_headers(key):
    return {"Authorization": f"Bearer {key}", "Notion-Version": NOTION_VERSION,
            "Content-Type": "application/json"}


def notion_get(path, key):
    req = urllib.request.Request(
        f"https://api.notion.com/v1/{path}", headers=notion_headers(key))
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def notion_post(path, key, payload):
    req = urllib.request.Request(
        f"https://api.notion.com/v1/{path}",
        data=json.dumps(payload).encode(), headers=notion_headers(key),
        method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def notion_db_schema(db_id, key):
    """Return a database's live properties dict (the schema)."""
    return notion_get(f"databases/{db_id}", key).get("properties", {})


def clonable_properties(properties: dict) -> dict:
    """Strip a Notion schema down to what can be re-created in a new DB.
    Keeps title/rich_text/number/select/multi_select/status/date/checkbox/url/
    people/email/phone. Drops computed and relational columns (formula, rollup,
    relation, created/last_edited, files) that cannot be cloned standalone."""
    drop = {"formula", "rollup", "relation", "created_time", "created_by",
            "last_edited_time", "last_edited_by", "files", "unique_id"}
    out = {}
    for name, meta in properties.items():
        t = meta.get("type")
        if t in drop:
            continue
        if t in ("select", "multi_select", "status"):
            opts = meta.get(t, {}).get("options", [])
            # keep only name + color, drop ids so Notion mints fresh ones
            kept = [{"name": o["name"], **({"color": o["color"]} if o.get("color") else {})}
                    for o in opts]
            out[name] = {t: {"options": kept}}
        elif t == "number":
            fmt = meta.get("number", {}).get("format", "number")
            out[name] = {"number": {"format": fmt}}
        else:
            out[name] = {t: {}}
    return out


# --------------------------------------------------------------------------- #
# Airtable (Day Docket base) - derive a PM's active projects
# --------------------------------------------------------------------------- #
def airtable_get(url, key):
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Airtable HTTP {e.code}: {e.read().decode()[:300]}")


def pm_projects(pm_key, env: dict, active_only=True) -> list:
    """Projects assigned to a PM in the Day Docket Airtable. Returns a list of
    {number, name} dicts. Reuses the plain AIRTABLE_API_KEY / AIRTABLE_BASE_ID."""
    key = env.get("AIRTABLE_API_KEY")
    base = env.get("AIRTABLE_BASE_ID")
    if not key or not base:
        raise RuntimeError("Missing AIRTABLE_API_KEY / AIRTABLE_BASE_ID in env")
    out, offset = [], None
    while True:
        url = f"https://api.airtable.com/v0/{base}/{DOCKET_PROJECTS_TABLE}?pageSize=100"
        if offset:
            url += f"&offset={offset}"
        data = airtable_get(url, key)
        for rec in data.get("records", []):
            f = rec.get("fields", {})
            if (f.get("PM Assigned") or "").strip().lower() != pm_key:
                continue
            if active_only and (f.get("Status") or "").strip().lower() != "active":
                continue
            num = str(f.get("Project Number") or "").strip()
            if num:
                out.append({"number": num, "name": (f.get("Name") or "").strip()})
        offset = data.get("offset")
        if not offset:
            break
    out.sort(key=lambda p: p["number"])
    return out
