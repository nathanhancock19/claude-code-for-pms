#!/usr/bin/env python3
"""
toolbox_talk.py - worker behind the /toolbox-talk command (Surface 1).

Generates a Dunsteel-branded weekly toolbox talk for a project, creates the
matching item on the Monday "Toolbox Talk" board (YOUR_TOOLBOX_BOARD_ID) so the board shows
it was done, and renders a PDF locally via scripts/html_to_pdf.py.

Topic selection: an explicit --topic (number or name) wins; otherwise the next
topic in the rolling weekly program (reference/systems/toolbox-talks/topic-program.json)
is chosen by ISO week. Topic content (points, hazards, controls) is curated in
that program, so no API key is required.

Usage:
    python scripts/toolbox_talk.py 501
    python scripts/toolbox_talk.py 501 --topic "silica"
    python scripts/toolbox_talk.py 501 --topic 6 --date 2026-06-08
    python scripts/toolbox_talk.py 501 --dry-run        # no Monday write, no PDF
    python scripts/toolbox_talk.py 501 --no-monday      # PDF only
    python scripts/toolbox_talk.py 501 --test           # write to workspace temp

HARD RULE: no long dashes anywhere in this file or in generated content.
"""

import argparse
import datetime
import html
import json
import subprocess
import sys
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WORKSPACE / "scripts"))
import dunsteel_projects as projects  # noqa: E402

TEMPLATE = WORKSPACE / "reference" / "systems" / "toolbox-talks" / "toolbox-talk-template.html"
TOPIC_PROGRAM = WORKSPACE / "reference" / "systems" / "toolbox-talks" / "topic-program.json"
LOGO = WORKSPACE / "reference" / "assets" / "logos" / "dunsteel-logo-letterhead.jpg"
HTML_TO_PDF = WORKSPACE / "scripts" / "html_to_pdf.py"

TOOLBOX_BOARD = YOUR_TOOLBOX_BOARD_ID
TOOLBOX_GROUP = "topics"
COL = {  # from monday-board-map.md
    "date": "date", "job": "single_select", "reason": "single_select3",
    "person": "short_text", "pm": "single_select82oifav", "status": "color_mm41m213",
    "agenda": "long_text", "hazards": "long_text5", "controls": "long_text1",
    "chk_qual": "true___false", "chk_permit": "true___false8",
    "chk_equip": "true___false5", "chk_ppe": "true___false1",
}
# The Toolbox board has "Attendee N Name" text columns plus matching signature
# columns. The name columns are not cleanly numbered (duplicates / gaps), so we
# discover them live by title rather than hardcode ids. The signature columns are
# file type and cannot be set via the API (Monday file upload via API does not
# work), so the PDF holds the actual signatures.
EMPTY_ATTENDEE_ROWS = 14


def load_dotenv():
    env = WORKSPACE / ".env"
    if not env.exists():
        return
    import os
    for raw in env.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        if k and k not in os.environ:
            os.environ[k] = v.strip().strip('"').strip("'")


def load_topics():
    return json.loads(TOPIC_PROGRAM.read_text(encoding="utf-8"))["topics"]


def pick_topic(topics, topic_arg, the_date):
    if topic_arg:
        if topic_arg.isdigit():
            for t in topics:
                if t["n"] == int(topic_arg):
                    return t
        ql = topic_arg.lower()
        for t in topics:
            if ql in t["topic"].lower():
                return t
        raise SystemExit(f"No topic matched '{topic_arg}'. Run with --list to see topics.")
    # rotate by ISO week
    isoweek = the_date.isocalendar()[1]
    return topics[(isoweek - 1) % len(topics)]


def split_items(text):
    """Split a period-separated hazards/controls string into clean items."""
    items = []
    for part in text.replace("\n", " ").split("."):
        part = part.strip()
        if part:
            items.append(part)
    return items


def thc_list_html(text):
    """Render hazards/controls as a spaced bulleted list (one item per line)."""
    items = split_items(text)
    if not items:
        return "&nbsp;"
    lis = "".join(f"<li>{html.escape(i)}</li>" for i in items)
    return f"<ul>{lis}</ul>"


