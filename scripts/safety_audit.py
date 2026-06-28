#!/usr/bin/env python3
"""
safety_audit.py - worker behind the /safety-audit command (Surface 1).

Generates a Dunsteel-branded pre-start safety audit for a project, creates the
matching item on the Monday "Safety Audit" board (YOUR_SAFETY_AUDIT_BOARD_ID), and renders a PDF
locally via scripts/html_to_pdf.py.

Tasks / Hazards / Controls come from --tasks/--hazards/--controls if given, or
from Claude when --ai is set and ANTHROPIC_API_KEY is available, otherwise from a
sensible structural-steel default keyed off --scope. All seven pre-start checks
default to Yes; mark one failed with --fail "ppe:reason" (repeatable). A draft
always requires the supervisor to review and sign before the work proceeds.

Usage:
    python scripts/safety_audit.py 501 --scope "L6 grating install"
    python scripts/safety_audit.py 501 --scope "Bolt-up L7" --fail "ppe:no dust mask"
    python scripts/safety_audit.py 501 --dry-run
    python scripts/safety_audit.py 501 --no-monday        # PDF only
    python scripts/safety_audit.py 501 --test             # workspace temp

HARD RULE: no long dashes anywhere in this file or in generated content.
"""

import argparse
import datetime
import html
import subprocess
import sys
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WORKSPACE / "scripts"))
import dunsteel_projects as projects  # noqa: E402

TEMPLATE = WORKSPACE / "reference" / "systems" / "toolbox-talks" / "safety-audit-template.html"
LOGO = WORKSPACE / "reference" / "assets" / "logos" / "dunsteel-logo-letterhead.jpg"
HTML_TO_PDF = WORKSPACE / "scripts" / "html_to_pdf.py"

AUDIT_BOARD = YOUR_SAFETY_AUDIT_BOARD_ID
COL = {  # from monday-board-map.md
    "date": "date", "job": "single_select", "person": "short_text0",
    "pm": "single_selectb6ar7yk", "status": "color_mm41qb2d",
    "tasks": "long_text", "hazards": "long_text5", "controls": "long_text1",
    "notes": "long_text6",
}
# pre-start check: short-key -> (display title, board status column id)
CHECKS = [
    ("qual", "Qualification adequate for work activities", "single_select2"),
    ("permit", "All permits in place", "single_select4"),
    ("logbook", "Log books filled out", "single_select6"),
    ("ppe", "Correct and adequate PPE", "single_select58"),
    ("electrical", "Electrical tags up to date", "single_select5"),
    ("materials", "Materials and tools stored safely", "single_select0"),
    ("leadhooks", "Lead hooks", "single_select06"),
]


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


def default_content(scope: str) -> dict:
    scope = scope.strip() or "General structural steel installation works"
    return {
        "tasks": scope,
        "hazards": ("Working at heights and open edges. Moving plant and crane operations. "
                    "Manual handling of steel members. Slips, trips and housekeeping. "
                    "Other trades working in the area."),
        "controls": ("Harness and 100% tie-off above 2m on unprotected edges. Exclusion zones under "
                     "all lifts. SWMS reviewed and signed on. Correct PPE worn. Area kept clear of "
                     "offcuts and leads. Coordination with the head contractor on shared zones."),
    }


def ai_content(proj, scope: str):
    """Optional Claude draft on the Max subscription. Returns dict or None."""
    prompt = (
        "You are drafting the Tasks, Hazards and Controls for a pre-start safety audit for a "
        "structural steel installation crew. Australian construction context. Keep each field to "
        "2-4 short sentences, plain language, no long dashes.\n\n"
        f"Project: {proj['number']} {proj['name']} (head contractor {proj['head_contractor']}).\n"
        f"Today's scope of work: {scope}.\n\n"
        'Return strict JSON: {"tasks": "...", "hazards": "...", "controls": "..."}'
    )
    try:
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import claude_max
        # Subscription-billed via claude_max (forces Max auth, never the API key).
        data = claude_max.complete_json(prompt, model="sonnet")
        return {k: str(data.get(k, "")).strip() for k in ("tasks", "hazards", "controls")}
    except Exception as e:
        print(f"  (Claude draft failed, using default content: {e})")
        return None


def render(ctx) -> str:
    tpl = TEMPLATE.read_text(encoding="utf-8")
    rows = []
    for key, title, _col in CHECKS:
        failed = ctx["fails"].get(key)
        if failed:
            badge = '<span class="badge-no">NO</span>'
            nc = html.escape(failed)
        else:
            badge = '<span class="badge-yes">YES</span>'
            nc = "&nbsp;"
        rows.append(f"<tr><td>{html.escape(title)}</td><td>{badge}</td><td>{nc}</td></tr>")
    repl = {
        "{{LOGO_PATH}}": str(LOGO.resolve()).replace("\\", "/"),
        "{{PROJECT_NUMBER}}": str(ctx["number"]),
        "{{PROJECT_NAME}}": html.escape(ctx["name"]),
        "{{HEAD_CONTRACTOR}}": html.escape(ctx["head_contractor"] or "N/A"),
        "{{SCOPE}}": html.escape(ctx["scope"]),
        "{{DATE}}": ctx["date_display"],
        "{{PM_NAME}}": html.escape(ctx["pm"]),
        "{{CHECK_ROWS}}": "\n      ".join(rows),
        "{{TASKS}}": html.escape(ctx["tasks"]),
        "{{HAZARDS}}": html.escape(ctx["hazards"]),
        "{{CONTROLS}}": html.escape(ctx["controls"]),
        "{{NOTES}}": html.escape(ctx["notes"]) if ctx["notes"] else "&nbsp;",
    }
    out = tpl
    for k, v in repl.items():
        out = out.replace(k, v)
    return out


