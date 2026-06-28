#!/usr/bin/env python3
"""
dunsteel_projects.py - light project-context resolver shared by the safety
command workers (toolbox_talk.py, safety_audit.py).

Resolves a project number to: name, head contractor, location, the output
folder under outputs/, and the exact Monday "Job" single-select label for each
board (toolbox / safety audit). Board labels come from
reference/systems/toolbox-talks/monday-board-map.md (verified 2026-06-05).

HARD RULE: no long dashes anywhere in this file.
"""

import glob
import re
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parent.parent

# Exact Monday "Job" single-select labels, by board. A project absent here is
# not in that board's dropdown yet (set Job to None and warn).
TOOLBOX_JOB_LABEL = {
    501: "501 - Riverside SYD02", 503: "503 - Stratus Data Centres", 510: "510 - Northgate",
    506: "506 - Riverside Leisure Centre", 507: "507 - Coastal Hospital",
    502: "502 - Parkview", 508: "508 - Airport", 509: "509 - 60 King St",
    407: "407 - KPMG", 410: "410 - Glebe - Schols",
}
AUDIT_JOB_LABEL = {
    501: "501 - Riverside SYD02", 507: "507 - Coastal Hospital",
    342: "342 - Parkline Builders - Optus", 253: "253 - SOH", 224: "224 - NSOP",
    323: "323 - Stratus Data Centres",
}

# Fallback context for projects without a usable brief.
KNOWN = {
    501: {"name": "Stratus Data Centres SYD2", "head_contractor": "Northbridge Constructions", "location": "Riverside, NSW"},
    504: {"name": "Meridian Data Centres SY5-4", "head_contractor": "a head contractor", "location": "Sydney, NSW"},
    505: {"name": "Vantage Tech CBD", "head_contractor": "a head contractor", "location": "Sydney, NSW"},
    502: {"name": "Parkview", "head_contractor": "Parkline Builders", "location": "NSW"},
    503: {"name": "SYD3 Westview Industrial (Stratus Data Centres)", "head_contractor": "Northbridge Constructions", "location": "Westview Industrial, NSW"},
}

# PM directory. The Monday "Project Manager" single-select on the forms uses
# first-name labels (Sam, Nathan, Riley, Jordan, Alex, Casey). We normalise any
# spelling (first name OR full name) to a canonical key, then map to
# the email and the exact option label the Monday column expects.
_PM_ALIAS = {
    "nathan": "nathan", "nathan hancock": "nathan",
    "sam": "sam", "sam taylor": "sam",
    "riley": "riley", "riley brooks": "riley",
    "jordan": "jordan", "jordan reed": "jordan",
    "alex": "alex", "alex morgan": "alex",
    "casey": "casey", "casey quinn": "casey",
}
PM_EMAIL = {
    "nathan": "nathanh@dunsteel.com.au", "sam": "pm-b@dunsteel.com.au",
    "riley": "estimator@dunsteel.com.au", "jordan": "pm-c@dunsteel.com.au",
    "alex": "pm-a@dunsteel.com.au", "casey": "pm-d@dunsteel.com.au",
}
# Exact label to write into the Monday "Project Manager" single-select.
PM_LABEL = {
    "nathan": "Nathan", "sam": "Sam", "riley": "Riley",
    "jordan": "Jordan", "alex": "Alex", "casey": "Casey",
}
DEFAULT_PM = "nathan"


def _pm_key(name: str):
    if not name:
        return None
    n = name.strip().lower()
    return _PM_ALIAS.get(n) or _PM_ALIAS.get(n.split()[0])


def _parse_front_matter(text: str) -> dict:
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    out = {}
    for line in text[3:end].splitlines():
        m = re.match(r"\s*([a-zA-Z_]+):\s*(.+?)\s*$", line)
        if m:
            out[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    return out


def resolve(number) -> dict:
    """Return {number, name, head_contractor, location, out_dir, toolbox_job,
    audit_job}. out_dir is the project's outputs/ folder (created on demand by
    the caller)."""
    num = int(number)
    name = head = location = ""

    # Prefer the project brief front matter.
    briefs = glob.glob(str(WORKSPACE / "reference" / "projects" / f"{num}-*" / "project-brief.md"))
    if briefs:
        fm = _parse_front_matter(Path(briefs[0]).read_text(encoding="utf-8", errors="replace"))
        name = fm.get("project_name", "") or name
        head = fm.get("head_contractor", "") or head

    k = KNOWN.get(num, {})
    name = name or k.get("name", f"Project {num}")
    head = head or k.get("head_contractor", "")
    location = location or k.get("location", "")

    # Output folder: prefer an existing outputs/<num>-* dir.
    out_matches = glob.glob(str(WORKSPACE / "outputs" / f"{num}-*"))
    if out_matches:
        out_dir = Path(out_matches[0])
    else:
        slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or f"project-{num}"
        out_dir = WORKSPACE / "outputs" / f"{num}-{slug}"

    return {
        "number": num,
        "name": name,
        "head_contractor": head,
        "location": location,
        "out_dir": out_dir,
        "toolbox_job": TOOLBOX_JOB_LABEL.get(num),
        "audit_job": AUDIT_JOB_LABEL.get(num),
    }


def pm_key(pm_name: str):
    """Public accessor: normalise any spelling of a PM name to its canonical
    key, or None if unknown. Used by the provisioning scripts."""
    return _pm_key(pm_name)


def pm_keys() -> list:
    """All canonical PM keys (deduped, stable order). For the fleet rollout."""
    seen = []
    for v in _PM_ALIAS.values():
        if v not in seen:
            seen.append(v)
    return seen


def pm_email(pm_name: str) -> str:
    return PM_EMAIL.get(_pm_key(pm_name) or DEFAULT_PM, PM_EMAIL[DEFAULT_PM])


def pm_label(pm_name: str) -> str:
    """Exact label for the Monday Project Manager single-select."""
    return PM_LABEL.get(_pm_key(pm_name) or DEFAULT_PM, PM_LABEL[DEFAULT_PM])


if __name__ == "__main__":
    import json, sys
    print(json.dumps(resolve(sys.argv[1] if len(sys.argv) > 1 else 501), default=str, indent=2))