def attendee_rows_html(ctx):
    """Filled attendance rows when attendees are supplied; blank rows otherwise.

    ``ctx['attendees']`` is a list of ``{"name", "company"}`` dicts. The signature
    cell is pre-filled with the attendee's name in a cursive script (unless
    --no-sign). No blank rows are appended unless --blank-rows is set.
    """
    attendees = ctx.get("attendees") or []
    sign = ctx.get("sign_attendees", True)
    blank_override = ctx.get("blank_rows")

    rows = []
    for att in attendees:
        name = att["name"]
        company = att.get("company") or ""
        sig = f'<span class="sig-script">{html.escape(name)}</span>' if sign else "&nbsp;"
        comp = html.escape(company) if company else "&nbsp;"
        rows.append(f"<tr><td>{html.escape(name)}</td><td>{comp}</td><td>{sig}</td></tr>")

    n_blank = blank_override if blank_override is not None else (
        0 if attendees else EMPTY_ATTENDEE_ROWS
    )
    rows.extend(
        "<tr><td>&nbsp;</td><td>&nbsp;</td><td>&nbsp;</td></tr>" for _ in range(n_blank)
    )
    return "\n".join(rows)


def latest_prior_talk(out_dir, before_iso):
    """Most recent talk HTML in out_dir dated strictly before before_iso, or None."""
    import re
    if not out_dir.exists():
        return None
    best, best_date = None, None
    for p in out_dir.glob("*-toolbox-talk-*.html"):
        m = re.search(r"(\d{4}-\d{2}-\d{2})", p.name)
        if not m or m.group(1) >= before_iso:
            continue
        if best_date is None or m.group(1) > best_date:
            best, best_date = p, m.group(1)
    return best


def parse_attendees(html_path):
    """Pull [{name, company}] from a talk's attendance register.

    Handles this script's three-column register (Name, Company, Signature) and
    is tolerant of the older hand-built format (#, Name, Role, Company, ...).
    Blank rows are skipped.
    """
    import re
    text = html_path.read_text(encoding="utf-8")
    m = re.search(r'class="att-table".*?<tbody>(.*?)</tbody>', text, re.S)
    if not m:
        return []
    out = []
    for row in re.findall(r"<tr>(.*?)</tr>", m.group(1), re.S):
        cells = re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)
        vals = [re.sub(r"<[^>]+>", "", c).replace("&nbsp;", "").strip() for c in cells]
        if vals and vals[0].isdigit():  # drop leading row-number (legacy format)
            vals = vals[1:]
        if not vals or not vals[0]:
            continue
        name = vals[0]
        company = vals[1] if len(vals) > 1 else ""
        # legacy: [name, role, company, ...] - role usually has a slash
        if len(vals) >= 3 and ("/" in vals[1] or vals[1].lower().endswith("operator")):
            company = vals[2]
        out.append({"name": name, "company": company})
    return out


def resolve_attendees(args, out_dir, the_iso):
    """Build the attendee list: explicit --attendees, else carry forward from
    last week's talk, then apply --add / --remove. Returns [{name, company}]."""
    default_company = args.company or ""
    carried_from = None

    if args.attendees:
        attendees = [
            {"name": n.strip(), "company": default_company}
            for n in args.attendees.split(",") if n.strip()
        ]
    elif args.carry and not args.no_carry:
        # Opt-in only: carrying last week's crew forward was filling the board
        # with stale names that had not actually attended or signed this week.
        prior = latest_prior_talk(out_dir, the_iso)
        attendees = parse_attendees(prior) if prior else []
        if attendees:
            carried_from = prior.name
    else:
        attendees = []

    for spec in (args.add or []):
        name, _, comp = spec.partition(":")
        attendees.append({
            "name": name.strip(),
            "company": (comp.strip() or default_company
                        or (attendees[0]["company"] if attendees else "")),
        })
    for name in (args.remove or []):
        attendees = [a for a in attendees if a["name"].lower() != name.strip().lower()]

    return attendees, carried_from


def render(ctx) -> str:
    tpl = TEMPLATE.read_text(encoding="utf-8")
    points_html = "\n      ".join(
        f"<li>{html.escape(p)}</li>" for p in ctx["points"]
    )
    rows = attendee_rows_html(ctx)
    repl = {
        "{{LOGO_PATH}}": str(LOGO.resolve()).replace("\\", "/"),
        "{{PROJECT_NUMBER}}": str(ctx["number"]),
        "{{PROJECT_NAME}}": html.escape(ctx["name"]),
        "{{HEAD_CONTRACTOR}}": html.escape(ctx["head_contractor"] or "N/A"),
        "{{LOCATION}}": html.escape(ctx["location"] or "N/A"),
        "{{DATE}}": ctx["date_display"],
        "{{CONDUCTED_BY}}": html.escape(ctx["conducted_by"]),
        "{{CONDUCTED_BY_ROLE}}": html.escape(ctx["role"]),
        "{{TOPIC}}": html.escape(ctx["topic"]),
        "{{POINTS_HTML}}": points_html,
        "{{HAZARDS}}": thc_list_html(ctx["topic_hazards"]),
        "{{CONTROLS}}": thc_list_html(ctx["topic_controls"]),
        "{{ATTENDEE_ROWS}}": rows,
    }
    out = tpl
    for k, v in repl.items():
        out = out.replace(k, v)
    return out


