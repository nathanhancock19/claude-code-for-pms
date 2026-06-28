#!/usr/bin/env python3
"""
project_events_query.py - flexible read over the Supabase project_events store.

The query layer behind /ask-projects. Pulls a slice of the event stream and
prints it (markdown by default, or JSON) so Claude Code can reason over it and
answer plain-language questions. No Claude API call here - the calling Claude
session IS the intelligence.

Examples:
    python scripts/project_events_query.py --project 501 --days 14
    python scripts/project_events_query.py --type Variation
    python scripts/project_events_query.py --type RFI --days 30
    python scripts/project_events_query.py --due-before 2026-06-12
    python scripts/project_events_query.py --days 7 --format json

Filters compose (AND). Default window is the last 14 days, all projects.

HARD RULE: no long dashes anywhere in this file or its output.
"""
import argparse
import datetime
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import email_router as er      # noqa: E402  (registry + .env)
import supabase_events         # noqa: E402

FIELDS = ("event_date", "project_id", "event_type", "subject", "summary",
          "sender", "action_owner", "due_date", "confidence", "status", "weblink")


def project_names():
    names = {}
    for p in er.load_registry():
        names[str(p["project_id"])] = p.get("project_name", "")
    return names


def fetch(args):
    filters = {}
    if args.project:
        filters["project_id"] = f"eq.{args.project}"
    if args.type:
        filters["event_type"] = f"eq.{args.type}"
    if args.status:
        filters["status"] = f"eq.{args.status}"
    if args.days and not args.since:
        since = (datetime.date.today() - datetime.timedelta(days=args.days)).isoformat()
        filters["event_date"] = f"gte.{since}"
    if args.since:
        filters["event_date"] = f"gte.{args.since}"
    if args.due_before:
        # only rows that actually have a due date on/before the cutoff
        filters["due_date"] = f"lte.{args.due_before}"
    rows = supabase_events.query(select=",".join(FIELDS),
                                 filters=filters, order="event_date.desc",
                                 limit=args.limit)
    if args.due_before:
        rows = [r for r in rows if r.get("due_date")]
    return rows


def as_markdown(rows, names):
    if not rows:
        return "No events match that query."
    by_proj = {}
    for r in rows:
        by_proj.setdefault(str(r.get("project_id")), []).append(r)
    out = [f"# {len(rows)} event(s)\n"]
    for pid in sorted(by_proj):
        evs = by_proj[pid]
        out.append(f"## {pid} {names.get(pid, '')} - {len(evs)} event(s)")
        for r in evs:
            head = f"- {r.get('event_date','?')} [{r.get('event_type','General')}] {r.get('subject','').strip()}"
            meta = []
            if r.get("sender"):
                meta.append(f"from {r['sender']}")
            if r.get("action_owner"):
                meta.append(f"owner {r['action_owner']}")
            if r.get("due_date"):
                meta.append(f"due {r['due_date']}")
            if r.get("confidence") is not None:
                meta.append(f"conf {r['confidence']}")
            if meta:
                head += f"  ({'; '.join(meta)})"
            out.append(head)
            if r.get("summary"):
                out.append(f"    {r['summary'].strip()}")
        out.append("")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(description="Query the project_events store")
    ap.add_argument("--project", help="project id, e.g. 501")
    ap.add_argument("--type", help="event type: Delivery, Variation, RFI, Decision, "
                                   "Schedule Change, Finish Approval, Site Issue, Commercial, General")
    ap.add_argument("--status", help="status select value")
    ap.add_argument("--days", type=int, default=14, help="lookback window in days (default 14)")
    ap.add_argument("--since", help="explicit ISO date floor for event_date (overrides --days)")
    ap.add_argument("--due-before", dest="due_before", help="only events with a due date on/before this ISO date")
    ap.add_argument("--limit", type=int, default=200, help="max rows")
    ap.add_argument("--format", choices=["md", "json"], default="md")
    args = ap.parse_args()

    er.load_dotenv()
    try:
        rows = fetch(args)
    except Exception as e:  # noqa: BLE001
        sys.exit(f"Query failed: {e}")

    if args.format == "json":
        print(json.dumps(rows, indent=2, ensure_ascii=False))
    else:
        print(as_markdown(rows, project_names()))


if __name__ == "__main__":
    main()