def to_pdf(html_path: Path) -> Path:
    subprocess.run([sys.executable, str(HTML_TO_PDF), str(html_path)], check=True)
    return html_path.with_suffix(".pdf")


def create_monday_item(ctx) -> str:
    from dunsteel_monday import MondayClient
    m = MondayClient()
    cv = {
        COL["date"]: {"date": ctx["date_iso"]},
        COL["person"]: ctx["pm"],  # text column = plain string
        COL["tasks"]: {"text": ctx["tasks"]},
        COL["hazards"]: {"text": ctx["hazards"]},
        COL["controls"]: {"text": ctx["controls"]},
    }
    if ctx["notes"]:
        cv[COL["notes"]] = {"text": ctx["notes"]}
    if ctx["audit_job"]:
        cv[COL["job"]] = {"label": ctx["audit_job"]}
    cv[COL["pm"]] = {"label": projects.pm_label(ctx["pm"])}
    for key, _title, col in CHECKS:
        cv[col] = {"label": "No" if ctx["fails"].get(key) else "Yes"}
    item_id = m.create_item(AUDIT_BOARD, None, "Incoming form answer", cv)
    m.change_column_values(AUDIT_BOARD, item_id, {COL["status"]: {"label": "PDF Created"}})
    return item_id


def main(argv=None):
    load_dotenv()
    ap = argparse.ArgumentParser(description="Generate a Dunsteel pre-start safety audit.")
    ap.add_argument("project")
    ap.add_argument("--scope", default="General installation works")
    ap.add_argument("--date")
    ap.add_argument("--pm", default="Nathan Hancock")
    ap.add_argument("--tasks")
    ap.add_argument("--hazards")
    ap.add_argument("--controls")
    ap.add_argument("--notes", default="")
    ap.add_argument("--fail", action="append", default=[],
                    help='mark a check failed, "key:reason" (key one of '
                         'qual/permit/logbook/ppe/electrical/materials/leadhooks)')
    ap.add_argument("--ai", action="store_true", help="draft Tasks/Hazards/Controls with Claude")
    ap.add_argument("--out-dir")
    ap.add_argument("--test", action="store_true")
    ap.add_argument("--no-monday", action="store_true")
    ap.add_argument("--no-pdf", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    the_date = datetime.date.fromisoformat(args.date) if args.date else datetime.date.today()
    proj = projects.resolve(args.project)

    fails = {}
    valid_keys = {k for k, _, _ in CHECKS}
    for f in args.fail:
        key, _, reason = f.partition(":")
        key = key.strip().lower()
        if key not in valid_keys:
            raise SystemExit(f"--fail key '{key}' invalid; use one of {sorted(valid_keys)}")
        fails[key] = reason.strip() or "Non-compliance noted"

    content = {"tasks": args.tasks, "hazards": args.hazards, "controls": args.controls}
    if not all(content.values()):
        drafted = (ai_content(proj, args.scope) if args.ai else None) or default_content(args.scope)
        for k in content:
            content[k] = content[k] or drafted[k]

    ctx = {
        **proj,
        "date_iso": the_date.isoformat(),
        "date_display": the_date.strftime("%#d %B %Y") if sys.platform == "win32"
                        else the_date.strftime("%-d %B %Y"),
        "pm": args.pm,
        "scope": args.scope,
        "fails": fails,
        "notes": args.notes,
        **content,
    }

    if args.test:
        out_dir = WORKSPACE / "outputs" / "automation" / "safety-audits" / "TEST_RUN"
    elif args.out_dir:
        out_dir = Path(args.out_dir)
    else:
        out_dir = proj["out_dir"] / "safety-audits"
    out_dir.mkdir(parents=True, exist_ok=True)

    scope_slug = args.scope.lower().replace(",", "").replace(" ", "-")[:30].strip("-") or "audit"
    base = f"{proj['number']}-safety-audit-{the_date.isoformat()}-{scope_slug}"
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

    print("\nSafety audit generated.")
    print(f"  Project:   {proj['number']} {proj['name']}")
    print(f"  Scope:     {args.scope}")
    print(f"  Date:      {ctx['date_display']}")
    if fails:
        print(f"  FAILED:    {', '.join(fails)}")
    print(f"  HTML:      {html_path}")
    print(f"  PDF:       {pdf_path if pdf_path else '(skipped)'}")
    if item_id:
        print(f"  Monday:    item {item_id} on board {AUDIT_BOARD} (Automation Status = PDF Created)")
        print(f"             https://your-workspace.monday.com/boards/{AUDIT_BOARD}/pulses/{item_id}")
    if not proj["audit_job"]:
        print(f"  NOTE: project {proj['number']} is not in the Safety Audit board Job dropdown; Job left blank.")
    print("  REVIEW: confirm the checks and content, then sign before work proceeds.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