def to_pdf(html_path: Path) -> Path:
    subprocess.run([sys.executable, str(HTML_TO_PDF), str(html_path)], check=True)
    return html_path.with_suffix(".pdf")


def attendee_columns(m):
    """Discover the Toolbox board's attendee columns in BOARD (visual) order.

    The board's name columns are mis-titled: there is a duplicate "Attendee 3",
    no "Attendee 2" and no "Attendee 8". Sorting by the number in the title (the
    old behaviour) put the 2nd person in a box labelled "Attendee 3" and dropped
    a column. So we ignore the title numbers entirely and use the order the
    columns appear on the board, which is the order a person reads them.

    Returns (name_col_ids, add_more_col_ids):
      - name_col_ids: every "Attendee N Name" text column, left to right.
      - add_more_col_ids: the "More Attendees to add?" / "Add another" checkboxes
        that sit between the name slots. Tick add_more_col_ids[i] to reveal the
        (i+2)th attendee, so N attendees need the first N-1 of these ticked.
    """
    names, add_more = [], []
    for c in m.get_columns(TOOLBOX_BOARD):
        title = c["title"].lower()
        if c["type"] == "text" and "attendee" in title and "name" in title:
            names.append(c["id"])
        elif c["type"] == "checkbox" and ("more attendees" in title or "add another" in title):
            add_more.append(c["id"])
    return names, add_more


def create_monday_item(ctx) -> str:
    from dunsteel_monday import MondayClient
    m = MondayClient()
    cv = {
        COL["date"]: {"date": ctx["date_iso"]},
        COL["reason"]: {"label": "Weekly"},
        COL["person"]: ctx["conducted_by"],  # text column = plain string
        COL["agenda"]: {"text": "\n".join(f"{i+1}. {p}" for i, p in enumerate(ctx["points"]))},
        COL["hazards"]: {"text": ctx["topic_hazards"]},
        COL["controls"]: {"text": ctx["topic_controls"]},
        COL["chk_qual"]: {"checked": "true"},
        COL["chk_permit"]: {"checked": "true"},
        COL["chk_equip"]: {"checked": "true"},
        COL["chk_ppe"]: {"checked": "true"},
    }
    if ctx["toolbox_job"]:
        cv[COL["job"]] = {"label": ctx["toolbox_job"]}
    cv[COL["pm"]] = {"label": projects.pm_label(ctx["conducted_by"])}

    # Attendee names into the discovered Attendee Name columns, in board order,
    # as plain first/last names (no "(signed)" suffix - the actual signatures
    # live in the PDF; the file-type signature columns are not API-writable).
    # The "More Attendees to add?" / "Add another" checkboxes are ticked so the
    # board shows the right number of attendee rows. Names beyond the available
    # columns are dropped with a warning rather than lost silently.
    attendees = ctx.get("attendees") or []
    if attendees:
        name_cols, add_more_cols = attendee_columns(m)
        for col, att in zip(name_cols, attendees):
            cv[col] = att["name"]
        for col in add_more_cols[:max(0, len(attendees) - 1)]:
            cv[col] = {"checked": "true"}
        if len(attendees) > len(name_cols):
            dropped = ", ".join(a["name"] for a in attendees[len(name_cols):])
            print(f"  WARNING: only {len(name_cols)} attendee columns on the board; "
                  f"not written to Monday: {dropped}")

    item_id = m.create_item(TOOLBOX_BOARD, TOOLBOX_GROUP, "Incoming form answer", cv)
    m.change_column_values(TOOLBOX_BOARD, item_id, {COL["status"]: {"label": "PDF Created"}})
    return item_id


def main(argv=None):
    load_dotenv()
    ap = argparse.ArgumentParser(description="Generate a Dunsteel weekly toolbox talk.")
    ap.add_argument("project")
    ap.add_argument("--topic", help="topic number or name; default = next in weekly rotation")
    ap.add_argument("--date", help="YYYY-MM-DD; default today")
    ap.add_argument("--conducted-by", default="Nathan Hancock")
    ap.add_argument("--role", default="Project Manager")
    ap.add_argument("--attendees", help="comma-separated attendee names; overrides carry-forward")
    ap.add_argument("--company", help="company applied to pre-filled / added attendees")
    ap.add_argument("--add", action="append",
                    help="add a new starter on top of the carried crew: 'Name' or 'Name:Company' (repeatable)")
    ap.add_argument("--remove", action="append",
                    help="remove a carried-forward attendee by name (repeatable)")
    ap.add_argument("--carry", action="store_true",
                    help="carry attendees forward from last week's talk (default: off - "
                         "the crew signs the register on the day, so no names are prefilled)")
    ap.add_argument("--no-carry", action="store_true",
                    help="(deprecated) no-op; carry-forward is now off by default")
    ap.add_argument("--no-sign", action="store_true",
                    help="leave the signature cell blank instead of cursive name")
    ap.add_argument("--blank-rows", type=int,
                    help="number of empty register rows (default 14 if no attendees, else 0)")
    ap.add_argument("--out-dir")
    ap.add_argument("--test", action="store_true", help="write to workspace temp, never Monday")
    ap.add_argument("--no-monday", action="store_true")
    ap.add_argument("--no-pdf", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="render HTML only, no PDF, no Monday")
    ap.add_argument("--list", action="store_true", help="list topics and exit")
    args = ap.parse_args(argv)

    topics = load_topics()
    if args.list:
        for t in topics:
            print(f"{t['n']:>2}  {t['topic']}")
        return 0

    the_date = (datetime.date.fromisoformat(args.date) if args.date else datetime.date.today())
    proj = projects.resolve(args.project)
    topic = pick_topic(topics, args.topic, the_date)

    ctx = {
        **proj,
        "date_iso": the_date.isoformat(),
        "date_display": the_date.strftime("%-d %B %Y") if sys.platform != "win32"
                        else the_date.strftime("%#d %B %Y"),
        "conducted_by": args.conducted_by,
        "role": args.role,
        "topic": topic["topic"],
        "points": topic["points"],
        "topic_hazards": topic["topic_hazards"],
        "topic_controls": topic["topic_controls"],
        "sign_attendees": not args.no_sign,
        "blank_rows": args.blank_rows,
    }

    # Output location
    if args.test:
        out_dir = WORKSPACE / "outputs" / "automation" / "toolbox-talks" / "TEST_RUN"
    elif args.out_dir:
        out_dir = Path(args.out_dir)
    else:
        out_dir = proj["out_dir"] / "toolbox-talks"
    out_dir.mkdir(parents=True, exist_ok=True)

    # Attendees: explicit list, else carry forward from last week's talk in this
    # same folder, then apply --add / --remove.
    ctx["attendees"], carried_from = resolve_attendees(args, out_dir, the_date.isoformat())

    slug = topic["topic"].lower().replace(",", "").replace(" ", "-")[:40].strip("-")
    base = f"{proj['number']}-toolbox-talk-{the_date.isoformat()}-{slug}"
    html_path = out_dir / f"{base}.html"
    html_path.write_text(render(ctx), encoding="utf-8")

    pdf_path = None
    if not args.dry_run and not args.no_pdf:
        pdf_path = to_pdf(html_path)

    item_id = None
    if not args.dry_run and not args.no_monday and not args.test:
        try:
            item_id = create_monday_item(ctx)
        except Exception as e:
            print(f"WARNING: Monday item not created: {e}")

    print("\nToolbox talk generated.")
    print(f"  Project:   {proj['number']} {proj['name']}")
    print(f"  Topic:     {topic['topic']}")
    print(f"  Date:      {ctx['date_display']}")
    print(f"  HTML:      {html_path}")
    print(f"  PDF:       {pdf_path if pdf_path else '(skipped)'}")
    att = ctx["attendees"]
    if att:
        names = ", ".join(a["name"] for a in att)
        src = f" (carried from {carried_from})" if carried_from else ""
        print(f"  Attendees: {names}{src}")
    if item_id:
        print(f"  Monday:    item {item_id} on board {TOOLBOX_BOARD} (Automation Status = PDF Created)")
        print(f"             https://your-workspace.monday.com/boards/{TOOLBOX_BOARD}/pulses/{item_id}")
    elif args.dry_run:
        print("  Monday:    (dry-run, not created)")
    elif args.test:
        print("  Monday:    (test mode, not created)")
    if not proj["toolbox_job"]:
        print(f"  NOTE: project {proj['number']} is not in the Toolbox board Job dropdown; Job left blank.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
